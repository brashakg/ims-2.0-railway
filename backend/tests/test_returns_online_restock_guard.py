"""
IMS 2.0 - Returns restock must NEVER mint stock on a pooled ONLINE store  (F9)
=============================================================================
An ONLINE store (BV-ONLINE-01 / WO-ONLINE-01) is POOLED and STOCKLESS: the
storefront sells the physical shops' combined stock and the online store owns no
serialized units of its own. PR #970 guarded the two known mint doors
(inventory.add_stock, opening_stock_commit). `returns._restock_good_items` was a
THIRD, unguarded door.

The refund's store is derived from the ORDER (returns.py IDOR guard), and an
online order's `store_id` is the VIRTUAL online billing bucket
(shopify_ingest._online_store_id -> "BV-ONLINE-01") while its serialized units
were claimed at a PHYSICAL fulfilment store. So a GOOD-condition return against
an online order used to:
  * search for the original SOLD unit AT the online store -> never match, then
  * MINT a fresh AVAILABLE unit ON the online store.

These tests pin the fix -- REDIRECT, never block, never drop:
  * an online-order return restocks to the fulfilling PHYSICAL store and mints
    ZERO units on the online store (and reactivates the real unit);
  * with no fulfilment stamp it falls back to the store PROCESSING the return,
    then to ONLINE_FULFILLMENT_STORE_ID;
  * with no physical store resolvable at all it FAILS LOUD -- nothing minted
    anywhere, applied=False so the retry surface keeps it visible;
  * an ordinary in-store return is behaviourally unchanged;
  * the Shopify write-back is handed the PHYSICAL store, never the online one;
  * the pooled on-hand the write-back publishes EXCLUDES online-store units.

Isolated fakes only -- no Mongo, no network.
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

from api.routers import returns as returns_router  # noqa: E402
from api.routers import auth as auth_mod  # noqa: E402
from api.services import online_stock_writeback as wb  # noqa: E402

ONLINE_STORE = "BV-ONLINE-01"
PHYSICAL_FULFILMENT_STORE = "BV-BOK-01"
PHYSICAL_COUNTER_STORE = "BV-PUN-01"


# ===========================================================================
# fakes
# ===========================================================================


class _FakeStockRepo:
    """In-memory serialized stock (mirrors test_returns_restock._FakeStockRepo)."""

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

    def units_at(self, store_id):
        return [u for u in self.units if u.get("store_id") == store_id]


class _FakeResult:
    def __init__(self, matched=1):
        self.matched_count = matched
        self.modified_count = matched


def _doc_matches(d, query):
    for k, v in (query or {}).items():
        if isinstance(v, dict):
            if "$ne" in v and d.get(k) == v["$ne"]:
                return False
            if "$in" in v and d.get(k) not in v["$in"]:
                return False
            if "$nin" in v and d.get(k) in v["$nin"]:
                return False
        elif d.get(k) != v:
            return False
    return True


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = [dict(x) for x in (docs or [])]

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

    def find(self, query=None, projection=None):
        return [dict(d) for d in self.docs if _doc_matches(d, query)]

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


class _FakeOrdersColl:
    """Enough of Mongo's positional array update for the returnable-qty claim."""

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
                        if bool(operand) != (key in elem):
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
                        leaf = field.split(".")[-1]
                        line[leaf] = (line.get(leaf) or 0) + delta
                    out = dict(d)
                    out.pop("_id", None)
                    return out
            return None
        return None


class _FakeOrderRepo:
    def __init__(self, order):
        order = dict(order)
        order.setdefault(
            "items",
            [{"item_id": "li1", "product_id": "PRD-1", "quantity": 100,
              "returned_qty": 0}],
        )
        order.setdefault("status", "DELIVERED")
        order.setdefault("amount_paid", 1_000_000.0)
        self._order = order
        self.collection = _FakeOrdersColl([order])

    def find_by_id(self, oid):
        return self._order if self._order.get("order_id") == oid else None

    def find_by_order_number(self, num):
        return self._order if self._order.get("order_number") == num else None


def _staff_token(roles, store_id, uid="u1"):
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


