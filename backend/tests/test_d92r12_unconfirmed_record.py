"""
IMS 2.0 - D92 round 12 (#1397, Claude's review of head 5f05b9ea).
==========================================================================
Native Mongo before the fix (handovers/evidence/round12-d92/PR-1397,
claude_d92r12_probe.py defect): U -- a cash press's record insert LANDED,
its ack was lost and the read-back failed: 503 "do not pay", but Needs
completing never listed it and "cash NOT paid" replayed "already exists"
(the record says CASH paid, Day-End nets it, the customer is never paid);
the identical re-press replayed success. K -- a press died after its
money-out mark, Accounts answered "cash NOT paid", and the identical
re-press booked a SECOND return (201, cash) of the line's other unit.
T -- the blocked-restock task insert failed, the answer still said "a task
was raised". A -- the fence's "restocking" stamp landed unacknowledged, the
press put nothing back and answered 409, the record said "left to the
counter": no task and its Restock refused.

Real code over the strict fakes of test_pos_money_doors (till). No emoji (cp1252).
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from pymongo.errors import AutoReconnect  # noqa: E402

from tests.test_nothing_paid_fence_d85 import _aged_past_the_timeout, _cash_press, _retry_restock  # noqa: E402
from tests.test_pos_money_doors import _advance_client, _hdr, _stored, _user, returns_router, till  # noqa: E402,F401
from tests.test_pos_money_doors_r12 import tasks  # noqa: E402,F401
from tests.test_pos_money_doors_r13 import _complete  # noqa: E402

pytestmark = pytest.mark.usefixtures("gst_numbers")


def _press(payload, key):
    return _advance_client().post("/api/v1/returns", json=payload,
                                  headers={**_hdr("STORE_MANAGER"), "Idempotency-Key": key})


def _landed_but_unconfirmed(rcoll, monkeypatch):
    """The press's own record insert LANDS, its acknowledgement is lost, and
    every read of the returns fails until ``done``."""
    real_insert, real_find, state = rcoll.insert_one, rcoll.find_one, {}

    def insert(doc, *a, **k):
        if not doc.get("recovered") and not state:
            real_insert(doc, *a, **k)
            state["landed"] = doc["return_id"]
            raise AutoReconnect("insert landed, acknowledgement lost")
        return real_insert(doc, *a, **k)

    def find_one(*a, **k):
        if state.get("landed") and not state.get("done"):
            raise AutoReconnect("returns unreadable")
        return real_find(*a, **k)

    monkeypatch.setattr(rcoll, "insert_one", insert)
    monkeypatch.setattr(rcoll, "find_one", find_one)
    return state


def _needs_completing():
    return asyncio.run(returns_router.needs_completing(current_user=_user("ACCOUNTANT")))["items"]


@pytest.mark.parametrize("cash_leg_paid", [False, True])
def test_a_landed_record_the_counter_was_told_not_to_pay_is_settled_once(till, monkeypatch, tasks, cash_leg_paid):
    payload, rcoll, _restocks = _cash_press(till, monkeypatch)
    state = _landed_but_unconfirmed(rcoll, monkeypatch)
    r = _press(payload, "K-U")
    assert r.status_code == 503 and r.json()["detail"] == returns_router.RECORD_UNCONFIRMED_MESSAGE, r.text
    state["done"] = True
    rid = state["landed"]
    assert _stored(till)["refund_holds"][rid].get(returns_router.UNCONFIRMED_MARK) is True
    # The identical re-press never replays the stored record as "recorded".
    again = _press(payload, "K-U")
    assert again.status_code == 503 and again.json()["detail"] == returns_router.RECORD_UNCONFIRMED_MESSAGE
    _aged_past_the_timeout(till)
    listed = [i for i in _needs_completing() if i["return_id"] == rid]
    assert listed and listed[0]["record_unconfirmed"] is True and listed[0]["cash_leg"] == 1800.0, listed
    rec = _complete(rid, cash_leg_paid=cash_leg_paid)
    stored = rcoll.find_one({"return_id": rid})
    assert len(list(rcoll.find({"return_id": rid}))) == 1 and stored["recovered"]["cash_leg_paid"] is cash_leg_paid
    owed_tasks = [t for t in tasks if t.get("dedupe_ref") == f"return_cash_owed:{rid}"]
    if cash_leg_paid:
        assert stored["refund_tenders"][0]["method"] == "CASH" and not stored.get("owed_amount")
        assert stored["drawer_auto_netted"] is True and owed_tasks == []
    else:
        # The cash part never left the drawer: OWED, never netted at Day-End.
        assert stored["owed_amount"] == 1800.0 and stored["refund_tenders"] == [], stored
        assert stored["drawer_auto_netted"] is False and len(owed_tasks) == 1, tasks
        assert "owed Rs 1800.00" in rec["message"]
    assert rid not in [i["return_id"] for i in _needs_completing()]
    assert _complete(rid, cash_leg_paid=not cash_leg_paid)["message"] == "This return's record already exists."
    assert rcoll.find_one({"return_id": rid})["recovered"]["cash_leg_paid"] is cash_leg_paid  # the first answer
    last = _press(payload, "K-U")
    assert last.status_code == 409 and last.json()["detail"] == returns_router.RECORD_TAKEN_MESSAGE, last.text


def test_the_identical_re_press_after_needs_completing_books_nothing(till, monkeypatch, tasks):
    """MF2: the press died after its money-out mark; Accounts answered "cash
    NOT paid". Before: the same Idempotency-Key found no record (Needs
    completing wrote none with the key) and booked the line's next unit."""
    payload, rcoll, restocks = _cash_press(till, monkeypatch)
    real_fence, died = returns_router._fence_restock, []

    def dies(order_id, rid):
        if not died:
            died.append(rid)
            raise RuntimeError("the press died")
        return real_fence(order_id, rid)

    monkeypatch.setattr(returns_router, "_fence_restock", dies)
    with pytest.raises(RuntimeError):
        _press(payload, "K-K")
    assert _press(payload, "K-K").status_code == 409  # still settling
    rid = _aged_past_the_timeout(till)
    rec = _complete(rid, cash_leg_paid=False)
    assert rec["status"] == "COMPLETED" and rec["idempotency_key"] == "K-K" and rec["owed_amount"] == 1800.0
    r = _press(payload, "K-K")
    assert r.status_code == 409 and r.json()["detail"] == returns_router.RECORD_TAKEN_MESSAGE, r.text
    assert [d["return_id"] for d in rcoll.find({})] == [rid] and restocks == []


