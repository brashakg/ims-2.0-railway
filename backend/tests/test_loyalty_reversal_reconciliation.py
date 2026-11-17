"""
IMS 2.0 - automatic reconciliation of stranded loyalty reversals.
=================================================================
Codex round 4 (#1397): a debit landing after the cancel, whose post-debit bill
read failed, stayed deducted: the cancel retry answered 400, the admin LOYALTY
payment answered 400, and nothing a staff member can reach restored it. The
same family: a flagged cancel or return reversal had no button anywhere.

Owner ruling 2026-11-07: AUTOMATIC backend reconciliation -- exactly once,
idempotent, a visible exception for anything unresolved, no new staff control.

Driven through the REAL redeem door, the REAL reversal engine and the REAL
scheduled money sweep over mongomock; the exception is a row on the existing tasks
collection. Every money case reconciles balance, lifetime counters and ledger.

No emoji (Windows cp1252).
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from test_loyalty_redeem_exactly_once import (  # noqa: E402
    CUST,
    ORDER,
    START,
    _account,
    _account_reads_down,
    _cancel_inside_the_press,
    _cancel_route,
    _earn_on_order,
    _order_reads_down_from_the_debit,
    _press_with_ledger_down,
    _redeem,
    _redeem_rows,
    _reversal_rows,
    rig,  # noqa: F401  (pytest fixture)
)


@pytest.fixture
def tasks(rig, monkeypatch):
    import api.dependencies as deps
    from database.repositories.task_repository import TaskRepository

    repo = TaskRepository(rig["db"].get_collection("tasks"))
    monkeypatch.setattr(deps, "get_task_repository", lambda: repo)
    monkeypatch.setattr(deps, "get_user_repository", lambda: None)
    return repo


def _tick(_rig):
    """The scheduler's real money sweep, the whole job."""
    from api.routers.customers import scheduled_credit_reconcile

    asyncio.run(scheduled_credit_reconcile())


def _bill(rig):
    return rig["db"].orders.find_one({"order_id": ORDER})


def _exceptions(rig, status=None):
    query = {"source_ref": f"loyalty_reversal:{ORDER}"}
    if status:
        query["status"] = status
    return list(rig["db"].tasks.find(query))


def _books(rig):
    acct = _account(rig)
    return (int(acct["balance_points"]), int(acct.get("lifetime_redeemed") or 0),
            sorted(str(r["txn_id"]) for r in rig["db"].loyalty_transactions.find({"customer_id": CUST})))


def _stamp_cancelled(rig, **extra):
    rig["db"].orders.update_one(
        {"order_id": ORDER},
        {"$set": {"status": "CANCELLED", "cancelled_at": datetime.now(timezone.utc).isoformat(), **extra}},
    )


def _late_debit_stranded(rig, monkeypatch):
    """Codex's reproduction: no EARN; the cancel ran inside the press, before the
    debit; the debit landed; the guard after it could not read the bill."""
    _cancel_inside_the_press(rig, monkeypatch)
    undo = _order_reads_down_from_the_debit(rig, monkeypatch)
    with pytest.raises(Exception) as ei:
        _redeem()
    undo()
    assert not (isinstance(ei.value, HTTPException) and 400 <= ei.value.status_code < 500)
    _stamp_cancelled(rig)
    assert _bill(rig).get("loyalty_reversal_failed") in (None, False)
    assert int(_account(rig)["balance_points"]) == START - 100


def test_the_tick_restores_a_late_debit_no_staff_door_can_reach_once(rig, tasks, monkeypatch):
    _late_debit_stranded(rig, monkeypatch)

    _tick(rig)
    acct = _account(rig)
    assert int(acct["balance_points"]) == START and int(acct["lifetime_redeemed"]) == 0
    assert len(_redeem_rows(rig)) == 1
    assert sum(int(r.get("points") or 0) for r in _reversal_rows(rig)) == 100
    assert _exceptions(rig) == []
    assert rig["db"].audit_logs.count_documents({"action": "loyalty.reversal_reconciled"}) == 1

    books = _books(rig)
    _tick(rig)
    with pytest.raises(HTTPException) as ei:  # the till's identical retry
        _redeem()
    assert ei.value.status_code == 409 and ei.value.detail["reason"] == "order_cancelled"
    _tick(rig)
    assert _books(rig) == books
    assert rig["db"].audit_logs.count_documents({"action": "loyalty.reversal_reconciled"}) == 1


