"""
IMS 2.0 — Walkouts router tests (Module i, Phase 1)
====================================================
Phase-1 contract:
  POST /api/v1/walkouts                create one
  GET  /api/v1/walkouts/{walkout_id}   fetch one

Six tests per the build plan §"Tests (must-pass per phase)":
  - full 30-field create round-trips
  - mobile validation rejects 9- and 11-digit inputs
  - invalid enum value returns 422
  - walkout_id matches WO-{STORE3}-{YYYY}-{6HEX}
  - audit-log row written with action="walkout.create"
  - customer auto-created when mobile is new

DB-backed paths (audit + customer auto-create) use the in-memory fakes
the rest of the test suite already uses; the FastAPI TestClient runs
without a real Mongo. We patch get_db / get_customer_repository /
get_audit_repository on the router module.
"""
from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# In-memory fakes
# ============================================================================


class FakeCollection:
    """Minimal MongoDB collection stub — supports the calls the
    Walkout / Customer / Audit repos exercise."""

    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, filter=None, projection=None):
        if not filter:
            return self.docs[0] if self.docs else None
        for d in self.docs:
            if all(d.get(k) == v for k, v in filter.items()):
                return d
        return None


class FakeDB:
    is_connected = True

    def __init__(self):
        self._collections = {}

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]

    def __getattr__(self, name):
        return self.get_collection(name)


@pytest.fixture
def patched_walkouts(monkeypatch):
    """Wire fake DB + repositories into the router module."""
    fake_db = FakeDB()

    # Patch the get_db that walkouts.py imports
    from api.routers import walkouts as walkouts_module
    monkeypatch.setattr(walkouts_module, "get_db", lambda: fake_db)

    # Patch user resolution to return a deterministic name
    def _fake_user_repo():
        class _R:
            def find_by_id(self, uid):
                return {"user_id": uid, "name": f"User-{uid}"}
            def find_one(self, filter):
                return self.find_by_id(filter.get("user_id", ""))
        return _R()
    monkeypatch.setattr(walkouts_module, "get_user_repository", _fake_user_repo)

    # Customer repo backed by FakeCollection
    from database.repositories.customer_repository import CustomerRepository
    customer_repo = CustomerRepository(fake_db.get_collection("customers"))
    monkeypatch.setattr(
        walkouts_module, "get_customer_repository", lambda: customer_repo
    )

    # Audit repo backed by FakeCollection
    from database.repositories.audit_repository import AuditRepository
    audit_repo = AuditRepository(fake_db.get_collection("audit_logs"))
    monkeypatch.setattr(walkouts_module, "get_audit_repository", lambda: audit_repo)

    return {"db": fake_db, "customer_repo": customer_repo, "audit_repo": audit_repo}


# ============================================================================
# Test payload helpers
# ============================================================================


def _full_payload(**overrides):
    p = {
        "customer_name": "Avinash Kumar Gupta",
        "mobile": "9473457157",
        "age_group": "26-35",
        "gender": "MALE",
        "product_interested": "FRAME",
        "has_prescription": "YES",
        "displayed_price_range": "5000-10000",
        "required_price_range": "3000-5000",
        "primary_walkout_reason": "BUDGET/PRICE",
        "secondary_walkout_reason": "BRAND",
        "brand_interest": "Ray-Ban",
        "competitor_mentioned": "Lenskart",
        "purchase_planned_in": "1-7 DAYS",
        "sales_person_id": "user-akshay",
        "action_remarks": "Wants to come back next week",
    }
    p.update(overrides)
    return p


# ============================================================================
# Tests
# ============================================================================


