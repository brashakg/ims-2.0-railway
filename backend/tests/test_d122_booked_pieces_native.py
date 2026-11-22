"""
IMS 2.0 - #1397 + #1413 (D129, D130; D122(2), (3), (5)): ONE stock step for
every door of a Shopify refund. NATIVE Mongo (PyMongo, the app's own index
set); stock ROWS, order lines, review rows and task rows asserted -- never
call counts.
==========================================================================
Claude's reviewers on head 73de8981 (six must-fix items, four distinct; each
reproduced here first, on that head, with these tests):

A. HISTORICAL order (must-fix 2 and 6): a stock-step worker that stalled
   between its booking and its stock-in task, and woke after the sweep (or a
   press) finished the step, raised a SECOND open stock-in task for one
   frame (its ref was per claim and its insert unfenced).
B. The confirm / AUTO post and Goods back booked the order line before the
   unit write with no run id (must-fix 3 and 4): a process that died
   between the two writes left a booking every later door read as "already
   put back" -- the unit SOLD for good while IMS answered done; Goods back
   answered 409 'already put back' for ever.
C. Goods back numbered its slots without knowing the slot a stopped or
   stalled Update-stock worker had chosen for ANOTHER piece of the same
   product (must-fix 5): both pieces got one slot and one unit stayed SOLD.
D. The one-time refusal of a by-hand task close rewrote the task of a
   STANDING block (no physical shop resolved) to 'press Update stock', a
   button that cannot work for it, and erased the real cause (must-fix 1).

The fix (D129, shopify_refund.restock_refund, the one stock step every door
runs): every booking names its run, its refund and the slot of each piece,
in the write that counts it, and is refused while another booking of the
refund names one of its slots; whoever runs next TAKES OVER a booking whose
slot no row holds and places its pieces on those SAME slots (on a historical
order: raises its stock-in task under that booking's fixed id).

D130 as built: the sweep STARTS a counter record's step nobody started (15
minutes after the record) and never a Shopify-refund record's; a sweep run
that cannot place everything keeps one open task under a fixed id; with a
return-slot index missing the confirm books the money and no stock, its task
naming the index; the builder's limits 2 and 3 end with each piece once.

Same harness as test_return_retry_race_native (#1413). No emoji (cp1252).
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import HTTPException  # noqa: E402

from api.routers import returns as R  # noqa: E402
from api.services import shopify_refund as SR  # noqa: E402
from tests.test_d122_taken_over_step_native import (  # noqa: E402
    OLD,
    RF,
    Killed,
    _booked,
    _refund_record,
    _stock_in_tasks,
    _units,
)
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

SLOT0, SLOT1 = f"{RID}|back|{PID}|0", f"{RID}|back|{PID}|1"
LINE = {"order_item_id": "li1", "product_id": PID, "sku": "F1", "product_name": "Frame",
        "return_qty": 1, "unit_price": 1000.0, "condition": "GOOD", "restock": True}
BLOCKED = f"return_restock_blocked:{RID}"


def _review(db, lines=(LINE,), return_id=RID, status="POSTED"):
    row = {"review_id": "RV-1", "shopify_refund_id": RF, "order_id": OID, "store_id": STORE,
           "shopify_order_id": "S-1", "status": status, "resolved": status == "POSTED",
           "return_id": return_id, "proposed_restock": [dict(ln) for ln in lines], "goods_back_at": None}
    db.shopify_refund_review.insert_one(dict(row))
    return row


def _row(db):
    return db.shopify_refund_review.find_one({"review_id": "RV-1"})


def _killed(monkeypatch, target, attr, call, *, at=1):
    """`call()` runs and its process dies at its `at`-th call of target.attr:
    nothing of it runs any more -- not the un-booking in _restock_booked's
    `finally` either (a dead process never runs it)."""
    real, real_release, state = getattr(target, attr), SR._release_unlanded, {"calls": 0}

    def dies(*a, **k):
        state["calls"] += 1
        if state["calls"] == at and not state.get("dead"):
            state["dead"] = True
            raise Killed()
        return real(*a, **k)

    monkeypatch.setattr(target, attr, dies)
    monkeypatch.setattr(SR, "_release_unlanded", lambda *a, **k: (
        0.0 if state.get("dead") and not state.get("buried") else real_release(*a, **k)))
    with pytest.raises(Killed):
        call()
    state["buried"] = True
    monkeypatch.setattr(target, attr, real)


def _line_bookings(db):
    return {it["item_id"]: float((it.get("restocked_refunds") or {}).get(RF) or 0)
            for it in db.orders.find_one({"order_id": OID})["items"]}


def _all_back_once(db, units, booked):
    """One AVAILABLE row per piece, each on its own slot, each piece booked
    once -- and it stays so through every later door."""
    R._retry_restock(RID, USER, STORE)  # the record's own step, if it is still open
    for _ in range(2):
        assert _units(db) == {u: ("AVAILABLE", None) for u in units}
        assert db.stock_units.count_documents({}) == len(units)
        assert sorted(u["return_slot"] for u in db.stock_units.find({})) == [SLOT0, SLOT1][:len(units)]
        assert sum(_line_bookings(db).values()) == booked
        assert sum(float(it.get("returned_qty") or 0)
                   for it in db.orders.find_one({"order_id": OID})["items"]) == booked
        assert R.finish_stopped_stock_steps() == 0
        R.sweep_stuck_return_holds()  # the whole sweep runs and changes nothing
        assert _units(db) == {u: ("AVAILABLE", None) for u in units}
        assert R._retry_restock(RID, USER, STORE)["message"] == "Already restocked"


# --- A: one stock-in task per frame of a historical order -------------------------------------------


@pytest.mark.parametrize("finisher", ["sweep", "press"])
def test_a_worker_stalled_before_its_stock_in_task_never_raises_a_second_one(native, monkeypatch, finisher):
    """Before (head 73de8981): W1 booked the frame and stalled before its task
    insert; the finisher released W1's booking, booked again and raised the
    task under ITS ref; W1 woke and raised a second task under its own: two
    OPEN 'add 1 returned frame(s) through stock-in' tasks for one frame."""
    _refund_record(native, units=(), historical=True)
    real, seen = SR.raise_stock_in_task, {}

    def stalled(*a, **k):
        if not seen:
            seen["w1"] = True
            assert _booked(native) == (1.0, 1.0) and not _stock_in_tasks(native)  # booked, no task yet
            native.returns.update_one({"return_id": RID}, {"$set": {"restock_started_at": OLD}})
            seen["finished"] = (R.finish_stopped_stock_steps() if finisher == "sweep"
                                else R._retry_restock(RID, USER, STORE)["restock_applied"])
        return real(*a, **k)

    monkeypatch.setattr(SR, "raise_stock_in_task", stalled)
    w1 = _stale()  # W1 wakes: its stamp misses on its token (409)
    assert seen["finished"] in (1, True) and isinstance(w1, HTTPException)
    for _ in range(2):
        tasks = _stock_in_tasks(native)
        assert [t["status"] for t in tasks] == ["OPEN"], tasks
        assert _booked(native) == (1.0, 1.0) and native.stock_units.count_documents({}) == 0
        doc = native.returns.find_one({"return_id": RID})
        assert doc["restock_applied"] is True and doc["restock_in_progress"] is False
        assert R.finish_stopped_stock_steps() == 0
        assert R._retry_restock(RID, USER, STORE)["message"] == "Already restocked"


def test_goods_back_on_a_historical_order_that_died_before_its_task_gets_the_task_on_the_next_press(
        native, monkeypatch):
    """B on a historical order: the booking is there, the task is not. Before:
    the next press answered 409 'already put back' with no task in existence."""
    _refund_record(native, units=(), historical=True)
    row = _review(native)
    _killed(monkeypatch, SR, "raise_stock_in_task", lambda: SR.goods_back(native, row, user_id="u1"))
    assert _booked(native) == (1.0, 1.0) and not _stock_in_tasks(native)
    assert SR.goods_back(native, _row(native), user_id="u2")["status"] != "duplicate"
    native.shopify_refund_review.update_one({"review_id": "RV-1"}, {"$set": {"goods_back_at": OLD}})
    out = SR.goods_back(native, _row(native), user_id="u2")
    assert out["status"] == "stock_in", out
    for _ in range(2):
        assert [t["status"] for t in _stock_in_tasks(native)] == ["OPEN"] and _booked(native) == (1.0, 1.0)
        assert SR.goods_back(native, _row(native), user_id="u2")["status"] == "duplicate"
        assert R._retry_restock(RID, USER, STORE)["restock_applied"] is True
        assert len(_stock_in_tasks(native)) == 1


# --- B: a booking whose door died before the unit write has a finisher ------------------------------


@pytest.mark.parametrize("finisher", ["press", "sweep", "update_stock"])
def test_goods_back_that_died_after_its_booking_is_finished(native, monkeypatch, finisher):
    """Before: goods_back_at stayed set, the next press answered 409 'The
    goods of this refund were already put back', U1 SOLD for good; Update
    stock read the booking as landed and stamped 'Restock applied'."""
    _refund_record(native)
    row = _review(native)
    _killed(monkeypatch, R, "_flip_sold_unit", lambda: SR.goods_back(native, row, user_id="u1"))
    assert _units(native) == {"U1": ("SOLD", OID)} and _booked(native) == (1.0, 1.0)  # what it really leaves
    assert _row(native)["goods_back_at"] and not _row(native).get("goods_restock")
    # Pressed again at once: still running as far as IMS can tell -- never 'already put back'.
    assert SR.goods_back(native, _row(native), user_id="u2")["status"] != "duplicate"
    assert _units(native) == {"U1": ("SOLD", OID)}
    if finisher == "update_stock":
        assert R._retry_restock(RID, USER, STORE)["restock_applied"] is True
    else:
        native.shopify_refund_review.update_one({"review_id": "RV-1"}, {"$set": {"goods_back_at": OLD}})
        if finisher == "press":
            assert SR.goods_back(native, _row(native), user_id="u2")["status"] == "restocked"
        else:
            # The hourly sweep runs the press that stopped.
            assert R.sweep_stuck_return_holds() >= 1
            assert _row(native)["goods_restock"]["restock_applied"] is True
    assert _units(native) == {"U1": ("AVAILABLE", None)} and _booked(native) == (1.0, 1.0)
    assert native.stock_units.find_one({"_id": "U1"})["return_slot"] == SLOT0
    _all_back_once(native, ["U1"], 1.0)


def _post_fixture(db):
    """An online order billed at a shop, paid on Shopify, its Shopify refund
    queued for the accountant (restock proposed), nothing posted yet."""
    db.stores.insert_one({"store_id": STORE, "store_type": "RETAIL"})
    db.customers.insert_one({"customer_id": "C-1", "name": "Test Customer", "phone": "9000000001",
                             "store_credit_balance": 0.0})
    db.orders.insert_one({
        "_id": OID, "order_id": OID, "store_id": STORE, "order_number": "R-1", "shopify_order_id": "S-1",
        "grand_total": 1050.0, "payments": [{"method": "CARD", "amount": 1050.0}], "customer_id": "C-1",
        "items": [{"item_id": "li1", "product_id": PID, "sku": "F1", "product_name": "Frame",
                   "quantity": 1, "unit_price": 1000.0, "gst_rate": 5.0}]})
    db.stock_units.insert_one({"_id": "U1", "stock_id": "U1", "product_id": PID, "store_id": STORE,
                               "status": "SOLD", "order_id": OID, "quantity": 1})
    row = {"review_id": "RV-1", "shopify_refund_id": RF, "order_id": OID, "store_id": STORE,
           "order_number": "R-1", "shopify_order_id": "S-1", "status": "PENDING", "resolved": False,
           "customer_id": "C-1",
           "proposed_restock": [dict(LINE)],
           "credit_note": {"gross_refund": 1050.0, "money_back": 1050.0, "settled_externally": True,
                           "gst_breakup": {"taxable": 1000.0, "tax": 50.0, "cgst": 25.0, "sgst": 25.0,
                                           "igst": 0.0},
                           "lines": [dict(LINE)]}}
    db.shopify_refund_review.insert_one(dict(row))
    return row


def test_a_confirm_that_died_after_its_booking_is_finished_by_its_re_drive(native, monkeypatch):
    """Before: the re-drive's cap read the dead post's booking as this
    refund's own unit back, restocked nothing and finalized the record
    'restock applied' with no unit: U1 SOLD on the order for good."""
    row = _post_fixture(native)
    _killed(monkeypatch, R, "_flip_sold_unit", lambda: SR.post_from_review(native, row))
    assert _units(native) == {"U1": ("SOLD", OID)} and _booked(native) == (1.0, 1.0)
    doc = native.returns.find_one({"shopify_refund_id": RF})
    assert doc["status"] == "PENDING"  # its claim, never finalized
    # The claim goes stale and the refund is driven again (the tick, or Confirm).
    native.returns.update_one({"shopify_refund_id": RF}, {"$set": {"created_at": OLD}})
    native.orders.update_one({"order_id": OID}, {"$unset": {"credit_lock": ""}})
    again = SR.post_from_review(native, row)
    assert again["restock_applied"] is True and again["restock_stock_ids"] == ["U1"], again
    for _ in range(2):
        assert _units(native) == {"U1": ("AVAILABLE", None)} and _booked(native) == (1.0, 1.0)
        assert native.stock_units.find_one({"_id": "U1"})["return_slot"] == f"{doc['return_id']}|back|{PID}|0"
        doc = native.returns.find_one({"shopify_refund_id": RF})
        assert doc["restock_applied"] is True and doc["restock_stock_ids"] == ["U1"]
        assert native.returns.count_documents({}) == 1 and native.stock_units.count_documents({}) == 1
        assert native.credit_note_ledger.count_documents({}) == 1  # the money booked once
        assert R._retry_restock(doc["return_id"], USER, STORE)["message"] == "Already restocked"
        assert R.finish_stopped_stock_steps() == 0


# --- C: two pieces of one product never share a slot -----------------------------------------------


def _two_lines_one_product(db):
    """One refund over two SOLD units of one product on two order lines: li1
    restocks at the confirm, li2's goods come back later (Goods back). The
    confirm's unit write failed: the record's restock is open."""
    _seed(db, units=["U1", "U2"])
    db.orders.update_one({"order_id": OID}, {"$push": {"items": {
        "item_id": "li2", "product_id": PID, "sku": "F1", "product_name": "Frame",
        "quantity": 1, "unit_price": 1000.0}}})
    held = {**LINE, "order_item_id": "li2", "restock": False}
    db.returns.update_one({"return_id": RID}, {"$set": {"shopify_refund_id": RF, "items": [dict(LINE), held]}})
    return _review(db, lines=(LINE, held))


@pytest.mark.parametrize("worker", ["dead", "stalled_after_booking", "stalled_before_booking"])
def test_goods_back_beside_a_stopped_update_stock_puts_both_pieces_back(native, monkeypatch, worker):
    """Before: W1 (Update stock) chose slot 0 for li1's piece and stopped
    before its unit write; Goods back numbered li2's piece 0 too and flipped
    a unit on it. Dead W1: the taker found the slot held, kept W1's booking,
    placed nothing and stamped applied. Stalled W1: its flip was refused on
    the slot and credited Goods back's unit as its own. Either way: two
    bookings, ONE unit back, the other SOLD for good. `stalled_before_booking`:
    W1 chose slot 0 and stalled before it booked; Goods back booked slot 0
    for its own piece -- W1's booking is then refused on the slot and planned
    again."""
    row = _two_lines_one_product(native)
    if worker == "dead":
        _killed(monkeypatch, R, "_flip_sold_unit", lambda: R._retry_restock(RID, USER, STORE))
        assert native.returns.find_one({"return_id": RID})["restock_in_progress"] is True
        assert SR.goods_back(native, row, user_id="u1")["status"] == "restocked"
        native.returns.update_one({"return_id": RID}, {"$set": {"restock_started_at": OLD}})
        assert R.finish_stopped_stock_steps() == 1
    else:
        target = "_flip_sold_unit" if worker == "stalled_after_booking" else "_note_intent"
        real, seen = getattr(R, target), {}

        def stalled(*a, **k):
            if not seen:
                seen["goods"] = SR.goods_back(native, row, user_id="u1")["status"]
            return real(*a, **k)

        monkeypatch.setattr(R, target, stalled)
        w1 = R._retry_restock(RID, USER, STORE)
        assert seen["goods"] == "restocked" and w1["restock_applied"] is True
    doc = native.returns.find_one({"return_id": RID})
    assert doc["restock_applied"] is True and doc["restock_in_progress"] is False
    assert _line_bookings(native) == {"li1": 1.0, "li2": 1.0}
    _all_back_once(native, ["U1", "U2"], 2.0)


def test_a_booking_is_refused_on_a_slot_another_run_booked_since_it_chose_it(native, monkeypatch):
    """The window between choosing a slot and booking it. W1 (Update stock,
    li1's piece) chose slot 0 and stalls before its booking; Goods back (li2's
    piece only) chooses slot 0 too, books it and places its unit there. W1's
    booking naming slot 0 is REFUSED in its own write (the slot is booked),
    W1 plans again and places its piece on slot 1. Without that refusal W1
    booked slot 0, its unit write was refused on the used slot and it was
    credited Goods back's unit: li1's unit SOLD for good, both lines booked."""
    _seed(native, units=["U1", "U2"])
    native.orders.update_one({"order_id": OID}, {"$push": {"items": {
        "item_id": "li2", "product_id": PID, "sku": "F1", "product_name": "Frame",
        "quantity": 1, "unit_price": 1000.0}}})
    later = {**LINE, "order_item_id": "li2", "restock": False}
    native.returns.update_one({"return_id": RID}, {"$set": {"shopify_refund_id": RF,
                                                            "items": [dict(LINE), later]}})
    row = _review(native, lines=(later,))
    real, seen = R._note_intent, {}

    def stalled(*a, **k):
        if not seen:
            seen["goods"] = SR.goods_back(native, row, user_id="u1")["status"]
            assert native.stock_units.count_documents({"return_slot": SLOT0}) == 1
        return real(*a, **k)

    monkeypatch.setattr(R, "_note_intent", stalled)
    w1 = R._retry_restock(RID, USER, STORE)
    assert seen["goods"] == "restocked" and w1["restock_applied"] is True
    assert _line_bookings(native) == {"li1": 1.0, "li2": 1.0}
    _all_back_once(native, ["U1", "U2"], 2.0)


def test_a_flip_that_lost_its_race_is_credited_by_its_slot_and_keeps_its_booking(native, monkeypatch):
    """A routed online order (its units are exactly the ones its claim sold
    to it: IMS mints nothing). Another attempt's write of the same piece
    lands between this attempt's read and its flip. Before: the flip found no
    SOLD unit, the piece counted 'never claimed' and its booking was taken
    back -- the unit back, the order line reading it as still out."""
    _refund_record(native)
    native.orders.update_one({"order_id": OID}, {"$set": {"fulfillment_route": {"store_id": STORE}}})
    real, seen = R._flip_sold_unit, {}

    def loses(repo, sid, fields, **k):
        if not seen:
            seen["other"] = real(repo, sid, dict(fields), **k)  # the other attempt's flip, same slot
        return real(repo, sid, fields, **k)

    monkeypatch.setattr(R, "_flip_sold_unit", loses)
    out = R._retry_restock(RID, USER, STORE)
    assert seen["other"] is True and out["restock_applied"] is True
    assert _units(native) == {"U1": ("AVAILABLE", None)} and _booked(native) == (1.0, 1.0)
    assert native.returns.find_one({"return_id": RID})["restock_stock_ids"] == ["U1"]


# --- D130(5): the builder's limits 2 and 3 end with each piece once ---------------------------------


def _one_line_twice(db):
    """One refund lists ONE order line of two pieces twice: one piece to put
    back at once (li1 restock), the other when its goods come back (Goods
    back). Two SOLD units, the record's restock open."""
    _seed(db, units=["U1", "U2"], qty=2)
    later = {**LINE, "restock": False}
    db.returns.update_one({"return_id": RID}, {"$set": {"shopify_refund_id": RF, "items": [dict(LINE), later]}})
    return _review(db, lines=(LINE, later))


