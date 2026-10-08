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
  R13 (money panel, round 4) a duplicate delivery mid-claim neither skips the
     move nor writes stock back before the claim; a short split leg is tasked
     at that shop; every split-leg shop passes the seller's own-state GSTIN
     rule, and the invoice door, GSTR-1, the challan and the e-invoice refuse
     through the ONE seller check the booking held on; no shop named ->
     nothing claimed, held; a retried failed move tasks only MOVE_FAILED
  R14 (money panel, round 5) Shopify's split is followed leg by leg: a leg
     holding its units is claimed at its own shop, only a SHORT leg moves (to
     a shop already in the order first) or fails loud at that shop; a line IMS
     does not stock never moves anything; the fulfillment order follows a
     claim that came up short; no shop named is no oversell and the stock
     miss never overwrites the seller hold; a split return restocks at the
     leg shop; the seller check gates the hold release, the dispatch and the
     Shopify fulfilment; GSTR-1's credit notes, GSTR-3B and both Tally
     exports skip what GSTR-1 refuses to file; Re-map re-routes a
     seller-held order (re-claims, re-bills at the shop that ships it)
  R15 (money panel, round 7) Re-map is a human replay, not a delivery (the
     stored webhook id no longer turns it into a no-op 'replayed'); it
     refuses an order Shopify fulfilled or closed, one whose fresh route
     names no shop, one whose booking is still claiming or whose move is on
     the wire -- touching nothing; it is the door out of a failed move and
     never re-bills an order that may be invoiced; lifting a seller hold
     (Re-map or clear-hold) re-splits the GST so the invoice and every return
     file one tax head, on the shop state the seller check reads; a re-issued
     invoice is filed in the month it is dated; a move (whole order or a
     split leg) prefers a shop that passes the seller check; R11's
     never-into-a-short-shop is pinned on the whole-order path; the GST
     summary, reconciliation and cross-check leave out what the returns do;
     a split leg's GSTIN hold is worded for the leg
  R16 (money panel, round 8) an unmapped location's problem names the shop
     that really ships; Re-map counts the order's own units without giving
     them back, keeps every unit its new route still wants (never swapped),
     and a refused Re-map leaves every unit the order's -- the same ones;
     Re-map re-raises no task a human closed; a seller hold still standing
     keeps the invoice, the challan and every return off even once its cause
     is fixed; a released seller hold is dated and filed in its release
     month (clear-hold and Re-map, same shop too); a failed split-leg move
     names the leg's shop, and a human's later move of it rewrites stock at
     dispatch
  R17 (money panel, round 10) Re-map takes its lease first, checks the order
     again after the routing read and writes only on the order it checked
     (two presses, a cancel mid-read); THE ROOT RULE -- an issued invoice
     (printed, in a filed or fileable GSTR-1 month, or with an IRN) is never
     re-billed, re-numbered, re-dated or put on a seller hold, at Re-map or
     clear-hold; a same-shop release in a new financial year gets a fresh
     serial; on Re-map nothing moves into a shop failing the seller check;
     Re-map refuses while a refund waits for the accountant; a retried move
     leaves its task as it was; a return on an order no shop shipped mints
     nothing; the sync-health tile gives route_order's fallback answer
  R18 (money panel, round 11) a cancel landing mid-Re-map (another worker,
     before or after its write) never leaves a unit Re-map marked SOLD on the
     cancelled order; Re-map resends a failed move to the shop holding the
     order's units (a short claim stays there); no order or leg ever moves
     into a shop failing the seller check, at booking either; clear-hold
     refuses a seller hold a refund or return stands on, and writes only on
     the order it read; the invoice door stamps only the invoice it prints;
     an e-invoice request counts as issued from before the IRP call and is
     never sent for a superseded number; every field of the Re-map
     compare-and-set is pinned; a still-short Re-map leaves its stock-miss
     task open; a retried move refused in new words is not a new task
  R19 (money panel, round 13) THE SIMPLIFIED ROOT RULE (owner 2026-10-01)
     replaces R15-R18's re-billing: Re-map and clear-hold NEVER change an
     order's invoice number, invoice date, seller shop or tax heads -- a
     fresh route to another shop, one failing the seller check, or a fix
     that changes the shop's GST split is refused (a credit note and a new
     booking); a released hold keeps its booked number and date, in any
     month or financial year. Re-map refuses an IMS line Shopify shows
     fulfilled, refunded or closed (a line IMS claims nothing for never
     blocks); needs no webhook payload; carries on a crashed Re-map's
     unsettled claim, offered on the list and never dispatched meanwhile;
     offers a move back only to a billing shop with a Shopify location;
     files a still-short claim's task at the shop
     short now; keeps the tasked problems on the route for every sender of
     its move; keeps an Rx-pending order held
  R20 (money panel, round 14) a fulfillment order carrying the IMS item
     moves even when a sibling carrying none stays (named, held; the move
     lifts only its own hold); nothing moves into a fallback failing the
     seller check while a clean shop holds the order; every Shopify stock
     write-back of the move and Re-map paths is pinned (failed move, skipped
     move, Re-map without a move, Re-map undone by a cancel); every door
     reads the booking's seller verdict, never re-judging an order it passed
     on today's shop records; a Re-map that died after settling its claim
     is finished by the next press
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
FO_3 = "gid://shopify/FulfillmentOrder/7003"

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

    def complete_task(self, task_id, notes=""):
        for t in self.created:
            if t["task_id"] == task_id:
                t.update(status="COMPLETED", completion_notes=notes)
        return True

    def open_refs(self, prefix):
        return [t["source_ref"] for t in self.created
                if t["source_ref"].startswith(prefix) and t["status"] == "OPEN"]

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
    """Book through THE door as a real delivery does -- with its webhook id,
    which ingest records in the 30-day dedupe log."""
    res = asyncio.run(route_mod.map_routed_order(
        payload, world["db"], webhook_id=f"WH-{payload['id']}", topic="orders/create"))
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
    routed = {"fulfillment_route": {"store_id": "BV-BOK-01", "fulfillment_order_ids": []}}

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
    pune = {**base, "store_id": PUNE, "fulfillment_route": {"store_id": PUNE, "fulfillment_order_ids": [FO_1]}}
    res = _push(world, pune, {FO_1: LOC_PUNE_SHOPIFY})
    assert res.ok and _fulfilled(world) == [[FO_1]]
    # ... never one at a MAPPED shop's location (P4: Bokaro would ship it again).
    res = _push(world, pune, {FO_1: LOC_PUNE_SHOPIFY, FO_2: LOC_BOK})
    assert not res.ok and _fulfilled(world) == [[FO_1]]

    # A pre-PR-5 order (no fulfillment_route at all) keeps the legacy all-open push.
    res = _push(world, {**base, "store_id": "BV-ONLINE-01"}, split)
    assert res.ok and _fulfilled(world) == [[FO_1, FO_2]]


@pytest.mark.parametrize("booked_route", [
    {"store_id": "BV-RAN-01", "reason": "MOVED", "fulfillment_order_ids": None,
     "problems": [{"code": "ROUTING_UNREAD", "message": "status 503"}]},  # read failed
    {"store_id": "BV-RAN-01", "reason": "FALLBACK", "fulfillment_order_ids": None,
     "problems": []},  # dark at booking
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
             "fulfillment_route": {"store_id": "BV-RAN-01",
                                   "moves": [{"fulfillment_order_id": FO_1, "status": "FAILED"}]}}

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
    """P1 (reworked, panel round 5): Shopify split RB->Bokaro, OA->Ranchi;
    Bokaro holds RB, but Ranchi cannot ship its own part (it holds RB, not OA)
    and no shop holds OA. IMS never moves Ranchi's FO INTO Bokaro (which
    cannot ship OA). Bokaro's leg is untouched and claimed; Ranchi's leg fails
    loud AT Ranchi -- never claimed at Bokaro, the billing shop."""
    db = world["db"]
    _stock(db, "BV-BOK-01", "P-RB", 1)
    _stock(db, "BV-RAN-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 1)])
    world["shop"].fo(FO_2, LOC_RAN, lines=[(9001, 1)])

    res, order = _book(world, _order(52001, lines=(("RB-1234", 1), ("OA-5", 1))))

    route = order["fulfillment_route"]
    assert world["shop"].moves() == [] and route["moves"] == []
    assert route["fulfillment_order_ids"] == [FO_1, FO_2]  # each at its own leg shop
    assert route["problems"] == []
    assert _sold_at(db, res["order_id"]) == ["BV-BOK-01"]
    assert db.stock_units.find_one({"store_id": "BV-RAN-01"})["status"] == "AVAILABLE"
    assert order["fulfillment_hold"] is True
    assert db.online_stock_miss.find_one({"order_id": res["order_id"]})["store_id"] == "BV-RAN-01"


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


@pytest.mark.parametrize("move_error", [None, "Location does not stock the item"])
def test_the_stock_write_back_lands_after_the_move(world, monkeypatch, move_error):
    """P6: inventorySetQuantities writes absolute per-location numbers, so it
    must run AFTER the fulfillmentOrderMove -- once, not also before it.
    Round 14: a REFUSED move too (the common MOVE_FAILED cause) -- the
    booking deferred its own write-back to the move, so without this one
    Shopify keeps offering Ranchi's sold unit (an oversell on the site)."""
    import api.services.online_stock_writeback as wb

    db = world["db"]
    shop = world["shop"]
    monkeypatch.setattr(wb, "writeback_after_sale", lambda *a, **k: shop.calls.append(("WRITEBACK", {})))
    _stock(db, "BV-RAN-01", "P-RB", 1)
    shop.fo(FO_1, LOC_BOK)
    shop.move_error = move_error

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
    """P2 (hollow before): Shopify split RB x2 -> Bokaro, OA -> Pune (another
    GSTIN, holds it) and OA -> Ranchi (short), so Ranchi's leg moves to
    Bokaro while the seller check (SPLIT_SELLERS) owns the order's hold
    reason. The move succeeds -- and must NOT release the seller hold with
    it. (Round 11: IMS no longer moves into a shop failing the seller check,
    so the seller hold comes from Shopify's own split here.)"""
    db = world["db"]
    db.stores.update_one({"store_id": PUNE}, {"$set": {"shopify_location_id": LOC_PUN}})
    _stock(db, "BV-BOK-01", "P-RB", 2)
    _stock(db, "BV-BOK-01", "P-OA", 1)
    _stock(db, PUNE, "P-OA", 1)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 2)])
    world["shop"].fo(FO_2, LOC_PUN, lines=[(9001, 1)])
    world["shop"].fo(FO_3, LOC_RAN, lines=[(9001, 1)])

    _res, order = _book(world, _order(53003, lines=(("RB-1234", 2), ("OA-5", 2))))

    route = order["fulfillment_route"]
    assert world["shop"].moves() == [{"id": FO_3, "newLocationId": LOC_BOK}]
    assert route["moves"][0]["status"] == "MOVED" and route["hold_reason"] is None
    assert [p["code"] for p in route["problems"]] == ["SPLIT_SELLERS"]
    assert order["fulfillment_hold"] is True
    assert order["stock_hold_reason"] == route["problems"][0]["message"]


def test_the_fo_follows_a_claim_that_came_up_short(world, monkeypatch):
    """Panel round 5 (was: the move is SKIPPED): routing counted Ranchi's one
    unit, a walk-in sale took it before the claim. The claim and the invoice
    are at Ranchi, so the fulfillment order is MOVED there too -- left at
    Bokaro, Shopify keeps a unit committed at a shop IMS shows it on sale at
    and the write-back offers it twice. The stock miss holds the order, and
    the move's success does not lift that hold."""
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

    assert order["store_id"] == "BV-RAN-01" and _sold_at(db, res["order_id"]) == []
    assert world["shop"].moves() == [{"id": FO_1, "newLocationId": LOC_RAN}]
    assert order["fulfillment_route"]["moves"][0]["status"] == "MOVED"
    assert order["fulfillment_hold"] is True
    assert "Stock could not be claimed" in order["stock_hold_reason"]
    assert db.online_stock_miss.find_one({"order_id": res["order_id"]})["store_id"] == "BV-RAN-01"


def test_a_skipped_short_move_never_strands_the_fo_at_the_assigned_shop(world, monkeypatch):
    """Panel round 5, probe: Bokaro assigned holds RB, not OA; Ranchi holds
    both -> MOVED. A walk-in takes Ranchi's OA before the claim. Ranchi's RB
    is SOLD for this order -- so the fulfillment order MUST reach Ranchi, or
    Bokaro's RB stays committed on Shopify while IMS shows it on sale."""
    db = world["db"]
    _stock(db, "BV-BOK-01", "P-RB", 1)
    _stock(db, "BV-RAN-01", "P-RB", 1)
    _stock(db, "BV-RAN-01", "P-OA", 1)
    world["shop"].fo(FO_1, LOC_BOK, units=2)
    real_route = route_mod.route_order

    def route_then_a_walk_in_sale(db_, items, routing):
        out = real_route(db_, items, routing)
        db.stock_units.update_many({"store_id": "BV-RAN-01", "product_id": "P-OA"},
                                   {"$set": {"status": "SOLD"}})
        return out

    monkeypatch.setattr(route_mod, "route_order", route_then_a_walk_in_sale)

    res, order = _book(world, _order(53014, lines=(("RB-1234", 1), ("OA-5", 1))))

    assert _sold_at(db, res["order_id"]) == ["BV-RAN-01"]
    assert world["shop"].moves() == [{"id": FO_1, "newLocationId": LOC_RAN}]
    assert order["fulfillment_route"]["fulfillment_order_ids"] == [FO_1]
    assert db.stock_units.find_one({"store_id": "BV-BOK-01"})["status"] == "AVAILABLE"
    assert order["fulfillment_hold"] is True


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
    the stale move (nor re-hold the order when Shopify refuses it). Round
    14: the booking deferred its stock write-back to this move, so the skip
    writes it back -- once."""
    import api.services.online_stock_writeback as wb

    db = world["db"]
    oid, _real = _book_with_a_planned_move(world, monkeypatch, 53007)
    db.orders.update_one({"order_id": oid}, {"$set": {"rx_pending": False, "fulfillment_hold": False}})
    world["shop"].move_error = "Fulfillment order is already at that location"
    wrote = []
    monkeypatch.setattr(wb, "writeback_after_sale", lambda db_, items, store: wrote.append(store))

    asyncio.run(route_mod.map_routed_order(_order(53007), db, topic="orders/updated"))

    order = db.orders.find_one({"order_id": oid})
    assert world["shop"].moves() == []
    assert order["fulfillment_route"]["moves"][0]["status"] == "SKIPPED"
    assert order["fulfillment_hold"] is False
    assert world["tasks"].refs("online_route:MOVE_FAILED") == []
    assert wrote == ["BV-RAN-01"]


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
             "fulfillment_route": {"store_id": "BV-BOK-01",
                                   "fulfillment_order_ids": booked_ids, "moves": [],
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


# ---------------------------------------------------------------------------
# R13 -- money panel, round 4
# ---------------------------------------------------------------------------


def _invoice_refusal(world, monkeypatch, order_id):
    """The invoice door's answer for the order: the refusal text, or None."""
    from fastapi import HTTPException
    from api.routers.orders import invoices as inv_mod
    from database.repositories.order_repository import OrderRepository

    monkeypatch.setattr(inv_mod, "get_order_repository", lambda: OrderRepository(world["db"].orders))
    try:
        inv_mod._assemble_invoice(order_id, {"roles": ["SUPERADMIN"]})
    except HTTPException as exc:
        assert exc.status_code == 400
        return exc.detail
    return None


def _gstr1(world, monkeypatch, order, store_id):
    from api.routers.reports import gstr1 as gstr1_mod
    from api.utils.ist import ist_date_str

    monkeypatch.setattr(gstr1_mod, "_get_raw_db", lambda: world["db"])
    return gstr1_mod._compute_gstr1(ist_date_str(order["created_at"])[:7], store_id)


def _challan(world, monkeypatch, order_id):
    """The order's delivery challan: its HTML, or the refusal text."""
    from fastapi import HTTPException
    from api.routers import print_documents as pd
    from database.repositories.order_repository import OrderRepository

    db = world["db"]
    monkeypatch.setattr(pd, "get_order_repository", lambda: OrderRepository(db.orders))
    monkeypatch.setattr(pd, "get_customer_repository", lambda: None)
    monkeypatch.setattr(pd, "load_store", lambda sid: db.stores.find_one({"store_id": sid}, {"_id": 0}) or {})
    monkeypatch.setattr(pd, "load_entity_for_store", lambda s: {"legal_name": "BV Retail", "gstins": [
        {"gstin": "20AAAAA0000A1Z5", "state_code": "20", "is_primary": True}]})
    monkeypatch.setattr(pd, "load_overrides", lambda *a: None)
    try:
        page = asyncio.run(pd.delivery_challan_for_order(order_id, current_user={"roles": ["SUPERADMIN"]}))
    except HTTPException as exc:
        assert exc.status_code == 400
        return exc.detail
    return page.body.decode()


def test_a_duplicate_delivery_mid_claim_keeps_the_move_and_writes_stock_after_the_claim(world, monkeypatch):
    """P1: orders/paid lands on another worker between the creator's insert
    and its claim. It must not SKIP the planned move as 'short-claimed' (the
    claim had not happened yet) nor write Shopify's stock back before the
    claim; the creator sends the move, and ONE write-back lands after it."""
    import threading

    import api.services.online_stock_writeback as wb
    from api.services import shopify_ingest

    db = world["db"]
    _stock(db, "BV-RAN-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK)
    wrote = []  # Ranchi's AVAILABLE units at each Shopify stock write-back
    monkeypatch.setattr(wb, "writeback_after_sale", lambda *a, **k: wrote.append(
        db.stock_units.count_documents({"store_id": "BV-RAN-01", "status": "AVAILABLE"})))
    real_claim = shopify_ingest._claim_units_at
    loser = []

    def claim_with_a_duplicate_racing(db_, order_id, lines, store_id):
        if not loser:
            t = threading.Thread(target=lambda: loser.append(asyncio.run(
                route_mod.map_routed_order(_order(54001), db, topic="orders/paid"))))
            t.start()
            t.join()
        return real_claim(db_, order_id, lines, store_id)

    monkeypatch.setattr(shopify_ingest, "_claim_units_at", claim_with_a_duplicate_racing)

    res, order = _book(world, _order(54001))

    assert loser[0]["status"] == "duplicate"
    assert _sold_at(db, res["order_id"]) == ["BV-RAN-01"]
    assert world["shop"].moves() == [{"id": FO_1, "newLocationId": LOC_RAN}]
    assert order["fulfillment_route"]["moves"][0]["status"] == "MOVED"
    assert order["fulfillment_hold"] is False
    assert wrote == [0]  # once, AFTER the claim sold Ranchi's unit


def test_a_short_split_leg_is_tasked_and_named_at_the_short_shop(world, monkeypatch):
    """P2: Shopify split RB->Bokaro, OA->Ranchi; a walk-in sale takes Ranchi's
    OA between routing and the claim. The stock miss and its P1 task land at
    RANCHI (the short leg), naming it -- not at Bokaro, the billing shop."""
    db = world["db"]
    _stock(db, "BV-BOK-01", "P-RB", 1)
    _stock(db, "BV-RAN-01", "P-OA", 1)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 1)])
    world["shop"].fo(FO_2, LOC_RAN, lines=[(9001, 1)])
    real_route = route_mod.route_order

    def route_then_a_walk_in_sale(db_, items, routing):
        out = real_route(db_, items, routing)
        db.stock_units.update_many({"store_id": "BV-RAN-01"}, {"$set": {"status": "SOLD"}})
        return out

    monkeypatch.setattr(route_mod, "route_order", route_then_a_walk_in_sale)

    res, order = _book(world, _order(54002, lines=(("RB-1234", 1), ("OA-5", 1))))

    assert order["store_id"] == "BV-BOK-01" and order["fulfillment_route"]["split"]
    miss = db.online_stock_miss.find_one({"order_id": res["order_id"]})
    assert miss["store_id"] == "BV-RAN-01"
    assert miss["detail"]["short_stores"] == ["BV-RAN-01"]
    assert sorted(miss["detail"]["stores_tried"]) == ["BV-BOK-01", "BV-RAN-01"]
    task = next(t for t in world["tasks"].created
                if t["source_ref"] == f"online_stock_miss:{res['order_id']}")
    assert task["store_id"] == "BV-RAN-01"
    assert "Short at: BV-RAN-01." in task["description"]


def test_a_split_leg_shop_without_its_own_states_gstin_holds_the_order(world, monkeypatch):
    """P3: the org module stamped the entity's Jharkhand PRIMARY GSTIN on the
    Maharashtra shop (no MH registration). Shopify splits RB->Bokaro, OA->Pune:
    Pune's leg leaves a Maharashtra premises with no Maharashtra GSTIN, so the
    order gets the seller's own-state problem and hold -- and the invoice door
    refuses it."""
    db = world["db"]
    db.stores.update_one({"store_id": PUNE}, {"$set": {
        "shopify_location_id": LOC_PUN, "gstin": "20AAAAA0000A1Z5"}})
    _stock(db, "BV-BOK-01", "P-RB", 1)
    _stock(db, PUNE, "P-OA", 1)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 1)])
    world["shop"].fo(FO_2, LOC_PUN, lines=[(9001, 1)])

    res, order = _book(world, _order(54003, lines=(("RB-1234", 1), ("OA-5", 1)), buyer_state="27"))

    problems = order["fulfillment_route"]["problems"]
    assert [p["code"] for p in problems] == ["SHOP_GSTIN_MISSING"]
    assert "Pune ships part of this order and is in state 27" in problems[0]["message"]
    assert order["fulfillment_hold"] is True
    assert "Pune ships part of this order and is in state 27" in _invoice_refusal(
        world, monkeypatch, res["order_id"])


