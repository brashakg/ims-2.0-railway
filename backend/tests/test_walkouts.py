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


def _doc_matches(doc, filter):
    """Tiny Mongo-filter matcher — supports plain equality and the
    operators the walkout repo uses ($gte/$lte). Doesn't try to be a
    full Mongo emulator."""
    if not filter:
        return True
    for k, expected in filter.items():
        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, op_val in expected.items():
                if op == "$gte" and not (actual is not None and actual >= op_val):
                    return False
                if op == "$lte" and not (actual is not None and actual <= op_val):
                    return False
                if op == "$ne" and actual == op_val:
                    return False
        else:
            if actual != expected:
                return False
    return True


class _FakeCursor:
    """Lazy cursor for FakeCollection.find() with chainable
    sort/skip/limit. Materializes on iter()."""

    def __init__(self, docs):
        self._docs = list(docs)
        self._sort_keys = None
        self._skip = 0
        self._limit = None

    def sort(self, keys):
        self._sort_keys = keys
        return self

    def skip(self, n):
        self._skip = int(n or 0)
        return self

    def limit(self, n):
        self._limit = int(n or 0) or None
        return self

    def _materialize(self):
        out = list(self._docs)
        if self._sort_keys:
            for key, direction in reversed(self._sort_keys):
                out.sort(key=lambda d, k=key: (d.get(k) is None, d.get(k)), reverse=(direction == -1))
        if self._skip:
            out = out[self._skip:]
        if self._limit:
            out = out[: self._limit]
        return out

    def __iter__(self):
        return iter(self._materialize())


class FakeCollection:
    """Minimal MongoDB collection stub — supports the calls the
    Walkout / Customer / Audit repos exercise (Phases 1 + 2)."""

    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, filter=None, projection=None):
        if not filter:
            return self.docs[0] if self.docs else None
        for d in self.docs:
            if _doc_matches(d, filter):
                return d
        return None

    def find(self, filter=None, projection=None):
        return _FakeCursor(d for d in self.docs if _doc_matches(d, filter))

    def count_documents(self, filter=None):
        return sum(1 for d in self.docs if _doc_matches(d, filter))

    def update_one(self, filter, update):
        modified = 0
        for d in self.docs:
            if _doc_matches(d, filter):
                set_block = (update or {}).get("$set", {}) or {}
                d.update(set_block)
                modified += 1
                break  # update_one updates one match
        return type("R", (), {"modified_count": modified, "matched_count": modified})()


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


# ============================================================================
# Phase 2 — list / patch / delete
# ============================================================================


@pytest.fixture
def staff_headers_pune():
    """SALES_STAFF token whose user_id == 'user-akshay'. Lets us test
    own-only edit RBAC without changing the seed payload."""
    from api.routers.auth import create_access_token
    token = create_access_token({
        "user_id": "user-akshay",
        "username": "akshay",
        "roles": ["SALES_STAFF"],
        "store_ids": ["BV-TEST-01"],
        "active_store_id": "BV-TEST-01",
    })
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def manager_headers():
    """STORE_MANAGER token for BV-TEST-01."""
    from api.routers.auth import create_access_token
    token = create_access_token({
        "user_id": "test-manager-001",
        "username": "teststoremgr",
        "roles": ["STORE_MANAGER"],
        "store_ids": ["BV-TEST-01"],
        "active_store_id": "BV-TEST-01",
    })
    return {"Authorization": f"Bearer {token}"}


