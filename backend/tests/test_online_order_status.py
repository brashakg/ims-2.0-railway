"""
Owner rulings 2026-09-28 -- ONE rule for an online order's status
(api.services.online_order_status): every Shopify / courier event reduces to
one fact, and one table says what the fact does to the status IMS holds.

  1. Shopify "fulfilled" means SHIPPED; DELIVERED only from the courier.
  2. DELIVERED + a Shopify cancel / refund / delete stays DELIVERED, with ONE
     task for a person.
  3. A Shopify edit never moves an order backwards.
  A finished order (CANCELLED / REFUNDED / VOID) stays finished.

Every rule here goes red when that rule alone is reverted.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.services import online_order_status as oos  # noqa: E402

FACTS = (oos.SHIP, oos.DELIVER, oos.CANCEL, oos.REFUND, oos.DELETE)
_W, _C = (None, "withheld"), (None, "conflict")
_OPEN = {
    oos.SHIP: ("SHIPPED", None), oos.DELIVER: ("DELIVERED", None),
    oos.CANCEL: ("CANCELLED", None), oos.REFUND: ("REFUNDED", None), oos.DELETE: ("VOID", None),
}

# (status IMS holds) -> {fact: decide() answer}. The whole rule, cell by cell.
GOLDEN = {
    "CONFIRMED": _OPEN,
    "PROCESSING": _OPEN,
    "READY": _OPEN,
    "SHIPPED": {**_OPEN, oos.SHIP: (None, None)},
    "DELIVERED": {oos.SHIP: (None, None), oos.DELIVER: (None, None),
                  oos.CANCEL: _C, oos.REFUND: _C, oos.DELETE: _C},
    "CANCELLED": {**{f: _W for f in FACTS}, oos.CANCEL: (None, None)},
    "REFUNDED": {**{f: _W for f in FACTS}, oos.REFUND: (None, None)},
    "VOID": {**{f: _W for f in FACTS}, oos.DELETE: (None, None)},
    "VOIDED": {**{f: _W for f in FACTS}, oos.DELETE: (None, None)},
    "DRAFT": {f: (None, None) for f in FACTS},
    "HISTORICAL": {f: (None, None) for f in FACTS},
}


@pytest.mark.parametrize("status", sorted(GOLDEN))
@pytest.mark.parametrize("fact", FACTS)
def test_every_cell_of_the_table(status, fact):
    assert oos.decide({"status": status}, fact) == GOLDEN[status][fact]
    # Case / whitespace on the stored status never changes the answer.
    assert oos.decide({"status": f" {status.lower()} "}, fact) == GOLDEN[status][fact]


def test_no_fact_writes_nothing():
    for status in GOLDEN:
        assert oos.decide({"status": status}, None) == (None, None)


_RANK = {"CONFIRMED": 0, "PROCESSING": 1, "READY": 2, "SHIPPED": 3,
         "DELIVERED": 4, "CANCELLED": 4, "REFUNDED": 4, "VOID": 4}


def test_the_table_only_moves_forward_and_finished_rows_are_empty():
    for row, cells in oos.TABLE.items():
        for cell in cells.values():
            if cell in (oos.KEEP, oos.TASK):
                continue
            assert cell not in ("CONFIRMED", "PROCESSING", "READY"), (row, cell)  # ruling 3
            assert _RANK[cell] >= _RANK[row], (row, cell)
    assert set(oos.TABLE["DELIVERED"].values()) == {oos.TASK}  # ruling 2
    for row in ("CANCELLED", "REFUNDED", "VOID", "VOIDED"):
        assert oos.TABLE.get(row, {}) == {}, row  # a finished order stays finished


def test_only_deliver_leads_to_delivered():
    """Ruling 1: DELIVERED is reached by the DELIVER fact and nothing else."""
    for row, cells in oos.TABLE.items():
        for fact, cell in cells.items():
            if cell == "DELIVERED":
                assert fact == oos.DELIVER, (row, fact)


@pytest.mark.parametrize("body, ful_stale, fact", [
    ({"cancelled_at": "x", "financial_status": "refunded", "fulfillment_status": "fulfilled"}, False, oos.CANCEL),
    ({"financial_status": "refunded", "fulfillment_status": "fulfilled"}, False, oos.REFUND),
    ({"financial_status": "paid", "fulfillment_status": "fulfilled"}, False, oos.SHIP),
    ({"financial_status": "paid", "fulfillment_status": "fulfilled"}, True, None),
    ({"financial_status": "paid"}, False, None),
    ({"financial_status": "partially_refunded"}, False, None),
    ({"financial_status": "pending", "fulfillment_status": "partial"}, False, None),
    ({"fulfillment_status": "restocked"}, False, None),
    ({"note": "gift wrap"}, False, None),
])
def test_order_fact(body, ful_stale, fact):
    assert oos.order_fact(body, ful_stale=ful_stale) == fact


@pytest.mark.parametrize("ful, shipment, awb, fact", [
    ("FULFILLED", "delivered", "AWB", oos.DELIVER),
    ("PARTIAL", "delivered", "", oos.DELIVER),
    ("FULFILLED", "in_transit", "AWB", oos.SHIP),
    ("FULFILLED", "", "", oos.SHIP),
    ("PARTIAL", "", "AWB", oos.SHIP),
    ("PARTIAL", "", "", None),
    ("CANCELLED", "delivered", "AWB", None),
    ("CANCELLED", "", "AWB", None),
    ("ERROR", "", "AWB", None),
])
def test_fulfilment_fact(ful, shipment, awb, fact):
    assert oos.fulfilment_fact(ful, shipment, awb) == fact


@pytest.mark.parametrize("courier, fact", [
    ("DELIVERED", oos.DELIVER), ("Delivered", oos.DELIVER), (" delivered ", oos.DELIVER),
    ("RTO DELIVERED", None), ("RTO Delivered", None), ("OUT FOR DELIVERY", None),
    ("UNDELIVERED", None), (None, None), ("", None),
])
def test_courier_fact_is_an_exact_delivered(courier, fact):
    assert oos.courier_fact(courier) == fact


# ---------------------------------------------------------------------------
# End to end: every leg (the mapper on the drain and the sweep, the fulfilment
# reconcile, the delete handler) decides through the table. Rig: the status
# catch-up `swept` fixture (mapper FakeDB + the REAL mapper / ingest / refund
# handler, a faked Shopify fetch).
# ---------------------------------------------------------------------------

import copy  # noqa: E402

from test_online_order_mapper import wired  # noqa: E402,F401 (fixture chain)
from test_shopify_order_catchup import _pulled, pull  # noqa: E402,F401 (fixture chain)
from test_shopify_status_catchup import (  # noqa: E402,F401
    CANCELLED_AT,
    _book,
    _doc,
    _fulfilment,
    swept,
)

from api.services import shopify_fulfillment, shopify_order_delete  # noqa: E402

LATER = "2026-09-06T02:00:00Z"


def _set(swept, oid, **fields):
    swept["orders"].update_one({"shopify_order_id": str(oid)}, {"$set": fields})


def _tasks(swept, oid, task_type):
    return swept["db"]["tasks"].count_documents(
        {"order_id": _doc(swept, oid)["order_id"], "task_type": task_type})


def test_shopify_fulfilled_is_shipped_on_the_drain_and_the_sweep(swept):
    """Ruling 1: a fulfilled body (no fulfilment rows: the mapper's own fact)
    lands SHIPPED -- never DELIVERED, no delivered_at -- on both paths."""
    _book(swept, 60020)
    _book(swept, 60021)
    res = swept["real_map"](_pulled(60020, fulfillment_status="fulfilled"), swept["db"],
                            webhook_id="ful-60020", topic="orders/fulfilled")
    assert res["status_synced"] is True
    swept["state"]["orders"] = [_pulled(60021, fulfillment_status="fulfilled")]
    assert swept["run"]().payload["status_synced"] == ["60021"]

    for oid in (60020, 60021):
        doc = _doc(swept, oid)
        assert (doc["status"], doc["fulfillment_status"]) == ("SHIPPED", "FULFILLED")
        assert "delivered_at" not in doc
        last = doc["status_history"][-1]
        assert (last["status"], last["changed_by"]) == ("SHIPPED", "system:ONLINE_MAP")


def test_a_courier_delivered_fulfilment_is_the_delivery(swept):
    """Ruling 1: DELIVERED comes from shipment_status 'delivered', through the
    staff door's own claim -- delivered_at and a status_history entry land."""
    _book(swept, 60010)
    res = shopify_fulfillment.reconcile_fulfillment(
        swept["db"], _fulfilment(60010, 1, shipment_status="delivered"), topic="fulfillments/update")

    assert res["order_status"] == "DELIVERED" and res["terminal_withheld"] is False
    doc = _doc(swept, 60010)
    assert doc["status"] == "DELIVERED" and doc["delivered_at"]
    assert doc["status_updated_by"] == "system:SHOPIFY_FULFILL"
    assert [(h["status"], h["changed_by"]) for h in doc["status_history"]] == [
        ("DELIVERED", "system:SHOPIFY_FULFILL")]


def test_a_delivered_order_shopify_calls_fulfilled_is_no_disagreement(swept):
    """Ruling 1: fulfilled (= shipped) is behind DELIVERED, not against it. A
    sweep whose body is fulfilled with a courier-delivered fulfilment lands
    DELIVERED and reports nothing; a counter-delivered pickup order Shopify
    then marks fulfilled is not reported as IMS and Shopify disagreeing."""
    _book(swept, 60015)
    swept["state"]["orders"] = [_pulled(60015, fulfillment_status="fulfilled",
                                        fulfillments=[_fulfilment(60015, 1, shipment_status="delivered")])]
    p = swept["run"]().payload
    assert p["status_synced"] == ["60015"] and p["status_skipped_terminal"] == []
    assert _doc(swept, 60015)["status"] == "DELIVERED"

    _book(swept, 60016)
    _set(swept, 60016, status="DELIVERED")
    res = swept["real_map"](_pulled(60016, fulfillment_status="fulfilled"), swept["db"],
                            webhook_id="ful-60016", topic="orders/fulfilled")
    assert res["status_synced"] is True and res["terminal_withheld"] is False
    assert _doc(swept, 60016)["status"] == "DELIVERED"


def test_a_pickup_order_shopify_fulfilled_is_delivered_at_the_counter(swept, monkeypatch):
    """Ruling 1, the counter half: a click-and-collect order at READY that
    Shopify marks fulfilled (picked up) is SHIPPED, and the counter's Mark
    Delivered takes it to DELIVERED through the same claim. The door used to
    refuse anything but READY, so nothing ever delivered it."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routers import orders as om
    from api.routers import workshop as wm
    from api.routers.auth import get_current_user
    from database.repositories.order_repository import OrderRepository

    _book(swept, 60017)
    _set(swept, 60017, status="READY")
    swept["real_map"](_pulled(60017, fulfillment_status="fulfilled"), swept["db"],
                      webhook_id="ful-60017", topic="orders/fulfilled")
    doc = _doc(swept, 60017)
    assert doc["status"] == "SHIPPED"

    class _NoJobs:
        def find_by_id(self, _):
            return None

        def find_by_order(self, _):
            return []

    monkeypatch.setattr(om, "get_order_repository", lambda: OrderRepository(swept["orders"]))
    monkeypatch.setattr(wm, "get_workshop_repository", lambda: _NoJobs())
    app = FastAPI()
    app.include_router(om.router, prefix="/api/v1/orders")

    async def _user():
        return {"user_id": "u1", "username": "counter", "roles": ["SALES_CASHIER"],
                "store_ids": [doc["store_id"]], "active_store_id": doc["store_id"]}

    app.dependency_overrides[get_current_user] = _user
    resp = TestClient(app).post(f"/api/v1/orders/{doc['order_id']}/deliver")

    assert resp.status_code == 200, resp.text
    doc = _doc(swept, 60017)
    assert doc["status"] == "DELIVERED" and doc["delivered_at"]
    assert [(h["status"], h["changed_by"]) for h in doc["status_history"]] == [
        ("SHIPPED", "system:ONLINE_MAP"), ("DELIVERED", "u1")]


def test_a_cancelled_fulfilment_with_tracking_never_ships(swept):
    _book(swept, 60070)
    res = shopify_fulfillment.reconcile_fulfillment(
        swept["db"], _fulfilment(60070, 1, status="cancelled"), topic="fulfillments/update")

    assert res["order_status"] == "CONFIRMED"
    doc = _doc(swept, 60070)
    assert (doc["status"], doc["fulfillment_status"], doc["awb"]) == ("CONFIRMED", "CANCELLED", "AWB60070")
    assert "status_history" not in doc


@pytest.mark.parametrize("staff_status, body_over", [
    ("READY", {"financial_status": "paid"}),
    ("PROCESSING", {"note": "gift wrap please", "financial_status": "pending"}),
    ("SHIPPED", {"fulfillment_status": "partial"}),
    ("READY", {"fulfillment_status": None}),
])
def test_a_shopify_edit_never_moves_an_order_backwards(swept, staff_status, body_over):
    """Ruling 3: a body that states no lifecycle fact writes no status -- a
    staff-set PROCESSING / READY / SHIPPED is never reset to CONFIRMED, on the
    drain or the sweep, and nothing is pushed to the history."""
    for oid in (60030, 60031):
        _book(swept, oid, financial_status="pending")
        _set(swept, oid, status=staff_status)
    res = swept["real_map"](_pulled(60030, updated_at=LATER, **body_over), swept["db"],
                            webhook_id="edit-60030", topic="orders/updated")
    assert res["status_synced"] is True
    swept["state"]["orders"] = [_pulled(60031, updated_at=LATER, **body_over)]
    swept["run"]()

    for oid in (60030, 60031):
        doc = _doc(swept, oid)
        assert doc["status"] == staff_status and "status_history" not in doc


def test_a_staff_cancelled_order_is_never_flipped_to_refunded(swept):
    """Finding (a): staff cancelled at the counter; Shopify then refunds. The
    money lands (REFUNDED), the status stays CANCELLED, and the operator is
    told (terminal_withheld) -- on the drain and the sweep."""
    for oid in (60040, 60041):
        _book(swept, oid)
        _set(swept, oid, status="CANCELLED", cancelled_by="staff-1",
             cancelled_at="2026-09-05T10:00:00Z")
    res = swept["real_map"](_pulled(60040, financial_status="refunded"), swept["db"],
                            webhook_id="ref-60040", topic="orders/updated")
    assert res["terminal_withheld"] is True
    swept["state"]["orders"] = [_pulled(60041, financial_status="refunded")]
    assert swept["run"]().payload["status_skipped_terminal"] == ["60041"]

    for oid in (60040, 60041):
        doc = _doc(swept, oid)
        assert (doc["status"], doc["payment_status"]) == ("CANCELLED", "REFUNDED")
        assert "status_history" not in doc


@pytest.mark.parametrize("leg", ["mapper", "reconcile"])
def test_a_held_order_the_table_asks_finished_first_then_the_hold(swept, leg):
    """Finding (b): both legs ask the same function, finished status first. A
    held CANCELLED order is kept (reported) and raises no Rx task; a held
    CONFIRMED order is kept and raises exactly one."""
    for oid, status in ((60050, "CANCELLED"), (60051, "CONFIRMED")):
        _book(swept, oid)
        _set(swept, oid, status=status, rx_pending=True, fulfillment_hold=True,
             rx_hold_reasons=["RX_MISSING"])

    def feed(oid, n):
        if leg == "mapper":
            return swept["real_map"](_pulled(oid, fulfillment_status="fulfilled"), swept["db"],
                                     webhook_id=f"ful-{oid}-{n}", topic="orders/fulfilled")
        return shopify_fulfillment.reconcile_fulfillment(swept["db"], _fulfilment(oid, n))

    assert feed(60050, 1)["terminal_withheld"] is True
    assert _doc(swept, 60050)["status"] == "CANCELLED"
    assert _tasks(swept, 60050, "online_rx_hold") == 0
    for n in (1, 2):
        assert feed(60051, n)["terminal_withheld"] is False
    assert _doc(swept, 60051)["status"] == "CONFIRMED"
    assert _tasks(swept, 60051, "online_rx_hold") == 1


@pytest.mark.parametrize("finished", ["CANCELLED", "REFUNDED"])
def test_a_delete_on_a_finished_order_keeps_it(swept, finished):
    """Finance leaves VOID in revenue: voiding a cancelled / refunded order
    would count it again. The delete marker still lands."""
    _book(swept, 60060)
    _set(swept, 60060, status=finished)
    res = shopify_order_delete.handle_shopify_order_delete(swept["db"], {"id": 60060}, topic="orders/delete")

    assert res["status"] == "kept" and res["terminal_withheld"] is True and res["conflict_task"] is False
    doc = _doc(swept, 60060)
    assert doc["status"] == finished and doc["shopify_deleted_at"]
    assert "status_before_void" not in doc and "void_reason" not in doc


def test_an_open_order_deleted_on_shopify_is_voided_through_the_claim(swept):
    _book(swept, 60061)
    _set(swept, 60061, status="SHIPPED")
    res = shopify_order_delete.handle_shopify_order_delete(swept["db"], {"id": 60061}, topic="orders/delete")

    assert res["status"] == "voided" and res["status_before_void"] == "SHIPPED"
    doc = _doc(swept, 60061)
    assert (doc["status"], doc["status_before_void"], doc["void_reason"]) == (
        "VOID", "SHIPPED", "Shopify orders/delete webhook")
    assert doc["status_history"][-1]["changed_by"] == "system:SHOPIFY_ORDER_DELETE"


# ---------------------------------------------------------------------------
# An event's markers ride its status claim: a write that fails (a Mongo
# failover inside the claim) leaves no marker, so the event is retried -- by
# the re-delivered webhook, or by the hourly sweep that sees the fact unapplied.
# ---------------------------------------------------------------------------


@pytest.fixture
def blip(monkeypatch):
    """The status claim raises once, then works."""
    from api.routers.orders import release

    real, left = release._claim_order_status, [1]

    def claim(*a, **kw):
        if left and left.pop():
            raise RuntimeError("mongo failover")
        return real(*a, **kw)

    monkeypatch.setattr(release, "_claim_order_status", claim)


def test_a_blip_in_the_delete_claim_is_retried_by_the_redelivery(swept, blip):
    _book(swept, 60130)
    first = shopify_order_delete.handle_shopify_order_delete(swept["db"], {"id": 60130}, topic="orders/delete")
    assert first["status"] == "error"
    doc = _doc(swept, 60130)
    assert doc["status"] == "CONFIRMED" and "shopify_deleted_at" not in doc

    again = shopify_order_delete.handle_shopify_order_delete(swept["db"], {"id": 60130}, topic="orders/delete")
    assert again["status"] == "voided"
    doc = _doc(swept, 60130)
    assert doc["status"] == "VOID" and doc["shopify_deleted_at"]


def test_a_blip_in_the_cancel_claim_is_refed_by_the_sweep(swept, blip):
    _book(swept, 60140)
    body = _pulled(60140, cancelled_at=CANCELLED_AT)
    res = swept["real_map"](copy.deepcopy(body), swept["db"], webhook_id="c-60140", topic="orders/cancelled")
    assert res["status_synced"] is False, "a failed write is never reported as synced"
    doc = _doc(swept, 60140)
    assert doc["status"] == "CONFIRMED" and "shopify_cancelled_at" not in doc

    swept["state"]["orders"] = [copy.deepcopy(body)]
    assert swept["run"]().payload["status_synced"] == ["60140"]
    doc = _doc(swept, 60140)
    assert (doc["status"], doc["shopify_cancelled_at"], doc["cancelled_at"]) == (
        "CANCELLED", CANCELLED_AT, CANCELLED_AT)


def test_a_blip_in_the_fulfilment_claim_is_refed_by_the_sweep(swept, blip):
    _book(swept, 60150)
    f = _fulfilment(60150, 1)
    res = shopify_fulfillment.reconcile_fulfillment(swept["db"], copy.deepcopy(f), topic="fulfillments/create")
    assert res["status"] == "error"
    doc = _doc(swept, 60150)
    assert doc["status"] == "CONFIRMED" and "awb" not in doc and "shopify_fulfillment_id" not in doc

    swept["state"]["orders"] = [_pulled(60150, fulfillment_status="fulfilled", fulfillments=[f])]
    assert swept["run"]().payload["status_synced"] == ["60150"]
    doc = _doc(swept, 60150)
    assert (doc["status"], doc["awb"], doc["fulfillment_status"]) == ("SHIPPED", "AWB60150", "FULFILLED")


def test_three_lost_races_write_nothing_and_say_so(swept, monkeypatch):
    from api.routers.orders import release

    _book(swept, 60160)
    monkeypatch.setattr(release, "_claim_order_status", lambda *a, **kw: False)
    res = oos.apply_fact(swept["db"], _doc(swept, 60160), oos.SHIP, source="T", marks={"awb": "A1"})
    assert res["failed"] is True and res["to"] is None
    assert "awb" not in _doc(swept, 60160)


@pytest.mark.parametrize("variant", ["cancel", "refund"])
def test_a_delivered_order_shopify_cancels_stays_delivered_with_one_task_forever(swept, variant):
    """Ruling 2: DELIVERED stays DELIVERED; ONE task for a person, claimed by
    a marker on the order -- the drain twice, two sweeps, a remap, an
    orders/delete, and a replay after the task was closed raise no second
    one, and the sweep does not re-feed the order every hour."""
    oid = 60080
    _book(swept, oid)
    _set(swept, oid, status="DELIVERED", fulfillment_status="FULFILLED")
    over = {"financial_status": "refunded", "fulfillment_status": "fulfilled"}
    if variant == "cancel":
        over["cancelled_at"] = CANCELLED_AT
    body = _pulled(oid, **over)
    topic = "orders/cancelled" if variant == "cancel" else "orders/updated"

    for n in (1, 2):
        res = swept["real_map"](copy.deepcopy(body), swept["db"], webhook_id=f"w-{n}", topic=topic)
        assert res["status_synced"] is True and res["terminal_withheld"] is True
    swept["state"]["orders"] = [copy.deepcopy(body)]
    for _ in range(2):
        swept["seen"].clear()
        p = swept["run"]().payload
        assert p["status_failed"] == [] and swept["seen"] == [], "never re-fed every hour"
    swept["real_map"](copy.deepcopy(body), swept["db"], webhook_id=None, topic=topic)  # a remap
    deleted = shopify_order_delete.handle_shopify_order_delete(swept["db"], {"id": oid}, topic="orders/delete")
    assert deleted["status"] == "kept" and deleted["conflict_task"] is True
    swept["db"]["tasks"].update_one({"task_type": "online_status_conflict"}, {"$set": {"status": "COMPLETED"}})
    swept["real_map"](copy.deepcopy(body), swept["db"], webhook_id="w-1", topic=topic)  # replay

    doc = _doc(swept, oid)
    assert (doc["status"], doc["payment_status"]) == ("DELIVERED", "REFUNDED")
    assert "cancelled_at" not in doc and "status_history" not in doc
    assert doc.get("shopify_cancelled_at") == (CANCELLED_AT if variant == "cancel" else None)
    fact = oos.CANCEL if variant == "cancel" else oos.REFUND
    assert doc["status_conflict_fact"] == fact and doc["shopify_deleted_at"]
    rows = list(swept["db"]["tasks"].find({"order_id": doc["order_id"]}))
    assert [(r["task_type"], r["shopify_fact"], r["priority"]) for r in rows] == [
        ("online_status_conflict", fact, "P1")]
    verb = "cancelled" if variant == "cancel" else "refunded"
    assert rows[0]["title"].startswith(f"Shopify {verb} order ")
    assert "decide: refund, return or Shopify mistake" in rows[0]["title"]
    assert "counter return door" in rows[0]["description"]


NEWER = "2026-09-06T03:00:00Z"


def test_a_stale_body_never_rewinds_a_fulfilment_when_its_money_lands(swept):
    """Finding (c): the reconcile applied FULFILLED / in_transit at 03:00; a
    body stamped 01:00 (unfulfilled, now paid) arrives through the sweep and
    through the drain. Its payment is a fact and lands; its fulfillment_status
    predates the fulfilment IMS applied (the reconcile's clock, read by the
    mapper itself) and never rewinds it -- SHIPPED stays SHIPPED."""
    for oid in (60090, 60091):
        _book(swept, oid, financial_status="pending")
        shopify_fulfillment.reconcile_fulfillment(
            swept["db"], _fulfilment(oid, 1, shipment_status="in_transit", updated_at=NEWER))
        assert (_doc(swept, oid)["status"], _doc(swept, oid)["fulfillment_status"]) == ("SHIPPED", "FULFILLED")

    swept["state"]["orders"] = [_pulled(60090)]
    assert swept["run"]().payload["status_synced"] == ["60090"]
    res = swept["real_map"](_pulled(60091), swept["db"], webhook_id="paid-60091", topic="orders/paid")
    assert res["status_synced"] is True

    for oid in (60090, 60091):
        doc = _doc(swept, oid)
        assert (doc["payment_status"], doc["fulfillment_status"], doc["status"]) == (
            "PAID", "FULFILLED", "SHIPPED")
        assert doc["shopify_fulfillment_updated_at"] == datetime(2026, 9, 6, 3, 0), "the mapper never moves it"


def test_an_order_first_seen_through_a_status_topic_gets_its_real_status(swept):
    """The create webhook was missed: an orders/cancelled on the drain books
    the order AND lands CANCELLED (it used to stay CONFIRMED); the pull's
    catch-up does the same in ONE mapper call."""
    res = swept["real_map"](_pulled(60100, cancelled_at=CANCELLED_AT, financial_status="refunded"),
                            swept["db"], webhook_id="c-60100", topic="orders/cancelled")
    assert res["status"] == "created" and res["order_id"]
    doc = _doc(swept, 60100)
    assert (doc["status"], doc["cancelled_at"], doc["shopify_cancelled_at"]) == (
        "CANCELLED", CANCELLED_AT, CANCELLED_AT)
    assert doc["status_history"][-1]["changed_by"] == "system:ONLINE_MAP"

    swept["state"]["orders"] = [_pulled(60101, fulfillment_status="fulfilled")]
    p = swept["run"]().payload
    assert p["mapped"] == ["60101"] and swept["seen"] == ["60101"], "one mapper call"
    assert _doc(swept, 60101)["status"] == "SHIPPED"
    assert len(swept["orders"].docs) == 2


def test_a_late_create_retry_never_rewinds_the_money_the_pull_booked(swept):
    """The pull books a paid order (a body with no lifecycle fact, so no
    status sync runs); Shopify then retries the ORIGINAL orders/create body --
    partially_paid, older. The create stamped the order watermark, so the
    retry is stale and the booked money stands."""
    swept["state"]["orders"] = [_pulled(71001)]
    assert swept["run"]().payload["mapped"] == ["71001"]
    booked = _doc(swept, 71001)
    assert (booked["payment_status"], booked["amount_paid"]) == ("PAID", 999.0)

    retry = _pulled(71001, financial_status="partially_paid", total_outstanding="800.00",
                    updated_at="2026-09-06T00:30:00Z")
    swept["real_map"](retry, swept["db"], webhook_id="create-71001", topic="orders/create")

    doc = _doc(swept, 71001)
    assert (doc["payment_status"], doc["amount_paid"], doc["balance_due"]) == ("PAID", 999.0, 0.0)
    assert [p["amount"] for p in doc["payments"]] == [999.0]


# ---------------------------------------------------------------------------
# Ruling 1, the courier half: Shiprocket's DELIVERED (the NEXUS poll, or the
# signed webhook) is the delivery -- an exact match, never "RTO DELIVERED".
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402

from agents.implementations import nexus as nexus_module  # noqa: E402
from agents.nexus_providers import SyncResult  # noqa: E402


def test_the_shiprocket_poll_delivers_only_on_an_exact_delivered(swept, monkeypatch):
    latest = {"AWB-D": "Delivered", "AWB-RTO": "RTO DELIVERED", "AWB-OFD": "OUT FOR DELIVERY"}
    for oid, awb in ((60110, "AWB-D"), (60111, "AWB-RTO"), (60112, "AWB-OFD")):
        _book(swept, oid)
        _set(swept, oid, status="SHIPPED", awb=awb)

    async def fake_track(db, awb):
        return SyncResult(ok=True, provider="shiprocket", kind="pull",
                          payload={"latest_status": latest[awb]})

    monkeypatch.setattr(nexus_module, "shiprocket_track_awb", fake_track)
    res = asyncio.run(nexus_module.NexusAgent(db=swept["db"])._sync_shiprocket_outbound())

    assert "1 delivered" in res.notes
    doc = _doc(swept, 60110)
    assert (doc["status"], doc["tracking_status"]) == ("DELIVERED", "Delivered") and doc["delivered_at"]
    assert doc["status_history"][-1]["changed_by"] == "system:SHIPROCKET"
    for oid, courier in ((60111, "RTO DELIVERED"), (60112, "OUT FOR DELIVERY")):
        doc = _doc(swept, oid)
        assert (doc["status"], doc["tracking_status"]) == ("SHIPPED", courier)
        assert "delivered_at" not in doc and "status_history" not in doc


def test_the_shiprocket_poll_rotates_past_orders_the_courier_never_delivers(swept, monkeypatch):
    """50 SHIPPED orders that never reach DELIVERED (RTO, or an AWB Shiprocket
    cannot track) were booked first; a 51st is Delivered. The poll reads 50 a
    run, least-recently polled first, so the 51st lands on the second run."""
    for i in range(50):
        swept["orders"].insert_one({"_id": f"STUCK-{i}", "order_id": f"STUCK-{i}",
                                    "status": "SHIPPED", "awb": f"AWB-S{i}"})
    _book(swept, 60125)
    _set(swept, 60125, status="SHIPPED", awb="AWB-LATE")
    asked = []

    async def fake_track(db, awb):
        asked.append(awb)
        if awb == "AWB-LATE":
            return SyncResult(ok=True, provider="shiprocket", kind="pull",
                              payload={"latest_status": "DELIVERED"})
        if awb.endswith(("0", "2", "4", "6", "8")):
            return SyncResult(ok=False, provider="shiprocket", kind="pull", error="AWB not found")
        return SyncResult(ok=True, provider="shiprocket", kind="pull",
                          payload={"latest_status": "RTO DELIVERED"})

    monkeypatch.setattr(nexus_module, "shiprocket_track_awb", fake_track)
    agent = nexus_module.NexusAgent(db=swept["db"])
    for _ in range(2):
        asyncio.run(agent._sync_shiprocket_outbound())

    assert len(asked) == 100 and "AWB-LATE" in asked
    doc = _doc(swept, 60125)
    assert doc["status"] == "DELIVERED" and doc["delivered_at"]


def test_the_signed_shiprocket_webhook_delivers_on_a_known_awb(swept):
    for oid, awb in ((60120, "AWB-W1"), (60121, "AWB-W2")):
        _book(swept, oid)
        _set(swept, oid, status="SHIPPED", awb=awb)
    agent = nexus_module.NexusAgent(db=swept["db"])
    asyncio.run(agent._handle_shiprocket_webhook({"awb": "AWB-W1", "current_status": "DELIVERED"}))
    asyncio.run(agent._handle_shiprocket_webhook({"awb": "AWB-W2", "current_status": "RTO DELIVERED"}))
    asyncio.run(agent._handle_shiprocket_webhook({"awb": "AWB-NONE", "current_status": "DELIVERED"}))

    doc = _doc(swept, 60120)
    assert doc["status"] == "DELIVERED" and doc["delivered_at"]
    assert doc["status_history"][-1]["changed_by"] == "system:SHIPROCKET_WEBHOOK"
    assert _doc(swept, 60121)["status"] == "SHIPPED"


# ---------------------------------------------------------------------------
# The refund leg. Finding (d): a Shopify cancel refund restocks no unit the
# order does not hold SOLD (an oversold / under-claimed line would MINT a
# phantom), in both postures. Goods out with the courier or the customer: a
# person decides, even under AUTO; a DELIVERED order's "cancel" line never
# restocks (the customer has the goods).
# ---------------------------------------------------------------------------

from test_online_order_mapper import _frame_order  # noqa: E402
from test_shopify_status_catchup import _claim_unit, _refund  # noqa: E402

from api.services import shopify_refund  # noqa: E402


def _two_unit_order_one_sold(swept, oid):
    """A quantity-2 frame line of which ingest claimed ONE serialized unit."""
    doc = _book(swept, oid, line_items=[{**_frame_order(oid)["line_items"][0], "quantity": 2}])
    _claim_unit(swept, doc)
    return _doc(swept, oid)


def _refund_both(rid, oid):
    r = _refund(rid, oid, amount="1998.00")
    r["refund_line_items"][0].update(quantity=2, subtotal=1902.86, total_tax=95.14)
    r["refund_line_items"][0]["line_item"]["quantity"] = 2
    return r


def _post(swept, rid, oid, posture):
    res = shopify_refund.handle_shopify_refund(swept["db"], _refund_both(rid, oid), webhook_id=None,
                                               topic="refunds/create")
    if posture == "queue":
        assert res["status"] == "queued"
        row = swept["review"].find_one({"shopify_refund_id": str(rid)})
        assert [(line["return_qty"], line["restock"]) for line in row["proposed_restock"]] == [
            (1, True), (1, False)]
        shopify_refund.post_from_review(swept["db"], row)
    return res


@pytest.mark.parametrize("posture", ["auto", "queue"])
def test_a_cancel_refund_restocks_only_the_units_the_order_holds_sold(swept, monkeypatch, posture):
    if posture == "auto":
        monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    _two_unit_order_one_sold(swept, 60130)
    _post(swept, 700330, 60130, posture)

    units = swept["stock_repo"].units
    assert [(u["stock_id"], u["status"]) for u in units] == [("stk-1", "AVAILABLE")], "no phantom minted"
    ret = swept["returns"].find_one({"shopify_refund_id": "700330"})
    assert ret is not None and ret["status"] == "COMPLETED"


def test_an_unreadable_stock_answer_restocks_nothing(swept, monkeypatch):
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    _two_unit_order_one_sold(swept, 60131)
    repo = swept["stock_repo"]
    creates = []
    monkeypatch.setattr(repo, "find_many", lambda q: _raise_read())
    monkeypatch.setattr(repo, "create", lambda d: creates.append(d))
    shopify_refund.handle_shopify_refund(swept["db"], _refund_both(700331, 60131), webhook_id=None,
                                         topic="refunds/create")

    assert creates == [] and [(u["stock_id"], u["status"]) for u in repo.units] == [("stk-1", "SOLD")]


def _raise_read():
    raise RuntimeError("stock read down")


def test_a_refund_on_goods_out_waits_for_a_person_even_under_auto(swept, monkeypatch):
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    doc = _book(swept, 60140)
    _claim_unit(swept, doc)
    _set(swept, 60140, status="SHIPPED", awb="AWB60140")
    res = shopify_refund.handle_shopify_refund(swept["db"], _refund(700340, 60140), webhook_id=None,
                                               topic="refunds/create")

    assert res["status"] == "queued"
    row = swept["review"].find_one({"shopify_refund_id": "700340"})
    assert row["status"] == "PENDING" and "Goods are with the courier or the customer" in row["note"]
    assert [(u["stock_id"], u["status"]) for u in swept["stock_repo"].units] == [("stk-1", "SOLD")]
    assert swept["returns"].count_documents({}) == 0 and swept["ledger"].count_documents({}) == 0


@pytest.mark.parametrize("restock_type, restock", [("cancel", False), ("return", True)])
def test_a_delivered_orders_cancel_line_is_proposed_without_a_restock(swept, restock_type, restock):
    doc = _book(swept, 60150)
    _claim_unit(swept, doc)
    _set(swept, 60150, status="DELIVERED")
    shopify_refund.handle_shopify_refund(swept["db"], _refund(700350, 60150, restock_type=restock_type),
                                         webhook_id=None, topic="refunds/create")

    row = swept["review"].find_one({"shopify_refund_id": "700350"})
    assert row["status"] == "PENDING"
    assert [line["restock"] for line in row["proposed_restock"]] == [restock]


# ---------------------------------------------------------------------------
# An unreadable SOLD answer is no answer (panel P2): the restock stays OPEN
# (applied=False) and the retry door puts the unit back -- never a finalized
# "applied" with the unit still SOLD and the counter door blocked.
# ---------------------------------------------------------------------------

from api.routers import returns as returns_router  # noqa: E402

_ACCT = {"user_id": "acct-1", "roles": ["ACCOUNTANT"], "active_store_id": "BV-GANGA-01"}


def _one_unit_refund(swept, oid, rid, **order_set):
    doc = _book(swept, oid)
    _claim_unit(swept, doc)
    if order_set:
        _set(swept, oid, **order_set)
    shopify_refund.handle_shopify_refund(swept["db"], _refund(rid, oid), webhook_id=None,
                                         topic="refunds/create")
    return swept["review"].find_one({"shopify_refund_id": str(rid)})


def _read_fails_once(monkeypatch, repo):
    real, calls = repo.find_many, []

    def flaky(query):
        calls.append(query)
        if len(calls) == 1:
            _raise_read()
        return real(query)

    monkeypatch.setattr(repo, "find_many", flaky)


def _units(swept):
    return [(u["stock_id"], u["status"]) for u in swept["stock_repo"].units]


def test_a_blip_in_the_sold_read_at_the_confirm_leaves_the_restock_open(swept, monkeypatch):
    row = _one_unit_refund(swept, 60160, 700360)
    assert [line["restock"] for line in row["proposed_restock"]] == [True]
    _read_fails_once(monkeypatch, swept["stock_repo"])
    res = shopify_refund.post_from_review(swept["db"], row)

    assert res["status"] == "credited" and res["restock_applied"] is False
    assert _units(swept) == [("stk-1", "SOLD")]
    ret = swept["returns"].find_one({"shopify_refund_id": "700360"})
    assert ret["restock_applied"] is False, "open, so the retry door can run"
    out = asyncio.run(returns_router.retry_restock(ret["return_id"], current_user=_ACCT))
    assert out["restock_applied"] is True and _units(swept) == [("stk-1", "AVAILABLE")]


def test_a_blip_in_the_sold_read_at_the_webhook_keeps_the_proposed_restock(swept, monkeypatch):
    doc = _book(swept, 60161)
    _claim_unit(swept, doc)
    _read_fails_once(monkeypatch, swept["stock_repo"])
    shopify_refund.handle_shopify_refund(swept["db"], _refund(700361, 60161), webhook_id=None,
                                         topic="refunds/create")
    row = swept["review"].find_one({"shopify_refund_id": "700361"})
    assert [line["restock"] for line in row["proposed_restock"]] == [True]

    res = shopify_refund.post_from_review(swept["db"], row)
    assert res["restock_applied"] is True and _units(swept) == [("stk-1", "AVAILABLE")]

