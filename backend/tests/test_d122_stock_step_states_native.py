"""
IMS 2.0 - #1397 + #1413 (D122, D123): the stock step of a return's record
is fenced on the stock row and finished by IMS. NATIVE Mongo, real routes.
==========================================================================
The state table is in returns.py (above _claim_restock). This file holds
its rows that a timed claim on the returns record could not close (Claude's
reviewers on head c010f935, Codex round 7's class "an expired hold whose
owner is still alive"):

  S2  the closing stamp LANDED and its reply was lost     -> reads as closed
  S2  the closing stamp never landed                      -> claim given back,
                                                             'Update stock' finishes
  S3  stopped part way (worker died / claim aged)         -> 'Update stock', or
                                                             the hourly sweep,
                                                             re-runs the same slots
  the task of an open step closed by hand (D122(5), D109(3))
  D123 cash owed: every reader settles a stored payout

Before (reproduced on head c010f935, native Mongo, the same sequences):

* a stamp that landed with its reply lost read as "the claim was lost": the
  till said 'stopped part way: check each unit by hand ... add one only if it
  is not in stock' beside a record IMS still finished by itself -- and with a
  unit still out, the cashier's hand entry plus the manager's Update stock
  made two AVAILABLE rows for one piece;
* a stopped step's only finisher was a person told to add a unit by hand;
* a payout stored behind an abandoned 503 left the return on the owed list
  with its 'Pay customer' task open.

Every test asserts the stored stock ROWS (never call counts). Same harness
as test_d95_one_record_native. No emoji (cp1252).
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

from fastapi import HTTPException  # noqa: E402
from pymongo.collection import Collection  # noqa: E402
from pymongo.errors import AutoReconnect  # noqa: E402

from tests.test_d95_one_record_native import (  # noqa: E402,F401 -- `native` is the fixture
    _age_hold,
    _complete,
    _payload,
    _press,
    _restock,
    native,
)
from tests.test_d95_press_again_native import _once  # noqa: E402
from tests.test_d95_stock_step_states_native import (  # noqa: E402
    OLD,
    _complete_task,
    _marks,
    _mixed,
    _rec,
    _row,
    _rows,
    _stopped_record,
)
from tests.test_pos_money_doors import _user, returns_router  # noqa: E402

pytestmark = pytest.mark.usefixtures("gst_numbers")

REAL_UPDATE = Collection.update_one
DONE = {"p1": ["AVAILABLE", "SOLD"], "p2": "QUARANTINED", "rows": 3}
BY_HAND = ("check each unit", "add one by hand", "add one only", "stopped part way")


def _never_by_hand(*texts):
    for text in texts:
        low = str(text or "").lower()
        assert not [w for w in BY_HAND if w in low], text


def _closing_stamp(change):
    return "restock_completed_at" in ((change or {}).get("$set") or {})


def _stamp_reply_lost(monkeypatch, state, times=1):
    """The closing stamp of a stock step is APPLIED by Mongo and its reply is
    lost (a standalone Mongo does not retry writes)."""
    def update(self, flt, change, *a, **k):
        if self.name == "returns" and _closing_stamp(change) and state.get("lost", 0) < times:
            state["lost"] = state.get("lost", 0) + 1
            REAL_UPDATE(self, flt, change, *a, **k)
            raise AutoReconnect("the stamp landed, its reply was lost")
        return REAL_UPDATE(self, flt, change, *a, **k)

    monkeypatch.setattr(Collection, "update_one", update)


def _stamp_never_lands(monkeypatch, state, release_too=False):
    """Neither try of the closing stamp reaches Mongo; with ``release_too``
    the write that gives the claim back is lost as well."""
    def update(self, flt, change, *a, **k):
        sets = (change or {}).get("$set") or {}
        if self.name == "returns" and _closing_stamp(change) and state.get("lost", 0) < 2:
            state["lost"] = state.get("lost", 0) + 1
            raise AutoReconnect("the stamp never reached the database")
        if release_too and self.name == "returns" and set(sets) == {"restock_in_progress"} \
                and state.get("released", 0) < 2:
            state["released"] = state.get("released", 0) + 1
            raise AutoReconnect("the release never reached the database")
        return REAL_UPDATE(self, flt, change, *a, **k)

    monkeypatch.setattr(Collection, "update_one", update)


def _p2_write_fails_once(monkeypatch, state):
    real = returns_router._reactivate_original_unit

    def flip(stock_repo, product_id, *a, **k):
        if product_id == "P2" and _once(state, "p2_failed"):
            raise AutoReconnect("stock write failed")
        return real(stock_repo, product_id, *a, **k)

    monkeypatch.setattr(returns_router, "_reactivate_original_unit", flip)


def _two_good(db):
    body = _mixed(db)
    body["items"][1].update(condition="GOOD", restock=True)
    return body


def _slots(db, rid):
    return sorted(u["return_slot"] for u in db["stock_units"].find({"return_slot": {"$regex": f"^{rid}\\|"}}))


def _restock_tasks(db):
    return sorted((t["status"], t["description"]) for t in db["tasks"].find(
        {"source_ref": {"$regex": "^return_restock_blocked:"}}))


def _worker_died_holding_the_claim(db, rid):
    """S3 as a dead worker leaves it: the claim on the record, older than a
    worker runs, and NO closing stamp -- whatever it placed is on the stock
    rows only (their return slots)."""
    db["returns"].update_one({"return_id": rid}, {"$set": {
        "restock_applied": False, "stock_pending": True, "restock_in_progress": True, "restock_claim": "dead-worker",
        "restock_started_at": OLD, "created_at": OLD, "restocked": [], "restock_stock_ids": [],
        "quarantined_stock_ids": []}})


# --- S2: the closing stamp landed, its reply was lost (must-fix 1; D122(4)) ----------------------


def test_s2_a_stamp_that_landed_with_its_reply_lost_reads_as_done(native, monkeypatch):
    """Sequence A of the review: everything is placed, the stamp lands, the
    reply is lost. Before: 201 said 'IMS ... stopped part way: check each
    unit by hand' beside restock_applied true. Now the answer is the stored
    record's: done, no note, no task."""
    db, state = native["db"], {}
    _stamp_reply_lost(monkeypatch, state)
    r = _press(_mixed(db))
    assert r.status_code == 201 and state.get("lost") == 1, (r.text, state)
    rid = r.json()["return_id"]
    assert r.json()["restock_applied"] is True and r.json()["stock_note"] == "", r.json()
    _never_by_hand(r.json()["message"], r.json()["stock_note"])
    assert _marks(db, rid) == (True, False, False) and _rows(db) == DONE
    assert _restock_tasks(db) == [] and not _row(db, rid).get("stock_pending")
    assert _slots(db, rid) == [f"{rid}|aside|P2|0", f"{rid}|back|P1|0"]
    assert _restock(rid)["message"] == "Already restocked" and _rows(db) == DONE


