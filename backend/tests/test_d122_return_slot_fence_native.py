"""
IMS 2.0 - #1397 + #1413 (D122(2), D122(3), D82(2)): the stock write of a
return is fenced ON THE STOCK ROW. NATIVE Mongo (PyMongo, the app's own
index set), stock ROWS asserted.
==========================================================================
A timed claim on the returns record is a check-then-write: a holder that
passed its liveness check and then stalled could still land its unit late
(Claude's reviewers on head c010f935; the class Codex rejected in #1397
round 7 and reproduced on #1413 round 1 as P1-A, a paid resale un-sold by a
stale retry). The fence is the unit's return slot plus its ownership:

  * a flip matches only a unit still SOLD on THIS bill, and writes the
    return's slot; a mint inserts under the slot. The slot is unique in the
    database (uniq_stock_unit_return_slot / _slots), so the second write of
    a piece changes nothing -- also after the unit was sold again;
  * the age of the claim decides only who continues. The tests run each
    sequence with the claim check ON and with it switched OFF: the stock
    rows are the same.

Before, on head c010f935 (the timed claim, no slot): sequence 1 below ended
with the stalled holder's unit AVAILABLE beside the unit its taker (or a
person told to 'add one by hand') had put back -- two rows for one piece.

Same harness as test_return_retry_race_native (#1413). No emoji (cp1252).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.routers import returns as R  # noqa: E402
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

OLD = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
SLOT = f"{RID}|back|{PID}|0"


def _units(db):
    return {u["_id"]: (u["status"], u.get("order_id")) for u in db.stock_units.find({})}


def _stalled_after_its_liveness_check(db, monkeypatch, meanwhile):
    """Worker A passes its claim check and stalls right before its unit write
    (_flip_sold_unit is called after _check_restock_fence). While it is
    stalled its claim ages, ``meanwhile`` runs, and then A's write goes on."""
    real, seen = R._flip_sold_unit, {}

    def stalled(*a, **k):
        if not seen:
            seen["a"] = True
            db.returns.update_one({"return_id": RID}, {"$set": {"restock_started_at": OLD}})
            seen["meanwhile"] = meanwhile()
        return real(*a, **k)

    monkeypatch.setattr(R, "_flip_sold_unit", stalled)
    return seen


def _claim_check(monkeypatch, on):
    if not on:  # the slot alone must hold: the claim check never runs
        monkeypatch.setattr(R, "_check_restock_fence", lambda: None)


@pytest.mark.parametrize("claim_check", [True, False])
def test_a_holder_stalled_after_its_liveness_check_lands_nothing_once_its_taker_finished(
        native, monkeypatch, claim_check):
    """Sequence 1 (must-fix 2). A two-unit line, ONE piece returned. A stalls
    before its flip; B takes the stale claim over, puts U1 back and stamps.
    A resumes: its flip of U1 misses (no longer SOLD on this bill); the
    line's other unit U2 is refused by the slot. One piece = one AVAILABLE
    row; the customer's other unit stays SOLD."""
    _seed(native, units=["U1", "U2"], qty=2)
    _claim_check(monkeypatch, claim_check)
    seen = _stalled_after_its_liveness_check(native, monkeypatch, lambda: R._retry_restock(RID, USER, STORE))
    _stale()  # A: nothing more is moved; 409 (taken over) or a credited answer
    assert seen["meanwhile"]["restock_applied"] is True
    assert _units(native) == {"U1": ("AVAILABLE", None), "U2": ("SOLD", OID)}
    assert native.stock_units.count_documents({}) == 2  # nothing minted
    assert native.stock_units.find_one({"_id": "U1"})["return_slot"] == SLOT
    doc = native.returns.find_one({"return_id": RID})
    assert doc["restock_applied"] is True and doc["restock_in_progress"] is False
    assert doc["restock_stock_ids"] == ["U1"]
    assert R._retry_restock(RID, USER, STORE)["message"] == "Already restocked"
    assert _units(native) == {"U1": ("AVAILABLE", None), "U2": ("SOLD", OID)}


@pytest.mark.parametrize("claim_check", [True, False])
@pytest.mark.parametrize("units", [["U1"], ["U1", "U2"]])
def test_the_same_after_the_unit_was_sold_again_the_new_bill_keeps_its_unit(native, monkeypatch, claim_check, units):
    """Sequence 2 (#1413 round 1, P1-A, the paid-resale repeat). As above,
    and before A resumes the returned unit is SOLD again on a new, paid bill.
    A's flip must not touch it (it is SOLD -- on ANOTHER bill), must not flip
    the line's other unit, and must not mint a row for the piece."""
    _seed(native, units=units, qty=len(units))
    _claim_check(monkeypatch, claim_check)

    def taker_finishes_then_the_unit_is_sold_again():
        out = R._retry_restock(RID, USER, STORE)
        native.orders.insert_one({"_id": "ORD-NEW", "order_id": "ORD-NEW", "store_id": STORE, "status": "CONFIRMED",
                                  "amount_paid": 1000.0, "items": [{"item_id": "n1", "product_id": PID,
                                                                    "quantity": 1, "unit_price": 1000.0}]})
        native.stock_units.update_one({"_id": "U1", "status": "AVAILABLE"}, {"$set": {
            "status": "SOLD", "order_id": "ORD-NEW", "sold_at": datetime.now(timezone.utc).isoformat(),
            "sold_to_customer_id": "CUST-NEW"}})
        return out

    seen = _stalled_after_its_liveness_check(native, monkeypatch, taker_finishes_then_the_unit_is_sold_again)
    _stale()
    assert seen["meanwhile"]["restock_applied"] is True
    expected = {"U1": ("SOLD", "ORD-NEW")}
    if len(units) == 2:
        expected["U2"] = ("SOLD", OID)
    assert _units(native) == expected  # the new bill keeps its unit; nothing else moved, nothing minted
    sold = native.stock_units.find_one({"_id": "U1"})
    assert sold["sold_to_customer_id"] == "CUST-NEW" and SLOT in sold["return_slots"]
    # A third press says done and repairs nothing it should not.
    assert R._retry_restock(RID, USER, STORE)["message"] == "Already restocked"
    assert _units(native) == expected


