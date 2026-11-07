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
