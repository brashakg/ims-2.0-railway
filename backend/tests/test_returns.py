"""
IMS 2.0 - Customer returns / exchange / credit-note tests
=========================================================
Two layers:
  1. Pure money-math engine (returns_engine): returned_value + exchange
     settlement (COLLECT / REFUND / EVEN), rounding, validation errors.
  2. Endpoint smoke tests via FastAPI TestClient with monkeypatched fake
     repos/collections - no live DB. Asserts amounts, auth, store scoping,
     restock + store-credit-ledger side effects.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import jwt  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import returns_engine as engine  # noqa: E402
from api.routers import returns as returns_router  # noqa: E402
from api.routers import auth as auth_mod  # noqa: E402


# ============================================================================
# 1. ENGINE - returned_value
# ============================================================================


def test_returned_value_basic():
    items = [
        {"return_qty": 2, "unit_price": 1500},
        {"return_qty": 1, "unit_price": 500.50},
    ]
    assert engine.returned_value(items) == 3500.50


def test_returned_value_uses_quantity_alias():
    # Accept `quantity` when `return_qty` is absent.
    assert engine.returned_value([{"quantity": 3, "unit_price": 100}]) == 300.0


def test_returned_value_empty_is_zero():
    assert engine.returned_value([]) == 0.0
    assert engine.returned_value(None) == 0.0


def test_returned_value_rounding():
    # 3 * 33.333 = 99.999 -> 100.00 at 2dp
    assert engine.returned_value([{"return_qty": 3, "unit_price": 33.333}]) == 100.0


def test_returned_value_rejects_negative_qty():
    with pytest.raises(ValueError):
        engine.returned_value([{"return_qty": -1, "unit_price": 100}])


def test_returned_value_rejects_negative_price():
    with pytest.raises(ValueError):
        engine.returned_value([{"return_qty": 1, "unit_price": -100}])


def test_returned_value_rejects_non_numeric():
    with pytest.raises(ValueError):
        engine.returned_value([{"return_qty": "abc", "unit_price": 100}])


# ============================================================================
# 1. ENGINE - exchange_settlement
# ============================================================================


def test_exchange_collect():
    # Replacement worth more -> customer pays the difference.
    s = engine.exchange_settlement(1000.0, [{"quantity": 1, "unit_price": 1500}])
    assert s["direction"] == engine.COLLECT
    assert s["replacement_total"] == 1500.0
    assert s["returned_value"] == 1000.0
    assert s["difference"] == 500.0


def test_exchange_refund():
    # Replacement worth less -> shop owes the customer (difference is absolute).
    s = engine.exchange_settlement(2000.0, [{"quantity": 1, "unit_price": 1200}])
    assert s["direction"] == engine.REFUND
    assert s["difference"] == 800.0


def test_exchange_even():
    s = engine.exchange_settlement(1000.0, [{"quantity": 2, "unit_price": 500}])
    assert s["direction"] == engine.EVEN
    assert s["difference"] == 0.0


def test_exchange_even_within_epsilon():
    # A sub-paisa gap must read EVEN, not a 0.00x COLLECT.
    s = engine.exchange_settlement(1000.0, [{"quantity": 1, "unit_price": 1000.004}])
    assert s["direction"] == engine.EVEN


def test_exchange_multi_line_replacement():
    s = engine.exchange_settlement(
        500.0,
        [
            {"quantity": 2, "unit_price": 300},  # 600
            {"quantity": 1, "unit_price": 150},  # 150
        ],
    )
    assert s["replacement_total"] == 750.0
    assert s["direction"] == engine.COLLECT
    assert s["difference"] == 250.0


def test_exchange_empty_replacement_is_full_refund():
    # Returning with no replacement chosen -> shop owes the whole returned value.
    s = engine.exchange_settlement(900.0, [])
    assert s["direction"] == engine.REFUND
    assert s["difference"] == 900.0


def test_exchange_rounding():
    s = engine.exchange_settlement(33.33, [{"quantity": 1, "unit_price": 66.66}])
    assert s["difference"] == 33.33
    assert s["direction"] == engine.COLLECT


def test_exchange_rejects_negative():
    with pytest.raises(ValueError):
        engine.exchange_settlement(100.0, [{"quantity": -1, "unit_price": 50}])
    with pytest.raises(ValueError):
        engine.exchange_settlement(-5.0, [{"quantity": 1, "unit_price": 50}])


# ============================================================================
# 2. ENDPOINT smoke tests
# ============================================================================


class _FakeOrderRepo:
    def __init__(self, order=None):
        self._order = order

    def find_by_id(self, oid):
        if self._order and self._order.get("order_id") == oid:
            return self._order
        return None

    def find_by_order_number(self, num):
        if self._order and self._order.get("order_number") == num:
            return self._order
        return None


class _FakeCustomerRepo:
    def __init__(self):
        self.customers = {
            "CUST-1": {"customer_id": "CUST-1", "name": "Asha", "store_credit": 0.0}
        }
        self.updates = []

    def find_by_id(self, cid):
        return self.customers.get(cid)

    def update(self, cid, data):
        self.updates.append((cid, data))
        if cid in self.customers:
            self.customers[cid].update(data)
        return True


class _FakeResult:
    def __init__(self, matched=1):
        self.matched_count = matched
        self.modified_count = matched


class _FakeProductsColl:
    """Records $inc restock calls on stock_quantity."""

    def __init__(self):
        self.inc_calls = []

    def update_one(self, flt, update):
        self.inc_calls.append((flt, update))
        # Pretend product_id matches, _id does not (so fallback path is exercised
        # only when product_id misses).
        if "product_id" in flt:
            return _FakeResult(1)
        return _FakeResult(0)


class _FakeColl:
    """Generic in-memory collection for `returns` + `credit_note_ledger`."""

    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return _FakeResult(1)

    def find(self, query=None, projection=None):
        q = query or {}
        matched = [
            d for d in self.docs if all(d.get(k) == v for k, v in q.items())
        ]
        return _FakeCursor(matched)

    def find_one(self, query=None, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (query or {}).items()):
                out = dict(d)
                out.pop("_id", None)
                return out
        return None

    def count_documents(self, query=None):
        q = query or {}
        return sum(
            1 for d in self.docs if all(d.get(k) == v for k, v in q.items())
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
def ctx(monkeypatch):
    """Wire the returns router with fake repos + collections. Returns a bag of
    handles so each test can assert side effects."""
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
    products_coll = _FakeProductsColl()
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
                "products": products_coll,
            }.get(name, _FakeColl())

    fake_db = _FakeDB()

    monkeypatch.setattr(returns_router, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(
        returns_router, "get_customer_repository", lambda: customer_repo
    )
    monkeypatch.setattr(returns_router, "get_product_repository", lambda: None)
    # Patch the local get_db import target used by _get_db().
    monkeypatch.setattr(
        "api.dependencies.get_db", lambda: fake_db, raising=False
    )
    # Audit repo absent -> fail-soft path.
    monkeypatch.setattr(
        "api.dependencies.get_audit_repository", lambda: None, raising=False
    )

    return {
        "client": TestClient(app),
        "order_repo": order_repo,
        "customer_repo": customer_repo,
        "products_coll": products_coll,
        "returns_coll": returns_coll,
        "ledger_coll": ledger_coll,
    }


def _payload(**over):
    base = {
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
    base.update(over)
    return base


def test_create_requires_auth(ctx):
    r = ctx["client"].post("/api/v1/returns", json=_payload())
    assert r.status_code == 401


def test_create_forbidden_for_wrong_role(ctx):
    tok = _staff_token(["OPTOMETRIST"])
    r = ctx["client"].post(
        "/api/v1/returns", json=_payload(), headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 403


def test_create_return_records_refund_and_restocks(ctx):
    tok = _staff_token(["CASHIER"])
    r = ctx["client"].post(
        "/api/v1/returns", json=_payload(), headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 201
    data = r.json()
    assert data["return_type"] == "RETURN"
    assert data["returned_value"] == 1500.0
    assert data["refund_amount"] == 1500.0
    # Default refund method falls back to the order's payment method.
    assert data["refund_method"] == "UPI"
    assert data["return_id"].startswith("RET-")
    # GOOD item was restocked via $inc on stock_quantity.
    assert ctx["products_coll"].inc_calls
    flt, update = ctx["products_coll"].inc_calls[0]
    assert flt == {"product_id": "PRD-1"}
    assert update == {"$inc": {"stock_quantity": 1}}
    assert data["restocked"][0]["applied"] is True
    # Persisted.
    assert ctx["returns_coll"].docs[0]["return_id"] == data["return_id"]


def test_damaged_item_not_restocked(ctx):
    tok = _staff_token(["ADMIN"])
    payload = _payload()
    payload["items"][0]["condition"] = "DAMAGED"
    r = ctx["client"].post(
        "/api/v1/returns", json=payload, headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 201
    assert ctx["products_coll"].inc_calls == []  # nothing restocked
    assert r.json()["restocked"] == []


def test_create_credit_note_issues_store_credit(ctx):
    tok = _staff_token(["STORE_MANAGER"])
    payload = _payload(return_type="CREDIT_NOTE", customer_id="CUST-1")
    payload["items"][0]["unit_price"] = 2000
    r = ctx["client"].post(
        "/api/v1/returns", json=payload, headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 201
    data = r.json()
    assert data["credit_amount"] == 2000.0
    assert data["refund_method"] == "STORE_CREDIT"
    # A ledger entry was written + the customer balance bumped.
    assert len(ctx["ledger_coll"].docs) == 1
    entry = ctx["ledger_coll"].docs[0]
    assert entry["type"] == "ISSUED"
    assert entry["amount"] == 2000.0
    assert entry["balance_after"] == 2000.0
    assert entry["ref"] == data["return_id"]
    assert ctx["customer_repo"].customers["CUST-1"]["store_credit"] == 2000.0


def test_exchange_collect(ctx):
    tok = _staff_token(["SALES_CASHIER"])
    payload = _payload(
        return_type="EXCHANGE",
        customer_id="CUST-1",
        replacement_items=[
            {"product_id": "PRD-9", "name": "Oakley", "sku": "OK-9", "quantity": 1, "unit_price": 2500}
        ],
    )
    # returned_value = 1500, replacement = 2500 -> COLLECT 1000
    r = ctx["client"].post(
        "/api/v1/returns", json=payload, headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 201
    data = r.json()
    assert data["settlement"]["direction"] == "COLLECT"
    assert data["collect_amount"] == 1000.0
    assert data["credit_amount"] is None
    # COLLECT must NOT issue store credit.
    assert ctx["ledger_coll"].docs == []


def test_exchange_refund_issues_credit_for_difference(ctx):
    tok = _staff_token(["ADMIN"])
    payload = _payload(
        return_type="EXCHANGE",
        customer_id="CUST-1",
        replacement_items=[
            {"product_id": "PRD-9", "name": "Local frame", "sku": "LF-9", "quantity": 1, "unit_price": 900}
        ],
    )
    # returned_value = 1500, replacement = 900 -> REFUND 600 as store credit
    r = ctx["client"].post(
        "/api/v1/returns", json=payload, headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 201
    data = r.json()
    assert data["settlement"]["direction"] == "REFUND"
    assert data["credit_amount"] == 600.0
    assert len(ctx["ledger_coll"].docs) == 1
    assert ctx["ledger_coll"].docs[0]["amount"] == 600.0
    assert ctx["customer_repo"].customers["CUST-1"]["store_credit"] == 600.0


def test_create_rejects_no_items(ctx):
    tok = _staff_token(["ADMIN"])
    payload = _payload()
    payload["items"][0]["return_qty"] = 0
    r = ctx["client"].post(
        "/api/v1/returns", json=payload, headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 400


def test_list_returns_store_scoped(ctx):
    tok_admin = _staff_token(["ADMIN"])
    tok_cashier_other = _staff_token(["CASHIER"], store_id="BV-OTHER-02")
    # Create one return at BV-PUN-01.
    ctx["client"].post(
        "/api/v1/returns",
        json=_payload(),
        headers={"Authorization": f"Bearer {tok_admin}"},
    )
    # Cashier at a different store sees none (pinned to their active store).
    r = ctx["client"].get(
        "/api/v1/returns", headers={"Authorization": f"Bearer {tok_cashier_other}"}
    )
    assert r.status_code == 200
    assert r.json()["returns"] == []
    # Admin can scope to the originating store and see it.
    r2 = ctx["client"].get(
        "/api/v1/returns",
        params={"store_id": "BV-PUN-01"},
        headers={"Authorization": f"Bearer {tok_admin}"},
    )
    assert r2.json()["total"] == 1


def test_get_return_detail(ctx):
    tok = _staff_token(["ADMIN"])
    created = ctx["client"].post(
        "/api/v1/returns",
        json=_payload(),
        headers={"Authorization": f"Bearer {tok}"},
    ).json()
    rid = created["return_id"]
    r = ctx["client"].get(
        f"/api/v1/returns/{rid}", headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 200
    assert r.json()["return_id"] == rid
    # Unknown id 404s.
    assert (
        ctx["client"]
        .get("/api/v1/returns/NOPE", headers={"Authorization": f"Bearer {tok}"})
        .status_code
        == 404
    )
