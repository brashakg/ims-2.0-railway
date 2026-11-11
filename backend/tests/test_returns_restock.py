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

# The doors under test number their GST documents (a return's credit note,
# a debit note): these fakes hold no store records, so a stub mint numbers
# them (conftest.gst_numbers). The mint is tested in test_gst_document_numbers.
pytestmark = pytest.mark.usefixtures("gst_numbers")


# ============================================================================
# 1. PURE ENGINE - should_restock
# ============================================================================



class _FakeProductRepoForExchange:
    """Catalog stand-in for EXCHANGE replacement pricing.

    The server resolves a replacement line's price from the product master
    (never the client payload) because the settlement difference drives a real
    cash collection. Keyed by SKU so each test can price its own replacement.
    """

    _CATALOG = {
        "OK-9": 2500.0,
        "LF-9": 900.0,
    }

    def find_by_id(self, pid):
        return None

    def find_by_sku(self, sku):
        price = self._CATALOG.get(str(sku))
        if price is None:
            return None
        return {"product_id": "PRD-9", "sku": sku, "offer_price": price}


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

    def find(self, query=None, projection=None):
        # The counter door's earlier-returns scan: a collection it cannot
        # read is no answer (503), so the fake answers like Mongo.
        return [dict(d) for d in self.docs if _doc_matches(d, query)]

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


# Every order_item_id / product_id that the HTTP tests in this file return
# against. The returns router now REQUIRES the return line to resolve to a real
# original order line (over-refund guard), so each fake order is auto-seeded
# with a generous line per identity (qty 100, returned_qty 0) unless the test
# supplied its own items. The piece that goes back is the BILL LINE's product
# (L2-RETURN-F01), never one the client names beside another line's id, so
# each product a test returns has its own line (_mint_line picks it).
_DEFAULT_ORDER_ITEMS = [
    {"item_id": "li1", "product_id": "PRD-1", "quantity": 100, "returned_qty": 0},
    {"item_id": "li9", "product_id": "PRD-9", "quantity": 100, "returned_qty": 0},
    {"item_id": "li10", "product_id": "PRD-10", "quantity": 100, "returned_qty": 0},
    {"item_id": "li-PRD-NOPRIOR", "product_id": "PRD-NOPRIOR", "quantity": 100, "returned_qty": 0},
    {"item_id": "li-PRD-FRESH-MINT", "product_id": "PRD-FRESH-MINT", "quantity": 100, "returned_qty": 0},
    {"item_id": "li-PRD-MINT-LINEAGE", "product_id": "PRD-MINT-LINEAGE", "quantity": 100, "returned_qty": 0},
]


def _mint_line(payload, product_id):
    """Point the payload's line at the bill line of a product that has no
    stock row yet (so its return mints)."""
    payload["items"][0].update(order_item_id=f"li-{product_id}", product_id=product_id)


class _FakeOrdersColl:
    """Fake `orders` collection that models just enough of Mongo's array-update
    semantics for the returns atomic returnable-qty claim: a filter using
    items.$elemMatch (identity + a returned_qty cap) plus a positional
    items.$.returned_qty $inc on the matched element."""

    def __init__(self, orders):
        self.docs = [dict(o) for o in orders]

    @staticmethod
    def _elem_matches(elem, cond):
        for key, c in cond.items():
            if key == "$or":
                if not any(_FakeOrdersColl._elem_matches(elem, sub) for sub in c):
                    return False
                continue
            val = elem.get(key)
            if isinstance(c, dict):
                for op, operand in c.items():
                    if op == "$lte" and not (val is not None and val <= operand):
                        return False
                    if op == "$lt" and not (val is not None and val < operand):
                        return False
                    if op == "$gte" and not (val is not None and val >= operand):
                        return False
                    if op == "$exists":
                        present = key in elem
                        if bool(operand) != present:
                            return False
            elif val != c:
                return False
        return True

    def find_one(self, query=None, projection=None):
        for d in self.docs:
            if d.get("order_id") == (query or {}).get("order_id"):
                out = dict(d)
                out.pop("_id", None)
                return out
        return None

    def find_one_and_update(self, query, update, return_document=None):
        order_id = (query or {}).get("order_id")
        elem_cond = ((query or {}).get("items") or {}).get("$elemMatch") or {}
        inc = (update or {}).get("$inc", {}) or {}
        for d in self.docs:
            if d.get("order_id") != order_id:
                continue
            for line in d.get("items") or []:
                if self._elem_matches(line, elem_cond):
                    for field, delta in inc.items():
                        # positional path "items.$.returned_qty"
                        leaf = field.split(".")[-1]
                        line[leaf] = (line.get(leaf) or 0) + delta
                    out = dict(d)
                    out.pop("_id", None)
                    return out
            return None  # order found but no element satisfied the guard
        return None


