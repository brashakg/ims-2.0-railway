"""
Multi-location PR 5 -- an online order ships from, is claimed at and is billed by
the shop Shopify assigned it to (owner rulings 2026-09-06/07, Q1/Q2/Q4).

Driven end to end through THE door (online_fulfillment_route.map_routed_order ->
online_order_mapper -> shopify_ingest) on a mongomock database with the REAL
OrderRepository (invoice series), StockRepository (atomic unit claim),
stores_util.physical_stores (the one shop reader) and the real stock aggregate.
Shopify is a MOCKED TRANSCRIPT: shopify_push._graphql is replaced by a fake
that answers the routing read and the fulfillmentOrderMove and refuses any other
call; no network is ever reached.

Rules pinned (each was reverted in the source and seen red, see the PR notes):
  R1 claim + bill + fulfil at the assigned location's shop
  R2 a short shop -> the whole order moves to a MAPPED shop that holds it
     (fulfillmentOrderMove sent through the gate)
  R3 an unmapped assigned location -> ONLINE_FULFILLMENT_STORE_ID fallback,
     loud (order flag + one deduped task)
  R4 no shop holds every unit -> never split, stock-miss hold + task
  R5 seller GSTIN / CGST+SGST vs IGST / invoice series follow the shipping shop
  R6 a shipping shop with no GSTIN for its state -> loud AND held; the
     invoice door refuses it
  R7 a failed move -> loud, and the fulfillment order is not the shop's
  R8 dark gate -> zero Shopify calls, documented fallback
  R9 the fulfilment push closes only the shipping shop's fulfillment orders
  R10 every create door (webhook drain, missed-webhook pull) reads the routing
  R11 (money panel, round 2) a fulfillment order moves only INTO a shop that
     holds the whole order; one left at another shop is loud and holds the
     order; the routing count and the claim are one rule; a pending or failed
     move holds the order, a crash-left move is retried; the stock write-back
     lands after the move; no shop named -> SELLER_UNKNOWN; the door result
     names the order's own shop
  R12 (money panel, round 3) Shopify's own split is claimed at each shop (no
     move, no hold) and shipped/restocked per shop, held only across GSTINs; a
     move lifts only its own hold and reads the Rx hold as stored; a planned
     move is skipped for a cancelled, human-released or short-claimed order;
     the dispatch rewrites stock after ANY human move; dark/unread never
     guesses a seller from stock counts; the fallback must be an ACTIVE
     physical shop; the claim is at the shipping shop, never at the fallback
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test_x")
os.environ["GST_PRICING_MODE"] = "inclusive"

mongomock = pytest.importorskip("mongomock")

from api.services import online_fulfillment_route as route_mod  # noqa: E402
from api.services import shopify_push  # noqa: E402

LOC_BOK = "gid://shopify/Location/101"
LOC_RAN = "gid://shopify/Location/102"
LOC_PUNE_SHOPIFY = "gid://shopify/Location/199"  # exists on Shopify, no IMS shop maps it
PUNE = "8f0c2d1e-pune-uuid"  # Pune's store_id is a UUID on prod
FO_1 = "gid://shopify/FulfillmentOrder/7001"
FO_2 = "gid://shopify/FulfillmentOrder/7002"

_STORES = [
    {"store_id": "BV-BOK-01", "store_code": "BV-BOK-01", "store_name": "Bokaro",
     "store_type": "RETAIL", "is_active": True, "state_code": "20",
     "gstin": "20AAAAA0000A1Z5", "shopify_location_id": LOC_BOK},
    {"store_id": "BV-RAN-01", "store_code": "BV-RAN-01", "store_name": "Ranchi",
     "store_type": "RETAIL", "is_active": True, "state_code": "20",
     "gstin": "20AAAAA0000A1Z5", "shopify_location_id": LOC_RAN},
    {"store_id": PUNE, "store_code": "BV-PUN-01", "store_name": "Pune",
     "store_type": "RETAIL", "is_active": True, "state_code": "27",
     "gstin": "27BBBBB0000B1Z5"},
    {"store_id": "BV-ONLINE-01", "store_code": "BV-ONLINE-01",
     "store_name": "Online", "store_type": "ONLINE", "is_active": True,
     "state_code": "20", "gstin": "20AAAAA0000A1Z5"},
]
_PRODUCTS = {"RB-1234": "P-RB", "OA-5": "P-OA"}


class _Tasks:
    def __init__(self):
        self.created = []

    def find_many(self, q):
        return [t for t in self.created if t.get("source_ref") == q.get("source_ref")]

    def create(self, task):
        self.created.append(task)
        return task

    def refs(self, prefix):
        return [t["source_ref"] for t in self.created if t["source_ref"].startswith(prefix)]


class _Shopify:
    """The mocked Shopify transcript: every call is recorded; only the routing
    read and fulfillmentOrderMove are answered, anything else fails the test."""

    def __init__(self):
        self.calls = []
        self.fos = []
        self.move_error = None
        self.read_error = None

    def fo(self, fo_id, location_id, units=1, status="OPEN", name="loc", lines=None):
        """``lines`` = [(order line item id, qty)] names WHICH order lines the
        fulfillment order carries (a Shopify split); without it the lines are
        anonymous, as in the older transcripts."""
        nodes = (
            [{"remainingQuantity": q, "lineItem": {"id": f"gid://shopify/LineItem/{lid}"}}
             for lid, q in lines]
            if lines
            else [{"remainingQuantity": units}]
        )
        self.fos.append(
            {
                "id": fo_id,
                "status": status,
                "assignedLocation": {"name": name, "location": {"id": location_id}},
                "lineItems": {"nodes": nodes},
            }
        )

    async def graphql(self, db, query, variables):
        self.calls.append((query, dict(variables)))
        if "imsOrderRouting" in query:
            if self.read_error:
                raise ValueError(self.read_error)
            return {"data": {"order": {"id": variables["id"], "fulfillmentOrders": {"nodes": self.fos}}}}
        if "imsFulfillmentOrderMove" in query:
            await asyncio.sleep(0)  # a real network hop: lets a 2nd sender interleave
            if self.move_error:
                return {"data": {"fulfillmentOrderMove": {
                    "movedFulfillmentOrder": None,
                    "userErrors": [{"field": ["id"], "message": self.move_error}]}}}
            return {"data": {"fulfillmentOrderMove": {
                "movedFulfillmentOrder": {"id": variables["id"], "status": "OPEN"},
                "userErrors": []}}}
        raise AssertionError(f"unexpected Shopify call: {query[:60]}")

    def moves(self):
        return [v for q, v in self.calls if "imsFulfillmentOrderMove" in q]


@pytest.fixture
def world(monkeypatch):
    db = mongomock.MongoClient()["ims_pr5"]
    db.stores.insert_many([dict(s) for s in _STORES])

    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.order_repository import OrderRepository
    from database.repositories.product_repository import StockRepository
    import api.dependencies as deps
    from api.routers import orders as orders_mod

    class _Products:
        def find_by_sku(self, sku):
            pid = _PRODUCTS.get(sku)
            return {"product_id": pid, "hsn_code": "9003", "category": "FRAME"} if pid else None

    class _Stores:
        def find_by_id(self, store_id):
            return db.stores.find_one({"store_id": store_id}, {"_id": 0})

        def find_active(self, filter=None):
            return list(db.stores.find({"is_active": True}, {"_id": 0}))

    tasks = _Tasks()
    shop = _Shopify()
    monkeypatch.setattr(deps, "get_order_repository", lambda: OrderRepository(db.orders))
    monkeypatch.setattr(deps, "get_customer_repository", lambda: CustomerRepository(db.customers))
    monkeypatch.setattr(deps, "get_product_repository", lambda: _Products())
    monkeypatch.setattr(deps, "get_store_repository", lambda: _Stores())
    monkeypatch.setattr(deps, "get_task_repository", lambda: tasks)
    monkeypatch.setattr(deps, "get_user_repository", lambda: None)
    monkeypatch.setattr(orders_mod, "get_stock_repository", lambda: StockRepository(db.stock_units))
    monkeypatch.setattr(shopify_push, "_live_or_reason", lambda _db: (True, None))
    monkeypatch.setattr(shopify_push, "_graphql", shop.graphql)
    # The after-sale Shopify stock write-back is PR 2's (tested there); keep it off the loop.
    import api.services.online_stock_writeback as wb

    monkeypatch.setattr(wb, "writeback_after_sale", lambda *a, **k: None)
    monkeypatch.setenv("ONLINE_STORE_ID", "BV-ONLINE-01")
    monkeypatch.delenv("ONLINE_FULFILLMENT_STORE_ID", raising=False)
    monkeypatch.delenv("ONLINE_FULFILLMENT_FALLBACK", raising=False)
    return {"db": db, "tasks": tasks, "shop": shop}


def _stock(db, store_id, pid, n):
    for i in range(n):
        db.stock_units.insert_one(
            {"stock_id": f"U-{store_id}-{pid}-{i}", "product_id": pid,
             "store_id": store_id, "status": "AVAILABLE"}
        )


def _order(order_id, lines=(("RB-1234", 1),), buyer_state="20"):
    return {
        "id": order_id,
        "name": f"#{order_id}",
        "financial_status": "paid",
        "email": "buyer@example.com",
        "phone": "+91 98765 43210",
        "customer": {"id": 555, "first_name": "Ravi", "last_name": "Kumar"},
        "shipping_address": {"province": buyer_state, "province_code": buyer_state},
        "line_items": [
            {"id": 9000 + i, "product_id": 7000 + i, "title": sku,
             "product_type": "Frames", "sku": sku, "quantity": qty,
             "price": "999.00", "total_discount": "0.00"}
            for i, (sku, qty) in enumerate(lines)
        ],
    }


def _book(world, payload):
    res = asyncio.run(route_mod.map_routed_order(payload, world["db"], topic="orders/create"))
    assert res["status"] == "created", res
    order = world["db"].orders.find_one({"order_id": res["order_id"]}, {"_id": 0})
    return res, order


def _sold_at(db, order_id):
    return sorted(u["store_id"] for u in db.stock_units.find({"order_id": order_id}))


# ---------------------------------------------------------------------------
# R1 -- the assigned location's shop claims, bills and fulfils
# ---------------------------------------------------------------------------


def test_order_is_claimed_billed_and_fulfilled_at_the_assigned_shop(world):
    db = world["db"]
    _stock(db, "BV-BOK-01", "P-RB", 1)
    _stock(db, "BV-RAN-01", "P-RB", 3)  # more stock elsewhere must not steal it
    world["shop"].fo(FO_1, LOC_BOK)

    res, order = _book(world, _order(51001))

    assert order["store_id"] == "BV-BOK-01"
    assert _sold_at(db, res["order_id"]) == ["BV-BOK-01"]
    route = order["fulfillment_route"]
    assert route["reason"] == "ASSIGNED"
    assert route["assigned_location_id"] == LOC_BOK
    assert route["fulfillment_order_ids"] == [FO_1]
    assert route["problems"] == [] and world["shop"].moves() == []
    # The shop's OWN invoice series (store segment), not the online bucket's.
    assert "/BV-BOK-01/" in order["invoice_number"]
    # Its return restocks straight back at the shop that shipped it.
    from api.routers import returns

    assert returns._resolve_restock_store(order["store_id"], order["order_id"], order=order) == {
        "store_id": "BV-BOK-01", "redirected_from": None, "reason": "PHYSICAL_STORE",
    }


# ---------------------------------------------------------------------------
# R2 -- short shop -> ONE mapped shop that holds the whole order + a move
# ---------------------------------------------------------------------------


def test_short_assigned_shop_moves_the_order_to_a_mapped_shop_that_holds_it(world):
    db = world["db"]
    _stock(db, "BV-RAN-01", "P-RB", 2)
    _stock(db, PUNE, "P-RB", 9)  # more stock, but no Shopify location to move to
    world["shop"].fo(FO_1, LOC_BOK)

    res, order = _book(world, _order(51002))

    assert order["store_id"] == "BV-RAN-01"
    assert _sold_at(db, res["order_id"]) == ["BV-RAN-01"]
    assert world["shop"].moves() == [{"id": FO_1, "newLocationId": LOC_RAN}]
    route = order["fulfillment_route"]
    assert route["reason"] == "MOVED"
    assert route["moves"][0]["status"] == "MOVED"
    assert route["fulfillment_order_ids"] == [FO_1]
    assert route["problems"] == []
    # The pending-move hold the booking put on it is lifted by the move.
    assert order["fulfillment_hold"] is False and "stock_hold_reason" not in order
    assert route["hold_reason"] is None
    # The shop that now ships it is told to.
    assert world["tasks"].refs("online_fallback_ship:") == [
        f"online_fallback_ship:{res['order_id']}:BV-RAN-01"
    ]


# ---------------------------------------------------------------------------
# R3 -- unmapped assigned location: the documented fallback, LOUDLY
# ---------------------------------------------------------------------------


def test_unmapped_location_uses_the_fallback_shop_and_is_loud(world, monkeypatch):
    db = world["db"]
    monkeypatch.setenv("ONLINE_FULFILLMENT_STORE_ID", PUNE)
    _stock(db, PUNE, "P-RB", 1)
    _stock(db, "BV-BOK-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_PUNE_SHOPIFY, name="Pune warehouse")

    res, order = _book(world, _order(51003))

    assert order["store_id"] == PUNE
    assert _sold_at(db, res["order_id"]) == [PUNE]
    route = order["fulfillment_route"]
    assert route["reason"] == "FALLBACK"
    assert [p["code"] for p in route["problems"]] == ["LOCATION_UNMAPPED"]
    assert "Pune warehouse" in route["problems"][0]["message"]
    # Pune has no location: nothing to move; the unmapped location's FO is Pune's.
    assert world["shop"].moves() == []
    assert route["fulfillment_order_ids"] == [FO_1]
    assert world["tasks"].refs("online_route:") == [
        f"online_route:LOCATION_UNMAPPED:{res['order_id']}"
    ]
    # A replayed delivery books nothing and tasks nothing more.
    asyncio.run(route_mod.map_routed_order(_order(51003), db, topic="orders/create"))
    assert len(world["tasks"].refs("online_route:")) == 1


def test_unmapped_location_without_a_fallback_moves_to_a_mapped_holder(world):
    db = world["db"]
    _stock(db, "BV-RAN-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_PUNE_SHOPIFY)

    res, order = _book(world, _order(51004))

    assert order["store_id"] == "BV-RAN-01"
    assert world["shop"].moves() == [{"id": FO_1, "newLocationId": LOC_RAN}]
    assert [p["code"] for p in order["fulfillment_route"]["problems"]] == ["LOCATION_UNMAPPED"]


# ---------------------------------------------------------------------------
# R4 -- nobody holds every unit: no split, fail loud
# ---------------------------------------------------------------------------


def test_no_shop_holds_every_unit_fails_loud_and_never_splits(world):
    db = world["db"]
    _stock(db, "BV-BOK-01", "P-RB", 1)
    _stock(db, "BV-RAN-01", "P-OA", 1)
    world["shop"].fo(FO_1, LOC_BOK, units=2)

    res, order = _book(world, _order(51005, lines=(("RB-1234", 1), ("OA-5", 1))))

    assert order["store_id"] == "BV-BOK-01"
    # Only what the assigned shop holds is claimed -- Ranchi's unit is NOT pulled
    # into a second shop (and so a second seller) behind the invoice's back.
    assert _sold_at(db, res["order_id"]) == ["BV-BOK-01"]
    assert world["shop"].moves() == []
    assert order["fulfillment_hold"] is True
    assert "Stock could not be claimed" in order["stock_hold_reason"]
    assert world["tasks"].refs("online_stock_miss:") == [f"online_stock_miss:{res['order_id']}"]


# ---------------------------------------------------------------------------
# R5 -- the seller follows the shipping shop (Q1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "location,buyer,shop,interstate",
    [
        (LOC_BOK, "27", "BV-BOK-01", True),   # Jharkhand shop -> MH buyer: IGST
        (LOC_BOK, "20", "BV-BOK-01", False),  # Jharkhand shop -> JH buyer: CGST+SGST
        ("gid://shopify/Location/103", "27", PUNE, False),  # MH shop -> MH buyer
        ("gid://shopify/Location/103", "20", PUNE, True),   # MH shop -> JH buyer
    ],
)
def test_seller_gstin_and_tax_split_follow_the_shipping_shop(world, location, buyer, shop, interstate):
    db = world["db"]
    db.stores.update_one({"store_id": PUNE}, {"$set": {"shopify_location_id": "gid://shopify/Location/103"}})
    _stock(db, shop, "P-RB", 1)
    world["shop"].fo(FO_1, location)

    _res, order = _book(world, _order(51006, buyer_state=buyer))

    assert order["store_id"] == shop
    assert order["interstate"] is interstate
    totals = order["tax_totals"]
    if interstate:
        assert totals["igst"] > 0 and totals["cgst"] == totals["sgst"] == 0
    else:
        assert totals["igst"] == 0 and totals["cgst"] > 0 and totals["sgst"] > 0
    # The printed/JSON invoice reads its seller GSTIN from order.store_id.
    from api.routers.orders.invoices import _build_invoice_gst_split

    seller = db.stores.find_one({"store_id": order["store_id"]})
    assert _build_invoice_gst_split(order["items"], seller, {})["store_gstin"] == seller["gstin"]
    assert order["place_of_supply"] == buyer


# ---------------------------------------------------------------------------
# R6 -- the shipping shop must have a GSTIN for its own state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "gstin,state",
    [
        ("", None),  # no GSTIN at all (and no state to check one against)
        ("27AAAAA0000A1Z5", "20"),  # a GSTIN registered in ANOTHER state
    ],
)
def test_shop_without_a_gstin_for_its_state_is_loud(world, monkeypatch, gstin, state):
    db = world["db"]
    db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": gstin, "state_code": state}})
    _stock(db, "BV-BOK-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK)

    res, order = _book(world, _order(51007))

    assert order["store_id"] == "BV-BOK-01"  # the paid order still books
    assert [p["code"] for p in order["fulfillment_route"]["problems"]] == ["SHOP_GSTIN_MISSING"]
    assert world["tasks"].refs("online_route:") == [f"online_route:SHOP_GSTIN_MISSING:{res['order_id']}"]
    # ...and it is HELD: the goods must not leave before a tax invoice can be
    # issued from the shop's own GSTIN (round 3: it used to be only tasked).
    assert order["fulfillment_hold"] is True
    assert order["stock_hold_reason"] == order["fulfillment_route"]["problems"][0]["message"]
    # The invoice door refuses it too (a wrong-state GSTIN used to print CGST+SGST).
    from fastapi import HTTPException
    from api.routers.orders import invoices as inv_mod
    from database.repositories.order_repository import OrderRepository

    monkeypatch.setattr(inv_mod, "get_order_repository", lambda: OrderRepository(db.orders))
    with pytest.raises(HTTPException) as ei:
        inv_mod._assemble_invoice(res["order_id"], {"roles": ["SUPERADMIN"]})
    assert ei.value.status_code == 400


# ---------------------------------------------------------------------------
# R7 -- a failed move is loud and the fulfillment order is not the shop's
# ---------------------------------------------------------------------------


def test_failed_move_is_loud_and_the_fo_is_not_the_shipping_shops(world):
    db = world["db"]
    _stock(db, "BV-RAN-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK)
    world["shop"].move_error = "Fulfillment order cannot be moved"

    res, order = _book(world, _order(51008))

    route = order["fulfillment_route"]
    assert order["store_id"] == "BV-RAN-01"
    assert route["moves"][0]["status"] == "FAILED"
    assert route["fulfillment_order_ids"] == []
    assert [p["code"] for p in route["problems"]] == ["MOVE_FAILED"]
    assert world["tasks"].refs("online_route:") == [f"online_route:MOVE_FAILED:{res['order_id']}"]
    # Shopify still has it (and its committed unit) at Bokaro: Ranchi must not
    # pack it until a human moves it -- the order is HELD, under the move's reason.
    assert order["fulfillment_hold"] is True
    assert "could not move" in order["stock_hold_reason"]


def test_failed_routing_read_is_loud(world, monkeypatch):
    db = world["db"]
    monkeypatch.setenv("ONLINE_FULFILLMENT_STORE_ID", "BV-BOK-01")
    _stock(db, "BV-BOK-01", "P-RB", 1)
    world["shop"].read_error = "status 503"

    res, order = _book(world, _order(51009))

    assert order["store_id"] == "BV-BOK-01"
    assert order["fulfillment_route"]["fulfillment_order_ids"] is None
    assert [p["code"] for p in order["fulfillment_route"]["problems"]] == ["ROUTING_UNREAD"]
    assert world["tasks"].refs("online_route:") == [f"online_route:ROUTING_UNREAD:{res['order_id']}"]


# ---------------------------------------------------------------------------
# R8 -- DARK: zero Shopify calls, the documented fallback, no task spam
# ---------------------------------------------------------------------------


def test_dark_gate_makes_no_shopify_call_and_uses_the_fallback(world, monkeypatch):
    db = world["db"]
    monkeypatch.setattr(shopify_push, "_live_or_reason", lambda _db: (False, "writes_disabled"))
    monkeypatch.setenv("ONLINE_FULFILLMENT_STORE_ID", "BV-BOK-01")
    _stock(db, "BV-BOK-01", "P-RB", 1)

    res, order = _book(world, _order(51010))

    assert world["shop"].calls == []
    assert order["store_id"] == "BV-BOK-01"
    route = order["fulfillment_route"]
    assert route["reason"] == "FALLBACK" and route["fulfillment_order_ids"] is None
    assert route["problems"] == [] and world["tasks"].refs("online_route:") == []


def test_move_is_not_sent_when_the_gate_closes(world, monkeypatch):
    db = world["db"]
    _stock(db, "BV-RAN-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK)
    # The read ran live; the gate closes before the move goes out.
    real = route_mod.move_fulfillment_orders

    async def closed_then_move(_db, order_id):
        monkeypatch.setattr(shopify_push, "_live_or_reason", lambda _d: (False, "writes_disabled"))
        return await real(_db, order_id)

    monkeypatch.setattr(route_mod, "move_fulfillment_orders", closed_then_move)

    _res, order = _book(world, _order(51011))

    assert world["shop"].moves() == []
    assert order["fulfillment_route"]["moves"][0]["status"] == "FAILED"
    assert "writes_disabled" in order["fulfillment_route"]["moves"][0]["error"]


# ---------------------------------------------------------------------------
# R9 -- the fulfilment push closes ONLY the shipping shop's fulfillment orders
# ---------------------------------------------------------------------------


def _push(world, order, fo_locations):
    """push_fulfillment against a mocked transcript whose open fulfillment
    orders sit at ``fo_locations`` = {fo_id: location gid}."""
    from api.services.shopify_fulfillment_push import push_fulfillment

    world["shop"].calls.clear()

    async def answer(db, query, variables):
        world["shop"].calls.append((query, variables))
        if "imsOrderFulfillmentOrders" in query:
            return {"data": {"order": {"id": "x", "fulfillments": [], "fulfillmentOrders": {"edges": [
                {"node": {"id": fo, "status": "OPEN", "assignedLocation": {"location": {"id": loc}}}}
                for fo, loc in fo_locations.items()
            ]}}}}
        return {"data": {"fulfillmentCreateV2": {"fulfillment": {"id": "gid://shopify/Fulfillment/1"}, "userErrors": []}}}

    shopify_push._graphql = answer
    try:
        return asyncio.run(push_fulfillment(world["db"], order))
    finally:
        shopify_push._graphql = world["shop"].graphql


def _fulfilled(world):
    return [
        [r["fulfillmentOrderId"] for r in v["fulfillment"]["lineItemsByFulfillmentOrder"]]
        for q, v in world["shop"].calls
        if "fulfillmentCreateV2" in q
    ]


def test_push_fulfils_only_the_shipping_shops_fulfillment_orders(world):
    base = {"order_id": "o1", "source": "shopify", "shopify_order_id": "51012"}
    split = {FO_1: LOC_BOK, FO_2: LOC_RAN}
    routed = {"fulfillment_route": {"fulfillment_order_ids": []}}

    # Bokaro ships: only the FO Shopify has at Bokaro's location NOW -- which
    # also covers one a human moved there after a failed move (none recorded).
    # Ranchi's FO stays open on Shopify, so the push is recorded but LOUD.
    res = _push(world, {**base, **routed, "store_id": "BV-BOK-01"}, split)
    assert not res.ok and _fulfilled(world) == [[FO_1]]
    assert "stay OPEN at another shop" in res.error

    res = _push(world, {**base, **routed, "store_id": "BV-BOK-01"}, {FO_1: LOC_BOK})
    assert res.ok and _fulfilled(world) == [[FO_1]]

    # Nothing at the shipping shop's location: loud, nothing closed.
    res = _push(world, {**base, **routed, "store_id": "BV-BOK-01"}, {FO_2: LOC_RAN})
    assert not res.ok and _fulfilled(world) == []
    assert "another shop" in res.error

    # The fallback shop has no location: the FOs at locations no shop maps.
    pune = {**base, "store_id": PUNE, "fulfillment_route": {"fulfillment_order_ids": [FO_1]}}
    res = _push(world, pune, {FO_1: LOC_PUNE_SHOPIFY})
    assert res.ok and _fulfilled(world) == [[FO_1]]
    # ... never one at a MAPPED shop's location (P4: Bokaro would ship it again).
    res = _push(world, pune, {FO_1: LOC_PUNE_SHOPIFY, FO_2: LOC_BOK})
    assert not res.ok and _fulfilled(world) == [[FO_1]]

    # A pre-PR-5 order (no fulfillment_route at all) keeps the legacy all-open push.
    res = _push(world, {**base, "store_id": "BV-ONLINE-01"}, split)
    assert res.ok and _fulfilled(world) == [[FO_1, FO_2]]


@pytest.mark.parametrize("booked_route", [
    {"reason": "MOVED", "fulfillment_order_ids": None,
     "problems": [{"code": "ROUTING_UNREAD", "message": "status 503"}]},  # read failed
    {"reason": "FALLBACK", "fulfillment_order_ids": None, "problems": []},  # dark at booking
])
def test_push_never_closes_another_shops_fo_when_routing_was_not_read(world, booked_route):
    """A PR-5 order whose routing was never read at booking (fo ids None) is
    judged by where Shopify has each FO NOW, not as a pre-PR-5 order: IMS
    shipped from Ranchi, the only open FO is at Bokaro -> nothing closed, loud."""
    order = {"order_id": "o2", "source": "shopify", "shopify_order_id": "51020",
             "store_id": "BV-RAN-01", "fulfillment_route": booked_route}

    res = _push(world, order, {FO_1: LOC_BOK})

    assert not res.ok and _fulfilled(world) == []


def test_push_after_a_human_moved_a_failed_move_rewrites_the_shops_stock(world, monkeypatch):
    """P6: the human's move shifted Shopify's committed unit after the booking
    write-back; the dispatch re-asserts the absolute per-shop numbers."""
    import api.services.online_stock_writeback as wb

    wrote = []
    monkeypatch.setattr(wb, "writeback_after_sale", lambda db, items, store: wrote.append(store))
    order = {"order_id": "o3", "source": "shopify", "shopify_order_id": "51021",
             "store_id": "BV-RAN-01", "items": [{"sku": "RB-1234", "quantity": 1}],
             "fulfillment_route": {"moves": [{"fulfillment_order_id": FO_1, "status": "FAILED"}]}}

    res = _push(world, order, {FO_1: LOC_RAN})  # a human moved it to Ranchi

    assert res.ok and _fulfilled(world) == [[FO_1]]
    assert wrote == ["BV-RAN-01"]


# ---------------------------------------------------------------------------
# R10 -- every create door reads Shopify's routing
# ---------------------------------------------------------------------------


def test_webhook_drain_and_missed_webhook_pull_both_route(world, monkeypatch):
    db = world["db"]
    _stock(db, "BV-BOK-01", "P-RB", 2)
    world["shop"].fo(FO_1, LOC_BOK)

    from agents import nexus_providers as np
    from agents.implementations.nexus import NexusAgent

    agent = NexusAgent(db=db)
    asyncio.run(agent._dispatch_shopify_order(_order(51013), "orders/create", {}))
    bucket, _why = asyncio.run(np._catch_up_one(db, _order(51014), "51014", True))
    assert bucket == "mapped"

    reads = [v["id"] for q, v in world["shop"].calls if "imsOrderRouting" in q]
    assert reads == ["gid://shopify/Order/51013", "gid://shopify/Order/51014"]
    for sid in ("51013", "51014"):
        order = db.orders.find_one({"shopify_order_id": sid})
        assert order["store_id"] == "BV-BOK-01"
        assert order["fulfillment_route"]["reason"] == "ASSIGNED"


def test_a_routing_stamp_on_a_stored_payload_is_never_trusted(world):
    """A replayed/stored payload may carry an old _ims_routing stamp; only the
    door's own fresh read decides the shop."""
    db = world["db"]
    _stock(db, "BV-BOK-01", "P-RB", 1)
    _stock(db, "BV-RAN-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK)
    stale = _order(51015)
    stale["_ims_routing"] = {"fulfillment_orders": [
        {"id": FO_2, "status": "OPEN", "location_id": LOC_RAN, "units": 1}
    ]}

    _res, order = _book(world, stale)

    assert order["store_id"] == "BV-BOK-01"
    assert order["fulfillment_route"]["fulfillment_order_ids"] == [FO_1]


# ---------------------------------------------------------------------------
# R11 -- money panel, round 2
# ---------------------------------------------------------------------------


def test_a_split_no_single_shop_covers_moves_nothing_into_a_short_shop(world):
    """P1: Shopify split RB->Bokaro, OA->Ranchi; Bokaro holds RB, but Ranchi
    cannot ship its own part (it holds RB, not OA) and no shop holds the whole
    order. IMS never moves Ranchi's FO INTO Bokaro (which cannot ship OA); the
    left FO is loud and the order is held. (When each shop DOES hold its own
    part, Shopify's split ships as it is -- R12 below.)"""
    db = world["db"]
    _stock(db, "BV-BOK-01", "P-RB", 1)
    _stock(db, "BV-RAN-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 1)])
    world["shop"].fo(FO_2, LOC_RAN, lines=[(9001, 1)])

    res, order = _book(world, _order(52001, lines=(("RB-1234", 1), ("OA-5", 1))))

    route = order["fulfillment_route"]
    assert world["shop"].moves() == [] and route["moves"] == []
    assert route["fulfillment_order_ids"] == [FO_1]  # FO_2 is not Bokaro's
    assert "FO_AT_OTHER_SHOP" in [p["code"] for p in route["problems"]]
    assert order["fulfillment_hold"] is True
    assert world["tasks"].refs("online_route:FO_AT_OTHER_SHOP") == [
        f"online_route:FO_AT_OTHER_SHOP:{res['order_id']}"
    ]


def test_relocation_off_sends_no_move_even_to_consolidate(world, monkeypatch):
    """P1: ONLINE_FULFILLMENT_FALLBACK=off is 'no relocation' -- Bokaro covers
    everything, Shopify split it, and still nothing is moved; loud + held."""
    db = world["db"]
    monkeypatch.setenv("ONLINE_FULFILLMENT_FALLBACK", "off")
    _stock(db, "BV-BOK-01", "P-RB", 1)
    _stock(db, "BV-BOK-01", "P-OA", 1)
    world["shop"].fo(FO_1, LOC_BOK)
    world["shop"].fo(FO_2, LOC_RAN)

    _res, order = _book(world, _order(52002, lines=(("RB-1234", 1), ("OA-5", 1))))

    assert world["shop"].moves() == []
    assert [p["code"] for p in order["fulfillment_route"]["problems"]] == ["FO_AT_OTHER_SHOP"]
    assert order["fulfillment_hold"] is True


def test_fallback_shop_split_with_a_mapped_shop_is_loud_not_stranded(world, monkeypatch):
    """P4: the fallback shop (no location) claims the whole order, but Shopify
    also has an FO at Bokaro -- it can't be moved to a shop with no location,
    so it is named and the order is held (Bokaro could ship it again)."""
    db = world["db"]
    monkeypatch.setenv("ONLINE_FULFILLMENT_STORE_ID", PUNE)
    _stock(db, PUNE, "P-RB", 2)
    _stock(db, PUNE, "P-OA", 1)
    world["shop"].fo(FO_1, LOC_PUNE_SHOPIFY, units=2, name="Pune warehouse")
    world["shop"].fo(FO_2, LOC_BOK, units=1, name="Bokaro")

    _res, order = _book(world, _order(52003, lines=(("RB-1234", 2), ("OA-5", 1))))

    route = order["fulfillment_route"]
    assert order["store_id"] == PUNE and world["shop"].moves() == []
    assert route["fulfillment_order_ids"] == [FO_1]
    assert [p["code"] for p in route["problems"]] == ["LOCATION_UNMAPPED", "FO_AT_OTHER_SHOP"]
    assert "Bokaro" in route["problems"][1]["message"]
    assert order["fulfillment_hold"] is True


@pytest.mark.parametrize("bokaro_unit", [
    {},  # no status field (a legacy minted row)
    {"status": "available"},  # lowercase: on hand, but the claim wants AVAILABLE
    {"status": "AVAILABLE", "expiry_date": "2020-01-01"},  # expired: F2 unclaimable
])
def test_a_unit_the_claim_refuses_does_not_keep_the_order_at_that_shop(world, bokaro_unit):
    """P2: the routing count asks the claim's question. Bokaro's only unit is
    one claim_one_available will not take, so Ranchi (a real AVAILABLE unit)
    ships it -- not a held order at Bokaro."""
    db = world["db"]
    db.stock_units.insert_one({"stock_id": "U-BOK", "product_id": "P-RB",
                               "store_id": "BV-BOK-01", **bokaro_unit})
    _stock(db, "BV-RAN-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK)

    res, order = _book(world, _order(52004))

    assert order["store_id"] == "BV-RAN-01"
    assert order["fulfillment_route"]["reason"] == "MOVED"
    assert _sold_at(db, res["order_id"]) == ["BV-RAN-01"]
    assert order["fulfillment_hold"] is False


@pytest.mark.parametrize("shape", [
    {"status": "AVAILABLE"},
    {"status": "available"},
    {"status": " available "},
    {"status": "IN_STOCK"},
    {},
    {"status": None},
    {"status": "RESERVED"},
    {"status": "SOLD"},
    {"status": "AVAILABLE", "expiry_date": "2020-01-01"},
    {"status": "AVAILABLE", "expiry_date": "2999-01-01"},
    {"status": "AVAILABLE", "expiry_date": "31/12/2025"},  # unreadable: fail-open
])
def test_routing_count_and_the_claim_are_one_rule(world, shape):
    """The differential: for every stored shape, the shop count route_order
    decides with == what claim_one_available actually takes."""
    from database.repositories.product_repository import StockRepository

    db = world["db"]
    db.stock_units.insert_one({"stock_id": "U-1", "product_id": "P-X", "store_id": "BV-BOK-01", **shape})

    counted = route_mod._stock_by_store(db, "P-X").get("BV-BOK-01", 0)
    claimed = StockRepository(db.stock_units).claim_one_available("P-X", "BV-BOK-01", "o-diff")

    assert counted == (1 if claimed else 0), (shape, counted, claimed)


def test_quantity_decides_cover_not_just_presence(world):
    """P5: RB x2, Bokaro holds ONE, Ranchi holds two -> Ranchi ships all of it."""
    db = world["db"]
    _stock(db, "BV-BOK-01", "P-RB", 1)
    _stock(db, "BV-RAN-01", "P-RB", 2)
    world["shop"].fo(FO_1, LOC_BOK, units=2)

    res, order = _book(world, _order(52005, lines=(("RB-1234", 2),)))

    assert order["store_id"] == "BV-RAN-01"
    assert order["fulfillment_route"]["reason"] == "MOVED"
    assert _sold_at(db, res["order_id"]) == ["BV-RAN-01", "BV-RAN-01"]
    assert world["shop"].moves() == [{"id": FO_1, "newLocationId": LOC_RAN}]


def test_a_move_a_crash_left_planned_holds_the_order_and_the_next_delivery_sends_it(world, monkeypatch):
    """P3: the process dies between booking and the move. The order is booked
    HELD (not dispatchable with the FO at the wrong shop); the next delivery
    for it (orders/updated) sends the move once and lifts the hold."""
    db = world["db"]
    _stock(db, "BV-RAN-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK)
    real = route_mod.move_fulfillment_orders

    async def died(_db, _order_id):
        return {"moved": 0, "failed": 0}

    monkeypatch.setattr(route_mod, "move_fulfillment_orders", died)
    res, order = _book(world, _order(52006))
    assert order["fulfillment_route"]["moves"][0]["status"] == "PLANNED"
    assert order["fulfillment_hold"] is True and "IMS is moving" in order["stock_hold_reason"]

    monkeypatch.setattr(route_mod, "move_fulfillment_orders", real)
    asyncio.run(route_mod.map_routed_order(_order(52006), db, topic="orders/updated"))

    order = db.orders.find_one({"order_id": res["order_id"]})
    assert world["shop"].moves() == [{"id": FO_1, "newLocationId": LOC_RAN}]
    assert order["fulfillment_route"]["moves"][0]["status"] == "MOVED"
    assert order["fulfillment_hold"] is False and "stock_hold_reason" not in order


def test_two_deliveries_never_send_one_move_twice(world, monkeypatch):
    """The retry above must not race the creator. Another worker process read
    the order while its move was still PLANNED (a stale read no in-process
    lock can see); when it gets to send, the move is already claimed and sent
    -- exactly one fulfillmentOrderMove goes out."""
    db = world["db"]
    _stock(db, "BV-RAN-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK)
    real = route_mod.move_fulfillment_orders

    async def later(_db, _order_id):
        return {"moved": 0, "failed": 0}

    monkeypatch.setattr(route_mod, "move_fulfillment_orders", later)
    res, stale = _book(world, _order(52007))
    monkeypatch.setattr(route_mod, "move_fulfillment_orders", real)
    asyncio.run(real(db, res["order_id"]))  # the creator sends it

    class _StaleRead:  # the other worker's view: read before the send
        def find_one(self, *_a, **_k):
            return stale

        def update_one(self, *a, **k):
            return db.orders.update_one(*a, **k)

    monkeypatch.setattr(route_mod, "_orders", lambda _db: _StaleRead())
    asyncio.run(real(db, res["order_id"]))

    assert world["shop"].moves() == [{"id": FO_1, "newLocationId": LOC_RAN}]
    stored = db.orders.find_one({"order_id": res["order_id"]})
    assert stored["fulfillment_route"]["moves"][0]["status"] == "MOVED"


def test_the_stock_write_back_lands_after_the_move(world, monkeypatch):
    """P6: inventorySetQuantities writes absolute per-location numbers, so it
    must run AFTER the fulfillmentOrderMove -- once, not also before it."""
    import api.services.online_stock_writeback as wb

    db = world["db"]
    shop = world["shop"]
    monkeypatch.setattr(wb, "writeback_after_sale", lambda *a, **k: shop.calls.append(("WRITEBACK", {})))
    _stock(db, "BV-RAN-01", "P-RB", 1)
    shop.fo(FO_1, LOC_BOK)

    _book(world, _order(52008))

    seq = [
        q if q == "WRITEBACK" else ("MOVE" if "imsFulfillmentOrderMove" in q else "READ")
        for q, _v in shop.calls
    ]
    assert seq == ["READ", "MOVE", "WRITEBACK"]


def test_no_shop_named_bills_the_bucket_loudly(world):
    """[PR-5 design] live, Shopify routed to Pune's unmapped location, no
    fallback set, no mapped shop holds it: billed from the bucket -- and the
    order SAYS so (SELLER_UNKNOWN, tasked), never silently."""
    db = world["db"]
    _stock(db, PUNE, "P-RB", 1)
    world["shop"].fo(FO_1, LOC_PUNE_SHOPIFY, name="Pune warehouse")

    res, order = _book(world, _order(52009))

    route = order["fulfillment_route"]
    assert order["store_id"] == "BV-ONLINE-01" and route["reason"] == "NONE"
    assert [p["code"] for p in route["problems"]] == ["LOCATION_UNMAPPED", "SELLER_UNKNOWN"]
    assert "BV-ONLINE-01" in route["problems"][1]["message"]
    assert f"online_route:SELLER_UNKNOWN:{res['order_id']}" in world["tasks"].refs("online_route:")


def test_no_shop_named_in_dark_mode_is_loud_too(world, monkeypatch):
    """[PR-5 design] dark gate, no fallback, no physical shop holds it."""
    monkeypatch.setattr(shopify_push, "_live_or_reason", lambda _db: (False, "writes_disabled"))

    res, order = _book(world, _order(52010))

    assert order["store_id"] == "BV-ONLINE-01"
    assert [p["code"] for p in order["fulfillment_route"]["problems"]] == ["SELLER_UNKNOWN"]
    assert f"online_route:SELLER_UNKNOWN:{res['order_id']}" in world["tasks"].refs("online_route:")


def test_routing_that_raises_bills_the_bucket_loudly(world, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("routing exploded")

    monkeypatch.setattr(route_mod, "route_order", boom)
    _res, order = _book(world, _order(52011))

    assert order["store_id"] == "BV-ONLINE-01"
    codes = [p["code"] for p in order["fulfillment_route"]["problems"]]
    assert codes == ["ROUTING_UNREAD", "SELLER_UNKNOWN"]


def test_every_door_result_names_the_orders_own_shop(world):
    """[Stale second answer] the mapper result (NEXUS log, missed-webhook pull,
    Re-map audit) names the shop the order is billed at, not the bucket --
    on the create and on a later delivery of the same order."""
    db = world["db"]
    _stock(db, "BV-BOK-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK)

    res, order = _book(world, _order(52012))
    again = asyncio.run(route_mod.map_routed_order(_order(52012), db, topic="orders/updated"))

    assert order["store_id"] == "BV-BOK-01"
    assert res["store_id"] == "BV-BOK-01"
    assert again["status"] == "duplicate" and again["store_id"] == "BV-BOK-01"



# ---------------------------------------------------------------------------
# R12 -- money panel, round 3
# ---------------------------------------------------------------------------

LOC_PUN = "gid://shopify/Location/103"


async def _died(_db, _order_id):
    """A process that died between booking and the move: the move stays PLANNED."""
    return {"moved": 0, "failed": 0}


def _book_with_a_planned_move(world, monkeypatch, order_id):
    """Ranchi holds the unit, Shopify assigned Bokaro -> a move is PLANNED and
    the order booked HELD; the process dies before sending it."""
    _stock(world["db"], "BV-RAN-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK)
    real = route_mod.move_fulfillment_orders
    monkeypatch.setattr(route_mod, "move_fulfillment_orders", _died)
    res, order = _book(world, _order(order_id))
    monkeypatch.setattr(route_mod, "move_fulfillment_orders", real)
    assert order["fulfillment_route"]["moves"][0]["status"] == "PLANNED"
    assert order["fulfillment_hold"] is True
    return res["order_id"], real


def test_shopifys_own_split_is_claimed_at_each_shop_and_nothing_moves(world):
    """P1 (Q2 allows Shopify's split, Q4 moves only a SHORT shop): each shop
    holds its own part, so each claims it -- Ranchi's OA unit is SOLD, not left
    on sale while Shopify has it committed to FO_2 -- nothing moves, nothing is
    held, and the dispatch and the return treat each shop's part as its own."""
    db = world["db"]
    _stock(db, "BV-BOK-01", "P-RB", 1)
    _stock(db, "BV-RAN-01", "P-OA", 1)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 1)])
    world["shop"].fo(FO_2, LOC_RAN, lines=[(9001, 1)])

    res, order = _book(world, _order(53001, lines=(("RB-1234", 1), ("OA-5", 1))))

    route = order["fulfillment_route"]
    assert _sold_at(db, res["order_id"]) == ["BV-BOK-01", "BV-RAN-01"]
    assert db.stock_units.count_documents({"status": "AVAILABLE"}) == 0
    assert world["shop"].moves() == [] and route["moves"] == []
    assert route["problems"] == [] and order["fulfillment_hold"] is False
    assert "stock_hold_reason" not in order
    # One invoice from one GSTIN: both shops are the same registration.
    assert order["store_id"] == "BV-BOK-01" and "/BV-BOK-01/" in order["invoice_number"]
    assert route["fulfillment_order_ids"] == [FO_1, FO_2]
    assert sorted(world["tasks"].refs("online_fallback_ship:")) == [
        f"online_fallback_ship:{res['order_id']}:BV-BOK-01",
        f"online_fallback_ship:{res['order_id']}:BV-RAN-01",
    ]
    # Each returned unit goes back to the shop it left from.
    from api.routers import returns

    for pid, shop in (("P-RB", "BV-BOK-01"), ("P-OA", "BV-RAN-01")):
        hit = returns._resolve_restock_store(
            order["store_id"], order["order_id"], order=order, product_id=pid
        )
        assert hit["store_id"] == shop, (pid, hit)
    # The dispatch closes BOTH shops' fulfillment orders -- none is left open
    # at a shop that could ship it again.
    pushed = _push(world, order, {FO_1: LOC_BOK, FO_2: LOC_RAN})
    assert pushed.ok and _fulfilled(world) == [[FO_1, FO_2]]


def test_a_split_across_two_gstins_is_held_for_the_accountant(world):
    """One order, one tax invoice, one GSTIN (Q1): Shopify split it between a
    Jharkhand shop and a Maharashtra shop -> each still claims its own part
    (the units are committed there), but the order is HELD and tasked."""
    db = world["db"]
    db.stores.update_one({"store_id": PUNE}, {"$set": {"shopify_location_id": LOC_PUN}})
    _stock(db, "BV-BOK-01", "P-RB", 1)
    _stock(db, PUNE, "P-OA", 1)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 1)])
    world["shop"].fo(FO_2, LOC_PUN, lines=[(9001, 1)])

    res, order = _book(world, _order(53002, lines=(("RB-1234", 1), ("OA-5", 1))))

    assert _sold_at(db, res["order_id"]) == sorted(["BV-BOK-01", PUNE])
    assert [p["code"] for p in order["fulfillment_route"]["problems"]] == ["SPLIT_SELLERS"]
    assert order["fulfillment_hold"] is True
    assert "different GSTINs" in order["stock_hold_reason"]
    assert world["tasks"].refs("online_route:") == [f"online_route:SPLIT_SELLERS:{res['order_id']}"]


def test_a_successful_move_lifts_only_its_own_pending_move_hold(world):
    """P2 (hollow before): Ranchi has no GSTIN, so its seller hold owns the
    order's hold reason. The move to Ranchi succeeds -- and must NOT release
    the seller hold with it."""
    db = world["db"]
    db.stores.update_one({"store_id": "BV-RAN-01"}, {"$set": {"gstin": ""}})
    _stock(db, "BV-RAN-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK)

    _res, order = _book(world, _order(53003))

    route = order["fulfillment_route"]
    assert world["shop"].moves() == [{"id": FO_1, "newLocationId": LOC_RAN}]
    assert route["moves"][0]["status"] == "MOVED" and route["hold_reason"] is None
    assert [p["code"] for p in route["problems"]] == ["SHOP_GSTIN_MISSING"]
    assert order["fulfillment_hold"] is True
    assert order["stock_hold_reason"] == route["problems"][0]["message"]


def test_no_move_into_a_shop_whose_claim_came_up_short(world, monkeypatch):
    """P2 race (probe H): routing counted Ranchi's one unit, a walk-in sale took
    it before the claim. The order is held as a stock miss, and the move is
    SKIPPED -- never sent INTO a short shop -- so the paid order with zero
    units claimed stays held."""
    db = world["db"]
    _stock(db, "BV-RAN-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK)
    real_route = route_mod.route_order

    def route_then_a_walk_in_sale(db_, items, routing):
        out = real_route(db_, items, routing)
        db.stock_units.update_many({"store_id": "BV-RAN-01"}, {"$set": {"status": "SOLD"}})
        return out

    monkeypatch.setattr(route_mod, "route_order", route_then_a_walk_in_sale)

    res, order = _book(world, _order(53004))

    assert _sold_at(db, res["order_id"]) == []
    assert world["shop"].moves() == []
    assert order["fulfillment_route"]["moves"][0]["status"] == "SKIPPED"
    assert order["fulfillment_hold"] is True
    assert "Stock could not be claimed" in order["stock_hold_reason"]


@pytest.mark.parametrize("cleared_mid_flight", [True, False])
def test_the_move_lift_reads_the_rx_hold_as_stored_after_the_move(world, monkeypatch, cleared_mid_flight):
    """An admin clears the Rx hold (clear-rx-hold) while fulfillmentOrderMove is
    on the wire: the lift must not bring the cleared hold back from its stale
    read. Still Rx-pending -> stays held."""
    db = world["db"]
    oid, real = _book_with_a_planned_move(world, monkeypatch, 53005)
    db.orders.update_one({"order_id": oid}, {"$set": {"rx_pending": True}})
    graphql = world["shop"].graphql

    async def admin_clears_rx_during_the_move(db_, query, variables):
        if "imsFulfillmentOrderMove" in query and cleared_mid_flight:
            db.orders.update_one({"order_id": oid}, {"$set": {
                "rx_pending": False, "fulfillment_hold": False, "rx_hold_cleared": True}})
        return await graphql(db_, query, variables)

    monkeypatch.setattr(shopify_push, "_graphql", admin_clears_rx_during_the_move)
    asyncio.run(real(db, oid))

    order = db.orders.find_one({"order_id": oid})
    assert order["fulfillment_route"]["moves"][0]["status"] == "MOVED"
    assert "stock_hold_reason" not in order
    assert order["fulfillment_hold"] is (not cleared_mid_flight)


def test_a_planned_move_is_never_sent_for_a_cancelled_order(world, monkeypatch):
    """P3 (probe C): the move a crash left PLANNED is not retried by the
    orders/cancelled delivery -- no move for a CANCELLED order, no MOVE_FAILED
    hold or P1 task on it."""
    db = world["db"]
    oid, _real = _book_with_a_planned_move(world, monkeypatch, 53006)
    world["shop"].move_error = "Fulfillment order is closed"  # what Shopify would say
    cancelled = {**_order(53006), "cancelled_at": "2026-09-28T10:00:00+05:30"}

    asyncio.run(route_mod.map_routed_order(cancelled, db, topic="orders/cancelled"))

    order = db.orders.find_one({"order_id": oid})
    assert order["status"] == "CANCELLED"
    assert world["shop"].moves() == []
    assert order["fulfillment_route"]["moves"][0]["status"] == "SKIPPED"
    assert world["tasks"].refs("online_route:MOVE_FAILED") == []


def test_a_planned_move_is_never_sent_once_a_human_released_the_order(world, monkeypatch):
    """P3: the hold text tells a human to move it in Shopify admin and clear
    the hold. Once they have, the next orders/updated delivery must not send
    the stale move (nor re-hold the order when Shopify refuses it)."""
    db = world["db"]
    oid, _real = _book_with_a_planned_move(world, monkeypatch, 53007)
    db.orders.update_one({"order_id": oid}, {"$set": {"rx_pending": False, "fulfillment_hold": False}})
    world["shop"].move_error = "Fulfillment order is already at that location"

    asyncio.run(route_mod.map_routed_order(_order(53007), db, topic="orders/updated"))

    order = db.orders.find_one({"order_id": oid})
    assert world["shop"].moves() == []
    assert order["fulfillment_route"]["moves"][0]["status"] == "SKIPPED"
    assert order["fulfillment_hold"] is False
    assert world["tasks"].refs("online_route:MOVE_FAILED") == []


@pytest.mark.parametrize("booked_ids,now_at,rewrites", [
    ([FO_1], {FO_1: LOC_BOK, FO_2: LOC_BOK}, True),  # FO_AT_OTHER_SHOP: a human moved FO_2 in
    (None, {FO_1: LOC_BOK}, True),  # routing unread at booking: unknown -> rewrite
    ([FO_1], {FO_1: LOC_BOK}, False),  # nothing moved since booking: no extra write
])
def test_the_dispatch_rewrites_stock_after_any_human_move(world, monkeypatch, booked_ids, now_at, rewrites):
    """P3 (probe D): a fulfillment order IMS closes that was not the shop's at
    booking was moved by a human AFTER the booking write-back -- Shopify's
    committed unit left the other shop, whose 'available' is now one phantom
    unit high until the dispatch re-asserts the per-shop numbers."""
    import api.services.online_stock_writeback as wb

    wrote = []
    monkeypatch.setattr(wb, "writeback_after_sale", lambda db, items, store: wrote.append(store))
    order = {"order_id": "o4", "source": "shopify", "shopify_order_id": "53008",
             "store_id": "BV-BOK-01", "items": [{"sku": "OA-5", "quantity": 1}],
             "fulfillment_route": {"fulfillment_order_ids": booked_ids, "moves": [],
                                   "problems": [{"code": "FO_AT_OTHER_SHOP", "message": "x"}]}}

    res = _push(world, order, now_at)

    assert res.ok and _fulfilled(world) == [list(now_at)]
    assert wrote == (["BV-BOK-01"] if rewrites else [])


@pytest.mark.parametrize("mode", ["dark", "unread"])
@pytest.mark.parametrize("holders", [
    [("WO-OLD-01", 1)],  # the only claimable unit sits at a CLOSED shop
    [(PUNE, 3), ("BV-RAN-01", 1)],  # the old guess: the unmapped shop with the most stock
])
def test_no_routing_never_guesses_a_seller_from_stock_counts(world, monkeypatch, mode, holders):
    """P2 + medium: with the gate dark (or the routing read failed) and no
    fallback set, IMS has no routing to follow. It must not pick a seller --
    whose GSTIN issues the tax invoice -- by counting stock (a closed shop, or
    an unmapped one Shopify never assigned): the bucket bills it, LOUDLY."""
    db = world["db"]
    db.stores.insert_one({"store_id": "WO-OLD-01", "store_code": "WO-OLD-01", "store_name": "Old",
                          "store_type": "RETAIL", "is_active": False, "state_code": "27",
                          "gstin": "27CCCCC0000C1Z5"})
    if mode == "dark":
        monkeypatch.setattr(shopify_push, "_live_or_reason", lambda _db: (False, "writes_disabled"))
    else:
        world["shop"].read_error = "status 503"
    for shop, n in holders:
        _stock(db, shop, "P-RB", n)

    res, order = _book(world, _order(53010 + len(holders)))

    route = order["fulfillment_route"]
    assert order["store_id"] == "BV-ONLINE-01" and route["reason"] == "NONE"
    assert "/BV-ONLINE-01/" in order["invoice_number"]
    expected = ["SELLER_UNKNOWN"] if mode == "dark" else ["ROUTING_UNREAD", "SELLER_UNKNOWN"]
    assert [p["code"] for p in route["problems"]] == expected
    assert _sold_at(db, res["order_id"]) == []  # nothing claimed behind the bill's back
    assert order["fulfillment_hold"] is True


@pytest.mark.parametrize("fallback", ["WO-OLD-01", "BV-TYPO-99", "BV-ONLINE-01"])
def test_the_fallback_must_be_an_active_physical_shop(world, monkeypatch, fallback):
    """PLAUSIBLE low + P2: ONLINE_FULFILLMENT_STORE_ID naming a closed shop, a
    typo or the stockless ONLINE store never becomes the seller -- and says so."""
    db = world["db"]
    db.stores.insert_one({"store_id": "WO-OLD-01", "store_code": "WO-OLD-01", "store_name": "Old",
                          "store_type": "RETAIL", "is_active": False, "state_code": "20",
                          "gstin": "20CCCCC0000C1Z5"})
    monkeypatch.setenv("ONLINE_FULFILLMENT_STORE_ID", fallback)
    _stock(db, fallback, "P-RB", 1)
    world["shop"].fo(FO_1, LOC_PUNE_SHOPIFY, name="Pune warehouse")

    res, order = _book(world, _order(53020))

    route = order["fulfillment_route"]
    assert order["store_id"] == "BV-ONLINE-01" and route["reason"] == "NONE"
    assert [p["code"] for p in route["problems"]] == [
        "LOCATION_UNMAPPED", "FALLBACK_INVALID", "SELLER_UNKNOWN"]
    assert fallback in route["problems"][1]["message"]
    assert _sold_at(db, res["order_id"]) == []


def test_the_claim_is_at_the_shipping_shop_never_at_the_fallback(world, monkeypatch):
    """Medium (hollow before): the fallback names Pune, Shopify assigned
    Bokaro, both hold a unit. Bokaro bills it AND gives up the unit Shopify
    committed there; Pune's unit stays on sale."""
    db = world["db"]
    monkeypatch.setenv("ONLINE_FULFILLMENT_STORE_ID", PUNE)
    _stock(db, "BV-BOK-01", "P-RB", 1)
    _stock(db, PUNE, "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK)

    res, order = _book(world, _order(53030))

    assert order["store_id"] == "BV-BOK-01" and "/BV-BOK-01/" in order["invoice_number"]
    assert _sold_at(db, res["order_id"]) == ["BV-BOK-01"]
    assert db.stock_units.find_one({"store_id": PUNE})["status"] == "AVAILABLE"
    assert order["fulfillment_hold"] is False