@pytest.mark.parametrize("at", ["_note_intent", "_flip_sold_unit"])
def test_limit_2_two_doors_on_two_pieces_of_one_line_at_once_put_each_back_once(native, monkeypatch, at):
    """Limit 2: Update stock (one piece of the line) and Goods back (both)
    run at the same instant -- Goods back lands inside Update stock's step,
    before its booking (`_note_intent`) or between its booking and its unit
    write (`_flip_sold_unit`)."""
    row = _one_line_twice(native)
    real, seen = getattr(R, at), {}

    def inside(*a, **k):
        if not seen:
            seen["goods"] = SR.goods_back(native, row, user_id="u1")["status"]
        return real(*a, **k)

    monkeypatch.setattr(R, at, inside)
    w1 = R._retry_restock(RID, USER, STORE)
    assert seen["goods"] == "restocked" and w1["restock_applied"] is True
    assert _line_bookings(native) == {"li1": 2.0}
    _all_back_once(native, ["U1", "U2"], 2.0)


def test_limit_3_a_stopped_worker_lands_between_its_takers_take_over_and_write_then_the_taker_dies(
        native, monkeypatch):
    """Limit 3, on a routed online order (claimed_only: a unit IMS cannot
    find SOLD is 'never claimed'). W1 books and stalls before its unit
    write; the sweep T takes the step over (and W1's booking); W1's write
    lands; T's write finds no SOLD unit; T dies before its stamp; the next
    sweep finishes. Each piece once, booked once."""
    _refund_record(native)
    native.orders.update_one({"order_id": OID}, {"$set": {"fulfillment_route": {"store_id": STORE}}})
    real_flip, real_close, real_release = R._flip_sold_unit, R._close_claim, SR._release_unlanded
    state = {"calls": 0}

    def flip(*a, **k):
        state["calls"] += 1
        if state["calls"] == 1:  # W1 stalls before its write
            state["w1"] = (a, k)
            native.returns.update_one({"return_id": RID}, {"$set": {"restock_started_at": OLD}})
            with pytest.raises(Killed):
                R.finish_stopped_stock_steps()  # T, dies at its stamp
            return state["w1_result"]
        if state["calls"] == 2:  # T's write: W1's lands first
            state["w1_result"] = real_flip(*state["w1"][0], **state["w1"][1])
        return real_flip(*a, **k)

    def close(coll, mine, sets):
        if state["calls"] >= 2 and not state.get("t_dead"):
            state["t_dead"] = True
            raise Killed()
        return real_close(coll, mine, sets)

    monkeypatch.setattr(R, "_flip_sold_unit", flip)
    monkeypatch.setattr(R, "_close_claim", close)
    monkeypatch.setattr(SR, "_release_unlanded", lambda *a, **k: 0.0)  # T is dead: it releases nothing
    w1 = _stale()  # W1: its stamp misses on its token (T took the claim)
    assert isinstance(w1, HTTPException) and state.get("t_dead")
    for target, attr, real in ((R, "_flip_sold_unit", real_flip), (R, "_close_claim", real_close),
                               (SR, "_release_unlanded", real_release)):
        monkeypatch.setattr(target, attr, real)
    assert _units(native) == {"U1": ("AVAILABLE", None)} and _booked(native) == (1.0, 1.0)
    native.returns.update_one({"return_id": RID}, {"$set": {"restock_started_at": OLD}})
    assert R.finish_stopped_stock_steps() == 1
    for _ in range(2):
        assert _units(native) == {"U1": ("AVAILABLE", None)} and _booked(native) == (1.0, 1.0)
        assert native.stock_units.count_documents({}) == 1
        doc = native.returns.find_one({"return_id": RID})
        assert doc["restock_applied"] is True and doc["restock_stock_ids"] == ["U1"]
        assert R.finish_stopped_stock_steps() == 0


