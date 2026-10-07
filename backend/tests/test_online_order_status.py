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


@pytest.mark.parametrize("status", sorted(GOLDEN))
@pytest.mark.parametrize("fact", FACTS)
def test_only_a_conflict_cell_raises_the_conflict_task(status, fact):
    """apply_fact on every cell: a fact the table keeps on a finished order
    ("withheld": CANCELLED / REFUNDED / VOID) raises no task; only the
    DELIVERED row's conflict raises the one conflict task."""
    import mongomock

    db = mongomock.MongoClient().db
    db.orders.insert_one({"order_id": "O1", "order_number": "#1", "status": status})
    out = oos.apply_fact(db, db.orders.find_one({"order_id": "O1"}, {"_id": 0}), fact, source="test")
    assert out["why"] == GOLDEN[status][fact][1] and not out["failed"]
    raised = db.tasks.count_documents({"task_type": "online_status_conflict"})
    assert raised == (1 if out["why"] == "conflict" else 0)


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
        assert _tasks(swept, oid, "online_status_conflict") == 0, "a finished order is no conflict"


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
    assert _tasks(swept, 60060, "online_status_conflict") == 0, "a finished order is no conflict"


def test_an_open_order_deleted_on_shopify_is_voided_through_the_claim(swept):
    _book(swept, 60061)
    _set(swept, 60061, status="SHIPPED")
    res = shopify_order_delete.handle_shopify_order_delete(swept["db"], {"id": 60061}, topic="orders/delete")

    assert res["status"] == "voided" and res["status_before_void"] == "SHIPPED"
    doc = _doc(swept, 60061)
    assert (doc["status"], doc["status_before_void"], doc["void_reason"]) == (
        "VOID", "SHIPPED", "Shopify orders/delete webhook")
    assert doc["status_history"][-1]["changed_by"] == "system:SHOPIFY_ORDER_DELETE"


def test_a_void_that_lost_a_race_snapshots_the_status_it_voided(swept, monkeypatch):
    """The delete read CONFIRMED; staff marked the order READY before its
    claim. The claim loses, apply_fact reads READY and voids that: the
    snapshot kept to make the void auditable and reversible is READY."""
    from api.routers.orders import release

    _book(swept, 60062)
    real = release._claim_order_status

    def staff_first(*a, **kw):
        monkeypatch.setattr(release, "_claim_order_status", real)
        _set(swept, 60062, status="READY")
        return real(*a, **kw)

    monkeypatch.setattr(release, "_claim_order_status", staff_first)
    res = shopify_order_delete.handle_shopify_order_delete(swept["db"], {"id": 60062}, topic="orders/delete")

    assert res["status"] == "voided" and res["status_before_void"] == "READY"
    doc = _doc(swept, 60062)
    assert (doc["status"], doc["status_before_void"]) == ("VOID", "READY")


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


@pytest.mark.parametrize("leg", ["drain_then_sweep", "delete_redelivered"])
def test_a_lost_conflict_task_is_raised_by_the_next_event(swept, monkeypatch, leg):
    """Ruling 2: a delivered order Shopify cancels or deletes gets ONE task.
    Its insert fails once: the event wrote its marks anyway and reported
    success, so the sweep never fed it again and a redelivered delete was a
    duplicate -- no task, ever. A lost task is a failed event: no marks."""
    oid = 60165 + (leg == "delete_redelivered")
    _book(swept, oid)
    _set(swept, oid, status="DELIVERED", fulfillment_status="FULFILLED")
    tasks, lost = swept["db"]["tasks"], []
    real = tasks.insert_one

    def insert_fails_once(doc, *a, **kw):
        if not lost:
            lost.append(doc)
            raise RuntimeError("tasks write down")
        return real(doc, *a, **kw)

    monkeypatch.setattr(tasks, "insert_one", insert_fails_once)
    if leg == "drain_then_sweep":
        body = _pulled(oid, cancelled_at=CANCELLED_AT, fulfillment_status="fulfilled")
        res = swept["real_map"](copy.deepcopy(body), swept["db"], webhook_id=f"c-{oid}",
                                topic="orders/cancelled")
        assert res["status_synced"] is False and "shopify_cancelled_at" not in _doc(swept, oid)
        swept["state"]["orders"] = [body]
        swept["run"]()
    else:
        first = shopify_order_delete.handle_shopify_order_delete(swept["db"], {"id": oid}, topic="orders/delete")
        assert first["status"] == "error" and "shopify_deleted_at" not in _doc(swept, oid)
        again = shopify_order_delete.handle_shopify_order_delete(swept["db"], {"id": oid}, topic="orders/delete")
        assert (again["status"], again["conflict_task"]) == ("kept", True)
    assert lost and _doc(swept, oid)["status"] == "DELIVERED"
    assert _tasks(swept, oid, "online_status_conflict") == 1


def test_three_lost_races_write_nothing_and_say_so(swept, monkeypatch):
    from api.routers.orders import release

    _book(swept, 60160)
    monkeypatch.setattr(release, "_claim_order_status", lambda *a, **kw: False)
    res = oos.apply_fact(swept["db"], _doc(swept, 60160), oos.SHIP, source="T", marks={"awb": "A1"})
    assert res["failed"] is True and res["to"] is None
    assert "awb" not in _doc(swept, 60160)


# A race: another move lands between the read a decision was made on and its
# write. The claim's precondition is the status that was READ, so the stale
# write loses; apply_fact reads again and decides again on the newer status.


def test_a_courier_delivery_that_lands_first_wins_over_a_stale_shopify_cancel(swept):
    """The mapper read SHIPPED and decided CANCEL; the courier's DELIVERED
    landed before its write. Ruling 2: it stays DELIVERED, with one task."""
    _book(swept, 60165)
    _set(swept, 60165, status="SHIPPED", awb="AWB60165")
    stale = _doc(swept, 60165)
    _set(swept, 60165, status="DELIVERED")
    res = oos.apply_fact(swept["db"], stale, oos.CANCEL, source="T")
    assert (res["to"], res["why"], res["failed"]) == (None, "conflict", False)
    assert _doc(swept, 60165)["status"] == "DELIVERED"
    assert _tasks(swept, 60165, "online_status_conflict") == 1


def test_a_staff_cancel_that_lands_first_wins_over_a_stale_shopify_fulfilment(swept):
    """The mapper read CONFIRMED with a fulfilled body; staff cancelled before
    its write. A finished order stays finished: never CANCELLED -> SHIPPED."""
    _book(swept, 60166)
    stale = _doc(swept, 60166)
    assert stale["status"] == "CONFIRMED"
    _set(swept, 60166, status="CANCELLED")
    res = oos.apply_fact(swept["db"], stale, oos.SHIP, source="T")
    assert (res["to"], res["why"], res["failed"]) == (None, "withheld", False)
    assert _doc(swept, 60166)["status"] == "CANCELLED"