def test_the_invoice_door_and_gstr1_refuse_a_split_across_gstins(world, monkeypatch):
    """P4 + P7: a SPLIT_SELLERS order is held because one tax invoice cannot
    cover it -- so the invoice door (JSON and PDF share _assemble_invoice)
    refuses it through the SAME check, and GSTR-1 does not file the Pune unit
    under Bokaro's GSTIN. The leg shop's ship task never claims its GSTIN is
    the invoice's."""
    db = world["db"]
    db.stores.update_one({"store_id": PUNE}, {"$set": {"shopify_location_id": LOC_PUN}})
    _stock(db, "BV-BOK-01", "P-RB", 1)
    _stock(db, PUNE, "P-OA", 1)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 1)])
    world["shop"].fo(FO_2, LOC_PUN, lines=[(9001, 1)])

    res, order = _book(world, _order(54004, lines=(("RB-1234", 1), ("OA-5", 1)), buyer_state="27"))

    assert [p["code"] for p in order["fulfillment_route"]["problems"]] == ["SPLIT_SELLERS"]
    assert "different GSTINs" in _invoice_refusal(world, monkeypatch, res["order_id"])
    filed = _gstr1(world, monkeypatch, order, "BV-BOK-01")
    assert filed["b2b"] == [] and filed["b2cl"] == [] and filed["b2cs"] == []
    assert [i["invoice"] for i in filed["validation"]["issues"] if i["level"] == "error"] == [
        order["invoice_number"]]
    ship = [t for t in world["tasks"].created if t["source_ref"].startswith("online_fallback_ship:")]
    assert ship and not [t for t in ship if "GSTIN" in t["description"]]


def test_a_clean_routed_order_is_invoiced_filed_and_challaned(world, monkeypatch):
    """The other direction: the shared check refuses nothing on a clean order,
    and the challan prints the shop's own GSTIN."""
    db = world["db"]
    _stock(db, "BV-BOK-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK)

    res, order = _book(world, _order(54005))

    assert _invoice_refusal(world, monkeypatch, res["order_id"]) is None
    filed = _gstr1(world, monkeypatch, order, "BV-BOK-01")
    assert not [i for i in filed["validation"]["issues"] if i["level"] == "error"]
    assert len(filed["b2cs"]) == 1
    assert "20AAAAA0000A1Z5" in _challan(world, monkeypatch, res["order_id"])


def test_the_challan_refuses_what_the_booking_held(world, monkeypatch):
    """P5: an online order at a Maharashtra shop whose only GSTIN is another
    state's is held and refused an invoice -- and its delivery challan is
    refused too, never printed under the Jharkhand GSTIN."""
    db = world["db"]
    db.stores.update_one({"store_id": PUNE}, {"$set": {
        "shopify_location_id": LOC_PUN, "gstin": "20AAAAA0000A1Z5"}})
    _stock(db, PUNE, "P-RB", 1)
    world["shop"].fo(FO_1, LOC_PUN)

    res, order = _book(world, _order(54006, buyer_state="27"))

    assert order["store_id"] == PUNE and order["fulfillment_hold"] is True
    assert "registered in state 20" in _challan(world, monkeypatch, res["order_id"])


@pytest.mark.parametrize("bucket_env", [None, "BV-RAN-01"])
def test_no_shop_named_claims_nothing_and_is_held_whatever_the_bucket(world, monkeypatch, bucket_env):
    """P8: dark gate, no fallback. The bill store comes from the mapper's
    bucket picker, which can name a PHYSICAL shop (step 5: the first active
    store; or ONLINE_STORE_ID). That shop must not claim, the stock miss is no
    shop's, and the order is HELD on SELLER_UNKNOWN -- the invoice door and
    GSTR-1 refuse it."""
    db = world["db"]
    monkeypatch.setattr(shopify_push, "_live_or_reason", lambda _db: (False, "writes_disabled"))
    if bucket_env:
        monkeypatch.setenv("ONLINE_STORE_ID", bucket_env)
    else:
        monkeypatch.delenv("ONLINE_STORE_ID", raising=False)
    _stock(db, "BV-BOK-01", "P-RB", 1)
    _stock(db, "BV-RAN-01", "P-RB", 1)

    res, order = _book(world, _order(54010 + (1 if bucket_env else 0)))

    route = order["fulfillment_route"]
    assert route["reason"] == "NONE" and order["store_id"] == (bucket_env or "BV-BOK-01")
    assert _sold_at(db, res["order_id"]) == []
    assert order["fulfillment_hold"] is True
    assert [p["code"] for p in route["problems"]] == ["SELLER_UNKNOWN"]
    assert "could not name the shop" in _invoice_refusal(world, monkeypatch, res["order_id"])
    # Panel round 5: no false oversell -- nothing was ever to be claimed, and
    # the SELLER_UNKNOWN hold keeps its own reason (no 'resolve stock, then
    # clear the hold' invitation, no stock-miss P1 task, no sync-health count).
    assert db.online_stock_miss.count_documents({"order_id": res["order_id"]}) == 0
    assert order["stock_hold_reason"] == route["problems"][0]["message"]
    assert world["tasks"].refs("online_stock_miss:") == []
    filed = _gstr1(world, monkeypatch, order, order["store_id"])
    assert filed["b2cs"] == []
    assert [i["invoice"] for i in filed["validation"]["issues"] if i["level"] == "error"] == [
        order["invoice_number"]]


def test_a_retried_failed_move_raises_only_its_new_task(world, monkeypatch):
    """P9: a crash left a move PLANNED; the human closed the booking-time
    LOCATION_UNMAPPED task. The next delivery retries the move and Shopify
    refuses it: only MOVE_FAILED is new -- the closed task is not re-raised."""
    db = world["db"]
    monkeypatch.setenv("ONLINE_FULFILLMENT_STORE_ID", "BV-BOK-01")  # a MAPPED fallback
    _stock(db, "BV-BOK-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_PUNE_SHOPIFY, name="Pune warehouse")
    real = route_mod.move_fulfillment_orders
    monkeypatch.setattr(route_mod, "move_fulfillment_orders", _died)
    res, order = _book(world, _order(54020))
    assert order["fulfillment_route"]["moves"][0]["status"] == "PLANNED"
    for t in world["tasks"].created:
        t["status"] = "COMPLETED"  # the human handled the booking-time task
    world["shop"].move_error = "Fulfillment order cannot be moved"
    monkeypatch.setattr(route_mod, "move_fulfillment_orders", real)

    asyncio.run(route_mod.map_routed_order(_order(54020), db, topic="orders/updated"))

    assert world["tasks"].refs("online_route:") == [
        f"online_route:LOCATION_UNMAPPED:{res['order_id']}",
        f"online_route:MOVE_FAILED:{res['order_id']}",
    ]


def test_no_shop_named_holds_even_when_nothing_is_ours_to_claim(world, monkeypatch):
    """P8: the SELLER_UNKNOWN hold is its own -- an order whose lines are not
    IMS stock records no stock miss, and is still held under that reason."""
    monkeypatch.setattr(shopify_push, "_live_or_reason", lambda _db: (False, "writes_disabled"))

    _res, order = _book(world, _order(54012, lines=(("NOT-IN-IMS", 1),)))

    assert order["fulfillment_route"]["reason"] == "NONE"
    assert order["fulfillment_hold"] is True
    assert order["stock_hold_reason"] == order["fulfillment_route"]["problems"][0]["message"]
    assert world["db"].online_stock_miss.count_documents({}) == 0


# ---------------------------------------------------------------------------
# R14 -- money panel, round 5
# ---------------------------------------------------------------------------


def _gstr3b(world, monkeypatch, order, store_id):
    from api.routers.reports import gstr3b as g3
    from api.utils.ist import ist_date_str

    monkeypatch.setattr(g3, "_get_raw_db", lambda: world["db"])
    return g3._compute_gstr3b(ist_date_str(order["created_at"])[:7], store_id)


def _tally_jv(world, monkeypatch, store_id):
    from api.routers.finance import tally

    monkeypatch.setattr(tally, "_get_db", lambda: world["db"])
    res = asyncio.run(tally.get_tally_sales_jv(
        from_date=None, to_date=None, store_id=store_id, entity_id=None,
        current_user={"user_id": "u1", "roles": ["SUPERADMIN"]}))
    return res.body.decode()


def _remap(world, monkeypatch, payload, on_file=True):
    """THE Re-map door (POST /remap/{id}). ``on_file``: the order's webhook
    payload is still in the inbox (it keeps one for 30 days at most)."""
    from api.routers import online_store_orders as oso

    monkeypatch.setattr(oso, "_get_db", lambda: world["db"])
    # The stored inbox row carries the delivery's own webhook id (the one
    # _book recorded), exactly as the loader returns it.
    monkeypatch.setattr(oso, "_load_last_shopify_payload",
                        lambda _db, _sid: (payload, f"WH-{payload['id']}", "orders/create")
                        if on_file else (None, None, None))
    monkeypatch.setattr(oso, "_write_remap_audit", lambda *a, **k: None)
    return asyncio.run(oso.remap_online_order(
        str(payload["id"]), current_user={"user_id": "u1", "roles": ["ADMIN"]}))


def _clear_hold(world, monkeypatch, order_id):
    """THE hold release (POST /{id}/clear-rx-hold): its answer, or the HTTPException."""
    from fastapi import HTTPException
    from api.routers import online_store_orders as oso

    monkeypatch.setattr(oso, "_get_db", lambda: world["db"])
    monkeypatch.setattr(oso, "_write_rx_hold_audit", lambda *a, **k: None)
    try:
        return asyncio.run(oso.clear_rx_hold(
            order_id, None, current_user={"user_id": "u1", "roles": ["ADMIN"]}))
    except HTTPException as exc:
        return exc


def test_a_split_leg_holding_its_units_is_claimed_where_shopify_committed_them(world):
    """[MEDIUM] Shopify split RB x2 -> Bokaro (largest FO), OA -> Ranchi.
    Ranchi holds its OA; Bokaro holds no RB but does hold an OA. Only Bokaro's
    leg is short (Q4): Ranchi's OA -- the unit Shopify committed -- is SOLD,
    Bokaro's OA stays on sale, and the miss is named at Bokaro."""
    db = world["db"]
    _stock(db, "BV-BOK-01", "P-OA", 1)
    _stock(db, "BV-RAN-01", "P-OA", 1)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 2)])
    world["shop"].fo(FO_2, LOC_RAN, lines=[(9001, 1)])

    res, order = _book(world, _order(56001, lines=(("RB-1234", 2), ("OA-5", 1))))

    route = order["fulfillment_route"]
    assert order["store_id"] == "BV-BOK-01" and route["reason"] == "ASSIGNED" and route["split"]
    sold = [(u["store_id"], u["product_id"]) for u in db.stock_units.find({"order_id": res["order_id"]})]
    assert sold == [("BV-RAN-01", "P-OA")]
    assert db.stock_units.find_one({"store_id": "BV-BOK-01"})["status"] == "AVAILABLE"
    assert world["shop"].moves() == [] and route["problems"] == []
    miss = db.online_stock_miss.find_one({"order_id": res["order_id"]})
    assert miss["store_id"] == "BV-BOK-01" and miss["detail"]["short_stores"] == ["BV-BOK-01"]
    assert order["fulfillment_hold"] is True


def test_a_short_split_leg_is_named_at_its_own_shop_at_booking_too(world):
    """[MEDIUM] second input: the same split, Bokaro holds RB x2, Ranchi has
    no OA at booking. The miss and its P1 task are Ranchi's -- the same shop
    the race version names (test_a_short_split_leg_is_tasked_...)."""
    db = world["db"]
    _stock(db, "BV-BOK-01", "P-RB", 2)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 2)])
    world["shop"].fo(FO_2, LOC_RAN, lines=[(9001, 1)])

    res, order = _book(world, _order(56002, lines=(("RB-1234", 2), ("OA-5", 1))))

    assert _sold_at(db, res["order_id"]) == ["BV-BOK-01", "BV-BOK-01"]
    assert order["fulfillment_route"]["problems"] == [] and world["shop"].moves() == []
    miss = db.online_stock_miss.find_one({"order_id": res["order_id"]})
    assert miss["store_id"] == "BV-RAN-01"
    task = next(t for t in world["tasks"].created
                if t["source_ref"] == f"online_stock_miss:{res['order_id']}")
    assert task["store_id"] == "BV-RAN-01" and "Short at: BV-RAN-01." in task["description"]


def test_only_the_short_leg_moves_and_to_a_shop_already_in_the_order(world):
    """Q4 + Q2: Ranchi cannot ship its OA leg; Bokaro (already in the order)
    and Pune (more stock) both hold an OA. Ranchi's fulfillment order alone
    moves -- to Bokaro, so the order ships from ONE location -- and the move
    lifts its own hold."""
    db = world["db"]
    db.stores.update_one({"store_id": PUNE}, {"$set": {"shopify_location_id": LOC_PUN}})
    _stock(db, "BV-BOK-01", "P-RB", 1)
    _stock(db, "BV-BOK-01", "P-OA", 1)
    _stock(db, PUNE, "P-OA", 3)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 1)])
    world["shop"].fo(FO_2, LOC_RAN, lines=[(9001, 1)])

    res, order = _book(world, _order(56003, lines=(("RB-1234", 1), ("OA-5", 1))))

    assert world["shop"].moves() == [{"id": FO_2, "newLocationId": LOC_BOK}]
    assert _sold_at(db, res["order_id"]) == ["BV-BOK-01", "BV-BOK-01"]
    assert {r["store_id"] for r in order["fulfillment_route"]["split"]} == {"BV-BOK-01"}
    assert order["fulfillment_hold"] is False and "stock_hold_reason" not in order


@pytest.mark.parametrize("move_error", [None, "Location does not stock the item"])
def test_a_split_of_lines_ims_does_not_stock_moves_nothing(world, move_error):
    """[LOW-MEDIUM] covers() was vacuously true for an order with no IMS
    line, so IMS moved a Shopify split nobody is short on (Q4, Q2) -- and,
    with the move refused, held it MOVE_FAILED for goods IMS does not stock.
    Each shop ships its own fulfillment order; the dispatch closes both."""
    world["shop"].move_error = move_error
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 2)])
    world["shop"].fo(FO_2, LOC_RAN, lines=[(9001, 1)])

    _res, order = _book(world, _order(56004 if move_error else 56005,
                                      lines=(("NOT-IN-IMS", 2), ("ALSO-NOT", 1))))

    route = order["fulfillment_route"]
    assert world["shop"].moves() == [] and route["moves"] == []
    assert route["problems"] == [] and order["fulfillment_hold"] is False
    assert route["fulfillment_order_ids"] == [FO_1, FO_2]
    pushed = _push(world, order, {FO_1: LOC_BOK, FO_2: LOC_RAN})
    assert pushed.ok and _fulfilled(world) == [[FO_1, FO_2]]


def test_a_fulfillment_order_carrying_no_ims_item_is_never_moved(world):
    """[LOW-MEDIUM] variant: RB at Bokaro, a line IMS does not stock at
    unmapped Pune. IMS is short on nothing at Pune, so its fulfillment order
    is not moved -- it is named (FO_AT_OTHER_SHOP) and the order held."""
    db = world["db"]
    _stock(db, "BV-BOK-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 1)])
    world["shop"].fo(FO_2, LOC_PUNE_SHOPIFY, lines=[(9001, 1)], name="Pune warehouse")

    _res, order = _book(world, _order(56006, lines=(("RB-1234", 1), ("NOT-IN-IMS", 1))))

    route = order["fulfillment_route"]
    assert world["shop"].moves() == [] and route["moves"] == []
    assert [p["code"] for p in route["problems"]] == ["FO_AT_OTHER_SHOP"]
    assert "no item IMS stocks" in route["problems"][0]["message"]
    assert order["fulfillment_hold"] is True


_NONE_MODES = ["dark", "read_failed", "route_raises", "fallback_typo"]


@pytest.mark.parametrize("mode", _NONE_MODES)
def test_no_shop_named_is_never_a_false_oversell(world, monkeypatch, mode):
    """[MEDIUM] x2: a route that named no shop claims nothing -- it must not
    record an oversell (sync-health tile, 'PAID but stock could not be
    claimed' P1 task) nor replace the SELLER_UNKNOWN hold text with 'resolve
    stock, then clear the hold' -- here with an IMS line whose unit Bokaro
    holds (the round-4 test used a non-IMS line, which hid it)."""
    db = world["db"]
    _stock(db, "BV-BOK-01", "P-RB", 1)
    if mode == "dark":
        monkeypatch.setattr(shopify_push, "_live_or_reason", lambda _db: (False, "writes_disabled"))
    elif mode == "read_failed":
        world["shop"].read_error = "status 503"
    elif mode == "route_raises":
        def boom(*_a, **_k):
            raise RuntimeError("routing exploded")

        monkeypatch.setattr(route_mod, "route_order", boom)
    else:  # dark, and the fallback names no active physical shop
        monkeypatch.setattr(shopify_push, "_live_or_reason", lambda _db: (False, "writes_disabled"))
        monkeypatch.setenv("ONLINE_FULFILLMENT_STORE_ID", "BV-TYPO-99")

    res, order = _book(world, _order(56010 + _NONE_MODES.index(mode)))

    route = order["fulfillment_route"]
    unknown = next(p for p in route["problems"] if p["code"] == "SELLER_UNKNOWN")
    assert route["reason"] == "NONE" and _sold_at(db, res["order_id"]) == []
    assert order["stock_hold_reason"] == unknown["message"]
    assert db.online_stock_miss.count_documents({}) == 0
    assert world["tasks"].refs("online_stock_miss:") == []
    assert world["tasks"].refs("online_route:SELLER_UNKNOWN") == [
        f"online_route:SELLER_UNKNOWN:{res['order_id']}"]


def test_a_stock_miss_never_overwrites_the_seller_hold(world):
    """The seller hold owns the reason: Bokaro has no GSTIN AND no stock. The
    miss is recorded (it is real), the hold text stays the GSTIN one."""
    db = world["db"]
    db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": ""}})
    world["shop"].fo(FO_1, LOC_BOK)

    res, order = _book(world, _order(56020))

    problems = order["fulfillment_route"]["problems"]
    assert [p["code"] for p in problems] == ["SHOP_GSTIN_MISSING"]
    assert db.online_stock_miss.count_documents({"order_id": res["order_id"]}) == 1
    assert order["stock_hold_reason"] == problems[0]["message"]


@pytest.mark.parametrize("race", [False, True])
def test_a_split_return_goes_back_to_the_leg_shop_the_unit_left(world, monkeypatch, race):
    """[LOW] the billing shop claimed nothing, so the breakdown names ONE
    shop -- not the billing one. The return restocks where the unit left.
    Input 1: 2 x a non-IMS line bill at Bokaro, RB ships from Ranchi. Input
    2 (race): RB x2 at Bokaro, OA at Ranchi, a walk-in empties Bokaro."""
    from api.routers import returns

    db = world["db"]
    if race:
        _stock(db, "BV-BOK-01", "P-RB", 2)
        _stock(db, "BV-RAN-01", "P-OA", 1)
        lines, pid = (("RB-1234", 2), ("OA-5", 1)), "P-OA"
        real_route = route_mod.route_order

        def route_then_a_walk_in_sale(db_, items, routing):
            out = real_route(db_, items, routing)
            db.stock_units.update_many({"store_id": "BV-BOK-01"}, {"$set": {"status": "SOLD"}})
            return out

        monkeypatch.setattr(route_mod, "route_order", route_then_a_walk_in_sale)
    else:
        _stock(db, "BV-RAN-01", "P-RB", 1)
        lines, pid = (("NOT-IN-IMS", 2), ("RB-1234", 1)), "P-RB"
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 2)])
    world["shop"].fo(FO_2, LOC_RAN, lines=[(9001, 1)])

    res, order = _book(world, _order(56030 + int(race), lines=lines))

    assert order["store_id"] == "BV-BOK-01" and _sold_at(db, res["order_id"]) == ["BV-RAN-01"]
    hit = returns._resolve_restock_store(order["store_id"], order["order_id"], order=order, product_id=pid)
    assert hit["store_id"] == "BV-RAN-01", hit


def _split_sellers(world, order_id, bokaro_oa=0):
    """Shopify splits RB -> Bokaro (Jharkhand) and OA -> Pune (Maharashtra,
    another GSTIN): booked, each leg claimed, HELD on SPLIT_SELLERS."""
    db = world["db"]
    db.stores.update_one({"store_id": PUNE}, {"$set": {"shopify_location_id": LOC_PUN}})
    _stock(db, "BV-BOK-01", "P-RB", 1)
    _stock(db, "BV-BOK-01", "P-OA", bokaro_oa)
    _stock(db, PUNE, "P-OA", 1)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 1)])
    world["shop"].fo(FO_2, LOC_PUN, lines=[(9001, 1)])
    payload = _order(order_id, lines=(("RB-1234", 1), ("OA-5", 1)))
    res, order = _book(world, payload)
    assert [p["code"] for p in order["fulfillment_route"]["problems"]] == ["SPLIT_SELLERS"]
    return payload, res, order


@pytest.mark.parametrize("via_ledger", [True, False])
def test_the_refund_of_a_sale_held_off_gstr1_is_not_filed_either(world, monkeypatch, via_ledger):
    """P1: GSTR-1 refused the held SPLIT_SELLERS sale -- and must refuse its
    credit note too (ledger pass and in-store returns pass), or it reverses
    output tax on a supply never declared. GSTR-3B nets neither."""
    db = world["db"]
    _payload, _res, order = _split_sellers(world, 56040 + int(via_ledger))
    created = order["created_at"]
    db.returns.insert_one({"return_id": "RET-X1", "order_id": order["order_id"],
                           "order_number": order["order_number"], "store_id": "BV-BOK-01",
                           "status": "COMPLETED", "created_at": created,
                           "gst_breakup": {"tax": 107.0, "taxable": 892.0, "gst_rate": 12}})
    if via_ledger:
        db.credit_note_ledger.insert_one({
            "store_id": "BV-BOK-01", "type": "ISSUED", "ref": "RET-X1", "tax": 107.0,
            "taxable": 892.0, "gross_refund": 999.0, "gst_rate": 12,
            "customer_id": order.get("customer_id"), "created_at": created.isoformat()})

    filed = _gstr1(world, monkeypatch, order, "BV-BOK-01")
    assert filed["cdnr"] == [] and filed["b2cs"] == []
    assert [i for i in filed["validation"]["issues"]
            if "RET-X1" in i["invoice"] and "credit note" in i["issue"]]
    g3 = _gstr3b(world, monkeypatch, order, "BV-BOK-01")
    assert g3["creditNotes"] == {"integratedTax": 0.0, "centralTax": 0.0,
                                 "stateTax": 0.0, "taxableValue": 0.0}
    assert g3["outwardTaxableValue"] == 0.0