# --- D: the refused close keeps the task of a standing block ----------------------------------------


def test_a_refused_close_keeps_the_cause_and_action_of_a_standing_block(native, monkeypatch):
    """Before: the 409 rewrote the task to 'its stock step did not finish ...
    press Update stock', which answers 'Restock partial - retry again' for
    ever on an online order with no physical shop resolved."""
    monkeypatch.delenv("ONLINE_FULFILLMENT_STORE_ID", raising=False)
    native.stores.insert_one({"store_id": "BV-ONLINE-01", "store_type": "ONLINE", "is_online_store": True})
    native.orders.insert_one({
        "_id": OID, "order_id": OID, "store_id": "BV-ONLINE-01", "order_number": "R-1", "channel": "ONLINE",
        "items": [{"item_id": "li1", "product_id": PID, "sku": "F1", "product_name": "Frame",
                   "quantity": 1, "unit_price": 1000.0}]})
    native.stock_units.insert_one({"_id": "U1", "stock_id": "U1", "product_id": PID, "store_id": STORE,
                                   "status": "SOLD", "order_id": OID, "quantity": 1})
    native.returns.insert_one({
        "return_id": RID, "store_id": "BV-ONLINE-01", "order_id": OID, "status": "COMPLETED",
        "items": [dict(LINE)], "restock_applied": False, "restock_in_progress": False, "restocked": [],
        "stock_pending": True})
    admin = {**USER, "roles": ["SUPERADMIN"], "store_ids": []}
    first = R._retry_restock(RID, admin, None)
    assert first["restock_applied"] is False
    doc = native.returns.find_one({"return_id": RID})
    assert doc["restock_store_reason"] == R._RESTOCK_ROUTE_UNRESOLVED
    task = native.tasks.find_one({"source_ref": BLOCKED})
    assert "no physical shop could be resolved" in task["description"]
    assert "ONLINE_FULFILLMENT_STORE_ID" in task["description"]
    with pytest.raises(HTTPException) as refused:
        asyncio.run(R.note_stock_task_closed(RID, USER, task))
    assert refused.value.status_code == 409
    assert "Update stock" not in refused.value.detail and "task says" in refused.value.detail
    after = native.tasks.find_one({"source_ref": BLOCKED})
    assert after["description"] == task["description"] and after["title"] == task["title"]
    assert "Update stock" not in after["description"]
    assert after["payload"]["close_refused"] is True and after["status"] == "OPEN"
    assert native.tasks.count_documents({"source_ref": BLOCKED}) == 1
    # An ordinary open step (nothing standing in its way) still gets the button's text.
    native.returns.update_one({"return_id": RID}, {"$set": {"restock_store_reason": None}})
    native.tasks.update_one({"task_id": after["task_id"]}, {"$set": {"payload.close_refused": False}})
    with pytest.raises(HTTPException) as refused:
        asyncio.run(R.note_stock_task_closed(RID, USER, native.tasks.find_one({"task_id": after["task_id"]})))
    assert refused.value.status_code == 409 and "Update stock" in refused.value.detail
    assert "press 'Update stock'" in native.tasks.find_one({"task_id": after["task_id"]})["description"]


