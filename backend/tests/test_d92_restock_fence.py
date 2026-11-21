"""
IMS 2.0 - D92 (#1397, Claude's review of head 54c73dbd): a missing hold is
never permission to restock; DAMAGED units are always set aside; a lost
acknowledgement whose read-back fails is UNKNOWN; a press whose record
Needs completing took answers 409, never 201.
==========================================================================
D95 (head a72aec88) replaced the hold marks these rules were built on with
ONE insert-once record per return (returns._insert_record): the same
outcomes are held here on the strict fakes, and on native Mongo with stock
and ledger rows in test_d95_one_record_native.py.

Native Mongo before the fix (handovers/evidence/round10-d92/PR-1397,
claude_d92_probe.py defect): M -- a cash press stalled after its money-out
mark, Cash paid wrote the record, a later counter return dropped the press's
hold, the press woke: it read "no hold" as "may restock", put a unit back
AND the record's Restock put another back (two AVAILABLE for one frame),
answering 201. N -- the hold still there: the press answered 201 "Refund of
Rs 900 recorded" although Cash paid had recorded the cash as paid. O -- a
DAMAGED unit stayed SOLD. S1 -- a points-paid line landed, its ack was lost
and the read-back failed: "NOTHING was recorded", claims given back, number
written off beside the ISSUED note; the retry filed a second credit note.

Real code over the strict fakes of test_pos_money_doors (till). No emoji (cp1252).
"""

from __future__ import annotations

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from pymongo.errors import AutoReconnect  # noqa: E402

from api.services import store_credit_ledger as scl  # noqa: E402
from tests.test_nothing_paid_fence_d85 import (  # noqa: E402
    _all_points, _cash_paid_now, _cash_press, _counted, _credit_note_return, _retry_restock)
from tests.test_pos_money_doors import _advance_client, _hdr, _ledger, _stored, returns_router, till  # noqa: E402,F401
from tests.test_pos_money_doors_r9 import _customer  # noqa: E402
from tests.test_pos_money_doors_r12 import tasks  # noqa: E402,F401

pytestmark = pytest.mark.usefixtures("gst_numbers")


def _task_age():
    """A record older than the D95 task's wait (10 minutes) and younger than
    the 15 minutes after which the hourly sweep STARTS a counter record's step
    nobody started (D130(1)): the task is raised, the step still waits."""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    return (_dt.now(_tz.utc) - _td(minutes=11)).isoformat()


def _stall_after_money_out(till, monkeypatch, out, then=None):
    """The press stalls right after its money-out mark; Cash paid answers
    (and ``then`` runs) before it wakes."""
    real_keep = returns_router._keep_order_refund_hold

    def keep(order_id, rid, *a, extra=None, **k):
        res = real_keep(order_id, rid, *a, extra=extra, **k)
        if "money_out" in (extra or {}) and "record" not in out:
            _cash_paid_now(till, out)
            if then:
                then(out["rid"])
        return res

    monkeypatch.setattr(returns_router, "_keep_order_refund_hold", keep)


def _drop_hold(till):
    """What the next counter return's reservation does once the record counts."""
    def drop(rid):
        orders = till["db"].get_collection("orders")
        holds = dict(orders.find_one({"order_id": "ORD-1"})["refund_holds"])
        holds.pop(rid)
        orders.update_one({"order_id": "ORD-1"}, {"$set": {"refund_holds": holds}})
    return drop


# --- D92(1) + (4) ----------------------------------------------------------------


def test_a_press_whose_hold_was_dropped_after_cash_paid_restocks_nothing(till, monkeypatch, tasks):
    """The exact sequence: Cash paid wrote the COMPLETED record, a later
    return dropped the press's hold, the press woke. Before: no hold = may
    restock -- the press and the record's Restock both put the unit back."""
    payload, rcoll, restocks = _cash_press(till, monkeypatch)
    out = {}
    _stall_after_money_out(till, monkeypatch, out, then=_drop_hold(till))
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert out["record"]["status"] == "COMPLETED", out
    assert out["rid"] not in (_stored(till).get("refund_holds") or {})
    assert r.status_code == 409 and r.json()["detail"] == returns_router.RECORD_TAKEN_MESSAGE, r.text
    assert restocks == []  # the press put nothing back
    assert len(list(rcoll.find({"return_id": out["rid"]}))) == 1
    first, second = _retry_restock(out["rid"]), _retry_restock(out["rid"])
    assert first["restock_applied"] is True and second["message"] == "Already restocked"
    assert restocks == [out["rid"]]  # one frame, one stock row