def test_the_orders_screen_can_filter_on_shipped(monkeypatch):
    """The table writes SHIPPED on every fulfilled online order, and the Orders
    screen's Shipped filter sends GET /orders?status=SHIPPED. The endpoint
    validates ?status= against OrderStatus: a status missing there is a 422
    and the screen shows 'Failed to load orders'."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routers.auth import get_current_user
    from api.routers.orders import _shared, lists

    app = FastAPI()
    app.include_router(_shared.router, prefix="/api/v1/orders")
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "u-1", "roles": ["SUPERADMIN"], "active_store_id": None}
    monkeypatch.setattr(lists, "get_order_repository", lambda: None)
    monkeypatch.setattr(lists, "validate_store_access", lambda sid, user: None)
    client = TestClient(app)
    for status in ("READY", "SHIPPED", "DELIVERED"):
        assert client.get(f"/api/v1/orders?status={status}").status_code == 200, status


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
    assert "press Goods back" in rows[0]["description"]
    assert "never refund it again at the counter" in rows[0]["description"]


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


def test_one_awb_whose_track_call_raises_never_stops_the_poll(swept, monkeypatch):
    """An unexpected tracking JSON shape (AttributeError / KeyError) or an AWB
    httpx refuses (InvalidURL is no HTTPError) raises out of the track call.
    That order goes to the back like any unanswered one and the poll goes on:
    a later order the courier delivered is delivered on the same run. Left
    unstamped, the poisoned order sorted first and aborted every run."""
    for oid, awb in (("A", "AWB-TRANSIT"), ("P", "AWB-POISON")):
        swept["orders"].insert_one({"_id": oid, "order_id": oid, "status": "SHIPPED", "awb": awb})
    _book(swept, 60127)
    _set(swept, 60127, status="SHIPPED", awb="AWB-LATE")
    asked = []

    async def fake_track(db, awb):
        asked.append(awb)
        if awb == "AWB-POISON":
            raise AttributeError("'str' object has no attribute 'get'")
        latest = "DELIVERED" if awb == "AWB-LATE" else "IN TRANSIT"
        return SyncResult(ok=True, provider="shiprocket", kind="pull", payload={"latest_status": latest})

    monkeypatch.setattr(nexus_module, "shiprocket_track_awb", fake_track)
    agent = nexus_module.NexusAgent(db=swept["db"])
    for _ in range(2):
        asyncio.run(agent._run_integration_sync("shiprocket"))

    assert asked == ["AWB-TRANSIT", "AWB-POISON", "AWB-LATE", "AWB-TRANSIT", "AWB-POISON"]
    assert _doc(swept, 60127)["status"] == "DELIVERED"
    assert swept["orders"].find_one({"order_id": "P"})["tracking_polled_at"]


def test_the_shiprocket_poll_asks_the_table_again_after_a_lost_delivery(swept, monkeypatch):
    """The poll asks the table on every answer, not only on a tracking
    change: the courier's DELIVERED stamp landed but the status claim hit a
    Mongo blip, so the order stayed SHIPPED with tracking_status DELIVERED.
    Asked only on a change, every later answer equals the stored one and the
    order is SHIPPED forever."""
    from api.routers.orders import release

    _book(swept, 60113)
    _set(swept, 60113, status="SHIPPED", awb="AWB-B")

    async def fake_track(db, awb):
        return SyncResult(ok=True, provider="shiprocket", kind="pull", payload={"latest_status": "DELIVERED"})

    real, left = release._claim_order_status, [1]

    def claim(*a, **kw):
        if left and left.pop():
            raise RuntimeError("mongo failover")
        return real(*a, **kw)

    monkeypatch.setattr(nexus_module, "shiprocket_track_awb", fake_track)
    monkeypatch.setattr(release, "_claim_order_status", claim)
    agent = nexus_module.NexusAgent(db=swept["db"])
    asyncio.run(agent._sync_shiprocket_outbound())
    doc = _doc(swept, 60113)
    assert (doc["status"], doc["tracking_status"]) == ("SHIPPED", "DELIVERED")

    asyncio.run(agent._sync_shiprocket_outbound())
    doc = _doc(swept, 60113)
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


@pytest.mark.parametrize("leg", ["poll", "webhook"])
@pytest.mark.parametrize("status", ["CONFIRMED", "PROCESSING", "READY"])
def test_a_parcel_out_on_an_unshipped_order_is_delivered_by_either_courier_leg(swept, monkeypatch,
                                                                              status, leg):
    """An Rx / stock hold kept the order at CONFIRMED when its Shopify
    fulfilment landed (the reconcile still wrote its awb); an admin then
    cleared the hold. Shiprocket's DELIVERED delivers it through the ONE
    table on both courier legs -- the poll used to read SHIPPED orders only,
    so it stayed CONFIRMED forever where the webhook delivered it."""
    _book(swept, 60128)
    _set(swept, 60128, status=status, awb="AWB-H")

    async def fake_track(db, awb):
        return SyncResult(ok=True, provider="shiprocket", kind="pull", payload={"latest_status": "DELIVERED"})

    monkeypatch.setattr(nexus_module, "shiprocket_track_awb", fake_track)
    agent = nexus_module.NexusAgent(db=swept["db"])
    if leg == "poll":
        asyncio.run(agent._sync_shiprocket_outbound())
    else:
        asyncio.run(agent._handle_shiprocket_webhook({"awb": "AWB-H", "current_status": "DELIVERED"}))
    doc = _doc(swept, 60128)
    assert doc["status"] == "DELIVERED" and doc["delivered_at"]


_HELD = {"rx_pending": True, "fulfillment_hold": True, "rx_hold_reasons": ["RX_MISSING"]}


@pytest.mark.parametrize("state", [
    {"status": "CANCELLED"}, {"status": "REFUNDED"}, {"status": "VOID"},
    {"status": "CONFIRMED", **_HELD}, {"status": "SHIPPED", **_HELD},
], ids=["cancelled", "refunded", "void", "rx_held_confirmed", "rx_held_shipped"])
def test_the_shiprocket_webhook_asks_the_table_on_any_order_its_awb_finds(swept, state):
    """The webhook finds the order by its AWB alone, whatever its status: an
    order cancelled after its parcel was (the AWB stays on it), a refunded or
    voided one, one on an Rx hold. A courier DELIVERED moves none of them --
    the table keeps a finished order and withholds a held one (raising its
    Rx task) -- where a bypass of the table would deliver each."""
    _book(swept, 60124)
    _set(swept, 60124, awb="AWB-C", **state)
    agent = nexus_module.NexusAgent(db=swept["db"])
    asyncio.run(agent._handle_shiprocket_webhook({"awb": "AWB-C", "current_status": "DELIVERED"}))

    doc = _doc(swept, 60124)
    assert doc["status"] == state["status"]
    assert "delivered_at" not in doc and "status_history" not in doc
    assert _tasks(swept, 60124, "online_rx_hold") == (1 if state.get("rx_pending") else 0)


_T = "2026-09-06T{}:00Z".format
_SPLITS = {
    # A second parcel created by mistake and cancelled: the live one keeps the
    # order's tracking fields.
    "a_cancelled_parcel_after_the_live_one": (
        "AWB-LIVE", [(1, "AWB-LIVE", "success", _T("01:00")), (2, "AWB-CXL", "cancelled", _T("02:00"))]),
    # The parcel the tracking fields show is cancelled; the earlier one is
    # still on its way.
    "the_newest_parcel_cancelled": (
        "AWB-F1", [(1, "AWB-F1", "open", _T("01:10")), (2, "AWB-F2", "open", _T("01:20")),
                   (2, "AWB-F2", "cancelled", _T("03:00"))]),
}


@pytest.mark.parametrize("leg", ["poll", "webhook"])
@pytest.mark.parametrize("split", sorted(_SPLITS))
def test_a_split_shipment_is_delivered_by_its_live_parcel(swept, monkeypatch, split, leg):
    """Ruling 1 on a split shipment: DELIVERED when the courier delivers the
    parcel still on its way. A cancelled parcel keeps its tracking number on
    Shopify; it never takes over the order's tracking fields from a live one,
    and the Shiprocket poll and webhook track every live parcel -- they used
    to know only the order's one awb, so the poll asked the cancelled AWB
    forever and the webhook for the live one found no order."""
    oid = 60126
    live, events = _SPLITS[split]
    _book(swept, oid)
    for fid, awb, status, at in events:
        shopify_fulfillment.reconcile_fulfillment(
            swept["db"], _fulfilment(oid, fid, tracking_number=awb, status=status, updated_at=at,
                                     created_at=at), topic="fulfillments/update")
    doc = _doc(swept, oid)
    assert doc["status"] == "SHIPPED"
    if split == "a_cancelled_parcel_after_the_live_one":
        assert (doc["awb"], doc["fulfillment_status"]) == ("AWB-LIVE", "FULFILLED")
    agent = nexus_module.NexusAgent(db=swept["db"])
    if leg == "poll":
        asked = []

        async def fake_track(db, awb):
            asked.append(awb)
            return SyncResult(ok=True, provider="shiprocket", kind="pull",
                              payload={"latest_status": "DELIVERED" if awb == live else "CANCELED"})

        monkeypatch.setattr(nexus_module, "shiprocket_track_awb", fake_track)
        asyncio.run(agent._sync_shiprocket_outbound())
        assert asked == [live]
    else:
        asyncio.run(agent._handle_shiprocket_webhook({"awb": live, "current_status": "DELIVERED"}))

    doc = _doc(swept, oid)
    assert doc["status"] == "DELIVERED" and doc["delivered_at"]


def test_reconciles_of_two_parcels_from_one_stale_read_keep_both(swept, monkeypatch):
    """Two reconciles of different parcels overlap (two workers, or the sweep
    beside a webhook): each read the order before the other wrote. The
    live-parcel list was one whole list from the read, so the later write
    dropped the other parcel -- the courier legs never asked its AWB -- and
    an older parcel took the tracking fields and the watermark back."""
    oid = 60127
    _book(swept, oid)
    pre = _doc(swept, oid)
    monkeypatch.setattr(shopify_fulfillment, "_find_ims_order", lambda db, sid: copy.deepcopy(pre))
    for fid, at in ((1, "01:00"), (2, "01:05"), (1, "01:00")):
        shopify_fulfillment.reconcile_fulfillment(
            swept["db"], _fulfilment(oid, fid, tracking_number=f"AWB-{fid}", updated_at=_T(at),
                                     created_at=_T(at)), topic="fulfillments/create")

    doc = _doc(swept, oid)
    assert sorted(shopify_fulfillment.tracked_awbs(doc)) == ["AWB-1", "AWB-2"]
    assert swept["orders"].find_one(shopify_fulfillment.awb_filter("AWB-1"))["order_id"] == doc["order_id"]
    assert doc["awb"] == "AWB-2"
    assert doc[shopify_fulfillment.FULFILLMENT_WATERMARK] == datetime(2026, 9, 6, 1, 5)


@pytest.mark.parametrize("newer_awb", ["", "AWB-Y"], ids=["cancelled", "relabelled"])
@pytest.mark.parametrize("stale_lands", ["between_the_newer_ones_writes", "after_the_newer_one"])
def test_a_stale_reconcile_of_a_parcel_never_sticks_over_its_newer_state(
        swept, monkeypatch, newer_awb, stale_lands):
    """Parcel 1 went out at 01:00 with AWB-X. Its 03:00 update (cancelled, or
    relabelled AWB-Y) and an hourly-sweep body fetched at 02:00 (still AWB-X)
    both read the 01:00 order. The parcel's entry and its clock were written
    apart, the clock unguarded: the list kept AWB-X beside the 03:00 clock,
    so no redelivery or sweep ever re-fed it -- the courier legs asked about
    the cancelled AWB for good."""
    oid = 60128
    _book(swept, oid)
    shopify_fulfillment.reconcile_fulfillment(swept["db"], _fulfilment(
        oid, 1, tracking_number="AWB-X", updated_at=_T("01:00"), created_at=_T("01:00")))
    pre = copy.deepcopy(_doc(swept, oid))
    monkeypatch.setattr(shopify_fulfillment, "_find_ims_order", lambda db, sid: copy.deepcopy(pre))
    newer = _fulfilment(oid, 1, tracking_number=newer_awb or "AWB-X", updated_at=_T("03:00"),
                        created_at=_T("01:00"), status="success" if newer_awb else "cancelled")
    stale = _fulfilment(oid, 1, tracking_number="AWB-X", updated_at=_T("02:00"), created_at=_T("01:00"))
    orders = swept["orders"]
    write = orders.update_one

    def stale_first(filter_, update, **kw):
        if any(k.startswith(shopify_fulfillment.FULFILLMENT_CLOCKS) for k in update.get("$set") or {}):
            monkeypatch.setattr(orders, "update_one", write)
            shopify_fulfillment.reconcile_fulfillment(swept["db"], stale)
        return write(filter_, update, **kw)

    if stale_lands == "between_the_newer_ones_writes":
        monkeypatch.setattr(orders, "update_one", stale_first)
    shopify_fulfillment.reconcile_fulfillment(swept["db"], newer)
    if stale_lands == "after_the_newer_one":
        shopify_fulfillment.reconcile_fulfillment(swept["db"], stale)

    doc = _doc(swept, oid)
    assert shopify_fulfillment.tracked_awbs(doc) == ([newer_awb] if newer_awb else [])
    assert orders.find_one(shopify_fulfillment.awb_filter("AWB-X")) is None
    assert doc[shopify_fulfillment.FULFILLMENT_CLOCKS]["f1"] == datetime(2026, 9, 6, 3, 0)


def test_a_parcels_first_reconciles_overlapping_keep_the_newer(swept, monkeypatch):
    """The create (01:00, AWB-X) and an update (01:30, AWB-Y) of one parcel
    both find no entry for it; the create appends first, so the update's
    append finds one and replaces it instead."""
    oid = 60129
    _book(swept, oid)
    pre = copy.deepcopy(_doc(swept, oid))
    monkeypatch.setattr(shopify_fulfillment, "_find_ims_order", lambda db, sid: copy.deepcopy(pre))
    orders = swept["orders"]
    write = orders.update_one

    def create_first(filter_, update, **kw):
        if "$push" in update:
            monkeypatch.setattr(orders, "update_one", write)
            shopify_fulfillment.reconcile_fulfillment(swept["db"], _fulfilment(
                oid, 1, tracking_number="AWB-X", updated_at=_T("01:00"), created_at=_T("01:00")))
        return write(filter_, update, **kw)

    monkeypatch.setattr(orders, "update_one", create_first)
    shopify_fulfillment.reconcile_fulfillment(swept["db"], _fulfilment(
        oid, 1, tracking_number="AWB-Y", updated_at=_T("01:30"), created_at=_T("01:00")))
    doc = _doc(swept, oid)
    assert doc[shopify_fulfillment.PARCEL_AWBS] == [{"id": "f1", "awb": "AWB-Y"}]
    assert doc[shopify_fulfillment.FULFILLMENT_CLOCKS]["f1"] == datetime(2026, 9, 6, 1, 30)


def test_the_last_live_parcel_cancelled_takes_the_tracking_fields_over(swept):
    """A cancelled parcel keeps its entry (AWB ""): it is no live parcel that
    keeps the tracking fields from the last one's cancel."""
    oid = 60130
    _book(swept, oid)
    for fid, status, at in ((1, "success", "01:00"), (2, "success", "01:10"),
                            (1, "cancelled", "02:00"), (2, "cancelled", "03:00")):
        shopify_fulfillment.reconcile_fulfillment(swept["db"], _fulfilment(
            oid, fid, tracking_number=f"AWB-{fid}", status=status, updated_at=_T(at), created_at=_T(at)))
    doc = _doc(swept, oid)
    assert shopify_fulfillment.tracked_awbs(doc) == []
    assert doc["fulfillment_status"] == "CANCELLED"


# ---------------------------------------------------------------------------
# The refund leg. Finding (d): a Shopify cancel refund restocks no unit the
# order does not hold SOLD (an oversold / under-claimed line would MINT a
# phantom), in both postures. Goods out with the courier or the customer: a
# person decides, even under AUTO; the "cancel" line of an order the counter
# handed over never restocks (the customer has the goods).
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