def test_the_tick_finishes_a_flagged_cancel_and_clears_its_flag(rig, tasks, monkeypatch):
    cancel = _cancel_route(rig, monkeypatch)
    _press_with_ledger_down(rig, monkeypatch)
    undo = _account_reads_down(rig, monkeypatch)
    assert cancel()["loyalty_reversal_failed"] is True
    undo()
    assert int(_account(rig)["balance_points"]) == START - 100

    _tick(rig)
    assert int(_account(rig)["balance_points"]) == START
    assert _bill(rig)["loyalty_reversal_failed"] is False
    assert _exceptions(rig) == []


def test_a_cancel_whose_stamp_was_lost_is_found_by_the_tick(rig, tasks):
    """No flag at all: the failure that lost its own marker. Earn clawed, redeem
    restored, each once."""
    _earn_on_order(rig, 40)
    assert _redeem()["redeemed_points"] == 100
    _stamp_cancelled(rig)  # the claim landed; the undo never ran
    _tick(rig)
    _tick(rig)
    acct = _account(rig)
    assert int(acct["balance_points"]) == START
    assert int(acct["lifetime_redeemed"]) == 0
    assert len(_reversal_rows(rig)) == 1 and _exceptions(rig) == []


def test_the_tick_finishes_a_flagged_return_once(rig, tasks, monkeypatch):
    from api.routers.loyalty import reverse_for_return

    _press_with_ledger_down(rig, monkeypatch)
    undo = _account_reads_down(rig, monkeypatch)
    assert reverse_for_return("RET-1", ORDER, CUST)["ok"] is False
    undo()
    rig["db"].returns.insert_one({"return_id": "RET-1", "order_id": ORDER, "customer_id": CUST,
                                  "store_id": "BV-TEST-01", "status": "COMPLETED",
                                  "loyalty_reversal_failed": True})

    _tick(rig)
    assert int(_account(rig)["balance_points"]) == START
    assert rig["db"].returns.find_one({"return_id": "RET-1"})["loyalty_reversal_failed"] is False
    books = _books(rig)
    _tick(rig)
    assert _books(rig) == books and len(_reversal_rows(rig)) == 1


def _underflow_stranded(rig, monkeypatch):
    """The bill's earned points were already spent elsewhere: taking them back
    would go below zero, so no sweep can finish this alone."""
    _earn_on_order(rig, 500)
    cancel = _cancel_route(rig, monkeypatch)
    rig["accounts"].adjust_balance(CUST, delta_points=-(START + 500))
    assert cancel()["loyalty_reversal_failed"] is True


def test_what_cannot_be_settled_is_one_visible_exception_that_closes_itself(rig, tasks, monkeypatch):
    _underflow_stranded(rig, monkeypatch)

    _tick(rig)
    _tick(rig)
    [task] = _exceptions(rig, "OPEN")
    assert task["priority"] == "P1" and task["store_id"] == "BV-TEST-01"
    assert "SO-1" in task["title"]
    assert "below zero" in task["description"]
    assert int(_account(rig)["balance_points"]) == 0
    assert _bill(rig)["loyalty_reversal_failed"] is True

    # The customer earns again: now the balance covers the claw.
    rig["accounts"].adjust_balance(CUST, delta_points=500)
    _tick(rig)
    assert _bill(rig)["loyalty_reversal_failed"] is False
    assert int(_account(rig)["balance_points"]) == 0
    assert _exceptions(rig, "OPEN") == [] and len(_exceptions(rig, "COMPLETED")) == 1
    _tick(rig)
    assert int(_account(rig)["balance_points"]) == 0 and len(_exceptions(rig)) == 1


