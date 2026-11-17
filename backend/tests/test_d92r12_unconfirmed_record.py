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

D95 (head a72aec88) replaced the "unconfirmed" hold mark and its cash
question with the one insert-once record: a press whose 503 is abandoned
after its record landed leaves the record as the counter wrote it (D95(5));
Needs completing has nothing to do with it.

Real code over the strict fakes of test_pos_money_doors (till). No emoji (cp1252).
"""

from __future__ import annotations

import asyncio
import os
import sys

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
def test_an_abandoned_unconfirmed_press_leaves_the_record_as_the_counter_wrote_it(
        till, monkeypatch, tasks, cash_leg_paid):
    """D95(5): the insert landed, its answer and the read-back were lost
    (503 'Not confirmed. Do not pay yet'), and the press was never pressed
    again. The record is the counter's: Needs completing never lists it and
    writes nothing on it whatever is answered -- no OWED leg, no owed task,
    its cash netted exactly once. (Before: the cash leg became OWED after
    Day-End had already netted it, and 'Cash owed - now paid' netted it again.)"""
    payload, rcoll, restocks = _cash_press(till, monkeypatch)
    state = _landed_but_unconfirmed(rcoll, monkeypatch)
    r = _press(payload, "K-U")
    assert r.status_code == 503 and r.json()["detail"] == returns_router.RECORD_NOT_CONFIRMED_MESSAGE, r.text
    state["done"] = True
    rid = state["landed"]
    before = rcoll.find_one({"return_id": rid})
    assert before["_id"] == f"ret:{rid}" and before["status"] == "COMPLETED" and restocks == []
    assert before["stock_pending"] is True  # the press stopped before its stock step
    _aged_past_the_timeout(till)
    assert rid not in [i["return_id"] for i in _needs_completing()]
    answer = _complete(rid, cash_leg_paid=cash_leg_paid)
    assert answer["message"] == returns_router.COUNTER_COMPLETED_MESSAGE and answer["replayed"] is True
    stored = rcoll.find_one({"return_id": rid})
    assert stored == before  # nothing written: no recovered stamp, no OWED leg
    assert stored["refund_tenders"][0]["method"] == "CASH" and stored["drawer_auto_netted"] is True
    assert not [t for t in tasks if t.get("dedupe_ref") == f"return_cash_owed:{rid}"], tasks
    with pytest.raises(returns_router.HTTPException) as owed:
        asyncio.run(returns_router.mark_owed_paid(rid, returns_router.OwedPaidBody(
            method="CASH", cash_paid_from="STORE_DRAWER"), current_user=_user("ACCOUNTANT")))
    assert owed.value.status_code == 409  # nothing is owed: no second drawer leg
    assert len(list(rcoll.find({}))) == 1
    # The identical re-press settles it: the counter's own record replays 201,
    # and says its units are not back yet; the record's Restock puts them back once.
    last = _press(payload, "K-U")
    assert last.status_code == 201 and last.json()["return_id"] == rid, last.text
    assert "Update stock" in last.json()["stock_note"] and restocks == []
    first, second = _retry_restock(rid), _retry_restock(rid)
    assert first["restock_applied"] is True and second["message"] == "Already restocked" and restocks == [rid]
    # The bill is not held by it: the line's other unit returns as a new press.
    assert _press(payload, "K-U2").status_code == 201
    assert len(list(rcoll.find({}))) == 2


def test_the_identical_re_press_after_needs_completing_books_nothing(till, monkeypatch, tasks):
    """MF2: the press died after its money-out mark; Accounts answered "cash
    NOT paid". Before: the same Idempotency-Key found no record (Needs
    completing wrote none with the key) and booked the line's next unit."""
    payload, rcoll, restocks = _cash_press(till, monkeypatch)
    real_write, died = returns_router._own_record_or_refuse, []

    async def dies(coll, doc, *a, **k):
        if not died:
            died.append(doc["return_id"])
            raise RuntimeError("the press died")
        return await real_write(coll, doc, *a, **k)

    monkeypatch.setattr(returns_router, "_own_record_or_refuse", dies)
    with pytest.raises(RuntimeError):
        _press(payload, "K-K")
    assert _press(payload, "K-K").status_code == 409  # still settling
    rid = _aged_past_the_timeout(till)
    rec = _complete(rid, cash_leg_paid=False)
    assert rec["status"] == "COMPLETED" and rec["idempotency_key"] == "K-K" and rec["owed_amount"] == 1800.0
    r = _press(payload, "K-K")
    assert r.status_code == 409 and r.json()["detail"] == returns_router.RECORD_TAKEN_MESSAGE, r.text
    assert [d["_id"] for d in rcoll.find({})] == [f"ret:{rid}"] and restocks == []
    # D95(4): the units of a press that stopped are never left without a task and a button.
    assert len([t for t in tasks if t.get("dedupe_ref") == f"return_restock_blocked:{rid}"]) == 1, tasks
    assert rcoll.find_one({"return_id": rid})["stock_pending"] is True
    first, second = _retry_restock(rid), _retry_restock(rid)
    assert first["restock_applied"] is True and second["message"] == "Already restocked" and restocks == [rid]