def test_an_order_shipped_by_its_orders_body_alone_is_goods_out(swept, monkeypatch):
    """orders/fulfilled lands SHIPPED (the mapper's own SHIP fact) before any
    fulfillments/* reconcile stamps awb or shopify_fulfillment_id: the status
    is all that says the frame is with the courier, so AUTO still waits for a
    person -- posted, it put stk-1 back on the shelf while it was in transit."""
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    doc = _book(swept, 60142)
    _claim_unit(swept, doc)
    swept["real_map"](_pulled(60142, fulfillment_status="fulfilled"), swept["db"],
                      webhook_id="ful-60142", topic="orders/fulfilled")
    shipped = _doc(swept, 60142)
    assert shipped["status"] == "SHIPPED"
    assert not {"awb", "shopify_fulfillment_id"} & {k for k, v in shipped.items() if v}
    res = shopify_refund.handle_shopify_refund(swept["db"], _refund(700342, 60142, restock_type="return"),
                                               webhook_id=None, topic="refunds/create")

    assert res["status"] == "queued"
    assert [(u["stock_id"], u["status"]) for u in swept["stock_repo"].units] == [("stk-1", "SOLD")]


_HANDED_OVER = {"status": "DELIVERED", "status_updated_by": "u1"}


def test_an_order_handed_over_at_the_counter_is_goods_out_by_its_status_alone(swept, monkeypatch):
    """No parcel (no awb, no Shopify fulfilment id): the DELIVERED status is
    all that says the customer holds the frame, so AUTO still waits for a
    person -- posted, it put stk-1 back on the shelf while the customer
    still had it."""
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    doc = _book(swept, 60141)
    _claim_unit(swept, doc)
    _set(swept, 60141, **_HANDED_OVER)
    assert not {"awb", "shopify_fulfillment_id"} & set(_doc(swept, 60141))
    res = shopify_refund.handle_shopify_refund(swept["db"], _refund(700341, 60141, restock_type="return"),
                                               webhook_id=None, topic="refunds/create")

    assert res["status"] == "queued"
    assert [(u["stock_id"], u["status"]) for u in swept["stock_repo"].units] == [("stk-1", "SOLD")]


@pytest.mark.parametrize("restock_type, restock", [("cancel", False), ("return", True)])
def test_a_handed_over_orders_cancel_line_is_proposed_without_a_restock(swept, restock_type, restock):
    doc = _book(swept, 60150)
    _claim_unit(swept, doc)
    _set(swept, 60150, **_HANDED_OVER)
    shopify_refund.handle_shopify_refund(swept["db"], _refund(700350, 60150, restock_type=restock_type),
                                         webhook_id=None, topic="refunds/create")

    row = swept["review"].find_one({"shopify_refund_id": "700350"})
    assert row["status"] == "PENDING"
    assert [line["restock"] for line in row["proposed_restock"]] == [restock]


def test_a_unit_shopify_never_fulfilled_goes_back_on_the_shelf_after_a_courier_delivery(swept):
    """Two frames on one line; the courier delivered one parcel (the order is
    DELIVERED by the courier leg), then Shopify cancels and refunds the other,
    unfulfilled unit ("cancel"). That frame never left the shelf: the confirm
    restocks it. The hold is for an order the counter handed over, where the
    customer has every unit whatever Shopify thinks was fulfilled."""
    oid = 60151
    doc = _book(swept, oid, line_items=[{**_frame_order(oid)["line_items"][0], "quantity": 2}])
    _claim_unit(swept, doc)
    swept["stock_repo"].units.append({**swept["stock_repo"].units[0], "stock_id": "stk-2"})
    shopify_fulfillment.reconcile_fulfillment(swept["db"], _fulfilment(oid, 1, shipment_status="delivered"))
    assert _doc(swept, oid)["status"] == "DELIVERED"
    shopify_refund.handle_shopify_refund(swept["db"], _refund(700351, oid), webhook_id=None,
                                         topic="refunds/create")
    row = swept["review"].find_one({"shopify_refund_id": "700351"})
    assert [(line["return_qty"], line["restock"]) for line in row["proposed_restock"]] == [(1, True)]
    assert "status-conflict task" not in row["note"], "a partial refund raises none"

    _confirm(row)
    assert sorted(s for _, s in _units(swept)) == ["AVAILABLE", "SOLD"]


# ---------------------------------------------------------------------------
# An unreadable SOLD answer is no answer (panel P2): the restock stays OPEN
# (applied=False) and the retry door puts the unit back -- never a finalized
# "applied" with the unit still SOLD and the counter door blocked.
# ---------------------------------------------------------------------------

from api.routers import online_store_refund_reviews as reviews_router  # noqa: E402
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
    # The task states the real cause and ONE action -- never "add them by hand
    # and re-run": the re-run reactivates the still-SOLD unit (two rows, one frame).
    [task] = swept["db"]["tasks"].find({"source_ref": f"return_restock_blocked:{ret['return_id']}"})
    text = task["description"]
    assert "could not read this order's SOLD units or its earlier returns" in text
    assert "press Goods back" in text and "Never add these units by hand" in text
    assert "no physical shop" not in text and "add them at the receiving shop" not in text
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


# ---------------------------------------------------------------------------
# Ruling 2's "return" option (panel P1). A DELIVERED order Shopify refunds stays
# DELIVERED and its cancel line is held (the customer has the goods). The money
# is the confirm's; the goods come back through Goods back on the review row,
# before or after the confirm -- one credit note, one unit, no phantom -- and
# never through the counter door (test_returns_refund_cap: it refuses an order
# Shopify refunded in full).
# ---------------------------------------------------------------------------


def _confirm(row):
    return asyncio.run(reviews_router.confirm_refund_review(row["review_id"], current_user=_ACCT))


def _goods_back(row):
    return asyncio.run(reviews_router.goods_back_refund_review(row["review_id"], current_user=_ACCT))


@pytest.mark.parametrize("order", ["confirm_first", "goods_first"])
def test_the_goods_of_a_delivered_orders_refund_come_back_once(swept, order):
    from fastapi import HTTPException

    row = _one_unit_refund(swept, 60170, 700370, **_HANDED_OVER, payment_status="REFUNDED")
    assert [line["restock"] for line in row["proposed_restock"]] == [False], "held: goods are out"
    if order == "confirm_first":
        res = _confirm(row)["result"]
        assert res["restock_applied"] is True and res["restock_stock_ids"] == []
        assert _units(swept) == [("stk-1", "SOLD")], "the confirm restocks nothing"
    got = _goods_back(swept["review"].find_one({"review_id": row["review_id"]}))
    assert got["result"]["status"] == "restocked" and _units(swept) == [("stk-1", "AVAILABLE")]
    with pytest.raises(HTTPException) as again:
        _goods_back(row)
    assert again.value.status_code == 409
    if order == "goods_first":
        _confirm(swept["review"].find_one({"review_id": row["review_id"]}))

    assert _units(swept) == [("stk-1", "AVAILABLE")], "one unit, no phantom"
    assert swept["ledger"].count_documents({}) == 1, "one credit note"
    assert _doc(swept, 60170)["status"] == "DELIVERED"


def test_goods_back_that_cannot_read_the_stock_can_be_pressed_again(swept, monkeypatch):
    from fastapi import HTTPException

    row = _one_unit_refund(swept, 60171, 700371, status="DELIVERED")
    _read_fails_once(monkeypatch, swept["stock_repo"])
    with pytest.raises(HTTPException) as first:
        _goods_back(row)
    assert first.value.status_code == 503 and _units(swept) == [("stk-1", "SOLD")]
    assert _goods_back(row)["result"]["status"] == "restocked"
    assert _units(swept) == [("stk-1", "AVAILABLE")]


# One frame, one unit, whichever doors run. The confirm of a goods-out refund
# (a SHIPPED order) that cannot read the stock leaves the restock OPEN and a
# task sends a person to the /returns/{id}/restock retry door; Goods back may
# put the unit back before that confirm, or before or after the retry. Every
# door restocks the one way (shopify_refund._restock_booked), so the refund
# restocks its line once.


def _open_restock(swept, monkeypatch, oid, rid):
    row = _one_unit_refund(swept, oid, rid, status="SHIPPED", awb=f"AWB{oid}")
    assert row["status"] == "PENDING" and [line["restock"] for line in row["proposed_restock"]] == [True]
    _read_fails_once(monkeypatch, swept["stock_repo"])
    res = shopify_refund.post_from_review(swept["db"], row)
    assert res["status"] == "credited" and res["restock_applied"] is False
    assert _units(swept) == [("stk-1", "SOLD")]
    return row, swept["returns"].find_one({"shopify_refund_id": str(rid)})


def _retry(ret):
    return asyncio.run(returns_router.retry_restock(ret["return_id"], current_user=_ACCT))


@pytest.mark.parametrize("goods_first", [False, True])
def test_goods_back_and_the_restock_retry_put_one_unit_back(swept, monkeypatch, goods_first):
    row, ret = _open_restock(swept, monkeypatch, 60172, 700372)
    doors = [lambda: _goods_back(row)["result"]["status"], lambda: _retry(ret)["restock_applied"]]
    assert [door() for door in (doors if goods_first else doors[::-1])] in (
        ["restocked", True], [True, "restocked"])
    assert _units(swept) == [("stk-1", "AVAILABLE")], "no phantom minted"


def test_goods_back_before_the_confirm_leaves_it_nothing_to_restock(swept):
    row = _one_unit_refund(swept, 60177, 700377, status="SHIPPED", awb="AWB60177")
    assert _goods_back(row)["result"]["status"] == "restocked"
    res = shopify_refund.post_from_review(swept["db"], row)
    assert res["restock_applied"] is True and res["restock_stock_ids"] == []
    assert _units(swept) == [("stk-1", "AVAILABLE")], "no phantom minted"


def test_a_restock_retry_that_cannot_read_the_stock_restocks_nothing(swept, monkeypatch):
    row, ret = _open_restock(swept, monkeypatch, 60173, 700373)
    _read_fails_once(monkeypatch, swept["stock_repo"])
    assert _retry(ret)["restock_applied"] is False
    assert _units(swept) == [("stk-1", "SOLD")]
    assert _retry(ret)["restock_applied"] is True, "left open, not stuck in progress"
    assert _goods_back(row)["result"]["status"] == "restocked"
    assert _units(swept) == [("stk-1", "AVAILABLE")]


# Our own Shopify order-history import (historical, DELIVERED) predates IMS
# stock: no SOLD unit guards its restock, and every phantom frame came from a
# door minting one. So no door ever adds a stock row for it (owner 2026-10-01).
# Goods back, the accountant's confirm, the AUTO post and the /returns/{id}/
# restock retry book the refund's units on the order line as for every order
# (_restock_booked), and the booking raises ONE task for the receiving shop's
# store manager to add the frame through stock-in. A booking is taken once, so
# one frame is one task whichever doors run, in either order.


_MANAGER = "u-mgr-ganga"


def _historical_refund(swept, monkeypatch, oid, rid, ims_product_id=None, **order_set):
    doc = _book(swept, oid)
    if ims_product_id:  # the SKU resolved: the line keeps Shopify's product_id beside it
        order_set["items"] = [{**doc["items"][0], "ims_product_id": ims_product_id}]
    _set(swept, oid, historical=True, import_source="shopify_order_history",
         fulfillment_stores=["BV-GANGA-01"], **order_set)
    if not swept["db"]["users"].find_one({"user_id": _MANAGER}):
        swept["db"]["users"].insert_one({"user_id": _MANAGER, "roles": ["STORE_MANAGER"],
                                         "is_active": True, "store_ids": ["BV-GANGA-01"]})
    shopify_refund.handle_shopify_refund(swept["db"], _refund(rid, oid, restock_type="return"),
                                         webhook_id=None, topic="refunds/create")
    return swept["review"].find_one({"shopify_refund_id": str(rid)})


