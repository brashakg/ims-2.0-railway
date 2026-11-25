"""
IMS 2.0 - L2-LOYALTY-F01: one bill's loyalty redemption debits points ONCE.
===========================================================================
Codex audit: two identical POST /loyalty/redeem presses for the same
(customer, order) both passed the "prior REDEEM row?" read before either had
written its ledger row, so both debited -- 1056 -> 856 for one 100-point
redemption. The funds guard held (balance covered both), so only an
operation identity ON THE DEBIT ITSELF can stop it.

Contracts proven here over mongomock (real filter / $push / _id semantics),
driving the REAL redeem handler + repositories:
  * two overlapping presses of one bill's redeem -> one debit, one REDEEM row,
    both answers name the same burn (txn_id);
  * the ledger row lost after the debit (lost ack / process death) -> no
    success is answered without the row; the retry writes the one missing row
    at the ORIGINAL rupee value and debits nothing more;
  * a cancel or return BEFORE that retry hands the debit back exactly once
    (the account's debit, not the ledger, is the truth of what was redeemed);
  * a debit landing after the cancel's reversal is handed back too, once;
  * a press losing the reversal-marker insert to a cancel that never saw its
    debit hands that debit back too, and the mirror restores nothing twice;
  * a bill that cannot be READ is never taken for a live one: the press gets no
    success, nothing strands, and the next press hands the late debit back once;
  * the same bill retried with a DIFFERENT amount after that -> refused,
    balance untouched;
  * a standalone goodwill redeem (no order) is unchanged: each press is its
    own redemption;
  * a plain single redeem on a bill still burns exactly what it did.
Every case reconciles: balance drop == sum of REDEEM rows == lifetime_redeemed,
with sufficient points for TWO burns (an insufficient-funds test hides this).

No emoji (Windows cp1252).
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

mongomock = pytest.importorskip("mongomock")

CUST = "cust-redeem-1"
ORDER = "ORD-REDEEM-1"
START = 1000
STAFF = {"user_id": "staff-1", "username": "priya", "roles": ["SALES_STAFF"],
         "active_store_id": "BV-TEST-01", "store_ids": ["BV-TEST-01"]}


@pytest.fixture
def rig(monkeypatch):
    from api.routers import loyalty as lm
    from database.repositories.audit_repository import AuditRepository
    from database.repositories.loyalty_repository import (
        LoyaltyAccountRepository,
        LoyaltySettingsRepository,
        LoyaltyTransactionRepository,
    )
    from database.repositories.order_repository import OrderRepository

    db = mongomock.MongoClient()["loyalty_exactly_once"]
    accounts = LoyaltyAccountRepository(db.get_collection("loyalty_accounts"))
    txns = LoyaltyTransactionRepository(db.get_collection("loyalty_transactions"))
    settings = LoyaltySettingsRepository(db.get_collection("loyalty_settings"))
    orders = OrderRepository(db.get_collection("orders"))
    audit = AuditRepository(db.get_collection("audit_logs"))
    monkeypatch.setattr(lm, "get_loyalty_account_repository", lambda: accounts)
    monkeypatch.setattr(lm, "get_loyalty_transaction_repository", lambda: txns)
    monkeypatch.setattr(lm, "get_loyalty_settings_repository", lambda: settings)
    monkeypatch.setattr(lm, "get_order_repository", lambda: orders)
    monkeypatch.setattr(lm, "get_audit_repository", lambda: audit)

    db.orders.insert_one({"_id": ORDER, "order_id": ORDER, "customer_id": CUST,
                          "grand_total": 5000.0, "store_id": "BV-TEST-01"})
    accounts.find_or_create(CUST)
    accounts.adjust_balance(CUST, delta_points=START, delta_lifetime_earned=START)
    return {"db": db, "accounts": accounts, "txns": txns}


def _redeem(points=100, order_id=ORDER):
    from api.routers.loyalty import RedeemRequest, redeem

    body = RedeemRequest(customer_id=CUST, order_id=order_id, points=points)
    return asyncio.run(redeem(body, current_user=STAFF))


def _redeem_rows(rig):
    return list(rig["db"].loyalty_transactions.find({"customer_id": CUST, "type": "REDEEM"}))


def _reconcile(rig, expected_burn):
    """Independent books check: what left the balance is exactly what the
    ledger says was redeemed, and what lifetime_redeemed counts."""
    acct = rig["db"].loyalty_accounts.find_one({"customer_id": CUST})
    burned = START - int(acct["balance_points"])
    ledger = sum(int(r["points"]) for r in _redeem_rows(rig))
    assert burned == expected_burn, (burned, expected_burn)
    assert ledger == expected_burn, (ledger, expected_burn)
    assert int(acct.get("lifetime_redeemed") or 0) == expected_burn


def test_two_overlapping_presses_of_one_bill_debit_once(rig, monkeypatch):
    """The audit's door: press 2 runs to completion inside press 1's window
    between the replay read and the debit (exactly where the audit paused)."""
    accounts = rig["accounts"]
    real = accounts.try_debit
    second = {}

    def overlapping(*a, **k):
        if not second:
            second["started"] = True

            def run():
                try:
                    second["out"] = _redeem()
                except HTTPException as exc:  # pragma: no cover - asserted below
                    second["out"] = exc

            t = threading.Thread(target=run)
            t.start()
            t.join()
        return real(*a, **k)

    monkeypatch.setattr(accounts, "try_debit", overlapping)
    first = _redeem()

    assert isinstance(second["out"], dict), second["out"]
    assert first["redeemed_points"] == 100 and second["out"]["redeemed_points"] == 100
    _reconcile(rig, 100)
    assert len(_redeem_rows(rig)) == 1
    assert first["txn_id"] == second["out"]["txn_id"]


def _ledger_insert_fails(rig, monkeypatch):
    """The REDEEM insert fails at the COLLECTION, so the real repository
    swallows it into None -- exactly what production does. Returns undo()."""
    coll = rig["txns"].collection
    real = coll.insert_one

    def refuse(doc, *a, **k):
        if doc.get("type") == "REDEEM":
            raise RuntimeError("ledger write unavailable")
        return real(doc, *a, **k)

    monkeypatch.setattr(coll, "insert_one", refuse)
    return lambda: monkeypatch.setattr(coll, "insert_one", real)


def _press_with_ledger_down(rig, monkeypatch, points=100):
    """One press with the ledger down: the error it answered, or None if it
    claimed success (asserted by the first-response test alone, so the others
    each fail for their own reason)."""
    undo = _ledger_insert_fails(rig, monkeypatch)
    try:
        _redeem(points=points)
        return None
    except HTTPException as exc:
        return exc
    finally:
        undo()


def test_ledger_row_lost_after_the_debit_is_never_answered_as_success(rig, monkeypatch):
    """Codex P1, first response: the debit stands and the row did not land, so
    the press must NOT answer 200 -- and not a 4xx either, which the till reads
    as 'no points were taken'. The retry writes the one row and burns no more."""
    exc = _press_with_ledger_down(rig, monkeypatch)
    assert exc is not None, "answered success with no ledger row"
    assert exc.status_code >= 500, exc.detail
    assert _redeem_rows(rig) == []
    acct = rig["db"].loyalty_accounts.find_one({"customer_id": CUST})
    assert int(acct["balance_points"]) == START - 100

    out = _redeem()
    assert out["redeemed_points"] == 100 and out["rupee_value"] == 100.0
    assert len(_redeem_rows(rig)) == 1
    _reconcile(rig, 100)
    # And any later press is the ordinary replay of that one burn.
    again = _redeem()
    assert again.get("replayed") is True and again["txn_id"] == out["txn_id"]
    _reconcile(rig, 100)


def test_same_bill_retried_with_a_different_amount_is_refused(rig, monkeypatch):
    _press_with_ledger_down(rig, monkeypatch, points=100)
    with pytest.raises(HTTPException) as ei:
        _redeem(points=200)
    assert ei.value.status_code == 409
    acct = rig["db"].loyalty_accounts.find_one({"customer_id": CUST})
    assert int(acct["balance_points"]) == START - 100
    assert int(acct["lifetime_redeemed"]) == 100
    assert _redeem_rows(rig) == []


def test_retry_keeps_the_original_rupee_value_after_a_rate_change(rig, monkeypatch):
    """Codex P2: one 100-point debit answered Rs 100; a conversion change before
    the retry must not re-price it to Rs 200 -- in the answer or on the books."""
    _press_with_ledger_down(rig, monkeypatch)
    rig["db"].loyalty_settings.update_one(
        {"_id": "loyalty_settings"}, {"$set": {"redeem_rupee_per_point": 2.0}}, upsert=True
    )
    from api.routers import loyalty as lm

    assert float(lm._settings_safe()["redeem_rupee_per_point"]) == 2.0  # the change is real
    out = _redeem()
    assert out["redeemed_points"] == 100 and out["rupee_value"] == 100.0
    [row] = _redeem_rows(rig)
    assert row["rupee_value"] == 100.0
    assert row["created_by"] == STAFF["user_id"]
    _reconcile(rig, 100)


def test_retry_needs_no_points_left_and_no_recalculation(rig, monkeypatch):
    """The retry completes the debit that landed; a minimum or a balance that
    moved since cannot refuse it."""
    _press_with_ledger_down(rig, monkeypatch)
    rig["db"].loyalty_accounts.update_one({"customer_id": CUST}, {"$set": {"balance_points": 0}})
    rig["db"].loyalty_settings.update_one(
        {"_id": "loyalty_settings"}, {"$set": {"min_redeem_points": 500}}, upsert=True
    )
    out = _redeem()
    assert out["redeemed_points"] == 100 and out["rupee_value"] == 100.0


def test_standalone_goodwill_redeems_stay_separate_redemptions(rig):
    """No order -> no bill identity: two goodwill presses are two burns, as
    the owner allowed before this fix."""
    a = _redeem(points=100, order_id=None)
    b = _redeem(points=100, order_id=None)
    assert a["txn_id"] != b["txn_id"]
    assert len(_redeem_rows(rig)) == 2
    _reconcile(rig, 200)


def test_a_plain_redeem_on_a_bill_burns_what_it_always_did(rig):
    out = _redeem(points=250)
    assert out["redeemed_points"] == 250 and out["rupee_value"] == 250.0
    assert "replayed" not in out
    row = _redeem_rows(rig)[0]
    assert row["txn_id"] == out["txn_id"] and row["order_id"] == ORDER
    assert row["rupee_value"] == 250.0
    _reconcile(rig, 250)


def _debit_lands_then_ack_lost(rig, monkeypatch):
    """The guarded find_one_and_update APPLIES, then its answer is lost (a
    socket timeout): money_guard reports no_atomic, try_debit returns None."""
    coll = rig["accounts"].collection
    real = coll.find_one_and_update

    def landed_then_raised(*a, **k):
        real(*a, **k)
        raise RuntimeError("NetworkTimeout: socket timed out")

    monkeypatch.setattr(coll, "find_one_and_update", landed_then_raised)
    return coll


def test_debit_landed_but_its_ack_lost_answers_with_that_one_burn(rig, monkeypatch):
    """The till must not be told 'no points were taken' (4xx) when they were:
    the press's own landed debit is recognised, its ledger row is written, and
    a later press replays it -- the points are never lost off the books."""
    _debit_lands_then_ack_lost(rig, monkeypatch)
    out = _redeem(points=100)
    assert out["redeemed_points"] == 100 and out["rupee_value"] == 100.0
    [row] = _redeem_rows(rig)
    assert row["txn_id"] == out["txn_id"]
    _reconcile(rig, 100)
    again = _redeem(points=100)
    assert again.get("replayed") is True and again["txn_id"] == out["txn_id"]
    _reconcile(rig, 100)


def test_debit_ack_lost_and_account_unreadable_is_never_a_refusal(rig, monkeypatch):
    """Lost debit answer AND the re-read fails: the outcome is unknown, so the
    till must get an error it treats as 'not confirmed' (5xx), never a 4xx it
    reads as 'this attempt took NO points'."""
    coll = _debit_lands_then_ack_lost(rig, monkeypatch)
    real_find_one = coll.find_one
    dark = {}
    real_faau = coll.find_one_and_update

    def faau(*a, **k):
        try:
            return real_faau(*a, **k)
        finally:
            dark["on"] = True  # after the write: the db goes dark

    def find_one(*a, **k):
        if dark:
            raise RuntimeError("ServerSelectionTimeoutError")
        return real_find_one(*a, **k)

    monkeypatch.setattr(coll, "find_one_and_update", faau)
    monkeypatch.setattr(coll, "find_one", find_one)
    with pytest.raises(Exception) as ei:
        _redeem(points=100)
    exc = ei.value
    assert not (isinstance(exc, HTTPException) and 400 <= exc.status_code < 500), exc.detail
    dark.clear()
    acct = rig["db"].loyalty_accounts.find_one({"customer_id": CUST})
    assert int(acct["balance_points"]) == START - 100  # debited once, no more


# --- Cancellation / return against the durable debit (Codex P1) -------------

def _cancel_order(rig):
    from api.routers.loyalty import reverse_for_cancel

    rig["db"].orders.update_one({"order_id": ORDER}, {"$set": {"status": "CANCELLED"}})
    return reverse_for_cancel(ORDER, CUST)


def _earn_on_order(rig, points=11):
    rig["txns"].create({"txn_id": "earn-1", "customer_id": CUST, "type": "EARN",
                        "points": points, "order_id": ORDER})
    rig["accounts"].adjust_balance(CUST, delta_points=points, delta_lifetime_earned=points)


def _account(rig):
    return rig["db"].loyalty_accounts.find_one({"customer_id": CUST})


def _reversal_rows(rig):
    return list(rig["db"].loyalty_transactions.find({"customer_id": CUST, "type": "ADJUST"}))


def test_cancel_before_the_retry_restores_the_debit_exactly_once(rig, monkeypatch):
    """Codex's exact path: debit, no REDEEM row, cancel, then the retry. The
    cancel hands the points back and completes the ledger; the retry on the
    cancelled bill neither burns nor restores again."""
    _earn_on_order(rig)
    _press_with_ledger_down(rig, monkeypatch)

    out = _cancel_order(rig)
    assert out["ok"] is True and out["redeemed_restored"] == 100 and out["earned_clawed"] == 11
    acct = _account(rig)
    assert int(acct["balance_points"]) == START
    assert int(acct["lifetime_redeemed"]) == 0
    [row] = _redeem_rows(rig)  # the ledger now tells the same story as the account
    assert row["points"] == 100 and row["rupee_value"] == 100.0
    assert len(_reversal_rows(rig)) == 1

    with pytest.raises(HTTPException) as ei:
        _redeem()
    assert ei.value.status_code == 409 and ei.value.detail["reason"] == "order_cancelled"
    _cancel_order(rig)  # the cancel retry door
    acct = _account(rig)
    assert int(acct["balance_points"]) == START and int(acct["lifetime_redeemed"]) == 0
    assert len(_redeem_rows(rig)) == 1 and len(_reversal_rows(rig)) == 1


def test_cancel_with_the_ledger_still_down_restores_and_a_retry_completes_it(rig, monkeypatch):
    """The ledger is down for the cancel too: the debit is still handed back,
    and the row a retry completes later moves no money."""
    undo = _ledger_insert_fails(rig, monkeypatch)
    try:
        _redeem()
    except HTTPException:
        pass
    out = _cancel_order(rig)
    undo()
    assert out["ok"] is True and out["redeemed_restored"] == 100
    assert _redeem_rows(rig) == []
    assert int(_account(rig)["balance_points"]) == START

    with pytest.raises(HTTPException) as ei:
        _redeem()
    assert ei.value.status_code == 409
    assert len(_redeem_rows(rig)) == 1
    acct = _account(rig)
    assert int(acct["balance_points"]) == START and int(acct["lifetime_redeemed"]) == 0
    assert len(_reversal_rows(rig)) == 1


def test_return_before_the_retry_restores_the_debit_once(rig, monkeypatch):
    from api.routers.loyalty import reverse_for_return

    _press_with_ledger_down(rig, monkeypatch)
    out = reverse_for_return("RET-1", ORDER, CUST)
    assert out["ok"] is True and out["redeemed_restored"] == 100
    assert int(_account(rig)["balance_points"]) == START
    reverse_for_return("RET-1", ORDER, CUST)
    reverse_for_return("RET-2", ORDER, CUST)
    acct = _account(rig)
    assert int(acct["balance_points"]) == START and int(acct["lifetime_redeemed"]) == 0
    assert len(_redeem_rows(rig)) == 1 and len(_reversal_rows(rig)) == 1


def test_a_completed_redeem_still_reverses_normally_and_once(rig):
    _earn_on_order(rig)
    _redeem()
    out = _cancel_order(rig)
    assert out["redeemed_restored"] == 100 and out["earned_clawed"] == 11
    _cancel_order(rig)
    acct = _account(rig)
    assert int(acct["balance_points"]) == START and int(acct["lifetime_redeemed"]) == 0
    assert len(_reversal_rows(rig)) == 1


def test_a_redeem_on_an_already_cancelled_bill_takes_nothing(rig):
    _earn_on_order(rig)
    _cancel_order(rig)
    with pytest.raises(HTTPException) as ei:
        _redeem()
    assert ei.value.status_code == 409 and ei.value.detail["reason"] == "order_cancelled"
    assert int(_account(rig)["balance_points"]) == START
    assert _redeem_rows(rig) == []


def _cancel_inside_the_press(rig, monkeypatch):
    """The whole cancel, reversal included, runs between the press's check of
    the bill and its debit -- so that reversal cannot have seen the debit."""
    accounts = rig["accounts"]
    real = accounts.try_debit
    done = {}

    def cancel_first(*a, **k):
        if not done:
            done["out"] = _cancel_order(rig)
        return real(*a, **k)

    monkeypatch.setattr(accounts, "try_debit", cancel_first)
    return done


def test_a_debit_landing_after_the_cancels_reversal_is_handed_back_once(rig, monkeypatch):
    """The reversal's marker would short-circuit every later call: the press
    itself must notice the cancelled bill, and nothing may hand back twice."""
    _earn_on_order(rig)
    done = _cancel_inside_the_press(rig, monkeypatch)
    with pytest.raises(HTTPException) as ei:
        _redeem()
    assert done["out"]["earned_clawed"] == 11 and done["out"].get("redeemed_restored") == 0
    assert ei.value.status_code == 409 and ei.value.detail["reason"] == "order_cancelled"
    acct = _account(rig)
    assert int(acct["balance_points"]) == START and int(acct["lifetime_redeemed"]) == 0
    assert len(_redeem_rows(rig)) == 1

    for _ in range(2):  # the press again, then the cancel retry door
        with pytest.raises(HTTPException):
            _redeem()
        _cancel_order(rig)
    acct = _account(rig)
    assert int(acct["balance_points"]) == START and int(acct["lifetime_redeemed"]) == 0
    assert len(_reversal_rows(rig)) == 2  # the cancel's reversal + the one restore


def _press_races_the_cancels_marker(rig, monkeypatch, cancel_lands_first):
    """Two workers, one bill, in the order that matters. The press passes its
    entry check; the cancel flips the bill, reads the account and the ledger
    (nothing to restore) and stalls at its marker insert; the press debits, sees
    CANCELLED, reads the ledger (no marker yet) and reaches its own insert. Only
    then do the two inserts land, in the order asked for."""
    rig["db"].loyalty_transactions.create_index(  # uniq_loyalty_cancel_reversal
        [("customer_id", 1), ("cancel_of_order_id", 1)],
        unique=True,
        partialFilterExpression={"type": "ADJUST", "cancel_of_order_id": {"$type": "string"}},
    )
    accounts, txns = rig["accounts"], rig["txns"]
    real_debit, real_create = accounts.try_debit, txns.create
    cancel_at_insert, press_at_insert, first_landed = (threading.Event() for _ in range(3))
    run = {"answer": {}}

    def debit(*a, **k):
        if "thread" not in run:
            run["thread"] = threading.Thread(
                target=lambda: run["answer"].update(_cancel_order(rig))
            )
            run["thread"].start()
            assert cancel_at_insert.wait(10)
        return real_debit(*a, **k)

    def create(doc, **k):
        if "cancel_of_order_id" not in doc:
            return real_create(doc, **k)
        is_cancel = threading.current_thread() is run.get("thread")
        if is_cancel:
            cancel_at_insert.set()
            press_at_insert.wait(10)
        else:
            press_at_insert.set()
        if is_cancel != cancel_lands_first:
            first_landed.wait(10)
        try:
            return real_create(doc, **k)
        finally:
            first_landed.set()

    monkeypatch.setattr(accounts, "try_debit", debit)
    monkeypatch.setattr(txns, "create", create)
    with pytest.raises(HTTPException) as ei:
        _redeem()
    run["thread"].join(10)
    assert ei.value.status_code == 409 and ei.value.detail["reason"] == "order_cancelled"
    assert run["answer"]["ok"] is True
    return next(r for r in _reversal_rows(rig) if r.get("cancel_of_order_id"))


def test_a_press_losing_the_marker_race_to_a_blind_cancel_hands_its_debit_back(rig, monkeypatch):
    """The winning marker never saw this debit, so the lost insert settles
    nothing: the 409 says the points are back, and they must be."""
    _earn_on_order(rig)
    marker = _press_races_the_cancels_marker(rig, monkeypatch, cancel_lands_first=True)
    assert marker["redeem_txn_ids"] == []  # the winner really was blind
    acct = _account(rig)
    assert int(acct["balance_points"]) == START and int(acct["lifetime_redeemed"]) == 0
    assert len(_redeem_rows(rig)) == 1
    assert not rig["db"].orders.find_one({"order_id": ORDER}).get("loyalty_reversal_failed")

    for _ in range(2):  # the press again, then the cancel retry door: nothing moves
        with pytest.raises(HTTPException):
            _redeem()
        _cancel_order(rig)
    acct = _account(rig)
    assert int(acct["balance_points"]) == START and int(acct["lifetime_redeemed"]) == 0
    assert len(_reversal_rows(rig)) == 2  # the cancel's reversal + the one restore


def test_a_cancel_losing_the_marker_race_to_the_press_restores_nothing_twice(rig, monkeypatch):
    """The mirror: the press's marker counted the debit, so the cancel's second
    look finds it covered and adds no restore."""
    _earn_on_order(rig)
    marker = _press_races_the_cancels_marker(rig, monkeypatch, cancel_lands_first=False)
    assert len(marker["redeem_txn_ids"]) == 1  # the press won, debit counted
    acct = _account(rig)
    assert int(acct["balance_points"]) == START and int(acct["lifetime_redeemed"]) == 0
    assert len(_reversal_rows(rig)) == 1


def test_a_late_debit_whose_restore_money_failed_is_finished_by_the_next_call(rig, monkeypatch):
    from api.routers import loyalty as lm

    _earn_on_order(rig)
    _cancel_inside_the_press(rig, monkeypatch)
    real = lm._apply_reversal_balance
    monkeypatch.setattr(
        lm, "_apply_reversal_balance",
        lambda a, c, txn_id, *r: ("failed", None) if txn_id.startswith("redeem-restore:")
        else real(a, c, txn_id, *r),
    )
    with pytest.raises(HTTPException) as ei:
        _redeem()
    assert ei.value.status_code == 503  # not a 4xx: the points are not back yet
    assert rig["db"].orders.find_one({"order_id": ORDER})["loyalty_reversal_failed"] is True
    assert int(_account(rig)["balance_points"]) == START - 100
    monkeypatch.setattr(lm, "_apply_reversal_balance", real)
    assert lm.reverse_for_cancel(ORDER, CUST)["ok"] is True
    lm.reverse_for_cancel(ORDER, CUST)
    acct = _account(rig)
    assert int(acct["balance_points"]) == START and int(acct["lifetime_redeemed"]) == 0


def test_a_return_does_not_grow_after_the_fact_for_a_later_debit(rig):
    """The late restore is the CANCEL's: a second partial return stays stopped
    by the order's one reversal, as it always was."""
    from api.routers.loyalty import reverse_for_return

    _earn_on_order(rig)
    reverse_for_return("RET-1", ORDER, CUST)
    _redeem()
    reverse_for_return("RET-2", ORDER, CUST)
    assert int(_account(rig)["balance_points"]) == START - 100