def test_a_press_that_finds_its_record_taken_answers_409_never_201(till, monkeypatch, tasks):
    """The hold still carries record_taken: before, the press answered 201
    'Refund recorded' (the cashier pays again) and raised a doc-missing task."""
    payload, rcoll, restocks = _cash_press(till, monkeypatch)
    out = {}
    _stall_after_money_out(till, monkeypatch, out)
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert r.status_code == 409 and r.json()["detail"] == returns_router.RECORD_TAKEN_MESSAGE, r.text
    assert restocks == [] and len(list(rcoll.find({"return_id": out["rid"]}))) == 1
    assert not [t for t in tasks if "return_doc_missing" in str(t.get("source_ref"))], tasks


def _fails(*a, **k):
    raise AutoReconnect("returns unreadable")


def test_the_one_insert_decides_who_owns_a_return(till, monkeypatch):
    """D95(1)/(3): landed -> None (its caller owns the return); a record
    already there (whatever _id an older build wrote it under) -> that record;
    the insert raised and the record is unreadable or reads as absent ->
    RecordUnknown (never "mine", never "theirs")."""
    rcoll = till["db"].get_collection("returns")
    assert returns_router._insert_record(rcoll, {"return_id": "RET-A", "status": "COMPLETED"}) is None
    assert rcoll.find_one({"_id": "ret:RET-A"})["return_id"] == "RET-A"
    again = returns_router._insert_record(rcoll, {"return_id": "RET-A", "status": "NOTHING_PAID"})
    assert again["status"] == "COMPLETED"  # the first insert stands
    # An older build's record of the same return (another _id) counts the same.
    rcoll.insert_one({"_id": "recovered:RET-B", "return_id": "RET-B", "status": "COMPLETED", "recovered": {}})
    real_insert = rcoll.insert_one
    monkeypatch.setattr(rcoll, "insert_one", _fails)
    assert returns_router._insert_record(rcoll, {"return_id": "RET-B"})["_id"] == "recovered:RET-B"
    with pytest.raises(returns_router.RecordUnknown):  # raised, and nothing reads back
        returns_router._insert_record(rcoll, {"return_id": "RET-C"})
    monkeypatch.setattr(rcoll, "insert_one", real_insert)
    monkeypatch.setattr(rcoll, "find_one", _fails)
    with pytest.raises(returns_router.RecordUnknown):  # a duplicate that cannot be read
        returns_router._insert_record(rcoll, {"return_id": "RET-A"})


def test_a_press_that_cannot_start_its_stock_step_restocks_nothing_and_says_so(till, monkeypatch, tasks):
    """The claim of the record's stock step cannot be written: the press puts
    nothing back and says so; the step stays open on the record with its
    button, and the hourly sweep raises ONE P1 task naming it (D122: never a
    hand entry)."""
    payload, rcoll, restocks = _cash_press(till, monkeypatch)
    real_claim, failed = rcoll.find_one_and_update, []

    def claim(*a, **k):
        if not failed:
            failed.append(1)
            raise AutoReconnect("returns unreadable")
        return real_claim(*a, **k)

    monkeypatch.setattr(rcoll, "find_one_and_update", claim)
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert r.status_code == 201 and restocks == [], r.text
    assert "NOT back in stock" in r.json()["message"] and r.json()["restock_applied"] is False
    assert "Update stock" in r.json()["stock_note"] and "do not add them by hand" in r.json()["stock_note"]
    rid = r.json()["return_id"]
    assert tasks == []  # younger than any press: the sweep raises it once it is not
    rcoll.update_one({"return_id": rid}, {"$set": {"created_at": _task_age()}})
    assert returns_router.sweep_stuck_return_holds() == 1
    raised = [t for t in tasks if t.get("dedupe_ref") == f"return_restock_blocked:{rid}"]
    assert len(raised) == 1 and raised[0]["priority"] == "P1", tasks
    assert rid in raised[0]["title"] and "Update stock" in raised[0]["description"]
    assert rcoll.find_one({"return_id": rid})["stock_pending"] is True
    # The task's step is real: the record's Restock puts the unit back, once.
    first, second = _retry_restock(rid), _retry_restock(rid)
    assert first["restock_applied"] is True and second["message"] == "Already restocked"
    assert restocks == [rid] and rcoll.find_one({"return_id": rid})["stock_pending"] is False


