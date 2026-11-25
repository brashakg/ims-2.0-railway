"""
IMS 2.0 - D95 / D84 (#1397, Claude's reviewers on head d7d0a963): what a
counter return's "press again" and its record promise. NATIVE Mongo.
==========================================================================
Before (each reproduced here on native Mongo, real routes, head d7d0a963):

* a failed read of the Idempotency-Key lookup was treated as "no earlier
  press": the identical re-press of a return whose record had landed wrote a
  SECOND return (second unit back, cash netted twice); two identical presses
  arriving together did the same;
* a DAMAGED unit whose set-aside did not happen was recorded as done: unit
  still SOLD, no note, no task, "Update stock" refused;
* Needs completing treated ITS OWN record as someone else's when only the
  insert's acknowledgement was lost: no audit row, no "Pay customer" task,
  the nothing-paid give-backs skipped;
* a failed audit write answered 201 with no RETURN_CREATED /
  RETURN_RECORD_COMPLETED row, ever.

Same harness and rules as test_d95_one_record_native (real MongoDB, stored
ROWS asserted, no index on returns.return_id). No emoji (cp1252).
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

from pymongo.collection import Collection  # noqa: E402
from pymongo.errors import AutoReconnect  # noqa: E402

from tests.test_d95_one_record_native import (  # noqa: E402,F401 -- `native` is the fixture
    REAL_FIND_ONE,
    REAL_INSERT,
    _age_hold,
    _books,
    _complete,
    _payload,
    _press,
    _restock,
    native,
)
from tests.test_pos_money_doors import _user, returns_router  # noqa: E402

pytestmark = pytest.mark.usefixtures("gst_numbers")


def _task_age():
    """A record older than the D95 task's wait (10 minutes) and younger than
    the 15 minutes after which the hourly sweep STARTS a counter record's step
    nobody started (D130(1)): the task is raised, the step still waits."""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    return (_dt.now(_tz.utc) - _td(minutes=11)).isoformat()


def _once(state, name):
    """True the first time only."""
    if state.get(name):
        return False
    state[name] = True
    return True


def _history(db):
    """The read behind Returns > History."""
    return asyncio.run(returns_router.list_returns(
        store_id=None, return_type=None, skip=0, limit=50, current_user=_user("STORE_MANAGER")))


# --- the identical re-press: an unreadable key lookup, two presses at once ----------------------


def test_an_unreadable_key_lookup_never_becomes_a_second_return(native, monkeypatch):
    """The 201 of a landed return was lost; the identical re-press arrives
    while the key lookup cannot be read. Before: logged and treated as a NEW
    press -- a second COMPLETED return of the 2-unit line (both units back,
    Rs 3,600 netted for one Rs 1,800 refund). Now 503 'press again'; the next
    identical press replays the one return."""
    db, state = native["db"], {}
    first = _press(_payload(), "K-READ")
    assert first.status_code == 201, first.text
    rid = first.json()["return_id"]

    def find_one(self, flt=None, *a, **k):
        if self.name == "returns" and isinstance(flt, dict) and "idempotency_key" in flt and _once(state, "failed"):
            raise AutoReconnect("returns unreadable")
        return REAL_FIND_ONE(self, flt, *a, **k)

    monkeypatch.setattr(Collection, "find_one", find_one)
    again = _press(_payload(), "K-READ")
    assert state.get("failed"), "the key lookup was never read"
    b = _books(db)
    assert b["records"] == [(f"ret:{rid}", "COMPLETED", False)], b
    assert b["units"] == ["AVAILABLE", "SOLD"] and b["cash_netted"] == 1800.0 and b["returned_qty"] == 1
    assert again.status_code == 503 and "Press Return again" in again.json()["detail"], again.text
    replay = _press(_payload(), "K-READ")
    assert replay.status_code == 201 and replay.json()["return_id"] == rid, replay.text
    assert _books(db) == b


def test_two_identical_presses_at_once_record_one_return(native, monkeypatch):
    """Two requests of ONE press (same Idempotency-Key) both read 'no record
    with this key' before either has reserved. Before: each reserved, fenced
    its own hold and claimed one unit -- two COMPLETED returns. Now the key is
    claimed once, on the bill's hold: one records the return, the other is
    told to press again and then replays it."""
    db = native["db"]
    gate, real = threading.Barrier(2), returns_router._refuse_while_a_return_settles

    def both_passed_their_checks(*a, **k):
        real(*a, **k)
        try:
            gate.wait(10)
        except threading.BrokenBarrierError:
            pass

    monkeypatch.setattr(returns_router, "_refuse_while_a_return_settles", both_passed_their_checks)
    answers = []
    threads = [threading.Thread(target=lambda: answers.append(_press(_payload(), "K-TWO"))) for _ in range(2)]
    [t.start() for t in threads]
    [t.join(120) for t in threads]
    b = _books(db)
    assert len(b["records"]) == 1 and b["units"] == ["AVAILABLE", "SOLD"], (b, [a.text for a in answers])
    assert b["cash_netted"] == 1800.0 and b["returned_qty"] == 1 and b["unit_rows"] == 2
    assert sorted(a.status_code for a in answers) == [201, 409], [a.text for a in answers]
    rid = [a for a in answers if a.status_code == 201][0].json()["return_id"]
    assert "Nothing was recorded" in [a for a in answers if a.status_code == 409][0].json()["detail"]
    replay = _press(_payload(), "K-TWO")
    assert replay.status_code == 201 and replay.json()["return_id"] == rid, replay.text
    assert _books(db) == b
    # A different return of the same bill is not this press: it is taken as usual.
    other = _press(_payload(), "K-OTHER")
    assert other.status_code == 201 and other.json()["return_id"] != rid, other.text
    assert _books(db)["units"] == ["AVAILABLE", "AVAILABLE"]


def test_a_press_that_reserved_after_its_twin_recorded_replays_it(native, monkeypatch):
    """The other order of the same race: this request read 'no record', then
    its twin recorded the return and its hold stopped counting, then this one
    reserved. The key is read AGAIN under the reservation: a replay, its own
    hold given back."""
    db, state = native["db"], {}
    real = returns_router._refuse_while_a_return_settles

    def twin_finishes_first(*a, **k):
        real(*a, **k)
        if _once(state, "twin"):
            state["first"] = _press(_payload(), "K-LATE")

    monkeypatch.setattr(returns_router, "_refuse_while_a_return_settles", twin_finishes_first)
    late = _press(_payload(), "K-LATE")
    first = state["first"]
    assert first.status_code == 201 and late.status_code == 201, (first.text, late.text)
    assert late.json()["return_id"] == first.json()["return_id"] and late.json().get("_idempotent_replay") is True
    b = _books(db)
    assert len(b["records"]) == 1 and b["units"] == ["AVAILABLE", "SOLD"] and b["cash_netted"] == 1800.0
    holds = db["orders"].find_one({"order_id": "ORD-1"}).get("refund_holds") or {}
    assert [k for k in holds if k != first.json()["return_id"]] == []  # the late press left no hold of its own


# --- D95(2): a DAMAGED unit that was not set aside keeps the stock step open ---------------------


def _stock_unreadable_once(monkeypatch, state):
    real = returns_router._find_stock

    def find(stock_repo, query):
        # The read of the bill's SOLD unit, before a unit is put back or set aside.
        if query.get("status") == "SOLD" and _once(state, "stock_failed"):
            raise AutoReconnect("stock unreadable")
        return real(stock_repo, query)

    monkeypatch.setattr(returns_router, "_find_stock", find)


def test_a_damaged_unit_that_was_not_set_aside_keeps_the_stock_step_open(native, monkeypatch):
    """One failed stock read while the press sets its DAMAGED unit aside. Before:
    201 with nothing said, the record 'restock applied, nothing pending', the
    unit SOLD for good ('Update stock' answered Already restocked). Now the
    step stays open on the record: the till says so, History shows 'Update
    stock', the sweep raises its task, and the button sets the unit aside once."""
    db, state = native["db"], {}
    _stock_unreadable_once(monkeypatch, state)
    r = _press(_payload(condition="DAMAGED"))
    assert r.status_code == 201 and state.get("stock_failed"), r.text
    rid = r.json()["return_id"]
    rec = db["returns"].find_one({"_id": f"ret:{rid}"})
    assert _books(db)["units"] == ["SOLD", "SOLD"] and rec["quarantined_stock_ids"] == []
    assert rec["stock_pending"] is True, rec  # the step is NOT recorded as done
    assert "Update stock" in (r.json().get("stock_note") or ""), r.json()
    assert [x["return_id"] for x in _history(db)["returns"] if x.get("stock_pending")] == [rid]
    db["returns"].update_one({"return_id": rid}, {"$set": {"created_at": "2026-01-01T00:00:00+00:00",
                                                          "restock_started_at": "2026-01-01T00:00:00+00:00"}})
    assert returns_router.sweep_stuck_return_holds() == 1 and _books(db)["restock_tasks"] == ["OPEN"]
    done = _restock(rid)
    b = _books(db)
    assert b["units"] == ["QUARANTINED", "SOLD"] and b["unit_rows"] == 2 and b["restock_tasks"] == ["COMPLETED"], (done, b)
    rec = db["returns"].find_one({"_id": f"ret:{rid}"})
    assert rec["stock_pending"] is False and len(rec["quarantined_stock_ids"]) == 1
    aside = db["stock_units"].find_one({"status": "QUARANTINED"})
    assert rec["quarantined_stock_ids"] == [aside["_id"]] and aside["return_slot"] == f"{rid}|aside|P1|0"
    assert _restock(rid)["message"] == "Already restocked" and _books(db)["units"] == ["QUARANTINED", "SOLD"]