def test_goodwill_that_cannot_be_recorded_is_handed_back(rig, monkeypatch):
    """No bill key, so a retry would burn again: a goodwill press whose row did
    not land must neither answer success nor keep the points."""
    _ledger_insert_fails(rig, monkeypatch)
    with pytest.raises(HTTPException) as ei:
        _redeem(order_id=None)
    assert ei.value.status_code >= 500
    acct = _account(rig)
    assert int(acct["balance_points"]) == START and int(acct["lifetime_redeemed"]) == 0


def test_a_loyalty_tender_completes_the_unconfirmed_burn(rig, monkeypatch):
    """The till answered 'not confirmed' and told staff to have an admin record
    the loyalty payment: that tender must find the real burn, at its own value,
    and one burn still backs only one tender."""
    from api import dependencies
    from api.routers.orders.payments import _claim_loyalty_redemption

    monkeypatch.setattr(dependencies, "get_loyalty_transaction_repository", lambda: rig["txns"])
    _press_with_ledger_down(rig, monkeypatch)
    order = {"order_id": ORDER, "customer_id": CUST, "payments": []}
    txn_id, twin = _claim_loyalty_redemption(order, 100.0, "pay-1")
    assert twin is None
    [row] = _redeem_rows(rig)
    assert row["txn_id"] == txn_id and row["tender_payment_id"] == "pay-1"
    with pytest.raises(HTTPException):
        _claim_loyalty_redemption(order, 100.0, "pay-2")
    _reconcile(rig, 100)