def test_an_old_bill_healed_at_a_door_still_closes_its_exception(rig, tasks, monkeypatch):
    """Past the sweep's reach, unflagged again: the open task must not outlive it."""
    _underflow_stranded(rig, monkeypatch)
    _tick(rig)
    assert len(_exceptions(rig, "OPEN")) == 1
    rig["accounts"].adjust_balance(CUST, delta_points=500)
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    rig["db"].orders.update_one({"order_id": ORDER}, {"$set": {"cancelled_at": old}})
    with pytest.raises(HTTPException) as ei:  # the redeem door heals it
        _redeem()
    assert ei.value.status_code == 409 and _bill(rig)["loyalty_reversal_failed"] is False
    _tick(rig)
    assert _exceptions(rig, "OPEN") == [] and "loyalty_exception_task" not in _bill(rig)


def test_an_exception_a_person_closed_is_not_filed_again(rig, tasks, monkeypatch):
    _underflow_stranded(rig, monkeypatch)
    _tick(rig)
    [task] = _exceptions(rig, "OPEN")
    tasks.complete_task(task["task_id"], notes="Spoken to the customer.")
    _tick(rig)
    assert _exceptions(rig, "OPEN") == [] and len(_exceptions(rig)) == 1


def test_a_blip_during_the_tick_moves_nothing_and_files_nothing_until_it_lasts(rig, tasks, monkeypatch):
    _late_debit_stranded(rig, monkeypatch)
    undo = _account_reads_down(rig, monkeypatch)
    _tick(rig)
    assert _bill(rig)["loyalty_reversal_failed"] is True and _exceptions(rig) == []
    _tick(rig)  # still failing on the next tick: now somebody is told
    assert len(_exceptions(rig, "OPEN")) == 1
    assert "could not be read" in _exceptions(rig)[0]["description"]
    undo()
    assert int(_account(rig)["balance_points"]) == START - 100

    _tick(rig)
    assert int(_account(rig)["balance_points"]) == START
    assert _bill(rig)["loyalty_reversal_failed"] is False and _exceptions(rig, "OPEN") == []


def test_the_tick_racing_a_press_hands_the_debit_back_once(rig, tasks, monkeypatch):
    """The sweep lands inside the press, between its debit and its row."""
    _cancel_inside_the_press(rig, monkeypatch)
    accounts = rig["accounts"]
    real = accounts.try_debit
    ticked = []

    def debit_then_tick(*a, **k):
        out = real(*a, **k)
        if not ticked:
            ticked.append(True)
            _stamp_cancelled(rig)
            from api.routers.loyalty import reconcile_loyalty_reversals

            reconcile_loyalty_reversals()
        return out

    monkeypatch.setattr(accounts, "try_debit", debit_then_tick)
    with pytest.raises(HTTPException) as ei:
        _redeem()
    assert ei.value.status_code == 409 and ei.value.detail["reason"] == "order_cancelled"
    _tick(rig)
    acct = _account(rig)
    assert int(acct["balance_points"]) == START and int(acct["lifetime_redeemed"]) == 0
    assert len(_redeem_rows(rig)) == 1
    assert sum(int(r.get("points") or 0) for r in _reversal_rows(rig)) == 100


def test_the_redeem_door_clears_the_flag_it_heals(rig, tasks, monkeypatch):
    """Codex's stale flag: the points came back, the bill still said failed."""
    _late_debit_stranded(rig, monkeypatch)
    rig["db"].orders.update_one({"order_id": ORDER}, {"$set": {"loyalty_reversal_failed": True}})
    with pytest.raises(HTTPException) as ei:
        _redeem()
    assert ei.value.status_code == 409
    assert int(_account(rig)["balance_points"]) == START
    assert _bill(rig)["loyalty_reversal_failed"] is False


def test_a_live_bill_is_never_touched(rig, tasks):
    assert _redeem()["redeemed_points"] == 100
    books = _books(rig)
    _tick(rig)
    assert _books(rig) == books and _reversal_rows(rig) == [] and _exceptions(rig) == []


def test_an_old_cancelled_bill_nobody_flagged_is_left_alone(rig, tasks):
    """Historical bills are not re-judged: outside the window, no flag, no move."""
    _earn_on_order(rig, 40)
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    rig["db"].orders.update_one({"order_id": ORDER}, {"$set": {"status": "CANCELLED", "cancelled_at": old}})
    rig["db"].orders.insert_one({"_id": "ORD-LEGACY", "order_id": "ORD-LEGACY",
                                 "customer_id": CUST, "status": "CANCELLED"})
    books = _books(rig)
    _tick(rig)
    assert _books(rig) == books and _reversal_rows(rig) == []