# --- D130(1): what the sweep starts ------------------------------------------------------------------


def test_the_sweep_starts_a_counter_records_step_nobody_started_15_minutes_after_the_record(native):
    """D130(1): a counter record whose press died between its record and its
    claim (or a record Needs completing wrote): the goods were handed over
    at the counter, so the sweep STARTS its step -- not while the record is
    fresh (its press may still be running)."""
    _seed(native, units=["U1"])
    native.returns.update_one({"return_id": RID}, {"$set": {
        "stock_pending": True, "created_at": datetime.now(timezone.utc).isoformat()}})
    assert R.finish_stopped_stock_steps() == 0 and _units(native) == {"U1": ("SOLD", OID)}
    native.returns.update_one({"return_id": RID}, {"$set": {"created_at": OLD}})
    assert R.finish_stopped_stock_steps() == 1
    for _ in range(2):
        assert _units(native) == {"U1": ("AVAILABLE", None)} and native.stock_units.count_documents({}) == 1
        doc = native.returns.find_one({"return_id": RID})
        assert doc["restock_applied"] is True and doc["stock_pending"] is False
        assert doc["restock_stock_ids"] == ["U1"]
        assert R.finish_stopped_stock_steps() == 0


def test_the_sweep_never_starts_a_shopify_refund_records_step_nobody_started(native):
    """D130(1): a Shopify refund's goods come back by post -- only Goods back
    (or a confirm that says the goods are back) starts its step. Its task
    stays open and names the re-run."""
    _refund_record(native)
    native.returns.update_one({"return_id": RID}, {"$set": {"created_at": OLD, "stock_pending": True}})
    assert not native.returns.find_one({"return_id": RID}).get("restock_claim")
    assert R.finish_stopped_stock_steps() == 0
    assert _units(native) == {"U1": ("SOLD", OID)} and _booked(native) == (0.0, 0.0)
    assert native.tasks.find_one({"task_id": "TSK-BLOCKED"})["status"] == "OPEN"


