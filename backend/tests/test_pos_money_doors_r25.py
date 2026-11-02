"""
IMS 2.0 - POS money doors, round-25 money review of PR #1264 (head 5011f864)
============================================================================
A key never replays a cancelled order, and never at another store. A voucher
leg's give-back has ONE effective decision (the door and Needs completing),
changed only by compare-and-set; the voucher restore lands only while the
decision is still VOUCHER, and a leg given back one way is never booked
another way. No emoji.
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
from tests.test_orders_hardening import _draft_orders, _frame_item, _post_order_idem, hardening_orders  # noqa: E402,F401
from tests.test_pos_money_doors import _user, till  # noqa: E402,F401
from tests.test_pos_money_doors_r12 import tasks  # noqa: E402,F401
from tests.test_pos_money_doors_r17 import _balance  # noqa: E402
from tests.test_pos_money_doors_r20 import VKEY  # noqa: E402
from tests.test_pos_money_doors_r21 import _dead_voucher  # noqa: E402
from tests.test_pos_money_doors_r23 import _credits, _set_customer  # noqa: E402


# =========================================================================== #
# 3: a cancelled order is never replayed; 7: the bill's store, on the server
# =========================================================================== #
def test_a_re_press_of_a_cancelled_order_is_refused(client, auth_headers, hardening_orders):
    r1 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-C")
    for d in hardening_orders["order_repo"].collection.docs:
        d["status"] = "CANCELLED"
    r2 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-C")
    assert r2.status_code == 409, r2.text
    detail = r2.json()["detail"]
    assert detail["code"] == "ORDER_NOT_LIVE" and r1.json()["order_number"] in detail["message"]


def test_a_bill_made_at_another_store_is_refused_by_the_server(client, auth_headers, hardening_orders):
    r0 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-0")
    here = hardening_orders["order_repo"].collection.docs[0]["store_id"]
    r1 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-X", bill_store_id="OTHER-STORE")
    assert r1.status_code == 409, r1.text
    assert r1.json()["detail"]["code"] == "BILL_STORE_MISMATCH"
    assert len(_draft_orders(hardening_orders)) == 1  # nothing written
    r2 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-Y", bill_store_id=here)
    assert r0.status_code in (200, 201) and r2.status_code in (200, 201), r2.text


# =========================================================================== #
# 4: two presses never book both the voucher and store credit
# =========================================================================== #
def _live_voucher_on_a_customers_bill(till, monkeypatch):
    vouchers = _dead_voucher(till, monkeypatch, status="ACTIVE", balance=0.0, expiry_date=None)
    _set_customer(till, "CUST-1")
    monkeypatch.setattr(returns_router, "_drawer_closed_today", lambda store_id: False)
    return vouchers


def test_two_concurrent_presses_book_one_destination(till, monkeypatch, tasks):
    """Press A decided VOUCHER and is about to restore it; meanwhile the
    voucher is cancelled and press B changes the decision to store credit
    and books it. A's restore then misses (the decision is no longer
    VOUCHER): the leg is given back once."""
    from api.services import money_guard

    vouchers = _live_voucher_on_a_customers_bill(till, monkeypatch)
    real = money_guard.credit
    state = {"n": 0}

    def _b_first(*a, **k):
        if state["n"] == 0:
            state["n"] += 1
            vouchers.update_one({"code": "GV7"}, {"$set": {"status": "CANCELLED"}})
            cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"))  # press B
        return real(*a, **k)

    monkeypatch.setattr(money_guard, "credit", _b_first)
    with pytest.raises(HTTPException):  # press A books nothing
        cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"))
    v = vouchers.find_one({"code": "GV7"})
    assert v["balance"] == 0.0 and _balance(till) == 80.0 and len(_credits(till)) == 1


def test_a_leg_given_back_one_way_is_never_booked_another_way(till, monkeypatch, tasks):
    """The decision says store credit, but the voucher restore landed in
    between: both fixed ids are checked before booking -- nothing more."""
    vouchers = _live_voucher_on_a_customers_bill(till, monkeypatch)
    vouchers.update_one({"code": "GV7"}, {"$set": {"status": "CANCELLED"}})
    real = cust_router._voucher_give_back_plan

    def _restore_lands(*a, **k):
        plan = real(*a, **k)
        vouchers.update_one({"code": "GV7"}, {"$push": {"money_ledger": {
            "idempotency_key": f"GIVEBACK:{VKEY}", "delta": 80.0}}, "$inc": {"balance": 80.0}})
        return plan

    monkeypatch.setattr(cust_router, "_voucher_give_back_plan", _restore_lands)
    with pytest.raises(HTTPException) as e:
        cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"))
    assert e.value.status_code == 409 and _credits(till) == [] and _balance(till) == 0.0


# =========================================================================== #
# 5: ONE effective decision for the door and Needs completing
# =========================================================================== #
def test_needs_completing_follows_a_stored_voucher_decision_whose_voucher_died(till, monkeypatch):
    vouchers = _live_voucher_on_a_customers_bill(till, monkeypatch)
    _set_customer(till, "walkin-1")
    vouchers.update_one({"code": "GV7"}, {"$set": {f"pos_give_back_plan.{VKEY}": {"dest": "VOUCHER", "amount": 80.0},
                                                    "status": "CANCELLED"}})
    rows = asyncio.run(returns_router.needs_completing(_user("ADMIN")))["unbilled"]
    assert [r["cash_payout"] for r in rows if r["key"] == VKEY] == [True]
    with pytest.raises(HTTPException) as e:  # the door agrees: cash, so it asks where from
        cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"))
    assert e.value.status_code == 400 and "drawer" in e.value.detail
