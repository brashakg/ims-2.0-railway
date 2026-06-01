"""
Period-lock enforcement (Finance Phase 4)
=========================================
Once a finance period is locked, expenses dated in that month can't be created
(or approved). Integration-level: requires Mongo (CI mongo:7.0). Uses a unique
far-future period so the shared test DB stays isolated.
"""

import os

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")


def _db_available(client) -> bool:
    """Return True only when the shared MongoDB is actually reachable."""
    try:
        from database.connection import get_db
        db = get_db()
        return bool(db and getattr(db, "is_connected", False))
    except Exception:
        return False


def test_locked_period_blocks_expense_create(client, auth_headers):
    if not _db_available(client):
        pytest.skip("MongoDB not available in this environment")

    yr = 2098
    # Lock a unique far-future period (idempotent: 400 if a prior run locked it).
    lk = client.post(
        "/api/v1/finance/period-lock", params={"month": 3, "year": yr}, headers=auth_headers
    )
    assert lk.status_code in (200, 400), lk.text

    # Expense dated in the locked month -> 423 Locked.
    blocked = client.post(
        "/api/v1/expenses",
        json={"category": "TRAVEL", "amount": 100, "description": "should block",
              "expense_date": f"{yr}-03-15"},
        headers=auth_headers,
    )
    assert blocked.status_code == 423, blocked.text

    # Expense in an unlocked month -> created.
    ok = client.post(
        "/api/v1/expenses",
        json={"category": "TRAVEL", "amount": 100, "description": "ok",
              "expense_date": f"{yr}-06-15"},
        headers=auth_headers,
    )
    assert ok.status_code == 201, ok.text
