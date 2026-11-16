"""
IMS 2.0 - D92 (#1397, Claude's review of head 54c73dbd): a missing hold is
never permission to restock; DAMAGED units are always set aside; a lost
acknowledgement whose read-back fails is UNKNOWN; a press whose record
Needs completing took answers 409, never 201.
==========================================================================
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


@pytest.mark.parametrize("record, read_fails, expected", [
    (True, False, "record_taken"), (False, False, True), (False, True, "unknown")])
def test_a_missing_hold_rereads_the_durable_record(till, monkeypatch, record, read_fails, expected):
    rcoll = till["db"].get_collection("returns")
    till["db"].get_collection("orders").insert_one({"order_id": "ORD-1", "refund_holds": {}, "refund_holds_v": 1})
    if record:
        rcoll.insert_one({"_id": "recovered:RET-X", "return_id": "RET-X", "status": "COMPLETED"})
    if read_fails:
        def boom(*a, **k):
            raise AutoReconnect("returns unreadable")
        monkeypatch.setattr(rcoll, "find_one", boom)
    assert returns_router._fence_restock("ORD-1", "RET-X") == expected


def test_a_press_that_cannot_check_its_record_restocks_nothing_and_says_so(till, monkeypatch, tasks):
    payload, rcoll, restocks = _cash_press(till, monkeypatch)

    def unreadable(*a, **k):
        raise AutoReconnect("bill unreadable")

    monkeypatch.setattr(returns_router, "_hold_cas", unreadable)
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert r.status_code == 201 and restocks == [], r.text
    assert "NOT put back in stock" in r.json()["message"] and r.json()["restock_applied"] is False


# --- D92(2) ----------------------------------------------------------------------


def test_a_damaged_unit_is_set_aside_even_when_cash_paid_took_the_record(till, monkeypatch, tasks):
    payload, rcoll, restocks = _cash_press(till, monkeypatch)
    payload["items"][0].update(condition="DAMAGED", restock=False)
    set_aside = []
    monkeypatch.setattr(returns_router, "_quarantine_damaged_units",
                        lambda lines, oid, order, rid, uid: set_aside.append(rid) or [
                            {"stock_id": "S-D", "store_id": "TST01", "product_id": "P1"}])
    out = {}
    _stall_after_money_out(till, monkeypatch, out)
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert r.status_code == 409, r.text
    assert set_aside == [out["rid"]]
    rec = rcoll.find_one({"_id": f"recovered:{out['rid']}"})
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
    assert r.json()["detail"] == returns_router.CREDIT_UNCONFIRMED_MESSAGE
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
