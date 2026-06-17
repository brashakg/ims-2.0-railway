"""
IMS 2.0 - Idempotency on money-side writes (POS-14)
===================================================
Regression tests that lock in the ``Idempotency-Key`` replay contract on the
two money-adjacent create endpoints that mirror the order-create / add-payment
pattern:

  * POST /api/v1/returns   (returns / exchange / credit-note refunds)
  * POST /api/v1/expenses  (expense claims)

Contract (POS-14): when a request carries a non-empty ``Idempotency-Key`` header
and a record with that key already exists, the EXISTING record is returned
(``_idempotent_replay: True``) instead of creating a duplicate financial record.
A double-clicked / retried refund or expense must never book twice. Fail-soft:
no header -> normal create; the key is persisted on the stored doc.

These tests drive the REAL FastAPI handlers via TestClient with in-memory fakes
(no live DB), so a regression in the handler -- not a re-implemented copy of its
logic -- is what fails them.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import jwt  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import returns as returns_router  # noqa: E402
from api.routers import expenses as expenses_router  # noqa: E402
from api.routers import auth as auth_mod  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


# ===========================================================================
# Shared fakes
# ===========================================================================


class _FakeResult:
    def __init__(self, matched=1):
        self.matched_count = matched
        self.modified_count = matched


class _FakeColl:
    """In-memory Mongo collection good enough for find_one / find / insert_one."""

    def __init__(self):
        self.docs: list = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return _FakeResult(1)

    def find_one(self, query=None, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (query or {}).items()):
                out = dict(d)
                out.pop("_id", None)
                return out
        return None

    def find(self, query=None, projection=None):
        q = query or {}
        return _FakeCursor(
            [d for d in self.docs if all(d.get(k) == v for k, v in q.items())]
        )


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def skip(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def __iter__(self):
        return iter(self._docs)


# ===========================================================================
# GAP 1a -- returns idempotency
# ===========================================================================


_DEFAULT_ORDER_ITEMS = [
    {
        "item_id": "li1",
        "product_id": "PRD-1",
        "product_name": "Ray-Ban Aviator",
        "sku": "RB-1",
        "quantity": 2,
        "unit_price": 1500,
        "gst_rate": 5,
        "taxable_value": 2857.14,
        "tax_amount": 142.86,
    }
]


class _FakeOrderRepo:
    def __init__(self, order):
        order = dict(order)
        order.setdefault("items", [dict(li) for li in _DEFAULT_ORDER_ITEMS])
        order.setdefault("amount_paid", 1_000_000_000.0)
        order.setdefault("status", "DELIVERED")
        self._order = order
        # The returns router's atomic claim reaches for repo.collection; an
        # object lacking find_one_and_update makes the claim fail-soft (True).
        self.collection = None

    def find_by_id(self, oid):
        return self._order if self._order.get("order_id") == oid else None

    def find_by_order_number(self, num):
        return self._order if self._order.get("order_number") == num else None


class _FakeCustomerRepo:
    def __init__(self):
        self.customers = {
            "CUST-1": {"customer_id": "CUST-1", "name": "Asha", "store_credit": 0.0}
        }

    def find_by_id(self, cid):
        return self.customers.get(cid)

    def update(self, cid, data):
        if cid in self.customers:
            self.customers[cid].update(data)
        return True


def _staff_token(roles, store_id="BV-PUN-01", uid="u1"):
    return jwt.encode(
        {
            "sub": uid,
            "user_id": uid,
            "username": "tester",
            "roles": roles,
            "active_store_id": store_id,
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        auth_mod.SECRET_KEY,
        algorithm=auth_mod.ALGORITHM,
    )


@pytest.fixture
def returns_ctx(monkeypatch):
    app = FastAPI()
    app.include_router(returns_router.router, prefix="/api/v1/returns")

    order = {
        "order_id": "ORD-1",
        "order_number": "INV-1001",
        "customer_id": "CUST-1",
        "customer_name": "Asha",
        "payment_method": "UPI",
        "store_id": "BV-PUN-01",
    }
    order_repo = _FakeOrderRepo(order)
    customer_repo = _FakeCustomerRepo()
    returns_coll = _FakeColl()
    ledger_coll = _FakeColl()

    class _FakeDB:
        is_connected = True

        def __init__(self):
            self.db = self

        def get_collection(self, name):
            return {
                "returns": returns_coll,
                "credit_note_ledger": ledger_coll,
            }.get(name, _FakeColl())

    fake_db = _FakeDB()
    monkeypatch.setattr(returns_router, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(returns_router, "get_customer_repository", lambda: customer_repo)
    monkeypatch.setattr(returns_router, "get_product_repository", lambda: None)
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: None)
    monkeypatch.setattr("api.dependencies.get_db", lambda: fake_db, raising=False)
    monkeypatch.setattr(
        "api.dependencies.get_audit_repository", lambda: None, raising=False
    )
    return {"client": TestClient(app), "returns_coll": returns_coll}


def _return_payload():
    return {
        "order_id": "ORD-1",
        "store_id": "BV-PUN-01",
        "return_type": "RETURN",
        "items": [
            {
                "order_item_id": "li1",
                "product_id": "PRD-1",
                "product_name": "Ray-Ban Aviator",
                "sku": "RB-1",
                "return_qty": 1,
                "unit_price": 1500,
                "reason": "DEFECTIVE",
                "condition": "GOOD",
            }
        ],
    }


def test_return_duplicate_idempotency_key_replays_not_duplicates(returns_ctx):
    """Two POSTs with the SAME Idempotency-Key -> one refund booked, the second
    is a replay of the first (same return_id, _idempotent_replay True)."""
    client = returns_ctx["client"]
    coll = returns_ctx["returns_coll"]
    headers = {
        "Authorization": f"Bearer {_staff_token(['CASHIER'])}",
        "Idempotency-Key": "RET-IDEM-KEY-1",
    }

    r1 = client.post("/api/v1/returns", json=_return_payload(), headers=headers)
    assert r1.status_code == 201, r1.text
    first_id = r1.json()["return_id"]
    assert first_id.startswith("RET-")
    assert len(coll.docs) == 1  # exactly one financial record persisted
    # The key is stamped on the stored doc so the guard can find it.
    assert coll.docs[0]["idempotency_key"] == "RET-IDEM-KEY-1"

    r2 = client.post("/api/v1/returns", json=_return_payload(), headers=headers)
    assert r2.status_code == 201, r2.text
    body2 = r2.json()
    assert body2["return_id"] == first_id  # SAME record, not a new one
    assert body2.get("_idempotent_replay") is True
    assert len(coll.docs) == 1  # still ONE record -- no double refund


def test_return_distinct_keys_create_distinct_records(returns_ctx):
    """A different Idempotency-Key is a genuinely different refund -> two docs."""
    client = returns_ctx["client"]
    coll = returns_ctx["returns_coll"]
    auth = {"Authorization": f"Bearer {_staff_token(['CASHIER'])}"}

    r1 = client.post(
        "/api/v1/returns",
        json=_return_payload(),
        headers={**auth, "Idempotency-Key": "RET-KEY-A"},
    )
    r2 = client.post(
        "/api/v1/returns",
        json=_return_payload(),
        headers={**auth, "Idempotency-Key": "RET-KEY-B"},
    )
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["return_id"] != r2.json()["return_id"]
    assert len(coll.docs) == 2


def test_return_without_key_is_unaffected(returns_ctx):
    """No header -> behaviour is the normal (non-idempotent) create path."""
    client = returns_ctx["client"]
    auth = {"Authorization": f"Bearer {_staff_token(['CASHIER'])}"}
    r = client.post("/api/v1/returns", json=_return_payload(), headers=auth)
    assert r.status_code == 201
    assert r.json().get("_idempotent_replay") is None


# ===========================================================================
# GAP 1b -- expense idempotency
# ===========================================================================


class _FakeExpenseRepo:
    """Minimal expense repo: create() stores, find_one() matches a flat filter."""

    def __init__(self):
        self.docs: list = []

    def create(self, doc):
        self.docs.append(dict(doc))
        return dict(doc)

    def find_one(self, query):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (query or {}).items()):
                return dict(d)
        return None

    def find_many(self, *_a, **_k):
        return list(self.docs)


def _expense_client(monkeypatch, repo, roles=("SALES_STAFF",)):
    app = FastAPI()
    app.include_router(expenses_router.router, prefix="/expenses")

    async def _fake_user():
        return {
            "user_id": "u1",
            "full_name": "Test User",
            "active_store_id": "store-001",
            "roles": list(roles),
        }

    app.dependency_overrides[get_current_user] = _fake_user
    monkeypatch.setattr(expenses_router, "get_expense_repository", lambda: repo)
    # Keep governance gates fail-soft / no-op so the test isolates idempotency.
    monkeypatch.setattr(expenses_router, "_period_locked", lambda *_a, **_k: False)
    monkeypatch.setattr(expenses_router, "_outstanding_advances", lambda *_a, **_k: [])
    monkeypatch.setattr(expenses_router, "_is_admin", lambda *_a, **_k: True)
    return TestClient(app)


def _expense_payload():
    return {
        "category": "TRAVEL",
        "amount": 250.0,
        "description": "cab fare",
        "expense_date": "2026-05-21",
    }


def test_expense_duplicate_idempotency_key_replays_not_duplicates(monkeypatch):
    """Two expense POSTs with the SAME Idempotency-Key -> one expense, the second
    a replay of the first (same expense_id, _idempotent_replay True)."""
    repo = _FakeExpenseRepo()
    client = _expense_client(monkeypatch, repo)
    headers = {"Idempotency-Key": "EXP-IDEM-KEY-1"}

    r1 = client.post("/expenses", json=_expense_payload(), headers=headers)
    assert r1.status_code == 201, r1.text
    first_id = r1.json()["expense_id"]
    assert len(repo.docs) == 1
    assert repo.docs[0]["idempotency_key"] == "EXP-IDEM-KEY-1"

    r2 = client.post("/expenses", json=_expense_payload(), headers=headers)
    assert r2.status_code == 201, r2.text
    body2 = r2.json()
    assert body2["expense_id"] == first_id
    assert body2.get("_idempotent_replay") is True
    assert len(repo.docs) == 1  # still ONE expense -- no duplicate claim


def test_expense_distinct_keys_create_distinct_records(monkeypatch):
    repo = _FakeExpenseRepo()
    client = _expense_client(monkeypatch, repo)

    r1 = client.post(
        "/expenses", json=_expense_payload(), headers={"Idempotency-Key": "EXP-A"}
    )
    r2 = client.post(
        "/expenses", json=_expense_payload(), headers={"Idempotency-Key": "EXP-B"}
    )
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["expense_id"] != r2.json()["expense_id"]
    assert len(repo.docs) == 2


def test_expense_without_key_is_unaffected(monkeypatch):
    repo = _FakeExpenseRepo()
    client = _expense_client(monkeypatch, repo)
    r = client.post("/expenses", json=_expense_payload())
    assert r.status_code == 201
    assert r.json().get("_idempotent_replay") is None
    assert repo.docs[0]["idempotency_key"] is None