# --- Account unreadable during a reversal (Codex round 3 P1) ----------------

class _FlakyReads:
    """The account collection with its find_one failing -- a wrapper, because
    mongomock's own writes go through find_one and a real server's do not."""

    def __init__(self, coll, fails):
        self._coll, self._fails = coll, fails

    def __getattr__(self, name):
        return getattr(self._coll, name)

    def find_one(self, *a, **k):
        if self._fails():
            raise RuntimeError("AutoReconnect: account reads unavailable")
        return self._coll.find_one(*a, **k)


def _account_reads_down(rig, monkeypatch):
    """Every account read fails at the COLLECTION, while the row and its debit
    stand -- the real repository swallows that into a zero-balance seed. The
    bill earned nothing, so nothing else can betray the debit. Returns undo()."""
    real = rig["accounts"].collection
    monkeypatch.setattr(rig["accounts"], "collection", _FlakyReads(real, lambda: True))
    return lambda: monkeypatch.setattr(rig["accounts"], "collection", real)


def _assert_restored_once(rig):
    acct = _account(rig)
    assert int(acct["balance_points"]) == START and int(acct["lifetime_redeemed"]) == 0
    assert len(_redeem_rows(rig)) == 1 and len(_reversal_rows(rig)) == 1


def test_unreadable_account_is_never_read_as_no_debit_and_the_retry_restores_once(rig, monkeypatch):
    """Codex's path: no EARN, durable debit, no REDEEM row, account unreadable.
    The answer must be a failure (the debit cannot be ruled out), nothing moves,
    and the next call hands the points back exactly once."""
    _press_with_ledger_down(rig, monkeypatch)
    undo = _account_reads_down(rig, monkeypatch)
    out = _cancel_order(rig)
    undo()

    assert out["ok"] is False and out["reason"] == "account_read_failed"
    assert int(_account(rig)["balance_points"]) == START - 100
    assert _reversal_rows(rig) == []

    retry = _cancel_order(rig)
    assert retry["ok"] is True and retry["redeemed_restored"] == 100
    _assert_restored_once(rig)
    _cancel_order(rig)
    _assert_restored_once(rig)