def _minted(swept):
    return sorted(u["status"] for u in swept["stock_repo"].units)


def _stock_in(swept, oid):
    """The stock-in tasks a historical order's bookings raised."""
    return list(swept["db"]["tasks"].find(
        {"order_id": _doc(swept, oid)["order_id"], "task_type": "historical_stock_in"}))


def _premark(monkeypatch):
    """A restock as it ran before the order-line marks (every refund confirmed
    on main): no booking, so a historical frame is minted. Returns the undo."""
    real = shopify_refund._restock_booked
    monkeypatch.setattr(shopify_refund, "_restock_booked",
                        lambda order, lines, rid, restock, **kw: restock(lines))
    return lambda: monkeypatch.setattr(shopify_refund, "_restock_booked", real)


@pytest.mark.parametrize("goods_first", [True, False])
def test_one_frame_of_a_historical_order_is_one_stock_in_task_goods_back_and_confirm(swept, monkeypatch,
                                                                                     goods_first):
    oid = 60180 + goods_first
    row = _historical_refund(swept, monkeypatch, oid, 700380 + goods_first, status="DELIVERED")
    assert row["status"] == "PENDING" and [line["restock"] for line in row["proposed_restock"]] == [True]
    said = {}
    doors = [lambda: said.update(goods=_goods_back(row)["result"]["status"]), lambda: _confirm(row)]
    for door in (doors if goods_first else doors[::-1]):
        door()
    # Pressed after the confirm booked the frame, Goods back has nothing left to
    # book: it still says stock-in (it said "Goods put back in stock").
    assert said == {"goods": "stock_in"}
    assert _minted(swept) == [], "IMS adds no stock row for a historical frame"
    [task] = _stock_in(swept, oid)
    assert (task["assigned_to"], task["store_id"], task["status"]) == (_MANAGER, "BV-GANGA-01", "OPEN")
    assert "add each frame" in task["description"] and "stock-in" in task["description"]
    assert "re-run" not in task["description"]
    line = _doc(swept, oid)["items"][0]
    assert (line.get("returned_qty"), line.get("restocked_refunds")) == (1, {str(700380 + goods_first): 1})
    assert swept["ledger"].count_documents({}) == 1


@pytest.mark.parametrize("goods_first", [True, False])
def test_one_frame_of_a_historical_order_is_one_stock_in_task_goods_back_and_retry(swept, monkeypatch,
                                                                                  goods_first):
    oid, rid = 60182 + goods_first, 700382 + goods_first
    row = _historical_refund(swept, monkeypatch, oid, rid, status="DELIVERED")
    real = returns_router._returns_coll
    blip = _ScanFailsOnce(real())
    monkeypatch.setattr(returns_router, "_returns_coll", lambda: blip)
    res = _confirm(row)["result"]
    assert (res["restock_applied"], res["historical"]) == (False, True), "its returns could not be read"
    monkeypatch.setattr(returns_router, "_returns_coll", real)
    ret = swept["returns"].find_one({"shopify_refund_id": str(rid)})
    # The blocked task says what happened on THIS order: the re-run books the
    # frame and a stock-in task adds it -- never "IMS puts each unit back" and
    # "never add by hand", which the stock-in task then contradicts.
    [blocked] = swept["db"]["tasks"].find({"source_ref": f"return_restock_blocked:{ret['return_id']}"})
    text = blocked["description"]
    assert "booked nothing" in text and "Shopify's order history" in text and "stock-in" in text
    assert "puts each unit back" not in text and "by hand as well" not in text
    said = []
    doors = [lambda: said.append(_goods_back(row)["result"]["status"]),
             lambda: said.append(_retry(ret)["message"])]
    for door in (doors if goods_first else doors[::-1]):
        door()
    assert said[goods_first is False] == "stock_in"
    assert said[goods_first is True].startswith("Booked. This order predates IMS stock")
    assert _minted(swept) == [] and len(_stock_in(swept, oid)) == 1, "one frame, one task"
    # One open task for one frame: the re-run closed the blocked one.
    assert swept["db"]["tasks"].find_one({"task_id": blocked["task_id"]})["status"] == "COMPLETED"


def test_a_stock_in_task_that_did_not_land_leaves_the_frame_to_the_next_door(swept, monkeypatch):
    """The task insert fails (BaseRepository.create swallows it and answers
    None). Goods back answered stock_in with the id of a task that does not
    exist and kept the booking, so the confirm restocked nothing, the retry
    said "Already restocked": no task, no stock row, no door left. A task
    that did not land is no booking: released, and the next door raises it."""
    from fastapi import HTTPException

    oid, rid = 60188, 700388
    row = _historical_refund(swept, monkeypatch, oid, rid, status="DELIVERED")
    tasks = swept["db"]["tasks"]
    real = tasks.insert_one

    def insert_down(doc, *a, **kw):
        raise RuntimeError("tasks write down")

    monkeypatch.setattr(tasks, "insert_one", insert_down)
    with pytest.raises(HTTPException) as no:
        _goods_back(row)
    assert no.value.status_code == 503 and "stock_in_task_not_saved" in no.value.detail
    line = _doc(swept, oid)["items"][0]
    assert not line.get("returned_qty") and not (line.get("restocked_refunds") or {}).get(str(rid))
    monkeypatch.setattr(tasks, "insert_one", real)
    res = _confirm(swept["review"].find_one({"review_id": row["review_id"]}))["result"]
    assert res["stock_in_task"] and res["restock_applied"] is True
    assert _minted(swept) == [] and len(_stock_in(swept, oid)) == 1, "one frame, one task"


def test_a_stock_in_task_whose_reply_was_lost_is_one_task(swept, monkeypatch):
    """The task insert commits and then raises (a socket timeout after the
    commit): BaseRepository.create answers None. Released on that None, the
    booking let the confirm book the frame again and raise a second task --
    two tasks, two stock rows for one frame. The task is read back by its
    own ref first: it landed, so the booking and its one task stay."""
    oid, rid = 60194, 700394
    row = _historical_refund(swept, monkeypatch, oid, rid, status="DELIVERED")
    tasks = swept["db"]["tasks"]
    real = tasks.insert_one

    def lands_then_raises(doc, *a, **kw):
        real(doc, *a, **kw)
        raise RuntimeError("socket timeout after the commit")

    monkeypatch.setattr(tasks, "insert_one", lands_then_raises)
    back = _goods_back(row)["result"]
    monkeypatch.setattr(tasks, "insert_one", real)
    [task] = _stock_in(swept, oid)
    assert (back["status"], back["stock_in_task"]) == ("stock_in", task["task_id"])
    _confirm(swept["review"].find_one({"review_id": row["review_id"]}))
    assert _minted(swept) == [] and len(_stock_in(swept, oid)) == 1, "one frame, one task"
    line = _doc(swept, oid)["items"][0]
    assert (line.get("returned_qty"), line.get("restocked_refunds")) == (1, {str(rid): 1})


def test_a_refund_restocks_its_line_once_even_when_another_refund_left_a_unit_out(swept, monkeypatch):
    """Two frames on one line: refund R2 is money only (the customer keeps a
    frame), refund R1's frame comes back through Goods back. The line still
    has a unit out with the buyer, so only R1's own mark on the line stops
    R1's confirm booking its frame a second time (a second stock-in task)."""
    oid = 60185
    _book(swept, oid, line_items=[{**_frame_order(oid)["line_items"][0], "quantity": 2}])
    _set(swept, oid, historical=True, import_source="shopify_order_history",
         fulfillment_stores=["BV-GANGA-01"], status="DELIVERED")
    for rid, restock_type in ((700386, "no_restock"), (700385, "return")):
        shopify_refund.handle_shopify_refund(swept["db"], _refund(rid, oid, restock_type=restock_type),
                                             webhook_id=None, topic="refunds/create")
    _confirm(swept["review"].find_one({"shopify_refund_id": "700386"}))
    row = swept["review"].find_one({"shopify_refund_id": "700385"})
    _goods_back(row)
    _confirm(swept["review"].find_one({"shopify_refund_id": "700385"}))
    assert _minted(swept) == [] and len(_stock_in(swept, oid)) == 1, "one frame back, one task"


def test_one_frame_of_a_historical_order_is_one_stock_in_task_auto_then_goods_back(swept, monkeypatch):
    """AUTO booked it but could not issue the credit (no customer): the
    NO_CUSTOMER row still offers Goods back."""
    monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    row = _historical_refund(swept, monkeypatch, 60184, 700384, status="COMPLETED", customer_id=None)
    assert row["status"] == "NO_CUSTOMER" and _minted(swept) == []
    assert len(_stock_in(swept, 60184)) == 1
    _goods_back(row)
    assert _minted(swept) == [] and len(_stock_in(swept, 60184)) == 1, "one frame, one task"


@pytest.mark.parametrize("ims_product_id", [None, "IMS-P-1"])
def test_goods_back_on_a_row_confirmed_before_the_line_marks_restocks_nothing(swept, monkeypatch,
                                                                             ims_product_id):
    """A row the accountant confirmed before the restock marked the order line
    (every refund confirmed on main) carries no mark and no returned_qty, but
    its own returns doc says what its restock put back. Goods back, new on
    that row, reads it: no second frame for one (a mint then, a stock-in task
    now). The doc's rows carry the IMS product id; the order line keeps
    Shopify's product_id beside its ims_product_id, and the count reads the
    IMS one."""
    oid = 60187
    row = _historical_refund(swept, monkeypatch, oid, 700387, ims_product_id=ims_product_id,
                             status="DELIVERED")
    undo = _premark(monkeypatch)
    _confirm(row)
    undo()
    assert _minted(swept) == ["AVAILABLE"]
    ret = swept["returns"].find_one({"shopify_refund_id": "700387"})
    assert [r["product_id"] for r in ret["restocked"]] == [ims_product_id or "7001"]
    assert not _doc(swept, oid)["items"][0].get("restocked_refunds")
    back = _goods_back(swept["review"].find_one({"review_id": row["review_id"]}))["result"]
    assert _minted(swept) == ["AVAILABLE"] and _stock_in(swept, oid) == [], "one frame, one unit"
    # It says what happened -- its confirm put the frame back -- never "a task
    # asks the store manager to add the frame" with no task behind it.
    assert (back["status"], back["stock_in_task"]) == ("restocked", None)


def _same_line_twice(rid, oid):
    """Shopify's refund of a partly fulfilled line in full: the unit it never
    shipped as a "cancel" line and the shipped one as a "return" line -- one
    order line listed twice in one refund."""
    r = _refund(rid, oid, amount="1998.00")
    cancel = r["refund_line_items"][0]
    r["refund_line_items"].append({**cancel, "id": cancel["id"] + 1, "restock_type": "return"})
    return r