def test_gstr3b_and_tally_skip_what_gstr1_refuses_to_file(world, monkeypatch):
    """One GSTIN, one answer: a SELLER_UNKNOWN order billed at the online
    bucket is 'Not filed' in GSTR-1 -- and not an outward supply in GSTR-3B
    or a Tally sales voucher either. A clean order still is (the other way)."""
    db = world["db"]
    monkeypatch.setattr(shopify_push, "_live_or_reason", lambda _db: (False, "writes_disabled"))
    _res, held = _book(world, _order(56050))
    assert held["store_id"] == "BV-ONLINE-01" and held["fulfillment_hold"] is True

    g3 = _gstr3b(world, monkeypatch, held, "BV-ONLINE-01")
    assert g3["outwardTaxableValue"] == 0.0
    assert g3["outwardTaxableSupplies"]["centralTax"] == 0.0
    assert held["order_id"] not in _tally_jv(world, monkeypatch, "BV-ONLINE-01")

    monkeypatch.setattr(shopify_push, "_live_or_reason", lambda _db: (True, None))
    _stock(db, "BV-BOK-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK)
    _res, clean = _book(world, _order(56051))
    assert _gstr3b(world, monkeypatch, clean, "BV-BOK-01")["outwardTaxableValue"] > 0
    assert clean["order_id"] in _tally_jv(world, monkeypatch, "BV-BOK-01")
    # The unattended (NEXUS nightly) Tally export quarantines it the same way.
    from agents.nexus_providers import tally_build_day_voucher_xml_checked

    _xml, _priced, rejected = tally_build_day_voucher_xml_checked(db, [held, clean])
    assert [r["order_id"] for r in rejected] == [held["order_id"]]
    assert "seller (GSTIN) check" in rejected[0]["reason"]


@pytest.mark.parametrize("bucket", ["BV-ONLINE-01", "BV-RAN-01"])
def test_no_door_lets_goods_leave_while_the_seller_check_refuses_the_invoice(world, monkeypatch, bucket):
    """P3 + the dispatch gate: the routing read failed and the bucket may be
    a PHYSICAL shop. The hold release refuses (409), and even with the flags
    cleared behind its back, the dispatch gate (ready / deliver / book
    shipment) refuses and the fulfilment push closes NOTHING on Shopify."""
    from fastapi import HTTPException
    from api.routers.orders import assert_no_active_rx_hold

    db = world["db"]
    monkeypatch.setenv("ONLINE_STORE_ID", bucket)
    world["shop"].read_error = "status 503"
    _stock(db, "BV-RAN-01", "P-RB", 1)
    res, order = _book(world, _order(56060 + (bucket == "BV-RAN-01")))
    assert order["store_id"] == bucket and order["fulfillment_route"]["reason"] == "NONE"

    refused = _clear_hold(world, monkeypatch, res["order_id"])
    assert isinstance(refused, HTTPException) and refused.status_code == 409
    assert "could not name the shop" in refused.detail
    assert db.orders.find_one({"order_id": res["order_id"]})["fulfillment_hold"] is True

    db.orders.update_one({"order_id": res["order_id"]}, {"$set": {"fulfillment_hold": False}})
    order = db.orders.find_one({"order_id": res["order_id"]}, {"_id": 0})
    with pytest.raises(HTTPException) as gate:
        assert_no_active_rx_hold(order)
    assert gate.value.status_code == 400 and "could not name the shop" in gate.value.detail
    pushed = _push(world, order, {FO_1: LOC_RAN})
    assert not pushed.ok and world["shop"].calls == [] and _fulfilled(world) == []
    # The second barrier: even past the seller check, the push closes only
    # the fulfillment orders of the shop THE route named -- never the bucket
    # shop's (order.store_id) for a route that named none.
    monkeypatch.setattr(route_mod, "stored_seller_problem", lambda *_a, **_k: None)
    pushed = _push(world, order, {FO_1: LOC_RAN})
    assert not pushed.ok and _fulfilled(world) == []


def test_a_seller_hold_is_released_once_its_cause_is_fixed_and_named_so(world, monkeypatch):
    """P3: a Maharashtra shop carrying a Jharkhand GSTIN holds the order; the
    release refuses until Organization gives Pune its own GSTIN, then names
    what it released -- a seller (GSTIN) hold, not a 'stock hold'."""
    from fastapi import HTTPException

    db = world["db"]
    db.stores.update_one({"store_id": PUNE}, {"$set": {
        "shopify_location_id": LOC_PUN, "gstin": "20AAAAA0000A1Z5"}})
    _stock(db, PUNE, "P-RB", 1)
    world["shop"].fo(FO_1, LOC_PUN)
    res, _order_doc = _book(world, _order(56070, buyer_state="27"))

    assert isinstance(_clear_hold(world, monkeypatch, res["order_id"]), HTTPException)
    db.stores.update_one({"store_id": PUNE}, {"$set": {"gstin": "27CCCCC0000C1Z5"}})
    out = _clear_hold(world, monkeypatch, res["order_id"])
    assert out["released"] == ["SELLER"] and out["message"].startswith("Seller (GSTIN) hold released")


@pytest.mark.parametrize("on_file", [True, False])
def test_remap_releases_a_split_the_human_moved_to_one_shop(world, monkeypatch, on_file):
    """P2 + the split-hold remedy: the hold text says move the fulfillment
    orders to one GSTIN in Shopify admin, then Re-map. The human moves FO_2
    to Bokaro; Re-map re-reads the routing, gives Pune's OA back, claims
    Bokaro's, and lifts the hold: the invoice door, GSTR-1 and the dispatch
    now accept the order (same invoice: Bokaro still bills it).
    ``on_file=False`` (round 13): the webhook payload has left the inbox --
    Re-map needs only the stored order and a fresh read (it answered 404,
    and the hold had no other door)."""
    from api.routers.orders import assert_no_active_rx_hold
    from api.routers import online_store_orders as oso

    db = world["db"]
    payload, res, order = _split_sellers(world, 56080 + 100 * on_file, bokaro_oa=1)
    assert oso._slim_list_row(dict(order))["remap_hold"] is True
    assert "Re-map" in order["stock_hold_reason"]
    assert len(world["tasks"].open_refs(f"online_fallback_ship:{res['order_id']}")) == 2
    world["shop"].fos[1]["assignedLocation"]["location"]["id"] = LOC_BOK  # the human's move

    out = _remap(world, monkeypatch, payload, on_file=on_file)

    assert out["ok"] and out["result"]["status"] == "rerouted", out
    after = db.orders.find_one({"order_id": res["order_id"]}, {"_id": 0})
    assert _sold_at(db, res["order_id"]) == ["BV-BOK-01", "BV-BOK-01"]
    assert db.stock_units.find_one({"store_id": PUNE})["status"] == "AVAILABLE"
    assert after["fulfillment_hold"] is False and "stock_hold_reason" not in after
    # Pune no longer ships a unit of it: its 'ship from your store' task closes.
    assert world["tasks"].open_refs(f"online_fallback_ship:{res['order_id']}") == [
        f"online_fallback_ship:{res['order_id']}:BV-BOK-01"]
    assert after["invoice_number"] == order["invoice_number"]
    assert _invoice_refusal(world, monkeypatch, res["order_id"]) is None
    assert len(_gstr1(world, monkeypatch, after, "BV-BOK-01")["b2cs"]) == 1
    assert_no_active_rx_hold(after)
    assert oso._slim_list_row(dict(after))["remap_hold"] is False


@pytest.mark.parametrize("on_file", [True, False])
def test_remap_never_changes_a_seller_unknown_orders_seller(world, monkeypatch, on_file):
    """P2 + round 13, THE SIMPLIFIED ROOT RULE (owner 2026-10-01): booked dark
    (SELLER_UNKNOWN, the bucket's invoice number, nothing claimed). Once
    Shopify can be read and has it at Bokaro, Re-map REFUSES -- Bokaro is
    another seller than the bucket that billed it -- and names the way out
    (a credit note and a new booking); it used to re-bill the order at
    Bokaro under a new number. ``on_file=False``: the webhook payload has
    left the inbox -- the door still answers (it was 404 and the hold
    permanent)."""
    db = world["db"]
    payload, res, order = _dark_seller_unknown(world, monkeypatch, 56090 + 100 * on_file)
    assert "credit note" in order["stock_hold_reason"]

    out = _remap(world, monkeypatch, payload, on_file=on_file)

    assert not out["ok"] and out["result"]["status"] == "refused", out
    assert "from BV-BOK-01, not from BV-ONLINE-01" in out["message"], out
    # The online bucket has no Shopify location: no move back is offered.
    assert "credit note" in out["message"] and "Change location" not in out["message"]
    after = db.orders.find_one({"order_id": res["order_id"]}, {"_id": 0})
    for k in ("store_id", "invoice_number", "invoice_date", "created_at", "stock_hold_reason"):
        assert after[k] == order[k], k
    assert after["fulfillment_hold"] is True and "superseded_invoice_number" not in after
    assert _sold_at(db, res["order_id"]) == []


# ---------------------------------------------------------------------------
# R15 -- money panel, round 7
# ---------------------------------------------------------------------------

LOC_DHN = "gid://shopify/Location/104"
LOC_WO = "gid://shopify/Location/105"


def _shop(db, store_id, name, gstin, loc):
    db.stores.insert_one({"store_id": store_id, "store_code": store_id, "store_name": name,
                          "store_type": "RETAIL", "is_active": True, "state_code": "20",
                          "gstin": gstin, "shopify_location_id": loc})


def _gstin_missing_at_bokaro(world, order_id):
    """Shopify assigned the order to Bokaro, which has no GSTIN: booked, its
    unit claimed at Bokaro, HELD on SHOP_GSTIN_MISSING."""
    db = world["db"]
    db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": ""}})
    _stock(db, "BV-BOK-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK)
    payload = _order(order_id)
    res, order = _book(world, payload)
    assert [p["code"] for p in order["fulfillment_route"]["problems"]] == ["SHOP_GSTIN_MISSING"]
    assert order["fulfillment_hold"] is True and _sold_at(db, res["order_id"]) == ["BV-BOK-01"]
    return payload, res, order


def _untouched(world, res, order):
    """Nothing changed -- not even a unit given back and claimed again: the
    route counts the order's own units without releasing them (round 8)."""
    db = world["db"]
    after = db.orders.find_one({"order_id": res["order_id"]}, {"_id": 0})
    assert after["store_id"] == order["store_id"]
    assert after["invoice_number"] == order["invoice_number"]
    assert after["fulfillment_hold"] is True
    assert after["stock_hold_reason"] == order["stock_hold_reason"]
    assert _sold_at(db, res["order_id"]) == ["BV-BOK-01"]
    assert db.stock_units.find_one({"store_id": "BV-RAN-01"})["status"] == "AVAILABLE"
    # Refused BEFORE any unit moved: none was given back and claimed again (a
    # shop holding several would re-claim a shelf unit and free the shipped one).
    assert db.stock_units.count_documents({"released_from_order_id": res["order_id"]}) == 0


@pytest.mark.parametrize("how", ["closed_in_shopify", "fulfilled_per_shopify", "unmapped_no_fallback"])
def test_remap_refuses_an_order_shopify_no_longer_routes_to_a_shop(world, monkeypatch, how):
    """[MEDIUM] Staff fulfilled the held order in Shopify admin (its only
    fulfillment order is CLOSED; or orders/updated said 'fulfilled' and the
    mapper synced it), or it now sits at a location no shop maps and no
    fallback is set. Re-map must refuse and touch nothing: before, it gave
    Bokaro's SHIPPED unit back to the shelf, claimed Ranchi's (the fallback)
    and re-billed from Ranchi's GSTIN -- or, with no fallback, freed the
    unit and claimed nothing."""
    db = world["db"]
    payload, res, order = _gstin_missing_at_bokaro(world, 57001 + ["closed_in_shopify",
                                                                   "fulfilled_per_shopify",
                                                                   "unmapped_no_fallback"].index(how))
    _stock(db, "BV-RAN-01", "P-RB", 1)
    fo = world["shop"].fos[0]
    if how == "closed_in_shopify":
        monkeypatch.setenv("ONLINE_FULFILLMENT_STORE_ID", "BV-RAN-01")
        fo["status"] = "CLOSED"
    elif how == "fulfilled_per_shopify":
        db.orders.update_one({"order_id": res["order_id"]}, {"$set": {"fulfillment_status": "FULFILLED"}})
        order = db.orders.find_one({"order_id": res["order_id"]}, {"_id": 0})
        fo["assignedLocation"]["location"]["id"] = LOC_RAN
    else:  # at a location no shop maps, and no shop to move it to
        monkeypatch.setenv("ONLINE_FULFILLMENT_FALLBACK", "off")
        fo["assignedLocation"]["location"]["id"] = LOC_PUNE_SHOPIFY

    out = _remap(world, monkeypatch, payload)

    assert not out["ok"] and out["result"]["status"] == "refused", out
    assert {"closed_in_shopify": "fulfilled, refunded or closed",
            "fulfilled_per_shopify": "Shopify shows it FULFILLED",
            "unmapped_no_fallback": "names no IMS shop"}[how] in out["message"]
    _untouched(world, res, order)


@pytest.mark.parametrize("state", ["claiming", "move_on_the_wire"])
def test_remap_waits_for_the_bookings_claim_and_its_move(world, monkeypatch, state):
    """[LOW] A Re-map pressed while the creator is still claiming (no
    fulfillment_breakdown yet) claimed the order twice; one pressed while a
    move is on the wire had its route overwritten by the move's write-back.
    Both refuse."""
    db = world["db"]
    payload, res, order = _gstin_missing_at_bokaro(world, 57010 + ["claiming", "move_on_the_wire"].index(state))
    _stock(db, "BV-RAN-01", "P-RB", 1)
    world["shop"].fos[0]["assignedLocation"]["location"]["id"] = LOC_RAN  # a human's move
    oid = res["order_id"]
    if state == "claiming":
        db.orders.update_one({"order_id": oid}, {"$unset": {"fulfillment_breakdown": ""}})
    else:
        db.orders.update_one({"order_id": oid}, {"$set": {"fulfillment_route.moves": [
            {"fulfillment_order_id": FO_1, "to_location_id": LOC_RAN, "status": "SENDING"}]}})

    out = _remap(world, monkeypatch, payload)

    assert not out["ok"] and "press Re-map again in a moment" in out["message"], out
    _untouched(world, res, order)


def _short_held_at_bokaro(world, order_id, qty=1):
    """Shopify assigned Bokaro, which had no GSTIN and no unit: booked short
    (a stock miss tasked at Bokaro) and HELD on SHOP_GSTIN_MISSING. Then
    Organization sets Bokaro's GSTIN and Bokaro restocks: a Re-map claims at
    Bokaro, the shop that billed it."""
    db = world["db"]
    db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": ""}})
    world["shop"].fo(FO_1, LOC_BOK, units=qty)
    payload = _order(order_id, lines=(("RB-1234", qty),))
    res, order = _book(world, payload)
    assert [p["code"] for p in order["fulfillment_route"]["problems"]] == ["SHOP_GSTIN_MISSING"]
    assert order["fulfillment_hold"] is True and _sold_at(db, res["order_id"]) == []
    db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": "20AAAAA0000A1Z5"}})
    _stock(db, "BV-BOK-01", "P-RB", qty)
    return payload, res, order


def _crashed_mid_claim(world, order_id):
    """A Re-map wrote (the hold lifted, the claim unsettled), claimed 1 of 2
    units at Bokaro and died, its lease left behind (stale)."""
    db = world["db"]
    payload, res, _o = _short_held_at_bokaro(world, order_id, qty=2)
    oid = res["order_id"]
    # Its write landed (its fresh route, no seller problem on it), one unit claimed.
    db.stock_units.update_one({"stock_id": "U-BV-BOK-01-P-RB-0"},
                              {"$set": {"status": "SOLD", "order_id": oid}})
    db.orders.update_one({"order_id": oid}, {
        "$set": {"fulfillment_hold": False, "reroute_lease_at": "2000-01-01T00:00:00+00:00",
                 "fulfillment_route.problems": []},
        "$unset": {"stock_hold_reason": "", "fulfillment_breakdown": "", "fulfillment_stores": ""}})
    return payload, oid


def test_a_remap_that_crashed_mid_claim_is_carried_on_by_the_next_press(world, monkeypatch):
    """[LOW] Round 13, item 3: a Re-map wrote (the hold lifted, the claim
    unsettled), claimed 1 of 2 units and died, its lease left behind. The
    next press refused -- 'not held' -- leaving the order releasable with 1
    unit SOLD and the other on sale. The stale lease is taken over before
    that check now: carried on, both units claimed, the claim settled. Until
    then the list offers Re-map for it (the hold is gone, so it did not) and
    no door dispatches it with one unit unclaimed."""
    from fastapi import HTTPException
    from api.routers import online_store_orders as oso
    from api.routers.orders import assert_no_active_rx_hold

    db = world["db"]
    payload, oid = _crashed_mid_claim(world, 60110)
    crashed = db.orders.find_one({"order_id": oid}, {"_id": 0})
    listed = db.orders.find_one({"order_id": oid}, dict(oso._LIST_PROJECTION))  # the list's read
    assert oso._slim_list_row(listed)["remap_hold"] is True
    with pytest.raises(HTTPException, match="a Re-map of it is running or stopped mid-way"):
        assert_no_active_rx_hold(crashed)

    out = _remap(world, monkeypatch, payload)

    assert out["ok"] and out["result"]["status"] == "rerouted", out
    after = db.orders.find_one({"order_id": oid}, {"_id": 0})
    assert _sold_at(db, oid) == ["BV-BOK-01", "BV-BOK-01"]
    assert sum(r["qty"] for r in after["fulfillment_breakdown"]) == 2
    assert after["fulfillment_hold"] is False and "reroute_lease_at" not in after
    assert oso._slim_list_row(dict(after))["remap_hold"] is False
    assert_no_active_rx_hold(after)


@pytest.mark.parametrize("why", ["routing_502", "gate_dark", "seller_check"])
def test_a_refused_takeover_keeps_the_crashed_remaps_lease(world, monkeypatch, why):
    """[LOW-MEDIUM] Round 16, item 1: the press taking over a crashed Re-map's
    stale lease was refused -- Shopify's routing read a 502, the gate dark,
    the seller check -- and its finally deleted the lease, the only mark of
    the unsettled claim: 1 of 2 units SOLD, the hold lifted, the list no
    longer offering Re-map and the order dispatchable with a unit never
    claimed. The lease stays now, STALE: still offered, still blocked, and
    the next press carries on at once. (A cause no press gets past settles
    the claim instead: test_a_takeover_refused_for_good_settles_...)"""
    from fastapi import HTTPException
    from api.routers import online_store_orders as oso
    from api.routers.orders import assert_no_active_rx_hold

    db = world["db"]
    payload, oid = _crashed_mid_claim(world, 60120 + len(why))
    if why == "routing_502":
        world["shop"].read_error = "502 Bad Gateway"
    elif why == "gate_dark":
        monkeypatch.setattr(shopify_push, "_live_or_reason", lambda _db: (False, "writes_disabled"))
    else:
        db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": ""}})

    out = _remap(world, monkeypatch, payload)

    assert not out["ok"] and "another Re-map" not in out["message"], out
    after = db.orders.find_one({"order_id": oid}, {"_id": 0})
    assert after.get("reroute_lease_at") and _sold_at(db, oid) == ["BV-BOK-01"]
    assert oso._slim_list_row(dict(after))["remap_hold"] is True
    with pytest.raises(HTTPException, match="a Re-map of it is running or stopped mid-way"):
        assert_no_active_rx_hold(after)
    # The cause gone, the very next press carries on (the lease left stale).
    world["shop"].read_error = None
    monkeypatch.setattr(shopify_push, "_live_or_reason", lambda _db: (True, None))
    db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": "20AAAAA0000A1Z5"}})

    out = _remap(world, monkeypatch, payload)

    assert out["ok"] and out["result"]["status"] == "rerouted", out
    after = db.orders.find_one({"order_id": oid}, {"_id": 0})
    assert _sold_at(db, oid) == ["BV-BOK-01", "BV-BOK-01"] and "reroute_lease_at" not in after
    assert_no_active_rx_hold(after)


def test_a_remap_stopped_after_its_write_leaves_its_lease_for_the_next_press(world, monkeypatch):
    """[LOW] Round 16, item 1, the same hole from a fresh press: its write
    lifted the hold, then its claim raised. The lease stays (stale), so the
    order stays marked and the next press carries on."""
    from fastapi import HTTPException
    from api.routers import online_store_orders as oso
    from api.routers.orders import assert_no_active_rx_hold
    from api.services import shopify_ingest

    db = world["db"]
    payload, res, _o = _short_held_at_bokaro(world, 60140)
    oid = res["order_id"]
    real = shopify_ingest._claim_online_units

    def down(*_a, **_k):
        raise RuntimeError("stock_units unreachable")

    monkeypatch.setattr(shopify_ingest, "_claim_online_units", down)
    with pytest.raises(RuntimeError):
        asyncio.run(route_mod.reroute_held_order(db, oid))
    after = db.orders.find_one({"order_id": oid}, {"_id": 0})
    assert after["fulfillment_hold"] is False and after.get("reroute_lease_at")
    assert oso._slim_list_row(dict(after))["remap_hold"] is True
    with pytest.raises(HTTPException, match="a Re-map of it is running or stopped mid-way"):
        assert_no_active_rx_hold(after)
    monkeypatch.setattr(shopify_ingest, "_claim_online_units", real)

    out = _remap(world, monkeypatch, payload)

    assert out["ok"] and out["result"]["status"] == "rerouted", out
    assert _sold_at(db, oid) == ["BV-BOK-01"]
    assert "reroute_lease_at" not in db.orders.find_one({"order_id": oid})


@pytest.mark.parametrize("then", ["shopify_takes_it", "bokaro_restocked"])
def test_remap_is_the_door_out_of_a_failed_move(world, monkeypatch, then):
    """[LOW] A failed move left the unit claimed at Ranchi and Shopify's
    fulfillment order at Bokaro, and Re-map answered 'duplicate' and did
    nothing. Now it re-reads the routing and sends the move again (the hold
    lifts once Shopify takes it). Round 13, THE SIMPLIFIED ROOT RULE: Bokaro
    restocked and Shopify still has it there, so its routing would ship it
    from Bokaro -- another seller. Refused, the order untouched (round 12's
    probe re-billed it at Bokaro although the accountant had exported it to
    Tally, its IRN standing under Ranchi's number and GSTIN)."""
    from api.routers import online_store_orders as oso

    db = world["db"]
    _stock(db, "BV-RAN-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK)
    world["shop"].move_error = "Location does not stock the item"
    payload = _order(57020 + (then == "bokaro_restocked"))
    res, order = _book(world, payload)
    assert [p["code"] for p in order["fulfillment_route"]["problems"]] == ["MOVE_FAILED"]
    assert oso._slim_list_row(dict(order))["remap_hold"] is True
    world["shop"].move_error = None
    if then == "bokaro_restocked":
        _stock(db, "BV-BOK-01", "P-RB", 1)
        db.orders.update_one({"order_id": res["order_id"]},
                             {"$set": {"tally_status": "DONE", "exported_to_tally": True}})
    sent = len(world["shop"].moves())

    out = _remap(world, monkeypatch, payload)

    after = db.orders.find_one({"order_id": res["order_id"]}, {"_id": 0})
    for k in ("store_id", "invoice_number", "invoice_date", "created_at"):
        assert after[k] == order[k], k
    assert _sold_at(db, res["order_id"]) == ["BV-RAN-01"]
    if then == "bokaro_restocked":
        assert not out["ok"] and out["result"]["status"] == "refused", out
        assert "from BV-BOK-01, not from BV-RAN-01" in out["message"], out
        # Ranchi has a Shopify location: moving it back there is offered too.
        assert "credit note" in out["message"] and "to BV-RAN-01's location" in out["message"]
        assert after["fulfillment_hold"] is True and after["stock_hold_reason"] == order["stock_hold_reason"]
        assert world["shop"].moves()[sent:] == [] and "superseded_invoice_number" not in after
        return
    assert out["ok"] and out["result"]["status"] == "rerouted", out
    assert world["shop"].moves()[-1] == {"id": FO_1, "newLocationId": LOC_RAN}
    assert after["fulfillment_hold"] is False and "stock_hold_reason" not in after
    assert after["fulfillment_route"]["fulfillment_order_ids"] == [FO_1]
    assert world["tasks"].open_refs("online_route:MOVE_FAILED:") == []


def test_a_short_split_leg_moves_to_a_shop_under_the_sellers_gstin(world):
    """[LOW-MEDIUM] Shopify split RB x2 -> Bokaro, OA -> Ranchi; Ranchi holds
    no OA. BV Dhanbad (Bokaro's GSTIN) holds 1, WizOpt Dhanbad (another
    GSTIN) holds 5. The leg goes to BV Dhanbad -- a clean order -- not to the
    shop with the most stock, which IMS then held as SPLIT_SELLERS."""
    db = world["db"]
    _shop(db, "BV-DHN-01", "BV Dhanbad", "20AAAAA0000A1Z5", LOC_DHN)
    _shop(db, "WO-DHN-01", "WizOpt Dhanbad", "20WWWWW0000W1Z5", LOC_WO)
    _stock(db, "BV-BOK-01", "P-RB", 2)
    _stock(db, "BV-DHN-01", "P-OA", 1)
    _stock(db, "WO-DHN-01", "P-OA", 5)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 2)])
    world["shop"].fo(FO_2, LOC_RAN, lines=[(9001, 1)])

    res, order = _book(world, _order(57030, lines=(("RB-1234", 2), ("OA-5", 1))))

    assert world["shop"].moves() == [{"id": FO_2, "newLocationId": LOC_DHN}]
    assert _sold_at(db, res["order_id"]) == ["BV-BOK-01", "BV-BOK-01", "BV-DHN-01"]
    assert order["fulfillment_route"]["problems"] == [] and order["fulfillment_hold"] is False