class _FakeOrderRepo:
    def __init__(self, order):
        order = dict(order)
        if "items" not in order:
            order["items"] = [dict(li) for li in _DEFAULT_ORDER_ITEMS]
        # BUG-096: a return now requires a returnable order status + a cumulative
        # refund <= amount_paid. These restock fixtures model a real PAID,
        # delivered sale, so default both unless an individual test overrides them.
        order.setdefault("status", "DELIVERED")
        order.setdefault("amount_paid", 1_000_000.0)
        self._order = order
        # Expose a bound `orders` collection so the returns atomic claim runs
        # against the SAME order doc the repo serves.
        self.collection = _FakeOrdersColl([order])

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
    monkeypatch.setattr(
        returns_router, "get_product_repository",
        lambda: _FakeProductRepoForExchange(),
    )
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
        "ledger_coll": ledger_coll,
        "customer_repo": customer_repo,
        "order_repo": order_repo,
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
    _mint_line(payload, "PRD-NOPRIOR")
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
    # Owner ruling 2026-09-28: every door that creates a unit mints its IMS
    # barcode through the one minter -- letters and digits only, so the till
    # accepts it typed (/^[A-Z0-9]{8,}$/). A barcode-less unit cannot be scanned.
    import re

    assert re.fullmatch(r"[A-Z0-9]{8,}", minted[0].get("barcode") or ""), minted[0]
    # ...and its label is still to print (the units view offers it).
    assert minted[0]["barcode_printed"] is False


def test_return_mint_goes_through_the_one_minter(ctx, monkeypatch):
    """The shape check above passes for ANY letters-and-digits code, including a
    door-local uuid that would break chain-wide uniqueness. So swap the one
    minter for a spy: the minted unit must carry the spy's code, and the door
    must hand the minter its real database (the counter path, never the
    no-database random fallback) and the shop the unit goes back to."""
    from api.services import barcode as barcode_svc

    calls = []

    def spy(mint_db, store_id):
        calls.append((mint_db, store_id))
        return "SPY0000001"

    monkeypatch.setattr(barcode_svc, "mint_unit_barcode", spy)
    payload = _payload()
    _mint_line(payload, "PRD-NOPRIOR")
    r = ctx["client"].post(
        "/api/v1/returns",
        json=payload,
        headers={"Authorization": f"Bearer {_staff_token(['CASHIER'])}"},
    )
    assert r.status_code == 201
    minted = [u for u in ctx["stock_repo"].units if u["product_id"] == "PRD-NOPRIOR"]
    assert [u.get("barcode") for u in minted] == ["SPY0000001"]
    db = returns_router._get_db()
    assert db is not None and calls == [(db, "BV-PUN-01")], calls