def test_s2_a_partial_stamp_that_landed_with_its_reply_lost_never_doubles_a_unit(native, monkeypatch):
    """Sequence B of the review (stock doubled): two GOOD lines, the second
    unit's write fails once, the partial stamp lands, its reply is lost.
    Before: the till said 'add one by hand if it is not in stock' and the
    task said 'press Update stock': both followed, P2 was two AVAILABLE rows.
    Now the till names ONE action (Update stock, never a hand entry), the
    record reads open, and Update stock puts P2 back once."""
    db, state = native["db"], {}
    _p2_write_fails_once(monkeypatch, state)
    _stamp_reply_lost(monkeypatch, state)
    r = _press(_two_good(db))
    assert r.status_code == 201 and state.get("p2_failed") and state.get("lost") == 1, (r.text, state)
    rid = r.json()["return_id"]
    assert r.json()["restock_applied"] is False and r.json()["stock_note"] == returns_router.STOCK_PENDING_NOTE
    _never_by_hand(r.json()["message"])
    assert _marks(db, rid) == (False, True, False)
    assert _rows(db) == {"p1": ["AVAILABLE", "SOLD"], "p2": "SOLD", "rows": 3}
    assert _row(db, rid)["stock_pending"] is True and "stock_step_stopped" not in _row(db, rid)
    out = _restock(rid)
    assert out["restock_applied"] is True, out
    assert _rows(db) == {"p1": ["AVAILABLE", "SOLD"], "p2": "AVAILABLE", "rows": 3}
    assert _slots(db, rid) == [f"{rid}|back|P1|0", f"{rid}|back|P2|0"]
    rec = _rec(db, rid)
    assert _marks(db, rid) == (True, False, False) and len(rec["restock_stock_ids"]) == 2
    assert _restock(rid)["message"] == "Already restocked"
    assert _rows(db) == {"p1": ["AVAILABLE", "SOLD"], "p2": "AVAILABLE", "rows": 3}