@pytest.mark.parametrize("door", ["auto", "confirm", "goods_back"])
def test_a_refund_that_lists_one_line_twice_restocks_each_unit_once(swept, monkeypatch, door):
    """The refund books its line once, for both units: booked per refund line,
    the second found the refund's own mark from the first, restocked nothing
    and was reported as restocked by another door -- both frames stranded SOLD."""
    oid, rid = 60190, 700390
    doc = _book(swept, oid, line_items=[{**_frame_order(oid)["line_items"][0], "quantity": 2}])
    _claim_unit(swept, doc)
    swept["stock_repo"].units.append({**swept["stock_repo"].units[0], "stock_id": "stk-2"})
    if door == "auto":
        monkeypatch.setenv("SHOPIFY_REFUND_AUTO", "1")
    shopify_refund.handle_shopify_refund(swept["db"], _same_line_twice(rid, oid), webhook_id=None,
                                         topic="refunds/create")
    row = swept["review"].find_one({"shopify_refund_id": str(rid)})
    if door == "confirm":
        assert _confirm(row)["result"]["restock_applied"] is True
    elif door == "goods_back":
        assert _goods_back(row)["result"]["status"] == "restocked"

    assert _units(swept) == [("stk-1", "AVAILABLE"), ("stk-2", "AVAILABLE")]
    line = _doc(swept, oid)["items"][0]
    assert (line.get("returned_qty"), line.get("restocked_refunds")) == (2, {str(rid): 2})


class _WriteDown:
    def find_one_and_update(self, *a, **kw):
        raise RuntimeError("orders write down")


@pytest.mark.parametrize("orders", [_WriteDown(), None], ids=["write_errors", "no_collection"])
def test_a_restock_the_order_line_could_not_book_restocks_nothing(swept, monkeypatch, orders):
    """The booking cannot be written: nothing goes ahead unbooked (the mark is
    all that stops the confirm handing the same historical frame to stock-in
    again). No task, the press is released, and a press once the write works
    books the frame once."""
    from fastapi import HTTPException

    oid = 60191
    row = _historical_refund(swept, monkeypatch, oid, 700391, ims_product_id="IMS-P-1",
                             status="DELIVERED")
    real = returns_router._orders_coll
    monkeypatch.setattr(returns_router, "_orders_coll", lambda: orders)
    with pytest.raises(HTTPException) as first:
        _goods_back(row)
    assert first.value.status_code == 503 and _stock_in(swept, oid) == []
    monkeypatch.setattr(returns_router, "_orders_coll", real)
    assert _goods_back(row)["result"]["status"] == "stock_in"
    _confirm(swept["review"].find_one({"review_id": row["review_id"]}))
    assert _minted(swept) == [] and len(_stock_in(swept, oid)) == 1, "one frame, one task"


def test_a_booking_that_errors_part_way_releases_the_line_it_booked(monkeypatch):
    """Line a booked, line b's write errors: a stays booked no longer (it would
    read as restocked with its unit still SOLD)."""
    from api.routers.returns import ReturnLine

    order = {"order_id": "O1", "items": [{"item_id": i, "product_id": i, "quantity": 1} for i in "ab"]}
    lines = [ReturnLine(order_item_id=i, product_id=i, return_qty=1, unit_price=0.0) for i in "ab"]
    calls = []

    def claim(oid, orig, qty, rid, units):
        if orig["item_id"] == "b":
            raise RuntimeError("orders write down")
        calls.append(("claim", orig["item_id"]))
        return True

    monkeypatch.setattr(returns_router, "_claim_returnable_qty", claim)
    monkeypatch.setattr(returns_router, "_release_returnable_qty",
                        lambda oid, orig, qty, rid: calls.append(("release", orig["item_id"])))
    with pytest.raises(RuntimeError):
        shopify_refund._hold_returned_qty(order, lines, "R1")
    assert calls == [("claim", "a"), ("release", "a")]


# A refund restocks each of its units once and no unit it did not refund, per
# ORDER LINE: its mark on a line counts the units its restock put back there,
# and any door may put back the rest of the refund's units on that line later
# (the held one the customer brings back, the one a partial restock missed).


def _two_lines_one_product(swept, oid, rid, second):
    """Two Shopify lines (9001, 9002) of ONE IMS product -- two identical
    frames with different Rx -- each with its unit SOLD. One refund lists 9001
    as a "return" and 9002 as `second`."""
    line = _frame_order(oid)["line_items"][0]
    doc = _book(swept, oid, line_items=[line, {**line, "id": 9002}])
    _set(swept, oid, status="DELIVERED", fulfillment_stores=["BV-GANGA-01"],
         items=[{**i, "ims_product_id": "IMS-P-1"} for i in doc["items"]])
    swept["stock_repo"].units.extend(
        {"stock_id": sid, "product_id": "IMS-P-1", "store_id": "BV-GANGA-01",
         "order_id": doc["order_id"], "status": "SOLD"} for sid in ("stk-1", "stk-2"))
    r = _refund(rid, oid, restock_type="return", amount="1998.00")
    a = r["refund_line_items"][0]
    r["refund_line_items"].append({**a, "id": a["id"] + 1, "line_item_id": 9002, "restock_type": second,
                                   "line_item": {**a["line_item"], "id": 9002}})
    shopify_refund.handle_shopify_refund(swept["db"], r, webhook_id=None, topic="refunds/create")
    return swept["review"].find_one({"shopify_refund_id": str(rid)})


def _lands_only_first(monkeypatch, qty=None):
    """The restock puts back only its first line's units (or `qty` of them)
    and says it did not land whole."""
    real = returns_router._restock_good_items

    def first(lines, *a, **kw):
        head = lines[0].model_copy(update={"return_qty": qty}) if qty else lines[0]
        return {**real([head], *a, **kw), "applied": False}

    monkeypatch.setattr(returns_router, "_restock_good_items", first)
    return real


def test_goods_back_restocks_the_held_line_beside_a_restocked_line_of_one_product(swept):
    """The own-doc count read the doc's per-PRODUCT restocked rows for every
    line of that product: line 9002's frame, held at the confirm, counted as
    already back, and Goods back answered "restocked" with stk-2 still SOLD."""
    row = _two_lines_one_product(swept, 60192, 700392, "no_restock")
    assert [line["restock"] for line in row["proposed_restock"]] == [True, False]
    _confirm(row)
    assert sorted(s for _, s in _units(swept)) == ["AVAILABLE", "SOLD"]
    got = _goods_back(swept["review"].find_one({"review_id": row["review_id"]}))["result"]
    assert got["status"] == "restocked" and len(got["restock_stock_ids"]) == 1
    assert _units(swept) == [("stk-1", "AVAILABLE"), ("stk-2", "AVAILABLE")]


def test_the_retry_restocks_the_line_whose_unit_did_not_land_beside_one_of_its_product(
        swept, monkeypatch):
    row = _two_lines_one_product(swept, 60193, 700393, "return")
    real = _lands_only_first(monkeypatch)
    assert _confirm(row)["result"]["restock_applied"] is False
    monkeypatch.setattr(returns_router, "_restock_good_items", real)
    assert sorted(s for _, s in _units(swept)) == ["AVAILABLE", "SOLD"]
    assert _retry(swept["returns"].find_one({"shopify_refund_id": "700393"}))["restock_applied"] is True
    assert _units(swept) == [("stk-1", "AVAILABLE"), ("stk-2", "AVAILABLE")]


@pytest.mark.parametrize("held", ["cancel", "no_restock"])
def test_goods_back_restocks_the_held_unit_of_a_line_the_confirm_partly_restocked(swept, held):
    """A qty-2 line refunded in full on an order the counter handed over: one
    unit held (a "cancel" line, ruling 2026-09-28, or a "no_restock" one), one
    restocked by the confirm. The line's mark used to stop every later door
    for this refund: Goods back answered "restocked" with stk-2 SOLD forever."""
    oid, rid = 60194 + (held == "no_restock"), 700394 + (held == "no_restock")
    doc = _book(swept, oid, line_items=[{**_frame_order(oid)["line_items"][0], "quantity": 2}])
    _claim_unit(swept, doc)
    swept["stock_repo"].units.append({**swept["stock_repo"].units[0], "stock_id": "stk-2"})
    _set(swept, oid, **_HANDED_OVER)
    r = _same_line_twice(rid, oid)
    r["refund_line_items"][0]["restock_type"] = held
    shopify_refund.handle_shopify_refund(swept["db"], r, webhook_id=None, topic="refunds/create")
    row = swept["review"].find_one({"shopify_refund_id": str(rid)})
    assert [(line["return_qty"], line["restock"]) for line in row["proposed_restock"]] == [
        (1, False), (1, True)]
    _confirm(row)
    assert sorted(s for _, s in _units(swept)) == ["AVAILABLE", "SOLD"]

    got = _goods_back(swept["review"].find_one({"review_id": row["review_id"]}))["result"]
    assert got["status"] == "restocked" and len(got["restock_stock_ids"]) == 1
    assert _units(swept) == [("stk-1", "AVAILABLE"), ("stk-2", "AVAILABLE")]
    line = _doc(swept, oid)["items"][0]
    assert (line.get("returned_qty"), line.get("restocked_refunds")) == (2, {str(rid): 2})


def test_goods_back_restocks_the_held_unit_beside_a_unit_the_refund_never_covered(swept):
    """A qty-3 line; the refund covers two units (one held, one restocked by
    the confirm), the third was never refunded. Goods back may put back only
    the refund's units less what its restock already did (its own share):
    counted from the line's units still out, it asked for two, found no room
    under the refund's mark and refused every press -- the held unit
    stranded SOLD."""
    oid, rid = 60199, 700399
    doc = _book(swept, oid, line_items=[{**_frame_order(oid)["line_items"][0], "quantity": 3}])
    _claim_unit(swept, doc)
    swept["stock_repo"].units.extend({**swept["stock_repo"].units[0], "stock_id": sid}
                                     for sid in ("stk-2", "stk-3"))
    _set(swept, oid, status="DELIVERED")
    r = _same_line_twice(rid, oid)
    r["refund_line_items"][0]["restock_type"] = "no_restock"
    shopify_refund.handle_shopify_refund(swept["db"], r, webhook_id=None, topic="refunds/create")
    row = swept["review"].find_one({"shopify_refund_id": str(rid)})
    assert [(line["return_qty"], line["restock"]) for line in row["proposed_restock"]] == [
        (1, False), (1, True)]
    _confirm(row)
    assert sorted(s for _, s in _units(swept)) == ["AVAILABLE", "SOLD", "SOLD"]

    got = _goods_back(swept["review"].find_one({"review_id": row["review_id"]}))["result"]
    assert got["status"] == "restocked" and len(got["restock_stock_ids"]) == 1
    assert sorted(s for _, s in _units(swept)) == ["AVAILABLE", "AVAILABLE", "SOLD"]
    line = _doc(swept, oid)["items"][0]
    assert (line.get("returned_qty"), line.get("restocked_refunds")) == (2, {str(rid): 2})


def test_the_retry_restocks_the_unit_a_partly_landed_confirm_left_sold(swept, monkeypatch):
    """One qty-2 "return" line; the confirm put back one unit (restock not
    applied). The retry saw the line's mark, restocked nothing and answered
    "Restock applied" with stk-2 still SOLD."""
    oid, rid = 60196, 700396
    doc = _book(swept, oid, line_items=[{**_frame_order(oid)["line_items"][0], "quantity": 2}])
    _claim_unit(swept, doc)
    swept["stock_repo"].units.append({**swept["stock_repo"].units[0], "stock_id": "stk-2"})
    _set(swept, oid, status="DELIVERED")
    r = _refund_both(rid, oid)
    r["refund_line_items"][0]["restock_type"] = "return"
    shopify_refund.handle_shopify_refund(swept["db"], r, webhook_id=None, topic="refunds/create")
    row = swept["review"].find_one({"shopify_refund_id": str(rid)})
    real = _lands_only_first(monkeypatch, qty=1)
    assert _confirm(row)["result"]["restock_applied"] is False
    monkeypatch.setattr(returns_router, "_restock_good_items", real)
    assert sorted(s for _, s in _units(swept)) == ["AVAILABLE", "SOLD"]

    assert _retry(swept["returns"].find_one({"shopify_refund_id": str(rid)}))["restock_applied"] is True
    assert _units(swept) == [("stk-1", "AVAILABLE"), ("stk-2", "AVAILABLE")]
    line = _doc(swept, oid)["items"][0]
    assert (line.get("returned_qty"), line.get("restocked_refunds")) == (2, {str(rid): 2})