def test_a_whole_order_move_prefers_a_shop_that_passes_the_seller_check(world):
    """[LOW-MEDIUM] Bokaro is short; Dhanbad (no GSTIN) holds 5, Ranchi holds
    1. The order moves to Ranchi and ships clean -- not to the most stock,
    where the seller check would hold it."""
    db = world["db"]
    _shop(db, "BV-DHN-01", "BV Dhanbad", "", LOC_DHN)
    _stock(db, "BV-DHN-01", "P-RB", 5)
    _stock(db, "BV-RAN-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK)

    res, order = _book(world, _order(57031))

    assert order["store_id"] == "BV-RAN-01" and order["fulfillment_route"]["problems"] == []
    assert world["shop"].moves() == [{"id": FO_1, "newLocationId": LOC_RAN}]
    assert _sold_at(db, res["order_id"]) == ["BV-RAN-01"]


def test_the_whole_order_rule_never_moves_a_fulfillment_order_into_a_short_shop(world):
    """[LOW, hollow] R11 on the WHOLE-ORDER path (anonymous lines, no split):
    FO_1 at Bokaro (2 units), FO_2 at Ranchi (1); Bokaro holds RB, nobody
    holds OA. No move -- FO_AT_OTHER_SHOP -- never FO_2 into short Bokaro."""
    db = world["db"]
    _stock(db, "BV-BOK-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK, units=2)
    world["shop"].fo(FO_2, LOC_RAN, units=1)

    _res, order = _book(world, _order(57032, lines=(("RB-1234", 1), ("OA-5", 1))))

    route = order["fulfillment_route"]
    assert route["split"] is None  # the whole-order path, not Shopify's split
    assert world["shop"].moves() == [] and route["moves"] == []
    assert "FO_AT_OTHER_SHOP" in [p["code"] for p in route["problems"]]
    assert order["fulfillment_hold"] is True


def _invoice(world, monkeypatch, order_id):
    from api.routers.orders import invoices as inv_mod
    from database.repositories.order_repository import OrderRepository

    monkeypatch.setattr(inv_mod, "get_order_repository", lambda: OrderRepository(world["db"].orders))
    payload, _o, _c = inv_mod._assemble_invoice(order_id, {"roles": ["SUPERADMIN"]})
    return payload


@pytest.mark.parametrize("door", ["remap", "clear_hold"])
def test_a_seller_hold_whose_fix_changes_the_tax_head_is_never_lifted(world, monkeypatch, door):
    """[MEDIUM] Bokaro has neither GSTIN nor state; the buyer is in
    Maharashtra. The booking split CGST+SGST (supplier state unknown) and
    held it. Organization fixes Bokaro (20.., state 20): the shop as it is
    now splits it IGST. Round 13, THE SIMPLIFIED ROOT RULE: no door
    re-splits a booked invoice -- both refuse and name the credit note; the
    order stays held under its booked tax head, so the invoice door and
    every return keep it off (one tax head, never two)."""
    db = world["db"]
    db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": "", "state_code": None}})
    _stock(db, "BV-BOK-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK)
    payload = _order(57040 + (door == "clear_hold"), buyer_state="27")
    res, order = _book(world, payload)
    assert order["fulfillment_hold"] is True and order["interstate"] is False
    db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {
        "gstin": "20AAAAA0000A1Z5", "state_code": "20"}})

    if door == "remap":
        out = _remap(world, monkeypatch, payload)
        assert not out["ok"], out
        why = out["message"]
    else:
        out = _clear_hold(world, monkeypatch, res["order_id"])
        assert getattr(out, "status_code", None) == 409, out
        why = out.detail
    assert "splits this order's GST as IGST" in why and "credit note" in why, why

    after = db.orders.find_one({"order_id": res["order_id"]}, {"_id": 0})
    for k in ("interstate", "tax_totals", "invoice_number", "invoice_date", "fulfillment_hold",
              "stock_hold_reason"):
        assert after[k] == order[k], k
    assert "still on its seller (GSTIN) hold" in _invoice_refusal(world, monkeypatch, res["order_id"])
    assert _gstr1(world, monkeypatch, after, "BV-BOK-01")["b2cs"] == []


def test_the_gst_split_reads_the_shops_state_as_the_seller_check_does():
    """[MEDIUM] A shop with a state NAME and no state_code (the seed shape):
    the seller check read its state from the name, the split from the GSTIN
    the check called wrong. One read (org_validation.shop_state_code): a
    Maharashtra shop selling to Maharashtra is intra-state."""
    from api.routers.orders import _build_invoice_gst_split

    pune = {"store_name": "Pune", "state": "Maharashtra", "gstin": "20AAAAA0000A1Z5"}
    assert "is in state 27" in route_mod.gstin_problem(pune)["message"]
    split = _build_invoice_gst_split(
        [{"gst_rate": 12, "taxable_value": 100.0, "tax_amount": 12.0}], pune, {"state": "27"})
    assert split["interstate"] is False and split["place_of_supply_assumed"] is False


def test_every_gst_view_leaves_out_what_the_returns_leave_out(world, monkeypatch):
    """[LOW] A held (SELLER_UNKNOWN) order and a clean one in the month: the
    GST summary, the reconciliation, the cross-check's books and Tally legs
    and the per-store GST report count only the clean one -- as GSTR-1/3B and
    the Tally JV do."""
    from api.routers.finance import gst as fgst
    from api.routers.finance import gst_crosscheck as xc
    from api.utils.ist import ist_date_str

    db = world["db"]
    monkeypatch.setattr(shopify_push, "_live_or_reason", lambda _db: (False, "writes_disabled"))
    _res, held = _book(world, _order(57060))
    assert held["fulfillment_hold"] is True
    monkeypatch.setattr(shopify_push, "_live_or_reason", lambda _db: (True, None))
    _stock(db, "BV-BOK-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK)
    _res, clean = _book(world, _order(57061))
    tax = clean["tax_amount"]
    y, m = (int(x) for x in ist_date_str(clean["created_at"])[:7].split("-"))
    user = {"user_id": "u1", "roles": ["SUPERADMIN"]}

    monkeypatch.setattr(fgst, "_get_db", lambda: db)
    # (the CGST/SGST halves round each way by a paisa; both orders => ~2x)
    summary = asyncio.run(fgst.get_gst_summary(month=m, year=y, current_user=user))
    assert summary["gst_collected"] == pytest.approx(tax, abs=0.011)
    monkeypatch.setattr(xc, "_get_db", lambda: db)
    recon = asyncio.run(xc.get_gst_reconciliation(month=m, year=y, entity_id=None, current_user=user))
    assert recon["total_collected"] == pytest.approx(tax, abs=0.011)
    books, tally = xc._books_and_tally_for_stores(db, None, *xc._gst_month_window(y, m))
    assert books["sales_tax"] == tally["tax"] == tax
    # The per-store GST report (/reports/finance/gst): the held order's store
    # shows no row, the clean order's store shows its one.
    from datetime import date as _date

    from api.routers.reports import finance_ops as fo
    from tests.ist_business_day import business_day
    from database.repositories.order_repository import OrderRepository

    monkeypatch.setattr(fo, "get_order_repository", lambda: OrderRepository(db.orders))
    monkeypatch.setattr(fo, "_get_raw_db", lambda: db)
    for order, rows in ((held, 0), (clean, 1)):
        day = _date.fromisoformat(business_day(order["created_at"]))
        rep = asyncio.run(fo.gst_report(from_date=day, to_date=day, store_id=order["store_id"],
                                        current_user=user))
        assert len(rep["data"]) == rows, (order["store_id"], rep)


def test_a_split_leg_without_a_gstin_is_named_as_a_leg(world):
    """[LOW] The invoice is Bokaro's; Pune ships a leg with no GSTIN. The
    hold (and its P1 task at Bokaro) says what the LEG lacks and both ways
    out -- not 'the tax invoice cannot be issued from Pune', which it never
    is."""
    db = world["db"]
    db.stores.update_one({"store_id": PUNE}, {"$set": {"shopify_location_id": LOC_PUN, "gstin": ""}})
    _stock(db, "BV-BOK-01", "P-RB", 1)
    _stock(db, PUNE, "P-OA", 1)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 1)])
    world["shop"].fo(FO_2, LOC_PUN, lines=[(9001, 1)])

    res, order = _book(world, _order(57070, lines=(("RB-1234", 1), ("OA-5", 1))))

    [p] = order["fulfillment_route"]["problems"]
    assert p["message"].startswith("Pune ships part of this order and has no GSTIN")
    assert "The tax invoice is Bokaro's (20AAAAA0000A1Z5)" in p["message"]
    assert "cannot be issued from it" not in p["message"] and "press Re-map" in p["message"]
    # Pune is in Maharashtra: no Maharashtra shop can be under Bokaro's
    # Jharkhand GSTIN, so the move is the only fix offered.
    assert "set that GSTIN" not in p["message"]
    [task] = [t for t in world["tasks"].created
              if t["source_ref"] == f"online_route:SHOP_GSTIN_MISSING:{res['order_id']}"]
    assert task["description"] == p["message"] and task["store_id"] == "BV-BOK-01"


def test_a_leg_in_the_sellers_state_is_offered_the_sellers_gstin_too():
    """[LOW] A Jharkhand leg shop with no GSTIN may be a place of business
    under Bokaro's registration: both fixes are named."""
    bokaro = {"store_name": "Bokaro", "state_code": "20", "gstin": "20AAAAA0000A1Z5"}
    msg = route_mod._leg_gstin_problem(
        {"store_name": "Dhanbad", "state_code": "20", "gstin": ""}, bokaro)["message"]
    assert "if Dhanbad is registered under 20AAAAA0000A1Z5, set that GSTIN on Dhanbad" in msg
    assert "otherwise move its fulfillment order" in msg


# ---------------------------------------------------------------------------
# R16 -- money panel, round 8
# ---------------------------------------------------------------------------


def test_an_unmapped_location_names_the_shop_that_really_ships_it(world, monkeypatch):
    """[LOW] Shopify assigned the unmapped Pune location; the fallback BV-BOK-01
    is mapped but holds nothing, Ranchi holds 1 -- the order moves to Ranchi.
    The LOCATION_UNMAPPED text (and its P1 task, scoped to Ranchi) said 'IMS
    used the fallback shop BV-BOK-01'; it names Ranchi now."""
    db = world["db"]
    monkeypatch.setenv("ONLINE_FULFILLMENT_STORE_ID", "BV-BOK-01")
    _stock(db, "BV-RAN-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_PUNE_SHOPIFY, name="Pune warehouse")

    res, order = _book(world, _order(58001))

    route = order["fulfillment_route"]
    assert order["store_id"] == "BV-RAN-01" and route["reason"] == "MOVED"
    [p] = route["problems"]
    assert p["code"] == "LOCATION_UNMAPPED" and "Pune warehouse" in p["message"]
    assert "IMS used the fallback shop" not in p["message"]
    assert ("The fallback shop BV-BOK-01 (ONLINE_FULFILLMENT_STORE_ID) does not hold every "
            "unit, so IMS ships it from BV-RAN-01") in p["message"]
    [task] = [t for t in world["tasks"].created
              if t["source_ref"] == f"online_route:LOCATION_UNMAPPED:{res['order_id']}"]
    assert task["description"] == p["message"] and task["store_id"] == "BV-RAN-01"


def _move_failed_at_ranchi(world, order_id, ranchi_units=1, qty=1):
    """Shopify has the order at Bokaro, which holds nothing; Ranchi holds it,
    so the order is claimed and billed at Ranchi and the move is refused:
    MOVE_FAILED, held. The human closes every task the booking raised."""
    _stock(world["db"], "BV-RAN-01", "P-RB", ranchi_units)
    world["shop"].fo(FO_1, LOC_BOK, units=qty)
    world["shop"].move_error = "Location does not stock the item"
    payload = _order(order_id, lines=(("RB-1234", qty),))
    res, order = _book(world, payload)
    assert [p["code"] for p in order["fulfillment_route"]["problems"]] == ["MOVE_FAILED"]
    assert order["store_id"] == "BV-RAN-01"
    for t in world["tasks"].created:
        t["status"] = "COMPLETED"
    world["shop"].move_error = None
    return payload, res, order


def _units(db, order_id):
    return sorted(u["stock_id"] for u in db.stock_units.find({"order_id": order_id, "status": "SOLD"}))


@pytest.mark.parametrize("hold", ["move_failed", "gstin_fixed"])
def test_a_remap_at_the_same_shop_keeps_the_very_units_the_order_holds(world, monkeypatch, hold):
    """[LOW] The order holds U1; an earlier unit U0 came back on the shelf (the
    order holding it was cancelled). A Re-map that ships from the same shop
    gave U1 back and claimed U0 -- the packed unit on the shelf, serial
    lineage wrong. Nothing is given back now, and the shop that already
    packed it is not told to ship it again."""
    db = world["db"]
    shop = "BV-RAN-01" if hold == "move_failed" else "BV-BOK-01"
    for sid in ("U0", "U1"):
        db.stock_units.insert_one({"stock_id": sid, "product_id": "P-RB", "store_id": shop,
                                   "status": "AVAILABLE"})
    db.stock_units.update_one({"stock_id": "U0"}, {"$set": {"status": "SOLD", "order_id": "OTHER"}})
    if hold == "move_failed":
        world["shop"].fo(FO_1, LOC_BOK)
        world["shop"].move_error = "Location does not stock the item"
    else:
        db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": ""}})
        world["shop"].fo(FO_1, LOC_BOK)
    payload = _order(58010 + (hold == "gstin_fixed"))
    res, order = _book(world, payload)
    assert order["fulfillment_hold"] is True and _units(db, res["order_id"]) == ["U1"]
    for t in world["tasks"].created:
        t["status"] = "COMPLETED"  # the shop packed it; the human closed the rest
    world["shop"].move_error = None
    db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": "20AAAAA0000A1Z5"}})
    db.stock_units.update_one({"stock_id": "U0"}, {"$set": {"status": "AVAILABLE", "order_id": None}})

    out = _remap(world, monkeypatch, payload)

    assert out["ok"] and out["result"]["status"] == "rerouted", out
    assert _units(db, res["order_id"]) == ["U1"]
    assert db.stock_units.find_one({"stock_id": "U0"})["status"] == "AVAILABLE"
    assert db.stock_units.count_documents({"released_from_order_id": res["order_id"]}) == 0
    after = db.orders.find_one({"order_id": res["order_id"]}, {"_id": 0})
    assert after["fulfillment_breakdown"] == [{"product_id": "P-RB", "store_id": shop, "qty": 1}]
    assert world["tasks"].open_refs("online_fallback_ship:") == []


def test_a_failing_release_on_a_same_shop_remap_never_touches_the_units(world, monkeypatch):
    """[LOW] The panel's input: a MOVE_FAILED order holding 2 units at Ranchi,
    and a release that frees 1 then fails. Re-map refused and left the freed
    unit on the shelf (sellable at the till, the breakdown still saying 2).
    Now a same-shop Re-map gives nothing back: the release is never called."""
    from database.repositories.product_repository import StockRepository

    db = world["db"]
    payload, res, _order_doc = _move_failed_at_ranchi(world, 58020, ranchi_units=2, qty=2)

    def half_then_fail(self, order_id, **kw):
        raise AssertionError("a same-shop Re-map must not give a unit back")

    monkeypatch.setattr(StockRepository, "release_sold_units_for_order", half_then_fail)
    out = _remap(world, monkeypatch, payload)

    assert out["ok"], out
    assert _sold_at(db, res["order_id"]) == ["BV-RAN-01", "BV-RAN-01"]
    assert db.stock_units.count_documents({"status": "AVAILABLE"}) == 0


@pytest.mark.parametrize("race", [False, True])
def test_a_refused_remap_puts_back_every_unit_it_gave_back(world, monkeypatch, race):
    """[LOW] Where a Re-map must give units back (the human moved Pune's leg
    to Bokaro, so Pune's 2 OA units go back) and the release frees 1 then
    fails: refused, and the freed unit is the order's again -- the very
    same one; nothing sellable at the till, the breakdown still true.
    ``race``: the till sold the freed unit in between -- a stock miss, loud."""
    from database.repositories.product_repository import StockReleaseResult, StockRepository

    db = world["db"]
    db.stores.update_one({"store_id": PUNE}, {"$set": {"shopify_location_id": LOC_PUN}})
    _stock(db, "BV-BOK-01", "P-RB", 2)
    _stock(db, "BV-BOK-01", "P-OA", 2)
    _stock(db, PUNE, "P-OA", 2)
    # Bokaro's fulfillment order is first and as large as Pune's: Bokaro bills.
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 2)])
    world["shop"].fo(FO_2, LOC_PUN, lines=[(9001, 2)])
    payload = _order(58021 + 100 * race, lines=(("RB-1234", 2), ("OA-5", 2)))
    res, order = _book(world, payload)
    assert [p["code"] for p in order["fulfillment_route"]["problems"]] == ["SPLIT_SELLERS"]
    assert order["store_id"] == "BV-BOK-01"
    before = _units(db, res["order_id"])
    world["shop"].fos[1]["assignedLocation"]["location"]["id"] = LOC_BOK  # the human's move
    real = StockRepository.release_sold_units_for_order

    def half_then_fail(self, order_id, **kw):
        got = real(self, order_id, **{**kw, "limit": 1})
        if race:
            db.stock_units.update_one({"stock_id": got.released[0]},
                                      {"$set": {"status": "SOLD", "order_id": "WALK-IN"}})
        return StockReleaseResult(got.released, True)

    monkeypatch.setattr(StockRepository, "release_sold_units_for_order", half_then_fail)
    out = _remap(world, monkeypatch, payload)

    assert not out["ok"] and "press Re-map again" in out["message"], out
    if race:
        [miss] = list(db.online_stock_miss.find({"order_id": res["order_id"]}))
        assert miss["store_id"] == PUNE and len(miss["detail"]["lost_on_remap"]) == 1
        assert len(_units(db, res["order_id"])) == len(before) - 1
        return
    assert _units(db, res["order_id"]) == before
    assert db.stock_units.count_documents({"store_id": PUNE, "status": "AVAILABLE"}) == 0
    after = db.orders.find_one({"order_id": res["order_id"]}, {"_id": 0})
    assert after["fulfillment_breakdown"] == order["fulfillment_breakdown"]
    assert after["stock_hold_reason"] == order["stock_hold_reason"]