# --- D130(2): a sweep run that cannot place everything keeps one open task --------------------------


@pytest.mark.parametrize("refund", [True, False])
def test_a_sweep_run_that_places_nothing_keeps_one_open_task_under_a_fixed_id(native, monkeypatch, refund):
    """Before: the sweep's 'not everything placed' was a log line; with the
    record's task closed by hand nobody was told and it was never tried
    again -- the unit SOLD. Now its run keeps (or opens again) ONE task."""
    if refund:
        _refund_record(native)
        native.tasks.update_one({"task_id": "TSK-BLOCKED"}, {"$set": {"status": "COMPLETED"}})
    else:
        _seed(native, units=["U1"])
        native.returns.update_one({"return_id": RID}, {"$set": {"stock_pending": True}})
    # A worker started the step and stopped (its claim aged).
    native.returns.update_one({"return_id": RID}, {"$set": {
        "restock_claim": "W0", "restock_in_progress": True, "restock_started_at": OLD}})

    def fails(*a, **k):
        raise RuntimeError("socket reset")

    real = R._flip_sold_unit
    monkeypatch.setattr(R, "_flip_sold_unit", fails)
    for hour in range(2):
        if hour:  # a manager closed the task by hand after its one refused close
            native.tasks.update_many({"source_ref": BLOCKED}, {"$set": {"status": "COMPLETED"}})
            native.returns.update_one({"return_id": RID}, {"$set": {
                "restock_in_progress": True, "restock_started_at": OLD}})
        assert R.finish_stopped_stock_steps() == 1
        # A Shopify refund's booking stays once its unit write was tried: the
        # next run reads its slot (free) and places the piece on it.
        assert _units(native) == {"U1": ("SOLD", OID)}
        assert _booked(native) == ((1.0, 1.0) if refund else (0.0, 0.0))
        doc = native.returns.find_one({"return_id": RID})
        assert doc["restock_applied"] is False and doc["restock_in_progress"] is False
        open_tasks = list(native.tasks.find({"source_ref": BLOCKED, "status": "OPEN"}))
        assert len(open_tasks) == 1, open_tasks
        assert open_tasks[0]["task_id"] == R.stock_task_id(RID)
        text = open_tasks[0]["description"]
        assert ("Update stock" in text) is (not refund)
        assert "by hand" in text and "add" in text.lower()
        assert R.finish_stopped_stock_steps() == 0  # stamped: not run again on its own
    assert native.tasks.count_documents({"task_id": R.stock_task_id(RID)}) == 1
    monkeypatch.setattr(R, "_flip_sold_unit", real)
    assert R._retry_restock(RID, USER, STORE)["restock_applied"] is True
    assert _units(native) == {"U1": ("AVAILABLE", None)}
    assert native.tasks.find_one({"task_id": R.stock_task_id(RID)})["status"] == "COMPLETED"


