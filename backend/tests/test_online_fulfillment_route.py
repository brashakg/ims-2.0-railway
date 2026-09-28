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
  R6 a shipping shop with no GSTIN for its state -> loud
  R7 a failed move -> loud, and the fulfillment order is not the shop's
  R8 dark gate -> zero Shopify calls, documented fallback
  R9 the fulfilment push closes only the shipping shop's fulfillment orders
  R10 every create door (webhook drain, missed-webhook pull) reads the routing
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

    def fo(self, fo_id, location_id, units=1, status="OPEN", name="loc"):
        self.fos.append(
            {
                "id": fo_id,
                "status": status,
                "assignedLocation": {"name": name, "location": {"id": location_id}},
                "lineItems": {"nodes": [{"remainingQuantity": units}]},
            }
        )

    async def graphql(self, db, query, variables):
        self.calls.append((query, dict(variables)))
        if "imsOrderRouting" in query:
            if self.read_error:
                raise ValueError(self.read_error)
            return {"data": {"order": {"id": variables["id"], "fulfillmentOrders": {"nodes": self.fos}}}}
        if "imsFulfillmentOrderMove" in query:
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
def test_shop_without_a_gstin_for_its_state_is_loud(world, gstin, state):
    db = world["db"]
    db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": gstin, "state_code": state}})
    _stock(db, "BV-BOK-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK)

    res, order = _book(world, _order(51007))

    assert order["store_id"] == "BV-BOK-01"  # the paid order still books
    assert [p["code"] for p in order["fulfillment_route"]["problems"]] == ["SHOP_GSTIN_MISSING"]
    assert world["tasks"].refs("online_route:") == [f"online_route:SHOP_GSTIN_MISSING:{res['order_id']}"]


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
    res = _push(world, {**base, **routed, "store_id": "BV-BOK-01"}, split)
    assert res.ok and _fulfilled(world) == [[FO_1]]

    # Nothing at the shipping shop's location: loud, nothing closed.
    res = _push(world, {**base, **routed, "store_id": "BV-BOK-01"}, {FO_2: LOC_RAN})
    assert not res.ok and _fulfilled(world) == []
    assert "another shop" in res.error

    # The fallback shop has no location: the FOs route_order recorded.
    res = _push(
        world,
        {**base, "store_id": PUNE, "fulfillment_route": {"fulfillment_order_ids": [FO_2]}},
        {FO_1: LOC_PUNE_SHOPIFY, FO_2: LOC_PUNE_SHOPIFY},
    )
    assert res.ok and _fulfilled(world) == [[FO_2]]

    # A pre-PR-5 order (routing never read) keeps the legacy all-open push.
    res = _push(world, {**base, "store_id": "BV-ONLINE-01"}, split)
    assert res.ok and _fulfilled(world) == [[FO_1, FO_2]]


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
