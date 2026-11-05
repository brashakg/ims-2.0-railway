"""
IMS 2.0 - L2-POS-F04: a payment racing a counter cancel
=======================================================
The cancel door checked "no money held" and THEN claimed the order. A real
payment landing in that gap left the money on a CANCELLED order with no
refund. The claim now lands only while the order's payments are still the
ones the check read, so on the one order document exactly one side wins:

  * payment first -> the cancel refuses with the approved refund route
    (Refund advance and cancel); the order stays a live, paid sale;
  * cancel first  -> the payment write refuses; a debit tender already taken
    is an unbilled leg the existing give-back door returns, exactly once.

Every interleaving is driven through the REAL handlers over strict fakes and
reconciled on the stored order, the returns collection and the customer's
store credit. No emoji.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import HTTPException  # noqa: E402

from api.routers import customers as cust_router  # noqa: E402
from api.routers.orders import PaymentCreate, add_payment, cancel_order  # noqa: E402
from api.routers.orders import cancel as cancel_module  # noqa: E402
from api.routers.orders.release import _claim_order_for_cancel  # noqa: E402
from tests.test_pos_money_doors import _ledger, _order, _stored, _user, till  # noqa: E402,F401
from tests.test_pos_money_doors_r9 import _ago, _balance, _customer  # noqa: E402
from tests.test_pos_money_doors_r12 import _repo  # noqa: E402

REASON = "customer changed mind"
LEG = "POSPAY:ORD-1:leg-k"


def _run(coro):
    """Run a second real request to completion from inside the first one."""
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _pay(method="CASH", amount=1000.0, key="leg-k"):
    return add_payment("ORD-1", PaymentCreate(method=method, amount=amount), _user("CASHIER"), key)


def _returns(till):
    return list(till["db"].get_collection("returns").find({}))


@pytest.fixture()
def credit(till, monkeypatch):
    _customer(till, store_credit=1000.0)
    monkeypatch.setattr("api.routers.customers.get_customer_repository", lambda: _repo(till))
    return till


def _payment_inside_cancel(monkeypatch, method):
    """The audit's interleaving: the cancel passed its no-money check; the real
    payment lands before the cancel's claim."""
    real = cancel_module._refuse_cancel_holding_money
    seen = {}

    def _check_then_pay(order, order_id, user):
        real(order, order_id, user)
        if "pay" not in seen:  # one-shot: a later re-press runs unwrapped
            seen["pay"] = _run(_pay(method))

    monkeypatch.setattr(cancel_module, "_refuse_cancel_holding_money", _check_then_pay)
    return seen


def _cancel_inside_payment(monkeypatch, till):
    """The other order: the payment read the order live (and took a debit
    tender), then the cancel lands before the payment's order write."""
    repo = till["orders"]
    real = repo.add_payment
    seen = {}

    def _cancel_then_write(*a, **k):
        if "cancel" not in seen:
            seen["cancel"] = _run(cancel_order("ORD-1", reason=REASON, current_user=_user("SALES_STAFF")))
        return real(*a, **k)

    monkeypatch.setattr(repo, "add_payment", _cancel_then_write)
    return seen


# =========================================================================== #
# Payment first: the cancel refuses, the order stays a paid sale
# =========================================================================== #
@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["CASH", "STORE_CREDIT"])
async def test_payment_landing_inside_the_cancel_refuses_the_cancel(till, credit, monkeypatch, method):
    till["db"].get_collection("orders").insert_one(_order(status="DRAFT"))
    seen = _payment_inside_cancel(monkeypatch, method)
    with pytest.raises(HTTPException) as exc:
        await cancel_order("ORD-1", reason=REASON, current_user=_user("SALES_STAFF"))
    assert exc.value.status_code == 400
    assert "Rs 1,000.00 was paid" in exc.value.detail and "Refund advance and cancel" in exc.value.detail
    assert seen["pay"]["payment_id"]
    # Reconcile: one live, paid sale; no refund booked; credit spent once.
    doc = _stored(till)
    assert doc["status"] == "CONFIRMED" and doc["amount_paid"] == 1000.0 and doc["balance_due"] == 0.0
    assert [p["method"] for p in doc["payments"]] == [method]
    assert "cancelled_at" not in doc and _returns(till) == []
    assert _balance(till) == (0.0 if method == "STORE_CREDIT" else 1000.0)


