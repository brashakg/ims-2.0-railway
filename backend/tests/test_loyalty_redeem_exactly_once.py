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
  * the ledger row lost after the debit (lost ack / process death) -> the
    retry writes the one missing row and debits nothing more;
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


def test_ledger_row_lost_after_the_debit_retry_does_not_debit_again(rig, monkeypatch):
    """Lost acknowledgement / process death between the debit and the ledger
    insert: the retry finds no REDEEM row, but the debit already carries the
    bill's key -- it writes the one missing row and burns nothing more."""
    txns = rig["txns"]
    real_create = txns.create
    calls = []

    def lost_once(doc, **kw):
        calls.append(doc)
        if len(calls) == 1:
            return None  # the insert never landed
        return real_create(doc, **kw)

    monkeypatch.setattr(txns, "create", lost_once)
    _redeem()
    assert _redeem_rows(rig) == []
    out = _redeem()
    assert out["redeemed_points"] == 100
    assert len(_redeem_rows(rig)) == 1
    _reconcile(rig, 100)
    # And any later press is the ordinary replay of that one burn.
    again = _redeem()
    assert again.get("replayed") is True and again["txn_id"] == out["txn_id"]
    _reconcile(rig, 100)


def test_same_bill_retried_with_a_different_amount_is_refused(rig, monkeypatch):
    txns = rig["txns"]
    monkeypatch.setattr(txns, "create", lambda doc, **kw: None)  # ledger lost
    _redeem(points=100)
    with pytest.raises(HTTPException) as ei:
        _redeem(points=200)
    assert ei.value.status_code == 409
    acct = rig["db"].loyalty_accounts.find_one({"customer_id": CUST})
    assert int(acct["balance_points"]) == START - 100
    assert int(acct["lifetime_redeemed"]) == 100


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
    row = _redeem_rows(rig)[0]
    assert row["txn_id"] == out["txn_id"] and row["order_id"] == ORDER
    assert row["rupee_value"] == 250.0
    _reconcile(rig, 250)