def test_an_unsaved_blocked_restock_task_is_never_claimed(till, monkeypatch):
    """MF4: create_system_task answers None when its insert fails."""
    payload, rcoll, restocks = _cash_press(till, monkeypatch)

    def unreadable(*a, **k):
        raise AutoReconnect("bill unreadable")

    monkeypatch.setattr(returns_router, "_hold_cas", unreadable)
    monkeypatch.setattr("api.services.task_triggers.create_system_task", lambda repo, **k: None)
    monkeypatch.setattr("api.dependencies.get_task_repository", lambda: object(), raising=False)
    r = _press(payload, "K-T")
    assert r.status_code == 201 and restocks == [], r.text
    body = r.json()
    assert "a task was raised" not in body["message"] and "tell the store manager" in body["message"]
    assert body["stock_note"] and "tell the store manager" in body["stock_note"]


def test_a_blocked_restock_task_already_open_counts_as_raised(monkeypatch):
    class _Tasks:
        @staticmethod
        def find(query):
            return [{"source_ref": query["source_ref"], "status": "OPEN"}]

    class _Repo:
        collection = _Tasks()

    monkeypatch.setattr("api.services.task_triggers.create_system_task", lambda repo, **k: None)  # deduped
    monkeypatch.setattr("api.dependencies.get_task_repository", lambda: _Repo(), raising=False)
    assert returns_router._raise_restock_blocked_task("RET-X", "ORD-1", "TST01", [{"sku": "F1"}],
                                                     unread="the bill") is True


def test_a_press_that_put_nothing_back_leaves_the_taken_record_restockable(till, monkeypatch, tasks):
    """Flag (a): the fence's "restocking" stamp landed but its ack was lost
    (RESTOCK_UNKNOWN: nothing put back); Accounts took the record meanwhile,
    so it said "left to the counter"; the press collided -> 409. Before: no
    task and the record's Restock refused -- the unit stayed SOLD."""
    payload, rcoll, restocks = _cash_press(till, monkeypatch)
    real_fence = returns_router._fence_restock

    def stamp_ack_lost(order_id, rid):
        assert real_fence(order_id, rid) is True  # the stamp landed
        return returns_router.RESTOCK_UNKNOWN

    monkeypatch.setattr(returns_router, "_fence_restock", stamp_ack_lost)
    real_insert, out = rcoll.insert_one, {}

    def taken_before_the_insert(doc, *a, **k):
        if not doc.get("recovered") and "record" not in out:
            out["rid"] = rid = _aged_past_the_timeout(till)
            t = threading.Thread(target=lambda: out.update(record=_complete(rid, cash_leg_paid=False)))
            t.start()
            t.join(60)
        return real_insert(doc, *a, **k)

    monkeypatch.setattr(rcoll, "insert_one", taken_before_the_insert)
    r = _press(payload, "K-A")
    assert out["record"].get("restock_left_to_counter") is True
    assert r.status_code == 409 and r.json()["detail"] == returns_router.RECORD_TAKEN_MESSAGE, r.text
    rid = out["rid"]
    assert restocks == [] and not rcoll.find_one({"return_id": rid}).get("restock_left_to_counter")
    raised = [t for t in tasks if t.get("dedupe_ref") == f"return_restock_blocked:{rid}"]
    assert len(raised) == 1 and raised[0]["priority"] == "P1", tasks
    first, second = _retry_restock(rid), _retry_restock(rid)
    assert first["restock_applied"] is True and second["message"] == "Already restocked"
    assert restocks == [rid]  # one frame, one stock row


def test_a_later_return_of_the_bill_keeps_the_unconfirmed_hold(till, monkeypatch, tasks):
    """The next counter return on the bill drops every hold whose record
    counts -- an unconfirmed one too, before: its mark was the only trace that
    the counter was told not to pay, and Needs completing never asked."""
    payload, rcoll, _restocks = _cash_press(till, monkeypatch)
    state = _landed_but_unconfirmed(rcoll, monkeypatch)
    assert _press(payload, "K-L1").status_code == 503
    state["done"] = True
    rid = state["landed"]
    assert _press(payload, "K-L2").status_code == 201  # the line's other unit, a new press
    assert _stored(till)["refund_holds"][rid].get(returns_router.UNCONFIRMED_MARK) is True
    _aged_past_the_timeout_of(till, rid)
    assert rid in [i["return_id"] for i in _needs_completing()]


def _aged_past_the_timeout_of(till, rid):
    from datetime import datetime, timedelta, timezone

    from api.routers import customers as cust_router

    old = (datetime.now(timezone.utc) - timedelta(seconds=cust_router.PENDING_TIMEOUT_SECONDS + 5)).isoformat()
    till["db"].get_collection("orders").update_one({"order_id": "ORD-1"}, {"$set": {f"refund_holds.{rid}.at": old}})