@pytest.mark.parametrize("fix", ["state_corrected", "gstin_only"])
def test_a_standing_seller_hold_keeps_the_invoice_and_every_return_off(world, monkeypatch, fix):
    """[MEDIUM] The buyer is in Maharashtra. Booked HELD on SHOP_GSTIN_MISSING
    with the split taken then. Organization fixes Bokaro -- and until someone
    lifts the hold, the invoice door split LIVE (IGST) while GSTR-1/3B filed
    the booking's stored split (CGST+SGST): one order, two tax heads. While
    the hold stands, no door issues or files it; and since the fix changed
    the tax head, the release refuses (round 13: a credit note, never a
    re-split)."""
    db = world["db"]
    if fix == "state_corrected":  # Jharkhand GSTIN, state typed as Maharashtra
        db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"state_code": "27"}})
    else:  # neither GSTIN nor state; the admin sets only the GSTIN
        db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": "", "state_code": None}})
    _stock(db, "BV-BOK-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK)
    res, order = _book(world, _order(58030 + (fix == "gstin_only"), buyer_state="27"))
    assert order["fulfillment_hold"] is True and order["interstate"] is False
    if fix == "state_corrected":
        db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"state_code": "20"}})
    else:
        db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": "20AAAAA0000A1Z5"}})

    assert "still on its seller (GSTIN) hold" in _invoice_refusal(world, monkeypatch, res["order_id"])
    filed = _gstr1(world, monkeypatch, order, "BV-BOK-01")
    assert filed["b2cs"] == [] and filed["b2b"] == []
    assert _gstr3b(world, monkeypatch, order, "BV-BOK-01")["outwardTaxableSupplies"]["centralTax"] == 0
    assert "still on its seller" in _challan(world, monkeypatch, res["order_id"])

    out = _clear_hold(world, monkeypatch, res["order_id"])

    assert getattr(out, "status_code", None) == 409 and "credit note" in out.detail, out
    after = db.orders.find_one({"order_id": res["order_id"]}, {"_id": 0})
    assert after["fulfillment_hold"] is True and after["interstate"] is False
    assert "still on its seller (GSTIN) hold" in _invoice_refusal(world, monkeypatch, res["order_id"])


@pytest.mark.parametrize("door", ["clear_hold", "remap"])
@pytest.mark.parametrize("days", [40, 400])
def test_a_released_seller_hold_keeps_its_invoice_number_and_date(world, monkeypatch, door, days):
    """[MEDIUM] Round 13, THE SIMPLIFIED ROOT RULE (owner 2026-10-01): held
    (SHOP_GSTIN_MISSING) at Bokaro, booked 40 -- or 400: another financial
    year -- days ago, released once Bokaro has its GSTIN. Round 8 re-dated it
    to the release day and round 10 re-numbered it in a new year; neither
    door touches the invoice now: the same number, date and tax head, filed
    in the booking month, nothing superseded, no serial drawn."""
    from datetime import datetime, timedelta, timezone

    db = world["db"]
    payload, res, order = _gstin_missing_at_bokaro(
        world, 58040 + (door == "remap") + 2 * (days == 400))
    booked = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0) - timedelta(days=days)
    db.orders.update_one({"order_id": res["order_id"]},
                         {"$set": {"created_at": booked, "invoice_date": booked}})
    db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": "20AAAAA0000A1Z5"}})

    if door == "remap":
        out = _remap(world, monkeypatch, payload)
        assert out["ok"] and "hold is lifted" in out["message"], out
    else:
        assert _clear_hold(world, monkeypatch, res["order_id"])["released"] == ["SELLER"]

    after = db.orders.find_one({"order_id": res["order_id"]}, {"_id": 0})
    assert after["fulfillment_hold"] is False
    for k in ("store_id", "invoice_number", "interstate", "tax_totals"):
        assert after[k] == order[k], k
    assert after["invoice_date"] == after["created_at"] == booked
    assert not {"booked_at", "superseded_invoice_number"} & set(after)
    assert len(_gstr1(world, monkeypatch, after, "BV-BOK-01")["b2cs"]) == 1
    assert db.counters.find_one({"_id": {"$regex": "BV-BOK-01"}})["seq"] == 1


def test_remap_never_reopens_a_task_a_human_closed(world, monkeypatch):
    """[LOW] Input A: the fallback BV-BOK-01 is mapped, Shopify has the order
    at unmapped Pune, only Ranchi holds it and Shopify refuses the move:
    booked MOVED to Ranchi with LOCATION_UNMAPPED + MOVE_FAILED tasks. The
    human closes both (and Ranchi its ship task); Shopify then takes the
    move and Re-map succeeds. LOCATION_UNMAPPED was re-opened (P1) and the
    ship task re-issued; neither is now, and MOVE_FAILED stays closed."""
    db = world["db"]
    monkeypatch.setenv("ONLINE_FULFILLMENT_STORE_ID", "BV-BOK-01")
    _stock(db, "BV-RAN-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_PUNE_SHOPIFY, name="Pune warehouse")
    world["shop"].move_error = "Fulfillment order cannot be moved"
    payload = _order(58050)
    res, order = _book(world, payload)
    oid = res["order_id"]
    assert sorted(p["code"] for p in order["fulfillment_route"]["problems"]) == [
        "LOCATION_UNMAPPED", "MOVE_FAILED"]
    for t in world["tasks"].created:
        t["status"] = "COMPLETED"
    world["shop"].move_error = None

    out = _remap(world, monkeypatch, payload)

    assert out["ok"] and "hold is lifted" in out["message"], out
    assert world["tasks"].open_refs("online_route:") == []
    assert world["tasks"].open_refs("online_fallback_ship:") == []
    assert len(world["tasks"].refs(f"online_route:LOCATION_UNMAPPED:{oid}")) == 1


def test_a_refused_remap_raises_no_task(world, monkeypatch):
    """[LOW] Input B: MOVE_FAILED at Ranchi, its invoice printed, Ranchi
    closed its ship task, Bokaro restocks, Re-map is REFUSED (Shopify's
    routing would change the seller). The refusal re-issued 'Pack and hand
    them to dispatch' to Ranchi; it touches nothing now."""
    db = world["db"]
    payload, res, _order_doc = _move_failed_at_ranchi(world, 58060)
    _invoice(world, monkeypatch, res["order_id"])
    _stock(db, "BV-BOK-01", "P-RB", 1)
    n_tasks = len(world["tasks"].created)

    out = _remap(world, monkeypatch, payload)

    assert not out["ok"] and "not from BV-RAN-01" in out["message"], out
    assert len(world["tasks"].created) == n_tasks
    assert _sold_at(db, res["order_id"]) == ["BV-RAN-01"]


def _split_leg_move_refused(world, order_id):
    """Shopify split RB x2 -> Bokaro, OA -> Ranchi (short); BV Dhanbad (Bokaro's
    GSTIN) holds the OA, so FO_2 is planned to Dhanbad -- and Shopify refuses."""
    db = world["db"]
    _shop(db, "BV-DHN-01", "BV Dhanbad", "20AAAAA0000A1Z5", LOC_DHN)
    _stock(db, "BV-BOK-01", "P-RB", 2)
    _stock(db, "BV-DHN-01", "P-OA", 1)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 2)])
    world["shop"].fo(FO_2, LOC_RAN, lines=[(9001, 1)])
    world["shop"].move_error = "Location does not stock the item"
    res, order = _book(world, _order(order_id, lines=(("RB-1234", 2), ("OA-5", 1))))
    assert world["shop"].moves() == [{"id": FO_2, "newLocationId": LOC_DHN}]
    assert _sold_at(db, res["order_id"]) == ["BV-BOK-01", "BV-BOK-01", "BV-DHN-01"]
    return res, order


def test_a_failed_split_leg_move_names_the_legs_shop(world):
    """[LOW] The MOVE_FAILED hold text and task said 'the shipping shop's
    Shopify location' -- order.store_id is Bokaro, so a human moved FO_2 to
    Bokaro. It names Dhanbad, the leg's shop, now."""
    res, order = _split_leg_move_refused(world, 58070)

    [p] = order["fulfillment_route"]["problems"]
    assert p["code"] == "MOVE_FAILED" and order["store_id"] == "BV-BOK-01"
    assert "to BV-DHN-01's Shopify location" in p["message"]
    assert "stock it at BV-DHN-01's location" in p["message"]
    assert "move it to BV-DHN-01's location in Shopify admin" in p["message"]
    assert "shipping shop" not in p["message"]
    assert order["stock_hold_reason"] == p["message"]


def test_a_human_moved_split_leg_rewrites_the_shops_stock_at_dispatch(world, monkeypatch):
    """[LOW] The split twin of the whole-order rule: the leg's move failed, a
    human moved FO_2 from Ranchi to Dhanbad in Shopify admin and cleared the
    hold. FO_2 was recorded as Dhanbad's at booking anyway, so the dispatch
    never re-asserted stock and Ranchi's location stayed one phantom OA high.
    FO_2 is not recorded until it really moves; the dispatch rewrites."""
    import api.services.online_stock_writeback as wb

    db = world["db"]
    res, order = _split_leg_move_refused(world, 58071)
    assert order["fulfillment_route"]["fulfillment_order_ids"] == [FO_1]
    assert _clear_hold(world, monkeypatch, res["order_id"])["released"] == ["STOCK"]
    wrote = []
    monkeypatch.setattr(wb, "writeback_after_sale", lambda db, items, store: wrote.append(store))
    after = db.orders.find_one({"order_id": res["order_id"]}, {"_id": 0})

    pushed = _push(world, after, {FO_1: LOC_BOK, FO_2: LOC_DHN})  # the human's move

    assert pushed.ok and _fulfilled(world) == [[FO_1, FO_2]]
    assert wrote == ["BV-BOK-01"]


def test_remap_tells_only_a_shop_that_claims_a_new_unit(world, monkeypatch):
    """[LOW] Split RB -> Bokaro, OA -> Pune (another GSTIN): held. Bokaro packs
    its RB and closes its ship task; the human moves FO_2 to Ranchi (Bokaro's
    GSTIN) and presses Re-map. Ranchi claims the OA and is told to ship it;
    Bokaro keeps the very RB it packed and is not told again."""
    db = world["db"]
    payload, res, _order_doc = _split_sellers(world, 58080)
    oid = res["order_id"]
    _stock(db, "BV-RAN-01", "P-OA", 1)
    rb = _units(db, oid)
    for t in world["tasks"].created:
        t["status"] = "COMPLETED"
    world["shop"].fos[1]["assignedLocation"]["location"]["id"] = LOC_RAN  # the human's move

    out = _remap(world, monkeypatch, payload)

    assert out["ok"] and "hold is lifted" in out["message"], out
    assert _sold_at(db, oid) == ["BV-BOK-01", "BV-RAN-01"]
    assert [u for u in _units(db, oid) if "BV-BOK-01" in u] == [u for u in rb if "BV-BOK-01" in u]
    assert world["tasks"].open_refs("online_fallback_ship:") == [f"online_fallback_ship:{oid}:BV-RAN-01"]


# ---------------------------------------------------------------------------
# R17 -- money panel, round 10
# ---------------------------------------------------------------------------


def _yielding_routing_read(world, monkeypatch, during=None):
    """The routing read yields to the loop, as a real network hop does,
    running ``during`` first -- what lands while Shopify answers."""
    real = world["shop"].graphql

    async def graphql(db, query, variables):
        if "imsOrderRouting" in query:
            if during:
                during()
            await asyncio.sleep(0)
        return await real(db, query, variables)

    monkeypatch.setattr(shopify_push, "_graphql", graphql)


def _dark_seller_unknown(world, monkeypatch, order_id):
    """Booked dark: SELLER_UNKNOWN at the bucket, nothing claimed. Then
    Shopify can be read and has it at Bokaro, which holds the unit."""
    db = world["db"]
    monkeypatch.setattr(shopify_push, "_live_or_reason", lambda _db: (False, "writes_disabled"))
    _stock(db, "BV-BOK-01", "P-RB", 1)
    payload = _order(order_id)
    res, order = _book(world, payload)
    assert [p["code"] for p in order["fulfillment_route"]["problems"]] == ["SELLER_UNKNOWN"]
    monkeypatch.setattr(shopify_push, "_live_or_reason", lambda _db: (True, None))
    world["shop"].fo(FO_1, LOC_BOK)
    return payload, res, order


def test_two_remap_presses_never_both_act(world, monkeypatch):
    """[LOW-MEDIUM] Two Re-maps at once, the routing read yielding: the second
    carried on from the order as read BEFORE its lease. The lease is taken
    first: one claims, one is refused -- one unit claimed though Bokaro holds
    two, and no serial drawn (round 13: Re-map never draws one)."""
    db = world["db"]
    payload, res, order = _short_held_at_bokaro(world, 59001)
    db.stock_units.insert_one({"stock_id": "U-EXTRA", "product_id": "P-RB",
                               "store_id": "BV-BOK-01", "status": "AVAILABLE"})
    _yielding_routing_read(world, monkeypatch)
    oid = res["order_id"]

    async def both():
        return await asyncio.gather(
            route_mod.reroute_held_order(db, oid),
            route_mod.reroute_held_order(db, oid),
        )

    outs = asyncio.run(both())

    assert sorted(o["status"] for o in outs) == ["refused", "rerouted"], outs
    assert "another Re-map of this order is running" in str(outs)
    after = db.orders.find_one({"order_id": oid}, {"_id": 0})
    assert after["invoice_number"] == order["invoice_number"]
    assert db.counters.find_one({"_id": {"$regex": "BV-BOK-01"}})["seq"] == 1
    assert _sold_at(db, oid) == ["BV-BOK-01"] and "reroute_lease_at" not in after


@pytest.mark.parametrize("when", ["during_the_routing_read", "after_the_checks"])
def test_a_cancel_landing_mid_remap_is_never_claimed(world, monkeypatch, when):
    """[LOW-MEDIUM] orders/cancelled lands while Re-map runs: it still claimed
    Bokaro's unit for the CANCELLED order (the unit SOLD against a cancelled
    order, Shopify's stock written one lower). The checks run again after
    the routing read, and the write is conditioned on the order as checked:
    refused, nothing claimed."""
    db = world["db"]
    payload, res, order = _short_held_at_bokaro(world, 59002 + (when == "after_the_checks"))
    oid = res["order_id"]

    def cancel():
        db.orders.update_one({"order_id": oid}, {"$set": {"status": "CANCELLED"}})

    if when == "during_the_routing_read":
        _yielding_routing_read(world, monkeypatch, during=cancel)
    else:  # between the last check and the write (another worker's thread)
        real = route_mod.route_order

        def route_then_cancel(*a, **k):
            cancel()
            return real(*a, **k)

        monkeypatch.setattr(route_mod, "route_order", route_then_cancel)

    out = asyncio.run(route_mod.reroute_held_order(db, oid))

    assert out["status"] == "refused", out
    assert {"during_the_routing_read": "the order is CANCELLED",
            "after_the_checks": "the order changed while Re-map ran"}[when] in out["message"], out
    after = db.orders.find_one({"order_id": oid}, {"_id": 0})
    assert after["status"] == "CANCELLED" and after["store_id"] == order["store_id"]
    assert after["invoice_number"] == order["invoice_number"]
    assert _sold_at(db, oid) == [] and "reroute_lease_at" not in after


def test_remap_never_puts_an_order_onto_a_seller_hold(world, monkeypatch):
    """[MEDIUM] The panel's input: RB+OA, Shopify at Bokaro (short), Ranchi
    covers, the move fails -- MOVE_FAILED at Ranchi. The invoice door prints
    Ranchi's invoice and its month's GSTR-1 files it. A human moves the OA
    line to Pune (Maharashtra) by hand and presses Re-map: it put the order
    on SPLIT_SELLERS -- off the filed GSTR-1, the reprint refused. Refused
    now: a route failing the seller check is never written."""
    db = world["db"]
    db.stores.update_one({"store_id": PUNE}, {"$set": {"shopify_location_id": LOC_PUN}})
    _stock(db, "BV-RAN-01", "P-RB", 1)
    _stock(db, "BV-RAN-01", "P-OA", 1)
    _stock(db, PUNE, "P-OA", 1)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 1), (9001, 1)])
    world["shop"].move_error = "Location does not stock the item"
    payload = _order(59020, lines=(("RB-1234", 1), ("OA-5", 1)))
    res, order = _book(world, payload)
    oid = res["order_id"]
    assert order["store_id"] == "BV-RAN-01"
    assert [p["code"] for p in order["fulfillment_route"]["problems"]] == ["MOVE_FAILED"]
    world["shop"].move_error = None
    assert _invoice(world, monkeypatch, oid)["invoiceNumber"] == order["invoice_number"]
    assert len(_gstr1(world, monkeypatch, order, "BV-RAN-01")["b2cs"]) == 1
    world["shop"].fos[:] = []  # the human's move: the OA line now at Pune
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 1)])
    world["shop"].fo(FO_2, LOC_PUN, lines=[(9001, 1)])

    out = _remap(world, monkeypatch, payload)

    assert not out["ok"] and "failing the seller check" in out["message"], out
    assert "different GSTINs" in out["message"]
    after = db.orders.find_one({"order_id": oid}, {"_id": 0})
    assert after["stock_hold_reason"] == order["stock_hold_reason"]
    assert after["invoice_number"] == order["invoice_number"] and after["created_at"] == order["created_at"]
    assert len(_gstr1(world, monkeypatch, after, "BV-RAN-01")["b2cs"]) == 1
    assert _invoice_refusal(world, monkeypatch, oid) is None
    assert _sold_at(db, oid) == ["BV-RAN-01", "BV-RAN-01"]
    assert db.stock_units.find_one({"store_id": PUNE})["status"] == "AVAILABLE"


def test_remap_never_moves_a_leg_back_into_a_shop_under_another_gstin(world, monkeypatch):
    """[LOW] Split RB -> Bokaro, OA -> Pune (another GSTIN; the order holds
    Pune's only OA): held. The human does what the hold says -- moves FO_2 to
    Ranchi (Bokaro's GSTIN, no OA) -- and presses Re-map. The order's own
    unit made Pune look like a holder: Re-map sent FO_2 back to Pune and
    raised SPLIT_SELLERS again, a loop no documented action left. Now the leg
    stays at Ranchi, short, a stock miss named there; Pune's OA goes back."""
    db = world["db"]
    payload, res, order = _split_sellers(world, 59040)
    oid = res["order_id"]
    world["shop"].fos[1]["assignedLocation"]["location"]["id"] = LOC_RAN  # the human's move
    sent = len(world["shop"].moves())

    out = _remap(world, monkeypatch, payload)

    assert out["ok"] and out["result"]["status"] == "rerouted", out
    assert world["shop"].moves()[sent:] == []
    after = db.orders.find_one({"order_id": oid}, {"_id": 0})
    assert after["store_id"] == "BV-BOK-01"
    assert "SPLIT_SELLERS" not in [p["code"] for p in after["fulfillment_route"]["problems"]]
    assert {r["store_id"] for r in after["fulfillment_route"]["split"]} == {"BV-BOK-01", "BV-RAN-01"}
    [miss] = list(db.online_stock_miss.find({"order_id": oid, "resolved": False}))
    assert miss["store_id"] == "BV-RAN-01" and after["fulfillment_hold"] is True
    assert _sold_at(db, oid) == ["BV-BOK-01"]
    assert db.stock_units.find_one({"store_id": PUNE, "product_id": "P-OA"})["status"] == "AVAILABLE"


def test_remap_refuses_while_a_refund_waits_for_the_accountant(world, monkeypatch):
    """[MEDIUM] A Shopify refund queued for review (SHOPIFY_REFUND_AUTO off):
    money went back, so what the order still ships is a human's call. Re-map
    refuses while it waits -- nothing claimed, the hold as it was."""
    db = world["db"]
    payload, res, order = _short_held_at_bokaro(world, 59060)
    db.shopify_refund_review.insert_one({
        "review_id": "R-1", "shopify_refund_id": "RF-1", "shopify_order_id": str(payload["id"]),
        "order_id": None, "store_id": order["store_id"], "invoice_number": order["invoice_number"],
        "status": "PENDING_REVIEW", "resolved": False})

    out = _remap(world, monkeypatch, payload)

    assert not out["ok"] and "refund or return" in out["message"], out
    after = db.orders.find_one({"order_id": res["order_id"]}, {"_id": 0})
    assert after["fulfillment_hold"] is True and after["stock_hold_reason"] == order["stock_hold_reason"]
    assert _sold_at(db, res["order_id"]) == []