def test_s2_update_stock_whose_stamp_landed_with_its_reply_lost_answers_what_is_stored(native, monkeypatch):
    """The same loss inside 'Update stock' itself."""
    db, state = native["db"], {}
    rid = _stopped_record(db, monkeypatch, _mixed(db))
    _stamp_reply_lost(monkeypatch, state)
    out = _restock(rid)
    assert state.get("lost") == 1 and out["restock_applied"] is True and out["message"] == "Restock applied", out
    assert _marks(db, rid) == (True, False, False) and _rows(db) == DONE


# --- S2: the closing stamp never landed ------------------------------------------------------------


def test_s2_a_stamp_that_never_landed_gives_the_claim_back_and_update_stock_finishes(native, monkeypatch):
    """Both tries of the stamp fail. The units ARE on the shelf (their rows
    carry this return's slots); the record still reads open. The claim is
    given back, the till names Update stock, and Update stock credits what
    is back -- no second unit, no mint."""
    db, state = native["db"], {}
    _stamp_never_lands(monkeypatch, state)
    r = _press(_mixed(db))
    assert r.status_code == 201 and state.get("lost") == 2, (r.text, state)
    rid = r.json()["return_id"]
    assert _rows(db) == DONE and _marks(db, rid) == (False, True, False)
    assert r.json()["restock_applied"] is False and r.json()["stock_note"] == returns_router.STOCK_PENDING_NOTE
    out = _restock(rid)
    assert out["restock_applied"] is True, out
    assert _rows(db) == DONE and _marks(db, rid) == (True, False, False)
    rec = _rec(db, rid)
    assert rec["restock_stock_ids"] and rec["quarantined_stock_ids"] == ["U3"], rec
    assert _slots(db, rid) == [f"{rid}|aside|P2|0", f"{rid}|back|P1|0"]


def test_s3_a_stamp_and_a_release_that_never_landed_age_into_a_stopped_step_ims_finishes(native, monkeypatch):
    """The stamp AND the release are lost: the record keeps the claim. While
    it is young the step reads as running; once it is older than a worker
    runs, 'Update stock' takes it over and finishes it (S3)."""
    db, state = native["db"], {}
    _stamp_never_lands(monkeypatch, state, release_too=True)
    r = _press(_mixed(db))
    assert r.status_code == 201 and state.get("lost") == 2 and state.get("released"), (r.text, state)
    rid = r.json()["return_id"]
    assert _rows(db) == DONE and _marks(db, rid) == (False, True, True)
    assert r.json()["stock_note"] == returns_router.STOCK_RUNNING_NOTE
    _never_by_hand(r.json()["message"])
    assert _restock(rid)["message"] == "Restock in progress - retry shortly" and _rows(db) == DONE
    db["returns"].update_one({"return_id": rid}, {"$set": {"restock_started_at": OLD}})
    out = _restock(rid)
    assert out["restock_applied"] is True, out
    assert _rows(db) == DONE and _marks(db, rid) == (True, False, False)


# --- S3: stopped part way -- the finisher is IMS (must-fix 2; D122(3)) -----------------------------


@pytest.mark.parametrize("placed", ["nothing", "one of two", "everything"])
def test_s3_a_worker_that_died_holding_the_claim_is_finished_by_update_stock(native, monkeypatch, placed):
    """The worker died after it placed nothing / the first of two units /
    every unit, and never stamped. 'Update stock' takes the stale claim over
    and runs the same slots again: each piece ends as ONE stock row."""
    db, state = native["db"], {}
    if placed == "nothing":
        rid = _stopped_record(db, monkeypatch, _two_good(db))
    else:
        if placed == "one of two":
            _p2_write_fails_once(monkeypatch, state)
        rid = _press(_two_good(db)).json()["return_id"]
    _worker_died_holding_the_claim(db, rid)
    out = _restock(rid)
    assert out["restock_applied"] is True, out
    _never_by_hand(out["message"], out.get("stock_note"))
    assert _rows(db) == {"p1": ["AVAILABLE", "SOLD"], "p2": "AVAILABLE", "rows": 3}
    assert _slots(db, rid) == [f"{rid}|back|P1|0", f"{rid}|back|P2|0"]
    assert _marks(db, rid) == (True, False, False) and _restock_tasks(db) == []
    assert _restock(rid)["message"] == "Already restocked"
    assert _rows(db) == {"p1": ["AVAILABLE", "SOLD"], "p2": "AVAILABLE", "rows": 3}