def test_update_stock_that_could_not_set_the_damaged_unit_aside_stays_open(native, monkeypatch):
    """The same single fault inside the record's own 'Update stock': before,
    it stamped the step done and closed the task with the unit still SOLD."""
    db, state = native["db"], {}
    real_finish = returns_router._finish_press

    async def dies_once(*a, **k):
        if _once(state, "died"):
            raise RuntimeError("the press stopped after its record")
        return await real_finish(*a, **k)

    monkeypatch.setattr(returns_router, "_finish_press", dies_once)
    with pytest.raises(RuntimeError):
        _press(_payload(condition="DAMAGED"))
    rid = db["returns"].find_one({})["return_id"]
    db["returns"].update_one({"return_id": rid}, {"$set": {"created_at": _task_age()}})
    assert returns_router.sweep_stuck_return_holds() == 1
    _stock_unreadable_once(monkeypatch, state)
    first = _restock(rid)
    rec = db["returns"].find_one({"return_id": rid})
    b = _books(db)
    assert b["units"] == ["SOLD", "SOLD"] and rec["stock_pending"] is True, (first, rec)
    assert b["restock_tasks"] == ["OPEN"] and "Already restocked" not in first["message"], (first, b)
    second = _restock(rid)
    b = _books(db)
    assert b["units"] == ["QUARANTINED", "SOLD"] and b["restock_tasks"] == ["COMPLETED"], (second, b)
    assert db["returns"].find_one({"return_id": rid})["stock_pending"] is False
    assert _restock(rid)["message"] == "Already restocked" and _books(db)["unit_rows"] == 2


