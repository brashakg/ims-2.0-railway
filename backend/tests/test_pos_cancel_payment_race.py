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
from types import SimpleNamespace

import mongomock
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import HTTPException  # noqa: E402
from pymongo.errors import AutoReconnect  # noqa: E402

from api.routers import customers as cust_router  # noqa: E402
from api.routers.orders import PaymentCreate, add_payment, cancel_order  # noqa: E402
from api.routers.orders import cancel as cancel_module  # noqa: E402
from api.routers.orders.release import _claim_order_for_cancel  # noqa: E402
from database.repositories.product_repository import StockRepository  # noqa: E402
from tests.test_pos_money_doors import (  # noqa: E402,F401
    _ADV_BODY, _advance_client, _advance_order, _hdr, _ledger, _order, _stored, _user, till,
)
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
# A capture beats a wallet tender (#1387): the debit is given back exactly once
# =========================================================================== #
def _capture_lands_after_the_debit(monkeypatch, till):
    """The tender passed the balance check and took its debit; another tender
    settles the bill before the tender's own order write."""
    repo = till["orders"]
    real = repo.add_payment
    seen = []

    def _capture_then_write(*a, **k):
        if not seen:
            seen.append(1)
            real("ORD-1", {"payment_id": "cap", "method": "UPI", "amount": 1000.0,
                           "idempotency_key": "razorpay:pay_1"})
        return real(*a, **k)

    monkeypatch.setattr(repo, "add_payment", _capture_then_write)
    return seen


def _voucher_wallet(monkeypatch):
    db = mongomock.MongoClient().db
    monkeypatch.setattr("api.dependencies.get_seeded_db", lambda: db)
    monkeypatch.setattr(cust_router, "_vouchers_coll", lambda: db.vouchers)
    db.vouchers.insert_one({"code": "GV1", "status": "ACTIVE", "balance": 1000.0, "expiry_date": None})
    return db.vouchers


def _pay_voucher(key):
    body = PaymentCreate(method="GIFT_VOUCHER", amount=1000.0, voucher_code="GV1")
    return add_payment("ORD-1", body, _user("CASHIER"), key)


def _leg_rows(till):
    """Every debit key the wallets now hold, read from the stored rows."""
    rows = [r["entry_id"] for r in _ledger(till).find({"source": "POS_PAYMENT"})
            if str(r["entry_id"]).startswith("POSPAY:")]
    for v in cust_router._vouchers_coll().find({}):
        rows += [e["idempotency_key"] for e in v.get("money_ledger") or []
                 if str(e.get("idempotency_key") or "").startswith("POSPAY:")]
    return rows