@pytest.mark.asyncio
async def test_retries_after_the_race_add_nothing(till, credit, monkeypatch):
    """A re-sent payment (lost acknowledgement) replays; a re-pressed cancel
    still refuses. One payment, one debit, order live."""
    till["db"].get_collection("orders").insert_one(_order(status="DRAFT"))
    _payment_inside_cancel(monkeypatch, "STORE_CREDIT")
    with pytest.raises(HTTPException):
        await cancel_order("ORD-1", reason=REASON, current_user=_user("SALES_STAFF"))
    replay = await add_payment("ORD-1", PaymentCreate(method="STORE_CREDIT", amount=1000.0),
                               _user("CASHIER"), "leg-k")
    assert replay.get("_idempotent_replay") is True
    with pytest.raises(HTTPException) as exc:
        await cancel_order("ORD-1", reason=REASON, current_user=_user("ADMIN"))
    assert exc.value.status_code == 400 and "Rs 1,000.00 was paid" in exc.value.detail
    doc = _stored(till)
    assert doc["status"] == "CONFIRMED" and len(doc["payments"]) == 1 and doc["amount_paid"] == 1000.0
    assert _balance(till) == 0.0 and _returns(till) == []


def test_the_claim_loses_to_a_payment_pushed_after_the_read(till):
    till["db"].get_collection("orders").insert_one(_order())
    read = dict(_stored(till))
    till["db"].get_collection("orders").update_one(
        {"order_id": "ORD-1"}, {"$push": {"payments": {"payment_id": "p1", "method": "CASH", "amount": 10.0}},
                                "$set": {"amount_paid": 10.0}})
    assert _claim_order_for_cancel(till["orders"], "ORD-1", REASON, {"user_id": "u"},
                                   invoice=None, money_read=read) is None
    assert _stored(till)["status"] == "CONFIRMED"
    # The same read with nothing in between still claims (same-shop cancel).
    fresh = dict(_stored(till))
    assert _claim_order_for_cancel(till["orders"], "ORD-1", REASON, {"user_id": "u"},
                                   invoice=None, money_read=fresh) is not None
    assert _stored(till)["status"] == "CANCELLED"


# =========================================================================== #
# Cancel first: the payment refuses; a debit already taken is given back once
# =========================================================================== #
@pytest.mark.asyncio
async def test_cancel_landing_inside_a_cash_payment_refuses_the_payment(till, monkeypatch):
    till["db"].get_collection("orders").insert_one(_order(status="DRAFT"))
    seen = _cancel_inside_payment(monkeypatch, till)
    with pytest.raises(HTTPException):
        await _pay("CASH")
    assert seen["cancel"]["status"] == "CANCELLED"
    doc = _stored(till)
    assert doc["status"] == "CANCELLED" and doc["payments"] == [] and doc["amount_paid"] == 0.0
    assert _returns(till) == []


@pytest.mark.asyncio
async def test_cancel_landing_inside_a_store_credit_payment_strands_nothing(till, credit, monkeypatch):
    till["db"].get_collection("orders").insert_one(_order(status="DRAFT", order_number="BV/0001"))
    _cancel_inside_payment(monkeypatch, till)
    with pytest.raises(HTTPException):
        await _pay("STORE_CREDIT")
    doc = _stored(till)
    assert doc["status"] == "CANCELLED" and doc["payments"] == [] and doc["amount_paid"] == 0.0
    assert _balance(till) == 0.0  # the debit was taken before the order write lost
    # It is an unbilled leg (Returns > Needs completing), never silent.
    row = _ledger(till).find_one({"entry_id": LEG})
    assert cust_router.leg_unresolved(cust_router._store_credit_leg(row), doc)
    _ledger(till).update_one({"entry_id": LEG}, {"$set": {"pending_at": _ago(30), "leg_owner_at": _ago(30)}})
    cust_router.give_back_unbilled_leg(LEG, _user("ADMIN"))
    cust_router.give_back_unbilled_leg(LEG, _user("ADMIN"))  # a re-press gives nothing more
    assert _balance(till) == 1000.0
    assert _stored(till)["status"] == "CANCELLED" and _stored(till)["payments"] == []


# =========================================================================== #
# Same shop, no race: unchanged
# =========================================================================== #
@pytest.mark.asyncio
async def test_unpaid_order_still_cancels_and_paid_one_still_takes_payment(till):
    coll = till["db"].get_collection("orders")
    coll.insert_one(_order())
    out = await cancel_order("ORD-1", reason=REASON, current_user=_user("SALES_STAFF"))
    assert out["status"] == "CANCELLED" and _stored(till)["balance_due"] == 0.0
    coll.insert_one(_order("ORD-2"))
    await add_payment("ORD-2", PaymentCreate(method="CASH", amount=400.0), _user("CASHIER"), "k2")
    assert _stored(till, "ORD-2")["amount_paid"] == 400.0