# --- D84: RETURN_CREATED is written before the press answers 201 ---------------------------------


def _audit_fails_once(monkeypatch, state, action):
    def insert(self, doc, *a, **k):
        if self.name == "audit_logs" and isinstance(doc, dict) and doc.get("action") == action \
                and _once(state, "audit_failed"):
            raise AutoReconnect("audit trail unreachable")
        return REAL_INSERT(self, doc, *a, **k)

    monkeypatch.setattr(Collection, "insert_one", insert)


def test_a_press_whose_audit_row_could_not_be_written_says_press_again(native, monkeypatch):
    """Before: 201 (pay) with no RETURN_CREATED row, and the done screen never
    presses again. Now the press answers 'Not confirmed. Do not pay yet. Press
    Return again'; the identical re-press writes the row once and answers 201."""
    db, state = native["db"], {}
    _audit_fails_once(monkeypatch, state, "RETURN_CREATED")
    r = _press(_payload(), "K-AUDIT")
    assert state.get("audit_failed"), "the audit insert never ran"
    assert r.status_code == 503 and r.json()["detail"] == returns_router.RECORD_NOT_CONFIRMED_MESSAGE, r.text
    b = _books(db)
    assert len(b["records"]) == 1 and b["audits"] == [] and b["units"] == ["AVAILABLE", "SOLD"]
    rid = db["returns"].find_one({})["return_id"]
    for _ in range(2):
        again = _press(_payload(), "K-AUDIT")
        assert again.status_code == 201 and again.json()["return_id"] == rid, again.text
    rows = list(db["audit_logs"].find({"action": "RETURN_CREATED"}))
    assert [x["_id"] for x in rows] == [f"return_created:{rid}"], rows
    b = _books(db)
    assert len(b["records"]) == 1 and b["units"] == ["AVAILABLE", "SOLD"] and b["cash_netted"] == 1800.0


