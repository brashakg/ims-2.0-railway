"""
IMS 2.0 - Payroll period-lock enforcement tests
================================================
Financial control: approving or locking payroll is a posting into the run's
accounting month. Both POST /payroll/approve and POST /payroll/lock MUST refuse
to write into a CLOSED (period-locked) month -- exactly like orders / returns /
vendor-bills / expenses / finance journal-entries already do. The guard is
finance.check_period_locked(...), which raises HTTPException(423) when the
month/year is present in the `period_locks` collection.

These tests are self-contained: they mount ONLY the payroll router on a throwaway
FastAPI app, monkeypatch payroll._get_db to a Mongo-shaped in-memory fake, and
override get_current_user / require_roles. No live Mongo is needed (mirrors the
period-lock test pattern in test_f9_dc_invoice_tally.py, which seeds period_locks
{month, year} and asserts 423).

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_payroll_period_lock.py -q
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import payroll as payroll_router  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


# ===========================================================================
# In-memory Mongo-shaped fake (only the operators these handlers touch)
# ===========================================================================


class _FakeCollection:
    """Minimal Mongo collection over a shared list of dicts. Supports the
    operators the approve/lock + period-lock path use: equality match in
    find_one / update_many, and $set updates."""

    def __init__(self, store):
        self._store = store  # shared list of dicts

    @staticmethod
    def _matches(doc, query):
        for key, cond in query.items():
            if doc.get(key) != cond:
                return False
        return True

    def find_one(self, query=None):
        query = query or {}
        for doc in self._store:
            if self._matches(doc, query):
                return doc
        return None

    def find(self, query=None):
        query = query or {}
        return [d for d in self._store if self._matches(d, query)]

    def insert_one(self, doc):
        self._store.append(doc)

        class _R:
            inserted_id = doc.get("_id")

        return _R()

    def update_many(self, query, update):
        query = query or {}
        sets = (update or {}).get("$set", {}) or {}
        modified = 0
        for doc in self._store:
            if self._matches(doc, query):
                doc.update(sets)
                modified += 1

        class _R:
            modified_count = modified

        return _R()


class _FakeDB:
    """A bag of named collections, Mongo-shaped (get_collection)."""

    def __init__(self):
        self.collections: dict = {}

    def get_collection(self, name):
        return _FakeCollection(self.collections.setdefault(name, []))


# ===========================================================================
# Test client factory
# ===========================================================================

_USER = {
    "user_id": "test-admin-001",
    "username": "testadmin",
    "roles": ["ADMIN", "SUPERADMIN"],
    "store_ids": ["BV-TEST-01"],
    "active_store_id": "BV-TEST-01",
}

_MONTH = 6
_YEAR = 2099
_ENTITY = "ent_period_lock_test"


def _seed_db(locked: bool) -> _FakeDB:
    """A fake DB with two payroll rows for MONTH/YEAR (one DRAFT, one APPROVED)
    plus, when `locked`, a period_locks doc closing that month."""
    db = _FakeDB()
    payroll = db.get_collection("payroll")
    payroll.insert_one(
        {
            "employee_id": "E-DRAFT",
            "entity_id": _ENTITY,
            "month": _MONTH,
            "year": _YEAR,
            "status": "DRAFT",
            "net_salary": 30000,
        }
    )
    payroll.insert_one(
        {
            "employee_id": "E-APPROVED",
            "entity_id": _ENTITY,
            "month": _MONTH,
            "year": _YEAR,
            "status": "APPROVED",
            "net_salary": 20000,
        }
    )
    if locked:
        db.get_collection("period_locks").insert_one(
            {"month": _MONTH, "year": _YEAR}
        )
    return db


def _client(db: _FakeDB, monkeypatch) -> TestClient:
    """Mount ONLY the payroll router with _get_db monkeypatched to `db` and the
    auth dependency overridden to an ADMIN user."""
    monkeypatch.setattr(payroll_router, "_get_db", lambda: db)

    app = FastAPI()
    app.include_router(payroll_router.router, prefix="/api/v1/payroll")
    app.dependency_overrides[get_current_user] = lambda: _USER
    return TestClient(app)


def _body():
    return {"month": _MONTH, "year": _YEAR, "entity_id": _ENTITY}


# ===========================================================================
# approve
# ===========================================================================


def test_approve_blocked_when_period_locked(monkeypatch):
    db = _seed_db(locked=True)
    c = _client(db, monkeypatch)
    r = c.post("/api/v1/payroll/approve", json=_body())
    assert r.status_code == 423, r.text
    # The DRAFT row must NOT have been flipped to APPROVED.
    rows = db.get_collection("payroll").find({"employee_id": "E-DRAFT"})
    assert rows[0]["status"] == "DRAFT"


def test_approve_succeeds_when_period_open(monkeypatch):
    db = _seed_db(locked=False)
    c = _client(db, monkeypatch)
    r = c.post("/api/v1/payroll/approve", json=_body())
    assert r.status_code == 200, r.text
    assert r.json()["approved"] == 1  # only the DRAFT row moves to APPROVED
    rows = db.get_collection("payroll").find({"employee_id": "E-DRAFT"})
    assert rows[0]["status"] == "APPROVED"


# ===========================================================================
# lock
# ===========================================================================


def test_lock_blocked_when_period_locked(monkeypatch):
    db = _seed_db(locked=True)
    c = _client(db, monkeypatch)
    r = c.post("/api/v1/payroll/lock", json=_body())
    assert r.status_code == 423, r.text
    # The APPROVED row must NOT have been flipped to PAID.
    rows = db.get_collection("payroll").find({"employee_id": "E-APPROVED"})
    assert rows[0]["status"] == "APPROVED"


def test_lock_succeeds_when_period_open(monkeypatch):
    db = _seed_db(locked=False)
    c = _client(db, monkeypatch)
    r = c.post("/api/v1/payroll/lock", json=_body())
    assert r.status_code == 200, r.text
    assert r.json()["locked"] == 1  # only the APPROVED row moves to PAID
    rows = db.get_collection("payroll").find({"employee_id": "E-APPROVED"})
    assert rows[0]["status"] == "PAID"