def _past_the_lease(till, leg):
    _ledger(till).update_one({"entry_id": leg}, {"$set": {"pending_at": _ago(30), "leg_owner_at": _ago(30)}})
    coll = cust_router._vouchers_coll()
    for v in coll.find({"money_ledger.idempotency_key": leg}):
        ledger = [{**e, "created_at": _ago(30)} if e.get("idempotency_key") == leg else e
                  for e in v["money_ledger"]]
        coll.update_one({"code": v["code"]}, {"$set": {
            "money_ledger": ledger, f"pos_leg_owner_at.{leg.replace('.', '_')}": _ago(30)}})


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["leg-k", None], ids=["keyed", "keyless"])
async def test_a_store_credit_tender_beaten_by_a_capture_is_given_back_once(till, credit, monkeypatch, key):
    till["db"].get_collection("orders").insert_one(_order(status="DRAFT", order_number="BV/0001"))
    _capture_lands_after_the_debit(monkeypatch, till)
    with pytest.raises(HTTPException) as e:
        await _pay("STORE_CREDIT", key=key)
    assert e.value.status_code == 400 and "exceeds balance due" in e.value.detail
    assert "Needs completing" in e.value.detail  # the cashier knows it is not lost
    assert _balance(till) == 0.0 and [p["method"] for p in _stored(till)["payments"]] == ["UPI"]
    [leg] = _leg_rows(till)
    row = _ledger(till).find_one({"entry_id": leg})
    assert cust_router.leg_unresolved(cust_router._store_credit_leg(row), _stored(till))
    _past_the_lease(till, leg)
    cust_router.give_back_unbilled_leg(leg, _user("ADMIN"))
    again = cust_router.give_back_unbilled_leg(leg, _user("ADMIN"))
    assert again["replayed"] is True and _balance(till) == 1000.0
    assert [p["method"] for p in _stored(till)["payments"]] == ["UPI"]
    assert _stored(till)["amount_paid"] == 1000.0


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["leg-k", None], ids=["keyed", "keyless"])
async def test_a_voucher_tender_beaten_by_a_capture_is_given_back_once(till, credit, monkeypatch, key):
    till["db"].get_collection("orders").insert_one(_order(status="DRAFT", order_number="BV/0001"))
    vouchers = _voucher_wallet(monkeypatch)
    _capture_lands_after_the_debit(monkeypatch, till)
    with pytest.raises(HTTPException) as e:
        await _pay_voucher(key)
    assert e.value.status_code == 400 and "Needs completing" in e.value.detail
    assert vouchers.find_one({"code": "GV1"})["balance"] == 0.0
    [leg] = _leg_rows(till)
    _past_the_lease(till, leg)
    cust_router.give_back_unbilled_leg(leg, _user("ADMIN"))
    again = cust_router.give_back_unbilled_leg(leg, _user("ADMIN"))
    v = vouchers.find_one({"code": "GV1"})
    assert again["replayed"] is True and v["balance"] == 1000.0 and v["status"] == "ACTIVE"
    assert [p["method"] for p in _stored(till)["payments"]] == ["UPI"]


@pytest.mark.asyncio
async def test_a_keyless_wallet_tender_that_lands_is_on_its_bill_and_resolved(till, credit):
    """The key minted for a keyless leg rides on the payment: nothing unresolved."""
    till["db"].get_collection("orders").insert_one(_order(status="DRAFT", order_number="BV/0001"))
    await _pay("STORE_CREDIT", key=None)
    [leg] = _leg_rows(till)
    row = _ledger(till).find_one({"entry_id": leg})
    assert not cust_router.leg_unresolved(cust_router._store_credit_leg(row), _stored(till))
    assert _stored(till)["amount_paid"] == 1000.0


@pytest.mark.asyncio
async def test_a_cash_tender_beaten_by_a_capture_says_nothing_of_a_wallet(till, monkeypatch):
    till["db"].get_collection("orders").insert_one(_order(status="DRAFT"))
    _capture_lands_after_the_debit(monkeypatch, till)
    with pytest.raises(HTTPException) as e:
        await _pay("CASH")
    assert e.value.status_code == 400 and e.value.detail.startswith("Payment amount exceeds balance due")
    assert "Needs completing" not in e.value.detail


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


# =========================================================================== #
# The claim's write fails or its outcome is unknown (Codex PR-1396 P1). There
# is no second, weaker write: the claim's one conditional write is the only
# thing that may cancel, so a failed claim changes nothing and a lost
# acknowledgement finishes through the retry door on what the database holds.
# =========================================================================== #
@pytest.fixture()
def shelf(till, monkeypatch):
    """One serialized frame SOLD to ORD-1, behind the real stock repository."""
    units = till["db"].get_collection("stock_units")
    units.insert_one({"stock_id": "U1", "product_id": "P1", "store_id": "BV-PUN-01",
                      "status": "SOLD", "order_id": "ORD-1"})
    repo = StockRepository(units)
    monkeypatch.setattr(cancel_module, "get_stock_repository", lambda: repo)
    return units


def _claim_fault(till, monkeypatch, land=False):
    """The cancel's find_one_and_update raises once, like a dropped connection:
    before the write, or (land=True) after it, the lost acknowledgement. Every
    other write is real."""
    coll = till["db"].get_collection("orders")
    real = coll.find_one_and_update
    seen = {"faults": 0}

    def _fault(flt, upd, *a, **k):
        if upd.get("$set", {}).get("status") == "CANCELLED" and not seen["faults"]:
            seen["faults"] += 1
            if land:
                real(flt, upd, *a, **k)
            raise AutoReconnect("connection dropped")
        return real(flt, upd, *a, **k)

    monkeypatch.setattr(coll, "find_one_and_update", _fault)
    return seen