def test_an_unsaved_blocked_restock_task_is_never_claimed(till, monkeypatch):
    """MF4: create_system_task answers None when its insert fails."""
    payload, rcoll, restocks = _cash_press(till, monkeypatch)

    def unreadable(*a, **k):
        raise AutoReconnect("returns unreadable")

    monkeypatch.setattr(rcoll, "find_one_and_update", unreadable)  # the claim of its stock step
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


def test_a_stock_step_left_open_by_a_stopped_press_gets_one_task(till, monkeypatch, tasks):
    """D95(2): the press stopped after its record landed, before its stock
    step. The record says so (stock_pending); once it is older than any press
    the sweep raises ONE task naming the button -- never again after that."""
    payload, rcoll, restocks = _cash_press(till, monkeypatch)

    async def dies(*a, **k):
        raise RuntimeError("the press died after its record")

    monkeypatch.setattr(returns_router, "_finish_press", dies)
    with pytest.raises(RuntimeError):
        _press(payload, "K-S")
    rec = rcoll.find_one({})
    rid = rec["return_id"]
    assert rec["status"] == "COMPLETED" and rec["stock_pending"] is True and restocks == []
    assert returns_router.sweep_stuck_return_holds() == 0 and tasks == []  # younger than any press
    rcoll.update_one({"return_id": rid}, {"$set": {"created_at": "2026-01-01T00:00:00+00:00"}})
    assert returns_router.sweep_stuck_return_holds() == 1
    assert [t["dedupe_ref"] for t in tasks] == [f"return_restock_blocked:{rid}"]
    assert "Update stock" in tasks[0]["description"]
    assert returns_router.sweep_stuck_return_holds() == 0 and len(tasks) == 1  # one task, ever
    first, second = _retry_restock(rid), _retry_restock(rid)
    assert first["restock_applied"] is True and second["message"] == "Already restocked" and restocks == [rid]


def test_a_stock_step_that_stopped_part_way_is_left_to_a_person(till, monkeypatch, tasks):
    """D92(5), kept: a worker stopped WHILE placing the units (its claim is
    older than any press). What it placed is unknown, so the record's Restock
    adds nothing and says to check each unit -- never a second unit."""
    payload, rcoll, restocks = _cash_press(till, monkeypatch)
    assert _press(payload, "K-M").status_code == 201
    rid = rcoll.find_one({})["return_id"]
    rcoll.update_one({"return_id": rid}, {"$set": {
        "restock_applied": False, "stock_pending": True, "restock_in_progress": True,
        "restock_started_at": "2026-01-01T00:00:00+00:00"}})
    out = _retry_restock(rid)
    assert out["restock_applied"] is False and "Check each unit" in out["message"]
    assert restocks == [rid]  # only the press's own run: nothing was added again
    assert [t["dedupe_ref"] for t in tasks] == [f"return_restock_blocked:{rid}"]
    assert "check each unit" in tasks[0]["description"]
