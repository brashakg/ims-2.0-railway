"""
IMS 2.0 - #1397 + #1413 (D122(2), D122(3)): a stock step that is TAKEN OVER.
NATIVE Mongo (PyMongo, the app's own index set), stock ROWS, order lines and
task rows asserted -- never call counts.
==========================================================================
Claude's reviewers on head 5498e1b2 (four must-fix items; all reproduced
here first, on that head, with these tests):

1. A Shopify-refund return whose worker DIED after it booked the order line
   and before its unit write. The finisher (the sweep, or the next Update
   stock) read the dead worker's booking as "this refund already put the
   unit back", placed nothing and stamped 'Restock applied'. The unit stayed
   SOLD for good, every door answered done. Same hole on a historical order
   (a booking with no stock-in task), and for a stalled holder that woke up
   after its taker (the record reopened with its task closed, nobody told).
   The worker here really runs and really stops: `Killed` is a BaseException
   the app's own `except Exception` never sees, and the un-booking that a
   dead process never runs is switched off for it.
2. A counter return on a HISTORICAL order: its stock-in task was raised a
   second time when the step ran again after the first task was completed.
3. A return's slots were numbered from the units still attributed to it; a
   unit sold again and returned by ANOTHER return freed its number, and the
   first return's next piece was "credited" on it and left SOLD.
4. The sweep finished only steps still claimed; a step that stopped with its
   claim given back (its closing stamp lost), or that was reopened, waited
   for a person -- and a Shopify refund's record has no label, no button and
   no task for that (D122(3): IMS finishes it).

Same harness as test_return_retry_race_native (#1413). No emoji (cp1252).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import HTTPException  # noqa: E402
from pymongo.collection import Collection  # noqa: E402
from pymongo.errors import AutoReconnect  # noqa: E402

from api.routers import returns as R  # noqa: E402
from api.services import shopify_refund as SR  # noqa: E402
from tests.test_return_retry_race_native import (  # noqa: E402,F401 -- `native` is the fixture
    OID,
    PID,
    RID,
    STORE,
    USER,
    _seed,
    _stale,
    native,
)

OLD = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
RF = "RF-1"
REAL_UPDATE = Collection.update_one


class Killed(BaseException):
    """The worker process died here: nothing of it runs any more."""


def _units(db):
    return {u["_id"]: (u["status"], u.get("order_id")) for u in db.stock_units.find({})}


def _line(db, oid=OID):
    return db.orders.find_one({"order_id": oid})["items"][0]


def _booked(db):
    line = _line(db)
    return float(line.get("returned_qty") or 0), float((line.get("restocked_refunds") or {}).get(RF) or 0)


def _refund_record(db, units=("U1",), qty=1, historical=False):
    """A Shopify-refund return whose restock was left open by its confirm,
    with its blocked-restock task open."""
    _seed(db, units=list(units), qty=qty)
    db.returns.update_one({"return_id": RID}, {"$set": {"shopify_refund_id": RF, "items.0.return_qty": qty}})
    if historical:
        db.orders.update_one({"order_id": OID}, {"$set": {"historical": True}})
    db.tasks.insert_one({"task_id": "TSK-BLOCKED", "task_number": "TSK-BLOCKED", "status": "OPEN",
                         "source": "SYSTEM", "source_ref": f"return_restock_blocked:{RID}", "title": "blocked",
                         "description": "blocked", "priority": "P1", "store_id": STORE})


def _dies(db, monkeypatch, target, attr, *, at=1, after=False):
    """'Update stock' is pressed and its worker dies at its `at`-th call of
    target.attr (`after`: right after that call's write landed). The claim
    stays on the record and ages; what a dead process never runs (the
    un-booking in _restock_booked's `finally`) does not run."""
    real, real_release, state = getattr(target, attr), SR._release_unlanded, {"calls": 0}

    def call(*a, **k):
        state["calls"] += 1
        if state["calls"] == at and not state.get("dead"):
            if after:
                real(*a, **k)
            state["dead"] = True
            raise Killed()
        return real(*a, **k)

    monkeypatch.setattr(target, attr, call)
    monkeypatch.setattr(SR, "_release_unlanded", lambda *a, **k: (
        None if state.get("dead") and not state.get("buried") else real_release(*a, **k)))
    with pytest.raises(Killed):
        R._retry_restock(RID, USER, STORE)
    state["buried"] = True
    doc = db.returns.find_one({"return_id": RID})
    assert doc["restock_in_progress"] is True and not doc.get("restock_applied")  # the fixture: S2, no stamp
    db.returns.update_one({"return_id": RID}, {"$set": {"restock_started_at": OLD}})
    return state


def _finish(finisher):
    if finisher == "sweep":
        assert R.finish_stopped_stock_steps() == 1
        return None
    return R._retry_restock(RID, USER, STORE)


def _done_once(db, units, booked):
    """The end every sequence must reach, and keep through every later door."""
    for _ in range(2):
        assert _units(db) == units
        assert _booked(db) == (booked, booked)  # booked on its order line once per piece
        doc = db.returns.find_one({"return_id": RID})
        assert doc["restock_applied"] is True and doc["restock_in_progress"] is False
        assert db.tasks.find_one({"task_id": "TSK-BLOCKED"})["status"] == "COMPLETED"
        assert R.finish_stopped_stock_steps() == 0
        assert R._retry_restock(RID, USER, STORE)["message"] in (
            "Already restocked",)
        # Goods back / the confirm ask the same cap: nothing of this refund is left to put back.
        order = db.orders.find_one({"order_id": OID})
        lines, _, unknown = SR._cap_restock_to_returnable(R._retry_lines(doc, order), order, RF)
        assert not unknown and not [ln for ln in lines if ln.restock and ln.return_qty > 0]


# --- must-fix 1 / 3: the dead worker's booking ------------------------------------------------------


@pytest.mark.parametrize("finisher", ["sweep", "press"])
def test_a_worker_that_died_after_its_booking_and_before_its_unit_write_is_finished(native, monkeypatch, finisher):
    """Before (head 5498e1b2): the finisher stamped 'Restock applied' on the
    dead worker's booking, closed the task and left U1 SOLD for good."""
    _refund_record(native)
    _dies(native, monkeypatch, R, "_flip_sold_unit")
    assert _units(native) == {"U1": ("SOLD", OID)} and _booked(native) == (1.0, 1.0)  # what it really leaves
    _finish(finisher)
    _done_once(native, {"U1": ("AVAILABLE", None)}, 1.0)
    assert native.stock_units.find_one({"_id": "U1"})["return_slot"] == f"{RID}|back|{PID}|0"
    assert native.stock_units.count_documents({}) == 1


@pytest.mark.parametrize("finisher", ["sweep", "press"])
def test_a_worker_that_died_after_its_unit_write_and_before_its_stamp_stays_one_row(native, monkeypatch, finisher):
    """The neighbour: booking AND unit are there. Nothing is placed again and
    the booking of the landed unit is never taken back."""
    _refund_record(native)
    _dies(native, monkeypatch, R, "_flip_sold_unit", after=True)
    assert _units(native) == {"U1": ("AVAILABLE", None)} and _booked(native) == (1.0, 1.0)
    _finish(finisher)
    _done_once(native, {"U1": ("AVAILABLE", None)}, 1.0)
    assert native.stock_units.count_documents({}) == 1


@pytest.mark.parametrize("finisher", ["sweep", "press"])
def test_a_worker_that_died_between_two_pieces_has_only_the_missing_one_placed(native, monkeypatch, finisher):
    _refund_record(native, units=("U1", "U2"), qty=2)
    _dies(native, monkeypatch, R, "_flip_sold_unit", at=2)
    assert _units(native) == {"U1": ("AVAILABLE", None), "U2": ("SOLD", OID)} and _booked(native) == (2.0, 2.0)
    _finish(finisher)
    _done_once(native, {"U1": ("AVAILABLE", None), "U2": ("AVAILABLE", None)}, 2.0)
    assert sorted(u["return_slot"] for u in native.stock_units.find({})) == [
        f"{RID}|back|{PID}|0", f"{RID}|back|{PID}|1"]


def _stock_in_tasks(db):
    return list(db.tasks.find({"source_ref": {"$regex": "^historical_stock_in:"}}))


@pytest.mark.parametrize("finisher", ["sweep", "press"])
@pytest.mark.parametrize("after", [False, True])
def test_a_worker_that_died_around_a_historical_orders_stock_in_task_leaves_one_task(native, monkeypatch,
                                                                                     finisher, after):
    """A historical order has no stock rows: its booking's unit is the
    stock-in task. Died between the booking and the task: before, the taker
    answered 'Booked ... a task asks the store manager' with no task in
    existence. Died after the task: one task, never a second."""
    _refund_record(native, units=(), historical=True)
    _dies(native, monkeypatch, SR, "_stock_in_task", after=after)
    assert _booked(native) == (1.0, 1.0) and len(_stock_in_tasks(native)) == (1 if after else 0)
    _finish(finisher)
    assert len(_stock_in_tasks(native)) == 1
    _done_once(native, {}, 1.0)
    assert len(_stock_in_tasks(native)) == 1 and native.stock_units.count_documents({}) == 0


def test_a_holder_that_stalled_holding_its_booking_wakes_after_the_sweep_finished(native, monkeypatch):
    """The stalled (not dead) variant. A books its line and stalls before its
    first unit write; the sweep takes the step over. Before: the sweep
    stamped 'applied' on A's booking and closed the task; A woke, lost its
    claim, un-booked and reopened the record -- open, its task closed, no
    label, U1 SOLD with nobody told."""
    _refund_record(native)
    real, seen = R._check_restock_fence, {}

    def stalled():
        if not seen:
            seen["a"] = True
            native.returns.update_one({"return_id": RID}, {"$set": {"restock_started_at": OLD}})
            seen["swept"] = R.finish_stopped_stock_steps()
        return real()

    monkeypatch.setattr(R, "_check_restock_fence", stalled)
    a = _stale()
    assert seen["swept"] == 1 and isinstance(a, HTTPException)  # A: 409, it moved nothing
    _done_once(native, {"U1": ("AVAILABLE", None)}, 1.0)
    assert native.stock_units.count_documents({}) == 1


@pytest.mark.parametrize("claim_check", [True, False])
def test_a_holder_stalled_after_its_liveness_check_on_a_refund_return_lands_nothing_twice(native, monkeypatch,
                                                                                         claim_check):
    """A passed its claim check and stalls right before its unit write; the
    sweep finishes the step (U1 back, booked once); A's write then lands on
    a unit no longer SOLD on this bill and on a used slot."""
    _refund_record(native)
    if not claim_check:
        monkeypatch.setattr(R, "_check_restock_fence", lambda: None)
    real, seen = R._flip_sold_unit, {}

    def stalled(*a, **k):
        if not seen:
            seen["a"] = True
            native.returns.update_one({"return_id": RID}, {"$set": {"restock_started_at": OLD}})
            seen["swept"] = R.finish_stopped_stock_steps()
        return real(*a, **k)

    monkeypatch.setattr(R, "_flip_sold_unit", stalled)
    _stale()
    assert seen["swept"] == 1
    _done_once(native, {"U1": ("AVAILABLE", None)}, 1.0)
    assert native.stock_units.count_documents({}) == 1


# --- must-fix 2: a historical order's counter return raises its stock-in task once ever ------------


def _closing_stamp_never_lands(monkeypatch, state):
    def update(self, flt, change, *a, **k):
        if self.name == "returns" and "restock_completed_at" in ((change or {}).get("$set") or {}) \
                and state.get("lost", 0) < 2:
            state["lost"] = state.get("lost", 0) + 1
            raise AutoReconnect("the stamp never reached the database")
        return REAL_UPDATE(self, flt, change, *a, **k)

    monkeypatch.setattr(Collection, "update_one", update)


@pytest.mark.parametrize("finisher", ["press", "sweep"])
def test_a_counter_return_on_a_historical_order_raises_its_stock_in_task_once_ever(native, monkeypatch, finisher):
    """Before: the step's closing stamp is lost, the manager does the task
    (adds the frame, completes it), the step runs again as IMS asks -- and a
    SECOND 'add 1 returned frame(s) through stock-in' task appears for the
    same frame."""
    _seed(native, units=[])
    native.orders.update_one({"order_id": OID}, {"$set": {"historical": True}})
    native.returns.update_one({"return_id": RID}, {"$set": {"stock_pending": True}})
    ref = f"historical_stock_in:{RID}"
    _closing_stamp_never_lands(monkeypatch, {})
    with pytest.raises(HTTPException) as exc:
        R._retry_restock(RID, USER, STORE)
    assert exc.value.status_code == 503
    assert [t["status"] for t in native.tasks.find({"source_ref": ref})] == ["OPEN"]
    doc = native.returns.find_one({"return_id": RID})
    assert not doc.get("restock_applied") and doc["stock_pending"] is True  # the step reads open
    # The store manager adds the frame through Opening Stock and completes the task.
    native.stock_units.insert_one({"_id": "HAND-1", "stock_id": "HAND-1", "product_id": PID, "store_id": STORE,
                                   "status": "AVAILABLE", "quantity": 1})
    native.tasks.update_one({"source_ref": ref}, {"$set": {"status": "COMPLETED"}})
    if finisher == "press":
        again = R._retry_restock(RID, USER, STORE)
        assert again["restock_applied"] is True and "already closed - add nothing more" in again["message"]
    else:
        native.returns.update_one({"return_id": RID}, {"$set": {"restock_started_at": OLD}})
        assert R.finish_stopped_stock_steps() == 1
    for _ in range(2):
        assert [t["status"] for t in native.tasks.find({"source_ref": ref})] == ["COMPLETED"]
        doc = native.returns.find_one({"return_id": RID})
        assert doc["restock_applied"] is True and doc["stock_pending"] is False
        assert native.stock_units.count_documents({}) == 1  # the one frame, added once
        assert R._retry_restock(RID, USER, STORE)["message"] == "Already restocked"
        assert R.finish_stopped_stock_steps() == 0


# --- must-fix 4: a unit sold again and returned again keeps the slot it carried ---------------------

RID2, OID2 = "RET-RACE-2", "ORD-NEW"


def _first_piece_back_then_resold_and_returned_by_another_return(db, monkeypatch):
    _seed(db, units=["U1", "U2"], qty=2)
    db.returns.update_one({"return_id": RID}, {"$set": {"items.0.return_qty": 2, "stock_pending": True}})
    real, state = R._flip_sold_unit, {}

    def flip(repo, sid, *a, **k):
        if sid == "U2" and not state.get("failed"):
            state["failed"] = True
            raise RuntimeError("socket reset")
        return real(repo, sid, *a, **k)

    monkeypatch.setattr(R, "_flip_sold_unit", flip)
    first = R._retry_restock(RID, USER, STORE)
    assert first["restock_applied"] is False and first["restock_stock_ids"] == ["U1"]
    assert _units(db) == {"U1": ("AVAILABLE", None), "U2": ("SOLD", OID)}
    # U1 is sold on a new bill and returned by ANOTHER return.
    db.orders.insert_one({"_id": OID2, "order_id": OID2, "store_id": STORE, "order_number": "R-2",
                          "items": [{"item_id": "n1", "product_id": PID, "sku": "F1", "product_name": "Frame",
                                     "quantity": 1, "unit_price": 1000.0}]})
    db.stock_units.update_one({"_id": "U1", "status": "AVAILABLE"}, {"$set": {
        "status": "SOLD", "order_id": OID2, "sold_at": datetime.now(timezone.utc).isoformat()}})
    db.returns.insert_one({
        "return_id": RID2, "store_id": STORE, "order_id": OID2, "status": "COMPLETED",
        "items": [{"order_item_id": "n1", "product_id": PID, "sku": "F1", "product_name": "Frame",
                   "return_qty": 1, "unit_price": 1000.0, "condition": "GOOD", "restock": True}],
        "restock_applied": False, "restock_in_progress": False, "restocked": []})
    assert R._retry_restock(RID2, USER, STORE)["restock_applied"] is True
    u1 = db.stock_units.find_one({"_id": "U1"})
    assert (u1["status"], u1["returned_by_return_id"], u1["return_slot"]) == ("AVAILABLE", RID2, f"{RID2}|back|{PID}|0")
    assert u1["return_slots"] == [f"{RID}|back|{PID}|0", f"{RID2}|back|{PID}|0"]


def _two_pieces_two_slots(db):
    assert _units(db) == {"U1": ("AVAILABLE", None), "U2": ("AVAILABLE", None)}
    assert db.stock_units.count_documents({}) == 2
    assert f"{RID}|back|{PID}|0" in db.stock_units.find_one({"_id": "U1"})["return_slots"]
    assert db.stock_units.find_one({"_id": "U2"})["return_slot"] == f"{RID}|back|{PID}|1"


@pytest.mark.parametrize("claim_check", [True, False])
@pytest.mark.parametrize("finisher", ["press", "sweep"])
def test_the_second_piece_of_a_return_is_placed_after_its_first_unit_was_returned_again(native, monkeypatch,
                                                                                       claim_check, finisher):
    """Before: the return's next run picked slot 0 again (U1 now answers to
    the other return), the unique index refused U2's flip, the old holder was
    found through its history and U2 was CREDITED as landed: record applied,
    task closed, U2 SOLD though it is in the shop."""
    _first_piece_back_then_resold_and_returned_by_another_return(native, monkeypatch)
    if not claim_check:
        monkeypatch.setattr(R, "_check_restock_fence", lambda: None)
    if finisher == "press":
        assert R._retry_restock(RID, USER, STORE)["restock_applied"] is True
    else:  # its claim was left stale by a worker that died
        native.returns.update_one({"return_id": RID}, {"$set": {
            "restock_in_progress": True, "restock_claim": "dead-worker", "restock_started_at": OLD}})
        assert R.finish_stopped_stock_steps() == 1
    _two_pieces_two_slots(native)
    doc = native.returns.find_one({"return_id": RID})
    assert doc["restock_applied"] is True and doc["stock_pending"] is False
    assert sorted(doc["restock_stock_ids"]) == ["U1", "U2"]
    assert R._retry_restock(RID, USER, STORE)["message"] == "Already restocked"
    _two_pieces_two_slots(native)


def test_the_same_through_a_door_that_reads_the_slots_live(native, monkeypatch):
    """moved_ids None (the Shopify doors): the slots a return used are read
    from every unit that ever carried one of them."""
    _first_piece_back_then_resold_and_returned_by_another_return(native, monkeypatch)
    order = native.orders.find_one({"order_id": OID})
    line = R._retry_lines(native.returns.find_one({"return_id": RID}), order)[0].model_copy(update={"return_qty": 1})
    out = R._restock_good_items([line], STORE, RID, order_id=OID, order=order)
    assert out["applied"] is True and out["restock_stock_ids"] == ["U2"]
    _two_pieces_two_slots(native)


# --- D122(3): the sweep finishes every step that stopped part way ----------------------------------


def test_the_sweep_starts_a_counter_step_no_worker_ever_started(native):
    """D130(1) (supersedes the builder's earlier D95(2)/(5) reading): the
    goods of a counter record were handed over at the counter, so a step its
    press never claimed is STARTED by the sweep once the record is 15 minutes
    old, and placed once; a person's press after it changes nothing."""
    _seed(native, units=["U1"])
    native.returns.update_one({"return_id": RID}, {"$set": {"stock_pending": True, "created_at": OLD}})
    assert R.finish_stopped_stock_steps() == 1
    assert _units(native) == {"U1": ("AVAILABLE", None)}
    assert R._retry_restock(RID, USER, STORE)["message"] == "Already restocked"
    assert _units(native) == {"U1": ("AVAILABLE", None)} and native.stock_units.count_documents({}) == 1


@pytest.mark.parametrize("refund", [False, True])
def test_the_sweep_finishes_a_step_whose_closing_stamp_never_landed(native, monkeypatch, refund):
    """The stamp is lost and the claim given back: the record reads open and
    unclaimed. Before: only a person's press finished it (a Shopify refund's
    record has no label and no button in History)."""
    if refund:
        _refund_record(native)
    else:
        _seed(native, units=["U1"])
        native.returns.update_one({"return_id": RID}, {"$set": {"stock_pending": True}})
    _closing_stamp_never_lands(monkeypatch, {})
    with pytest.raises(HTTPException) as exc:
        R._retry_restock(RID, USER, STORE)
    assert exc.value.status_code == 503
    doc = native.returns.find_one({"return_id": RID})
    assert not doc.get("restock_applied") and doc["restock_in_progress"] is False
    assert R.finish_stopped_stock_steps() == 0  # the worker may still be answering: not yet
    native.returns.update_one({"return_id": RID}, {"$set": {"restock_started_at": OLD}})
    assert R.finish_stopped_stock_steps() == 1
    assert _units(native) == {"U1": ("AVAILABLE", None)} and native.stock_units.count_documents({}) == 1
    doc = native.returns.find_one({"return_id": RID})
    assert doc["restock_applied"] is True and doc["restock_stock_ids"] == ["U1"]
    if refund:
        assert _booked(native) == (1.0, 1.0)
    assert R.finish_stopped_stock_steps() == 0


def test_the_sweep_leaves_a_step_an_older_version_left_claimed_to_a_persons_press(native):
    """DECISION: a claim with no token was taken by the code before this PR
    (it never released one). Units it moved carry no return slot, so the
    sweep does not re-run it on its own; 'Update stock' still takes it over."""
    _seed(native, units=["U1"])
    native.returns.update_one({"return_id": RID}, {"$set": {
        "restock_in_progress": True, "restock_started_at": OLD}})
    assert R.finish_stopped_stock_steps() == 0
    assert _units(native) == {"U1": ("SOLD", OID)}
    assert R._retry_restock(RID, USER, STORE)["restock_applied"] is True
    assert _units(native) == {"U1": ("AVAILABLE", None)}