def _paused_at_its_insert(till, rcoll, monkeypatch, out, cash_leg_paid, read_back_fails=False):
    """The press pauses at its record insert; Accounts answer Needs
    completing; the press's insert then collides with that record."""
    from tests.test_nothing_paid_fence_d85 import _aged_past_the_timeout, _complete

    real_insert, real_find = rcoll.insert_one, rcoll.find_one

    def insert(doc, *a, **k):
        if not doc.get("recovered") and "record" not in out:
            out["rid"] = rid = _aged_past_the_timeout(till)
            t = threading.Thread(target=lambda: out.update(record=_complete(rid, cash_leg_paid=cash_leg_paid)))
            t.start()
            t.join(60)
            out["collided"] = True
        return real_insert(doc, *a, **k)

    def find_one(*a, **k):
        if out.get("collided") and read_back_fails and not out.get("healed"):
            out["reads"] = out.get("reads", 0) + 1
            raise AutoReconnect("returns unreadable")
        return real_find(*a, **k)

    monkeypatch.setattr(rcoll, "insert_one", insert)
    monkeypatch.setattr(rcoll, "find_one", find_one)


@pytest.mark.parametrize("cash_leg_paid", [False, True])
def test_a_press_that_loses_its_insert_to_needs_completing_answers_409(till, monkeypatch, tasks, cash_leg_paid):
    """D95(3)/(4): Accounts wrote the record while the press was paused at
    its own insert. The press owns nothing: 409, no unit placed by it; the
    record carries the stock step, its task and its button."""
    payload, rcoll, restocks = _cash_press(till, monkeypatch)
    out = {}
    _paused_at_its_insert(till, rcoll, monkeypatch, out, cash_leg_paid)
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert out["record"]["status"] == "COMPLETED" and out["record"]["stock_pending"] is True
    # D7: a record Needs completing wrote is retried there (Retry stock step).
    assert "Retry stock step" in out["record"]["message"] and out["record"]["stock_step_open"] is True
    assert r.status_code == 409 and r.json()["detail"] == returns_router.RECORD_TAKEN_MESSAGE, r.text
    assert restocks == [] and [d["_id"] for d in rcoll.find({"return_id": out["rid"]})] == [f"ret:{out['rid']}"]
    assert len([t for t in tasks if t.get("dedupe_ref") == f"return_restock_blocked:{out['rid']}"]) == 1, tasks
    assert not [t for t in tasks if "return_doc_missing" in str(t.get("dedupe_ref"))], tasks
    first, second = _retry_restock(out["rid"]), _retry_restock(out["rid"])
    assert first["restock_applied"] is True and second["message"] == "Already restocked"
    assert restocks == [out["rid"]]  # one frame, one stock row