class _CommitsThenRaises:
    """The first find_one_and_update commits, then its reply is lost (a
    socket timeout after the commit; a standalone mongod retries no write)."""

    def __init__(self, real):
        self.real, self.lost = real, False

    def __getattr__(self, name):
        return getattr(self.real, name)

    def find_one_and_update(self, *a, **kw):
        out = self.real.find_one_and_update(*a, **kw)
        if not self.lost:
            self.lost = True
            raise RuntimeError("socket timeout after the commit")
        return out


def test_a_booking_whose_reply_was_lost_after_it_committed_restocks_the_unit(swept, monkeypatch):
    """The claim committed but raised: the line read as restocked by this
    refund with stk-1 still SOLD, and every later door agreed -- the unit
    stranded SOLD behind a "Restock applied"."""
    row = _one_unit_refund(swept, 60197, 700397)
    lossy = _CommitsThenRaises(returns_router._orders_coll())
    monkeypatch.setattr(returns_router, "_orders_coll", lambda: lossy)
    assert _confirm(row)["result"]["restock_applied"] is True
    assert _units(swept) == [("stk-1", "AVAILABLE")]
    _retry(swept["returns"].find_one({"shopify_refund_id": "700397"}))
    _goods_back(swept["review"].find_one({"review_id": row["review_id"]}))
    assert _units(swept) == [("stk-1", "AVAILABLE")], "one unit, once"
    line = _doc(swept, 60197)["items"][0]
    assert (line.get("returned_qty"), line.get("restocked_refunds")) == (1, {"700397": 1})


class _RaisesBeforeTheWrite(_CommitsThenRaises):
    """Every find_one_and_update fails before the server sees it."""

    def find_one_and_update(self, *a, **kw):
        raise RuntimeError("connection reset before the write")


def test_a_booking_that_did_not_land_never_reads_another_doors_booking_as_its_own(swept, monkeypatch):
    """A door read the order, then Goods back booked the frame, then the
    door's own booking failed without landing. The read-back compared the
    line's mark with the door's stale read: Goods back's unit read as its own,
    and the door put the historical frame back a second time. Each attempt
    now looks for its own token on the line."""
    oid, rid = 60198, 700398
    row = _historical_refund(swept, monkeypatch, oid, rid, ims_product_id="IMS-P-1", status="DELIVERED")
    stale = copy.deepcopy(_doc(swept, oid))
    lines = shopify_refund._return_lines_from_proposed(row["proposed_restock"])
    assert _goods_back(row)["result"]["status"] == "stock_in"

    down = _RaisesBeforeTheWrite(returns_router._orders_coll())
    monkeypatch.setattr(returns_router, "_orders_coll", lambda: down)
    stale.update(fulfillment_stores=["BV-GANGA-01"])
    with pytest.raises(RuntimeError):
        shopify_refund._restock_booked(stale, lines, str(rid), lambda ls: returns_router._restock_good_items(
            ls, stale["store_id"], "RET-X", order_id=stale["order_id"], user_id="u",
            processing_store_id=None, order=stale))
    assert _minted(swept) == [] and len(_stock_in(swept, oid)) == 1, "one frame, one task"
    line = _doc(swept, oid)["items"][0]
    assert (line.get("returned_qty"), line.get("restocked_refunds")) == (1, {str(rid): 1})


# ---------------------------------------------------------------------------
# Ruling 1 leaves a fulfilled online order SHIPPED (it used to be DELIVERED).
# Every report that picks orders by status reads the ONE pair of sets in
# online_order_status; the Tally export and the commission ledgers are pinned
# in test_tally_export / test_order_attribution_sweep, the widgets here.
# ---------------------------------------------------------------------------


def test_a_shipped_online_sale_counts_in_the_revenue_widgets(monkeypatch):
    from datetime import timezone

    from strict_fakes import StrictDB

    from api.routers import dashboard_widgets as dw

    db = StrictDB()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.seed("orders", [
        {"order_id": "O1", "store_id": "S1", "status": "SHIPPED", "created_at": now, "grand_total": 999.0},
        {"order_id": "O2", "store_id": "S1", "status": "CANCELLED", "created_at": now, "grand_total": 5.0},
    ])
    monkeypatch.setattr(dw, "_coll", lambda name: db.get_collection(name))
    user = {"user_id": "u-1", "roles": ["ADMIN"], "active_store_id": "S1"}
    month = asyncio.run(dw.finance_summary_month(store_id="S1", current_user=user))
    assert month == {"revenue_month": 999.0, "orders_month": 1}
    today = asyncio.run(dw.analytics_store_target_today(store_id="S1", current_user=user))
    assert today["achieved_today"] == 999.0


def test_no_report_keeps_its_own_copy_of_the_sale_status_sets():
    """A local copy is how SHIPPED went missing from the Tally export, the
    commission ledgers, the revenue widgets, the stock reports and the RFM
    segments. Read as Python, not as text: a copy written over several lines,
    in any case, is still a copy."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    done, booked = {"DELIVERED", "PAID"}, {"CONFIRMED", "PROCESSING", "READY", "DELIVERED"}
    hits = []
    for p in (q for d in ("api", "agents") for q in (root / d).rglob("*.py")):
        if p.name == "online_order_status.py":
            continue
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
                vals = {e.value.upper() for e in node.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)}
                if done <= vals or booked <= vals:
                    hits.append(f"{p.relative_to(root)}:{node.lineno}")
    assert hits == []
    assert "SHIPPED" in oos.SALE_DONE_STATUSES and "SHIPPED" in oos.BOOKED_STATUSES
    assert {"SHIPPED", "Delivered", "fulfilled"} <= set(oos.SALE_DONE_ANY_CASE)


def test_a_shipped_online_sale_keeps_its_customer_in_the_rfm_segments(monkeypatch):
    from datetime import timedelta, timezone

    from strict_fakes import StrictDB

    from api.routers import crm

    db = StrictDB()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.seed("orders", [
        {"order_id": "O1", "customer_id": "c1", "status": "SHIPPED", "grand_total": 30000.0,
         "created_at": now - timedelta(days=3)},
        {"order_id": "O2", "customer_id": "c2", "status": "CANCELLED", "grand_total": 30000.0,
         "created_at": now - timedelta(days=3)},
    ])
    monkeypatch.setattr(crm, "_crm_get_db", lambda: db)
    segments = crm._perform_rfm_segmentation([{"customer_id": "c1"}, {"customer_id": "c2"}])
    assert sum(s["customer_count"] for s in segments) == 1


# ---------------------------------------------------------------------------
# The counter return door sees a Goods back. Goods back writes no return doc,
# so the units it puts back are booked returned on the order line -- the
# count the counter's own atomic claim reads. The counter then cannot take the
# same unit back a second time (a phantom unit on a live shelf, and the money
# paid twice); a unit the customer still holds stays returnable.
# ---------------------------------------------------------------------------

_ADMIN = {"user_id": "adm-1", "roles": ["ADMIN"], "active_store_id": "BV-GANGA-01"}


def _counter_return(swept, oid):
    from api.routers.returns import ReturnCreate, ReturnLine

    doc = _doc(swept, oid)
    line = doc["items"][0]
    body = ReturnCreate(order_id=doc["order_id"], return_type="CREDIT_NOTE", items=[ReturnLine(
        order_item_id=line.get("item_id"), product_id=line.get("ims_product_id"),
        product_name=line.get("product_name") or "", return_qty=1, unit_price=0.0,
        condition="GOOD")])
    return asyncio.run(returns_router.create_return(body=body, current_user=_ADMIN,
                                                    idempotency_key=None))


def _refused(swept, oid):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as refused:
        _counter_return(swept, oid)
    assert refused.value.status_code == 400, refused.value.detail
    assert "exceeds the returnable quantity 0" in refused.value.detail


@pytest.mark.parametrize("lands", [False, True])
def test_a_counter_claim_that_errored_is_decided_by_the_line_read_again(swept, monkeypatch, lands):
    """The claim's write raised. It went ahead as reserved whether or not it
    landed: unreserved, a second return of the unit could pass beside it, and
    its release on a later failure took back another door's units. Not
    landed: refused for a retry, nothing recorded. Landed (a lost reply): the
    return is booked, once."""
    from fastapi import HTTPException

    oid = 60177 + lands
    _claim_unit(swept, _book(swept, oid))
    _set(swept, oid, status="DELIVERED")
    real = returns_router._orders_coll()
    flaky = (_CommitsThenRaises if lands else _RaisesBeforeTheWrite)(real)
    monkeypatch.setattr(returns_router, "_orders_coll", lambda: flaky)
    if lands:
        assert _counter_return(swept, oid)["return_id"]
    else:
        with pytest.raises(HTTPException) as refused:
            _counter_return(swept, oid)
        assert refused.value.status_code == 409 and swept["returns"].count_documents({}) == 0
        assert "returned_qty" not in _doc(swept, oid)["items"][0]
        monkeypatch.setattr(returns_router, "_orders_coll", lambda: real)
        assert _counter_return(swept, oid)["return_id"]
    assert _doc(swept, oid)["items"][0]["returned_qty"] == 1
    _refused(swept, oid)


def test_the_counter_cannot_take_back_the_unit_goods_back_put_back(swept):
    row = _one_unit_refund(swept, 60174, 700374, status="DELIVERED", payment_status="PARTIAL_REFUND")
    assert _goods_back(row)["result"]["status"] == "restocked"
    _refused(swept, 60174)
    assert _units(swept) == [("stk-1", "AVAILABLE")], "no phantom minted"
    assert swept["ledger"].count_documents({}) == 0, "no second refund"


def test_the_counter_still_takes_back_the_unit_the_customer_holds(swept):
    """Two frames sold, Shopify refunded one and it came back through Goods
    back: the counter takes back the OTHER frame, once."""
    doc = _book(swept, 60175, line_items=[{**_frame_order(60175)["line_items"][0], "quantity": 2}])
    _claim_unit(swept, doc)
    swept["stock_repo"].units.append({**swept["stock_repo"].units[0], "stock_id": "stk-2"})
    _set(swept, 60175, status="DELIVERED", payment_status="PARTIAL_REFUND")
    shopify_refund.handle_shopify_refund(swept["db"], _refund(700375, 60175), webhook_id=None,
                                         topic="refunds/create")
    row = swept["review"].find_one({"shopify_refund_id": "700375"})
    assert _goods_back(row)["result"]["status"] == "restocked"
    assert _units(swept) == [("stk-1", "AVAILABLE"), ("stk-2", "SOLD")]

    assert _counter_return(swept, 60175)["return_id"]
    _refused(swept, 60175)
    assert _units(swept) == [("stk-1", "AVAILABLE"), ("stk-2", "AVAILABLE")], "no phantom minted"


def test_a_goods_back_that_did_not_land_books_nothing_returned(swept, monkeypatch):
    """No unit went back on a shelf (the restock did not land): the press is
    released for another go, and so is the returned count -- else the counter
    would refuse the customer's real return of a unit still SOLD."""
    from fastapi import HTTPException

    row = _one_unit_refund(swept, 60176, 700376, status="DELIVERED")
    monkeypatch.setattr(returns_router, "_restock_good_items", lambda *a, **kw: {"applied": False})
    with pytest.raises(HTTPException) as first:
        _goods_back(row)
    assert first.value.status_code == 503 and _units(swept) == [("stk-1", "SOLD")]
    assert not _doc(swept, 60176)["items"][0].get("returned_qty")