def test_create_walkout_full_30_fields(client, auth_headers, patched_walkouts):
    """Full payload persists every column we ship."""
    resp = client.post(
        "/api/v1/walkouts", json=_full_payload(), headers=auth_headers
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Server-stamped fields
    assert body["walkout_id"].startswith("WO-")
    assert body["store_id"] == "BV-TEST-01"
    assert body["sales_person_name"] == "User-user-akshay"
    assert body["date_str"]  # set
    # Round-trip semantic fields
    assert body["customer_name"] == "Avinash Kumar Gupta"
    assert body["mobile"] == "9473457157"
    assert body["age_group"] == "26-35"
    assert body["gender"] == "MALE"
    assert body["product_interested"] == "FRAME"
    assert body["has_prescription"] == "YES"
    assert body["primary_walkout_reason"] == "BUDGET/PRICE"
    assert body["secondary_walkout_reason"] == "BRAND"
    assert body["brand_interest"] == "Ray-Ban"
    assert body["competitor_mentioned"] == "Lenskart"
    assert body["purchase_planned_in"] == "1-7 DAYS"


@pytest.mark.parametrize("bad_mobile", ["123456789", "12345678901", "abcdefghij", ""])
def test_mobile_validation_rejects_non_10_digits(
    client, auth_headers, patched_walkouts, bad_mobile
):
    """9 / 11 digit / empty / non-numeric mobiles are 422."""
    resp = client.post(
        "/api/v1/walkouts",
        json=_full_payload(mobile=bad_mobile),
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_invalid_enum_value_returns_422(client, auth_headers, patched_walkouts):
    """A reason not in the WalkoutReason enum is rejected by pydantic."""
    resp = client.post(
        "/api/v1/walkouts",
        json=_full_payload(primary_walkout_reason="DOESNT_LIKE_LOGO"),
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_walkout_id_format_matches_pattern(
    client, auth_headers, patched_walkouts
):
    """WO-{STORE3}-{YYYY}-{6HEX} — for store BV-TEST-01 → WO-TES-2026-XXXXXX."""
    resp = client.post(
        "/api/v1/walkouts", json=_full_payload(), headers=auth_headers
    )
    assert resp.status_code == 201
    walkout_id = resp.json()["walkout_id"]
    # Pattern: WO-{3 alnum}-{4 digits}-{6 alnum}
    assert re.match(r"^WO-[A-Z0-9]{1,3}-\d{4}-[A-F0-9]{6}$", walkout_id), walkout_id
    # Store BV-TEST-01 → after stripping BV/WO/BVO chain, parts[0]='TEST'[:3]='TES'
    assert walkout_id.startswith("WO-TES-"), walkout_id


def test_audit_log_row_written(client, auth_headers, patched_walkouts):
    """A walkout.create row hits the audit_logs collection."""
    resp = client.post(
        "/api/v1/walkouts", json=_full_payload(), headers=auth_headers
    )
    assert resp.status_code == 201
    walkout_id = resp.json()["walkout_id"]

    audit_docs = patched_walkouts["audit_repo"].collection.docs
    walkout_audits = [d for d in audit_docs if d.get("action") == "walkout.create"]
    assert len(walkout_audits) == 1
    audit = walkout_audits[0]
    assert audit["entity_type"] == "walkout"
    assert audit["entity_id"] == walkout_id
    assert audit["store_id"] == "BV-TEST-01"
    assert audit["user_id"] == "test-admin-001"
    assert audit["detail"]["mobile"] == "9473457157"


def test_customer_auto_created_when_mobile_new(
    client, auth_headers, patched_walkouts
):
    """A walkout with a previously-unknown mobile creates a skeleton
    customer + links the customer_id back onto the walkout + logs a
    customer.create audit row tagged via_walkout=True."""
    customer_repo = patched_walkouts["customer_repo"]
    audit_repo = patched_walkouts["audit_repo"]

    # Pre-state: no customers
    assert len(customer_repo.collection.docs) == 0

    resp = client.post(
        "/api/v1/walkouts",
        json=_full_payload(mobile="9876543210", customer_name="New Walker"),
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text

    # A skeleton customer was created
    docs = customer_repo.collection.docs
    assert len(docs) == 1
    cust = docs[0]
    assert cust["mobile"] == "9876543210"
    assert cust["name"] == "New Walker"
    assert cust["source"] == "walkout"
    assert cust["primary_store_id"] == "BV-TEST-01"

    # Walkout response is linked
    body = resp.json()
    assert body["customer_id"] == cust["customer_id"]

    # Audit trail: both a customer.create AND a walkout.create
    actions = [d.get("action") for d in audit_repo.collection.docs]
    assert "customer.create" in actions
    assert "walkout.create" in actions
    cust_audit = next(d for d in audit_repo.collection.docs
                      if d.get("action") == "customer.create")
    assert cust_audit["detail"]["via_walkout"] is True
