"""
IMS 2.0 - #1397 (Claude's reviewers on head 71b0508a; D107, D108, D109):
the stock step of a return's record, one test per state. NATIVE Mongo.
==========================================================================
The marks on a COMPLETED return record and what finishes each state
(A = restock_applied, P = stock_pending, I = restock_in_progress):

  R2 pending      A no,  P yes, I no    the press's insert / Needs completing
                                        -> Update stock (its task: the sweep)
  R3 claim held   A no,  P yes, I yes   _claim_restock (the press, Update stock)
                                        -> the claimer's own closing stamp
  R4 stopped      A no,  P yes, I old   a claimer died / its stamp was lost
                                        -> a person checks (task, History label);
                                           completing the task records the check
  R5 damaged open A yes, P yes          the closing stamp, DAMAGED not set aside
                                        -> Update stock / the identical re-press,
                                           NO claim (idempotent on named units)
  R6 done         A yes, P no           "Already restocked"
  R7 checked      A no,  P no, checked  final (IMS adds nothing)
  R8 left to the counter (a hold from before D95): a person checks.

Before (each reproduced here on native Mongo, real routes, head 71b0508a):

* R5 was taken with a claim (I yes) that only one fail-soft write gave back:
  that write lost, "Update stock" answered "Already restocked" for ever with
  the damaged unit still SOLD;
* the identical re-press that took that claim stamped the record "nothing
  restocked": the next "Update stock" put a SECOND unit back for one piece;
* "Cash owed - now paid" answered 201 with no audit row, ever;
* the replay of a return recorded by the build on main wrote a second
  RETURN_CREATED row;
* a failed read before the record insert let the insert decide alone (two
  COMPLETED records beside an older build's record, no index);
* a failed History read showed an empty history.

Same harness and rules as test_d95_one_record_native (real MongoDB, stored
stock ROWS asserted, never call counts, no index on returns.return_id).
No emoji (cp1252).
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

from fastapi import HTTPException  # noqa: E402
from pymongo.collection import Collection  # noqa: E402
from pymongo.errors import AutoReconnect, DuplicateKeyError  # noqa: E402

from api.routers import tasks as tasks_router  # noqa: E402
from database.repositories.task_repository import TaskRepository  # noqa: E402
from tests.test_d95_one_record_native import (  # noqa: E402,F401 -- `native` is the fixture
    REAL_FIND_ONE,
    REAL_INSERT,
    _age_hold,
    _complete,
    _in_thread,
    _insert_fails_once,
    _payload,
    _press,
    _press_doc,
    _restock,
    _two_at_once,
    native,
)
from tests.test_d95_press_again_native import _history, _once, _stock_unreadable_once  # noqa: E402
from tests.test_pos_money_doors import STORE, _user, returns_router  # noqa: E402

pytestmark = pytest.mark.usefixtures("gst_numbers")

REAL_UPDATE = Collection.update_one
OLD = "2026-01-01T00:00:00+00:00"


def _mixed(db):
    """The bill gains a Rs 100 line (P2) with its own SOLD unit U3. The
    return takes ONE P1 piece back GOOD and the P2 piece back DAMAGED."""
    db["orders"].update_one({"order_id": "ORD-1"}, {
        "$push": {"items": {"item_id": "li2", "product_id": "P2", "product_name": "Case", "sku": "C1",
                            "quantity": 1, "unit_price": 84.75, "gst_rate": 18.0, "taxable_value": 84.75,
                            "tax_amount": 15.25}},
        "$set": {"grand_total": 3700.0, "amount_paid": 3700.0,
                 "payments": [{"payment_id": "p1", "method": "CASH", "amount": 3700.0}]}})
    db["stock_units"].insert_one({"_id": "U3", "stock_id": "U3", "product_id": "P2", "store_id": STORE,
                                  "status": "SOLD", "order_id": "ORD-1", "quantity": 1, "barcode": "BC3"})
    body = _payload()
    body["items"].append({"order_item_id": "li2", "product_id": "P2", "product_name": "Case", "sku": "C1",
                          "return_qty": 1, "unit_price": 84.75, "reason": "DEFECTIVE", "condition": "DAMAGED",
                          "restock": False})
    body["refund_tenders"] = [{"method": "CASH", "amount": 1900.0}]
    return body


def _rows(db):
    """The stock ROWS: the two P1 units (one piece came back), the P2 unit,
    and how many rows there are (a mint would add one)."""
    units = {u["_id"]: u["status"] for u in db["stock_units"].find({})}
    return {"p1": sorted(units[k] for k in ("U1", "U2")), "p2": units.get("U3"), "rows": len(units)}


def _rec(db, rid):
    return db["returns"].find_one({"return_id": rid})


def _tasks(db):
    return sorted((t["status"], "check each unit" in t["description"]) for t in db["tasks"].find(
        {"source_ref": {"$regex": "^return_restock_blocked:"}}))


def _stamp_lost(monkeypatch, state, times=1):
    """The closing write of a stock step never lands (the reply and the write)."""
    def update(self, flt, change, *a, **k):
        sets = (change or {}).get("$set") or {}
        if self.name == "returns" and "stock_pending" in sets and state.get("arm") and state.get("lost", 0) < times:
            state["lost"] = state.get("lost", 0) + 1
            raise AutoReconnect("the stock step's stamp was lost")
        return REAL_UPDATE(self, flt, change, *a, **k)

    monkeypatch.setattr(Collection, "update_one", update)


def _dies_after_its_record(monkeypatch, state):
    real = returns_router._finish_press

    async def dies_once(*a, **k):
        if _once(state, "died"):
            raise RuntimeError("the press stopped after its record")
        return await real(*a, **k)

    monkeypatch.setattr(returns_router, "_finish_press", dies_once)


def _stopped_record(db, monkeypatch, payload):
    """R2: a record whose press stopped before its stock step."""
    state = {}
    _dies_after_its_record(monkeypatch, state)
    with pytest.raises(RuntimeError):
        _press(payload)
    return db["returns"].find_one({})["return_id"]


# --- R2: pending, the press stopped before its stock step ---------------------------------------


def test_r2_a_pending_step_is_finished_once_by_update_stock(native, monkeypatch):
    db = native["db"]
    rid = _stopped_record(db, monkeypatch, _mixed(db))
    rec = _rec(db, rid)
    assert (rec["restock_applied"], rec["stock_pending"], rec.get("restock_in_progress")) == (False, True, None)
    assert _rows(db) == {"p1": ["SOLD", "SOLD"], "p2": "SOLD", "rows": 3}
    db["returns"].update_one({"return_id": rid}, {"$set": {"created_at": OLD}})
    assert returns_router.sweep_stuck_return_holds() == 1 and _tasks(db) == [("OPEN", False)]
    row = [x for x in _history(db)["returns"] if x["return_id"] == rid][0]
    assert row["stock_pending"] is True and not row.get("stock_step_stopped")
    out = _restock(rid)
    assert out["restock_applied"] is True, out
    assert _rows(db) == {"p1": ["AVAILABLE", "SOLD"], "p2": "QUARANTINED", "rows": 3}
    rec = _rec(db, rid)
    assert (rec["restock_applied"], rec["stock_pending"], rec["restock_in_progress"]) == (True, False, False)
    assert _tasks(db) == [("COMPLETED", False)]
    # R6: done. The button moves nothing more.
    assert _restock(rid)["message"] == "Already restocked"
    assert _rows(db) == {"p1": ["AVAILABLE", "SOLD"], "p2": "QUARANTINED", "rows": 3}


# --- R3: a claim held right now ------------------------------------------------------------------


def test_r3_a_claim_held_by_another_worker_places_nothing_here(native, monkeypatch):
    db = native["db"]
    rid = _stopped_record(db, monkeypatch, _mixed(db))
    held = returns_router._claim_restock(db["returns"], rid)  # another worker, right now
    assert held["restock_in_progress"] is True
    out = _restock(rid)
    assert out["message"] == "Restock in progress - retry shortly", out
    assert _rows(db) == {"p1": ["SOLD", "SOLD"], "p2": "SOLD", "rows": 3}
    assert returns_router._claim_restock(db["returns"], rid) is None  # one claim, one worker


def test_r3_a_step_that_could_not_place_the_units_gives_its_claim_back(native, monkeypatch):
    """The claimer's closing stamp when nothing was placed (the bill could
    not be read): back to R2, and the next Update stock places each unit once."""
    db = native["db"]
    rid = _stopped_record(db, monkeypatch, _mixed(db))
    with monkeypatch.context() as m:
        m.setattr(returns_router, "_load_order_for_restock", lambda _oid: None)
        out = _restock(rid)
    assert out["restock_applied"] is False and "Nothing was added" in out["message"], out
    rec = _rec(db, rid)
    assert (rec["restock_applied"], rec["stock_pending"], rec["restock_in_progress"]) == (False, True, False)
    assert _rows(db) == {"p1": ["SOLD", "SOLD"], "p2": "SOLD", "rows": 3}
    assert _restock(rid)["restock_applied"] is True
    assert _rows(db) == {"p1": ["AVAILABLE", "SOLD"], "p2": "QUARANTINED", "rows": 3}


def test_r3_a_press_that_claims_after_a_partial_update_stock_places_only_what_is_still_out(native, monkeypatch):
    """Both pieces come back GOOD. 'Update stock' gets to the stock step
    before the press, puts the P1 piece back and fails on the P2 one: its
    stamp says which units landed and gives the claim back (R2). The press
    then takes the claim. Before: it placed EVERY line again -- the
    customer's other P1 piece became AVAILABLE too. The claimer places only
    the units the record does not already say are back (the rule Update
    stock itself follows)."""
    db, state = native["db"], {}
    real_claim, real_flip = returns_router._claim_restock, returns_router._reactivate_original_unit

    def p2_fails_once(stock_repo, product_id, *a, **k):
        if product_id == "P2" and _once(state, "p2_failed"):
            raise AutoReconnect("stock write failed")
        return real_flip(stock_repo, product_id, *a, **k)

    def update_stock_gets_there_first(coll, return_id):
        if _once(state, "raced"):
            state["first"] = _in_thread(lambda: _restock(return_id))
        return real_claim(coll, return_id)

    monkeypatch.setattr(returns_router, "_reactivate_original_unit", p2_fails_once)
    monkeypatch.setattr(returns_router, "_claim_restock", update_stock_gets_there_first)
    body = _mixed(db)
    body["items"][1].update(condition="GOOD", restock=True)
    r = _press(body)
    assert r.status_code == 201 and state.get("p2_failed"), (r.text, state)
    assert state["first"]["value"]["restock_applied"] is False, state["first"]
    assert _rows(db) == {"p1": ["AVAILABLE", "SOLD"], "p2": "AVAILABLE", "rows": 3}
    rec = _rec(db, r.json()["return_id"])
    assert (rec["restock_applied"], rec["stock_pending"], rec["restock_in_progress"]) == (True, False, False)
    assert returns_router._landed_units(rec["restocked"]) == {"P1": 1.0, "P2": 1.0}, rec["restocked"]
    assert len(rec["restock_stock_ids"]) == 2 and r.json()["restock_applied"] is True
    assert _restock(rec["return_id"])["message"] == "Already restocked"
    assert _rows(db) == {"p1": ["AVAILABLE", "SOLD"], "p2": "AVAILABLE", "rows": 3}


# --- R4: the claimer stopped part way (D92(5), D109(2)) ------------------------------------------


def _complete_task(db, monkeypatch, rid, who="MGR-7"):
    monkeypatch.setattr(tasks_router, "get_task_repository", lambda: TaskRepository(db["tasks"]))
    task = db["tasks"].find_one({"source_ref": f"return_restock_blocked:{rid}", "status": {"$ne": "COMPLETED"}})
    return asyncio.run(tasks_router.complete_task(
        task["task_id"], tasks_router.TaskComplete(completion_notes="checked each unit on the shelf"),
        _user("STORE_MANAGER", who)))


def test_r4_a_stamp_lost_after_the_units_went_back_never_adds_a_second_unit(native, monkeypatch):
    """The press put its GOOD unit back and BOTH tries of its stamp were lost:
    the record still reads 'claimed, nothing back'. IMS places nothing more by
    itself (a second run flipped the customer's other piece); the History row
    says to check by hand; closing the task records who checked and clears it."""
    db, state = native["db"], {"arm": True}
    _stamp_lost(monkeypatch, state, times=2)
    r = _press(_mixed(db))
    assert r.status_code == 201 and state["lost"] == 2, r.text
    rid = r.json()["return_id"]
    after_press = {"p1": ["AVAILABLE", "SOLD"], "p2": "QUARANTINED", "rows": 3}
    assert _rows(db) == after_press
    rec = _rec(db, rid)
    assert (rec["restock_applied"], rec["stock_pending"], rec["restock_in_progress"]) == (False, True, True)
    # Fresh: it may still be running.
    assert _restock(rid)["message"] == "Restock in progress - retry shortly" and _rows(db) == after_press
    db["returns"].update_one({"return_id": rid}, {"$set": {"restock_started_at": OLD, "created_at": OLD}})
    for _ in range(2):
        out = _restock(rid)
        assert out["restock_applied"] is False and "stopped part way" in out["message"], out
        assert _rows(db) == after_press  # the other P1 piece stays SOLD; nothing minted
    assert _tasks(db) == [("OPEN", True)]
    row = [x for x in _history(db)["returns"] if x["return_id"] == rid][0]
    assert row["stock_pending"] is True and row["stock_step_stopped"] is True
    # D109(2): the person who checked closes the task; the return records it.
    assert _complete_task(db, monkeypatch, rid)["status"] == "COMPLETED"
    rec = _rec(db, rid)
    assert rec["stock_pending"] is False and rec["stock_checked_by"] == "MGR-7" and rec["stock_checked_at"]
    row = [x for x in _history(db)["returns"] if x["return_id"] == rid][0]
    assert row["stock_pending"] is False and not row.get("stock_step_stopped")
    # R7: checked. The button (if anything still calls it) adds nothing.
    out = _restock(rid)
    assert out["restock_applied"] is False and "checked" in out["message"], out
    assert _rows(db) == after_press and _tasks(db) == [("COMPLETED", True)]
    assert returns_router.sweep_stuck_return_holds() == 0 and _tasks(db) == [("COMPLETED", True)]


def test_r4_a_claimer_that_died_leaves_the_damaged_unit_to_update_stock(native, monkeypatch):
    """A worker died holding the claim before it placed anything. Update stock
    places no GOOD unit (it cannot know) but still sets the DAMAGED one aside
    (D92(2)); a task not closed by a person changes nothing on the return."""
    db = native["db"]
    rid = _stopped_record(db, monkeypatch, _mixed(db))
    db["returns"].update_one({"return_id": rid}, {"$set": {"restock_in_progress": True, "restock_started_at": OLD}})
    out = _restock(rid)
    assert "stopped part way" in out["message"], out
    assert _rows(db) == {"p1": ["SOLD", "SOLD"], "p2": "QUARANTINED", "rows": 3}
    rec = _rec(db, rid)
    assert rec["stock_pending"] is True and not rec.get("stock_checked_at") and _tasks(db) == [("OPEN", True)]
    # A record whose step is NOT stopped is never marked checked by a task close.
    db["returns"].update_one({"return_id": rid}, {"$set": {"restock_in_progress": False}})
    _complete_task(db, monkeypatch, rid)
    rec = _rec(db, rid)
    assert rec["stock_pending"] is True and not rec.get("stock_checked_at")


# --- R5: GOOD units back, a DAMAGED unit not set aside -------------------------------------------


def test_r5_a_damaged_step_whose_stamp_was_lost_is_finished_by_the_same_button(native, monkeypatch):
    """Must-fix (returns.py:7457). The press put the GOOD unit back; the
    DAMAGED unit's set-aside failed. Update stock: the set-aside fails again
    AND its closing stamp is lost. Before: every later press answered
    'Already restocked' with the unit SOLD for good. Now the next press sets
    it aside and closes the step; the GOOD unit is never touched again."""
    db, state = native["db"], {}
    _stock_unreadable_once(monkeypatch, state)
    r = _press(_mixed(db))
    assert r.status_code == 201 and state.get("stock_failed"), r.text
    rid = r.json()["return_id"]
    open_rows = {"p1": ["AVAILABLE", "SOLD"], "p2": "SOLD", "rows": 3}
    rec = _rec(db, rid)
    assert _rows(db) == open_rows and (rec["restock_applied"], rec["stock_pending"]) == (True, True)
    assert "damaged unit is NOT set aside" in r.json()["stock_note"]
    state.pop("stock_failed")  # the stock cannot be read once more ...
    state["arm"] = True        # ... and the step's closing stamp is lost
    _stamp_lost(monkeypatch, state)
    first = _restock(rid)
    assert state.get("stock_failed") and state.get("lost") == 1, state
    assert _rows(db) == open_rows and "Already restocked" not in first["message"], first
    second = _restock(rid)
    assert second["message"] != "Already restocked", second
    assert _rows(db) == {"p1": ["AVAILABLE", "SOLD"], "p2": "QUARANTINED", "rows": 3}, second
    rec = _rec(db, rid)
    assert (rec["restock_applied"], rec["stock_pending"]) == (True, False) and not rec.get("restock_in_progress")
    assert rec["quarantined_stock_ids"] == ["U3"] and len(rec["restock_stock_ids"]) == 1
    assert not [x for x in _history(db)["returns"] if x.get("stock_pending")]
    assert _restock(rid)["message"] == "Already restocked"
    assert _rows(db) == {"p1": ["AVAILABLE", "SOLD"], "p2": "QUARANTINED", "rows": 3}


def test_r5_a_set_aside_that_landed_with_its_stamp_lost_is_closed_by_the_next_press(native, monkeypatch):
    """The other half of the same loss: the unit WAS set aside, only the
    stamp was lost. Before: stock right, 'Units not back in stock' and a dead
    button for ever. Now the next press finds it done and closes the step."""
    db, state = native["db"], {}
    _stock_unreadable_once(monkeypatch, state)
    rid = _press(_mixed(db)).json()["return_id"]
    state["arm"] = True
    _stamp_lost(monkeypatch, state)
    _restock(rid)
    done_rows = {"p1": ["AVAILABLE", "SOLD"], "p2": "QUARANTINED", "rows": 3}
    assert state.get("lost") == 1 and _rows(db) == done_rows
    assert _rec(db, rid)["stock_pending"] is True  # the label is still up
    out = _restock(rid)
    rec = _rec(db, rid)
    assert rec["stock_pending"] is False and not rec.get("restock_in_progress"), (out, rec)
    assert _rows(db) == done_rows and _restock(rid)["message"] == "Already restocked"


def test_r5_update_stock_first_then_the_press_never_wipes_what_is_back(native, monkeypatch):
    """Must-fix (returns.py:6321, a regression of 71b0508a). 'Update stock'
    is pressed before the press reaches its stock step: it puts the GOOD unit
    back and its DAMAGED set-aside fails. The press then finishes. Before: it
    stamped the record 'nothing restocked', and the next Update stock put the
    customer's OTHER piece back too (two AVAILABLE units for one return)."""
    db, state = native["db"], {}
    real_claim = returns_router._claim_restock

    def update_stock_gets_there_first(coll, return_id):
        if _once(state, "raced"):
            _stock_unreadable_once(monkeypatch, state)
            state["first"] = _in_thread(lambda: _restock(return_id))
        return real_claim(coll, return_id)

    monkeypatch.setattr(returns_router, "_claim_restock", update_stock_gets_there_first)
    r = _press(_mixed(db))
    assert r.status_code == 201 and state.get("stock_failed"), (r.text, state)
    assert state["first"]["value"]["restock_applied"] is True, state["first"]
    rid = r.json()["return_id"]
    done_rows = {"p1": ["AVAILABLE", "SOLD"], "p2": "QUARANTINED", "rows": 3}
    assert _rows(db) == done_rows
    said = _rec(db, rid)
    for _ in range(2):
        _restock(rid)
        assert _rows(db) == done_rows  # the customer's other piece stays SOLD; nothing minted
    assert said["restock_applied"] is True and len(said["restock_stock_ids"]) == 1, said
    assert [x.get("reactivated") for x in said["restocked"]] == [1], said
    assert r.json()["restock_applied"] is True and r.json()["restock_stock_ids"] == said["restock_stock_ids"]
    assert _rec(db, rid)["stock_pending"] is False


def test_r5_the_second_of_two_identical_re_presses_never_wipes_what_is_back(native, monkeypatch):
    """The reviewer's steps: a 503 'Not confirmed' press; two identical
    re-presses finish it. The first puts the GOOD unit back and its DAMAGED
    set-aside fails; the second reaches the stock step AFTER that stamp."""
    db, state = native["db"], {}
    _insert_fails_once(monkeypatch, state)
    payload = _mixed(db)
    assert _press(payload, "K-MIX").status_code == 503
    rid, one_at_a_time, real_finish = state["failed"], threading.Lock(), returns_router._finish_press

    async def finish(*a, **k):
        with one_at_a_time:  # the second starts after the first has stamped
            if _once(state, "first_finisher"):
                _stock_unreadable_once(monkeypatch, state)
            return await real_finish(*a, **k)

    monkeypatch.setattr(returns_router, "_finish_press", finish)
    answers = _two_at_once(monkeypatch, payload, "K-MIX")
    assert sorted(a.status_code for a in answers) == [201, 201], [a.text for a in answers]
    assert state.get("stock_failed")
    done_rows = {"p1": ["AVAILABLE", "SOLD"], "p2": "QUARANTINED", "rows": 3}
    assert _rows(db) == done_rows
    said = _rec(db, rid)
    for _ in range(2):
        _restock(rid)
        assert _rows(db) == done_rows  # one piece came back: one P1 unit AVAILABLE, never two
    assert said["restock_applied"] is True and len(said["restock_stock_ids"]) == 1, said
    assert _rec(db, rid)["stock_pending"] is False
    assert db["returns"].count_documents({"payout_of": None}) == 1


# --- R8: a hold from before D95 ------------------------------------------------------------------


def test_r8_a_record_left_to_the_counter_is_never_restocked_by_ims(native):
    db = native["db"]
    db["returns"].insert_one({"_id": "ret:RET-L", "return_id": "RET-L", "order_id": "ORD-1", "store_id": STORE,
                              "status": "COMPLETED", "recovered": {"by": "U9"}, "restock_applied": False,
                              "stock_pending": False, "restock_left_to_counter": True,
                              "items": _payload()["items"]})
    out = _restock("RET-L")
    assert out["restock_applied"] is False and "Check each unit" in out["message"], out
    assert sorted(u["status"] for u in db["stock_units"].find({})) == ["SOLD", "SOLD"]


# --- D84: "Cash owed - now paid" writes its audit row before it answers --------------------------


def test_cash_owed_now_paid_without_its_audit_row_says_press_again(native, monkeypatch):
    """Must-fix (returns.py:7165). Before: 201 'booked as paid', the return
    left the owed list and no door ever wrote the row. Now 503 with the return
    still listed; the re-press replays the STORED payout and writes the row once."""
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
    assert _complete(rid, cash_leg_paid=False)["owed_amount"] == 1800.0

    def insert(self, doc, *a, **k):
        if self.name == "audit_logs" and str((doc or {}).get("log_id")).startswith("return_owed_paid:") \
                and _once(state, "audit_failed"):
            raise AutoReconnect("audit trail unreachable")
        return REAL_INSERT(self, doc, *a, **k)

    monkeypatch.setattr(Collection, "insert_one", insert)

    def paid(cash_from):
        return asyncio.run(returns_router.mark_owed_paid(
            rid, returns_router.OwedPaidBody(method="CASH", cash_paid_from=cash_from), _user("ACCOUNTANT")))

    def owed_list():
        return [x["return_id"] for x in asyncio.run(returns_router.needs_completing(_user("ACCOUNTANT")))["owed"]]

    with pytest.raises(HTTPException) as err:
        paid("STORE_DRAWER")
    assert state.get("audit_failed") and err.value.status_code == 503 and "Press again" in err.value.detail
    assert _rec(db, rid).get("owed_paid_at") is None and owed_list() == [rid]
    assert db["audit_logs"].count_documents({"_id": f"return_owed_paid:{rid}"}) == 0
    assert db["tasks"].count_documents({"source_ref": f"return_cash_owed:{rid}", "status": "OPEN"}) == 1
    for _ in range(2):
        again = paid("HQ")  # a changed answer: the STORED payment is replayed
        assert again["replayed"] is True and again["cash_paid_from"] == "STORE_DRAWER", again
    assert db["audit_logs"].count_documents({"_id": f"return_owed_paid:{rid}"}) == 1
    assert db["returns"].count_documents({"payout_of": rid}) == 1
    assert _rec(db, rid)["owed_paid_at"] and owed_list() == []
    assert db["tasks"].count_documents({"source_ref": f"return_cash_owed:{rid}", "status": "OPEN"}) == 0


# --- D84: a replay never adds a second RETURN_CREATED row ---------------------------------------


def test_the_replay_of_a_return_recorded_before_this_rule_adds_no_audit_row(native):
    """Must-fix (returns.py:6166). The build on main writes RETURN_CREATED
    under a random id and its record carries no 'audited' mark. Before: the
    identical retry after the deploy wrote a second row for one return."""
    db = native["db"]
    r = _press(_payload(), "K-OLD")
    assert r.status_code == 201, r.text
    rid = r.json()["return_id"]
    # As main wrote it: the row under a generated id, no mark on the record.
    row = db["audit_logs"].find_one({"_id": f"return_created:{rid}"})
    db["audit_logs"].delete_one({"_id": row["_id"]})
    db["audit_logs"].insert_one({**row, "_id": "LOG-3F9A", "log_id": "LOG-3F9A"})
    db["returns"].update_one({"return_id": rid}, {"$unset": {"audited": ""}})
    for _ in range(2):
        again = _press(_payload(), "K-OLD")
        assert again.status_code == 201 and again.json()["return_id"] == rid, again.text
    assert [a["_id"] for a in db["audit_logs"].find({"action": "RETURN_CREATED"})] == ["LOG-3F9A"]
    assert "audited" not in _rec(db, rid)  # stored history is not rewritten


def test_the_replay_of_a_record_that_says_not_audited_still_writes_its_row(native):
    """Control (D84): the mark False means this build's press never reached
    its row -- the replay writes it once."""
    db = native["db"]
    rid = _press(_payload(), "K-NEW").json()["return_id"]
    db["audit_logs"].delete_many({})
    db["audit_chain_head"].delete_many({})
    db["returns"].update_one({"return_id": rid}, {"$set": {"audited": False}})
    for _ in range(2):
        assert _press(_payload(), "K-NEW").status_code == 201
    assert [a["_id"] for a in db["audit_logs"].find({"action": "RETURN_CREATED"})] == [f"return_created:{rid}"]
    assert _rec(db, rid)["audited"] is True


# --- D108(1): a failed read before the insert is "not confirmed" ---------------------------------


def test_a_failed_read_before_the_insert_never_lets_the_insert_decide_alone(native, monkeypatch):
    """An older build's Needs completing wrote 'recovered:<rid>' while this
    press ran, and the press's look for a record of its return FAILS. Before:
    'the insert below still decides' -- with no index it landed beside the
    older record: two COMPLETED records, the cash netted twice. Now 503 'Do
    not pay yet'; the identical re-press finds the older record: 409."""
    db, out = native["db"], {}
    real_keep = returns_router._keep_order_refund_hold

    def older_build_completes_it(order_id, rid, *a, extra=None, **k):
        res = real_keep(order_id, rid, *a, extra=extra, **k)
        if "money_out" in (extra or {}) and "rid" not in out:
            out["rid"] = rid
            db["returns"].insert_one({"_id": f"recovered:{rid}", "return_id": rid, "order_id": "ORD-1",
                                      "status": "COMPLETED", "recovered": True, "store_id": STORE})
        return res

    def find_one(self, flt=None, *a, **k):
        if self.name == "returns" and flt == {"return_id": out.get("rid")} and _once(out, "read_failed"):
            raise AutoReconnect("returns unreadable")
        return REAL_FIND_ONE(self, flt, *a, **k)

    monkeypatch.setattr(returns_router, "_keep_order_refund_hold", older_build_completes_it)
    monkeypatch.setattr(Collection, "find_one", find_one)
    r = _press(_payload(), "K-PRE")
    assert out.get("read_failed"), "the read before the insert never ran"
    records = [(x["_id"], x["status"]) for x in db["returns"].find({})]
    assert records == [(f"recovered:{out['rid']}", "COMPLETED")], records
    assert r.status_code == 503 and r.json()["detail"] == returns_router.RECORD_NOT_CONFIRMED_MESSAGE, r.text
    assert sorted(u["status"] for u in db["stock_units"].find({})) == ["SOLD", "SOLD"]
    again = _press(_payload(), "K-PRE")
    assert again.status_code == 409 and again.json()["detail"] == returns_router.RECORD_TAKEN_MESSAGE, again.text
    assert db["returns"].count_documents({}) == 1


def test_a_failed_read_before_the_insert_is_settled_by_the_identical_re_press(native, monkeypatch):
    """The same failed read with no other writer: nothing was inserted, the
    doc rides on the hold, and the identical re-press makes the one insert."""
    db, state = native["db"], {}

    def find_one(self, flt=None, *a, **k):
        if self.name == "returns" and isinstance(flt, dict) and list(flt) == ["return_id"] \
                and _once(state, "read_failed"):
            raise AutoReconnect("returns unreadable")
        return REAL_FIND_ONE(self, flt, *a, **k)

    monkeypatch.setattr(Collection, "find_one", find_one)
    r = _press(_payload(), "K-PRE2")
    assert state.get("read_failed") and r.status_code == 503, r.text
    assert db["returns"].count_documents({}) == 0
    assert sorted(u["status"] for u in db["stock_units"].find({})) == ["SOLD", "SOLD"]
    again = _press(_payload(), "K-PRE2")
    assert again.status_code == 201, again.text
    assert db["returns"].count_documents({}) == 1
    assert sorted(u["status"] for u in db["stock_units"].find({})) == ["AVAILABLE", "SOLD"]


# --- D108(2): what a worker still running main's code sees of the new press ----------------------


def _main_hold_actionable(hold, line):
    """VERBATIM from origin/main (6e10f76b) returns._hold_actionable."""
    from datetime import datetime, timedelta, timezone

    from api.routers.customers import PENDING_TIMEOUT_SECONDS
    from api.utils.event_time import parse_event_utc_naive

    hold = hold or {}
    if not hold.get("kept") or (line is not None and line.get("type") == "ISSUING"):
        return False
    at = parse_event_utc_naive(hold.get("at"))
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=PENDING_TIMEOUT_SECONDS)
    return at is not None and at <= cutoff