@pytest.mark.parametrize("when", ["queued_after_the_checks", "marked_before_the_row"])
def test_a_refund_queued_while_remap_runs_stops_its_write(world, monkeypatch, when):
    """[LOW] Round 13, item 8: Re-map's last refund check runs before
    route_order, the unit release and the claim; nothing a refund door wrote
    was in the write's condition, so a review queued on another worker in
    that window was never seen -- Re-map moved the order's claims under a
    queued credit note. Every refund and return door stamps the order's mark
    FIRST now (mark_refund_or_return), and the mark is in HOLD_CAS: refused,
    nothing claimed. A mark stamped before its row lands is refused on too."""
    from api.services import shopify_refund

    db = world["db"]
    payload, res, order = _short_held_at_bokaro(world, 59061 + (when == "marked_before_the_row"))
    oid = res["order_id"]
    live = db.orders.find_one({"order_id": oid}, {"_id": 0})

    def queue():
        shopify_refund._queue_review(
            db, refund_id="RF-9", shopify_order_id=str(payload["id"]), order=live,
            credit_note={"gross_refund": 100.0}, restock_lines=[], restock_store=None,
            status="PENDING_REVIEW", note="queued mid Re-map")

    if when == "queued_after_the_checks":  # another worker's refund drain
        real = route_mod.route_order

        def route_then_queue(*a, **k):
            queue()
            return real(*a, **k)

        monkeypatch.setattr(route_mod, "route_order", route_then_queue)
    else:  # the door stamped its mark; its row is not written yet
        route_mod.mark_refund_or_return(db, oid)

    out = asyncio.run(route_mod.reroute_held_order(db, oid))

    assert out["status"] == "refused", out
    assert {"queued_after_the_checks": "the order changed while Re-map ran",
            "marked_before_the_row": "refund or return"}[when] in out["message"], out
    after = db.orders.find_one({"order_id": oid}, {"_id": 0})
    assert after["fulfillment_hold"] is True and after["stock_hold_reason"] == order["stock_hold_reason"]
    assert after[route_mod.REFUND_MARK] == 1
    assert _sold_at(db, oid) == [] and "reroute_lease_at" not in after
    if when == "queued_after_the_checks":
        assert db.shopify_refund_review.count_documents({"order_id": oid}) == 1


@pytest.mark.parametrize("task", ["closed", "open"])
def test_a_remap_whose_move_fails_again_leaves_its_task_as_it_was(world, monkeypatch, task):
    """[LOW] MOVE_FAILED at Ranchi; Shopify still refuses the move when the
    human presses Re-map. The retried move re-raised the MOVE_FAILED task a
    human had closed; an open one was closed with 'the order was routed' and
    an identical one opened. The one task stays as it was now."""
    payload, res, _o = _move_failed_at_ranchi(world, 59070 + (task == "open"))
    ref = f"online_route:MOVE_FAILED:{res['order_id']}"
    if task == "open":
        for t in world["tasks"].created:
            if t["source_ref"] == ref:
                t["status"] = "OPEN"
    world["shop"].move_error = "Location does not stock the item"

    out = _remap(world, monkeypatch, payload)

    assert out["ok"] and "still on hold" in out["message"], out
    [t] = [t for t in world["tasks"].created if t["source_ref"] == ref]
    assert t["status"] == ("OPEN" if task == "open" else "COMPLETED")
    assert "completion_notes" not in t


def test_a_return_on_an_order_no_shop_shipped_mints_nothing(world, monkeypatch):
    """[LOW] ONLINE_STORE_ID=BV-RAN-01 (a physical shop), dark: route NONE,
    billed at BV-RAN-01, nothing claimed. The customer cancels with restock:
    the direct branch minted a unit at Ranchi (1 -> 2 AVAILABLE), written back
    to Shopify and sellable at the till. Unresolved now (loud, nothing
    minted) -- the accountant's refund-review door included, whose rebuilt
    order carries the route."""
    from api.routers import returns
    from api.services import shopify_refund
    from database.repositories.order_repository import OrderRepository

    db = world["db"]
    monkeypatch.setattr(shopify_push, "_live_or_reason", lambda _db: (False, "writes_disabled"))
    monkeypatch.setenv("ONLINE_STORE_ID", "BV-RAN-01")
    _stock(db, "BV-RAN-01", "P-RB", 1)
    res, order = _book(world, _order(59080))
    assert order["fulfillment_route"]["reason"] == "NONE" and order["store_id"] == "BV-RAN-01"
    monkeypatch.setattr(returns, "get_order_repository", lambda: OrderRepository(db.orders))
    rebuilt = {"order_id": order["order_id"], "store_id": order["store_id"]}  # the review row's
    assert shopify_refund._merge_fulfilment_context(rebuilt)

    for o in (order, rebuilt):
        hit = returns._resolve_restock_store(o["store_id"], o["order_id"], order=o, product_id="P-RB")
        assert hit["store_id"] is None and hit["reason"] == returns._RESTOCK_ROUTE_UNRESOLVED, hit


# ---------------------------------------------------------------------------
# R18 -- money panel, round 11
# ---------------------------------------------------------------------------


def _cancel_door(db, order_id):
    """THE cancel door's two acts (orders/cancel.py), as another worker runs
    them: its claim flips the status (stamping its reason), then it releases
    every unit still SOLD against the order."""
    from api.routers.orders.release import _claim_order_for_cancel
    from database.repositories.order_repository import OrderRepository
    from database.repositories.product_repository import StockRepository

    assert _claim_order_for_cancel(OrderRepository(db.orders), order_id,
                                   "Customer changed their mind", {"user_id": "u-counter"})
    StockRepository(db.stock_units).release_sold_units_for_order(order_id)


def test_a_cancel_after_remap_gave_a_unit_back_never_sells_it_again(world, monkeypatch):
    """[LOW-MEDIUM] Split RB -> Bokaro, OA -> Pune (another GSTIN): held. The
    human moves FO_2 to Bokaro, so Re-map gives Pune's OA back. The cancel
    door lands on another worker before Re-map's write: the write fails on
    status and put_back marked Pune's unit SOLD against the CANCELLED order
    -- unsellable at the till, written to Shopify as gone. It stays on the
    shelf now."""
    from database.repositories.product_repository import StockRepository

    db = world["db"]
    payload, res, _o = _split_sellers(world, 60001, bokaro_oa=1)
    oid = res["order_id"]
    world["shop"].fos[1]["assignedLocation"]["location"]["id"] = LOC_BOK  # the human's move
    real = StockRepository.release_sold_units_for_order

    def give_back_then_cancel(self, order_id, **kw):
        got = real(self, order_id, **kw)
        if kw.get("reason") == "ONLINE_REROUTE":
            _cancel_door(db, order_id)
        return got

    monkeypatch.setattr(StockRepository, "release_sold_units_for_order", give_back_then_cancel)
    out = asyncio.run(route_mod.reroute_held_order(db, oid))

    assert out["status"] == "refused" and "changed while Re-map ran" in out["message"], out
    assert db.stock_units.find_one({"store_id": PUNE, "product_id": "P-OA"})["status"] == "AVAILABLE"
    assert _sold_at(db, oid) == []


def test_a_cancel_after_remaps_write_never_claims_a_unit(world, monkeypatch):
    """[LOW-MEDIUM] Short and seller-held at Bokaro, fixed and restocked; the
    cancel door lands after Re-map's write and before its claim. Re-map
    claimed Bokaro's unit SOLD against the CANCELLED order, told Bokaro to
    ship it and answered 'the hold is lifted'. Now every unit it claimed
    goes back, the ship task is closed, refused -- and (round 14) Shopify's
    stock is written back once more, after the units came back."""
    import api.services.online_stock_writeback as wb
    from api.services import shopify_ingest

    db = world["db"]
    payload, res, _o = _short_held_at_bokaro(world, 60002)
    oid = res["order_id"]
    real = shopify_ingest._claim_online_units
    wrote = []  # Bokaro's AVAILABLE units at each Shopify stock write-back
    monkeypatch.setattr(wb, "writeback_after_sale", lambda *a, **k: wrote.append(
        db.stock_units.count_documents({"store_id": "BV-BOK-01", "status": "AVAILABLE"})))

    def cancel_then_claim(*a, **k):
        _cancel_door(db, oid)
        return real(*a, **k)

    monkeypatch.setattr(shopify_ingest, "_claim_online_units", cancel_then_claim)
    out = asyncio.run(route_mod.reroute_held_order(db, oid))

    assert out["status"] == "refused" and "cancelled while Re-map ran" in out["message"], out
    assert _sold_at(db, oid) == []
    assert db.stock_units.count_documents({"status": "AVAILABLE"}) == 1
    assert world["tasks"].open_refs(f"online_fallback_ship:{oid}") == []
    assert wrote == [1]


def test_a_cancel_before_remaps_claim_leaves_no_unit_sold_to_the_dead_order(world, monkeypatch):
    """[LOW] Round 16, item 2: seller-held at Bokaro, 2 ordered, 1 claimed
    (U-BV-BOK-01-P-RB-0); the GSTIN fixed, Bokaro restocks one. The cancel
    door lands after Re-map's write and releases that unit; Re-map's claim
    keeps 1 by count and FIFO-claims the very same unit again. The undo
    spared the units Re-map kept, so it stayed SOLD to the CANCELLED order
    while Re-map said every unit was back on the shelf. Every unit sold to a
    dead order goes back now."""
    from api.services import shopify_ingest

    db = world["db"]
    db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": ""}})
    _stock(db, "BV-BOK-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK, units=2)
    payload = _order(60130, lines=(("RB-1234", 2),))
    res, order = _book(world, payload)
    oid = res["order_id"]
    assert [p["code"] for p in order["fulfillment_route"]["problems"]] == ["SHOP_GSTIN_MISSING"]
    assert _sold_at(db, oid) == ["BV-BOK-01"]
    db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": "20AAAAA0000A1Z5"}})
    db.stock_units.insert_one({"stock_id": "U-NEW", "product_id": "P-RB",
                               "store_id": "BV-BOK-01", "status": "AVAILABLE"})
    real = shopify_ingest._claim_online_units

    def cancel_then_claim(*a, **k):
        _cancel_door(db, oid)
        return real(*a, **k)

    monkeypatch.setattr(shopify_ingest, "_claim_online_units", cancel_then_claim)
    out = asyncio.run(route_mod.reroute_held_order(db, oid))

    assert out["status"] == "refused" and "cancelled while Re-map ran" in out["message"], out
    assert _sold_at(db, oid) == []
    assert db.stock_units.count_documents({"store_id": "BV-BOK-01", "status": "AVAILABLE"}) == 2
    assert "reroute_lease_at" not in db.orders.find_one({"order_id": oid})


@pytest.mark.parametrize("printed", [False, True])
def test_remap_resends_a_failed_move_to_the_shop_holding_the_unit(world, monkeypatch, printed):
    """[LOW] MOVE_FAILED at Ranchi (its unit packed); Bokaro still short; BV
    Dhanbad (same GSTIN) now holds 2. Re-map moved the claim to Dhanbad (most
    stock), gave Ranchi's packed unit back and re-billed -- or, printed,
    refused the very retry the hold's text tells the human to press. It
    resends the move to Ranchi now: same unit, same invoice."""
    db = world["db"]
    _shop(db, "BV-DHN-01", "BV Dhanbad", "20AAAAA0000A1Z5", LOC_DHN)
    payload, res, order = _move_failed_at_ranchi(world, 60010 + printed)
    oid = res["order_id"]
    _stock(db, "BV-DHN-01", "P-RB", 2)
    if printed:
        _invoice(world, monkeypatch, oid)

    out = _remap(world, monkeypatch, payload)

    assert out["ok"] and out["result"]["status"] == "rerouted", out
    assert world["shop"].moves()[-1] == {"id": FO_1, "newLocationId": LOC_RAN}
    after = db.orders.find_one({"order_id": oid}, {"_id": 0})
    assert after["store_id"] == "BV-RAN-01" and after["invoice_number"] == order["invoice_number"]
    assert _units(db, oid) == ["U-BV-RAN-01-P-RB-0"] and after["fulfillment_hold"] is False
    assert db.stock_units.count_documents({"store_id": "BV-DHN-01", "status": "AVAILABLE"}) == 2


def test_remap_keeps_a_short_claim_at_the_shop_holding_its_units(world, monkeypatch):
    """[LOW] Ranchi's claim is short (2 ordered, one of its units since sold
    at the till); Bokaro, where Shopify has it, holds nothing. Re-map fell
    back to empty Bokaro and gave Ranchi's unit back. It stays at Ranchi now,
    the unit kept, short and loud there."""
    db = world["db"]
    payload, res, order = _move_failed_at_ranchi(world, 60012, ranchi_units=2, qty=2)
    oid = res["order_id"]
    db.stock_units.update_one({"stock_id": "U-BV-RAN-01-P-RB-1"}, {"$set": {"order_id": "WALK-IN"}})

    out = _remap(world, monkeypatch, payload)

    assert out["ok"], out
    after = db.orders.find_one({"order_id": oid}, {"_id": 0})
    assert after["store_id"] == "BV-RAN-01" and after["invoice_number"] == order["invoice_number"]
    assert _units(db, oid) == ["U-BV-RAN-01-P-RB-0"] and after["fulfillment_hold"] is True
    [miss] = list(db.online_stock_miss.find({"order_id": oid, "resolved": False}))
    assert miss["store_id"] == "BV-RAN-01"


@pytest.mark.parametrize("door", ["clear_hold", "remap"])
@pytest.mark.parametrize("booked", ["return", "refund_review"])
def test_no_door_re_dates_a_seller_held_invoice_a_refund_stands_on(world, monkeypatch, door, booked):
    """[MEDIUM] Held on SHOP_GSTIN_MISSING at Bokaro, booked 40 days ago; a
    return or a refund queued for the accountant is stamped with the
    booking's invoice. Clear-hold re-dated the invoice to today -- the credit
    note filed a month before its invoice. Round 13: no door changes the
    invoice at all, so the hold lifts with the invoice as booked; Re-map,
    which would move the order's stock claims, still refuses."""
    from datetime import datetime, timedelta, timezone

    db = world["db"]
    payload, res, _o = _gstin_missing_at_bokaro(
        world, 60020 + 2 * (door == "remap") + (booked == "return"))
    oid = res["order_id"]
    booked_at = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0) - timedelta(days=40)
    db.orders.update_one({"order_id": oid}, {"$set": {"created_at": booked_at, "invoice_date": booked_at}})
    if booked == "return":
        db.returns.insert_one({"return_id": "RT-1", "order_id": oid, "store_id": "BV-BOK-01"})
    else:
        db.shopify_refund_review.insert_one({"review_id": "R-1", "order_id": oid,
                                             "shopify_order_id": str(payload["id"]), "status": "CONFIRMED"})
    db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": "20AAAAA0000A1Z5"}})
    before = db.orders.find_one({"order_id": oid}, {"_id": 0})

    if door == "remap":
        out = _remap(world, monkeypatch, payload)
        assert not out["ok"] and "refund or return" in out["message"], out
    else:
        assert _clear_hold(world, monkeypatch, oid)["released"] == ["SELLER"]
    after = db.orders.find_one({"order_id": oid}, {"_id": 0})
    for k in ("store_id", "invoice_number", "invoice_date", "created_at", "interstate", "tax_totals"):
        assert after.get(k) == before.get(k), k


def test_clear_hold_never_overwrites_a_remap_that_landed_after_its_read(world, monkeypatch):
    """[MEDIUM] Clear-hold read the seller-held order, then a Re-map on another
    worker put a new hold on it (a fulfillment-order move now pending). The
    release is conditioned on the order as read: 409, the new hold stands."""
    db = world["db"]
    _payload, res, _o = _gstin_missing_at_bokaro(world, 60030)
    oid = res["order_id"]
    db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": "20AAAAA0000A1Z5"}})
    pending = route_mod._pending_move_text(["BV-RAN-01"])
    real = route_mod.seller_change

    def remap_lands_then_check(*a, **k):
        db.orders.update_one({"order_id": oid}, {"$set": {"stock_hold_reason": pending}})
        return real(*a, **k)

    monkeypatch.setattr(route_mod, "seller_change", remap_lands_then_check)
    out = _clear_hold(world, monkeypatch, oid)

    assert getattr(out, "status_code", None) == 409 and "changed while the hold" in out.detail, out
    after = db.orders.find_one({"order_id": oid}, {"_id": 0})
    assert after["fulfillment_hold"] is True and after["stock_hold_reason"] == pending
    assert not after.get("rx_hold_cleared")


@pytest.mark.parametrize("shape", ["whole_order", "split_leg"])
def test_booking_never_moves_an_order_into_a_shop_failing_the_seller_check(world, shape):
    """[LOW-MEDIUM] Booking moved the order (or a short split leg) into the
    only shop holding it although it fails the seller check: Ranchi without
    a GSTIN (billed from it, held), or Pune under another GSTIN (IMS made the
    cross-GSTIN split itself). Nothing moves there now: the short shop stays
    short, loud at that shop."""
    db = world["db"]
    if shape == "whole_order":
        db.stores.update_one({"store_id": "BV-RAN-01"}, {"$set": {"gstin": ""}})
        _stock(db, "BV-RAN-01", "P-RB", 1)
        world["shop"].fo(FO_1, LOC_BOK)
        res, order = _book(world, _order(60050))
        short = "BV-BOK-01"
    else:
        db.stores.update_one({"store_id": PUNE}, {"$set": {"shopify_location_id": LOC_PUN}})
        _stock(db, "BV-BOK-01", "P-RB", 1)
        _stock(db, PUNE, "P-OA", 1)
        world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 1)])
        world["shop"].fo(FO_2, LOC_RAN, lines=[(9001, 1)])
        res, order = _book(world, _order(60051, lines=(("RB-1234", 1), ("OA-5", 1))))
        short = "BV-RAN-01"

    assert world["shop"].moves() == [] and order["store_id"] == "BV-BOK-01"
    codes = [p["code"] for p in order["fulfillment_route"]["problems"]]
    assert not {"SHOP_GSTIN_MISSING", "SPLIT_SELLERS"} & set(codes), codes
    [miss] = list(db.online_stock_miss.find({"order_id": res["order_id"]}))
    assert miss["store_id"] == short and order["fulfillment_hold"] is True


_CAS_LANDINGS = [
    ("stock_hold_reason", "Released by hand"),
    ("fulfillment_status", "PARTIAL"),
    ("shopify_fulfillment_id", "gid://shopify/Fulfillment/1"),
    ("rx_pending", True),
    ("fulfillment_hold", False),  # clear-hold on another worker
]


@pytest.mark.parametrize("field,value", _CAS_LANDINGS)
def test_remap_writes_only_on_the_order_it_checked(world, monkeypatch, field, value):
    """[LOW-MEDIUM] Each field Re-map's write is conditioned on, landing
    between its last check and its write (a hold release, an Rx flag, a
    fulfilment, a cancel): refused, nothing claimed."""
    db = world["db"]
    payload, res, order = _short_held_at_bokaro(
        world, 60070 + [f for f, _v in _CAS_LANDINGS].index(field))
    oid = res["order_id"]
    assert order.get(field) != value
    real = route_mod.route_order

    def land_then_route(*a, **k):
        db.orders.update_one({"order_id": oid}, {"$set": {field: value}})
        return real(*a, **k)

    monkeypatch.setattr(route_mod, "route_order", land_then_route)
    out = asyncio.run(route_mod.reroute_held_order(db, oid))

    assert out["status"] == "refused" and "changed while Re-map ran" in out["message"], out
    after = db.orders.find_one({"order_id": oid}, {"_id": 0})
    assert after["store_id"] == order["store_id"] and after["invoice_number"] == order["invoice_number"]
    assert _sold_at(db, oid) == [] and after[field] == value