def test_a_keyless_press_without_its_audit_row_is_repaired_by_reading_history(native, monkeypatch):
    """A press with no Idempotency-Key cannot be pressed again (it would be a
    new return), so D84's other arm applies: reading the record repairs it."""
    db, state = native["db"], {}
    _audit_fails_once(monkeypatch, state, "RETURN_CREATED")
    r = _press(_payload())
    assert r.status_code == 201 and state.get("audit_failed"), r.text
    rid = r.json()["return_id"]
    assert _books(db)["audits"] == []
    for _ in range(2):
        assert [x["return_id"] for x in _history(db)["returns"]] == [rid]
    rows = list(db["audit_logs"].find({"action": "RETURN_CREATED"}))
    assert [x["_id"] for x in rows] == [f"return_created:{rid}"], rows
    assert db["returns"].find_one({"return_id": rid})["audited"] is True


# --- Needs completing knows its own record (a lost acknowledgement) ------------------------------


def _press_stops_before_its_record(monkeypatch, state):
    real = returns_router._own_record_or_refuse

    async def stops_once(*a, **k):
        if _once(state, "stopped"):
            raise RuntimeError("the press stopped before its record")
        return await real(*a, **k)

    monkeypatch.setattr(returns_router, "_own_record_or_refuse", stops_once)


def _recovered_lands_unacknowledged(monkeypatch, state):
    def insert(self, doc, *a, **k):
        if self.name == "returns" and isinstance(doc, dict) and doc.get("recovered") and _once(state, "lost_ack"):
            REAL_INSERT(self, doc, *a, **k)
            raise AutoReconnect("insert landed, acknowledgement lost")
        return REAL_INSERT(self, doc, *a, **k)

    monkeypatch.setattr(Collection, "insert_one", insert)


def test_cash_not_paid_whose_insert_answer_was_lost_still_raises_its_task_and_audit(native, monkeypatch):
    """Accounts answer 'cash NOT paid'; the record lands, only its
    acknowledgement is lost. Before: read back as someone else's record --
    'already exists', no 'Pay customer' task, no audit row, and the return no
    longer listed. Now the writer knows its own record and finishes its steps."""
    db, state = native["db"], {}
    _press_stops_before_its_record(monkeypatch, state)
    with pytest.raises(RuntimeError):
        _press(_payload())
    rid = _age_hold(db)
    _recovered_lands_unacknowledged(monkeypatch, state)
    out = _complete(rid, cash_leg_paid=False)
    assert state.get("lost_ack"), "the record insert never ran"
    b = _books(db)
    assert b["records"] == [(f"ret:{rid}", "COMPLETED", True)] and b["cash_netted"] == 0, b
    assert b["owed_tasks"] == 1 and b["audits"] == ["RETURN_RECORD_COMPLETED"], (out.get("message"), b)
    assert not out.get("replayed") and "owed Rs 1800.00" in out["message"], out
    assert b["restock_tasks"] == ["OPEN"]
    again = _complete(rid, cash_leg_paid=False)
    assert again["replayed"] is True and _books(db) == b
    row = db["audit_logs"].find_one({"action": "RETURN_RECORD_COMPLETED"})
    assert row["_id"] == f"return_completed:{rid}" and row["details"]["cash_leg_paid"] is False


