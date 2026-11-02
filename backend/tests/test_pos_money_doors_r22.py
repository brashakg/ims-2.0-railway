"""
IMS 2.0 - POS money doors, round-22 money review of PR #1264 (head 032fb785)
============================================================================
The give-back is fenced on the ORDER: a till write that lands late after a
takeover is refused there (no timing assumption). A walk-in bill's dead
voucher is paid back in cash through THE cash rule, once, and its leg is never
let go. The give-back door's store check is pinned. No emoji.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import HTTPException  # noqa: E402

from api.routers import customers as cust_router  # noqa: E402
from api.routers import returns as returns_router  # noqa: E402
from tests.test_pos_money_doors import _ledger, _user, till  # noqa: E402,F401
from tests.test_pos_money_doors_r12 import tasks  # noqa: E402,F401
from tests.test_pos_money_doors_r17 import KEY, _balance, redeem_env  # noqa: E402,F401
from tests.test_pos_money_doors_r20 import VKEY, _unbilled_leg  # noqa: E402
from tests.test_pos_money_doors_r21 import (  # noqa: E402,F401
    LEG, _age, _dead_voucher, _lose_first_press, _paid, _press, lost_leg)


# =========================================================================== #
# B: the order fence -- a late till write after a takeover is refused
# =========================================================================== #
@pytest.mark.asyncio
async def test_a_till_write_stalled_past_the_lease_cannot_bill_a_given_back_leg(lost_leg, monkeypatch):
    """The reviewers' stall probe: the till won the leg, then stalled; the
    lease ran out, the door took the leg over and credited it; THEN the
    till's order write lands -- the order refuses it."""
    await _lose_first_press(lost_leg)
    _age(lost_leg)
    repo = lost_leg["orders"]
    real = repo.add_payment

    def _stalled(*a, **k):
        _age(lost_leg, minutes=11)  # the press stalls past its lease ...
        cust_router.give_back_unbilled_leg(LEG, _user("ADMIN"))  # ... and the door gives it back
        return real(*a, **k)

    monkeypatch.setattr(repo, "add_payment", _stalled)
    with pytest.raises(HTTPException) as e:
        await _press()
    assert e.value.status_code == 409 and "given back" in e.value.detail
    assert _balance(lost_leg) == 1000.0 and _paid(lost_leg) == []


@pytest.mark.asyncio
async def test_a_payment_that_reached_the_order_first_stops_the_door_at_the_fence(lost_leg, monkeypatch):
    """The other side: the till's late write lands after the door won the
    leg but before it fenced the order -- the fence misses, nothing is
    credited and the leg goes back to its bill."""
    await _lose_first_press(lost_leg)
    _age(lost_leg)
    real = cust_router._own_leg

    def _write_lands(*a, **k):
        got = real(*a, **k)
        lost_leg["db"].get_collection("orders").update_one(
            {"order_id": "ORD-1"}, {"$push": {"payments": {"payment_id": "late", "method": "STORE_CREDIT",
                                                            "amount": 300.0, "idempotency_key": "leg-k"}}})
        return got

    monkeypatch.setattr(cust_router, "_own_leg", _write_lands)
    with pytest.raises(HTTPException) as e:
        cust_router.give_back_unbilled_leg(LEG, _user("ADMIN"))
    assert e.value.status_code == 409 and _balance(lost_leg) == 700.0
    assert _ledger(lost_leg).find_one({"entry_id": LEG})["leg_owner"] == "BILL"


# =========================================================================== #
# A: a walk-in bill's dead voucher is paid back in cash, once
# =========================================================================== #
def _walk_in_dead(till, monkeypatch):
    vouchers = _dead_voucher(till, monkeypatch, expiry_date="2026-01-01")
    till["db"].get_collection("orders").update_one({"order_id": "O7"}, {"$set": {"customer_id": "walkin-1"}})
    monkeypatch.setattr(returns_router, "_drawer_closed_today", lambda store_id: False)
    return vouchers


def _payouts(till):
    return list(till["db"].get_collection("returns").find({"payout_of": VKEY}))


def test_a_walk_in_dead_voucher_is_paid_back_in_cash_once(till, monkeypatch, tasks):
    vouchers = _walk_in_dead(till, monkeypatch)
    out = cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"), "STORE_DRAWER")
    assert out["given_back"] is True and "cash" in out["message"]
    [payout] = _payouts(till)
    assert payout["refund_tenders"][0]["method"] == "CASH" and payout["refund_tenders"][0]["amount"] == 80.0
    assert payout["cash_paid_from"] == "STORE_DRAWER" and payout["created_by"] == _user("ADMIN")["user_id"]
    v = vouchers.find_one({"code": "GV7"})
    assert v["balance"] == 0.0 and VKEY in v["pos_given_back"]
    assert v["pos_leg_owner"][VKEY] == "GIVEBACK"
    with pytest.raises(HTTPException) as e:  # the till can never bill it
        cust_router.claim_leg_for_bill("GIFT_VOUCHER", VKEY, "GV7")
    assert e.value.status_code == 409
    cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"), "HQ")  # a re-press: the stored payout stands
    assert len(_payouts(till)) == 1 and _payouts(till)[0]["cash_paid_from"] == "STORE_DRAWER"


def test_a_lost_mark_after_the_cash_payout_replays_the_stored_payout(till, monkeypatch, tasks):
    vouchers = _walk_in_dead(till, monkeypatch)
    real = vouchers.update_one
    state = {"n": 0}

    def _mark(flt, upd, *a, **k):
        if "pos_given_back" in (upd.get("$addToSet") or {}) and state["n"] == 0:
            state["n"] += 1
            raise RuntimeError("the mark write was lost")
        return real(flt, upd, *a, **k)

    monkeypatch.setattr(vouchers, "update_one", _mark)
    with pytest.raises(RuntimeError):
        cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"), "HQ")
    cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"), "STORE_DRAWER")
    [payout] = _payouts(till)
    assert payout["cash_paid_from"] == "HQ" and payout["paid_outside_drawer"][0]["amount"] == 80.0
    assert VKEY in vouchers.find_one({"code": "GV7"})["pos_given_back"]


def test_the_cash_source_is_required_and_the_leg_is_never_let_go(till, monkeypatch):
    vouchers = _walk_in_dead(till, monkeypatch)
    with pytest.raises(HTTPException) as e:
        cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"))
    assert e.value.status_code == 400 and _payouts(till) == []
    assert vouchers.find_one({"code": "GV7"})["pos_leg_owner"][VKEY] == "GIVEBACK"
    assert "O7" in [o["order_id"] for o in till["db"].get_collection("orders").find(
        {"given_back_legs": VKEY.split(":", 2)[2]})]


def test_needs_completing_asks_for_the_cash_source_on_that_row(till, monkeypatch):
    _walk_in_dead(till, monkeypatch)
    rows = asyncio.run(returns_router.needs_completing(_user("ADMIN")))["unbilled"]
    assert [r["cash_payout"] for r in rows if r["key"] == VKEY] == [True]


# =========================================================================== #
# D: the door's store check
# =========================================================================== #
def test_an_accountant_of_another_store_cannot_give_it_back(till, redeem_env):
    _unbilled_leg(till)
    other = {"user_id": "U9", "username": "U9", "roles": ["ACCOUNTANT"], "active_store_id": "OTHER",
             "store_ids": ["OTHER"]}
    with pytest.raises(HTTPException) as e:
        cust_router.give_back_unbilled_leg(KEY, other)
    assert e.value.status_code in (403, 404) and _balance(till) == 400.0
    assert _ledger(till).find_one({"entry_id": KEY}).get("leg_owner") is None