def test_a_minted_unit_written_twice_is_one_row(native):
    """The bill has no SOLD unit of its own for the piece, so it is minted
    (D5). Two writers with the same view of the return (a stalled one and its
    taker) both mint: the second insert is refused by the slot."""
    _seed(native, units=[])
    doc = native.returns.find_one({"return_id": RID})
    order = native.orders.find_one({"order_id": OID})
    lines = R._retry_lines(doc, order)
    first = R._restock_good_items(lines, STORE, RID, order_id=OID, order=order, moved_ids=set())
    again = R._restock_good_items(lines, STORE, RID, order_id=OID, order=order, moved_ids=set())
    assert first["applied"] is True and again["applied"] is True
    minted = list(native.stock_units.find({"source_type": "RETURN", "source_id": RID}))
    assert len(minted) == 1 and minted[0]["return_slot"] == SLOT and minted[0]["status"] == "AVAILABLE"
    assert again["restock_stock_ids"] == first["restock_stock_ids"] == [str(minted[0]["stock_id"])]
    assert native.stock_units.count_documents({}) == 1


def test_the_sweep_finishes_a_counter_return_whose_worker_died(native):
    """D122(3): the claim is on the record, older than a worker runs, and
    nobody presses anything. The hourly sweep runs the step."""
    _seed(native, units=["U1"])
    native.returns.update_one({"return_id": RID}, {"$set": {
        "restock_in_progress": True, "restock_claim": "dead-worker", "restock_started_at": OLD,
        "stock_pending": True}})
    assert R.finish_stopped_stock_steps() == 1
    assert _units(native) == {"U1": ("AVAILABLE", None)}
    doc = native.returns.find_one({"return_id": RID})
    assert (doc["restock_applied"], doc["stock_pending"], doc["restock_in_progress"]) == (True, False, False)
    assert doc["restock_claim"] != "dead-worker" and doc["restock_stock_ids"] == ["U1"]
    assert R.finish_stopped_stock_steps() == 0


def test_the_sweep_finishes_a_shopify_refund_return_nobody_pressed(native, monkeypatch):
    """D122(3), the reviewers' grey area: a Shopify-refund return whose
    'Update stock' worker died holding the claim. Before: the door answered
    'stopped part way, check each unit' (add one by hand), with no task, no
    label and no finisher. Now the sweep finishes it through the Shopify
    doors' own booked restock: the unit is back once and booked on its line."""
    from api.services import shopify_refund as SR

    _seed(native, units=["U1"])
    native.returns.update_one({"return_id": RID}, {"$set": {
        "shopify_refund_id": "RF-1", "restock_in_progress": True, "restock_claim": "dead-worker",
        "restock_started_at": OLD}})
    monkeypatch.setattr(SR, "_cap_restock_to_returnable", lambda lines, order, rid: (lines, None, []))
    assert R.finish_stopped_stock_steps() == 1
    assert _units(native) == {"U1": ("AVAILABLE", None)}
    unit = native.stock_units.find_one({"_id": "U1"})
    assert unit["return_slot"] == SLOT and unit["returned_by_return_id"] == RID
    doc = native.returns.find_one({"return_id": RID})
    assert doc["restock_applied"] is True and doc["restock_in_progress"] is False
    line = native.orders.find_one({"order_id": OID})["items"][0]
    assert float(line.get("returned_qty") or 0) == 1.0  # booked on its order line, once
    # Nothing left for any later door: the sweep and the retry find it done.
    assert R.finish_stopped_stock_steps() == 0
    assert R._retry_restock(RID, USER, STORE)["message"] == "Already restocked"
    assert _units(native) == {"U1": ("AVAILABLE", None)} and native.stock_units.count_documents({}) == 1


def test_the_shopify_doors_refuse_when_a_return_slot_index_is_missing(native):
    """D82(2) / D122(6): the Shopify refund doors reach the stock through
    _restock_booked. With a slot index missing it refuses BEFORE it books or
    writes anything -- a missing index can double stock through no door."""
    from api.services import shopify_refund as SR

    _seed(native, units=["U1"])
    native.stock_units.drop_index("uniq_stock_unit_return_slots")
    order = native.orders.find_one({"order_id": OID})
    lines = R._retry_lines(native.returns.find_one({"return_id": RID}), order)
    called = []
    with pytest.raises(SR.ReturnSlotIndexMissing):
        SR._restock_booked(order, lines, "RF-1", lambda ls: called.append(ls) or {"applied": True})
    assert called == [] and _units(native) == {"U1": ("SOLD", OID)}
    assert not native.orders.find_one({"order_id": OID})["items"][0].get("returned_qty")  # nothing booked
