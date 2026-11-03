"""
IMS 2.0 — referral redeem must be role-gated AND idempotent
============================================================
POST /marketing/referrals/{id}/redeem credits the referrer real store credit.
Two holes (audit M1):
  1. It was only Depends(get_current_user) — ANY logged-in user (down to a
     cashier) could credit ₹500. Now gated to the money-minting _REWARD_ROLES.
  2. It $inc'd the reward then set status without ever CHECKING status — so a
     repeat call (double-click / retry / abuse) minted the reward again and
     again. Now the credit is claimed atomically (find_one_and_update on
     status != REWARD_CREDITED); the wallet is credited only when the claim wins.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-referral-redeem")


def _run(db, roles=("STORE_MANAGER",), referral_id="REF-1"):
    import api.routers.marketing as mk

    mk_db = db
    orig = mk._get_db
    mk._get_db = lambda: mk_db
    try:
        user = {"user_id": "u1", "roles": list(roles)}
        # asyncio.run() uses a FRESH loop per call. get_event_loop() can reuse a
        # loop an earlier async test left closed -> "RuntimeError: Event loop is
        # closed" in the full suite (passes in isolation).
        return asyncio.run(mk.redeem_referral(referral_id, user))
    finally:
        mk._get_db = orig


def _strict_db(monkeypatch):
    """Since PR #1264 the reward goes through THE store-credit rule
    (customers.post_ledger_credit: a ledger row + one atomic $inc), so the
    wallet is read off a strict fake, not counted $inc calls."""
    from database.repositories.customer_repository import CustomerRepository
    from tests.strict_fakes import StrictDB

    db = StrictDB()
    db.get_collection("referrals").insert_one(
        {"referral_id": "REF-1", "status": "INVITED", "reward_amount": 500, "referrer_customer_id": "CUST-9"})
    db.get_collection("customers").insert_one({"customer_id": "CUST-9", "store_credit": 0})

    class _Conn:
        is_connected = True

        def get_collection(self, name):
            return db.get_collection(name)

    monkeypatch.setattr("api.dependencies.get_db", lambda: _Conn(), raising=False)
    monkeypatch.setattr("api.dependencies.get_customer_repository",
                        lambda: CustomerRepository(db.get_collection("customers")))
    return db


def _wallet(db):
    rows = list(db.get_collection("credit_note_ledger").find({"customer_id": "CUST-9"}))
    return db.get_collection("customers").find_one({"customer_id": "CUST-9"})["store_credit"], rows


def test_first_redeem_credits_once(monkeypatch):
    db = _strict_db(monkeypatch)
    out = _run(db)
    assert "credited" in out["message"].lower()
    balance, rows = _wallet(db)
    assert balance == 500 and [r["delta"] for r in rows] == [500.0]  # wallet credited exactly once
    assert db.get_collection("referrals").find_one({"referral_id": "REF-1"})["status"] == "REWARD_CREDITED"


def test_repeat_redeem_does_not_double_credit(monkeypatch):
    db = _strict_db(monkeypatch)
    _run(db)
    out2 = _run(db)  # second call
    assert out2.get("already_credited") is True
    # still only ONE wallet credit across both calls
    balance, rows = _wallet(db)
    assert balance == 500 and len(rows) == 1


def test_role_gate_blocks_non_reward_roles():
    # The endpoint's dependency is require_roles(*_REWARD_ROLES); a cashier/sales
    # role is not in that set, so the dependency would 403. Assert the set itself.
    import api.routers.marketing as mk

    assert "SALES_CASHIER" not in mk._REWARD_ROLES
    assert "SALES_STAFF" not in mk._REWARD_ROLES
    assert "WORKSHOP_STAFF" not in mk._REWARD_ROLES
    # money-minting roles ARE allowed
    for r in ("ACCOUNTANT", "STORE_MANAGER", "AREA_MANAGER", "ADMIN"):
        assert r in mk._REWARD_ROLES
