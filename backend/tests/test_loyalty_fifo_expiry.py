"""
IMS 2.0 — Loyalty expiry must be per-lot FIFO (initiative P2-C)
===============================================================
The old expiry sweep expired min(lot.points, account_balance) for each expired
EARN lot. The account balance can belong to NEWER, non-expired lots, so:

  earn 100 (lot A, expires day 30) -> redeem 100 -> earn 50 (lot B, valid)

On day 31 the old sweep would expire min(100, 50) = 50 -- destroying 50 of the
VALID lot B. FIFO accounting: the redeem already consumed lot A, so lot A has 0
unspent points to expire and lot B is untouched.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-loyalty-fifo")


def _earn(txn_id, points, created, expires, expired=False):
    return {"txn_id": txn_id, "type": "EARN", "points": points,
            "created_at": created, "expires_at": expires, "expired": expired}


def _redeem(txn_id, points, created):
    return {"txn_id": txn_id, "type": "REDEEM", "points": points, "created_at": created}


def test_spent_old_lot_does_not_expire_newer_valid_lot():
    from api.services.loyalty_engine import expirable_points_by_lot

    t0 = datetime(2026, 1, 1)
    now = datetime(2026, 2, 1)
    ledger = [
        _earn("A", 100, t0, expires=datetime(2026, 1, 31)),          # old, EXPIRED
        _redeem("R", 100, t0 + timedelta(days=2)),                   # spent lot A
        _earn("B", 50, t0 + timedelta(days=3), expires=datetime(2026, 6, 1)),  # valid
    ]
    out = expirable_points_by_lot(ledger, now)
    # Lot A was fully spent -> 0 to expire; lot B is valid -> not in result.
    assert out == {}, f"expected nothing to expire, got {out}"


def test_partially_spent_expired_lot_sheds_only_remainder():
    from api.services.loyalty_engine import expirable_points_by_lot

    t0 = datetime(2026, 1, 1)
    now = datetime(2026, 2, 1)
    ledger = [
        _earn("A", 100, t0, expires=datetime(2026, 1, 31)),  # expired
        _redeem("R", 30, t0 + timedelta(days=2)),            # 30 spent off A
    ]
    out = expirable_points_by_lot(ledger, now)
    assert out == {"A": 70}, f"expected 70 remaining to expire, got {out}"


def test_unexpired_lot_never_expires():
    from api.services.loyalty_engine import expirable_points_by_lot

    now = datetime(2026, 2, 1)
    ledger = [_earn("A", 100, datetime(2026, 1, 1), expires=datetime(2026, 12, 1))]
    assert expirable_points_by_lot(ledger, now) == {}


def test_already_swept_lot_excluded():
    from api.services.loyalty_engine import expirable_points_by_lot

    now = datetime(2026, 2, 1)
    ledger = [_earn("A", 100, datetime(2026, 1, 1),
                    expires=datetime(2026, 1, 15), expired=True)]
    assert expirable_points_by_lot(ledger, now) == {}


def test_fifo_consumes_oldest_first_across_two_expired_lots():
    from api.services.loyalty_engine import expirable_points_by_lot

    t0 = datetime(2026, 1, 1)
    now = datetime(2026, 3, 1)
    ledger = [
        _earn("A", 100, t0, expires=datetime(2026, 2, 1)),                    # expired
        _earn("B", 100, t0 + timedelta(days=5), expires=datetime(2026, 2, 10)),  # expired
        _redeem("R", 120, t0 + timedelta(days=6)),  # consumes all of A (100) + 20 of B
    ]
    out = expirable_points_by_lot(ledger, now)
    # A fully spent -> not present; B has 80 left -> expires 80.
    assert out == {"B": 80}, f"got {out}"


# ---------------------------------------------------------------------------
# Endpoint-level behaviour
# ---------------------------------------------------------------------------
# The previous test asserted that the string "expirable_points_by_lot" appeared
# in inspect.getsource(expire_sweep) and that "min(int(row.get" did not. That
# proves neither that the helper is CALLED nor that the customer's balance ends
# up right -- and a source lookup that desynchronises mid-suite can silently
# point the assertion at another function entirely (see tests/source_guard.py).
# These tests run the real sweep over strict in-memory collections and assert
# the money outcome, which differs numerically between the old and new logic.


import pytest  # noqa: E402


@pytest.fixture
def loyalty_env(monkeypatch):
    """Real loyalty repositories over strict in-memory collections."""
    from api.routers import loyalty as loyalty_mod
    from database.repositories.loyalty_repository import (
        LoyaltyAccountRepository,
        LoyaltyTransactionRepository,
    )
    from strict_fakes import StrictCollection

    txn_coll = StrictCollection("loyalty_transactions")
    acct_coll = StrictCollection("loyalty_accounts")
    txns = LoyaltyTransactionRepository(txn_coll)
    accounts = LoyaltyAccountRepository(acct_coll)

    monkeypatch.setattr(
        loyalty_mod, "get_loyalty_transaction_repository", lambda: txns
    )
    monkeypatch.setattr(loyalty_mod, "get_loyalty_account_repository", lambda: accounts)
    monkeypatch.setattr(loyalty_mod, "get_audit_repository", lambda: None)
    return {"txns": txn_coll, "accounts": acct_coll}


def _seed_spent_old_lot_then_fresh_lot(env, customer_id="cust-fifo"):
    """earn 100 (expired) -> redeem 100 -> earn 50 (still valid).

    Balance is 50, and every one of those points belongs to the VALID lot B.
    The old min(lot.points, balance) rule would expire 50 -- wiping lot B.
    """
    t0 = datetime(2026, 1, 1)
    for row in (
        {**_earn("A", 100, t0, expires=datetime(2026, 1, 31)), "customer_id": customer_id},
        {**_redeem("R", 100, t0 + timedelta(days=2)), "customer_id": customer_id},
        {
            **_earn("B", 50, t0 + timedelta(days=3), expires=datetime(2026, 12, 1)),
            "customer_id": customer_id,
        },
    ):
        env["txns"].insert_one(row)
    env["accounts"].insert_one(
        {"customer_id": customer_id, "balance_points": 50, "lifetime_earned": 150}
    )


def test_sweep_does_not_destroy_a_valid_lot(client, auth_headers, loyalty_env):
    """BEHAVIOURAL: the customer keeps the 50 points they actually still hold."""
    _seed_spent_old_lot_then_fresh_lot(loyalty_env)

    resp = client.post("/api/v1/loyalty/expire", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["points_expired"] == 0, (
        "the expired lot was already fully redeemed, so nothing may be expired; "
        f"the old min(lot, balance) rule would have destroyed 50. got {body}"
    )
    account = loyalty_env["accounts"].docs[0]
    assert account["balance_points"] == 50, account
    assert not [t for t in loyalty_env["txns"].docs if t.get("type") == "EXPIRE"]
    # The spent lot is still marked processed so a later sweep skips it.
    lot_a = next(t for t in loyalty_env["txns"].docs if t["txn_id"] == "A")
    assert lot_a.get("expired") is True


def test_sweep_expires_only_the_unspent_remainder(client, auth_headers, loyalty_env):
    """A partly-redeemed expired lot sheds exactly what it still holds."""
    t0 = datetime(2026, 1, 1)
    for row in (
        {**_earn("A", 100, t0, expires=datetime(2026, 1, 31)), "customer_id": "cust-part"},
        {**_redeem("R", 30, t0 + timedelta(days=2)), "customer_id": "cust-part"},
    ):
        loyalty_env["txns"].insert_one(row)
    loyalty_env["accounts"].insert_one(
        {"customer_id": "cust-part", "balance_points": 70, "lifetime_earned": 100}
    )

    body = client.post("/api/v1/loyalty/expire", headers=auth_headers).json()
    assert body["points_expired"] == 70, body
    assert body["expired_txns"] == 1, body

    expire_rows = [t for t in loyalty_env["txns"].docs if t.get("type") == "EXPIRE"]
    assert len(expire_rows) == 1
    assert expire_rows[0]["points"] == 70
    assert expire_rows[0]["source_earn_txn_id"] == "A"
    assert loyalty_env["accounts"].docs[0]["balance_points"] == 0


def test_sweep_leaves_unexpired_lots_alone(client, auth_headers, loyalty_env):
    loyalty_env["txns"].insert_one(
        {
            **_earn("A", 100, datetime(2026, 1, 1), expires=datetime(2099, 1, 1)),
            "customer_id": "cust-valid",
        }
    )
    loyalty_env["accounts"].insert_one(
        {"customer_id": "cust-valid", "balance_points": 100}
    )

    body = client.post("/api/v1/loyalty/expire", headers=auth_headers).json()
    assert body == {"expired_txns": 0, "points_expired": 0}, body
    assert loyalty_env["accounts"].docs[0]["balance_points"] == 100


def test_sweep_is_idempotent(client, auth_headers, loyalty_env):
    """A second sweep must not expire the same lot twice."""
    t0 = datetime(2026, 1, 1)
    loyalty_env["txns"].insert_one(
        {**_earn("A", 40, t0, expires=datetime(2026, 1, 31)), "customer_id": "cust-idem"}
    )
    loyalty_env["accounts"].insert_one(
        {"customer_id": "cust-idem", "balance_points": 40}
    )

    first = client.post("/api/v1/loyalty/expire", headers=auth_headers).json()
    assert first["points_expired"] == 40, first
    second = client.post("/api/v1/loyalty/expire", headers=auth_headers).json()
    assert second == {"expired_txns": 0, "points_expired": 0}, second
    assert loyalty_env["accounts"].docs[0]["balance_points"] == 0