def _cancel_route(rig, monkeypatch):
    """The real cancel door over the real repositories."""
    import api.services.audit_alerts as alerts
    import api.services.lens_stock_hook as hook
    from api.routers import loyalty as lm
    from api.routers import orders as om
    from database.repositories.product_repository import StockRepository

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(om, "get_order_repository", lm.get_order_repository)
    monkeypatch.setattr(om, "get_stock_repository",
                        lambda: StockRepository(rig["db"].get_collection("stock_units")))
    monkeypatch.setattr(om, "validate_store_access", lambda *a, **k: None)
    monkeypatch.setattr(hook, "release_for_cancel", noop)
    monkeypatch.setattr(alerts, "alert_order_cancelled", noop)
    rig["db"].orders.update_one(
        {"order_id": ORDER}, {"$set": {"status": "CONFIRMED", "order_number": "SO-1", "items": []}}
    )
    admin = {"user_id": "u-admin", "username": "admin", "roles": ["SUPERADMIN"],
             "store_ids": ["BV-TEST-01"], "active_store_id": "BV-TEST-01"}
    return lambda: asyncio.run(om.cancel_order(ORDER, reason="customer changed mind", current_user=admin))


def test_the_cancel_door_flags_the_unreadable_account_and_its_retry_restores_once(rig, monkeypatch):
    """The report's stranding: 200 with no failure flag closed the retry door
    (400 already cancelled) with 100 points gone. The door stays open until the
    money is back, and only then closes."""
    cancel = _cancel_route(rig, monkeypatch)
    _press_with_ledger_down(rig, monkeypatch)
    undo = _account_reads_down(rig, monkeypatch)
    first = cancel()
    undo()

    assert first["status"] == "CANCELLED" and first["loyalty_reversal_failed"] is True
    stored = rig["db"].orders.find_one({"order_id": ORDER})
    assert stored["status"] == "CANCELLED" and stored["loyalty_reversal_failed"] is True
    assert stored["loyalty_reversal"]["reason"] == "account_read_failed"
    assert int(_account(rig)["balance_points"]) == START - 100

    retry = cancel()
    assert retry["loyalty_reversal_failed"] is False
    assert rig["db"].orders.find_one({"order_id": ORDER})["loyalty_reversal_failed"] is False
    _assert_restored_once(rig)

    with pytest.raises(HTTPException) as ei:
        cancel()
    assert ei.value.status_code == 400
    _assert_restored_once(rig)