def test_a_voided_return_is_not_reversed(rig, tasks):
    rig["db"].returns.insert_one({"return_id": "RET-V", "order_id": ORDER, "customer_id": CUST,
                                  "status": "VOIDED", "loyalty_reversal_failed": True})
    _earn_on_order(rig, 40)
    books = _books(rig)
    _tick(rig)
    assert _books(rig) == books and _reversal_rows(rig) == []


# --- Independent review of e087e43a -----------------------------------------

def _audited(rig):
    return rig["db"].audit_logs.count_documents({"action": "loyalty.reversal_reconciled"})


def _sweep():
    from api.routers.loyalty import reconcile_loyalty_reversals

    return reconcile_loyalty_reversals()


def test_a_late_restore_on_a_bill_that_earned_is_recorded_once(rig, tasks, monkeypatch):
    """A bill that earned gets its marker from the cancel, before the debit: the
    marker never names that debit, so every tick for the sweep's reach takes the
    late-restore step again. Only the tick that moved the points may say so."""
    _earn_on_order(rig)
    _late_debit_stranded(rig, monkeypatch)

    assert _sweep()["reconciled"] == 1
    acct = _account(rig)
    assert int(acct["balance_points"]) == START and int(acct["lifetime_redeemed"]) == 0
    books = _books(rig)
    for _ in range(3):
        assert _sweep() == {"reconciled": 0, "unresolved": 0, "clean": 1}
    assert _books(rig) == books
    assert _audited(rig) == 1


def test_a_restore_the_door_already_made_is_no_news_to_the_tick(rig, tasks, monkeypatch):
    _earn_on_order(rig)
    _cancel_inside_the_press(rig, monkeypatch)
    with pytest.raises(HTTPException) as ei:  # the press's own check hands it back
        _redeem()
    assert ei.value.status_code == 409
    _stamp_cancelled(rig)
    books = _books(rig)

    _tick(rig)
    _tick(rig)
    assert _books(rig) == books and int(_account(rig)["balance_points"]) == START
    assert _audited(rig) == 0


RET = "RET-U"
ACCOUNTS_USER = {"user_id": "acc-1", "username": "accounts", "roles": ["ACCOUNTANT"],
                 "active_store_id": "BV-TEST-01", "store_ids": ["BV-TEST-01"]}


def _unfinished_return(rig, monkeypatch, *, customer=CUST, credit=True, **hold):
    """A return whose money moved but whose record was never written: the press
    stopped before its reversal, so Needs completing is the only way forward."""
    from api.routers import returns as returns_router

    monkeypatch.setattr(returns_router, "_get_db", lambda: rig["db"])
    monkeypatch.setattr(returns_router, "_orders_coll", lambda: rig["db"].orders)
    facts = {"amount": 1200.0, "at": "2026-11-01T10:00:00+00:00", "kept": True, "money_out": True,
             "store_id": "BV-TEST-01", "customer_id": customer, "net_amount": 1200.0, "return_type": "RETURN",
             "gst_view": {"taxable": 1016.95, "tax": 183.05, "gross": 1200.0},
             "tenders": [{"method": "CASH", "amount": 1200.0}],
             "lines": [{"item_id": "li1", "product_id": "P1", "qty": 1.0}]}
    facts.update(hold)
    rig["db"].orders.update_one({"order_id": ORDER}, {"$set": {"refund_holds": {RET: facts}, "refund_holds_v": 1}})
    if credit:
        rig["db"].credit_note_ledger.insert_one({
            "_id": f"{RET}:credit", "entry_id": f"{RET}:credit", "customer_id": customer, "type": "ISSUED",
            "applied": True, "delta": 1200.0, "note_number": "TST01/C2627/0007", "order_id": ORDER,
            "created_at": "2026-11-01T10:00:00+00:00",
            "undo_on_void": {"order_id": ORDER, "return_id": RET, "net_amount": 1200.0, "return_type": "CREDIT_NOTE",
                             "lines": facts["lines"], "refund_tenders": [{"method": "STORE_CREDIT", "amount": 1200.0}]}})
    return lambda: asyncio.run(returns_router.complete_return_record(
        RET, returns_router.CompleteRecordBody(cash_leg_paid=True), ACCOUNTS_USER))