# --- D130(3): the confirm books the money and no stock without the return-slot indexes --------------


def test_the_confirm_books_the_money_and_no_stock_without_the_return_slot_indexes(native, monkeypatch):
    """D122(6) as amended by D130(3): Shopify has already paid the money
    back, so the confirm records it; it writes NO stock (no booking, no
    unit) and leaves the step open with a task naming the missing index.
    Once the index is back, the re-run places the unit once."""
    _post_fixture(native)
    native.stock_units.drop_index("uniq_stock_unit_return_slot")
    out = SR.post_from_review(native, _row(native))
    assert out["status"] == "credited" and out["restock_applied"] is False, out
    assert native.credit_note_ledger.count_documents({}) == 1
    assert _units(native) == {"U1": ("SOLD", OID)} and _booked(native) == (0.0, 0.0)
    doc = native.returns.find_one({"shopify_refund_id": RF})
    assert doc["status"] == "COMPLETED" and doc["restock_applied"] is False
    task = native.tasks.find_one({"source_ref": f"return_restock_blocked:{doc['return_id']}", "status": "OPEN"})
    assert task and "not installed - tell head office" in task["description"]
    with pytest.raises(HTTPException) as refused:
        R._retry_restock(doc["return_id"], USER, STORE)
    assert refused.value.status_code == 503 and _units(native) == {"U1": ("SOLD", OID)}
    native.stock_units.create_index("return_slot", name="uniq_stock_unit_return_slot", unique=True,
                                    partialFilterExpression={"return_slot": {"$type": "string"}})
    assert R._retry_restock(doc["return_id"], USER, STORE)["restock_applied"] is True
    assert _units(native) == {"U1": ("AVAILABLE", None)} and _booked(native) == (1.0, 1.0)
    assert native.credit_note_ledger.count_documents({}) == 1


# --- E: no booking is taken back once a unit write was tried (review of 22f53ec0) --------------------


def _transient_once(monkeypatch, *, at=1):
    """`_flip_sold_unit` raises a transient error at its `at`-th call (a Mongo
    blip), once; every other call is real."""
    real, state = R._flip_sold_unit, {"calls": 0}

    def flip(*a, **k):
        state["calls"] += 1
        if state["calls"] == at:
            raise RuntimeError("socket reset")
        return real(*a, **k)

    monkeypatch.setattr(R, "_flip_sold_unit", flip)
    return real


def test_two_lines_of_one_product_whose_first_write_failed_put_each_piece_back_once(native, monkeypatch):
    """Must-fix 1. One refund restocks two order lines of ONE product (li1 on
    slot 0, li2 on slot 1); li1's unit write fails, li2's lands. Before: the
    un-booking counted landed pieces per PRODUCT, kept li1's booking (slot 0,
    held by nothing) and took back li2's (slot 1, held by li2's unit). The
    next Update stock took li1's booking over AND booked li2 again on slot 2:
    with no SOLD unit left for it, it MINTED a phantom -- three AVAILABLE rows
    for two frames."""
    _seed(native, units=["U1", "U2"])
    # Each line names its own unit (the till's scan, D43): li1's piece is U1 only.
    native.orders.update_one({"order_id": OID}, {"$set": {"items.0.stock_id": "U1"}})
    native.orders.update_one({"order_id": OID}, {"$push": {"items": {
        "item_id": "li2", "product_id": PID, "sku": "F1", "product_name": "Frame",
        "quantity": 1, "unit_price": 1000.0, "stock_id": "U2"}}})
    native.returns.update_one({"return_id": RID}, {"$set": {
        "shopify_refund_id": RF, "items": [dict(LINE), {**LINE, "order_item_id": "li2"}]}})
    real = _transient_once(monkeypatch)
    first = R._retry_restock(RID, USER, STORE)
    assert first["restock_applied"] is False
    assert native.stock_units.count_documents({"status": "AVAILABLE"}) == 1
    assert _line_bookings(native) == {"li1": 1.0, "li2": 1.0}  # both stay booked
    monkeypatch.setattr(R, "_flip_sold_unit", real)
    assert R._retry_restock(RID, USER, STORE)["restock_applied"] is True
    assert native.stock_units.count_documents({"source_type": "RETURN"}) == 0  # nothing minted
    assert _line_bookings(native) == {"li1": 1.0, "li2": 1.0}
    _all_back_once(native, ["U1", "U2"], 2.0)