def test_a_completion_whose_audit_row_failed_is_written_by_the_next_press(native, monkeypatch):
    """D84 for Needs completing: the audit row could not be written -> an
    error that says press again; the next press (the record is there now)
    writes it once. Before: 201 and no row, ever."""
    db, state = native["db"], {}
    _press_stops_before_its_record(monkeypatch, state)
    with pytest.raises(RuntimeError):
        _press(_payload())
    rid = _age_hold(db)
    _audit_fails_once(monkeypatch, state, "RETURN_RECORD_COMPLETED")
    with pytest.raises(returns_router.HTTPException) as failed:
        _complete(rid, cash_leg_paid=True)
    assert failed.value.status_code == 503 and "again" in failed.value.detail, failed.value.detail
    b = _books(db)
    assert b["records"] == [(f"ret:{rid}", "COMPLETED", True)] and b["audits"] == [] and b["cash_netted"] == 1800.0
    for _ in range(2):
        assert _complete(rid, cash_leg_paid=True)["replayed"] is True
    assert [x["_id"] for x in db["audit_logs"].find({"action": "RETURN_RECORD_COMPLETED"})] == [f"return_completed:{rid}"]
    assert _books(db)["cash_netted"] == 1800.0 and len(_books(db)["records"]) == 1


def test_nothing_was_paid_whose_insert_answer_was_lost_still_gives_everything_back(native, monkeypatch):
    """The same lost acknowledgement on 'Nothing was paid': before, its own
    tombstone was answered as 'already exists' and the give-backs (the unit
    claim, the bill's hold) and the audit row were skipped."""
    db, state = native["db"], {}
    real_check, real_give = returns_router._refuse_if_advance_refunded, returns_router._give_back

    def stops_after_its_claim(order):
        state["checks"] = state.get("checks", 0) + 1
        if state["checks"] == 2:  # the read right after the unit claim
            raise RuntimeError("the press stopped after its claim")
        return real_check(order)

    def give_back_fails_once(*a, **k):
        if _once(state, "give_back"):
            return False
        return real_give(*a, **k)

    monkeypatch.setattr(returns_router, "_refuse_if_advance_refunded", stops_after_its_claim)
    monkeypatch.setattr(returns_router, "_give_back", give_back_fails_once)
    with pytest.raises(RuntimeError):
        _press(_payload())
    assert _books(db)["returned_qty"] == 1 and _books(db)["records"] == []
    rid = _age_hold(db)
    _recovered_lands_unacknowledged(monkeypatch, state)
    out = _complete(rid, nothing_paid=True)
    assert state.get("lost_ack"), "the tombstone insert never ran"
    b = _books(db)
    assert b["records"] == [(f"ret:{rid}", "NOTHING_PAID", True)] and b["cash_netted"] == 0
    assert b["returned_qty"] == 0 and b["units"] == ["SOLD", "SOLD"], (out.get("message"), b)
    assert not out.get("replayed") and "Nothing was paid" in out["message"], out
    assert rid not in (db["orders"].find_one({"order_id": "ORD-1"}).get("refund_holds") or {})
    assert b["audits"] == ["RETURN_RECORD_COMPLETED"]
    # The bill is free again: the customer's return is taken as usual.
    assert _press(_payload()).status_code == 201


def test_a_stock_step_that_stopped_part_way_is_finished_by_update_stock(native, monkeypatch):
    """D122(3): a worker stopped WHILE placing the units (its claim is
    stale). 'Update stock' takes the claim over and finishes the step on the
    same return slots -- the damaged unit is set aside, once; nobody is asked
    to check or add a unit by hand."""
    db, state = native["db"], {}
    real_finish = returns_router._finish_press

    async def dies_once(*a, **k):
        if _once(state, "died"):
            raise RuntimeError("the press stopped after its record")
        return await real_finish(*a, **k)

    monkeypatch.setattr(returns_router, "_finish_press", dies_once)
    with pytest.raises(RuntimeError):
        _press(_payload(condition="DAMAGED"))
    rid = db["returns"].find_one({})["return_id"]
    db["returns"].update_one({"return_id": rid}, {"$set": {
        "restock_in_progress": True, "restock_started_at": "2026-01-01T00:00:00+00:00"}})
    out = _restock(rid)
    assert out["restock_applied"] is True and "by hand" not in out["message"], out
    for _ in range(2):
        b = _books(db)
        assert b["units"] == ["QUARANTINED", "SOLD"] and b["unit_rows"] == 2 and b["restock_tasks"] == [], b
        assert _restock(rid)["message"] == "Already restocked"
    rec = db["returns"].find_one({"return_id": rid})
    assert len(rec["quarantined_stock_ids"]) == 1
    assert (rec["restock_applied"], rec["stock_pending"], rec["restock_in_progress"]) == (True, False, False)
