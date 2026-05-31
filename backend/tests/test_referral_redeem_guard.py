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


# --- Fake Mongo collections -------------------------------------------------
class FakeColl:
    def __init__(self, docs=None):
        self.docs = docs or []
        self.inc_calls = []  # (filter, update) for customers.update_one $inc

    def find_one(self, flt):
        for d in self.docs:
            if all(d.get(k) == v for k, v in flt.items() if not isinstance(v, dict)):
                return d
        return None

    def find_one_and_update(self, flt, update, return_document=None):
        # Honour a {"status": {"$ne": "REWARD_CREDITED"}} guard.
        rid = flt.get("referral_id")
        for d in self.docs:
            if d.get("referral_id") != rid:
                continue
            ne = flt.get("status", {})
            if isinstance(ne, dict) and "$ne" in ne and d.get("status") == ne["$ne"]:
                return None  # already credited -> claim fails
            d.update(update["$set"])
            return d
        return None

    def update_one(self, flt, update):
        self.inc_calls.append((flt, update))

        class _R:
            modified_count = 1

        return _R()


class FakeDB:
    def __init__(self, referrals, customers):
        self._c = {"referrals": referrals, "customers": customers}

    def get_collection(self, name):
        return self._c[name]


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


def _fresh_db():
    referrals = FakeColl([
        {"referral_id": "REF-1", "status": "INVITED", "reward_amount": 500,
         "referrer_customer_id": "CUST-9"},
    ])
    customers = FakeColl([{"customer_id": "CUST-9", "store_credit": 0}])
    return FakeDB(referrals, customers), referrals, customers


def test_first_redeem_credits_once():
    db, referrals, customers = _fresh_db()
    out = _run(db)
    assert "credited" in out["message"].lower()
    assert len(customers.inc_calls) == 1  # wallet credited exactly once
    assert referrals.docs[0]["status"] == "REWARD_CREDITED"


def test_repeat_redeem_does_not_double_credit():
    db, referrals, customers = _fresh_db()
    _run(db)
    out2 = _run(db)  # second call
    assert out2.get("already_credited") is True
    # still only ONE wallet credit across both calls
    assert len(customers.inc_calls) == 1


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