def test_a_return_on_an_unreadable_account_fails_loudly_and_restores_once(rig, monkeypatch):
    """Same engine, the return door: flagged, never a quiet zero."""
    from api.routers.loyalty import reverse_for_return

    _press_with_ledger_down(rig, monkeypatch)
    undo = _account_reads_down(rig, monkeypatch)
    out = reverse_for_return("RET-1", ORDER, CUST)
    undo()
    assert out["ok"] is False and out["reason"] == "account_read_failed"
    assert int(_account(rig)["balance_points"]) == START - 100

    assert reverse_for_return("RET-1", ORDER, CUST)["redeemed_restored"] == 100
    _assert_restored_once(rig)


def test_a_late_debit_is_not_lost_to_an_unreadable_account(rig, monkeypatch):
    """The marker branch: the cancel's reversal exists, a debit landed after it,
    and the account cannot be read -- that must not settle as ok."""
    _earn_on_order(rig)
    assert _cancel_order(rig)["ok"] is True
    rig["accounts"].try_debit(
        CUST, 100, delta_lifetime_redeemed=100, idempotency_key=f"redeem:{ORDER}",
        ledger_extra={"order_id": ORDER, "requested_points": 100, "rupee_value": 100.0},
    )
    undo = _account_reads_down(rig, monkeypatch)
    out = _cancel_order(rig)
    undo()
    assert out["ok"] is False and out["reason"] == "account_read_failed"
    assert int(_account(rig)["balance_points"]) == START - 100

    assert _cancel_order(rig)["redeemed_restored"] == 100
    assert int(_account(rig)["balance_points"]) == START
    assert _cancel_order(rig)["ok"] is True
    assert int(_account(rig)["balance_points"]) == START