def _main_needs_completing_lists(coll, rid, hold):
    """VERBATIM from origin/main needs_completing: the per-hold checks."""
    if not hold.get("kept"):
        return False
    if coll.find_one({"return_id": rid, "status": "COMPLETED"}):
        return False
    return _main_hold_actionable(hold, None)


def _main_complete_record(coll, return_id, doc):
    """VERBATIM from origin/main complete_return_record: its check, then its
    insert. Returns 'replayed' or 'inserted'."""
    existing = coll.find_one({"return_id": return_id})
    if existing is not None:
        return "replayed"
    coll.insert_one({**doc, "_id": f"recovered:{return_id}"})
    return "inserted"


def test_a_worker_on_main_sees_the_new_press_record_and_hold_as_already_recorded(native, monkeypatch):
    """D108(2): the new code adds no state an old worker misreads. Main's own
    Needs completing checks and insert, copied verbatim, run against (a) the
    hold the new press writes while it runs, (b) its 'ret:<rid>' record and
    the hold left beside it, (c) the same with the backstop index present."""
    db, seen = native["db"], {}

    def insert(self, doc, *a, **k):
        if _press_doc(self, doc) and "hold" not in seen:
            # (a) The press is about to insert its record: its hold is on the bill.
            rid = doc["return_id"]
            hold = db["orders"].find_one({"order_id": "ORD-1"})["refund_holds"][rid]
            seen.update(rid=rid, hold=hold, listed_while_running=_main_needs_completing_lists(self, rid, hold),
                        actionable_while_running=_main_hold_actionable(hold, None))
        return REAL_INSERT(self, doc, *a, **k)

    monkeypatch.setattr(Collection, "insert_one", insert)
    r = _press(_payload())
    monkeypatch.undo()
    assert r.status_code == 201, r.text
    rid, hold = seen["rid"], seen["hold"]
    # (a) a kept hold younger than main's timeout: main lists nothing and refuses to complete it.
    assert hold.get("kept") is True and hold.get("at")
    assert seen["listed_while_running"] is False and seen["actionable_while_running"] is False
    # (b) the record is under 'ret:<rid>': main's checks find it by return_id.
    coll = db["returns"]
    assert [x["_id"] for x in coll.find({})] == [f"ret:{rid}"]
    aged = {**hold, "at": OLD}  # even a hold main would call actionable
    assert _main_needs_completing_lists(coll, rid, aged) is False
    assert _main_complete_record(coll, rid, {"return_id": rid, "status": "COMPLETED"}) == "replayed"
    assert coll.count_documents({}) == 1
    # (c) main's insert alone (its check skipped: the only unsafe order) is
    # refused when the backstop index is there -- main's own exposure today.
    coll.create_index("return_id", unique=True, name="uniq_returns_return_id")
    with pytest.raises(DuplicateKeyError):
        coll.insert_one({"return_id": rid, "status": "COMPLETED", "_id": f"recovered:{rid}"})
    assert coll.count_documents({}) == 1