@pytest.mark.parametrize("first", ["return_confirmed_before_the_marks", "money_only_refund"])
def test_the_counter_counts_goods_back_beside_a_refund_that_has_only_its_doc(swept, first):
    """Two frames sold. One refund already has its return doc and no booking
    on the line: a return confirmed before the marks (the state of every
    refund confirmed on main), or a money-only refund (the customer keeps
    that frame). Refund R1's frame then comes back through Goods back, before
    its confirm: booked on the line, no doc yet. Both frames are accounted
    for, so the counter takes nothing back -- it used to read the larger of
    the two books (1), not both (2), and mint or reactivate a third unit."""
    oid, earlier, r1 = 60188, 700388, 700389
    doc = _book(swept, oid, line_items=[{**_frame_order(oid)["line_items"][0], "quantity": 2}])
    _claim_unit(swept, doc)
    swept["stock_repo"].units.append({**swept["stock_repo"].units[0], "stock_id": "stk-2"})
    _set(swept, oid, status="DELIVERED", payment_status="PARTIAL_REFUND")
    restock_type = "return" if first == "return_confirmed_before_the_marks" else "no_restock"
    shopify_refund.handle_shopify_refund(swept["db"], _refund(earlier, oid, restock_type=restock_type),
                                         webhook_id=None, topic="refunds/create")
    _confirm(swept["review"].find_one({"shopify_refund_id": str(earlier)}))
    if first == "return_confirmed_before_the_marks":
        items = _doc(swept, oid)["items"]
        for item in items:
            item.pop("returned_qty", None)
            item.pop("restocked_refunds", None)
        _set(swept, oid, items=items)
    shopify_refund.handle_shopify_refund(swept["db"], _refund(r1, oid, restock_type="return"),
                                         webhook_id=None, topic="refunds/create")
    assert _goods_back(swept["review"].find_one({"shopify_refund_id": str(r1)}))["result"]["status"] == "restocked"
    back = ["AVAILABLE", "AVAILABLE"] if first == "return_confirmed_before_the_marks" else ["AVAILABLE", "SOLD"]
    assert sorted(s for _, s in _units(swept)) == back

    _refused(swept, oid)
    _confirm(swept["review"].find_one({"shopify_refund_id": str(r1)}))
    assert len(swept["stock_repo"].units) == 2, "no phantom minted"
    assert swept["ledger"].count_documents({}) == 2, "one credit note per refund, none at the counter"


def test_a_partly_landed_goods_back_keeps_its_mark_at_the_units_that_landed(swept, monkeypatch):
    """A two-unit refund line whose restock put back one unit: the line stays
    booked for that one (restocked_refunds and returned_qty), so the counter
    still takes back the other."""
    oid, rid = 60189, 700387
    doc = _book(swept, oid, line_items=[{**_frame_order(oid)["line_items"][0], "quantity": 2}])
    _claim_unit(swept, doc)
    swept["stock_repo"].units.append({**swept["stock_repo"].units[0], "stock_id": "stk-2"})
    _set(swept, oid, status="DELIVERED", payment_status="PARTIAL_REFUND")
    shopify_refund.handle_shopify_refund(swept["db"], _refund_both(rid, oid), webhook_id=None,
                                         topic="refunds/create")
    real = returns_router._restock_good_items

    def one_lands(lines, *a, **kw):
        return real([lines[0].model_copy(update={"return_qty": 1})], *a, **kw)

    monkeypatch.setattr(returns_router, "_restock_good_items", one_lands)
    _goods_back(swept["review"].find_one({"shopify_refund_id": str(rid)}))
    line = _doc(swept, oid)["items"][0]
    assert (line.get("returned_qty"), line.get("restocked_refunds")) == (1, {str(rid): 1})


# ---------------------------------------------------------------------------
# An answer that could not be read is no answer (panel round 11): the returns
# scan, and a claim whose reply AND read-back were both lost.
# ---------------------------------------------------------------------------


class _ScanFailsOnce:
    """The returns collection; its first find raises (a blip)."""

    def __init__(self, real):
        self.real, self.failed = real, False

    def __getattr__(self, name):
        return getattr(self.real, name)

    def find(self, *a, **kw):
        if not self.failed:
            self.failed = True
            raise RuntimeError("returns scan timed out")
        return self.real.find(*a, **kw)


def test_goods_back_on_a_premark_row_restocks_nothing_while_the_returns_scan_blips(swept, monkeypatch):
    """A row confirmed before the line marks (every refund confirmed on main)
    has only its own returns doc to say its restock minted the frame. The
    scan failing read as nothing back: Goods back put it back a second time."""
    from fastapi import HTTPException

    oid = 60201
    row = _historical_refund(swept, monkeypatch, oid, 700401, status="DELIVERED")
    undo = _premark(monkeypatch)
    _confirm(row)
    undo()
    assert _minted(swept) == ["AVAILABLE"]
    blip = _ScanFailsOnce(returns_router._returns_coll())
    monkeypatch.setattr(returns_router, "_returns_coll", lambda: blip)
    posted = swept["review"].find_one({"review_id": row["review_id"]})
    with pytest.raises(HTTPException) as first:
        _goods_back(posted)
    assert first.value.status_code == 503 and blip.failed
    _goods_back(posted)
    assert _minted(swept) == ["AVAILABLE"] and _stock_in(swept, oid) == [], "one frame, one unit"


def _two_frame_refund(swept, oid, rid, **order_set):
    """A line of two frames and ONE refund "return" line of both."""
    _book(swept, oid, line_items=[{**_frame_order(oid)["line_items"][0], "quantity": 2}])
    if order_set:
        _set(swept, oid, **order_set)
    refund = _refund(rid, oid, restock_type="return", amount="1998.00")
    refund["refund_line_items"][0]["quantity"] = 2
    shopify_refund.handle_shopify_refund(swept["db"], refund, webhook_id=None, topic="refunds/create")
    return swept["review"].find_one({"shopify_refund_id": str(rid)})


def _create_fails_after(monkeypatch, repo, n):
    real, calls = repo.create, []

    def flaky(data):
        calls.append(data)
        if len(calls) > n:
            raise RuntimeError("stock insert failed")
        return real(data)

    monkeypatch.setattr(repo, "create", flaky)
    return lambda: monkeypatch.setattr(repo, "create", real)


def _premark_one_of_two(swept, monkeypatch, row):
    """The confirm as it ran before the marks, landing one of the two frames
    (a mint) and leaving the restock open."""
    undo, undo_create = _premark(monkeypatch), _create_fails_after(monkeypatch, swept["stock_repo"], 1)
    real = returns_router._reactivate_original_unit
    monkeypatch.setattr(returns_router, "_reactivate_original_unit", lambda *a, **kw: None)
    assert _confirm(row)["result"]["restock_applied"] is False
    monkeypatch.setattr(returns_router, "_reactivate_original_unit", real)
    undo()
    undo_create()


def test_a_premark_unit_and_a_booked_one_are_two_units_on_a_historical_order(swept, monkeypatch):
    """Panel P1(b). A confirm before the marks put one of two frames back
    and left the restock open; the retry books the other. Its doc's unit was
    never booked, so the larger of doc and mark read 1 of 2 back: Goods back
    then booked a third frame. The retry also wrote its own rows over the
    doc's, and the first frame left the doc that counts it. Two frames: the
    one unit the old confirm minted, and one stock-in task for the other."""
    oid, rid = 60206, 700406
    row = _two_frame_refund(swept, oid, rid, historical=True, import_source="shopify_order_history",
                            fulfillment_stores=["BV-GANGA-01"], status="DELIVERED")
    _premark_one_of_two(swept, monkeypatch, row)
    assert _minted(swept) == ["AVAILABLE"]
    ret = swept["returns"].find_one({"shopify_refund_id": str(rid)})
    assert _retry(ret)["stock_in_task"]
    assert [r.get("minted") for r in swept["returns"].find_one({"shopify_refund_id": str(rid)})["restocked"]] == [1]
    _goods_back(swept["review"].find_one({"review_id": row["review_id"]}))
    assert _minted(swept) == ["AVAILABLE"]
    [task] = _stock_in(swept, oid)
    assert task["title"].endswith("add 1 returned frame(s) through stock-in")


def test_a_blocked_retry_keeps_the_frame_a_premark_confirm_put_back(swept, monkeypatch):
    """Two frames, one refund for both; the confirm before the marks minted
    one and left the restock open. The /returns/{id}/restock retry runs
    while the order cannot be read (blocked). Its doc rows MERGE: written
    over, the minted frame left the doc that counts it and Goods back asked
    for 2 frames through stock-in beside the 1 stock row -- 3 rows for 2."""
    oid, rid = 60211, 700411
    row = _two_frame_refund(swept, oid, rid, historical=True, import_source="shopify_order_history",
                            fulfillment_stores=["BV-GANGA-01"], status="DELIVERED")
    _premark_one_of_two(swept, monkeypatch, row)
    assert _minted(swept) == ["AVAILABLE"]
    real = returns_router._load_order_for_restock
    monkeypatch.setattr(returns_router, "_load_order_for_restock", lambda order_id: None)
    blocked = _retry(swept["returns"].find_one({"shopify_refund_id": str(rid)}))
    monkeypatch.setattr(returns_router, "_load_order_for_restock", real)
    assert blocked["restock_applied"] is False and "could not be read" in blocked["message"]
    ret = swept["returns"].find_one({"shopify_refund_id": str(rid)})
    [task] = swept["db"]["tasks"].find({"source_ref": f"return_restock_blocked:{ret['return_id']}"})
    assert "Shopify's order history" in task["description"], "unread order: both kinds' re-run said"
    assert [r.get("minted") for r in swept["returns"].find_one(
        {"shopify_refund_id": str(rid)})["restocked"]][:1] == [1]
    _goods_back(swept["review"].find_one({"review_id": row["review_id"]}))
    assert _minted(swept) == ["AVAILABLE"]
    [stock_in] = _stock_in(swept, oid)
    assert stock_in["title"].endswith("add 1 returned frame(s) through stock-in")
    assert swept["db"]["tasks"].find_one({"task_id": task["task_id"]})["status"] == "COMPLETED"


def test_a_premark_unit_and_a_booked_one_are_two_units(swept, monkeypatch):
    """The same on a live order whose pre-mark confirm minted one frame while
    both SOLD units stayed SOLD (the SOLD cap then does not stop it): the
    retry reactivates one, and Goods back finds both frames back."""
    oid, rid = 60207, 700407
    doc = _book(swept, oid, line_items=[{**_frame_order(oid)["line_items"][0], "quantity": 2}])
    _claim_unit(swept, doc)
    swept["stock_repo"].units.append({**swept["stock_repo"].units[0], "stock_id": "stk-2"})
    refund = _refund(rid, oid, restock_type="return", amount="1998.00")
    refund["refund_line_items"][0]["quantity"] = 2
    shopify_refund.handle_shopify_refund(swept["db"], refund, webhook_id=None, topic="refunds/create")
    row = swept["review"].find_one({"shopify_refund_id": str(rid)})
    _premark_one_of_two(swept, monkeypatch, row)
    _retry(swept["returns"].find_one({"shopify_refund_id": str(rid)}))
    _goods_back(swept["review"].find_one({"review_id": row["review_id"]}))
    units = sorted((u["stock_id"], u["status"]) for u in swept["stock_repo"].units)
    assert units == [("stk-1", "AVAILABLE"), ("stk-2", "SOLD"), ("stk-mint-1", "AVAILABLE")],         "two frames back, two AVAILABLE rows"