def test_an_absent_account_is_still_nothing_to_reverse(rig, monkeypatch):
    """Control: ABSENT is not UNAVAILABLE. A customer with no account and no
    ledger reverses to ok/zero with the empty row created as ever; only a read
    that FAILED refuses."""
    from api.routers.loyalty import reverse_for_cancel

    assert reverse_for_cancel(ORDER, "cust-never-seen") == {
        "ok": True, "earned_clawed": 0, "redeemed_restored": 0, "net_delta": 0,
    }
    assert rig["db"].loyalty_accounts.find_one({"customer_id": "cust-never-seen"})["balance_points"] == 0

    undo = _account_reads_down(rig, monkeypatch)
    out = reverse_for_cancel(ORDER, "cust-also-unseen")
    undo()
    assert out["ok"] is False and out["reason"] == "account_read_failed"
    assert rig["db"].loyalty_accounts.find_one({"customer_id": "cust-also-unseen"}) is None


def test_a_retry_press_on_an_unreadable_account_is_never_a_refusal(rig, monkeypatch):
    """The debit landed, its row did not, and the till's retry meets the same
    outage: a zero-balance seed answered 400 'insufficient', which the till
    reads as 'this attempt took NO points'. It must be a 5xx, and nothing moves."""
    _press_with_ledger_down(rig, monkeypatch)
    undo = _account_reads_down(rig, monkeypatch)
    with pytest.raises(Exception) as ei:
        _redeem()
    undo()
    exc = ei.value
    assert not (isinstance(exc, HTTPException) and 400 <= exc.status_code < 500), exc.detail
    assert int(_account(rig)["balance_points"]) == START - 100

    assert _redeem()["redeemed_points"] == 100
    _reconcile(rig, 100)