# --- D109(1): a History read that failed is an error, never an empty history ---------------------


def test_a_failed_history_read_is_an_error_not_an_empty_history(native, monkeypatch):
    db = native["db"]
    rid = _press(_payload()).json()["return_id"]
    assert [x["return_id"] for x in _history(db)["returns"]] == [rid]

    def unreadable(self, *a, **k):
        if self.name == "returns":
            raise AutoReconnect("returns unreadable")
        return 0

    with monkeypatch.context() as m:
        m.setattr(Collection, "count_documents", unreadable)
        with pytest.raises(HTTPException) as err:
            _history(db)
    assert err.value.status_code == 503 and "Could not load returns" in err.value.detail
    with monkeypatch.context() as m:
        m.setattr(returns_router, "_returns_coll", lambda: None)
        with pytest.raises(HTTPException) as err:
            _history(db)
    assert err.value.status_code == 503
    # One return, read failing: the detail door says so too (never 'not found').
    with monkeypatch.context() as m:
        m.setattr(Collection, "find_one", lambda self, *a, **k: (_ for _ in ()).throw(AutoReconnect("down")))
        with pytest.raises(HTTPException) as err:
            asyncio.run(returns_router.get_return(rid, current_user=_user("STORE_MANAGER")))
        assert err.value.status_code == 503
        with pytest.raises(HTTPException) as err:
            _restock(rid)
        assert err.value.status_code == 503
    assert [x["return_id"] for x in _history(db)["returns"]] == [rid]