def test_a_taker_whose_own_write_failed_never_unbooks_a_piece_its_live_worker_then_lands(native, monkeypatch):
    """Must-fix 2. One line of two pieces, the refund puts ONE back (the
    customer keeps the other). W1 (Update stock) books, passes its fence and
    stalls before its unit write; the sweep T takes the step over (and W1's
    booking), its own write fails. Before: T took the booking back; W1 woke
    and landed U1; the next Update stock booked the piece again and flipped
    U2 -- the customer's frame on sale in IMS, two units back for one."""
    _seed(native, units=["U1", "U2"], qty=2)
    native.returns.update_one({"return_id": RID}, {"$set": {"shopify_refund_id": RF}})
    real, state = R._flip_sold_unit, {"calls": 0, "taker": False}

    def flip(*a, **k):
        state["calls"] += 1
        if state["taker"]:  # every unit write of T fails (a Mongo blip): U1 and U2
            raise RuntimeError("socket reset")
        if state["calls"] == 1:  # W1, between its fence and its write
            native.returns.update_one({"return_id": RID}, {"$set": {"restock_started_at": OLD}})
            state["taker"] = True
            assert R.finish_stopped_stock_steps() == 1  # T: its own writes fail
            state["taker"] = False
            assert state["calls"] >= 2  # T did try
            assert native.returns.find_one({"return_id": RID})["restock_applied"] is False
        return real(*a, **k)

    monkeypatch.setattr(R, "_flip_sold_unit", flip)
    w1 = _stale()  # W1 lands its unit; its stamp misses on its token (409)
    assert isinstance(w1, HTTPException)
    monkeypatch.setattr(R, "_flip_sold_unit", real)
    assert sum(1 for s, _o in _units(native).values() if s == "AVAILABLE") == 1
    assert _booked(native) == (1.0, 1.0)  # the piece that is back stays booked
    R._retry_restock(RID, USER, STORE)  # the person presses the button the task names
    for _ in range(2):
        units = _units(native)
        assert sorted(s for s, _o in units.values()) == ["AVAILABLE", "SOLD"], units
        assert native.stock_units.count_documents({}) == 2 and _booked(native) == (1.0, 1.0)
        assert native.stock_units.find_one({"status": "AVAILABLE"})["return_slot"] == SLOT0
        assert native.returns.find_one({"return_id": RID})["restock_applied"] is True
        assert R.finish_stopped_stock_steps() == 0
        assert R._retry_restock(RID, USER, STORE)["message"] == "Already restocked"


def test_a_historical_stock_in_task_whose_save_is_unknown_keeps_its_booking_one_task(native, monkeypatch):
    """Must-fix 2, historical form (and D129(1)'s lost-reply residual): Goods
    back books the frame, its task insert LANDS but the reply is lost and the
    read-back fails. Before: it took its booking back, so Update stock booked
    the same frame under its own run and raised a SECOND open 'add 1
    returned frame(s) through stock-in' task."""
    _refund_record(native, units=(), historical=True)
    row = _review(native)
    real, seen = SR.raise_stock_in_task, {}

    def lost_reply(*a, **k):
        out = real(*a, **k)
        if not seen:
            seen["landed"] = out.get("applied")
            return {**out, "applied": False, "reason": "stock_in_task_not_saved"}
        return out

    monkeypatch.setattr(SR, "raise_stock_in_task", lost_reply)
    assert SR.goods_back(native, row, user_id="u1")["status"] == "stock_in"
    assert seen["landed"] is True and _booked(native) == (1.0, 1.0)  # the booking stays
    assert R._retry_restock(RID, USER, STORE)["restock_applied"] is True
    SR.goods_back(native, _row(native), user_id="u2")
    for _ in range(2):
        assert [t["status"] for t in _stock_in_tasks(native)] == ["OPEN"], _stock_in_tasks(native)
        assert _booked(native) == (1.0, 1.0) and native.stock_units.count_documents({}) == 0
        assert R.finish_stopped_stock_steps() == 0
        assert R._retry_restock(RID, USER, STORE)["message"] == "Already restocked"


def test_goods_back_that_cannot_route_never_unbooks_the_piece_a_live_update_stock_lands(native, monkeypatch):
    """Grey area 1 (D130(5)): an online order with no fulfilment stamp. Update
    stock W1 (the presser's shop routes it) books and stalls before its unit
    write; Goods back (no shop to route to) takes W1's booking over and places
    nothing. Before: Goods back took the booking back, W1 landed U1 and
    stamped applied -- the order line reading the unit as not returned."""
    monkeypatch.delenv("ONLINE_FULFILLMENT_STORE_ID", raising=False)
    native.stores.insert_many([{"store_id": STORE, "store_code": "RACE", "store_type": "RETAIL"},
                               {"store_id": "BV-ONLINE-01", "store_code": "ONL", "store_type": "ONLINE",
                                "is_online_store": True}])
    native.orders.insert_one({
        "_id": OID, "order_id": OID, "store_id": "BV-ONLINE-01", "order_number": "R-1", "channel": "ONLINE",
        "items": [{"item_id": "li1", "product_id": PID, "sku": "F1", "product_name": "Frame",
                   "quantity": 1, "unit_price": 1000.0}]})
    native.stock_units.insert_one({"_id": "U1", "stock_id": "U1", "product_id": PID, "store_id": STORE,
                                   "status": "SOLD", "order_id": OID, "quantity": 1})
    native.returns.insert_one({
        "return_id": RID, "store_id": "BV-ONLINE-01", "order_id": OID, "status": "COMPLETED",
        "shopify_refund_id": RF, "items": [dict(LINE)], "restock_applied": False,
        "restock_in_progress": False, "restocked": []})
    row = {**_review(native), "store_id": "BV-ONLINE-01"}
    native.shopify_refund_review.update_one({"review_id": "RV-1"}, {"$set": {"store_id": "BV-ONLINE-01"}})
    real, seen = R._flip_sold_unit, {}

    def stalled(*a, **k):
        if not seen:
            seen["goods"] = SR.goods_back(native, row, user_id="u1")["status"]
        return real(*a, **k)

    monkeypatch.setattr(R, "_flip_sold_unit", stalled)
    w1 = R._retry_restock(RID, USER, STORE)
    assert seen["goods"] == "not_restocked" and w1["restock_applied"] is True, (seen, w1)
    monkeypatch.setattr(R, "_flip_sold_unit", real)
    assert _units(native) == {"U1": ("AVAILABLE", None)}
    assert _booked(native) == (1.0, 1.0)
    assert R._retry_restock(RID, USER, STORE)["message"] == "Already restocked"
    assert native.stock_units.count_documents({}) == 1