def test_a_remap_short_at_another_shop_moves_the_stock_miss_task_there(world, monkeypatch):
    """[MEDIUM] Round 13, item 1: Shopify splits RB -> Bokaro, OA -> Pune
    (another GSTIN, no OA): SPLIT_SELLERS-held, the stock-miss task at Pune.
    The human moves Pune's fulfillment order to Ranchi, which has no OA
    either, and presses Re-map: short at Ranchi now. The task stayed at Pune
    (which ships nothing any more) and Ranchi was never told. Pune's task is
    closed and Ranchi's opened."""
    db = world["db"]
    db.stores.update_one({"store_id": PUNE}, {"$set": {"shopify_location_id": LOC_PUN}})
    _stock(db, "BV-BOK-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 1)])
    world["shop"].fo(FO_2, LOC_PUN, lines=[(9001, 1)])
    payload = _order(60120, lines=(("RB-1234", 1), ("OA-5", 1)))
    res, order = _book(world, payload)
    oid = res["order_id"]
    ref = f"online_stock_miss:{oid}"
    assert [p["code"] for p in order["fulfillment_route"]["problems"]] == ["SPLIT_SELLERS"]
    assert [t["store_id"] for t in world["tasks"].created if t["source_ref"] == ref] == [PUNE]
    world["shop"].fos[1]["assignedLocation"]["location"]["id"] = LOC_RAN  # the human's move

    out = _remap(world, monkeypatch, payload)

    assert out["ok"] and "still on hold" in out["message"], out
    [miss] = list(db.online_stock_miss.find({"order_id": oid, "resolved": False}))
    assert miss["store_id"] == "BV-RAN-01"
    tasks = [t for t in world["tasks"].created if t["source_ref"] == ref]
    assert [(t["store_id"], t["status"]) for t in tasks] == [(PUNE, "COMPLETED"), ("BV-RAN-01", "OPEN")]


@pytest.mark.parametrize("edited", [False, True])
def test_remap_never_frees_a_unit_shopify_shows_shipped(world, monkeypatch, edited):
    """[MEDIUM] Round 13, item 2: split RB -> Bokaro, OA -> Pune (another
    GSTIN): held, Pune's OA SOLD to the order. Pune's staff fulfil FO_2 in
    Shopify admin (CLOSED, nothing left on it) before IMS hears of it. Re-map
    answered 'rerouted': Pune's shipped OA went back on sale (a phantom the
    till sells again) and a second OA was claimed at Bokaro. It refuses now,
    the order untouched -- line by line: ``edited``, an order edit added a
    line IMS never booked, so the open UNITS still add up."""
    db = world["db"]
    payload, res, order = _split_sellers(world, 60130 + edited, bokaro_oa=1)
    oid = res["order_id"]
    before = _units(db, oid)
    world["shop"].fos[1]["status"] = "CLOSED"
    world["shop"].fos[1]["lineItems"]["nodes"][0]["remainingQuantity"] = 0
    if edited:
        world["shop"].fos[0]["lineItems"]["nodes"].append(
            {"remainingQuantity": 1, "lineItem": {"id": "gid://shopify/LineItem/9002"}})

    out = _remap(world, monkeypatch, payload)

    assert not out["ok"] and "fulfilled, refunded or closed" in out["message"], out
    assert _units(db, oid) == before
    assert db.stock_units.find_one({"store_id": "BV-BOK-01", "product_id": "P-OA"})["status"] == "AVAILABLE"
    after = db.orders.find_one({"order_id": oid}, {"_id": 0})
    assert after["fulfillment_hold"] is True and after["stock_hold_reason"] == order["stock_hold_reason"]


def test_a_line_ims_claims_nothing_for_never_blocks_remap(world, monkeypatch):
    """[LOW] Round 13, item 2's refusal counted EVERY order line: a gift card
    (no IMS product) Shopify fulfils at payment left Re-map refusing the
    seller-held order forever -- 'goods may have left' -- although Re-map
    moves only the frame's claim. Only the lines IMS claims count."""
    db = world["db"]
    db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": ""}})
    _stock(db, "BV-BOK-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 1)])
    world["shop"].fo(FO_2, LOC_BOK, lines=[(9001, 0)], status="CLOSED")  # the gift card
    payload = _order(60160, lines=(("RB-1234", 1), ("GIFT-1", 1)))
    res, order = _book(world, payload)
    assert [p["code"] for p in order["fulfillment_route"]["problems"]] == ["SHOP_GSTIN_MISSING"]
    db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": "20AAAAA0000A1Z5"}})

    out = _remap(world, monkeypatch, payload)

    assert out["ok"] and "the hold is lifted" in out["message"], out
    after = db.orders.find_one({"order_id": res["order_id"]}, {"_id": 0})
    assert after["invoice_number"] == order["invoice_number"] and after["fulfillment_hold"] is False
    assert _sold_at(db, res["order_id"]) == ["BV-BOK-01"]


def test_another_sender_of_a_remaps_move_never_reopens_its_closed_task(world, monkeypatch):
    """[LOW] Round 13, item 7: MOVE_FAILED at Ranchi, the human closed its
    task; Re-map retries the move and Shopify still refuses it. Another
    sender of the same PLANNED move (an orders/updated delivery, a second
    press) lands after Re-map's write and before Re-map's own send: it knew
    nothing of the closed task, and the MOVE_FAILED task came back OPEN. The
    tasked problems live on the route now: whoever sends, it stays closed."""
    payload, res, _o = _move_failed_at_ranchi(world, 60140)
    ref = f"online_route:MOVE_FAILED:{res['order_id']}"
    world["shop"].move_error = "Location does not stock the item"
    real = route_mod.move_fulfillment_orders

    async def another_sender_first(db, order_id, *_had):
        await real(db, order_id)  # the other sender: no word from Re-map
        return await real(db, order_id)

    monkeypatch.setattr(route_mod, "move_fulfillment_orders", another_sender_first)
    out = _remap(world, monkeypatch, payload)

    assert out["ok"] and "still on hold" in out["message"], out
    assert [t["status"] for t in world["tasks"].created if t["source_ref"] == ref] == ["COMPLETED"]


def test_remap_keeps_an_rx_pending_order_held(world, monkeypatch):
    """[LOW, hollow] Round 13, item 9: Re-map lifts a seller hold whose cause
    is fixed, but an order still waiting for its prescription stays held --
    the mutant dropping the Rx part survived the whole suite."""
    db = world["db"]
    payload, res, _o = _gstin_missing_at_bokaro(world, 60150)
    db.orders.update_one({"order_id": res["order_id"]}, {"$set": {"rx_pending": True}})
    db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": "20AAAAA0000A1Z5"}})

    out = _remap(world, monkeypatch, payload)

    assert out["ok"] and "the Rx hold stands" in out["message"], out
    after = db.orders.find_one({"order_id": res["order_id"]}, {"_id": 0})
    assert after["fulfillment_hold"] is True and after["rx_pending"] is True
    assert "stock_hold_reason" not in after


def test_a_remap_still_short_leaves_the_stock_miss_task_open(world, monkeypatch):
    """[LOW] Shopify assigns Bokaro, which has no stock and no GSTIN: booked
    short (stock-miss task open) and seller-held. The GSTIN is fixed and
    Re-map runs, still short. It closed the open task ('its stock claimed
    again') and opened an identical one. The one task stays open now."""
    db = world["db"]
    db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": ""}})
    world["shop"].fo(FO_1, LOC_BOK)
    payload = _order(60080)
    res, _o = _book(world, payload)
    oid = res["order_id"]
    ref = f"online_stock_miss:{oid}"
    assert world["tasks"].open_refs(ref) == [ref]
    db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": "20AAAAA0000A1Z5"}})

    out = _remap(world, monkeypatch, payload)

    assert out["ok"] and "still on hold" in out["message"], out
    [task] = [t for t in world["tasks"].created if t["source_ref"] == ref]
    assert task["status"] == "OPEN" and "completion_notes" not in task
    assert db.online_stock_miss.count_documents({"order_id": oid, "resolved": False}) == 1


def test_a_retried_move_refused_in_new_words_is_not_a_new_task(world, monkeypatch):
    """[LOW] The booking's move was refused ('does not stock the item') and
    the human closed the task; Re-map's retry of the same move to the same
    shop is refused in other words. The closed MOVE_FAILED task came back;
    it stays closed now -- the problem is its fulfillment orders and shop."""
    payload, res, _o = _move_failed_at_ranchi(world, 60090)
    ref = f"online_route:MOVE_FAILED:{res['order_id']}"
    world["shop"].move_error = "Fulfillment order is not in a movable state"

    out = _remap(world, monkeypatch, payload)

    assert out["ok"] and "still on hold" in out["message"], out
    assert [t["status"] for t in world["tasks"].created if t["source_ref"] == ref] == ["COMPLETED"]


# ---------------------------------------------------------------------------
# R20 -- money panel, round 14
# ---------------------------------------------------------------------------


def test_seller_check_never_rejudges_an_order_its_booking_passed(world, monkeypatch):
    """[MEDIUM] A clean Shopify split (RB at Bokaro, OA at Ranchi, one GSTIN)
    is booked with no hold; its invoice is filed in GSTR-1, GSTR-3B and the
    Tally JV. Later Ranchi's GSTIN is edited in Organization. The returns
    re-ran the seller check on today's shop records: the SAME month
    regenerated dropped the filed invoice from GSTR-1 ('on hold ... press
    Re-map'), GSTR-3B and Tally silently, the invoice door refused the
    reprint and the dispatch was refused -- while no exit worked (the order
    was never held). Every door reads the booking's verdict now: it files.
    The same for a billing shop whose state is edited after booking."""
    from api.routers.orders import assert_no_active_rx_hold
    from api.routers.reports.gst_itc import _cn_parent_held

    db = world["db"]
    _stock(db, "BV-BOK-01", "P-RB", 1)
    _stock(db, "BV-RAN-01", "P-OA", 1)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 1)])
    world["shop"].fo(FO_2, LOC_RAN, lines=[(9001, 1)])
    res, order = _book(world, _order(61030, lines=(("RB-1234", 1), ("OA-5", 1))))
    oid = res["order_id"]
    assert order["fulfillment_route"]["split"] and order["fulfillment_hold"] is False

    def filed():
        g1 = _gstr1(world, monkeypatch, order, "BV-BOK-01")
        return (len(g1["b2cs"]), [i for i in g1["validation"]["issues"] if i["level"] == "error"],
                _gstr3b(world, monkeypatch, order, "BV-BOK-01")["outwardTaxableValue"],
                oid in _tally_jv(world, monkeypatch, "BV-BOK-01"))

    before = filed()
    assert before[0] == 1 and before[1] == [] and before[2] > 0 and before[3]
    for shop, edit in (("BV-RAN-01", {"gstin": "20CCCCC0000C1Z5"}), ("BV-BOK-01", {"state_code": "27"})):
        db.stores.update_one({"store_id": shop}, {"$set": edit})

        assert filed() == before, edit
        assert _invoice_refusal(world, monkeypatch, oid) is None
        assert_no_active_rx_hold(db.orders.find_one({"order_id": oid}, {"_id": 0}))
        assert _cn_parent_held(db, {"order_id": oid}, {}) is False