def test_s3_the_sweep_finishes_a_stopped_step_nobody_presses(native, monkeypatch):
    """The same dead worker, and nobody presses anything: the hourly sweep
    finishes the step by itself. No task asks a person to check or add a
    unit; a second sweep finds nothing to do."""
    db, state = native["db"], {}
    _p2_write_fails_once(monkeypatch, state)
    rid = _press(_mixed(db)).json()["return_id"]  # P1 back, the damaged P2 unit still SOLD
    _worker_died_holding_the_claim(db, rid)
    assert _rows(db) == {"p1": ["AVAILABLE", "SOLD"], "p2": "SOLD", "rows": 3}
    assert returns_router.finish_stopped_stock_steps() == 1
    assert _rows(db) == DONE and _marks(db, rid) == (True, False, False)
    rec = _rec(db, rid)
    assert rec["quarantined_stock_ids"] == ["U3"] and len(rec["restock_stock_ids"]) == 1
    for _, description in _restock_tasks(db):
        _never_by_hand(description)
    assert returns_router.finish_stopped_stock_steps() == 0 and returns_router.sweep_stuck_return_holds() == 0
    assert _rows(db) == DONE


def test_s3_the_sweep_leaves_a_live_claim_alone(native, monkeypatch):
    db = native["db"]
    rid = _stopped_record(db, monkeypatch, _mixed(db))
    held = returns_router._claim_restock(db["returns"], rid)  # a worker, running right now
    assert held["restock_in_progress"] is True and held["restock_claim"]
    assert returns_router.finish_stopped_stock_steps() == 0
    assert _rows(db) == {"p1": ["SOLD", "SOLD"], "p2": "SOLD", "rows": 3}
    assert _rec(db, rid)["restock_claim"] == held["restock_claim"]


# --- the task of an open step, closed by hand (D122(5), D109(3)) ---------------------------------


def test_closing_the_task_of_an_open_step_is_refused_once_and_never_marks_the_step_done(native, monkeypatch):
    """Before (D109(2)): closing the task of a stopped step recorded 'checked
    by hand' and cleared the label with the unit possibly still out -- or
    after a hand-added row. Now the first close is refused (409) and the task
    names the button; a second close is recorded on the return (who, when)
    but the step stays open, with its label and its button, until IMS has
    placed the units."""
    db = native["db"]
    rid = _stopped_record(db, monkeypatch, _mixed(db))
    db["returns"].update_one({"return_id": rid}, {"$set": {"created_at": OLD}})
    assert returns_router.sweep_stuck_return_holds() == 1
    with pytest.raises(HTTPException) as err:
        _complete_task(db, monkeypatch, rid)
    assert err.value.status_code == 409 and err.value.detail == returns_router.TASK_UNITS_STILL_OUT_MESSAGE
    task = db["tasks"].find_one({"source_ref": f"return_restock_blocked:{rid}"})
    assert task["status"] == "OPEN" and task["payload"]["close_refused"] is True
    assert "Update stock" in task["description"]
    _never_by_hand(task["title"], task["description"], err.value.detail)
    assert "stock_task_closed_by" not in _rec(db, rid)
    assert _rows(db) == {"p1": ["SOLD", "SOLD"], "p2": "SOLD", "rows": 3}
    closed = _complete_task(db, monkeypatch, rid)
    assert closed["status"] == "COMPLETED"
    rec = _rec(db, rid)
    assert rec["stock_task_closed_by"] == "MGR-7" and rec["stock_task_closed_at"]
    assert _marks(db, rid) == (False, True, None) and _row(db, rid)["stock_pending"] is True  # still open
    assert "stock_checked_at" not in rec
    assert _restock(rid)["restock_applied"] is True and _rows(db) == DONE


# --- D123: cash owed, the payout stored behind an abandoned 503 -------------------------------------