def test_finishing_a_stranded_reversal_on_a_failed_read_still_drops_the_tier(rig, monkeypatch):
    """Marker written, money not moved, tier owed. The finishing pass moves the
    money under a guard that can never run again, so a seed standing in for the
    account at that moment left the bought tier on the customer for good."""
    from api.routers import loyalty as lm

    monkeypatch.setattr(lm, "_settings_safe", lambda: {"tier_thresholds": {"GOLD": 5000}})
    rig["db"].loyalty_accounts.update_one(
        {"customer_id": CUST}, {"$set": {"tier": "GOLD", "lifetime_earned": 4950}}
    )
    _earn_on_order(rig, points=100)
    coll = rig["accounts"].collection
    real_write = coll.find_one_and_update

    def write_down(*a, **k):
        raise RuntimeError("write unavailable")

    monkeypatch.setattr(coll, "find_one_and_update", write_down)
    assert _cancel_order(rig)["reason"] == "balance_update_failed"
    monkeypatch.setattr(coll, "find_one_and_update", real_write)

    reads = []

    def second_read_on():
        reads.append(1)
        return len(reads) > 1

    monkeypatch.setattr(rig["accounts"], "collection", _FlakyReads(coll, second_read_on))
    assert _cancel_order(rig)["ok"] is True
    monkeypatch.setattr(rig["accounts"], "collection", coll)

    acct = _account(rig)
    assert int(acct["balance_points"]) == START and acct["tier"] == "BRONZE"


