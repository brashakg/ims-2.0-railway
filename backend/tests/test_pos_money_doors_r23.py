"""
IMS 2.0 - POS money doors, round-23 money review of PR #1264 (head e7b51d20)
============================================================================
The give-back decides ONCE where a voucher leg's money goes (a booked payout
or credit by its fixed id first, else the decision the first winning press
stored), so a change between presses never pays the same leg twice. The cash
payout is not a return of the bill: the refund cap and the has-a-return checks
never see it. No emoji.
"""

from __future__ import annotations

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
from tests.test_pos_money_doors_r17 import _balance  # noqa: E402
from tests.test_pos_money_doors_r20 import VKEY  # noqa: E402
from tests.test_pos_money_doors_r21 import _dead_voucher  # noqa: E402
from tests.test_pos_money_doors_r22 import _payouts, _walk_in_dead  # noqa: E402


def _lose_the_mark_once(vouchers, monkeypatch):
    real = vouchers.update_one
    state = {"n": 0}

    def _mark(flt, upd, *a, **k):
        if "pos_given_back" in (upd.get("$addToSet") or {}) and state["n"] == 0:
            state["n"] += 1
            raise RuntimeError("the mark write was lost")
        return real(flt, upd, *a, **k)

    monkeypatch.setattr(vouchers, "update_one", _mark)


def _credits(till):
    return list(_ledger(till).find({"entry_id": f"GIVEBACK:{VKEY}"}))


def _set_customer(till, cid):
    till["db"].get_collection("orders").update_one({"order_id": "O7"}, {"$set": {"customer_id": cid}})


# =========================================================================== #
# A: one decision per leg -- a change between presses never pays twice
# =========================================================================== #
def test_a_cash_payout_then_a_customer_attached_never_adds_store_credit(till, monkeypatch, tasks):
    vouchers = _walk_in_dead(till, monkeypatch)
    _lose_the_mark_once(vouchers, monkeypatch)
    with pytest.raises(RuntimeError):
        cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"), "STORE_DRAWER")
    assert len(_payouts(till)) == 1
    _set_customer(till, "CUST-1")  # a customer attached between presses
    out = cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"), "STORE_DRAWER")
    assert "cash" in out["message"] and len(_payouts(till)) == 1
    assert _credits(till) == [] and _balance(till) == 0.0


def test_a_store_credit_then_the_customer_removed_never_adds_a_cash_payout(till, monkeypatch, tasks):
    vouchers = _dead_voucher(till, monkeypatch, expiry_date="2026-01-01")
    _set_customer(till, "CUST-1")
    monkeypatch.setattr(returns_router, "_drawer_closed_today", lambda store_id: False)
    _lose_the_mark_once(vouchers, monkeypatch)
    with pytest.raises(RuntimeError):
        cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"))
    assert _balance(till) == 80.0
    _set_customer(till, "walkin-1")  # the bill's customer removed between presses
    out = cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"), "STORE_DRAWER")
    assert "store credit" in out["message"] and _payouts(till) == []
    assert _balance(till) == 80.0 and len(_credits(till)) == 1


def test_the_first_presss_stored_decision_is_replayed_before_anything_is_booked(till, monkeypatch, tasks):
    """The first press stored "cash, by HQ" but its payout never landed; a
    customer is attached and the next press says the drawer -- the stored
    decision stands: cash, by HQ, once."""
    _walk_in_dead(till, monkeypatch)
    rcoll = till["db"].get_collection("returns")
    real = rcoll.insert_one
    state = {"n": 0}

    def _lost(doc, *a, **k):
        if str(doc.get("_id", "")).startswith("payout:") and state["n"] == 0:
            state["n"] += 1
            raise RuntimeError("the payout write was lost before it landed")
        return real(doc, *a, **k)

    monkeypatch.setattr(rcoll, "insert_one", _lost)
    with pytest.raises(HTTPException) as e:
        cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"), "HQ")
    assert e.value.status_code == 503 and _payouts(till) == []
    _set_customer(till, "CUST-1")
    cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"), "STORE_DRAWER")
    [payout] = _payouts(till)
    assert payout["cash_paid_from"] == "HQ" and _credits(till) == []


# =========================================================================== #
# B: the cash payout is not a return of the bill
# =========================================================================== #
def test_the_cash_payout_is_not_a_return_of_the_bill(till, monkeypatch, tasks):
    _walk_in_dead(till, monkeypatch)
    cust_router.give_back_unbilled_leg(VKEY, _user("ADMIN"), "STORE_DRAWER")
    [payout] = _payouts(till)
    assert payout["payout_kind"] == "GIVEBACK_PAYOUT" and payout["give_back_order_id"] == "O7"
    assert returns_router._prior_refund_tenders("O7") == {}  # the refund cap
    assert till["db"].get_collection("returns").count_documents({"order_id": "O7"}) == 0  # has-a-return