def _owed_return_with_a_stored_payout(db, monkeypatch, state):
    """A return whose cash part is owed; 'Cash owed - now paid' stores its
    payout row and answers 503 (its mark on the return is lost). Nobody
    presses again."""
    real_own = returns_router._own_record_or_refuse

    async def stops_once(*a, **k):
        if _once(state, "stopped"):
            raise RuntimeError("the press stopped before its record")
        return await real_own(*a, **k)

    monkeypatch.setattr(returns_router, "_own_record_or_refuse", stops_once)
    with pytest.raises(RuntimeError):
        _press(_payload())
    rid = _age_hold(db)
    assert _complete(rid, cash_leg_paid=False)["owed_amount"] == 1800.0

    def update(self, flt, change, *a, **k):
        if self.name == "returns" and "owed_paid_at" in ((change or {}).get("$set") or {}) \
                and _once(state, "mark_lost"):
            raise AutoReconnect("the mark on the return was lost")
        return REAL_UPDATE(self, flt, change, *a, **k)

    monkeypatch.setattr(Collection, "update_one", update)
    with pytest.raises(HTTPException) as err:
        asyncio.run(returns_router.mark_owed_paid(
            rid, returns_router.OwedPaidBody(method="CASH", cash_paid_from="STORE_DRAWER"), _user("ACCOUNTANT")))
    assert err.value.status_code == 503 and state.get("mark_lost")
    assert db["returns"].count_documents({"payout_of": rid}) == 1 and _rec(db, rid).get("owed_paid_at") is None
    assert db["tasks"].count_documents({"source_ref": f"return_cash_owed:{rid}", "status": "OPEN"}) == 1
    return rid


def _owed_list():
    return [x["return_id"] for x in asyncio.run(returns_router.needs_completing(_user("ACCOUNTANT")))["owed"]]


def _settled(db, rid):
    rec = _rec(db, rid)
    assert rec["owed_paid_at"] and rec["owed_paid_method"] == "CASH" and rec["owed_paid_from"] == "STORE_DRAWER"
    assert db["tasks"].count_documents({"source_ref": f"return_cash_owed:{rid}", "status": "OPEN"}) == 0
    assert db["audit_logs"].count_documents({"_id": f"return_owed_paid:{rid}"}) == 1
    assert db["returns"].count_documents({"payout_of": rid}) == 1  # booked once: one payout, netted once
    assert _owed_list() == []


@pytest.mark.parametrize("reader", ["owed list", "sweep", "task close"])
def test_d123_a_stored_payout_settles_the_return_whoever_reads_it(native, monkeypatch, reader):
    """Before: the payout was booked (it nets the drawer) but the return
    stayed on the owed list with its 'Pay customer' task open -- a second
    person could hand the cash over again. Now every reader settles it."""
    db, state = native["db"], {}
    rid = _owed_return_with_a_stored_payout(db, monkeypatch, state)
    if reader == "owed list":
        assert _owed_list() == []
    elif reader == "sweep":
        assert returns_router.settle_paid_owed_returns() == 1
    else:
        from api.routers import tasks as tasks_router
        from database.repositories.task_repository import TaskRepository

        monkeypatch.setattr(tasks_router, "get_task_repository", lambda: TaskRepository(db["tasks"]))
        task = db["tasks"].find_one({"source_ref": f"return_cash_owed:{rid}"})
        done = asyncio.run(tasks_router.complete_task(
            task["task_id"], tasks_router.TaskComplete(completion_notes="paid"), _user("STORE_MANAGER")))
        assert done["status"] == "COMPLETED"
    _settled(db, rid)
    # The re-press, if it ever comes, replays the stored payment and books nothing more.
    again = asyncio.run(returns_router.mark_owed_paid(
        rid, returns_router.OwedPaidBody(method="UPI"), _user("ACCOUNTANT")))
    assert again["replayed"] is True and again["method"] == "CASH"
    _settled(db, rid)


def test_d123_a_return_with_no_payout_row_stays_owed(native, monkeypatch):
    db, state = native["db"], {}
    real_own = returns_router._own_record_or_refuse

    async def stops_once(*a, **k):
        if _once(state, "stopped"):
            raise RuntimeError("the press stopped before its record")
        return await real_own(*a, **k)

    monkeypatch.setattr(returns_router, "_own_record_or_refuse", stops_once)
    with pytest.raises(RuntimeError):
        _press(_payload())
    rid = _age_hold(db)
    _complete(rid, cash_leg_paid=False)
    assert returns_router.settle_paid_owed_returns() == 0 and _owed_list() == [rid]
    assert _rec(db, rid).get("owed_paid_at") is None
    assert db["tasks"].count_documents({"source_ref": f"return_cash_owed:{rid}", "status": "OPEN"}) == 1