def _return_doc(rig):
    return rig["db"].returns.find_one({"return_id": RET})


def _return_exceptions(rig):
    return list(rig["db"].tasks.find({"source_ref": f"loyalty_reversal:{RET}"}))


@pytest.mark.parametrize("credit", [True, False], ids=["credit_line", "held_refund"])
def test_a_return_completed_by_hand_gets_its_reversal_once(rig, tasks, monkeypatch, credit):
    _earn_on_order(rig)
    _redeem()
    assert int(_account(rig)["balance_points"]) == START + 11 - 100
    complete = _unfinished_return(rig, monkeypatch, credit=credit)

    assert complete()["status"] == "COMPLETED"
    _tick(rig)
    acct = _account(rig)
    assert int(acct["balance_points"]) == START and int(acct["lifetime_redeemed"]) == 0
    assert _return_doc(rig)["loyalty_reversal_failed"] is False
    assert _audited(rig) == 1

    books = _books(rig)
    _tick(rig)
    assert _books(rig) == books and _audited(rig) == 1 and len(_reversal_rows(rig)) == 1
    assert _return_exceptions(rig) == []


def test_a_hand_completed_return_whose_reversal_already_ran_moves_nothing(rig, tasks, monkeypatch):
    from api.routers.loyalty import reverse_for_return

    _earn_on_order(rig)
    _redeem()
    assert reverse_for_return(RET, ORDER, CUST)["ok"] is True  # the press died just after this
    books = _books(rig)
    complete = _unfinished_return(rig, monkeypatch)

    complete()
    _tick(rig)
    assert _books(rig) == books and _audited(rig) == 0
    assert _return_doc(rig)["loyalty_reversal_failed"] is False


def test_a_hand_completed_return_that_cannot_settle_is_a_visible_exception(rig, tasks, monkeypatch):
    _earn_on_order(rig, 500)
    rig["accounts"].adjust_balance(CUST, delta_points=-(START + 500))  # already spent
    complete = _unfinished_return(rig, monkeypatch)

    complete()
    _tick(rig)
    assert _return_doc(rig)["loyalty_reversal_failed"] is True
    assert [t["priority"] for t in _return_exceptions(rig)] == ["P1"]
    assert _audited(rig) == 0

    rig["accounts"].adjust_balance(CUST, delta_points=500)  # the balance can cover it now
    _tick(rig)
    assert _return_doc(rig)["loyalty_reversal_failed"] is False
    assert int(_account(rig)["balance_points"]) == 0
    assert [t["status"] for t in _return_exceptions(rig)] == ["COMPLETED"]


def test_a_hand_completed_walk_in_return_opens_no_loyalty_account(rig, tasks, monkeypatch):
    complete = _unfinished_return(rig, monkeypatch, customer="walkin-77", credit=False)

    complete()
    _tick(rig)
    assert rig["db"].loyalty_accounts.find_one({"customer_id": "walkin-77"}) is None
    assert _return_doc(rig)["loyalty_reversal_failed"] is False


def test_the_saved_doc_keeps_the_reversal_answer_its_press_gave(rig, tasks, monkeypatch):
    """A kept doc was built after the press's reversal: it already says what
    happened, and a settled reversal is not re-opened."""
    complete = _unfinished_return(rig, monkeypatch, credit=False, doc={
        "return_id": RET, "order_id": ORDER, "customer_id": CUST, "store_id": "BV-TEST-01",
        "refund_tenders": [{"method": "CASH", "amount": 1200.0}], "loyalty_reversal_failed": False})

    assert complete()["loyalty_reversal_failed"] is False


# ----------------------------------------------------------------------------
# Owner ruling 2026-11-08 (#1397 round 5, item 1): once a PERSON closes the
# exception, the sweep leaves that bill or return alone for good -- it never
# claws back on its own later, when the customer's balance happens to cover it.
# An exception still open keeps retrying (the 11-07 ruling, unchanged).
# ----------------------------------------------------------------------------