# --- Bill unreadable at the cancelled-bill guard (review of 922bb6f) ---------

def _order_reads_down(rig, monkeypatch, fails=lambda: True):
    """Order reads fail at the COLLECTION, while the bill stands -- the real
    repository's find_by_id swallows that into None. Returns undo()."""
    from api.routers import loyalty as lm

    orders = lm.get_order_repository()
    real = orders.collection
    monkeypatch.setattr(orders, "collection", _FlakyReads(real, fails))
    return lambda: monkeypatch.setattr(orders, "collection", real)


def _order_reads_down_from_the_debit(rig, monkeypatch):
    """The front door reads the bill; from the debit on it cannot. Returns undo()."""
    accounts = rig["accounts"]
    real = accounts.try_debit
    landed = []

    def debit(*a, **k):
        out = real(*a, **k)
        landed.append(True)
        return out

    monkeypatch.setattr(accounts, "try_debit", debit)
    return _order_reads_down(rig, monkeypatch, fails=lambda: bool(landed))


def _assert_unconfirmed(exc):
    """A 5xx or a raised read: anything but the 4xx the till reads as 'NO points'."""
    assert not (isinstance(exc, HTTPException) and 400 <= exc.status_code < 500), exc.detail


def _assert_cancelled_and_restored_once(rig):
    with pytest.raises(HTTPException) as ei:
        _redeem()
    assert ei.value.status_code == 409 and ei.value.detail["reason"] == "order_cancelled"
    acct = _account(rig)
    assert int(acct["balance_points"]) == START and int(acct["lifetime_redeemed"]) == 0
    assert len(_redeem_rows(rig)) == 1
    # The cancel's own (empty) reversal + the one restore.
    assert sum(abs(int(r.get("points") or 0)) for r in _reversal_rows(rig)) == 100


def test_a_late_debit_on_an_unreadable_bill_is_never_answered_as_success(rig, monkeypatch):
    """No EARN; the cancel's reversal ran before the debit; the post-debit guard
    then cannot read the bill. A 200 stranded the points: cancel answers 'already
    cancelled' and the till has no reason to press again."""
    _cancel_inside_the_press(rig, monkeypatch)
    undo = _order_reads_down_from_the_debit(rig, monkeypatch)
    with pytest.raises(Exception) as ei:
        _redeem()
    undo()
    _assert_unconfirmed(ei.value)
    assert int(_account(rig)["balance_points"]) == START - 100

    _assert_cancelled_and_restored_once(rig)
    _assert_cancelled_and_restored_once(rig)


def test_a_retry_of_a_late_debit_on_an_unreadable_bill_is_never_a_success(rig, monkeypatch):
    """The lost-answer variant: the debit landed after the reversal, its row did
    not, and the retry meets the outage at the front door."""
    _cancel_inside_the_press(rig, monkeypatch)
    _press_with_ledger_down(rig, monkeypatch)
    assert int(_account(rig)["balance_points"]) == START - 100 and _redeem_rows(rig) == []

    undo = _order_reads_down(rig, monkeypatch)
    with pytest.raises(Exception) as ei:
        _redeem()
    undo()
    _assert_unconfirmed(ei.value)
    assert int(_account(rig)["balance_points"]) == START - 100

    _assert_cancelled_and_restored_once(rig)


def test_a_live_bill_unreadable_after_the_debit_replays_the_one_burn(rig, monkeypatch):
    """Control: the guard cannot tell cancelled from live, so it says so -- and on
    a live bill the retry answers the same burn, nothing debited twice."""
    undo = _order_reads_down_from_the_debit(rig, monkeypatch)
    with pytest.raises(Exception) as ei:
        _redeem()
    undo()
    _assert_unconfirmed(ei.value)

    out = _redeem()
    assert out["redeemed_points"] == 100 and out["replayed"] is True
    _reconcile(rig, 100)
    assert rig["db"].audit_logs.count_documents({"action": "loyalty.redeem"}) == 1


def test_a_fresh_press_on_an_unreadable_bill_takes_nothing_and_says_unconfirmed(rig, monkeypatch):
    """At the guard and at the bill's ceiling alike, UNKNOWN is not 'Order not
    found': no points move, and a healthy retry burns as ever."""
    for reads_before_outage in (0, 1):
        seen = []
        undo = _order_reads_down(
            rig, monkeypatch,
            fails=lambda n=reads_before_outage: seen.append(1) or len(seen) > n,
        )
        with pytest.raises(Exception) as ei:
            _redeem()
        undo()
        _assert_unconfirmed(ei.value)
        assert int(_account(rig)["balance_points"]) == START and _redeem_rows(rig) == []

    assert _redeem()["redeemed_points"] == 100
    _reconcile(rig, 100)


def test_a_missing_bill_is_still_not_found(rig):
    """Control: ABSENT is not UNAVAILABLE."""
    with pytest.raises(HTTPException) as ei:
        _redeem(order_id="ORD-NOT-THERE")
    assert ei.value.status_code == 404
    assert int(_account(rig)["balance_points"]) == START