@pytest.mark.parametrize("other_at", [LOC_PUNE_SHOPIFY, LOC_BOK])
def test_the_fulfillment_order_carrying_the_ims_item_moves_without_its_sibling(world, other_at):
    """[LOW-MEDIUM] RB (IMS) + a line IMS does not stock. Shopify puts FO_1
    (RB) at Bokaro, which holds none, and FO_2 (the other line) at unmapped
    Pune -- or at Bokaro too. Ranchi holds the RB, so the claim and the bill
    went there, but the all-or-nothing guard moved NOTHING and told the
    human FO_1 'carries no item IMS stocks'. FO_1 follows the claim now;
    only FO_2 is named, and the order stays held on it (the move lifts only
    its own pending-move hold)."""
    db = world["db"]
    _stock(db, "BV-RAN-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 1)])
    world["shop"].fo(FO_2, other_at, lines=[(9001, 1)], name="Other place")

    res, order = _book(world, _order(61001 + (other_at == LOC_BOK),
                                     lines=(("RB-1234", 1), ("NOT-IN-IMS", 1))))

    route = order["fulfillment_route"]
    assert order["store_id"] == "BV-RAN-01" and _sold_at(db, res["order_id"]) == ["BV-RAN-01"]
    assert world["shop"].moves() == [{"id": FO_1, "newLocationId": LOC_RAN}]
    assert [m["status"] for m in route["moves"]] == ["MOVED"]
    assert route["fulfillment_order_ids"] == [FO_1]
    [p] = route["problems"]
    assert p["code"] == "FO_AT_OTHER_SHOP" and "1 fulfillment order(s)" in p["message"]
    assert "because they carry no item IMS stocks" in p["message"] and "'Other place'" in p["message"]
    assert order["fulfillment_hold"] is True and order["stock_hold_reason"] == p["message"]


@pytest.mark.parametrize("bokaro_holds", [1, 0])
def test_nothing_moves_into_a_fallback_failing_the_seller_check(world, monkeypatch, bokaro_holds):
    """[LOW] ONLINE_FULFILLMENT_STORE_ID is Ranchi, which has a Shopify
    location but no GSTIN; Shopify assigns unmapped Pune. Ranchi held the
    unit, so it stayed the target: fulfillmentOrderMove(FO_1 -> Ranchi) was
    SENT and the order held on SHOP_GSTIN_MISSING although clean Bokaro could
    ship it. Bokaro ships it now; with no clean shop holding it, the order
    stays at Ranchi (held, loud) and nothing is moved into Ranchi."""
    db = world["db"]
    monkeypatch.setenv("ONLINE_FULFILLMENT_STORE_ID", "BV-RAN-01")
    db.stores.update_one({"store_id": "BV-RAN-01"}, {"$set": {"gstin": ""}})
    _stock(db, "BV-RAN-01", "P-RB", 1)
    _stock(db, "BV-BOK-01", "P-RB", bokaro_holds)
    world["shop"].fo(FO_1, LOC_PUNE_SHOPIFY, name="Pune warehouse")

    res, order = _book(world, _order(61010 + bokaro_holds))

    problems = order["fulfillment_route"]["problems"]
    if bokaro_holds:
        assert order["store_id"] == "BV-BOK-01" and _sold_at(db, res["order_id"]) == ["BV-BOK-01"]
        assert world["shop"].moves() == [{"id": FO_1, "newLocationId": LOC_BOK}]
        assert [p["code"] for p in problems] == ["LOCATION_UNMAPPED"]
        assert ("BV-RAN-01 (ONLINE_FULFILLMENT_STORE_ID) fails the seller (GSTIN) check, so IMS "
                "ships it from BV-BOK-01") in problems[0]["message"]
        assert order["fulfillment_hold"] is False
    else:
        assert order["store_id"] == "BV-RAN-01" and world["shop"].moves() == []
        assert [p["code"] for p in problems] == [
            "LOCATION_UNMAPPED", "FO_AT_OTHER_SHOP", "SHOP_GSTIN_MISSING"]
        assert "because BV-RAN-01 fails the seller (GSTIN) check" in problems[1]["message"]
        assert order["fulfillment_hold"] is True


def test_a_remap_with_no_move_writes_shopifys_stock_back(world, monkeypatch):
    """[LOW] Probe P3: Shopify split RB x2 -> Bokaro, OA -> Ranchi (short),
    the OA leg claimed at Dhanbad and its move refused. Ranchi is restocked;
    Re-map gives Dhanbad's OA back and claims Ranchi's -- no move. Without
    its write-back Shopify keeps the booking's numbers: Ranchi offers its
    now-sold OA, Dhanbad hides a free one, until the 01:00/09:00 pass."""
    import api.services.online_stock_writeback as wb

    db = world["db"]
    res, _o = _split_leg_move_refused(world, 61020)
    oid = res["order_id"]
    _stock(db, "BV-RAN-01", "P-OA", 1)
    wrote = []  # (Ranchi, Dhanbad) AVAILABLE OA at each Shopify stock write-back
    monkeypatch.setattr(wb, "writeback_after_sale", lambda *a, **k: wrote.append(tuple(
        db.stock_units.count_documents({"store_id": s, "product_id": "P-OA", "status": "AVAILABLE"})
        for s in ("BV-RAN-01", "BV-DHN-01"))))

    out = asyncio.run(route_mod.reroute_held_order(db, oid))

    assert out["status"] == "rerouted", out
    assert _sold_at(db, oid) == ["BV-BOK-01", "BV-BOK-01", "BV-RAN-01"]
    assert wrote == [(0, 1)]


class _Died(BaseException):
    """A worker killed mid-Re-map: no ``except Exception`` catches it."""


def test_a_remap_that_died_after_settling_its_claim_is_finished_by_the_next_press(world, monkeypatch):
    """[LOW] Split RB -> Bokaro, OA -> Pune (another GSTIN): held, Pune told
    to ship its OA. The human moves FO_2 to Bokaro (which holds an OA) and
    presses Re-map; it settles its claim (both units at Bokaro, Pune's OA
    back on the shelf) and dies before its tasks. The next press took the
    stale lease over but refused -- 'not held', its claim having settled --
    and cleared the lease: Pune's ship task stayed OPEN (a second OA packed
    for an order Bokaro ships), the SPLIT_SELLERS task too, Shopify's stock
    was never written back, and nothing offered Re-map again. It carries on
    now and finishes every one of them."""
    import api.services.online_stock_writeback as wb

    db = world["db"]
    _payload, res, _o = _split_sellers(world, 61040, bokaro_oa=1)
    oid = res["order_id"]
    ship, seller = f"online_fallback_ship:{oid}:{PUNE}", f"online_route:SPLIT_SELLERS:{oid}"
    assert world["tasks"].open_refs(ship) == [ship] and world["tasks"].open_refs(seller) == [seller]
    world["shop"].fos[1]["assignedLocation"]["location"]["id"] = LOC_BOK  # the human's move
    real_close = route_mod._close_tasks

    def die(*_a, **_k):
        raise _Died()

    monkeypatch.setattr(route_mod, "_close_tasks", die)
    with pytest.raises(_Died):
        asyncio.run(route_mod.reroute_held_order(db, oid))
    crashed = db.orders.find_one({"order_id": oid})
    assert crashed["fulfillment_stores"] == ["BV-BOK-01"] and crashed["fulfillment_hold"] is False
    # The dead worker's lease, gone stale.
    db.orders.update_one({"order_id": oid}, {"$set": {"reroute_lease_at": "2000-01-01T00:00:00+00:00"}})
    monkeypatch.setattr(route_mod, "_close_tasks", real_close)
    wrote = []
    monkeypatch.setattr(wb, "writeback_after_sale", lambda db_, items, store: wrote.append(store))

    out = asyncio.run(route_mod.reroute_held_order(db, oid))

    assert out["status"] == "rerouted", out
    assert _sold_at(db, oid) == ["BV-BOK-01", "BV-BOK-01"]
    assert db.stock_units.find_one({"store_id": PUNE, "product_id": "P-OA"})["status"] == "AVAILABLE"
    assert world["tasks"].open_refs(ship) == [] and world["tasks"].open_refs(seller) == []
    assert wrote == ["BV-BOK-01"]
    assert "reroute_lease_at" not in db.orders.find_one({"order_id": oid})


# ---------------------------------------------------------------------------
# R24 -- money panel, round 17
# ---------------------------------------------------------------------------


def _shopify_cancel(world, payload, refunded=False):
    """orders/cancelled -- a cancel made in Shopify -- through the mapper, as
    the webhook drain runs it: the status flips and NO unit is released (the
    refund's restock brings the order's units back)."""
    from api.services import online_order_mapper

    cancelled = {**payload, "cancelled_at": "2026-10-07T10:00:00+05:30"}
    if refunded:
        cancelled["financial_status"] = "refunded"
    online_order_mapper.map_shopify_order(cancelled, world["db"], topic="orders/cancelled")


def _refund_restock(world, monkeypatch, oid, lines):
    """The Shopify refund door's restock ('Restock items' ticked), called as
    shopify_refund calls it: the order's own store and the order itself."""
    from api.routers import returns
    from database.repositories.product_repository import StockRepository

    db = world["db"]
    monkeypatch.setattr(returns, "get_stock_repository", lambda: StockRepository(db.stock_units))
    monkeypatch.setattr(returns, "_get_db", lambda: db)
    order = db.orders.find_one({"order_id": oid})
    return returns._restock_good_items(
        [returns.ReturnLine(product_id=pid, return_qty=q, unit_price=892.0) for pid, q in lines],
        order["store_id"], f"RET-{oid}", order_id=oid, user_id="SYSTEM_SHOPIFY_REFUND",
        processing_store_id=None, order=order)


def _frames(db):
    """One stock row is one frame: (shop, product) per row."""
    return sorted((u["store_id"], u["product_id"]) for u in db.stock_units.find())


@pytest.mark.parametrize("held", ["move_failed", "gstin_fixed", "split_moved", "split_leg"])
def test_a_shopify_cancel_mid_remap_leaves_one_row_per_frame(world, monkeypatch, held):
    """[MEDIUM] Round 17, items 1 + 4: a cancel made in Shopify ('Restock
    items' ticked) lands after Re-map's write and before its claim. Shopify's
    cancel releases no unit: its refund's restock reactivates the units SOLD
    to the order and MINTS only what it cannot find. Re-map freed every unit
    sold to the dead order -- the IMS cancel door's rule -- so the restock
    minted: two AVAILABLE rows for one frame, sold at the till and written to
    Shopify. It never stamped where the units were either, so a split leg's
    unit was looked for at the billing shop. Re-map leaves a Shopify-dead
    order's units SOLD to it now, where it stamped them: one row per frame."""
    from api.services import shopify_ingest

    db = world["db"]
    if held == "move_failed":
        payload, res, _o = _move_failed_at_ranchi(world, 61101)
        lines = [("P-RB", 1)]
    elif held == "gstin_fixed":
        payload, res, _o = _gstin_missing_at_bokaro(world, 61102)
        db.stores.update_one({"store_id": "BV-BOK-01"}, {"$set": {"gstin": "20AAAAA0000A1Z5"}})
        lines = [("P-RB", 1)]
    elif held == "split_moved":  # Re-map gives Pune's OA back and claims Bokaro's
        payload, res, _o = _split_sellers(world, 61103, bokaro_oa=1)
        world["shop"].fos[1]["assignedLocation"]["location"]["id"] = LOC_BOK
        lines = [("P-RB", 1), ("P-OA", 1)]
    else:  # MOVE_FAILED on a split leg: the OA is Dhanbad's, the order Bokaro's
        res, _o = _split_leg_move_refused(world, 61104)
        payload = _order(61104, lines=(("RB-1234", 2), ("OA-5", 1)))
        world["shop"].move_error = None
        lines = [("P-RB", 2), ("P-OA", 1)]
    oid = res["order_id"]
    frames = _frames(db)
    real = shopify_ingest._claim_online_units

    def shopify_cancels_then_claim(*a, **k):
        _shopify_cancel(world, payload, refunded=held == "gstin_fixed")
        return real(*a, **k)

    monkeypatch.setattr(shopify_ingest, "_claim_online_units", shopify_cancels_then_claim)
    out = asyncio.run(route_mod.reroute_held_order(db, oid))

    assert out["status"] == "refused" and "cancelled while Re-map ran" in out["message"], out
    restocked = _refund_restock(world, monkeypatch, oid, lines)
    assert [r["minted"] for r in restocked["restocked"]] == [0] * len(lines), restocked
    assert _frames(db) == frames
    assert {u["status"] for u in db.stock_units.find()} == {"AVAILABLE"}


@pytest.mark.parametrize("cause", ["refund_mark", "part_closed", "shipped"])
def test_a_takeover_refused_for_good_settles_the_half_claim_loudly(world, monkeypatch, cause):
    """[LOW-MEDIUM] Round 17, item 5: a crashed Re-map left 1 of 2 units
    claimed and its lease; the press taking it over is refused for a cause
    no later press gets past -- a refund mark (a counter that never goes
    down), a line Shopify closed, a fulfilment. Every press answered '...
    resolve it by hand', clear-hold had no hold to clear (the crashed write
    lifted it) and the dispatch gate refused for good on the lease: no door
    out but a database edit. The half claim is settled as it stands now --
    the booking's own under-claim: a stock hold with its task -- and the
    lease comes off, so clear-hold is the door once a human resolved it."""
    from fastapi import HTTPException
    from api.routers.orders import assert_no_active_rx_hold

    db = world["db"]
    payload, oid = _crashed_mid_claim(world, 61110 + len(cause))
    if cause == "refund_mark":
        route_mod.mark_refund_or_return(db, oid)
    elif cause == "part_closed":
        world["shop"].fos[0]["lineItems"]["nodes"][0]["remainingQuantity"] = 1
    else:
        db.orders.update_one({"order_id": oid}, {"$set": {"fulfillment_status": "PARTIAL"}})

    out = _remap(world, monkeypatch, payload)

    assert not out["ok"] and "another Re-map" not in out["message"], out
    after = db.orders.find_one({"order_id": oid}, {"_id": 0})
    assert "reroute_lease_at" not in after and _sold_at(db, oid) == ["BV-BOK-01"]
    assert after["fulfillment_hold"] is True and after["stock_hold_reason"]
    assert after["fulfillment_breakdown"] == [{"product_id": "P-RB", "store_id": "BV-BOK-01", "qty": 1}]
    miss = db.online_stock_miss.find_one({"order_id": oid, "reason": "remap_stopped"})
    assert miss and miss["detail"]["expected"] == 2 and miss["detail"]["claimed"] == 1, miss
    assert world["tasks"].open_refs(f"online_stock_miss:{oid}") == [f"online_stock_miss:{oid}"]
    with pytest.raises(HTTPException, match="stock"):
        assert_no_active_rx_hold(after)
    assert _clear_hold(world, monkeypatch, oid)["released"] == ["STOCK"]
    assert_no_active_rx_hold(db.orders.find_one({"order_id": oid}, {"_id": 0}))


def test_a_half_claim_that_cannot_be_settled_keeps_the_lease(world, monkeypatch):
    """The settle above reads the units SOLD to the order; when that read
    fails nothing is recorded, so the lease -- the only mark of the half
    claim -- stays: still blocked, never dispatched with 1 of 2 claimed."""
    from fastapi import HTTPException
    from api.routers.orders import assert_no_active_rx_hold

    db = world["db"]
    payload, oid = _crashed_mid_claim(world, 61119)
    route_mod.mark_refund_or_return(db, oid)

    def unreadable(_oid):
        raise RuntimeError("stock_units unreadable")

    monkeypatch.setattr(route_mod, "_sold_units", unreadable)
    out = _remap(world, monkeypatch, payload)

    assert not out["ok"], out
    after = db.orders.find_one({"order_id": oid}, {"_id": 0})
    assert after.get("reroute_lease_at")
    assert not db.online_stock_miss.find_one({"order_id": oid, "reason": "remap_stopped"})
    with pytest.raises(HTTPException, match="a Re-map of it is running or stopped mid-way"):
        assert_no_active_rx_hold(after)


def test_a_remap_dead_with_its_move_on_the_wire_lets_go_once_its_claim_settled(world, monkeypatch):
    """[LOW-MEDIUM] Round 17, item 2: MOVE_FAILED at Ranchi; Re-map writes,
    claims, stamps its claim and dies with its move on the wire (SENDING),
    its lease stale. Every later press refused ('on the wire') and kept the
    lease, so once the human moved the order in Shopify admin and cleared the
    hold, the dispatch gate refused it forever. Its claim settled, the
    refused press lets the lease go."""
    from api.routers.orders import assert_no_active_rx_hold

    db = world["db"]
    payload, res, _o = _move_failed_at_ranchi(world, 61120)
    oid = res["order_id"]
    real = shopify_push._graphql

    async def die_on_the_wire(db_, query, variables):
        if "imsFulfillmentOrderMove" in query:
            raise _Died()
        return await real(db_, query, variables)

    monkeypatch.setattr(shopify_push, "_graphql", die_on_the_wire)
    with pytest.raises(_Died):
        asyncio.run(route_mod.reroute_held_order(db, oid))
    monkeypatch.setattr(shopify_push, "_graphql", real)
    crashed = db.orders.find_one({"order_id": oid})
    assert [m["status"] for m in crashed["fulfillment_route"]["moves"]] == ["SENDING"]
    assert "fulfillment_breakdown" in crashed and crashed["fulfillment_hold"] is True
    db.orders.update_one({"order_id": oid}, {"$set": {"reroute_lease_at": "2000-01-01T00:00:00+00:00"}})

    out = _remap(world, monkeypatch, payload)

    assert not out["ok"] and "on the wire" in out["message"], out
    assert "reroute_lease_at" not in db.orders.find_one({"order_id": oid})
    # The human moves it in Shopify admin and clears the hold, as its text says.
    assert _clear_hold(world, monkeypatch, oid)["released"] == ["STOCK"]
    assert_no_active_rx_hold(db.orders.find_one({"order_id": oid}, {"_id": 0}))


@pytest.mark.parametrize("door", ["ims", "shopify"])
def test_a_crashed_remaps_claim_on_a_cancelled_order_goes_by_its_cancel_door(world, monkeypatch, door):
    """A Re-map claimed 1 of 2 units and died; the order was then cancelled.
    The IMS cancel door's sweep ran before the dead run's mark, so the unit
    is still SOLD to a dead order. The press taking the lease over is refused
    for good (cancelled) and settles the claim by the cancel door's own rule:
    the IMS door's -- the unit back on the shelf -- or Shopify's -- the unit
    stays with the order, stamped where it is, and its refund's restock
    reactivates it (one row per frame, nothing minted)."""
    db = world["db"]
    payload, oid = _crashed_mid_claim(world, 61126 + (door == "shopify"))
    if door == "ims":
        from api.routers.orders.release import _claim_order_for_cancel
        from database.repositories.order_repository import OrderRepository

        assert _claim_order_for_cancel(OrderRepository(db.orders), oid,
                                       "Customer changed their mind", {"user_id": "u-counter"})
    else:
        _shopify_cancel(world, payload)
    frames = _frames(db)

    out = _remap(world, monkeypatch, payload)

    assert not out["ok"] and "CANCELLED" in out["message"], out
    after = db.orders.find_one({"order_id": oid})
    assert "reroute_lease_at" not in after
    if door == "ims":
        assert _sold_at(db, oid) == [] and after["fulfillment_breakdown"] == []
        return
    assert after["fulfillment_breakdown"] == [{"product_id": "P-RB", "store_id": "BV-BOK-01", "qty": 1}]
    restocked = _refund_restock(world, monkeypatch, oid, [("P-RB", 1)])
    assert [r["minted"] for r in restocked["restocked"]] == [0], restocked
    assert _frames(db) == frames and {u["status"] for u in db.stock_units.find()} == {"AVAILABLE"}


def test_a_remap_dead_after_giving_units_back_keeps_its_lease_though_stamped(world, monkeypatch):
    """[LOW-MEDIUM] Round 17, item 2's guard: a Re-map gives its units back
    BEFORE its write, so one dying in between leaves the booking's breakdown
    stamped with a unit no longer SOLD to the order. 'Stamped' is not
    'settled': a takeover refused (a 502) dropping the lease let a human's
    clear-hold dispatch it with nothing claimed. The claim stands only when
    every unit it names is SOLD to the order; the lease stays and the next
    press claims the unit again."""
    from fastapi import HTTPException
    from api.routers.orders import assert_no_active_rx_hold

    db = world["db"]
    payload, res, _o = _move_failed_at_ranchi(world, 61124)
    oid = res["order_id"]
    db.stock_units.update_one({"order_id": oid}, {"$set": {"status": "AVAILABLE", "order_id": None}})
    db.orders.update_one({"order_id": oid}, {"$set": {"reroute_lease_at": "2000-01-01T00:00:00+00:00"}})
    world["shop"].read_error = "502 Bad Gateway"

    out = _remap(world, monkeypatch, payload)

    assert not out["ok"] and "could not be read" in out["message"], out
    assert db.orders.find_one({"order_id": oid}).get("reroute_lease_at")
    assert _clear_hold(world, monkeypatch, oid)["released"] == ["STOCK"]
    with pytest.raises(HTTPException, match="a Re-map of it is running or stopped mid-way"):
        assert_no_active_rx_hold(db.orders.find_one({"order_id": oid}, {"_id": 0}))
    world["shop"].read_error = None

    out = _remap(world, monkeypatch, payload)

    assert out["ok"] and out["result"]["status"] == "rerouted", out
    assert _sold_at(db, oid) == ["BV-RAN-01"]
    assert "reroute_lease_at" not in db.orders.find_one({"order_id": oid})


@pytest.mark.parametrize("landing", ["an_rx_flag", "a_refund_mark"])
def test_a_takeover_whose_write_misses_keeps_or_settles_the_crashed_claim(world, monkeypatch, landing):
    """[LOW] Round 17, item 6: the crashed Re-map's claim unsettled, a change
    lands between the takeover's checks and its write, so the write matches
    nothing. An Rx flag is a change a later press gets past: the lease stays
    (stale), the order still blocked, never dispatched with 1 of 2 units
    claimed, and the next press carries on. A refund mark is not: the half
    claim is settled loudly at once (a stock hold, its task, no lease)."""
    from fastapi import HTTPException
    from api.routers.orders import assert_no_active_rx_hold

    db = world["db"]
    payload, oid = _crashed_mid_claim(world, 61130 + len(landing))
    real = route_mod.route_order

    def route_then_a_change(*a, **k):
        if landing == "an_rx_flag":
            db.orders.update_one({"order_id": oid}, {"$set": {"rx_pending": True}})
        else:
            route_mod.mark_refund_or_return(db, oid)
        return real(*a, **k)

    monkeypatch.setattr(route_mod, "route_order", route_then_a_change)
    out = _remap(world, monkeypatch, payload)
    monkeypatch.setattr(route_mod, "route_order", real)

    assert not out["ok"] and "changed while Re-map ran" in out["message"], out
    after = db.orders.find_one({"order_id": oid}, {"_id": 0})
    assert _sold_at(db, oid) == ["BV-BOK-01"]
    if landing == "a_refund_mark":
        assert "reroute_lease_at" not in after and after["fulfillment_hold"] is True
        assert db.online_stock_miss.find_one({"order_id": oid, "reason": "remap_stopped"})
        return
    assert after.get("reroute_lease_at")
    with pytest.raises(HTTPException, match="a Re-map of it is running or stopped mid-way"):
        assert_no_active_rx_hold(after)

    out = _remap(world, monkeypatch, payload)

    assert out["ok"] and out["result"]["status"] == "rerouted", out
    after = db.orders.find_one({"order_id": oid}, {"_id": 0})
    assert _sold_at(db, oid) == ["BV-BOK-01", "BV-BOK-01"] and "reroute_lease_at" not in after
    assert after["fulfillment_hold"] is True  # the Rx hold stands


def test_a_superadmin_credit_note_on_a_held_sale_is_not_filed_either(world, monkeypatch):
    """[MEDIUM] Round 17, item 3: the dark gate books the order SELLER_UNKNOWN
    at the online bucket, held off GSTR-1 and GSTR-3B; its hold text says
    'issue a credit note against invoice N'. The SUPERADMIN post-invoice
    credit note (CN- ref, no RET- id) was filed in CDNR and netted in
    GSTR-3B: output tax reversed on a supply never declared. The note's row
    names its order now, so the held sale's rule finds it."""
    from api.routers import finance as finance_mod
    from api.routers import returns
    from api.routers.orders import admin_edit
    from api.routers.orders.models import SuperadminInvoiceChange
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.order_repository import OrderRepository

    db = world["db"]
    monkeypatch.setattr(shopify_push, "_live_or_reason", lambda _db: (False, "writes_disabled"))
    _res, held = _book(world, _order(61140))
    assert held["store_id"] == "BV-ONLINE-01" and held["invoice_number"] and held["fulfillment_hold"]
    monkeypatch.setattr(admin_edit, "get_order_repository", lambda: OrderRepository(db.orders))
    monkeypatch.setattr(admin_edit, "_get_db", lambda: db)
    monkeypatch.setattr(admin_edit, "_write_order_edit_audit", lambda **k: None)
    monkeypatch.setattr(finance_mod, "check_period_locked", lambda *a, **k: None)
    monkeypatch.setattr(returns, "_get_db", lambda: db)
    monkeypatch.setattr(returns, "get_customer_repository", lambda: CustomerRepository(db.customers))

    out = asyncio.run(admin_edit.superadmin_invoice_change(
        held["order_id"],
        SuperadminInvoiceChange(mode="CREDIT_NOTE", reason="Half off, agreed", cart_discount_percent=50),
        current_user={"user_id": "u1", "roles": ["SUPERADMIN"], "active_store_id": "BV-ONLINE-01"}))

    assert out["note_type"] == "CREDIT_NOTE", out
    assert db.credit_note_ledger.find_one({"type": "ISSUED", "store_id": "BV-ONLINE-01"})["tax"] > 0
    filed = _gstr1(world, monkeypatch, held, "BV-ONLINE-01")
    assert filed["b2cs"] == [] and filed["cdnr"] == [], filed["cdnr"]
    assert [i for i in filed["validation"]["issues"] if "credit note" in i["issue"]]
    g3 = _gstr3b(world, monkeypatch, held, "BV-ONLINE-01")
    assert g3["outwardTaxableValue"] == 0.0
    assert g3["creditNotes"] == {"integratedTax": 0.0, "centralTax": 0.0,
                                 "stateTax": 0.0, "taxableValue": 0.0}, g3["creditNotes"]


# ---------------------------------------------------------------------------
# R26 -- money panel, round 19
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("landing", ["clear_hold", "remap_write"])
def test_a_move_on_the_wire_never_undoes_what_landed_meanwhile(world, monkeypatch, landing):
    """[LOW] Round 19, item 4: Shopify splits RB x3 + OA x1 -- FO_1 Bokaro
    (RB 2), FO_3 Dhanbad, a new Jharkhand shop with no GSTIN yet (RB 1),
    FO_2 Ranchi (OA 1). Ranchi is short, so the booking plans Ranchi ->
    Bokaro and holds the order SHOP_GSTIN_MISSING on Dhanbad's leg. While
    the booking's move is on the wire, the admin sets Dhanbad's GSTIN and
    presses clear-hold: released. The move then wrote back the WHOLE route it
    read before, erasing seller_released_at -- held off every GST door again
    with no hold flag to clear. The move writes only its own route fields
    now, and only on the moves it claimed (a Re-map's route written meanwhile
    stays too)."""
    from api.routers import online_store_orders as oso

    db = world["db"]
    _shop(db, "BV-DHN-01", "BV Dhanbad", "", LOC_DHN)
    _stock(db, "BV-BOK-01", "P-RB", 2)
    _stock(db, "BV-BOK-01", "P-OA", 1)
    _stock(db, "BV-DHN-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 2)])
    world["shop"].fo(FO_3, LOC_DHN, lines=[(9000, 1)])
    world["shop"].fo(FO_2, LOC_RAN, lines=[(9001, 1)])
    payload = _order(61240 + len(landing), lines=(("RB-1234", 3), ("OA-5", 1)))
    monkeypatch.setattr(oso, "_get_db", lambda: db)
    monkeypatch.setattr(oso, "_write_rx_hold_audit", lambda *a, **k: None)
    real = shopify_push._graphql
    landed = {}

    async def land_mid_move(db_, query, variables):
        if "imsFulfillmentOrderMove" in query and not landed:
            oid = db.orders.find_one({"shopify_order_id": str(payload["id"])})["order_id"]
            if landing == "clear_hold":
                db.stores.update_one({"store_id": "BV-DHN-01"}, {"$set": {"gstin": "20AAAAA0000A1Z5"}})
                landed["out"] = await oso.clear_rx_hold(
                    oid, None, current_user={"user_id": "u1", "roles": ["ADMIN"]})
            else:  # a Re-map's write: a whole new route, held on its own planned move
                pending = db.orders.find_one({"order_id": oid})["fulfillment_route"]["hold_reason"]
                landed["route"] = {"store_id": "BV-BOK-01", "problems": [], "hold_reason": pending,
                                   "moves": [{"fulfillment_order_id": FO_3, "to_location_id": LOC_BOK,
                                              "to_store_id": "BV-BOK-01", "status": "PLANNED"}],
                                   "rerouted_at": "2026-10-08T10:00:00+00:00"}
                db.orders.update_one({"order_id": oid}, {"$set": {
                    "fulfillment_route": landed["route"], "stock_hold_reason": pending,
                    "fulfillment_hold": True}})
        return await real(db_, query, variables)

    monkeypatch.setattr(shopify_push, "_graphql", land_mid_move)
    res, _order_doc = _book(world, payload)

    assert world["shop"].moves() == [{"id": FO_2, "newLocationId": LOC_BOK}]
    after = db.orders.find_one({"order_id": res["order_id"]}, {"_id": 0})
    if landing == "remap_write":  # its route and its hold stand
        assert after["fulfillment_route"] == landed["route"]
        assert after["fulfillment_hold"] is True
        assert after["stock_hold_reason"] == landed["route"]["hold_reason"]
        return
    assert "Seller (GSTIN) hold released" in landed["out"]["message"], landed
    assert after["fulfillment_route"].get(route_mod.SELLER_RELEASED)
    assert not route_mod.seller_held(after) and after["fulfillment_hold"] is False
    assert [m["status"] for m in after["fulfillment_route"]["moves"]] == ["MOVED"]


def _stale_lease(db, oid):
    """A worker hard-killed mid-Re-map leaves its lease, which goes stale."""
    db.orders.update_one({"order_id": oid}, {"$set": {"reroute_lease_at": "2000-01-01T00:00:00+00:00"}})


def _open_miss_tasks(world, oid):
    return [t["store_id"] for t in world["tasks"].created
            if t["source_ref"] == f"online_stock_miss:{oid}" and t["status"] == "OPEN"]


def test_a_settled_half_claim_tasks_the_shop_short_now(world, monkeypatch):
    """[LOW] Round 19, item 3: Shopify splits RB -> Bokaro, OA -> Pune
    (another GSTIN, no OA): booked short at Pune (its task) and held
    SPLIT_SELLERS. The human moves FO_2 to Bokaro, which gets an OA; Re-map
    writes the whole order at Bokaro and dies before its claim; a refund mark
    lands. The takeover settled the claim as it stands, its miss naming
    Bokaro -- but the one stock-miss task per order was Pune's, still open,
    for a leg Pune no longer ships: Bokaro's manager was never told. The
    settle answers the booking's miss as Re-map's own claim does."""
    from api.services import shopify_ingest

    db = world["db"]
    db.stores.update_one({"store_id": PUNE}, {"$set": {"shopify_location_id": LOC_PUN}})
    _stock(db, "BV-BOK-01", "P-RB", 1)
    world["shop"].fo(FO_1, LOC_BOK, lines=[(9000, 1)])
    world["shop"].fo(FO_2, LOC_PUN, lines=[(9001, 1)])
    payload = _order(61220, lines=(("RB-1234", 1), ("OA-5", 1)))
    res, order = _book(world, payload)
    oid = res["order_id"]
    assert [p["code"] for p in order["fulfillment_route"]["problems"]] == ["SPLIT_SELLERS"]
    assert _open_miss_tasks(world, oid) == [PUNE]
    world["shop"].fos[1]["assignedLocation"]["location"]["id"] = LOC_BOK  # the human's move
    _stock(db, "BV-BOK-01", "P-OA", 1)
    real = shopify_ingest._claim_online_units

    def die(*_a, **_k):
        raise _Died()

    monkeypatch.setattr(shopify_ingest, "_claim_online_units", die)
    with pytest.raises(_Died):
        asyncio.run(route_mod.reroute_held_order(db, oid))
    monkeypatch.setattr(shopify_ingest, "_claim_online_units", real)
    _stale_lease(db, oid)
    route_mod.mark_refund_or_return(db, oid)

    out = _remap(world, monkeypatch, payload)

    assert not out["ok"], out
    miss = db.online_stock_miss.find_one({"order_id": oid, "reason": "remap_stopped"})
    assert miss and miss["store_id"] == "BV-BOK-01", miss
    assert _open_miss_tasks(world, oid) == ["BV-BOK-01"]
    booked = db.online_stock_miss.find_one({"order_id": oid, "store_id": PUNE})
    assert booked["resolved"] is True and booked["resolution"] == "SUPERSEDED"


def test_a_settled_claim_short_at_two_shops_tasks_the_first(world, monkeypatch):
    """Round 19 follow-up (mutant S6): the shop the settle hands the miss to
    when two are short. Split RB -> Bokaro, OA -> Pune, held; Shopify then
    has the OA at Bokaro (the billing shop, its first fulfillment order) and
    the RB at Ranchi. Re-map gives both units back, writes and dies before
    its claim; a refund mark lands. Short at Bokaro and Ranchi: the miss and
    its task are Bokaro's (the first), naming both."""
    from api.services import shopify_ingest

    db = world["db"]
    payload, res, _o = _split_sellers(world, 61230, bokaro_oa=1)
    oid = res["order_id"]
    _stock(db, "BV-RAN-01", "P-RB", 1)
    world["shop"].fos[0]["assignedLocation"]["location"]["id"] = LOC_RAN
    world["shop"].fos[1]["assignedLocation"]["location"]["id"] = LOC_BOK
    world["shop"].fos.reverse()
    real = shopify_ingest._claim_online_units

    def die(*_a, **_k):
        raise _Died()

    monkeypatch.setattr(shopify_ingest, "_claim_online_units", die)
    with pytest.raises(_Died):
        asyncio.run(route_mod.reroute_held_order(db, oid))
    monkeypatch.setattr(shopify_ingest, "_claim_online_units", real)
    assert _sold_at(db, oid) == []
    _stale_lease(db, oid)
    route_mod.mark_refund_or_return(db, oid)

    out = _remap(world, monkeypatch, payload)

    assert not out["ok"], out
    miss = db.online_stock_miss.find_one({"order_id": oid, "reason": "remap_stopped"})
    assert miss["store_id"] == "BV-BOK-01" and miss["detail"]["short_stores"] == ["BV-BOK-01", "BV-RAN-01"]
    assert _open_miss_tasks(world, oid) == ["BV-BOK-01"]