def _build_ctx(monkeypatch, *, order, stock_units, active_store):
    """Wire the returns router against isolated fakes and return the handles."""
    app = FastAPI()
    app.include_router(returns_router.router, prefix="/api/v1/returns")

    order_repo = _FakeOrderRepo(order)
    stock_repo = _FakeStockRepo(stock_units)
    returns_coll = _FakeColl()
    stock_audit_coll = _FakeColl()
    # `stores` is consulted by is_online_store for ids outside the known list;
    # the physical shops are present with store_type PHYSICAL.
    stores_coll = _FakeColl(
        [
            {"store_id": PHYSICAL_FULFILMENT_STORE, "store_type": "PHYSICAL"},
            {"store_id": PHYSICAL_COUNTER_STORE, "store_type": "PHYSICAL"},
        ]
    )
    extra: dict = {}

    class _FakeDB:
        is_connected = True

        def __init__(self):
            self.db = self

        def get_collection(self, name):
            mapping = {
                "returns": returns_coll,
                "stock_audit": stock_audit_coll,
                "stores": stores_coll,
            }
            if name in mapping:
                return mapping[name]
            if name not in extra:
                extra[name] = _FakeColl()
            return extra[name]

    fake_db = _FakeDB()

    writeback_calls: list = []

    def _fake_writeback(db, skus, store_id, **kw):
        writeback_calls.append({"skus": list(skus or []), "store_id": store_id})

    monkeypatch.setattr(returns_router, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(
        returns_router, "get_customer_repository", lambda: _FakeCustomerRepo()
    )
    monkeypatch.setattr(returns_router, "get_product_repository", lambda: None)
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr("api.dependencies.get_db", lambda: fake_db, raising=False)
    monkeypatch.setattr(
        "api.dependencies.get_audit_repository", lambda: None, raising=False
    )
    monkeypatch.setattr(wb, "writeback_after_restock", _fake_writeback)
    monkeypatch.delenv("ONLINE_FULFILLMENT_STORE_ID", raising=False)

    return {
        "client": TestClient(app),
        "stock_repo": stock_repo,
        "returns_coll": returns_coll,
        "writeback_calls": writeback_calls,
        "token": _staff_token(["ADMIN"], active_store),
    }


def _payload(order_id, store_id, product_id="PRD-1"):
    return {
        "order_id": order_id,
        "store_id": store_id,
        "return_type": "RETURN",
        "items": [
            {
                "order_item_id": "li1",
                "product_id": product_id,
                "product_name": "Ray-Ban Aviator",
                "sku": "RB-1",
                "return_qty": 1,
                "unit_price": 1500,
                "reason": "CHANGED_MIND",
                "condition": "GOOD",
            }
        ],
    }


_ONLINE_ORDER = {
    "order_id": "ORD-ONL-1",
    "order_number": "ONL-5001",
    "customer_id": "CUST-1",
    "customer_name": "Asha",
    "payment_method": "CARD",
    # shopify_ingest stamps the VIRTUAL online billing bucket here.
    "store_id": ONLINE_STORE,
    "channel": "ONLINE",
}


# ===========================================================================
# 1. THE BUG: an online-order return restocks to a PHYSICAL store, mints
#    ZERO units on the online store.
# ===========================================================================


def test_online_order_return_restocks_to_fulfilment_store_not_online(monkeypatch):
    order = dict(
        _ONLINE_ORDER,
        fulfillment_stores=[PHYSICAL_FULFILMENT_STORE],
        fulfillment_breakdown=[
            {"product_id": "PRD-1", "store_id": PHYSICAL_FULFILMENT_STORE, "qty": 1}
        ],
    )
    ctx = _build_ctx(
        monkeypatch,
        order=order,
        stock_units=[
            {
                "stock_id": "STK-ONL-1",
                "product_id": "PRD-1",
                # The unit shopify_ingest CLAIMED at the physical shop.
                "store_id": PHYSICAL_FULFILMENT_STORE,
                "status": "SOLD",
                "order_id": "ORD-ONL-1",
            }
        ],
        active_store=PHYSICAL_FULFILMENT_STORE,
    )
    r = ctx["client"].post(
        "/api/v1/returns",
        json=_payload("ORD-ONL-1", ONLINE_STORE),
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    assert r.status_code == 201, r.text
    data = r.json()

    # ZERO units on the online store -- the whole point.
    assert ctx["stock_repo"].units_at(ONLINE_STORE) == []
    # Nothing minted at all: the REAL sold unit was reactivated instead.
    assert data["restock_applied"] is True
    assert data["restock_stock_ids"] == ["STK-ONL-1"]
    assert all(not u["stock_id"].startswith("NEW-") for u in ctx["stock_repo"].units)
    unit = ctx["stock_repo"].units[0]
    assert unit["status"] == "AVAILABLE"
    assert unit["store_id"] == PHYSICAL_FULFILMENT_STORE
    # Auditable redirect stamp.
    assert data["restock_store_id"] == PHYSICAL_FULFILMENT_STORE
    assert data["restock_store_redirected_from"] == ONLINE_STORE
    doc = ctx["returns_coll"].docs[0]
    assert doc["restock_store_id"] == PHYSICAL_FULFILMENT_STORE
    assert doc["restock_store_redirected_from"] == ONLINE_STORE
    assert doc["restock_store_reason"] == returns_router._RESTOCK_ROUTE_FULFILMENT
    # The return itself is still BOOKED against the online store (money/GST).
    assert doc["store_id"] == ONLINE_STORE


# ---------------------------------------------------------------------------
# MUST-FIX 1 (chair-reproduced regression): the redirected path must NEVER
# touch a unit belonging to a DIFFERENT order.
# ---------------------------------------------------------------------------


def test_redirected_return_never_hijacks_another_orders_sold_unit(monkeypatch):
    """THE CHAIR'S STK-WALKIN SCENARIO.

    A historical online import carries no fulfilment stamp, so the return is
    redirected to the PROCESSING shop. That shop happens to hold a SOLD unit of
    the same product -- a genuine walk-in sale to a different customer, who is
    wearing the frame. _reactivate_original_unit's order-agnostic fallback would
    grab it: flip it to AVAILABLE, erase order_id / sold_to_customer_id, and
    leave the frame that actually came back with no stock row at all.

    The redirected path must use the ORDER-SCOPED lookup only and MINT when it
    misses."""
    ctx = _build_ctx(
        monkeypatch,
        order=dict(_ONLINE_ORDER),  # historical import: no fulfilment stamp
        stock_units=[
            {
                "stock_id": "STK-WALKIN",
                "product_id": "PRD-1",
                "store_id": PHYSICAL_COUNTER_STORE,
                "status": "SOLD",
                "order_id": "ORD-WALKIN-9",
                "sold_to_customer_id": "CUST-999",
                "serial_number": "SN-WALKIN",
            }
        ],
        active_store=PHYSICAL_COUNTER_STORE,
    )
    r = ctx["client"].post(
        "/api/v1/returns",
        json=_payload("ORD-ONL-1", ONLINE_STORE),
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    assert r.status_code == 201, r.text

    # The stranger's sale is COMPLETELY untouched.
    walkin = [u for u in ctx["stock_repo"].units if u["stock_id"] == "STK-WALKIN"][0]
    assert walkin["status"] == "SOLD"
    assert walkin["order_id"] == "ORD-WALKIN-9"
    assert walkin["sold_to_customer_id"] == "CUST-999"
    assert "returned_at" not in walkin

    # The frame that actually came back IS recorded -- as a fresh unit at the
    # physical shop (the honest record when the original row is untraceable).
    minted = [u for u in ctx["stock_repo"].units if u["stock_id"].startswith("NEW-")]
    assert len(minted) == 1
    assert minted[0]["store_id"] == PHYSICAL_COUNTER_STORE
    assert minted[0]["status"] == "AVAILABLE"
    assert ctx["stock_repo"].units_at(ONLINE_STORE) == []
    data = r.json()
    assert data["restock_applied"] is True
    assert data["restocked"][0]["minted"] == 1
    assert data["restocked"][0]["reactivated"] == 0


def test_redirected_return_still_reactivates_its_OWN_order_unit(monkeypatch):
    """exact_order_only must not break the good case: when the order-scoped unit
    IS present at the redirected shop it is reactivated, not duplicated -- even
    with a decoy SOLD unit of the same product sitting next to it."""
    order = dict(_ONLINE_ORDER, fulfillment_stores=[PHYSICAL_FULFILMENT_STORE])
    ctx = _build_ctx(
        monkeypatch,
        order=order,
        stock_units=[
            {
                "stock_id": "STK-DECOY",
                "product_id": "PRD-1",
                "store_id": PHYSICAL_FULFILMENT_STORE,
                "status": "SOLD",
                "order_id": "ORD-OTHER-7",
                "sold_to_customer_id": "CUST-777",
            },
            {
                "stock_id": "STK-MINE",
                "product_id": "PRD-1",
                "store_id": PHYSICAL_FULFILMENT_STORE,
                "status": "SOLD",
                "order_id": "ORD-ONL-1",
            },
        ],
        active_store=PHYSICAL_FULFILMENT_STORE,
    )
    r = ctx["client"].post(
        "/api/v1/returns",
        json=_payload("ORD-ONL-1", ONLINE_STORE),
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["restock_stock_ids"] == ["STK-MINE"]
    by_id = {u["stock_id"]: u for u in ctx["stock_repo"].units}
    assert by_id["STK-MINE"]["status"] == "AVAILABLE"
    # The decoy from another order is untouched.
    assert by_id["STK-DECOY"]["status"] == "SOLD"
    assert by_id["STK-DECOY"]["order_id"] == "ORD-OTHER-7"


def test_ordinary_in_store_return_keeps_its_store_wide_fallback(monkeypatch):
    """The order-agnostic fallback stays for a PHYSICAL-store return: there the
    store IS where the sale happened, so 'some SOLD unit of this product at this
    shop' remains a reasonable stand-in for a lost row. Byte-unchanged."""
    order = {
        "order_id": "ORD-1",
        "order_number": "INV-1001",
        "customer_id": "CUST-1",
        "store_id": PHYSICAL_COUNTER_STORE,
    }
    ctx = _build_ctx(
        monkeypatch,
        order=order,
        stock_units=[
            {
                # No order_id at all -> only the store-wide fallback can find it.
                "stock_id": "STK-LEGACY",
                "product_id": "PRD-1",
                "store_id": PHYSICAL_COUNTER_STORE,
                "status": "SOLD",
            }
        ],
        active_store=PHYSICAL_COUNTER_STORE,
    )
    r = ctx["client"].post(
        "/api/v1/returns",
        json=_payload("ORD-1", PHYSICAL_COUNTER_STORE),
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["restock_stock_ids"] == ["STK-LEGACY"]
    assert ctx["stock_repo"].units[0]["status"] == "AVAILABLE"


# ---------------------------------------------------------------------------
# MUST-FIX 3: routing is per-UNIT, not per-product.
# ---------------------------------------------------------------------------


def test_one_line_split_across_two_shops_sends_each_unit_home(monkeypatch):
    """_claim_units_multistore splits a SINGLE line across shops when the
    preferred one is short. Routing at product granularity booked BOTH returned
    units to shop A -- minting a phantom there while shop B's real unit stayed
    SOLD forever. The per-unit queue must send one unit to each."""
    order = dict(
        _ONLINE_ORDER,
        items=[
            {"item_id": "li1", "product_id": "PRD-1", "quantity": 100,
             "returned_qty": 0},
        ],
        fulfillment_stores=[PHYSICAL_FULFILMENT_STORE, PHYSICAL_COUNTER_STORE],
        fulfillment_breakdown=[
            {"product_id": "PRD-1", "store_id": PHYSICAL_FULFILMENT_STORE, "qty": 1},
            {"product_id": "PRD-1", "store_id": PHYSICAL_COUNTER_STORE, "qty": 1},
        ],
    )
    ctx = _build_ctx(
        monkeypatch,
        order=order,
        stock_units=[
            {"stock_id": "STK-A", "product_id": "PRD-1",
             "store_id": PHYSICAL_FULFILMENT_STORE, "status": "SOLD",
             "order_id": "ORD-ONL-1"},
            {"stock_id": "STK-B", "product_id": "PRD-1",
             "store_id": PHYSICAL_COUNTER_STORE, "status": "SOLD",
             "order_id": "ORD-ONL-1"},
        ],
        active_store=PHYSICAL_FULFILMENT_STORE,
    )
    payload = _payload("ORD-ONL-1", ONLINE_STORE)
    payload["items"][0]["return_qty"] = 2
    r = ctx["client"].post(
        "/api/v1/returns",
        json=payload,
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    assert r.status_code == 201, r.text
    by_id = {u["stock_id"]: u for u in ctx["stock_repo"].units}
    # BOTH real units come home; ZERO mints, zero strandings.
    assert by_id["STK-A"]["status"] == "AVAILABLE"
    assert by_id["STK-B"]["status"] == "AVAILABLE"
    assert all(not sid.startswith("NEW-") for sid in by_id)
    assert ctx["stock_repo"].units_at(ONLINE_STORE) == []
    data = r.json()
    assert data["restocked"][0]["reactivated"] == 2
    assert data["restocked"][0]["minted"] == 0
    assert sorted(data["restocked"][0]["store_ids"]) == sorted(
        [PHYSICAL_FULFILMENT_STORE, PHYSICAL_COUNTER_STORE]
    )
    # A split must NOT name one shop as "the" restock store.
    assert data["restock_store_id"] is None
    assert sorted(data["restock_store_ids"]) == sorted(
        [PHYSICAL_FULFILMENT_STORE, PHYSICAL_COUNTER_STORE]
    )


def test_multi_store_online_order_returns_each_product_to_its_own_shop(monkeypatch):
    """shopify_ingest._claim_units_multistore can fulfil one online order from
    SEVERAL shops. Each returned product must go back to the shop its own unit
    left from -- not all of them to whichever store happened to be listed first
    (that would strand the other shop's unit SOLD forever)."""
    order = dict(
        _ONLINE_ORDER,
        items=[
            {"item_id": "li1", "product_id": "PRD-1", "quantity": 100,
             "returned_qty": 0},
            {"item_id": "li2", "product_id": "PRD-2", "quantity": 100,
             "returned_qty": 0},
        ],
        fulfillment_stores=[PHYSICAL_FULFILMENT_STORE, PHYSICAL_COUNTER_STORE],
        fulfillment_breakdown=[
            {"product_id": "PRD-1", "store_id": PHYSICAL_FULFILMENT_STORE, "qty": 1},
            {"product_id": "PRD-2", "store_id": PHYSICAL_COUNTER_STORE, "qty": 1},
        ],
    )
    ctx = _build_ctx(
        monkeypatch,
        order=order,
        stock_units=[
            {"stock_id": "STK-A", "product_id": "PRD-1",
             "store_id": PHYSICAL_FULFILMENT_STORE, "status": "SOLD",
             "order_id": "ORD-ONL-1"},
            {"stock_id": "STK-B", "product_id": "PRD-2",
             "store_id": PHYSICAL_COUNTER_STORE, "status": "SOLD",
             "order_id": "ORD-ONL-1"},
        ],
        active_store=PHYSICAL_FULFILMENT_STORE,
    )
    payload = _payload("ORD-ONL-1", ONLINE_STORE)
    payload["items"].append(
        {
            "order_item_id": "li2",
            "product_id": "PRD-2",
            "product_name": "Oakley Holbrook",
            "sku": "OK-2",
            "return_qty": 1,
            "unit_price": 2000,
            "reason": "CHANGED_MIND",
            "condition": "GOOD",
        }
    )
    r = ctx["client"].post(
        "/api/v1/returns",
        json=payload,
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    assert r.status_code == 201, r.text
    assert ctx["stock_repo"].units_at(ONLINE_STORE) == []
    by_id = {u["stock_id"]: u for u in ctx["stock_repo"].units}
    # Each shop's OWN unit was reactivated where it lives -- nothing minted.
    assert by_id["STK-A"]["status"] == "AVAILABLE"
    assert by_id["STK-A"]["store_id"] == PHYSICAL_FULFILMENT_STORE
    assert by_id["STK-B"]["status"] == "AVAILABLE"
    assert by_id["STK-B"]["store_id"] == PHYSICAL_COUNTER_STORE
    assert all(not sid.startswith("NEW-") for sid in by_id)
    rows = {row["product_id"]: row for row in r.json()["restocked"]}
    assert rows["PRD-1"]["store_id"] == PHYSICAL_FULFILMENT_STORE
    assert rows["PRD-2"]["store_id"] == PHYSICAL_COUNTER_STORE


def test_online_return_writeback_gets_physical_store_never_online(monkeypatch):
    order = dict(
        _ONLINE_ORDER, fulfillment_stores=[PHYSICAL_FULFILMENT_STORE]
    )
    ctx = _build_ctx(
        monkeypatch,
        order=order,
        stock_units=[
            {
                "stock_id": "STK-ONL-1",
                "product_id": "PRD-1",
                "store_id": PHYSICAL_FULFILMENT_STORE,
                "status": "SOLD",
                "order_id": "ORD-ONL-1",
            }
        ],
        active_store=PHYSICAL_FULFILMENT_STORE,
    )
    r = ctx["client"].post(
        "/api/v1/returns",
        json=_payload("ORD-ONL-1", ONLINE_STORE),
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    assert r.status_code == 201, r.text
    assert len(ctx["writeback_calls"]) == 1
    call = ctx["writeback_calls"][0]
    assert call["skus"] == ["RB-1"]
    assert call["store_id"] == PHYSICAL_FULFILMENT_STORE
    assert call["store_id"] != ONLINE_STORE


def test_online_return_without_fulfilment_stamp_uses_processing_store(monkeypatch):
    """A historical import never ran the decrement, so there is no fulfilment
    stamp. The goods are at the counter that took the return -> restock there."""
    ctx = _build_ctx(
        monkeypatch,
        order=dict(_ONLINE_ORDER),
        stock_units=[],
        active_store=PHYSICAL_COUNTER_STORE,
    )
    r = ctx["client"].post(
        "/api/v1/returns",
        json=_payload("ORD-ONL-1", ONLINE_STORE),
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert ctx["stock_repo"].units_at(ONLINE_STORE) == []
    minted = ctx["stock_repo"].units_at(PHYSICAL_COUNTER_STORE)
    assert len(minted) == 1
    assert minted[0]["status"] == "AVAILABLE"
    assert minted[0]["source_type"] == "RETURN"
    assert minted[0]["restocked_from_store_id"] == ONLINE_STORE
    assert (
        minted[0]["restock_route_reason"] == returns_router._RESTOCK_ROUTE_PROCESSING
    )
    assert data["restock_applied"] is True
    assert data["restock_store_id"] == PHYSICAL_COUNTER_STORE


def test_online_return_falls_back_to_configured_fulfilment_store(monkeypatch):
    """No fulfilment stamp AND the operator is working the online store itself
    -> ONLINE_FULFILLMENT_STORE_ID is the last physical candidate."""
    ctx = _build_ctx(
        monkeypatch,
        order=dict(_ONLINE_ORDER),
        stock_units=[],
        active_store=ONLINE_STORE,
    )
    monkeypatch.setenv("ONLINE_FULFILLMENT_STORE_ID", PHYSICAL_FULFILMENT_STORE)
    r = ctx["client"].post(
        "/api/v1/returns",
        json=_payload("ORD-ONL-1", ONLINE_STORE),
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    assert r.status_code == 201, r.text
    assert ctx["stock_repo"].units_at(ONLINE_STORE) == []
    minted = ctx["stock_repo"].units_at(PHYSICAL_FULFILMENT_STORE)
    assert len(minted) == 1
    assert (
        minted[0]["restock_route_reason"] == returns_router._RESTOCK_ROUTE_CONFIGURED
    )


def test_unresolvable_physical_store_fails_loud_and_mints_nothing(monkeypatch, caplog):
    """No fulfilment stamp, the operator IS the online store, and no configured
    fallback: restock NOTHING (never a phantom on the online store, never a
    silently dropped unit) and leave it retryable."""
    ctx = _build_ctx(
        monkeypatch,
        order=dict(_ONLINE_ORDER),
        stock_units=[],
        active_store=ONLINE_STORE,
    )
    with caplog.at_level("ERROR"):
        r = ctx["client"].post(
            "/api/v1/returns",
            json=_payload("ORD-ONL-1", ONLINE_STORE),
            headers={"Authorization": f"Bearer {ctx['token']}"},
        )
    # The return (money side) still records -- refunds are never blocked.
    assert r.status_code == 201, r.text
    data = r.json()
    # NOTHING minted anywhere.
    assert ctx["stock_repo"].units == []
    assert data["restock_applied"] is False
    assert data["restock_stock_ids"] == []
    # Loud + retryable + auditable.
    assert any("restock BLOCKED" in rec.message for rec in caplog.records)
    doc = ctx["returns_coll"].docs[0]
    assert doc["restock_applied"] is False
    assert doc["restock_store_id"] is None
    assert doc["restock_store_reason"] == returns_router._RESTOCK_ROUTE_UNRESOLVED
    # The un-restocked quantity is RECORDED (the unit is not lost silently).
    assert doc["restocked"][0]["quantity"] == 1
    assert doc["restocked"][0]["applied"] is False
    # And no phantom availability is published to Shopify.
    assert ctx["writeback_calls"] == []


# ===========================================================================
# 2. The ordinary in-store return is unchanged.
# ===========================================================================


def test_in_store_return_is_unchanged(monkeypatch):
    order = {
        "order_id": "ORD-1",
        "order_number": "INV-1001",
        "customer_id": "CUST-1",
        "customer_name": "Asha",
        "payment_method": "UPI",
        "store_id": PHYSICAL_COUNTER_STORE,
    }
    ctx = _build_ctx(
        monkeypatch,
        order=order,
        stock_units=[
            {
                "stock_id": "STK-OLD-1",
                "product_id": "PRD-1",
                "store_id": PHYSICAL_COUNTER_STORE,
                "status": "SOLD",
                "order_id": "ORD-1",
            }
        ],
        active_store=PHYSICAL_COUNTER_STORE,
    )
    r = ctx["client"].post(
        "/api/v1/returns",
        json=_payload("ORD-1", PHYSICAL_COUNTER_STORE),
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["restock_applied"] is True
    assert data["restock_stock_ids"] == ["STK-OLD-1"]
    assert ctx["stock_repo"].units[0]["status"] == "AVAILABLE"
    assert ctx["stock_repo"].units[0]["store_id"] == PHYSICAL_COUNTER_STORE
    # No redirect happened.
    assert data["restock_store_id"] == PHYSICAL_COUNTER_STORE
    assert data["restock_store_redirected_from"] is None
    doc = ctx["returns_coll"].docs[0]
    assert doc["restock_store_reason"] == returns_router._RESTOCK_ROUTE_DIRECT
    # Write-back still fires, with the shop's own id (as before).
    assert ctx["writeback_calls"][0]["store_id"] == PHYSICAL_COUNTER_STORE


def test_in_store_mint_carries_no_redirect_stamp(monkeypatch):
    """A physical-store mint keeps its original shape -- the redirect fields are
    only stamped when there actually was a redirect."""
    order = {
        "order_id": "ORD-1",
        "order_number": "INV-1001",
        "customer_id": "CUST-1",
        "store_id": PHYSICAL_COUNTER_STORE,
    }
    ctx = _build_ctx(
        monkeypatch, order=order, stock_units=[], active_store=PHYSICAL_COUNTER_STORE
    )
    r = ctx["client"].post(
        "/api/v1/returns",
        json=_payload("ORD-1", PHYSICAL_COUNTER_STORE),
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    assert r.status_code == 201, r.text
    minted = ctx["stock_repo"].units_at(PHYSICAL_COUNTER_STORE)
    assert len(minted) == 1
    assert minted[0]["restocked_from_store_id"] is None
    assert minted[0]["restock_route_reason"] == returns_router._RESTOCK_ROUTE_DIRECT


# ===========================================================================
# 2b. THE OTHER TWO DOORS, driven end to end.
#     MUST-FIX 2: the Shopify refund webhook is the DOMINANT automated online
#     return door and it used to pre-resolve the store itself, which made the
#     guard short-circuit "already physical" so the per-unit narrowing never
#     ran. Door 2 (retry_restock) had no coverage at all.
# ===========================================================================


def test_webhook_door_routes_each_product_to_its_own_shop(monkeypatch):
    """Drive services.shopify_refund._post_credit_and_restock for a two-shop
    online order. Every unit must land at the shop it left from, nothing on the
    online store, and the PERSISTED doc must carry the guard's answer -- not the
    pre-guard proposal."""
    from api.services import shopify_refund as sr

    order = dict(
        _ONLINE_ORDER,
        order_id="ORD-ONL-9",
        items=[
            {"item_id": "li1", "product_id": "PRD-1", "quantity": 1, "sku": "RB-1",
             "unit_price": 1500, "returned_qty": 0},
            {"item_id": "li2", "product_id": "PRD-2", "quantity": 1, "sku": "OK-2",
             "unit_price": 2000, "returned_qty": 0},
        ],
        # SORTED SET -> alphabetical, NOT the shop that shipped each line. This
        # is exactly what made the old pre-resolution pick the wrong shop.
        fulfillment_stores=sorted([PHYSICAL_FULFILMENT_STORE, PHYSICAL_COUNTER_STORE]),
        fulfillment_breakdown=[
            {"product_id": "PRD-1", "store_id": PHYSICAL_FULFILMENT_STORE, "qty": 1},
            {"product_id": "PRD-2", "store_id": PHYSICAL_COUNTER_STORE, "qty": 1},
        ],
    )
    ctx = _build_ctx(
        monkeypatch,
        order=order,
        stock_units=[
            {"stock_id": "STK-A", "product_id": "PRD-1",
             "store_id": PHYSICAL_FULFILMENT_STORE, "status": "SOLD",
             "order_id": "ORD-ONL-9"},
            {"stock_id": "STK-B", "product_id": "PRD-2",
             "store_id": PHYSICAL_COUNTER_STORE, "status": "SOLD",
             "order_id": "ORD-ONL-9"},
        ],
        active_store=PHYSICAL_FULFILMENT_STORE,
    )
    returns_coll = ctx["returns_coll"]

    class _WebhookDB:
        def get_collection(self, name):
            return returns_coll if name == "returns" else _FakeColl()

    lines = [
        returns_router.ReturnLine(
            order_item_id="li1", product_id="PRD-1", sku="RB-1",
            product_name="Ray-Ban", return_qty=1, unit_price=1500, condition="GOOD",
        ),
        returns_router.ReturnLine(
            order_item_id="li2", product_id="PRD-2", sku="OK-2",
            product_name="Oakley", return_qty=1, unit_price=2000, condition="GOOD",
        ),
    ]
    # The pre-guard hint must no longer be able to name the online store.
    assert sr._proposed_restock_store_for_order(order) != ONLINE_STORE

    out = sr._post_credit_and_restock(
        _WebhookDB(),
        refund_id="RF-9001",
        order=order,
        return_lines=lines,
        credit_note={"gross_refund": 0.0, "net_refund": 0.0, "gst_breakup": {},
                     "lines": []},
        restock_store=sr._proposed_restock_store_for_order(order),
    )

    # Each unit came home; nothing minted, nothing on the online store.
    by_id = {u["stock_id"]: u for u in ctx["stock_repo"].units}
    assert by_id["STK-A"]["status"] == "AVAILABLE"
    assert by_id["STK-A"]["store_id"] == PHYSICAL_FULFILMENT_STORE
    assert by_id["STK-B"]["status"] == "AVAILABLE"
    assert by_id["STK-B"]["store_id"] == PHYSICAL_COUNTER_STORE
    assert all(not sid.startswith("NEW-") for sid in by_id)
    assert ctx["stock_repo"].units_at(ONLINE_STORE) == []
    assert out["restock_applied"] is True
    assert sorted(out["restock_store_ids"]) == sorted(
        [PHYSICAL_FULFILMENT_STORE, PHYSICAL_COUNTER_STORE]
    )
    # The PERSISTED doc carries the guard's answer, never the online store.
    doc = [d for d in returns_coll.docs if d.get("shopify_refund_id") == "RF-9001"][0]
    assert doc["restock_store_id"] != ONLINE_STORE
    assert sorted(doc["restock_store_ids"]) == sorted(
        [PHYSICAL_FULFILMENT_STORE, PHYSICAL_COUNTER_STORE]
    )
    assert doc["restock_store_redirected_from"] == ONLINE_STORE


def test_webhook_door_on_stampless_order_mints_nothing_on_the_online_store(monkeypatch):
    """Historical import (no fulfilment stamp) + SYSTEM caller (no processing
    store) + no configured fallback -> restock NOTHING, and the persisted doc
    must not claim the units went to the online store."""
    from api.services import shopify_refund as sr

    order = dict(_ONLINE_ORDER, order_id="ORD-ONL-7")
    ctx = _build_ctx(
        monkeypatch, order=order, stock_units=[], active_store=PHYSICAL_COUNTER_STORE
    )
    returns_coll = ctx["returns_coll"]

    class _WebhookDB:
        def get_collection(self, name):
            return returns_coll if name == "returns" else _FakeColl()

    # The hint itself must be None now (it used to fall back to order.store_id,
    # i.e. the stockless online bucket, and the screen rendered that).
    assert sr._proposed_restock_store_for_order(order) is None

    out = sr._post_credit_and_restock(
        _WebhookDB(),
        refund_id="RF-7001",
        order=order,
        return_lines=[
            returns_router.ReturnLine(
                order_item_id="li1", product_id="PRD-1", sku="RB-1",
                product_name="Ray-Ban", return_qty=1, unit_price=1500,
                condition="GOOD",
            )
        ],
        credit_note={"gross_refund": 0.0, "net_refund": 0.0, "gst_breakup": {},
                     "lines": []},
        restock_store=sr._proposed_restock_store_for_order(order),
    )
    assert ctx["stock_repo"].units == []
    assert out["restock_applied"] is False
    assert out["restock_store_id"] is None
    doc = [d for d in returns_coll.docs if d.get("shopify_refund_id") == "RF-7001"][0]
    assert doc["restock_store_id"] is None
    assert doc["restock_store_reason"] == returns_router._RESTOCK_ROUTE_UNRESOLVED


def test_retry_restock_door_redirects_off_the_online_store(monkeypatch):
    """Door 2: POST /returns/{id}/restock on a return doc booked to the online
    store must land the unit at the physical fulfilment shop, not mint on the
    online store."""
    order = dict(_ONLINE_ORDER, fulfillment_stores=[PHYSICAL_FULFILMENT_STORE])
    ctx = _build_ctx(
        monkeypatch,
        order=order,
        stock_units=[
            {"stock_id": "STK-ONL-1", "product_id": "PRD-1",
             "store_id": PHYSICAL_FULFILMENT_STORE, "status": "SOLD",
             "order_id": "ORD-ONL-1"},
        ],
        active_store=PHYSICAL_FULFILMENT_STORE,
    )
    ctx["returns_coll"].insert_one(
        {
            "return_id": "RET-PROBE-1",
            "order_id": "ORD-ONL-1",
            "store_id": ONLINE_STORE,
            "restock_applied": False,
            "restock_stock_ids": [],
            "items": [
                {
                    "order_item_id": "li1",
                    "product_id": "PRD-1",
                    "product_name": "Ray-Ban Aviator",
                    "sku": "RB-1",
                    "return_qty": 1,
                    "unit_price": 1500,
                    "condition": "GOOD",
                }
            ],
        }
    )
    r = ctx["client"].post(
        "/api/v1/returns/RET-PROBE-1/restock",
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["restock_applied"] is True
    assert data["restock_stock_ids"] == ["STK-ONL-1"]
    assert data["restock_store_id"] == PHYSICAL_FULFILMENT_STORE
    assert ctx["stock_repo"].units_at(ONLINE_STORE) == []
    assert ctx["stock_repo"].units[0]["status"] == "AVAILABLE"
    # The retry now re-pushes the recovered count to Shopify (it did not before,
    # leaving the frame sellable in-shop but invisible online).
    assert ctx["writeback_calls"][-1]["store_id"] == PHYSICAL_FULFILMENT_STORE


# ===========================================================================
# 3. The resolver itself (also the door services/shopify_refund walks in by).
# ===========================================================================


@pytest.fixture
def resolver_db(monkeypatch):
    stores = _FakeColl(
        [
            {"store_id": PHYSICAL_FULFILMENT_STORE, "store_type": "PHYSICAL"},
            {"store_id": PHYSICAL_COUNTER_STORE, "store_type": "PHYSICAL"},
            {"store_id": "WO-EXTRA-ONLINE", "store_type": "ONLINE"},
        ]
    )

    class _DB:
        def get_collection(self, name):
            return stores if name == "stores" else _FakeColl()

    monkeypatch.setattr(returns_router, "_get_db", lambda: _DB())
    monkeypatch.setattr(returns_router, "get_order_repository", lambda: None)
    monkeypatch.delenv("ONLINE_FULFILLMENT_STORE_ID", raising=False)
    return _DB()


def test_resolver_passes_physical_store_through(resolver_db):
    out = returns_router._resolve_restock_store(PHYSICAL_COUNTER_STORE, "ORD-1")
    assert out["store_id"] == PHYSICAL_COUNTER_STORE
    assert out["redirected_from"] is None
    assert out["reason"] == returns_router._RESTOCK_ROUTE_DIRECT


def test_resolver_prefers_fulfilment_over_processing_store(resolver_db):
    out = returns_router._resolve_restock_store(
        ONLINE_STORE,
        "ORD-ONL-1",
        processing_store_id=PHYSICAL_COUNTER_STORE,
        order={"fulfillment_stores": [PHYSICAL_FULFILMENT_STORE]},
    )
    assert out["store_id"] == PHYSICAL_FULFILMENT_STORE
    assert out["reason"] == returns_router._RESTOCK_ROUTE_FULFILMENT


def test_resolver_skips_an_online_store_listed_as_fulfilment(resolver_db):
    """A mis-configured order whose fulfilment stamp is itself the online store
    must not be trusted -- fall through to the physical counter."""
    out = returns_router._resolve_restock_store(
        ONLINE_STORE,
        "ORD-ONL-1",
        processing_store_id=PHYSICAL_COUNTER_STORE,
        order={"fulfillment_stores": [ONLINE_STORE, "WO-EXTRA-ONLINE"]},
    )
    assert out["store_id"] == PHYSICAL_COUNTER_STORE
    assert out["reason"] == returns_router._RESTOCK_ROUTE_PROCESSING


def test_resolver_reads_breakdown_when_stores_list_absent(resolver_db):
    out = returns_router._resolve_restock_store(
        ONLINE_STORE,
        "ORD-ONL-1",
        order={
            "fulfillment_breakdown": [
                {"product_id": "PRD-1", "store_id": PHYSICAL_FULFILMENT_STORE, "qty": 1}
            ]
        },
    )
    assert out["store_id"] == PHYSICAL_FULFILMENT_STORE


def test_resolver_detects_online_by_store_type_not_just_known_ids(resolver_db):
    """The detector is store_type-driven, so a NEW online store is caught too."""
    out = returns_router._resolve_restock_store(
        "WO-EXTRA-ONLINE",
        "ORD-ONL-9",
        processing_store_id=PHYSICAL_COUNTER_STORE,
        order={},
    )
    assert out["store_id"] == PHYSICAL_COUNTER_STORE
    assert out["redirected_from"] == "WO-EXTRA-ONLINE"


def test_resolver_passes_a_blank_legacy_store_through_unchanged(resolver_db):
    """A legacy order with no store stamp is NOT an online store -- it must keep
    its existing store-agnostic restock, not be newly blocked by this guard."""
    for blank in (None, "", "   "):
        out = returns_router._resolve_restock_store(blank, "ORD-LEGACY")
        assert out["store_id"] == blank
        assert out["redirected_from"] is None
        assert out["reason"] == returns_router._RESTOCK_ROUTE_DIRECT


def test_blank_store_return_still_restocks(monkeypatch):
    """End-to-end guard on the same legacy case: nothing is blocked."""
    order = {
        "order_id": "ORD-LEGACY",
        "order_number": "INV-LEGACY",
        "customer_id": "CUST-1",
        "store_id": None,
    }
    ctx = _build_ctx(
        monkeypatch, order=order, stock_units=[], active_store=PHYSICAL_COUNTER_STORE
    )
    r = ctx["client"].post(
        "/api/v1/returns",
        json={k: v for k, v in _payload("ORD-LEGACY", None).items() if v is not None},
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["restock_applied"] is True
    assert len(ctx["stock_repo"].units) == 1


def test_resolver_returns_none_when_no_physical_store_exists(resolver_db):
    out = returns_router._resolve_restock_store(
        ONLINE_STORE,
        "ORD-ONL-1",
        processing_store_id=ONLINE_STORE,
        order={"fulfillment_stores": []},
    )
    assert out["store_id"] is None
    assert out["redirected_from"] == ONLINE_STORE
    assert out["reason"] == returns_router._RESTOCK_ROUTE_UNRESOLVED


# ===========================================================================
# 4. The write-back's POOLED on-hand excludes online-store units.
# ===========================================================================


def _match_ok(doc, match):
    for key, cond in (match or {}).items():
        val = doc.get(key)
        if key == "$or":
            if not any(_match_ok(doc, sub) for sub in cond):
                return False
            continue
        if isinstance(cond, dict):
            if "$in" in cond and val not in cond["$in"]:
                return False
            if "$nin" in cond and val in cond["$nin"]:
                return False
            if "$exists" in cond and bool(cond["$exists"]) != (key in doc):
                return False
        elif val != cond:
            return False
    return True


class _FakeStockUnitsColl:
    def __init__(self, units):
        self.units = units
        self.last_match = None

    def aggregate(self, pipeline):
        self.last_match = pipeline[0]["$match"]
        rows = {}
        for u in self.units:
            if not _match_ok(u, self.last_match):
                continue
            rows[u["product_id"]] = rows.get(u["product_id"], 0) + int(
                u.get("quantity", 1)
            )
        return [{"_id": pid, "n": n} for pid, n in rows.items()]


def _wb_db(units, stores):
    stock_coll = _FakeStockUnitsColl(units)

    class _DB:
        def get_collection(self, name):
            if name == "products":
                return _FakeColl([{"product_id": "PRD-1", "sku": "RB-1"}])
            if name == "stock_units":
                return stock_coll
            if name == "stores":
                return _FakeColl(stores)
            return _FakeColl()

    return _DB(), stock_coll


def test_pooled_on_hand_excludes_units_on_an_online_store():
    """A phantom AVAILABLE unit parked on the online store must NOT be published
    to Shopify -- no shop can pick it."""
    units = [
        {"product_id": "PRD-1", "store_id": PHYSICAL_FULFILMENT_STORE,
         "status": "AVAILABLE"},
        {"product_id": "PRD-1", "store_id": PHYSICAL_COUNTER_STORE,
         "status": "AVAILABLE"},
        {"product_id": "PRD-1", "store_id": ONLINE_STORE, "status": "AVAILABLE"},
    ]
    db, stock_coll = _wb_db(
        units, [{"store_id": ONLINE_STORE, "store_type": "ONLINE"}]
    )
    out = wb._on_hand_for_skus(db, ["RB-1"], None)
    assert out == {"RB-1": 2}
    assert ONLINE_STORE in stock_coll.last_match["store_id"]["$nin"]


def test_pooled_on_hand_excludes_a_new_online_store_by_store_type():
    units = [
        {"product_id": "PRD-1", "store_id": PHYSICAL_COUNTER_STORE,
         "status": "AVAILABLE"},
        {"product_id": "PRD-1", "store_id": "NEW-ONLINE-99", "status": "AVAILABLE"},
    ]
    db, _ = _wb_db(units, [{"store_id": "NEW-ONLINE-99", "store_type": "ONLINE"}])
    assert wb._on_hand_for_skus(db, ["RB-1"], None) == {"RB-1": 1}


def test_pooled_on_hand_still_counts_every_physical_store():
    """The exclusion must never shrink a real shop's contribution."""
    units = [
        {"product_id": "PRD-1", "store_id": PHYSICAL_FULFILMENT_STORE,
         "status": "AVAILABLE"},
        {"product_id": "PRD-1", "store_id": PHYSICAL_COUNTER_STORE,
         "status": "AVAILABLE"},
        {"product_id": "PRD-1", "store_id": "BV-RAN-01", "status": "AVAILABLE"},
    ]
    db, _ = _wb_db(units, [])
    assert wb._on_hand_for_skus(db, ["RB-1"], None) == {"RB-1": 3}


def test_online_store_ids_falls_back_to_known_ids_when_lookup_fails():
    """A `stores` blow-up must never degrade the exclusion to 'exclude nothing'."""

    class _BoomDB:
        def get_collection(self, name):
            raise RuntimeError("mongo down")

    ids = wb._online_store_ids(_BoomDB())
    assert ONLINE_STORE in ids
    assert "WO-ONLINE-01" in ids


# ===========================================================================
# 5. MUST-FIX 4 -- the CLAIM path. Excluding online stores from the pooled
#    COUNT only fixes what Shopify is TOLD; this stops IMS CONSUMING a phantom.
# ===========================================================================


class _FakeClaimStock:
    """stock_units stand-in for shopify_ingest._available_stores_for_product."""

    def __init__(self, units):
        self.units = units

    def aggregate(self, pipeline):
        match = pipeline[0]["$match"]
        counts: dict = {}
        for u in self.units:
            if u.get("product_id") != match.get("product_id"):
                continue
            if u.get("status") != match.get("status"):
                continue
            counts[u["store_id"]] = counts.get(u["store_id"], 0) + 1
        rows = [{"_id": s, "n": n} for s, n in counts.items()]
        # count desc, then store_id asc -- the real pipeline's sort.
        rows.sort(key=lambda r: (-r["n"], r["_id"]))
        return rows


def _claim_db(units, stores):
    stock = _FakeClaimStock(units)

    class _DB:
        def get_collection(self, name):
            if name == "stock_units":
                return stock
            if name == "stores":
                return _FakeColl(stores)
            return _FakeColl()

    return _DB()


def test_online_store_is_never_a_fulfilment_candidate():
    """A phantom AVAILABLE unit on the online store must not be claimable. Note
    'BV-ONLINE-01' sorts AHEAD of 'BV-PUN-01' on a count tie, so without the
    exclusion it would be tried FIRST."""
    from api.services import shopify_ingest as si

    units = [
        {"product_id": "PRD-1", "store_id": ONLINE_STORE, "status": "AVAILABLE"},
        {"product_id": "PRD-1", "store_id": PHYSICAL_COUNTER_STORE,
         "status": "AVAILABLE"},
    ]
    db = _claim_db(units, [{"store_id": ONLINE_STORE, "store_type": "ONLINE"}])
    out = si._available_stores_for_product(db, "PRD-1")
    assert ONLINE_STORE not in out
    assert out == [PHYSICAL_COUNTER_STORE]


def test_claim_candidates_still_list_every_physical_shop():
    """The exclusion must not shrink the real fallback list."""
    from api.services import shopify_ingest as si

    units = [
        {"product_id": "PRD-1", "store_id": PHYSICAL_COUNTER_STORE,
         "status": "AVAILABLE"},
        {"product_id": "PRD-1", "store_id": PHYSICAL_FULFILMENT_STORE,
         "status": "AVAILABLE"},
        {"product_id": "PRD-1", "store_id": PHYSICAL_FULFILMENT_STORE,
         "status": "AVAILABLE"},
    ]
    db = _claim_db(units, [])
    # Most stock first.
    assert si._available_stores_for_product(db, "PRD-1") == [
        PHYSICAL_FULFILMENT_STORE,
        PHYSICAL_COUNTER_STORE,
    ]


def test_online_preferred_store_is_skipped_and_claim_falls_through(monkeypatch):
    """An ONLINE preferred fulfilment store must never be claimed against; the
    claim falls through to the physical shops instead of faking a fulfilment."""
    from api.services import shopify_ingest as si

    units = [
        {"product_id": "PRD-1", "store_id": PHYSICAL_COUNTER_STORE,
         "status": "AVAILABLE"},
    ]
    db = _claim_db(units, [{"store_id": ONLINE_STORE, "store_type": "ONLINE"}])
    tried: list = []

    def _fake_mark_units_sold(order_id, lines, store_id):
        tried.append(store_id)
        if store_id == PHYSICAL_COUNTER_STORE:
            return ["STK-1"]
        return []

    import api.routers.orders as orders_mod

    monkeypatch.setattr(orders_mod, "_mark_units_sold", _fake_mark_units_sold)

    claimed, breakdown = si._claim_units_multistore(
        db, "ORD-X", [{"product_id": "PRD-1", "quantity": 1}], ONLINE_STORE
    )
    assert ONLINE_STORE not in tried
    assert claimed == 1
    assert breakdown == [
        {"product_id": "PRD-1", "store_id": PHYSICAL_COUNTER_STORE, "qty": 1}
    ]


def test_phantom_only_stock_now_under_claims_loudly(monkeypatch):
    """The whole point: when the ONLY AVAILABLE unit is a phantom on the online
    store, the claim must MISS (so the under-claim fail-loud fires) instead of
    silently succeeding against a unit nobody has."""
    from api.services import shopify_ingest as si

    units = [{"product_id": "PRD-1", "store_id": ONLINE_STORE, "status": "AVAILABLE"}]
    db = _claim_db(units, [{"store_id": ONLINE_STORE, "store_type": "ONLINE"}])

    import api.routers.orders as orders_mod

    monkeypatch.setattr(
        orders_mod, "_mark_units_sold", lambda oid, lines, s: ["X"] if s else []
    )

    claimed, breakdown = si._claim_units_multistore(
        db, "ORD-X", [{"product_id": "PRD-1", "quantity": 1}], ONLINE_STORE
    )
    assert claimed == 0  # expected 1 -> the caller records an under-claim MISS
    assert breakdown == []


# ===========================================================================
# 6. MUST-FIX 6 -- a blocked restock must reach a human, not just Railway logs.
# ===========================================================================


def test_blocked_restock_raises_a_deduped_system_task(monkeypatch):
    ctx = _build_ctx(
        monkeypatch,
        order=dict(_ONLINE_ORDER),
        stock_units=[],
        active_store=ONLINE_STORE,
    )
    raised: list = []
    import api.services.task_triggers as tt

    class _FakeTaskRepo:
        def find_many(self, _q):
            return []

        def create(self, doc):
            return doc

    monkeypatch.setattr(
        "api.dependencies.get_task_repository", lambda: _FakeTaskRepo(), raising=False
    )
    monkeypatch.setattr(
        tt, "create_system_task", lambda repo, **kw: raised.append(kw) or kw
    )
    r = ctx["client"].post(
        "/api/v1/returns",
        json=_payload("ORD-ONL-1", ONLINE_STORE),
        headers={"Authorization": f"Bearer {ctx['token']}"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["restock_applied"] is False
    assert len(raised) == 1
    task = raised[0]
    assert task["priority"] == "P1"
    assert task["dedupe_ref"].startswith("return_restock_blocked:")
    assert "RB-1" in task["description"]
    # The intent row carries the SAME keys as an applied row, so a UI reading
    # row["store_id"] does not blow up on exactly the failure path.
    doc = ctx["returns_coll"].docs[0]
    assert doc["restocked"][0]["store_id"] is None
    assert doc["restocked"][0]["store_ids"] == []


def test_explicit_store_scope_is_unchanged():
    """Passing an explicit store_id still scopes to exactly that store."""
    units = [
        {"product_id": "PRD-1", "store_id": PHYSICAL_COUNTER_STORE,
         "status": "AVAILABLE"},
        {"product_id": "PRD-1", "store_id": PHYSICAL_FULFILMENT_STORE,
         "status": "AVAILABLE"},
    ]
    db, stock_coll = _wb_db(units, [])
    assert wb._on_hand_for_skus(db, ["RB-1"], PHYSICAL_COUNTER_STORE) == {"RB-1": 1}
    assert stock_coll.last_match["store_id"] == PHYSICAL_COUNTER_STORE
