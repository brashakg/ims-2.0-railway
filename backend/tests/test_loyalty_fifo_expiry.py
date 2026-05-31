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


def test_endpoint_wires_the_helper():
    """The /expire endpoint must call the FIFO helper (no min(points,balance))."""
    import inspect

    import api.routers.loyalty as loyalty

    src = inspect.getsource(loyalty.expire_sweep)
    assert "expirable_points_by_lot" in src
    assert "min(int(row.get" not in src  # the old buggy cap is gone