def test_a_bill_whose_exception_a_person_closed_is_never_clawed_later(rig, tasks, monkeypatch):
    _underflow_stranded(rig, monkeypatch)
    _tick(rig)
    _tick(rig)
    [task] = _exceptions(rig, "OPEN")
    tasks.complete_task(task["task_id"], notes="Settled with the customer by hand.")

    rig["accounts"].adjust_balance(CUST, delta_points=500)  # later earning covers the claw now
    books = _books(rig)
    _tick(rig)
    _tick(rig)
    assert _books(rig) == books  # nothing clawed, no new ledger row
    assert _reversal_rows(rig) == []
    assert _bill(rig)["loyalty_reversal_failed"] is True  # still true: it was never settled
    assert len(_exceptions(rig)) == 1 and _exceptions(rig, "OPEN") == []
    assert _audited(rig) == 0


def test_a_return_whose_exception_a_person_closed_is_never_reversed_later(rig, tasks, monkeypatch):
    _earn_on_order(rig, 500)
    rig["accounts"].adjust_balance(CUST, delta_points=-(START + 500))
    _unfinished_return(rig, monkeypatch)()
    _tick(rig)
    [task] = _return_exceptions(rig)
    tasks.complete_task(task["task_id"], notes="Points corrected by hand.")

    rig["accounts"].adjust_balance(CUST, delta_points=500)
    books = _books(rig)
    _tick(rig)
    _tick(rig)
    assert _books(rig) == books and _reversal_rows(rig) == []
    assert _return_doc(rig)["loyalty_reversal_failed"] is True
    assert [t["status"] for t in _return_exceptions(rig)] == ["COMPLETED"]


def test_a_person_cancelling_the_exception_also_stops_the_retry(rig, tasks, monkeypatch):
    _underflow_stranded(rig, monkeypatch)
    _tick(rig)
    _tick(rig)
    [task] = _exceptions(rig, "OPEN")
    rig["db"].tasks.update_one({"task_id": task["task_id"]}, {"$set": {"status": "CANCELLED"}})
    rig["accounts"].adjust_balance(CUST, delta_points=500)
    books = _books(rig)
    _tick(rig)
    assert _books(rig) == books and _reversal_rows(rig) == []


def test_an_exception_the_sweep_closed_itself_mid_way_is_finished_not_frozen(rig, tasks, monkeypatch):
    """The sweep completes its task, then clears the stamp; a worker stopped
    between the two leaves the stamp beside the sweep's own closed task. That is
    not a person's decision: the next tick finishes the close."""
    from api.routers import loyalty

    _underflow_stranded(rig, monkeypatch)
    _tick(rig)
    _tick(rig)
    rig["accounts"].adjust_balance(CUST, delta_points=500)
    real = loyalty._unstamp_exception
    stop = {"once": True}

    def stopped(*a, **k):
        if stop.pop("once", False):
            return None  # the worker stopped here
        return real(*a, **k)

    monkeypatch.setattr(loyalty, "_unstamp_exception", stopped)
    _tick(rig)
    assert _exceptions(rig, "OPEN") == [] and "loyalty_exception_task" in _bill(rig)
    assert int(_account(rig)["balance_points"]) == 0  # the claw landed on this tick
    books = _books(rig)
    _tick(rig)
    assert "loyalty_exception_task" not in _bill(rig)
    assert not _bill(rig).get("loyalty_exception_closed")
    assert _books(rig) == books and _bill(rig)["loyalty_reversal_failed"] is False


def test_an_unreadable_task_list_settles_nothing(rig, tasks, monkeypatch):
    from api.services import task_triggers

    _underflow_stranded(rig, monkeypatch)
    _tick(rig)
    _tick(rig)
    rig["accounts"].adjust_balance(CUST, delta_points=500)
    books = _books(rig)

    def down(*_a, **_k):
        raise RuntimeError("tasks unreadable")

    monkeypatch.setattr(task_triggers, "active_tasks", down)
    _tick(rig)
    assert _books(rig) == books