def test_a_redrive_keeps_the_units_an_earlier_attempt_put_back(swept, monkeypatch):
    """A guest refund's confirm cannot issue the credit (no customer), so the
    row stays confirmable and the next confirm re-drives it. The first one
    (before the marks) put one of two frames back; the re-drive books the
    other. It wrote its rows over the doc's: the first frame left the doc
    that counts it, and Goods back put it back a second time."""
    from fastapi import HTTPException

    oid, rid = 60209, 700409
    doc = _book(swept, oid, line_items=[{**_frame_order(oid)["line_items"][0], "quantity": 2}])
    _claim_unit(swept, doc)
    swept["stock_repo"].units.append({**swept["stock_repo"].units[0], "stock_id": "stk-2"})
    _set(swept, oid, customer_id=None)
    refund = _refund(rid, oid, restock_type="return", amount="1998.00")
    refund["refund_line_items"][0]["quantity"] = 2
    shopify_refund.handle_shopify_refund(swept["db"], refund, webhook_id=None, topic="refunds/create")
    row = swept["review"].find_one({"shopify_refund_id": str(rid)})
    undo, undo_create = _premark(monkeypatch), _create_fails_after(monkeypatch, swept["stock_repo"], 1)
    real = returns_router._reactivate_original_unit
    monkeypatch.setattr(returns_router, "_reactivate_original_unit", lambda *a, **kw: None)
    with pytest.raises(HTTPException):
        _confirm(row)
    monkeypatch.setattr(returns_router, "_reactivate_original_unit", real)
    undo()
    undo_create()
    with pytest.raises(HTTPException):
        _confirm(swept["review"].find_one({"review_id": row["review_id"]}))
    ret = swept["returns"].find_one({"shopify_refund_id": str(rid)})
    assert sorted(r.get("minted", 0) for r in ret["restocked"]) == [0, 1]
    _goods_back(swept["review"].find_one({"review_id": row["review_id"]}))
    units = sorted((u["stock_id"], u["status"]) for u in swept["stock_repo"].units)
    assert units == [("stk-1", "AVAILABLE"), ("stk-2", "SOLD"), ("stk-mint-1", "AVAILABLE")],         "two frames back, two AVAILABLE rows"


def test_a_stampless_historical_frame_goes_to_the_retrying_shops_manager(swept, monkeypatch):
    """Our history import never ran the stock decrement, so it carries no
    fulfilment stamp: the retry names the shop the restock router picks for
    it (the retrying person's own shop) and that shop's store manager."""
    oid, rid = 60210, 700410
    row = _historical_refund(swept, monkeypatch, oid, rid, status="DELIVERED")
    _set(swept, oid, fulfillment_stores=None)
    blip = _ScanFailsOnce(returns_router._returns_coll())
    monkeypatch.setattr(returns_router, "_returns_coll", lambda: blip)
    assert _confirm(row)["result"]["restock_applied"] is False
    out = _retry(swept["returns"].find_one({"shopify_refund_id": str(rid)}))
    assert out["restock_applied"] is True and "stock-in" in out["message"]
    [task] = _stock_in(swept, oid)
    assert (task["store_id"], task["assigned_to"]) == ("BV-GANGA-01", _MANAGER)


class _MissesThenReadDown:
    """The claim's write fails before the server sees it, and so does its
    read-back; the take-back after them goes through."""

    def __init__(self, real):
        self.real, self.missed, self.read_down = real, False, False

    def __getattr__(self, name):
        return getattr(self.real, name)

    def find_one_and_update(self, *a, **kw):
        if not self.missed:
            self.missed = True
            raise RuntimeError("connection reset before the write")
        return self.real.find_one_and_update(*a, **kw)

    def find_one(self, *a, **kw):
        if self.missed and not self.read_down:
            self.read_down = True
            raise RuntimeError("read timed out")
        return self.real.find_one(*a, **kw)


def test_a_take_back_takes_back_only_its_own_write(swept, monkeypatch):
    """Goods back booked the frame. A door working from a stale read books it
    too: its write fails before it lands and so does its read-back, so it is
    taken back -- by its own token, which is not on the line. Taken back by
    the line's identity it un-booked Goods back's frame, and the next door
    handed that frame to stock-in a second time."""
    oid, rid = 60208, 700408
    row = _historical_refund(swept, monkeypatch, oid, rid, ims_product_id="IMS-P-1", status="DELIVERED")
    stale = copy.deepcopy(_doc(swept, oid))
    lines = shopify_refund._return_lines_from_proposed(row["proposed_restock"])
    assert _goods_back(row)["result"]["status"] == "stock_in"
    lossy = _MissesThenReadDown(returns_router._orders_coll())
    monkeypatch.setattr(returns_router, "_orders_coll", lambda: lossy)
    with pytest.raises(RuntimeError):
        shopify_refund._restock_booked(stale, lines, str(rid), lambda ls: {"applied": True})
    assert lossy.missed and lossy.read_down
    line = _doc(swept, oid)["items"][0]
    assert (line.get("returned_qty"), line.get("restocked_refunds")) == (1, {str(rid): 1})
    _confirm(swept["review"].find_one({"review_id": row["review_id"]}))
    assert len(_stock_in(swept, oid)) == 1, "one frame, one task"


class _CommitsThenReadDown(_CommitsThenRaises):
    """The lost reply, and the read-back right after it fails too: a partition
    that eats the one usually eats the other."""

    read_down = False

    def find_one(self, *a, **kw):
        if self.lost and not self.read_down:
            self.read_down = True
            raise RuntimeError("read timed out")
        return self.real.find_one(*a, **kw)


def test_a_booking_whose_reply_and_read_back_were_lost_is_taken_back_for_the_retry(swept, monkeypatch):
    """The booking committed, its reply was lost and so was the read-back. It
    stayed on the line with stk-1 SOLD; the retry the blocked-restock task
    sends a person to, and Goods back, both read it as restocked and
    answered success -- for good. It is taken back by its own token."""
    oid, rid = 60202, "700402"
    row = _one_unit_refund(swept, oid, int(rid))
    lossy = _CommitsThenReadDown(returns_router._orders_coll())
    monkeypatch.setattr(returns_router, "_orders_coll", lambda: lossy)
    assert _confirm(row)["result"]["restock_applied"] is False and lossy.read_down
    assert _units(swept) == [("stk-1", "SOLD")]
    line = _doc(swept, oid)["items"][0]
    assert not line.get("returned_qty") and not (line.get("restocked_refunds") or {}).get(rid)

    assert _retry(swept["returns"].find_one({"shopify_refund_id": rid}))["restock_applied"] is True
    assert _units(swept) == [("stk-1", "AVAILABLE")]
    _goods_back(swept["review"].find_one({"review_id": row["review_id"]}))
    assert _units(swept) == [("stk-1", "AVAILABLE")], "one unit, once"
    line = _doc(swept, oid)["items"][0]
    assert (line.get("returned_qty"), line.get("restocked_refunds")) == (1, {rid: 1})


def test_a_counter_claim_whose_reply_and_read_back_were_lost_is_taken_back(swept, monkeypatch):
    """The counter's claim committed, its reply was lost and so was the
    read-back: refused (409) with returned_qty burned at 1, so the customer's
    real return was refused for good and the frame stayed SOLD. Taken back by
    its token, the retry books it once."""
    from fastapi import HTTPException

    oid = 60203
    _claim_unit(swept, _book(swept, oid))
    _set(swept, oid, status="DELIVERED")
    lossy = _CommitsThenReadDown(returns_router._orders_coll())
    monkeypatch.setattr(returns_router, "_orders_coll", lambda: lossy)
    with pytest.raises(HTTPException) as refused:
        _counter_return(swept, oid)
    assert refused.value.status_code == 409 and lossy.read_down
    assert swept["returns"].count_documents({}) == 0
    assert not _doc(swept, oid)["items"][0].get("returned_qty")

    assert _counter_return(swept, oid)["return_id"]
    assert _doc(swept, oid)["items"][0]["returned_qty"] == 1
    assert _units(swept) == [("stk-1", "AVAILABLE")]
    _refused(swept, oid)


def test_the_counter_refuses_while_the_returns_scan_blips(swept, monkeypatch):
    """The same unreadable scan at the counter: a refund confirmed before the
    marks has only its doc, so reading nothing back took the unit back (and
    refunded it) a second time. Refused for a retry, nothing recorded."""
    from fastapi import HTTPException

    oid = 60204
    _claim_unit(swept, _book(swept, oid))
    _set(swept, oid, status="DELIVERED")
    blip = _ScanFailsOnce(returns_router._returns_coll())
    monkeypatch.setattr(returns_router, "_returns_coll", lambda: blip)
    with pytest.raises(HTTPException) as refused:
        _counter_return(swept, oid)
    assert refused.value.status_code == 503 and swept["returns"].count_documents({}) == 0
    assert _counter_return(swept, oid)["return_id"]


def test_a_parcels_create_after_its_update_at_one_clock_keeps_its_awb(swept):
    """A parcel's create (no tracking yet) and its update (AWB-X) carry one
    updated_at second; the update is processed first. The create wrote "" over
    AWB-X in the parcel list while the order's awb kept AWB-X: the courier
    legs asked about no AWB and the delivered webhook matched no order, so it
    stayed SHIPPED. A live parcel's empty tracking number never clears it."""
    oid = 60205
    _book(swept, oid)
    at = _T("01:00")
    shopify_fulfillment.reconcile_fulfillment(swept["db"], _fulfilment(
        oid, 1, tracking_number="AWB-X", updated_at=at, created_at=at), topic="fulfillments/update")
    shopify_fulfillment.reconcile_fulfillment(swept["db"], _fulfilment(
        oid, 1, tracking_number=None, updated_at=at, created_at=at), topic="fulfillments/create")
    doc = _doc(swept, oid)
    assert doc["status"] == "SHIPPED" and shopify_fulfillment.tracked_awbs(doc) == ["AWB-X"]

    agent = nexus_module.NexusAgent(db=swept["db"])
    asyncio.run(agent._handle_shiprocket_webhook({"awb": "AWB-X", "current_status": "DELIVERED"}))
    assert _doc(swept, oid)["status"] == "DELIVERED"


def test_a_fulfilled_body_older_than_the_parcels_cancel_ships_nothing(swept):
    """The only parcel went out at 01:03 (its webhook was missed) and was
    cancelled at 01:05. A delayed orders/fulfilled body stamped 01:03:30 (the
    parcel still out) states no SHIP fact: it would move the order to SHIPPED
    with no parcel out, and the counter cancel door then refuses it."""
    oid = 60206
    _book(swept, oid)
    before = _doc(swept, oid)["status"]
    shopify_fulfillment.reconcile_fulfillment(swept["db"], _fulfilment(
        oid, 1, status="cancelled", created_at=_T("01:03"), updated_at=_T("01:05")),
        topic="fulfillments/update")
    assert _doc(swept, oid)["status"] == before
    body = _pulled(oid, fulfillment_status="fulfilled", updated_at="2026-09-06T01:03:30Z",
                   fulfillments=[_fulfilment(oid, 1, created_at=_T("01:03"), updated_at=_T("01:03"))])
    swept["real_map"](body, swept["db"], webhook_id="ful-late", topic="orders/fulfilled")
    assert _doc(swept, oid)["status"] == before