def test_a_collision_whose_read_back_fails_is_not_confirmed_and_the_re_press_pays_nothing(till, monkeypatch, tasks):
    """D95(3): the press's insert collided and the record cannot be read:
    503 'Not confirmed. Do not pay yet' -- never 201. The identical re-press
    reads the record Needs completing wrote: 409."""
    payload, rcoll, restocks = _cash_press(till, monkeypatch)
    out = {}
    _paused_at_its_insert(till, rcoll, monkeypatch, out, False, read_back_fails=True)
    hdr = {**_hdr("STORE_MANAGER"), "Idempotency-Key": "press-1"}
    r = _advance_client().post("/api/v1/returns", json=payload, headers=hdr)
    assert out.get("reads"), out
    assert r.status_code == 503 and r.json()["detail"] == returns_router.RECORD_NOT_CONFIRMED_MESSAGE, r.text
    out["healed"] = True
    again = _advance_client().post("/api/v1/returns", json=payload, headers=hdr)
    assert again.status_code == 409 and again.json()["detail"] == returns_router.RECORD_TAKEN_MESSAGE, again.text
    assert restocks == [] and len(list(rcoll.find({"return_id": out["rid"]}))) == 1


def test_an_insert_that_fails_is_not_confirmed_and_the_identical_re_press_settles_it(till, monkeypatch, tasks):
    """D95(3): the insert raised and nothing reads back -- it may still land:
    503, never 'Refund recorded'. The doc rides on the kept hold; the
    identical re-press makes the SAME insert again and answers 201, with one
    record, one restock and nothing priced or claimed a second time."""
    payload, rcoll, restocks = _cash_press(till, monkeypatch)
    real_insert, state = rcoll.insert_one, {}

    def insert(doc, *a, **k):
        if not state.get("failed"):
            state["failed"] = True
            raise AutoReconnect("insert failed")
        return real_insert(doc, *a, **k)

    monkeypatch.setattr(rcoll, "insert_one", insert)
    hdr = {**_hdr("STORE_MANAGER"), "Idempotency-Key": "press-2"}
    r = _advance_client().post("/api/v1/returns", json=payload, headers=hdr)
    assert r.status_code == 503 and r.json()["detail"] == returns_router.RECORD_NOT_CONFIRMED_MESSAGE, r.text
    rid = next(iter(_stored(till)["refund_holds"]))
    hold = _stored(till)["refund_holds"][rid]
    assert hold.get("kept") is True and hold["doc"]["return_id"] == rid and hold["doc"]["press_token"]
    assert restocks == [] and rcoll.find_one({"return_id": rid}) is None
    assert not [t for t in tasks if "return_doc_missing" in str(t.get("dedupe_ref"))], tasks  # not yet abandoned
    returned = _stored(till)["items"][0].get("returned_qty")
    # Other content under the same key is a different press: refused.
    other = {**payload, "refund_tenders": [{"method": "UPI", "amount": 1800.0}], "refund_method": "UPI"}
    assert _advance_client().post("/api/v1/returns", json=other, headers=hdr).status_code == 409
    again = _advance_client().post("/api/v1/returns", json=payload, headers=hdr)
    assert again.status_code == 201 and again.json()["return_id"] == rid, again.text
    assert again.json()["restock_applied"] is True and restocks == [rid]
    assert [d["_id"] for d in rcoll.find({"order_id": "ORD-1"})] == [f"ret:{rid}"]
    assert _stored(till)["items"][0].get("returned_qty") == returned  # claimed once
    third = _advance_client().post("/api/v1/returns", json=payload, headers=hdr)
    assert third.status_code == 201 and third.json().get("_idempotent_replay") is True and restocks == [rid]


def test_an_unconfirmed_press_never_pressed_again_reaches_a_person(till, monkeypatch, tasks):
    """The 503 was abandoned and no record landed: the stuck-hold sweep
    raises the record-not-saved task once the hold is older than any press;
    Needs completing then writes the one record from the kept doc."""
    from tests.test_nothing_paid_fence_d85 import _aged_past_the_timeout, _complete

    payload, rcoll, restocks = _cash_press(till, monkeypatch)
    real_insert, down = rcoll.insert_one, [True]
    monkeypatch.setattr(rcoll, "insert_one", lambda doc, *a, **k: _fails() if down else real_insert(doc, *a, **k))
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert r.status_code == 503, r.text
    down.clear()  # the returns collection answers again
    assert returns_router.sweep_stuck_return_holds() == 0 and tasks == []  # younger than any press
    rid = _aged_past_the_timeout(till)
    assert returns_router.sweep_stuck_return_holds() == 1
    assert [t["dedupe_ref"] for t in tasks] == [f"return_doc_missing:{rid}"]
    rec = _complete(rid, cash_leg_paid=True)
    assert rec["status"] == "COMPLETED" and rec["recovered"]["source"] == "unsaved doc"
    assert rcoll.find_one({"return_id": rid})["_id"] == f"ret:{rid}"