def test_a_hand_completed_points_sale_pays_only_its_cash_part_and_the_points_once(rig, tasks, monkeypatch):
    """Owner ruling 2026-11-08 (item 2) on the recovery door: a press that died
    after its money moved is rebuilt from the hold, whose figures carry the
    points-paid part; the record pays the rest, the sweep gives the points."""
    _earn_on_order(rig)
    _redeem()
    complete = _unfinished_return(rig, monkeypatch, credit=False, net_amount=1200.0,
                                  points_paid_share=100.0, tenders=[{"method": "CASH", "amount": 1100.0}])
    complete()
    doc = _return_doc(rig)
    assert doc["refund_amount"] == 1100.0 and doc["points_paid_share"] == 100.0
    assert doc["net_refund"] == 1200.0 and doc["credit_amount"] == 0.0
    _tick(rig)
    _tick(rig)
    acct = _account(rig)
    assert int(acct["balance_points"]) == START and len(_reversal_rows(rig)) == 1


# ----------------------------------------------------------------------------
# Round 6 (#1397 review item 4): the admin's close holds at EVERY door, not only
# the sweep. The redeem door on a cancelled bill runs the reversal engine too;
# after the admin closed the exception and the customer earned again, it used to
# claw the points back although the cashier only tried a redemption. A later
# return or a cancel of the same bill runs the same engine.
# ----------------------------------------------------------------------------


def _admin_closed_underflow(rig, tasks, monkeypatch):
    _underflow_stranded(rig, monkeypatch)
    _tick(rig)
    _tick(rig)
    [task] = _exceptions(rig, "OPEN")
    tasks.complete_task(task["task_id"], notes="Settled with the customer by hand.")
    rig["accounts"].adjust_balance(CUST, delta_points=500)  # the customer earns again
    return _books(rig)


def test_a_redeem_attempt_never_claws_a_bill_whose_exception_an_admin_closed(rig, tasks, monkeypatch):
    books = _admin_closed_underflow(rig, tasks, monkeypatch)
    with pytest.raises(HTTPException) as ei:  # before the next tick saw the close
        _redeem()
    assert _books(rig) == books and _reversal_rows(rig) == []  # nothing clawed
    assert ei.value.status_code == 409 and ei.value.detail["reason"] == "order_cancelled"
    assert "admin" in ei.value.detail["message"].lower()
    assert "are back on the customer's account" not in ei.value.detail["message"]
    assert _bill(rig)["loyalty_reversal_failed"] is True  # never settled: the record says so
    _tick(rig)
    with pytest.raises(HTTPException):
        _redeem()
    assert _books(rig) == books and _audited(rig) == 0


def test_a_later_return_or_cancel_never_claws_once_an_admin_closed_the_return_exception(rig, tasks, monkeypatch):
    from api.routers.loyalty import reverse_for_cancel, reverse_for_return

    _earn_on_order(rig, 500)
    rig["accounts"].adjust_balance(CUST, delta_points=-(START + 500))
    _unfinished_return(rig, monkeypatch)()
    _tick(rig)
    [task] = _return_exceptions(rig)
    tasks.complete_task(task["task_id"], notes="Points corrected by hand.")
    rig["accounts"].adjust_balance(CUST, delta_points=500)
    books = _books(rig)

    second = reverse_for_return("RET-2", ORDER, CUST)  # the next return of the bill
    assert second["ok"] is True and second.get("waived") is True
    cancel = reverse_for_cancel(ORDER, CUST)  # then the bill is cancelled
    assert cancel["ok"] is True and cancel.get("waived") is True
    assert _books(rig) == books and _reversal_rows(rig) == []


def test_an_open_exception_still_lets_the_doors_settle_the_bill(rig, tasks, monkeypatch):
    """Positive control: while the task is open, the redeem door still settles."""
    _underflow_stranded(rig, monkeypatch)
    _tick(rig)
    _tick(rig)
    assert len(_exceptions(rig, "OPEN")) == 1
    rig["accounts"].adjust_balance(CUST, delta_points=500)
    with pytest.raises(HTTPException) as ei:
        _redeem()
    assert ei.value.status_code == 409
    assert int(_account(rig)["balance_points"]) == 0 and len(_reversal_rows(rig)) == 1
    assert _bill(rig)["loyalty_reversal_failed"] is False
