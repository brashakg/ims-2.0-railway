"""
IMS 2.0 — Go-live readiness checklist tests
===========================================
GET /stores/go-live-checklist aggregates the real prerequisites for the first
live sale. Covers:
  - Role gate (ADMIN/SUPERADMIN only).
  - DB-absent -> FAIL with a database check, ready=False.
  - All-ready -> every check PASS, ready=True.
  - Blockers: no stores / no products -> FAIL -> ready=False.
  - Warnings: missing GSTIN / blank tax codes / no staff -> WARN, still ready.
"""

from __future__ import annotations

import os
import sys
from typing import Dict

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-go-live")

_URL = "/api/v1/stores/go-live-checklist"


# ============================================================================
# Fake DB: count_documents driven by a {(collection, frozenset(query)) -> n}
# style map, simplified to a callable the test supplies per collection.
# ============================================================================
class FakeCollection:
    def __init__(self, counter):
        self._counter = counter

    def count_documents(self, query):
        return self._counter(query)


class FakeDB:
    def __init__(self, counters: Dict[str, object]):
        self.is_connected = True
        self._counters = counters

    def get_collection(self, name):
        fn = self._counters.get(name)
        if fn is None:
            return FakeCollection(lambda _q: 0)
        return FakeCollection(fn if callable(fn) else (lambda _q, n=fn: n))


@pytest.fixture
def patched(monkeypatch):
    from api.routers import stores as stores_module

    def install(counters):
        monkeypatch.setattr(stores_module, "get_db", lambda: FakeDB(counters))

    return install


def _by(name):
    """Return a dict keyed map -> count helper isn't needed; tests pass ints."""
    return name


# ============================================================================
# Role gate
# ============================================================================
def test_requires_auth(client):
    assert client.get(_URL).status_code == 401


def test_staff_blocked(client, staff_headers):
    assert client.get(_URL, headers=staff_headers).status_code == 403


# ============================================================================
# DB-absent
# ============================================================================
def test_db_absent_reports_fail(client, auth_headers, monkeypatch):
    from api.routers import stores as stores_module

    class _NoDB:
        is_connected = False

        def get_collection(self, _n):  # pragma: no cover
            return None

    monkeypatch.setattr(stores_module, "get_db", lambda: _NoDB())
    body = client.get(_URL, headers=auth_headers).json()
    assert body["ready"] is False
    assert body["summary"]["fail"] == 1
    assert body["checks"][0]["key"] == "database"


# ============================================================================
# Aggregation
# ============================================================================
def test_all_ready(client, auth_headers, patched):
    # Everything present, nothing missing.
    patched(
        {
            "stores": lambda q: 0 if "$or" in q else 2,  # 2 stores, 0 missing GSTIN
            "users": 3,
            "products": lambda q: 0 if "$or" in q else 50,  # 50 products, 0 bad tax
            "invoice_settings": 1,
        }
    )
    body = client.get(_URL, headers=auth_headers).json()
    assert body["ready"] is True
    assert body["summary"]["fail"] == 0
    statuses = {c["key"]: c["status"] for c in body["checks"]}
    assert statuses["stores"] == "PASS"
    assert statuses["store_gstin"] == "PASS"
    assert statuses["products"] == "PASS"
    assert statuses["tax_codes"] == "PASS"
    assert statuses["invoice"] == "PASS"


def test_no_stores_is_blocker(client, auth_headers, patched):
    patched({"stores": 0, "users": 0, "products": 0, "invoice_settings": 0})
    body = client.get(_URL, headers=auth_headers).json()
    assert body["ready"] is False
    statuses = {c["key"]: c["status"] for c in body["checks"]}
    assert statuses["stores"] == "FAIL"
    assert statuses["products"] == "FAIL"
    # store_gstin / tax_codes are conditional on having stores/products — absent here.
    assert "store_gstin" not in statuses
    assert "tax_codes" not in statuses


def test_missing_gstin_and_tax_codes_are_warnings(client, auth_headers, patched):
    patched(
        {
            "stores": lambda q: 1 if "$or" in q else 2,  # 2 stores, 1 missing GSTIN
            "users": 2,
            "products": lambda q: 4 if "$or" in q else 20,  # 20 products, 4 bad tax
            "invoice_settings": 1,
        }
    )
    body = client.get(_URL, headers=auth_headers).json()
    # WARNs don't block go-live (only FAIL does); no hard blockers here.
    assert body["ready"] is True
    statuses = {c["key"]: c["status"] for c in body["checks"]}
    assert statuses["store_gstin"] == "WARN"
    assert statuses["tax_codes"] == "WARN"
    counts = {c["key"]: c["count"] for c in body["checks"]}
    assert counts["store_gstin"] == 1
    assert counts["tax_codes"] == 4


def test_no_staff_is_warning(client, auth_headers, patched):
    patched(
        {
            "stores": lambda q: 0 if "$or" in q else 1,
            "users": 0,  # no staff
            "products": lambda q: 0 if "$or" in q else 5,
            "invoice_settings": 1,
        }
    )
    body = client.get(_URL, headers=auth_headers).json()
    statuses = {c["key"]: c["status"] for c in body["checks"]}
    assert statuses["staff"] == "WARN"
    # staff is a warning, not a blocker
    assert body["ready"] is True