def test_damaged_return_does_not_restock_and_sets_the_piece_aside(ctx, monkeypatch):
    """F21-3: a DAMAGED piece is never put back on sale -- and never left SOLD
    either: it comes back QUARANTINED (tracked, can go to the supplier) and
    the task agent is told so it raises the 7-day follow-up."""
    import agents.registry as registry

    told = []

    async def _capture(event, payload, source=""):
        told.append((event, payload))

    monkeypatch.setattr(registry, "dispatch_event", _capture)
    from api.services import item_events as ie

    ledger = []
    monkeypatch.setattr(ie, "record_post_write_event", lambda db, **kw: ledger.append(kw))
    tok = _staff_token(["ADMIN"])
    payload = _payload()
    payload["items"][0]["condition"] = "DAMAGED"
    r = ctx["client"].post(
        "/api/v1/returns", json=payload, headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 201
    data = r.json()
    # Nothing reactivated onto the shelf / minted.
    assert data["restock_stock_ids"] == []
    old = [u for u in ctx["stock_repo"].units if u["stock_id"] == "STK-OLD-1"][0]
    assert old["status"] == "QUARANTINED"
    assert old["quarantine_reason"] == "CUSTOMER_RETURN_DAMAGED"
    assert old["order_id"] is None and old["returned_from_order_id"] == "ORD-1"
    saved = ctx["returns_coll"].docs[-1]
    assert saved["quarantined_stock_ids"] == ["STK-OLD-1"]
    assert told == [("stock.quarantined", {"stock_id": "STK-OLD-1", "store_id": "BV-PUN-01",
                                           "reason": "CUSTOMER_RETURN_DAMAGED", "actor_id": "u1"})]
    # The piece's history says it came back damaged (SOLD -> QUARANTINED).
    assert [(e["stock_id"], e["from_state"], e["to_state"], e["source_type"]) for e in ledger] == [
        ("STK-OLD-1", ie.StockState.SOLD, ie.StockState.QUARANTINED, "RETURN")]


def test_a_damaged_piece_with_no_shop_on_record_is_chased_at_the_returns_shop(ctx, monkeypatch):
    import agents.registry as registry

    told = []

    async def _capture(event, payload, source=""):
        told.append(payload)

    monkeypatch.setattr(registry, "dispatch_event", _capture)
    ctx["stock_repo"].units[0].pop("store_id")
    payload = _payload()
    payload["items"][0]["condition"] = "DAMAGED"
    r = ctx["client"].post("/api/v1/returns", json=payload,
                           headers={"Authorization": f"Bearer {_staff_token(['ADMIN'])}"})
    assert r.status_code == 201
    assert [t["store_id"] for t in told] == ["BV-PUN-01"]


def test_a_damaged_return_never_takes_another_orders_piece(ctx):
    """Only the unit THIS order sold is set aside: a SOLD piece of the same
    frame on another customer's order stays exactly where it is."""
    ctx["stock_repo"].units[0]["order_id"] = "ORD-OTHER"
    tok = _staff_token(["ADMIN"])
    payload = _payload()
    payload["items"][0]["condition"] = "DAMAGED"
    r = ctx["client"].post(
        "/api/v1/returns", json=payload, headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 201
    assert ctx["stock_repo"].units[0]["status"] == "SOLD"
    assert ctx["returns_coll"].docs[-1]["quarantined_stock_ids"] == []


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
    # Two units are above Rs 2,000: a store manager records it (owner ruling
    # 2026-10-23 -- a cashier would need a manager's approval first).
    tok = _staff_token(["STORE_MANAGER"])
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
    monkeypatch.setattr(
        returns_router, "get_product_repository",
        lambda: _FakeProductRepoForExchange(),
    )
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
    monkeypatch.setattr(
        returns_router, "get_product_repository",
        lambda: _FakeProductRepoForExchange(),
    )
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
    _mint_line(payload, "PRD-FRESH-MINT")
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


# ============================================================================
# P1-C - reactivated / minted units carry CLEAN resale lineage
# ============================================================================
#
# A returned unit put back on the AVAILABLE shelf for re-sale must NOT keep the
# stale attribution of the sale it was returned FROM. Before the fix the
# reactivate path flipped SOLD -> AVAILABLE but left the ORIGINAL order_id (and
# sold_at / sold_to_customer_id) stamped on the now-sellable unit. That unit
# then "answered to two orders": status-agnostic lineage lookups (warranty by
# serial, per-order unit attribution) saw an AVAILABLE-on-the-shelf unit still
# claiming it was sold on the refunded order. The fix clears the stale sale
# attribution and records a returned_from_order_id audit link, so a later sale
# (claim_one_available) stamps the NEW order_id onto a clean unit.


def test_reactivated_unit_clears_stale_sale_attribution(ctx):
    """A SOLD->AVAILABLE reactivation must STRIP the original order_id / sold_at
    / sold_to_customer_id (the unit is back on the shelf, not sold) and stamp a
    returned_from_order_id audit link to the order it was returned from."""
    # Seed the original SOLD unit with full sale attribution.
    old = [u for u in ctx["stock_repo"].units if u["stock_id"] == "STK-OLD-1"][0]
    old["sold_at"] = "2026-01-01T00:00:00"
    old["sold_to_customer_id"] = "CUST-1"

    tok = _staff_token(["CASHIER"])
    r = ctx["client"].post(
        "/api/v1/returns", json=_payload(), headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 201
    assert r.json()["restock_stock_ids"] == ["STK-OLD-1"]

    unit = [u for u in ctx["stock_repo"].units if u["stock_id"] == "STK-OLD-1"][0]
    assert unit["status"] == "AVAILABLE"
    # The stale sale attribution must NOT linger on a sellable unit.
    assert not unit.get("order_id"), (
        "reactivated AVAILABLE unit still carries the refunded order_id"
    )
    assert not unit.get("sold_at")
    assert not unit.get("sold_to_customer_id")
    # An audit link back to the order it came from is preserved.
    assert unit.get("returned_from_order_id") == "ORD-1"


def test_resold_returned_unit_attributes_to_one_order_only(ctx):
    """Full lineage path: sell S1 on ORD-1 -> return -> resell on ORD-2. After
    the resale, 'units SOLD for ORD-1' must NOT include S1 and 'for ORD-2'
    must, with no ambiguity (the unit answers to exactly ONE current order)."""
    from database.repositories.product_repository import StockRepository

    old = [u for u in ctx["stock_repo"].units if u["stock_id"] == "STK-OLD-1"][0]
    old["sold_at"] = "2026-01-01T00:00:00"
    old["sold_to_customer_id"] = "CUST-1"

    tok = _staff_token(["CASHIER"])
    r = ctx["client"].post(
        "/api/v1/returns", json=_payload(), headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 201

    # Resell the same physical unit on ORD-2 via the real at-sale atomic claim.
    # NOTE: claim_one_available now carries the F2 expiry floor, so the fake
    # matcher must understand $gte (in-date phase) and the $or fallback branch
    # (undated / legacy). These units carry no expiry_date, so they match the
    # undated branch exactly as before.
    class _Coll:
        def __init__(self, units):
            self.units = units

        @staticmethod
        def _matches(d, flt):
            for k, v in (flt or {}).items():
                if k == "$or":
                    if not any(_Coll._matches(d, c) for c in v):
                        return False
                elif isinstance(v, dict):
                    val = d.get(k)
                    for op, arg in v.items():
                        if op == "$nin" and val in arg:
                            return False
                        if op == "$in" and val not in arg:
                            return False
                        if op == "$gte" and not (
                            isinstance(val, str)
                            and isinstance(arg, str)
                            and val >= arg
                        ):
                            return False
                        if op == "$not" and not isinstance(val, str):
                            # {"$not": {"$type": "string"}} -- true for non-strings
                            continue
                        if op == "$not":
                            return False
                elif d.get(k) != v:
                    return False
            return True

        def find_one_and_update(self, flt, upd, **kw):
            for d in self.units:
                if self._matches(d, flt):
                    d.update(upd.get("$set", {}))
                    return dict(d)
            return None

    repo = StockRepository(_Coll(ctx["stock_repo"].units))
    resold = repo.claim_one_available("PRD-1", "BV-PUN-01", "ORD-2")
    assert resold == "STK-OLD-1"

    units = ctx["stock_repo"].units
    sold_ord1 = [
        u for u in units if u.get("status") == "SOLD" and u.get("order_id") == "ORD-1"
    ]
    sold_ord2 = [
        u for u in units if u.get("status") == "SOLD" and u.get("order_id") == "ORD-2"
    ]
    assert sold_ord1 == [], "S1 must no longer attribute to the refunded ORD-1"
    assert [u["stock_id"] for u in sold_ord2] == ["STK-OLD-1"]


def test_minted_return_unit_carries_returned_from_link(ctx):
    """A freshly MINTED return unit (no prior SOLD row to reactivate) has no
    forward link to the sale it reverses unless we stamp one. It must carry
    returned_from_order_id so its origin is auditable, and NO sold attribution."""
    tok = _staff_token(["CASHIER"])
    payload = _payload()
    _mint_line(payload, "PRD-MINT-LINEAGE")
    r = ctx["client"].post(
        "/api/v1/returns", json=payload, headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 201
    minted = [
        u for u in ctx["stock_repo"].units if u["product_id"] == "PRD-MINT-LINEAGE"
    ][0]
    assert minted["status"] == "AVAILABLE"
    assert not minted.get("order_id")
    assert not minted.get("sold_at")
    assert minted.get("returned_from_order_id") == "ORD-1"


# ============================================================================
# P1-B - concurrent retry_restock must NOT mint/reactivate duplicate units
# ============================================================================
#
# Audit claim: two concurrent retry_restock calls on the same return both read
# restock_applied=False and both run reactivate/mint -> on-hand +2 where it
# should be +1. VERIFIED FALSE POSITIVE: retry_restock already claims the return
# atomically via find_one_and_update({restock_applied:{$ne:True},
# restock_in_progress:{$ne:True}} -> $set restock_in_progress) so exactly ONE
# worker proceeds; the other gets None and bails. The test below drives TRUE
# concurrency (two threads, a serialized claim + a widened race window) and
# asserts on-hand goes +1, not +2. It fails loudly if the atomic claim is ever
# weakened back to a read-check-write.


def test_concurrent_retry_restock_mints_exactly_one_unit(monkeypatch):
    """Two retry_restock calls race on the same return -> exactly ONE restock
    occurs (on-hand +1), the other is a no-op. DETERMINISTIC: the second call
    is fired from INSIDE the first call's claim (right after its
    find_one_and_update took the return) and runs to completion before the
    first continues -- the exact interleaving the audit worried about, with
    no thread timing or sleeps."""
    import threading

    app = FastAPI()
    app.include_router(returns_router.router, prefix="/api/v1/returns")

    class _ConcReturnsColl:
        """Returns coll whose first successful claim runs the SECOND request to
        completion before handing the claim back to the first."""

        def __init__(self):
            self.docs = []
            self.on_first_claim = None

        @staticmethod
        def _match(d, q):
            for k, v in (q or {}).items():
                if isinstance(v, dict) and "$ne" in v:
                    if d.get(k) == v["$ne"]:
                        return False
                elif d.get(k) != v:
                    return False
            return True

        def insert_one(self, doc):
            self.docs.append(dict(doc))
            return _FakeResult(1)

        def find_one(self, query=None, projection=None):
            for d in self.docs:
                if self._match(d, query):
                    out = dict(d)
                    out.pop("_id", None)
                    return out
            return None

        def update_one(self, query, update):
            for d in self.docs:
                if self._match(d, query):
                    d.update(update.get("$set", {}))
                    return _FakeResult(1)
            return _FakeResult(0)

        def find_one_and_update(self, query, update, return_document=None):
            for d in self.docs:
                if self._match(d, query):
                    d.update(update.get("$set", {}))
                    out = dict(d)
                    out.pop("_id", None)
                    hook, self.on_first_claim = self.on_first_claim, None
                    if hook:
                        hook()  # the second request runs NOW, the claim held
                    return out
            return None

    returns_coll = _ConcReturnsColl()
    # One SOLD unit eligible for reactivation.
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
    # Pre-seed a return doc that is eligible for retry (applied=False).
    returns_coll.insert_one(
        {
            "return_id": "RET-CONC",
            "order_id": "ORD-1",
            "store_id": "BV-PUN-01",
            "restock_applied": False,
            "items": [
                {
                    "order_item_id": "li1",
                    "product_id": "PRD-1",
                    "product_name": "X",
                    "sku": "X",
                    "return_qty": 1,
                    "unit_price": 1500,
                    "reason": "CHANGED_MIND",
                    "condition": "GOOD",
                }
            ],
            "restock_stock_ids": [],
        }
    )

    class _FakeDB:
        is_connected = True

        def __init__(self):
            self.db = self
            self.orders = _FakeColl()
            self.orders.insert_one({
                "order_id": "ORD-1", "store_id": "BV-PUN-01",
                "historical": False, "status": "DELIVERED",
                "items": [{"item_id": "li1", "product_id": "PRD-1", "quantity": 1}],
            })

        def get_collection(self, name):
            return {"returns": returns_coll, "orders": self.orders}.get(name, _FakeColl())

    fake_db = _FakeDB()
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr(returns_router, "_returns_coll", lambda: returns_coll)
    monkeypatch.setattr("api.dependencies.get_db", lambda: fake_db, raising=False)
    monkeypatch.setattr(
        "api.dependencies.get_audit_repository", lambda: None, raising=False
    )

    client = TestClient(app)
    tok = _staff_token(["ADMIN"])
    hdr = {"Authorization": f"Bearer {tok}"}

    results: list = []

    def _call():
        results.append(
            client.post("/api/v1/returns/RET-CONC/restock", headers=hdr).json()
        )

    def _second_request_inside_the_claim():
        # A separate thread only because the first request is still inside
        # its handler; join() makes the ordering fixed, not timed.
        t = threading.Thread(target=_call)
        t.start()
        t.join()

    returns_coll.on_first_claim = _second_request_inside_the_claim
    _call()
    assert len(results) == 2

    # Exactly one winner ("Restock applied"); the other bails ("in progress").
    msgs = sorted((x.get("message") or "").lower() for x in results)
    assert any("applied" in m for m in msgs)
    assert any("in progress" in m for m in msgs)
    # On-hand went +1, not +2: exactly one unit is AVAILABLE.
    available = [u for u in stock_repo.units if u.get("status") == "AVAILABLE"]
    assert len(available) == 1
    assert available[0]["stock_id"] == "STK-OLD-1"
    # No fresh unit was minted (the single SOLD unit was reactivated once).
    assert all(not u["stock_id"].startswith("NEW-") for u in stock_repo.units)


@pytest.mark.parametrize("walk_in", [True, False])
def test_a_counter_retry_restocks_only_the_units_still_out(ctx, monkeypatch, walk_in):
    """Panel finding (9da6612): a counter return of 2 on ORD-1 (SOLD STK-OLD-1
    and STK-B); STK-OLD-1's write fails (the real update answers False), so
    the return ends with STK-B back and the restock open. The retry re-ran
    BOTH units: one reactivated STK-OLD-1, the other found no SOLD unit on
    ORD-1 and the counter's order-agnostic fallback flipped the walk-in's
    sold STK-W to AVAILABLE (or, with no such unit, minted a phantom) -- 3
    units back for 2 returned. The retry puts back only what is still out."""
    units = ctx["stock_repo"].units
    units.append({**units[0], "stock_id": "STK-B"})
    if walk_in:
        units.append({**units[0], "stock_id": "STK-W", "order_id": "ORD-WALKIN"})
    real_update = ctx["stock_repo"].update
    down = {"STK-OLD-1"}
    monkeypatch.setattr(ctx["stock_repo"], "update",
                        lambda sid, data: False if sid in down else real_update(sid, data))
    hdr = {"Authorization": f"Bearer {_staff_token(['ADMIN'])}"}
    payload = _payload()
    payload["items"][0]["return_qty"] = 2
    created = ctx["client"].post("/api/v1/returns", json=payload, headers=hdr).json()
    assert created["restock_applied"] is False and created["restock_stock_ids"] == ["STK-B"]

    down.clear()
    body = ctx["client"].post(f"/api/v1/returns/{created['return_id']}/restock", headers=hdr).json()
    assert body["restock_applied"] is True
    assert body["restock_stock_ids"] == ["STK-B", "STK-OLD-1"]
    want = {"STK-OLD-1": "AVAILABLE", "STK-B": "AVAILABLE"}
    if walk_in:
        want["STK-W"] = "SOLD"
    assert {u["stock_id"]: u["status"] for u in units} == want, "2 back for 2, no hijack, no mint"
    assert all(u.get("order_id") == "ORD-WALKIN" for u in units if u["stock_id"] == "STK-W")


# ============================================================================
# L2-RETURN-F02 - replay and restock recovery are authorised against the
# persisted return's shop; a replay is bound to the physical disposition too.
# ============================================================================


def _hdr(roles, store_id="BV-PUN-01", key=None):
    h = {"Authorization": f"Bearer {_staff_token(roles, store_id=store_id)}"}
    if key:
        h["Idempotency-Key"] = key
    return h


def test_known_key_from_another_shop_reveals_no_return(ctx):
    """A BV-RNC-01 cashier replaying BV-PUN-01's known key gets the same 404 as
    the detail route -- no return id, no money, no stock ids -- and nothing is
    written."""
    created = ctx["client"].post("/api/v1/returns", json=_payload(),
                                 headers=_hdr(["ADMIN"], key="K-F02-A"))
    assert created.status_code == 201
    rid = created.json()["return_id"]
    r = ctx["client"].post("/api/v1/returns", json=_payload(),
                           headers=_hdr(["CASHIER"], store_id="BV-RNC-01", key="K-F02-A"))
    assert r.status_code == 404
    assert rid not in r.text and "net_refund" not in r.text
    assert len(ctx["returns_coll"].docs) == 1


def test_known_key_with_changed_body_from_another_shop_does_not_name_the_return(ctx):
    """Even the conflict answer used to name the other shop's return id."""
    rid = ctx["client"].post("/api/v1/returns", json=_payload(),
                             headers=_hdr(["ADMIN"], key="K-F02-B")).json()["return_id"]
    body = _payload()
    body["items"][0]["condition"] = "DAMAGED"
    r = ctx["client"].post("/api/v1/returns", json=body,
                           headers=_hdr(["CASHIER"], store_id="BV-RNC-01", key="K-F02-B"))
    assert r.status_code == 404 and rid not in r.text


def test_same_shop_identical_retry_replays_exactly_once(ctx):
    hdr = _hdr(["CASHIER"], key="K-F02-C")
    first = ctx["client"].post("/api/v1/returns", json=_payload(), headers=hdr)
    again = ctx["client"].post("/api/v1/returns", json=_payload(), headers=hdr)
    assert first.status_code == 201 and again.status_code == 201
    assert again.json()["_idempotent_replay"] is True
    assert again.json()["return_id"] == first.json()["return_id"]
    assert again.json()["net_refund"] == first.json()["net_refund"]
    assert len(ctx["returns_coll"].docs) == 1
    assert [u["status"] for u in ctx["stock_repo"].units] == ["AVAILABLE"]


@pytest.mark.parametrize("field,value", [
    ("condition", "DAMAGED"),
    ("restock", False),
    ("serial", "SER-OTHER"),
])
def test_changed_disposition_under_the_same_key_conflicts(ctx, field, value):
    """GOOD->DAMAGED, restock on->off or another scanned serial changes what
    happens to the piece; the same key must refuse it, not replay success."""
    hdr = _hdr(["CASHIER"], key=f"K-F02-D-{field}")
    assert ctx["client"].post("/api/v1/returns", json=_payload(), headers=hdr).status_code == 201
    snapshot = [dict(u) for u in ctx["stock_repo"].units]
    body = _payload()
    body["items"][0][field] = value
    r = ctx["client"].post("/api/v1/returns", json=body, headers=hdr)
    assert r.status_code == 409
    assert len(ctx["returns_coll"].docs) == 1
    assert ctx["stock_repo"].units == snapshot


def _unapplied_return(ctx, monkeypatch):
    """A BV-PUN-01 return whose restock never landed (stock layer was down)."""
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: None)
    created = ctx["client"].post("/api/v1/returns", json=_payload(), headers=_hdr(["ADMIN"])).json()
    assert created["restock_applied"] is False
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: ctx["stock_repo"])
    return created["return_id"]


def test_another_shop_cannot_restock_an_unapplied_return(ctx, monkeypatch):
    rid = _unapplied_return(ctx, monkeypatch)
    doc_before = dict(ctx["returns_coll"].docs[0])
    r = ctx["client"].post(f"/api/v1/returns/{rid}/restock",
                           headers=_hdr(["STORE_MANAGER"], store_id="BV-RNC-01"))
    assert r.status_code == 404
    assert "STK-OLD-1" not in r.text
    assert ctx["stock_repo"].units[0]["status"] == "SOLD"
    assert ctx["returns_coll"].docs[0] == doc_before, "claim flags untouched"


def test_another_shop_cannot_read_an_applied_returns_stock_ids(ctx):
    rid = ctx["client"].post("/api/v1/returns", json=_payload(), headers=_hdr(["ADMIN"])).json()["return_id"]
    r = ctx["client"].post(f"/api/v1/returns/{rid}/restock",
                           headers=_hdr(["STORE_MANAGER"], store_id="BV-RNC-01"))
    assert r.status_code == 404 and "STK-OLD-1" not in r.text


def test_own_shop_manager_still_recovers_an_unapplied_restock(ctx, monkeypatch):
    rid = _unapplied_return(ctx, monkeypatch)
    r = ctx["client"].post(f"/api/v1/returns/{rid}/restock", headers=_hdr(["STORE_MANAGER"]))
    assert r.status_code == 200
    assert r.json()["restock_stock_ids"] == ["STK-OLD-1"]
    assert ctx["stock_repo"].units[0]["status"] == "AVAILABLE"


# ============================================================================
# PR #1381 review F1 - a return saved BEFORE the disposition joined the
# fingerprint (old format) still replays an identical retry, and still refuses
# a changed condition / restock / serial: the retry is compared against the
# saved return's OWN lines, never accepted on the old hash alone. F3 - no retry
# ever adds a second refund, credit or physical unit.
# ============================================================================


def _old_format_fingerprint(body):
    """The fingerprint exactly as returns.py computed it before #1381
    (aa14c90e): identity + qty per line, no disposition."""
    import hashlib
    import json as _json

    lines = sorted((str(it.get("order_item_id") or it.get("product_id") or ""),
                    round(float(it.get("return_qty") or 0), 4))
                   for it in body["items"] if (it.get("return_qty") or 0) > 0)
    tenders = sorted((str(t.get("method") or ""), round(float(t.get("amount") or 0), 2))
                     for t in (body.get("refund_tenders") or []))
    raw = _json.dumps([body.get("order_id") or body.get("order_number"), body["return_type"],
                       round(float(body.get("restocking_fee") or 0), 2), lines, tenders])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _books(ctx):
    """Everything a retry could duplicate: returns, money, credit, units."""
    docs = ctx["returns_coll"].docs
    return {
        "returns": len(docs),
        "refunded": round(sum(float(d.get("refund_amount") or 0) for d in docs), 2),
        "credited": round(sum(float(d.get("credit_amount") or 0) for d in docs), 2),
        "ledger_rows": len(ctx["ledger_coll"].docs),
        "ledger_total": round(sum(float(d.get("amount") or 0) for d in ctx["ledger_coll"].docs), 2),
        "store_credit": ctx["customer_repo"].customers["CUST-1"].get("store_credit"),
        "units": sorted((u["stock_id"], u["status"]) for u in ctx["stock_repo"].units),
        "order_returned": [li.get("returned_qty") for li in ctx["order_repo"].collection.docs[0]["items"]],
    }


def _saved_old_format(ctx, key, return_type="RETURN", drop_serial=False):
    """Record a return, then turn it into one saved before #1381: the old
    fingerprint (and, optionally, no serial field on its lines)."""
    body = _payload(return_type=return_type)
    r = ctx["client"].post("/api/v1/returns", json=body, headers=_hdr(["CASHIER"], key=key))
    assert r.status_code == 201, r.text
    doc = ctx["returns_coll"].docs[0]
    doc["idempotency_fingerprint"] = _old_format_fingerprint(body)
    if drop_serial:
        for ln in doc["items"]:
            ln.pop("serial", None)
    return body, r.json()


@pytest.mark.parametrize("return_type", ["RETURN", "CREDIT_NOTE"])
@pytest.mark.parametrize("drop_serial", [False, True])
def test_identical_retry_of_an_old_format_saved_return_replays_once(ctx, return_type, drop_serial):
    key = f"K-OLD-{return_type}-{drop_serial}"
    body, first = _saved_old_format(ctx, key, return_type, drop_serial)
    before = _books(ctx)
    if return_type == "CREDIT_NOTE":
        assert before["credited"] > 0 and before["ledger_rows"] == 1
    for roles in (["CASHIER"], ["SALES_STAFF"], ["ADMIN"]):
        again = ctx["client"].post("/api/v1/returns", json=body, headers=_hdr(roles, key=key))
        assert again.status_code == 201, again.text
        assert again.json()["_idempotent_replay"] is True
        assert again.json()["return_id"] == first["return_id"]
        assert again.json()["net_refund"] == first["net_refund"]
    assert _books(ctx) == before, "no second return, refund, credit or unit"


@pytest.mark.parametrize("field,value", [
    ("condition", "DAMAGED"),
    ("restock", False),
    ("serial", "SER-OTHER"),
])
def test_changed_disposition_against_an_old_format_saved_return_conflicts(ctx, field, value):
    body, _first = _saved_old_format(ctx, f"K-OLD-D-{field}")
    before = _books(ctx)
    changed = _payload()
    changed["items"][0][field] = value
    assert _old_format_fingerprint(changed) == _old_format_fingerprint(body), "old hash cannot tell"
    r = ctx["client"].post("/api/v1/returns", json=changed, headers=_hdr(["CASHIER"], key=f"K-OLD-D-{field}"))
    assert r.status_code == 409, r.text
    assert _books(ctx) == before


def test_changed_qty_against_an_old_format_saved_return_conflicts(ctx):
    _saved_old_format(ctx, "K-OLD-Q")
    before = _books(ctx)
    body = _payload()
    body["items"][0]["return_qty"] = 2
    r = ctx["client"].post("/api/v1/returns", json=body, headers=_hdr(["CASHIER"], key="K-OLD-Q"))
    assert r.status_code == 409
    assert _books(ctx) == before


def test_old_format_saved_return_from_another_shop_still_404(ctx):
    body, first = _saved_old_format(ctx, "K-OLD-X")
    before = _books(ctx)
    r = ctx["client"].post("/api/v1/returns", json=body,
                           headers=_hdr(["CASHIER"], store_id="BV-RNC-01", key="K-OLD-X"))
    assert r.status_code == 404 and first["return_id"] not in r.text
    assert _books(ctx) == before


def test_new_format_identical_retries_and_lost_ack_never_duplicate(ctx, monkeypatch):
    """Lost acknowledgement: the press landed, the till retries the same key;
    then the restock recovery is pressed twice. One return, one refund, one
    unit back -- never two."""
    hdr = _hdr(["CASHIER"], key="K-NEW-RECON")
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: None)
    first = ctx["client"].post("/api/v1/returns", json=_payload(), headers=hdr)
    assert first.status_code == 201 and first.json()["restock_applied"] is False
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: ctx["stock_repo"])
    before = _books(ctx)
    for _ in range(2):
        again = ctx["client"].post("/api/v1/returns", json=_payload(), headers=hdr)
        assert again.status_code == 201 and again.json()["_idempotent_replay"] is True
    assert _books(ctx) == before
    rid = first.json()["return_id"]
    for _ in range(2):
        rec = ctx["client"].post(f"/api/v1/returns/{rid}/restock", headers=_hdr(["STORE_MANAGER"]))
        assert rec.status_code == 200, rec.text
    after = _books(ctx)
    assert after["units"] == [("STK-OLD-1", "AVAILABLE")], "one unit back, none minted"
    after.pop("units")
    before.pop("units")
    assert after == before, "recovery moves no money"


# ============================================================================
# PR #1381 review F2 - POST /returns/quote uses the same per-object shop rule
# as POST /returns: an order with no shop on record is HQ-only, and another
# shop's order is refused, before any amount or tender is shown.
# ============================================================================


def _quote(ctx, roles, store_id="BV-PUN-01"):
    return ctx["client"].post("/api/v1/returns/quote", json=_payload(store_id=None),
                              headers=_hdr(roles, store_id=store_id))


@pytest.mark.parametrize("roles,store_id", [
    (["SALES_STAFF"], "BV-RNC-01"),
    (["SALES_STAFF"], None),
    (["CASHIER"], "BV-PUN-01"),
    (["STORE_MANAGER"], "BV-PUN-01"),
])
def test_quote_on_an_order_with_no_shop_is_refused_to_shop_staff(ctx, roles, store_id):
    ctx["order_repo"]._order.pop("store_id")
    r = _quote(ctx, roles, store_id)
    assert r.status_code == 403, r.text
    for leak in ("net_refund", "gross_refund", "captured_tenders", "1500"):
        assert leak not in r.text
    create = ctx["client"].post("/api/v1/returns", json=_payload(store_id=None),
                                headers=_hdr(roles, store_id=store_id))
    assert create.status_code == r.status_code, "quote and create agree"
    assert ctx["returns_coll"].docs == []


def test_quote_on_an_order_with_no_shop_still_works_for_admin(ctx):
    ctx["order_repo"]._order.pop("store_id")
    r = _quote(ctx, ["ADMIN"])
    assert r.status_code == 200 and r.json()["net_refund"] > 0


def test_quote_on_another_shops_order_is_refused(ctx):
    r = _quote(ctx, ["CASHIER"], "BV-RNC-01")
    assert r.status_code == 403 and "net_refund" not in r.text


@pytest.mark.parametrize("roles", [["CASHIER"], ["SALES_STAFF"], ["STORE_MANAGER"], ["ADMIN"]])
def test_quote_on_own_shops_order_still_works(ctx, roles):
    r = _quote(ctx, roles)
    assert r.status_code == 200, r.text
    assert r.json()["net_refund"] > 0
    assert ctx["returns_coll"].docs == [], "a quote writes nothing"