def test_a_goods_back_press_that_was_taken_over_never_releases_its_takers_row(native, monkeypatch):
    """Builder limit 2: press A stalls; press B (two minutes later) takes the
    row over and puts the goods back; A wakes, its own write fails. Before:
    A's stamp cleared goods_back_at on the row B had finished, and the next
    press claimed the row again instead of answering 'already put back'."""
    _refund_record(native)
    row = _review(native)
    real, state = R._flip_sold_unit, {"calls": 0}

    def flip(*a, **k):
        state["calls"] += 1
        if state["calls"] == 1:  # A
            native.shopify_refund_review.update_one({"review_id": "RV-1"}, {"$set": {"goods_back_at": OLD}})
            state["b"] = SR.goods_back(native, _row(native), user_id="u2")["status"]
            raise RuntimeError("socket reset")
        return real(*a, **k)

    monkeypatch.setattr(R, "_flip_sold_unit", flip)
    assert SR.goods_back(native, row, user_id="u1")["status"] == "not_restocked"
    assert state["b"] == "restocked"
    monkeypatch.setattr(R, "_flip_sold_unit", real)
    after = _row(native)
    assert after["goods_back_at"] and after["goods_restock"]["restock_applied"] is True
    assert SR.goods_back(native, _row(native), user_id="u3")["status"] == "duplicate"
    assert _units(native) == {"U1": ("AVAILABLE", None)} and _booked(native) == (1.0, 1.0)


def test_a_shopify_refund_records_blocked_task_is_refused_once_when_closed_by_hand(native):
    """Builder limit 1 vs D130(2): a Shopify-refund record has no History
    label or button, so its blocked-restock task is the only thing telling a
    person. Before: a manager could close it at once with the unit SOLD."""
    _refund_record(native)
    task = native.tasks.find_one({"task_id": "TSK-BLOCKED"})
    with pytest.raises(HTTPException) as refused:
        asyncio.run(R.note_stock_task_closed(RID, USER, task))
    assert refused.value.status_code == 409 and "task says" in refused.value.detail
    after = native.tasks.find_one({"task_id": "TSK-BLOCKED"})
    assert after["status"] == "OPEN" and after["payload"]["close_refused"] is True
    assert after["description"] == task["description"]  # its own cause and action (D129(3))
    asyncio.run(R.note_stock_task_closed(RID, USER, after))  # the second close is the person's call
    assert native.tasks.count_documents({"source_ref": BLOCKED}) == 1


def test_a_confirm_whose_restock_was_left_open_is_finished_by_the_sweep(native, monkeypatch):
    """Builder limit 1 vs D130(1): a confirm that says the goods are back
    STARTS the stock step. Before: it left no token, so the sweep never ran
    it -- with its task closed by hand the unit stayed SOLD, nobody told."""
    _post_fixture(native)
    real = _transient_once(monkeypatch)
    out = SR.post_from_review(native, _row(native))
    assert out["status"] == "credited" and out["restock_applied"] is False, out
    monkeypatch.setattr(R, "_flip_sold_unit", real)
    doc = native.returns.find_one({"shopify_refund_id": RF})
    assert doc.get("restock_claim") and doc["restock_in_progress"] is False, doc
    assert _units(native) == {"U1": ("SOLD", OID)} and _booked(native) == (1.0, 1.0)
    assert R.finish_stopped_stock_steps() == 0  # not while the post may still be running
    native.returns.update_one({"shopify_refund_id": RF}, {"$set": {"restock_started_at": OLD}})
    assert R.finish_stopped_stock_steps() == 1
    for _ in range(2):
        assert _units(native) == {"U1": ("AVAILABLE", None)} and _booked(native) == (1.0, 1.0)
        doc = native.returns.find_one({"shopify_refund_id": RF})
        assert doc["restock_applied"] is True and doc["restock_stock_ids"] == ["U1"]
        assert native.credit_note_ledger.count_documents({}) == 1
        assert R.finish_stopped_stock_steps() == 0


def test_a_take_over_whose_write_fails_never_reads_as_put_back(native, monkeypatch):
    """Found while hunting this round: a run that takes over another run's
    booking (settle_bookings -> _adopt) and whose write does not land, the
    old token still on the line. Before: read as 'taken back', the cap
    counted the booking as done, nothing was placed and the record was
    stamped 'Restock applied' with U1 SOLD -- no task, no door left."""
    _seed(native, units=["U1"])
    native.returns.update_one({"return_id": RID}, {"$set": {"shopify_refund_id": RF}})
    real = _transient_once(monkeypatch)
    assert R._retry_restock(RID, USER, STORE)["restock_applied"] is False  # its booking stays
    monkeypatch.setattr(R, "_flip_sold_unit", real)
    assert _booked(native) == (1.0, 1.0)
    real_coll = R._orders_coll

    class _TakeOverDown:
        def __getattr__(self, name):
            return getattr(real_coll(), name)

        def update_one(self, flt, update, *a, **k):
            if any("$[t]" in key for key in (update.get("$set") or {})):
                raise RuntimeError("socket reset")  # the take-over write, not landed
            return real_coll().update_one(flt, update, *a, **k)

    monkeypatch.setattr(R, "_orders_coll", _TakeOverDown)
    try:
        out = R._retry_restock(RID, USER, STORE)
        assert out["restock_applied"] is False, out
    except HTTPException as exc:
        assert exc.status_code in (409, 503), exc.detail
    assert _units(native) == {"U1": ("SOLD", OID)} and _booked(native) == (1.0, 1.0)
    assert native.returns.find_one({"return_id": RID})["restock_applied"] is False
    monkeypatch.setattr(R, "_orders_coll", real_coll)
    assert R._retry_restock(RID, USER, STORE)["restock_applied"] is True
    for _ in range(2):
        assert _units(native) == {"U1": ("AVAILABLE", None)} and _booked(native) == (1.0, 1.0)
        assert native.stock_units.count_documents({}) == 1
        assert R._retry_restock(RID, USER, STORE)["message"] == "Already restocked"