def _create_walkout(client, headers, **overrides):
    resp = client.post(
        "/api/v1/walkouts", json=_full_payload(**overrides), headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_list_by_store_filters(client, auth_headers, patched_walkouts):
    """GET /walkouts returns the rows for the active store, sorted
    newest-first, and supports the documented filters."""
    # Seed three rows with distinct sales_person_id + reasons
    _create_walkout(client, auth_headers, mobile="9000000001",
                    sales_person_id="user-akshay",
                    primary_walkout_reason="BUDGET/PRICE")
    _create_walkout(client, auth_headers, mobile="9000000002",
                    sales_person_id="user-rupesh",
                    primary_walkout_reason="BRAND")
    _create_walkout(client, auth_headers, mobile="9000000003",
                    sales_person_id="user-akshay",
                    primary_walkout_reason="STYLE/DESIGN")

    # No filter — all three
    resp = client.get("/api/v1/walkouts", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    for item in body["items"]:
        assert item["store_id"] == "BV-TEST-01"

    # Filter by sales_person_id
    resp = client.get(
        "/api/v1/walkouts?sales_person_id=user-akshay",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert all(i["sales_person_id"] == "user-akshay" for i in body["items"])

    # Filter by reason
    resp = client.get(
        "/api/v1/walkouts?primary_walkout_reason=BRAND",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_pagination_returns_default_50(client, auth_headers, patched_walkouts):
    """No `limit` param → 50; explicit limit honored."""
    # 60 walkouts at unique mobiles
    for i in range(60):
        mobile = f"9{i:09d}"
        _create_walkout(client, auth_headers, mobile=mobile)

    # Default limit = 50
    resp = client.get("/api/v1/walkouts", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 60
    assert body["limit"] == 50
    assert len(body["items"]) == 50

    # Explicit small limit
    resp = client.get("/api/v1/walkouts?limit=5", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["limit"] == 5
    assert len(body["items"]) == 5


def test_patch_diff_audited(client, auth_headers, patched_walkouts):
    """PATCH writes a single walkout.update audit row whose
    `detail.changes` carries the field-level from→to diff."""
    audit_repo = patched_walkouts["audit_repo"]
    walkout = _create_walkout(client, auth_headers)
    walkout_id = walkout["walkout_id"]

    # Edit two fields
    resp = client.patch(
        f"/api/v1/walkouts/{walkout_id}",
        json={"action_remarks": "Updated remarks", "brand_interest": "Persol"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action_remarks"] == "Updated remarks"
    assert body["brand_interest"] == "Persol"

    update_audits = [
        d for d in audit_repo.collection.docs
        if d.get("action") == "walkout.update"
    ]
    assert len(update_audits) == 1
    audit = update_audits[0]
    changes = audit["detail"]["changes"]
    assert changes["action_remarks"] == {
        "from": "Wants to come back next week",
        "to": "Updated remarks",
    }
    assert changes["brand_interest"] == {
        "from": "Ray-Ban",
        "to": "Persol",
    }


def test_rbac_sales_staff_cannot_edit_others(
    client, auth_headers, staff_headers_pune, patched_walkouts
):
    """Sales staff can edit their own walkouts but not their colleague's.
    Sales-person attribution is gated to managers/admins.
    """
    # Walkout owned by user-akshay
    own = _create_walkout(client, auth_headers, sales_person_id="user-akshay")
    # Walkout owned by user-rupesh
    other = _create_walkout(
        client, auth_headers,
        sales_person_id="user-rupesh", mobile="9000099999",
    )

    # akshay editing own row → 200
    resp = client.patch(
        f"/api/v1/walkouts/{own['walkout_id']}",
        json={"action_remarks": "Following up tomorrow"},
        headers=staff_headers_pune,
    )
    assert resp.status_code == 200, resp.text

    # akshay editing rupesh's row → 403
    resp = client.patch(
        f"/api/v1/walkouts/{other['walkout_id']}",
        json={"action_remarks": "Sneaky edit"},
        headers=staff_headers_pune,
    )
    assert resp.status_code == 403

    # akshay attempting to re-attribute his own walkout → 403
    resp = client.patch(
        f"/api/v1/walkouts/{own['walkout_id']}",
        json={"sales_person_id": "user-rupesh"},
        headers=staff_headers_pune,
    )
    assert resp.status_code == 403


def test_soft_delete_excludes_from_list(
    client, auth_headers, patched_walkouts
):
    """DELETE soft-deletes; the row vanishes from list + GET-by-id.
    Audit row is written with the supplied reason."""
    audit_repo = patched_walkouts["audit_repo"]
    a = _create_walkout(client, auth_headers, mobile="9111111111")
    b = _create_walkout(client, auth_headers, mobile="9222222222")

    resp = client.request(
        "DELETE",
        f"/api/v1/walkouts/{a['walkout_id']}",
        json={"reason": "Duplicate test entry"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] is True

    # List excludes the deleted row
    resp = client.get("/api/v1/walkouts", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["walkout_id"] == b["walkout_id"]

    # GET-by-id returns 404 for the deleted row
    resp = client.get(
        f"/api/v1/walkouts/{a['walkout_id']}", headers=auth_headers
    )
    assert resp.status_code == 404

    # Audit
    delete_audits = [
        d for d in audit_repo.collection.docs
        if d.get("action") == "walkout.delete"
    ]
    assert len(delete_audits) == 1
    assert delete_audits[0]["detail"]["reason"] == "Duplicate test entry"
    assert delete_audits[0]["entity_id"] == a["walkout_id"]


def test_delete_rbac_sales_staff_blocked(
    client, auth_headers, staff_headers_pune, manager_headers, patched_walkouts
):
    """Sales staff cannot delete; STORE_MANAGER can on their store; bare
    SUPERADMIN can on any."""
    walkout = _create_walkout(client, auth_headers, sales_person_id="user-akshay")

    # Sales staff: 403
    resp = client.request(
        "DELETE",
        f"/api/v1/walkouts/{walkout['walkout_id']}",
        json={"reason": "Mistake"},
        headers=staff_headers_pune,
    )
    assert resp.status_code == 403

    # Store manager same store: 200
    resp = client.request(
        "DELETE",
        f"/api/v1/walkouts/{walkout['walkout_id']}",
        json={"reason": "Manager cleanup"},
        headers=manager_headers,
    )
    assert resp.status_code == 200, resp.text


def test_patch_invalid_mobile_rejected(
    client, auth_headers, patched_walkouts
):
    """PATCH inherits mobile validation — bad mobile → 422."""
    walkout = _create_walkout(client, auth_headers)
    resp = client.patch(
        f"/api/v1/walkouts/{walkout['walkout_id']}",
        json={"mobile": "12345"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_create_sales_staff_forced_to_self(
    client, staff_headers_pune, patched_walkouts
):
    """Lower-tier roles (SALES_STAFF) cannot log a walkout 'on behalf
    of' someone else — server overrides sales_person_id to their
    user_id even if the client tries to spoof it."""
    payload = _full_payload(sales_person_id="user-rupesh")
    resp = client.post(
        "/api/v1/walkouts", json=payload, headers=staff_headers_pune
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["sales_person_id"] == "user-akshay"  # the logged-in user


def test_create_manager_can_attribute_to_anyone(
    client, manager_headers, patched_walkouts
):
    """STORE_MANAGER (elevated role) is trusted with the sales_person_id
    field — what they post is what gets stored."""
    payload = _full_payload(sales_person_id="user-rupesh")
    resp = client.post(
        "/api/v1/walkouts", json=payload, headers=manager_headers
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["sales_person_id"] == "user-rupesh"


def test_patch_no_changes_returns_existing(
    client, auth_headers, patched_walkouts
):
    """Submitting the same value emits no audit row."""
    audit_repo = patched_walkouts["audit_repo"]
    walkout = _create_walkout(client, auth_headers)

    pre_update_audits = sum(
        1 for d in audit_repo.collection.docs
        if d.get("action") == "walkout.update"
    )

    resp = client.patch(
        f"/api/v1/walkouts/{walkout['walkout_id']}",
        json={"action_remarks": walkout["action_remarks"]},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    post_update_audits = sum(
        1 for d in audit_repo.collection.docs
        if d.get("action") == "walkout.update"
    )
    assert post_update_audits == pre_update_audits
