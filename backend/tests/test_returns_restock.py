"""
IMS 2.0 - Returns -> serialized-stock restock tests
===================================================
Two layers:
  1. Pure decision engine (restock_engine): resellable-vs-hold per line +
     plan_restock unit expansion + idempotency guard. No DB.
  2. Endpoint/integration tests via FastAPI TestClient with a fake serialized
     `stock` repo - asserts that a RETURN/EXCHANGE actually re-activates the
     original SOLD unit (or mints a fresh AVAILABLE one), is idempotent on
     retry, and stays fail-soft when the stock layer is down.

Money math is intentionally NOT re-tested here (covered by test_returns.py);
this file is purely the inventory side.
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

from api.services import restock_engine  # noqa: E402
from api.routers import returns as returns_router  # noqa: E402
from api.routers import auth as auth_mod  # noqa: E402


# ============================================================================
# 1. PURE ENGINE - should_restock
# ============================================================================


def test_good_condition_is_resellable():
    line = {"product_id": "PRD-1", "condition": "GOOD", "return_qty": 1}
    assert restock_engine.should_restock(line) is True


def test_damaged_condition_not_resellable():
    line = {"product_id": "PRD-1", "condition": "DAMAGED", "return_qty": 1}
    assert restock_engine.should_restock(line) is False


def test_opened_condition_not_resellable():
    line = {"product_id": "PRD-1", "condition": "OPENED", "return_qty": 1}
    assert restock_engine.should_restock(line) is False


def test_condition_defaults_to_good_when_missing():
    line = {"product_id": "PRD-1", "return_qty": 1}
    assert restock_engine.should_restock(line) is True


def test_restock_flag_false_holds_good_unit():
    line = {
        "product_id": "PRD-1",
        "condition": "GOOD",
        "return_qty": 1,
        "restock": False,
    }
    assert restock_engine.should_restock(line) is False


def test_restock_flag_string_false_holds_unit():
    line = {
        "product_id": "PRD-1",
        "condition": "GOOD",
        "return_qty": 1,
        "restock": "false",
    }
    assert restock_engine.should_restock(line) is False


def test_zero_qty_not_resellable():
    line = {"product_id": "PRD-1", "condition": "GOOD", "return_qty": 0}
    assert restock_engine.should_restock(line) is False


def test_missing_product_id_not_resellable():
    line = {"condition": "GOOD", "return_qty": 1}
    assert restock_engine.should_restock(line) is False


def test_non_dict_line_not_resellable():
    assert restock_engine.should_restock("nope") is False  # type: ignore[arg-type]


# ============================================================================
# 1. PURE ENGINE - plan_restock
# ============================================================================


def test_plan_expands_one_entry_per_unit():
    items = [
        {"product_id": "PRD-1", "condition": "GOOD", "return_qty": 2},
        {"product_id": "PRD-2", "condition": "GOOD", "return_qty": 1},
    ]
    plan = restock_engine.plan_restock(items)
    assert plan["unit_count"] == 3
    pids = sorted(u["product_id"] for u in plan["units"])
    assert pids == ["PRD-1", "PRD-1", "PRD-2"]
    assert plan["already_applied"] is False


def test_plan_skips_damaged_and_records_reason():
    items = [
        {"product_id": "PRD-1", "condition": "GOOD", "return_qty": 1},
        {"product_id": "PRD-2", "condition": "DAMAGED", "return_qty": 1},
    ]
    plan = restock_engine.plan_restock(items)
    assert plan["unit_count"] == 1
    assert len(plan["skipped"]) == 1
    assert plan["skipped"][0]["product_id"] == "PRD-2"
    assert plan["skipped"][0]["reason"] == "CONDITION_DAMAGED"


def test_plan_idempotent_when_already_applied():
    items = [{"product_id": "PRD-1", "condition": "GOOD", "return_qty": 5}]
    plan = restock_engine.plan_restock(items, already_applied=True)
    assert plan["already_applied"] is True
    assert plan["unit_count"] == 0
    assert plan["units"] == []


def test_plan_opted_out_reason():
    items = [
        {
            "product_id": "PRD-1",
            "condition": "GOOD",
            "return_qty": 1,
            "restock": False,
        }
    ]
    plan = restock_engine.plan_restock(items)
    assert plan["unit_count"] == 0
    assert plan["skipped"][0]["reason"] == "RESTOCK_OPTED_OUT"


def test_plan_fractional_qty_rounds_to_units():
    items = [{"product_id": "PRD-1", "condition": "GOOD", "return_qty": 2.0}]
    plan = restock_engine.plan_restock(items)
    assert plan["unit_count"] == 2


# ============================================================================
# 2. INTEGRATION - fakes
# ============================================================================


class _FakeStockRepo:
    """In-memory serialized stock. Mirrors StockRepository.find_many / create /
    update against a list of {stock_id, product_id, store_id, status, ...}."""

    def __init__(self, units=None):
        self.units = list(units or [])
        self._seq = 0

    def find_many(self, query):
        out = []
        for u in self.units:
            ok = True
            for k, v in (query or {}).items():
                if isinstance(v, dict) and "$ne" in v:
                    if u.get(k) == v["$ne"]:
                        ok = False
                        break
                elif u.get(k) != v:
                    ok = False
                    break
            if ok:
                out.append(u)
        return out

    def create(self, data):
        self._seq += 1
        data = dict(data)
        data["stock_id"] = f"NEW-{self._seq}"
        self.units.append(data)
        return data

    def update(self, stock_id, data):
        for u in self.units:
            if u.get("stock_id") == stock_id:
                u.update(data)
                return True
        return False


class _FakeResult:
    def __init__(self, matched=1):
        self.matched_count = matched
        self.modified_count = matched


def _doc_matches(d, query):
    """Tiny mongo-like matcher: scalar equality + $ne. All other operators
    are not used by the returns flow today, so we keep this minimal."""
    for k, v in (query or {}).items():
        if isinstance(v, dict) and "$ne" in v:
            if d.get(k) == v["$ne"]:
                return False
        elif d.get(k) != v:
            return False
    return True


class _FakeColl:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return _FakeResult(1)

    def find_one(self, query=None, projection=None):
        for d in self.docs:
            if _doc_matches(d, query):
                out = dict(d)
                out.pop("_id", None)
                return out
        return None

    def update_one(self, query, update):
        for d in self.docs:
            if _doc_matches(d, query):
                d.update(update.get("$set", {}))
                return _FakeResult(1)
        return _FakeResult(0)

    def find_one_and_update(self, query, update, return_document=None):
        for d in self.docs:
            if _doc_matches(d, query):
                d.update(update.get("$set", {}))
                out = dict(d)
                out.pop("_id", None)
                return out
        return None


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


class _FakeOrderRepo:
    def __init__(self, order):
        self._order = order

    def find_by_id(self, oid):
        return self._order if self._order.get("order_id") == oid else None

    def find_by_order_number(self, num):
        return self._order if self._order.get("order_number") == num else None


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
    stock_audit_coll = _FakeColl()
    # The original unit that was SOLD on ORD-1 for PRD-1 - eligible for reactivate.
    stock_repo = _FakeStockRepo(
        [
            {
                "stock_id": "STK-OLD-1",
                "product_id": "PRD-1",
                "store_id": "BV-PUN-01",
                "status": "SOLD",
                "order_id": "ORD-1",
            }
        ]
    )

    # Cache extra-collection lookups so a second call returns the SAME _FakeColl
    # instance - lets tests inspect stock_audit / any future collection.
    extra_colls: dict = {}

    class _FakeDB:
        is_connected = True

        def __init__(self):
            self.db = self

        def get_collection(self, name):
            mapping = {
                "returns": returns_coll,
                "credit_note_ledger": ledger_coll,
                "stock_audit": stock_audit_coll,
            }
            if name in mapping:
                return mapping[name]
            if name not in extra_colls:
                extra_colls[name] = _FakeColl()
            return extra_colls[name]

    fake_db = _FakeDB()

    monkeypatch.setattr(returns_router, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(
        returns_router, "get_customer_repository", lambda: customer_repo
    )
    monkeypatch.setattr(returns_router, "get_product_repository", lambda: None)
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr("api.dependencies.get_db", lambda: fake_db, raising=False)
    monkeypatch.setattr(
        "api.dependencies.get_audit_repository", lambda: None, raising=False
    )

    return {
        "client": TestClient(app),
        "stock_repo": stock_repo,
        "returns_coll": returns_coll,
        "stock_audit_coll": stock_audit_coll,
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
                "reason": "CHANGED_MIND",
                "condition": "GOOD",
            }
        ],
    }
    base.update(over)
    return base


# ============================================================================
# 2. INTEGRATION - behaviour
# ============================================================================


def test_return_reactivates_original_sold_unit(ctx):
    tok = _staff_token(["CASHIER"])
    r = ctx["client"].post(
        "/api/v1/returns", json=_payload(), headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 201
    data = r.json()
    assert data["restock_applied"] is True
    # The pre-existing SOLD unit flipped back to AVAILABLE (no mint).
    assert data["restock_stock_ids"] == ["STK-OLD-1"]
    old = [u for u in ctx["stock_repo"].units if u["stock_id"] == "STK-OLD-1"][0]
    assert old["status"] == "AVAILABLE"
    # No new unit minted.
    assert all(not u["stock_id"].startswith("NEW-") for u in ctx["stock_repo"].units)
    line = data["restocked"][0]
    assert line["reactivated"] == 1
    assert line["minted"] == 0


def test_return_mints_when_no_original_unit(ctx):
    tok = _staff_token(["CASHIER"])
    # Product with no prior stock row -> must mint a fresh AVAILABLE unit.
    payload = _payload()
    payload["items"][0]["product_id"] = "PRD-NOPRIOR"
    r = ctx["client"].post(
        "/api/v1/returns", json=payload, headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 201
    data = r.json()
    assert data["restock_applied"] is True
    assert len(data["restock_stock_ids"]) == 1
    minted = [
        u for u in ctx["stock_repo"].units if u["product_id"] == "PRD-NOPRIOR"
    ]
    assert len(minted) == 1
    assert minted[0]["status"] == "AVAILABLE"
    assert minted[0]["source_type"] == "RETURN"
    assert minted[0]["source_id"] == data["return_id"]
    assert data["restocked"][0]["minted"] == 1


def test_damaged_return_does_not_restock(ctx):
    tok = _staff_token(["ADMIN"])
    payload = _payload()
    payload["items"][0]["condition"] = "DAMAGED"
    r = ctx["client"].post(
        "/api/v1/returns", json=payload, headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 201
    data = r.json()
    # Nothing reactivated/minted; the SOLD unit stays SOLD.
    assert data["restock_stock_ids"] == []
    old = [u for u in ctx["stock_repo"].units if u["stock_id"] == "STK-OLD-1"][0]
    assert old["status"] == "SOLD"


def test_restock_idempotent_on_retry(ctx):
    tok = _staff_token(["STORE_MANAGER"])
    created = ctx["client"].post(
        "/api/v1/returns", json=_payload(), headers={"Authorization": f"Bearer {tok}"}
    ).json()
    rid = created["return_id"]
    units_before = len(ctx["stock_repo"].units)
    # Retry: must be a no-op (already applied) - no second mint/reactivate.
    r = ctx["client"].post(
        f"/api/v1/returns/{rid}/restock",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200
    assert r.json()["restock_applied"] is True
    assert r.json()["message"] == "Already restocked"
    assert len(ctx["stock_repo"].units) == units_before


def test_exchange_restocks_returned_line(ctx):
    tok = _staff_token(["SALES_CASHIER"])
    payload = _payload(
        return_type="EXCHANGE",
        customer_id="CUST-1",
        replacement_items=[
            {
                "product_id": "PRD-9",
                "name": "Oakley",
                "sku": "OK-9",
                "quantity": 1,
                "unit_price": 2500,
            }
        ],
    )
    r = ctx["client"].post(
        "/api/v1/returns", json=payload, headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 201
    data = r.json()
    # The RETURNED line (PRD-1) is restocked even though this is an EXCHANGE.
    assert data["restock_applied"] is True
    assert data["restock_stock_ids"] == ["STK-OLD-1"]


def test_restock_fail_soft_when_stock_repo_down(ctx, monkeypatch):
    # Stock layer unavailable -> the return still records, applied=False so it
    # can be retried; no 500.
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: None)
    tok = _staff_token(["CASHIER"])
    r = ctx["client"].post(
        "/api/v1/returns", json=_payload(), headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 201
    data = r.json()
    assert data["restock_applied"] is False
    assert data["restock_stock_ids"] == []
    # Intent still recorded for a later retry.
    assert data["restocked"][0]["product_id"] == "PRD-1"
    assert data["restocked"][0]["applied"] is False


def test_retry_applies_after_stock_recovers(ctx, monkeypatch):
    # 1) create with stock down -> applied False.
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: None)
    tok = _staff_token(["ADMIN"])
    created = ctx["client"].post(
        "/api/v1/returns", json=_payload(), headers={"Authorization": f"Bearer {tok}"}
    ).json()
    assert created["restock_applied"] is False
    rid = created["return_id"]
    # 2) stock recovers, retry -> now applied + original unit reactivated.
    monkeypatch.setattr(
        returns_router, "get_stock_repository", lambda: ctx["stock_repo"]
    )
    r = ctx["client"].post(
        f"/api/v1/returns/{rid}/restock",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["restock_applied"] is True
    assert body["restock_stock_ids"] == ["STK-OLD-1"]
    old = [u for u in ctx["stock_repo"].units if u["stock_id"] == "STK-OLD-1"][0]
    assert old["status"] == "AVAILABLE"


def test_multi_unit_return_reactivates_one_mints_rest(ctx):
    # Return 2 of PRD-1 but only ONE prior SOLD unit exists -> 1 reactivate + 1 mint.
    tok = _staff_token(["CASHIER"])
    payload = _payload()
    payload["items"][0]["return_qty"] = 2
    r = ctx["client"].post(
        "/api/v1/returns", json=payload, headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 201
    data = r.json()
    assert data["restock_applied"] is True
    assert len(data["restock_stock_ids"]) == 2
    line = data["restocked"][0]
    assert line["reactivated"] == 1
    assert line["minted"] == 1
    assert line["quantity"] == 2


# ============================================================================
# B3 - DAMAGED/RETURNED stock units must NOT be reactivated
# ============================================================================


def test_damaged_unit_not_reactivated_mints_fresh_instead(monkeypatch):
    """B3 guard: a stock_unit with status DAMAGED is NEVER flipped to AVAILABLE.

    Before B3 the reactivation query was `$ne: AVAILABLE` so DAMAGED, SCRAPPED,
    TRANSFERRED, RETURNED units were all eligible candidates. That's wrong: a
    return is the undo of a SALE, and only SOLD units are reversible. With B3
    in place the reactivation skips the DAMAGED row entirely and the restock
    flow mints a fresh AVAILABLE unit, leaving the damaged one DAMAGED.
    """
    app = FastAPI()
    app.include_router(returns_router.router, prefix="/api/v1/returns")

    order = {
        "order_id": "ORD-9",
        "order_number": "INV-9009",
        "customer_id": "CUST-1",
        "customer_name": "Asha",
        "payment_method": "UPI",
        "store_id": "BV-PUN-01",
    }
    order_repo = _FakeOrderRepo(order)
    customer_repo = _FakeCustomerRepo()
    returns_coll = _FakeColl()
    ledger_coll = _FakeColl()
    stock_audit_coll = _FakeColl()
    # The ONLY prior unit for PRD-9 is DAMAGED - it must NOT be reactivated.
    stock_repo = _FakeStockRepo(
        [
            {
                "stock_id": "STK-DAMAGED",
                "product_id": "PRD-9",
                "store_id": "BV-PUN-01",
                "status": "DAMAGED",
                "order_id": "ORD-9",
            }
        ]
    )

    extra_colls: dict = {}

    class _FakeDB:
        is_connected = True

        def __init__(self):
            self.db = self

        def get_collection(self, name):
            mapping = {
                "returns": returns_coll,
                "credit_note_ledger": ledger_coll,
                "stock_audit": stock_audit_coll,
            }
            if name in mapping:
                return mapping[name]
            if name not in extra_colls:
                extra_colls[name] = _FakeColl()
            return extra_colls[name]

    fake_db = _FakeDB()
    monkeypatch.setattr(returns_router, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(
        returns_router, "get_customer_repository", lambda: customer_repo
    )
    monkeypatch.setattr(returns_router, "get_product_repository", lambda: None)
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr("api.dependencies.get_db", lambda: fake_db, raising=False)
    monkeypatch.setattr(
        "api.dependencies.get_audit_repository", lambda: None, raising=False
    )

    client = TestClient(app)
    tok = _staff_token(["CASHIER"])
    payload = {
        "order_id": "ORD-9",
        "store_id": "BV-PUN-01",
        "return_type": "RETURN",
        "items": [
            {
                "order_item_id": "li9",
                "product_id": "PRD-9",
                "product_name": "Some Frame",
                "sku": "F-9",
                "return_qty": 1,
                "unit_price": 1500,
                "reason": "CHANGED_MIND",
                "condition": "GOOD",
            }
        ],
    }
    r = client.post(
        "/api/v1/returns", json=payload, headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 201
    data = r.json()
    # The DAMAGED unit MUST still be DAMAGED - never resurrected.
    damaged = [u for u in stock_repo.units if u["stock_id"] == "STK-DAMAGED"][0]
    assert damaged["status"] == "DAMAGED"
    # The restock flow falls back to minting a fresh AVAILABLE unit.
    minted = [u for u in stock_repo.units if u.get("stock_id", "").startswith("NEW-")]
    assert len(minted) == 1
    assert minted[0]["status"] == "AVAILABLE"
    assert data["restocked"][0]["reactivated"] == 0
    assert data["restocked"][0]["minted"] == 1


def test_returned_unit_not_reactivated_either(monkeypatch):
    """B3 guard: status RETURNED (a unit returned earlier and not yet
    re-shelved) is also NOT eligible for reactivation. Only SOLD is."""
    app = FastAPI()
    app.include_router(returns_router.router, prefix="/api/v1/returns")
    order = {
        "order_id": "ORD-10",
        "order_number": "INV-9010",
        "customer_id": "CUST-1",
        "customer_name": "Asha",
        "payment_method": "UPI",
        "store_id": "BV-PUN-01",
    }
    order_repo = _FakeOrderRepo(order)
    customer_repo = _FakeCustomerRepo()
    returns_coll = _FakeColl()
    ledger_coll = _FakeColl()
    stock_audit_coll = _FakeColl()
    stock_repo = _FakeStockRepo(
        [
            {
                "stock_id": "STK-PREVIOUSLY-RETURNED",
                "product_id": "PRD-10",
                "store_id": "BV-PUN-01",
                "status": "RETURNED",
                "order_id": "ORD-10",
            }
        ]
    )
    extra_colls: dict = {}

    class _FakeDB:
        is_connected = True

        def __init__(self):
            self.db = self

        def get_collection(self, name):
            mapping = {
                "returns": returns_coll,
                "credit_note_ledger": ledger_coll,
                "stock_audit": stock_audit_coll,
            }
            if name in mapping:
                return mapping[name]
            if name not in extra_colls:
                extra_colls[name] = _FakeColl()
            return extra_colls[name]

    fake_db = _FakeDB()
    monkeypatch.setattr(returns_router, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(
        returns_router, "get_customer_repository", lambda: customer_repo
    )
    monkeypatch.setattr(returns_router, "get_product_repository", lambda: None)
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr("api.dependencies.get_db", lambda: fake_db, raising=False)
    monkeypatch.setattr(
        "api.dependencies.get_audit_repository", lambda: None, raising=False
    )

    client = TestClient(app)
    tok = _staff_token(["CASHIER"])
    payload = {
        "order_id": "ORD-10",
        "store_id": "BV-PUN-01",
        "return_type": "RETURN",
        "items": [
            {
                "order_item_id": "li10",
                "product_id": "PRD-10",
                "product_name": "Item",
                "sku": "X-10",
                "return_qty": 1,
                "unit_price": 500,
                "reason": "CHANGED_MIND",
                "condition": "GOOD",
            }
        ],
    }
    r = client.post(
        "/api/v1/returns", json=payload, headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 201
    prev = [u for u in stock_repo.units if u["stock_id"] == "STK-PREVIOUSLY-RETURNED"][0]
    assert prev["status"] == "RETURNED"  # untouched
    minted = [u for u in stock_repo.units if u.get("stock_id", "").startswith("NEW-")]
    assert len(minted) == 1


# ============================================================================
# B4 - Atomic claim+commit idempotency on retry
# ============================================================================


def test_retry_when_already_in_progress_returns_busy(ctx, monkeypatch):
    """Two retries race: the FIRST claims the doc and starts; the SECOND must
    see restock_in_progress=True and bail out without re-doing the stock writes."""
    # 1) Save a return with applied=False so it's eligible for retry.
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: None)
    tok = _staff_token(["ADMIN"])
    created = ctx["client"].post(
        "/api/v1/returns",
        json=_payload(),
        headers={"Authorization": f"Bearer {tok}"},
    ).json()
    assert created["restock_applied"] is False
    rid = created["return_id"]

    # 2) Simulate worker A having taken the claim - flip the in-progress flag.
    ctx["returns_coll"].update_one(
        {"return_id": rid},
        {"$set": {"restock_in_progress": True}},
    )

    # 3) Worker B's retry must see "in progress" and not touch stock.
    monkeypatch.setattr(
        returns_router, "get_stock_repository", lambda: ctx["stock_repo"]
    )
    units_before = len(ctx["stock_repo"].units)
    statuses_before = [u.get("status") for u in ctx["stock_repo"].units]

    r = ctx["client"].post(
        f"/api/v1/returns/{rid}/restock",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["restock_applied"] is False
    assert "in progress" in body["message"].lower()
    # No new units, no status flips.
    assert len(ctx["stock_repo"].units) == units_before
    assert [u.get("status") for u in ctx["stock_repo"].units] == statuses_before


def test_retry_merges_existing_stock_ids_no_duplicate(ctx, monkeypatch):
    """A partial earlier run left some stock_ids on the doc. A successful retry
    must MERGE its new units with the existing list (dedup, preserve order) so
    the doc isn't lying about how many units are back on the shelf."""
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: None)
    tok = _staff_token(["ADMIN"])
    created = ctx["client"].post(
        "/api/v1/returns",
        json=_payload(),
        headers={"Authorization": f"Bearer {tok}"},
    ).json()
    rid = created["return_id"]

    # Simulate that a previous partial run already recorded ONE stock id.
    ctx["returns_coll"].update_one(
        {"return_id": rid},
        {"$set": {"restock_stock_ids": ["PARTIAL-OLD-1"]}},
    )

    monkeypatch.setattr(
        returns_router, "get_stock_repository", lambda: ctx["stock_repo"]
    )
    r = ctx["client"].post(
        f"/api/v1/returns/{rid}/restock",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["restock_applied"] is True
    # PARTIAL-OLD-1 is preserved + new reactivation appended; no dup.
    assert "PARTIAL-OLD-1" in body["restock_stock_ids"]
    assert "STK-OLD-1" in body["restock_stock_ids"]
    assert len(body["restock_stock_ids"]) == len(set(body["restock_stock_ids"]))


# ============================================================================
# B5 - Per-stock-id audit rows on every status transition
# ============================================================================


def test_audit_row_written_on_reactivation(ctx):
    """The reactivate path emits an audit row recording SOLD->AVAILABLE."""
    tok = _staff_token(["CASHIER"])
    r = ctx["client"].post(
        "/api/v1/returns", json=_payload(), headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 201
    docs = ctx["stock_audit_coll"].docs
    assert any(
        d.get("stock_id") == "STK-OLD-1"
        and d.get("prior_status") == "SOLD"
        and d.get("new_status") == "AVAILABLE"
        and d.get("source") == "RETURN_RESTOCK"
        for d in docs
    )


def test_audit_row_written_on_mint(ctx):
    """The mint path emits an audit row recording None -> AVAILABLE (new unit)."""
    tok = _staff_token(["CASHIER"])
    payload = _payload()
    payload["items"][0]["product_id"] = "PRD-FRESH-MINT"
    r = ctx["client"].post(
        "/api/v1/returns", json=payload, headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 201
    docs = ctx["stock_audit_coll"].docs
    # New unit was minted; audit row should record it with prior_status=None.
    mint_rows = [
        d
        for d in docs
        if d.get("prior_status") is None
        and d.get("new_status") == "AVAILABLE"
        and d.get("source") == "RETURN_RESTOCK"
    ]
    assert len(mint_rows) == 1
    # The stock_id on the row should match the newly-minted unit.
    minted = [
        u for u in ctx["stock_repo"].units if u["product_id"] == "PRD-FRESH-MINT"
    ][0]
    assert mint_rows[0]["stock_id"] == minted["stock_id"]