def _pay_before_any_plain_cancel(till, monkeypatch):
    """The audit's interleaving: a real payment lands immediately before any
    plain status write the cancel attempts after a failed claim."""
    repo = till["orders"]
    real = repo.update
    seen = {}

    def _pay_then_write(order_id, data):
        if data.get("status") == "CANCELLED" and "pay" not in seen:
            seen["pay"] = _run(_pay("CASH"))
        return real(order_id, data)

    monkeypatch.setattr(repo, "update", _pay_then_write)
    return seen


def _refuse_plain_cancel(repo, monkeypatch):
    """Only the conditional write may cancel: a plain one fails the test."""
    real = repo.update

    def _update(order_id, data):
        assert data.get("status") != "CANCELLED", "a plain write cancelled the order"
        return real(order_id, data)

    monkeypatch.setattr(repo, "update", _update)


@pytest.mark.asyncio
async def test_failed_claim_then_payment_never_cancels_or_releases(till, shelf, monkeypatch):
    till["db"].get_collection("orders").insert_one(_order(status="DRAFT"))
    fault = _claim_fault(till, monkeypatch)
    seen = _pay_before_any_plain_cancel(till, monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await cancel_order("ORD-1", reason=REASON, current_user=_user("SALES_STAFF"))
    assert exc.value.status_code == 500 and "retry" in exc.value.detail
    assert fault["faults"] == 1 and "pay" not in seen  # no plain write was attempted
    assert _stored(till)["status"] == "DRAFT"
    assert shelf.find_one({"stock_id": "U1"})["status"] == "SOLD"

    # The payment then goes through, and the re-pressed cancel names the refund door.
    await _pay("CASH")
    with pytest.raises(HTTPException) as again:
        await cancel_order("ORD-1", reason=REASON, current_user=_user("SALES_STAFF"))
    assert again.value.status_code == 400 and "Refund advance and cancel" in again.value.detail
    doc = _stored(till)
    assert doc["status"] == "CONFIRMED" and doc["amount_paid"] == 1000.0 and len(doc["payments"]) == 1
    assert "cancelled_at" not in doc and _returns(till) == []
    unit = shelf.find_one({"stock_id": "U1"})
    assert unit["status"] == "SOLD" and unit["order_id"] == "ORD-1"


@pytest.mark.asyncio
async def test_failed_claim_cancels_once_on_a_healthy_retry(till, shelf, monkeypatch):
    till["db"].get_collection("orders").insert_one(_order())
    _claim_fault(till, monkeypatch)
    _refuse_plain_cancel(till["orders"], monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await cancel_order("ORD-1", reason=REASON, current_user=_user("SALES_STAFF"))
    assert exc.value.status_code == 500
    assert _stored(till)["status"] == "CONFIRMED" and "cancelled_at" not in _stored(till)
    assert shelf.find_one({"stock_id": "U1"})["status"] == "SOLD"

    out = await cancel_order("ORD-1", reason=REASON, current_user=_user("SALES_STAFF"))
    assert out["status"] == "CANCELLED" and out["stock_units_released"] == 1
    with pytest.raises(HTTPException) as again:
        await cancel_order("ORD-1", reason=REASON, current_user=_user("SALES_STAFF"))
    assert again.value.status_code == 400
    unit = shelf.find_one({"stock_id": "U1"})
    assert unit["status"] == "AVAILABLE" and not unit.get("order_id")


@pytest.mark.asyncio
async def test_lost_acknowledgement_finishes_the_undo_and_refuses_later_money(till, shelf, monkeypatch):
    """The claim landed but its answer did not: the conditional write held, so
    no money can be on the order; the undo runs once and the payment refuses."""
    till["db"].get_collection("orders").insert_one(_order())
    _claim_fault(till, monkeypatch, land=True)
    out = await cancel_order("ORD-1", reason=REASON, current_user=_user("SALES_STAFF"))
    assert out["status"] == "CANCELLED" and out["stock_units_released"] == 1
    assert out["stock_release_failed"] is False
    with pytest.raises(HTTPException):
        await _pay("CASH")
    with pytest.raises(HTTPException) as again:
        await cancel_order("ORD-1", reason=REASON, current_user=_user("SALES_STAFF"))
    assert again.value.status_code == 400
    doc = _stored(till)
    assert doc["status"] == "CANCELLED" and doc["payments"] == [] and doc["amount_paid"] == 0.0
    assert _returns(till) == []
    assert shelf.find_one({"stock_id": "U1"})["status"] == "AVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("landed", [False, True])
async def test_claim_failure_with_an_unreadable_order_says_retry(till, shelf, monkeypatch, landed):
    """The claim raised AND the re-read failed: nobody knows whether it landed.
    It is a retry, not a status change; no undo runs until a re-press reads what
    happened, and that press finishes either way, once."""
    till["db"].get_collection("orders").insert_one(_order())
    _claim_fault(till, monkeypatch, land=landed)
    repo = till["orders"]
    real = repo.find_by_id
    blind = {"on": False}
    real_claim = cancel_module._claim_order_for_cancel

    def _claim_then_blind(*a, **k):
        out = real_claim(*a, **k)
        blind["on"] = True
        return out

    monkeypatch.setattr(cancel_module, "_claim_order_for_cancel", _claim_then_blind)
    monkeypatch.setattr(repo, "find_by_id", lambda oid: None if blind["on"] else real(oid))
    with pytest.raises(HTTPException) as exc:
        await cancel_order("ORD-1", reason=REASON, current_user=_user("SALES_STAFF"))
    assert exc.value.status_code == 500 and "retry" in exc.value.detail
    assert shelf.find_one({"stock_id": "U1"})["status"] == "SOLD"
    assert _stored(till)["status"] == ("CANCELLED" if landed else "CONFIRMED")

    monkeypatch.setattr(cancel_module, "_claim_order_for_cancel", real_claim)
    blind["on"] = False
    out = await cancel_order("ORD-1", reason=REASON, current_user=_user("SALES_STAFF"))
    assert out["status"] == "CANCELLED" and out["stock_units_released"] == 1
    assert shelf.find_one({"stock_id": "U1"})["status"] == "AVAILABLE"


def test_advance_refund_survives_a_failed_claim_and_a_second_press_finishes(till, shelf, monkeypatch):
    """The approved door: the refund stands once, the cancel reports pending,
    and the second press finishes it. One refund, one release."""
    _advance_order(till)
    _claim_fault(till, monkeypatch)
    _refuse_plain_cancel(till["orders"], monkeypatch)
    client = _advance_client()
    r = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 201, r.text
    assert r.json()["cancel_error"] and r.json()["order_status"] == "CONFIRMED"
    assert shelf.find_one({"stock_id": "U1"})["status"] == "SOLD"

    r2 = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r2.status_code == 201, r2.text
    assert r2.json()["cancel_error"] is None and r2.json()["refund_amount"] == 0.0
    assert _stored(till)["status"] == "CANCELLED"
    assert [x["net_refund"] for x in _returns(till)] == [1000.0]
    assert shelf.find_one({"stock_id": "U1"})["status"] == "AVAILABLE"

    r3 = client.post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r3.status_code == 400
    assert [x["net_refund"] for x in _returns(till)] == [1000.0]


def test_claim_without_an_atomic_surface_cancels_nothing(till, monkeypatch):
    """No conditional write available, no cancel: there is no plain-write path."""
    till["db"].get_collection("orders").insert_one(_order())
    read = dict(_stored(till))
    repo = till["orders"]
    real = repo.collection
    _refuse_plain_cancel(repo, monkeypatch)
    repo.collection = SimpleNamespace(find_one=real.find_one)  # reads, no conditional write
    claimed = _claim_order_for_cancel(repo, "ORD-1", REASON, {"user_id": "u"},
                                      invoice=None, money_read=read)
    repo.collection = real
    assert claimed is None and _stored(till)["status"] == "CONFIRMED"