# --- D92(2) ----------------------------------------------------------------------


def test_a_damaged_unit_is_set_aside_even_when_cash_paid_took_the_record(till, monkeypatch, tasks):
    payload, rcoll, restocks = _cash_press(till, monkeypatch)
    payload["items"][0].update(condition="DAMAGED", restock=False)
    set_aside = []
    monkeypatch.setattr(returns_router, "_quarantine_damaged_units",
                        # #1413: the press's slots (moved_ids) and (units, ok).
                        lambda lines, oid, order, rid, uid, **k: (set_aside.append(rid) or [
                            {"stock_id": "S-D", "store_id": "TST01", "product_id": "P1"}], True))
    out = {}
    _stall_after_money_out(till, monkeypatch, out)
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert r.status_code == 409, r.text
    assert set_aside == [out["rid"]]
    rec = rcoll.find_one({"_id": f"ret:{out['rid']}"})
    assert rec.get("quarantined_stock_ids") == ["S-D"], rec


# --- D92(3) ----------------------------------------------------------------------


@pytest.mark.parametrize("lands", [True, False])
def test_a_points_paid_line_whose_insert_and_read_back_both_fail_is_unknown(till, monkeypatch, tasks, lands):
    """Before: the read-back failure read as 'not written' -- the claims went
    back and the number was written off (beside an ISSUED note when it had
    landed), and the identical retry filed a second credit note."""
    payload = _credit_note_return(till, monkeypatch)
    _all_points(monkeypatch)
    released, written_off = _counted(monkeypatch)
    ledger = _ledger(till)
    real_insert, real_find, state = ledger.insert_one, ledger.find_one, {}

    def insert(doc):
        if doc.get("type") == scl.ISSUED and not state:
            state["id"] = doc["_id"]
            if lands:
                real_insert(doc)
            raise AutoReconnect("insert failed")
        return real_insert(doc)

    def find_one(flt=None, *a, **k):
        if state.get("id") and not state.get("read") and (flt or {}).get("entry_id") == state["id"]:
            state["read"] = True
            raise AutoReconnect("read-back failed")
        return real_find(flt, *a, **k)

    monkeypatch.setattr(ledger, "insert_one", insert)
    monkeypatch.setattr(ledger, "find_one", find_one)
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert state.get("read") and r.status_code == 503, r.text
    # Round 12: no balance moves on this line and it never settles by itself.
    assert r.json()["detail"] == returns_router.LINE_UNCONFIRMED_MESSAGE
    assert released == [] and written_off == []  # claims and the number kept
    hold = next(iter(_stored(till)["refund_holds"].values()))
    assert hold.get("kept") is True


def test_the_credit_line_writer_reports_an_unknown_outcome(till, monkeypatch):
    _customer(till)
    ledger = _ledger(till)

    def insert(doc):
        raise AutoReconnect("insert failed")

    real_find = ledger.find_one

    def find_one(flt=None, *a, **k):
        if "entry_id" in (flt or {}):
            raise AutoReconnect("read-back failed")
        return real_find(flt, *a, **k)

    monkeypatch.setattr(ledger, "insert_one", insert)
    monkeypatch.setattr(ledger, "find_one", find_one)
    outcome = {}
    with pytest.raises(returns_router.CreditNotConfirmed):
        returns_router._issue_store_credit(
            "CUST-1", 1000.0, reason="r", ref="RET-U", current_user={"user_id": "u1"}, store_id="TST01",
            order_id="ORD-1", note_number="TST01/C2627/0009", balance_delta=0.0, entry_id="RET-U:credit",
            tax=47.62, outcome=outcome)
    assert outcome.get("not_written") is False
