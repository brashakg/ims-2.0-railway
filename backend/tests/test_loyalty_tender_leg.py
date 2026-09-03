"""
IMS 2.0 - The LOYALTY tender leg: the server contract the POS submit depends on
===============================================================================
The burn-without-tender defect (POS): /loyalty/redeem atomically debits the
customer's points, but it only writes loyalty_accounts + loyalty_transactions
-- it NEVER touches the order document. The order is only made whole when the
POS posts a LOYALTY payment leg to POST /orders/{id}/payments. These tests pin
the server half of that contract by driving the REAL add_payment endpoint over
a REAL OrderRepository (nothing under test is stubbed):

  1. "LOYALTY" is a valid PaymentMethod -- the leg must not 422. The original
     defect hid behind exactly that 422 being swallowed client-side (see the
     enum comment in orders.py), so removing the member reopens the bug.
  2. A LOYALTY leg is real settlement: it lands in the order's payments,
     counts toward amount_paid, reduces balance_due, and together with the
     cash leg settles the order to PAID.
  3. Differential guard: a CREDIT leg of the same amount does NOT count toward
     amount_paid -- proving assertion 2 is not true-by-construction. If
     LOYALTY were ever mis-bucketed as a pay-later promise, test 2 fails.
  4. The over-tender guard applies to LOYALTY like any real-money tender: a
     leg larger than the balance due is refused, so the POS cannot
     double-charge through it.

No emoji (Windows cp1252).
"""

from __future__ import annotations

import copy
import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import HTTPException  # noqa: E402

from api.routers import orders as orders_module  # noqa: E402
from api.routers.orders import PaymentCreate, PaymentMethod, add_payment  # noqa: E402
from database.repositories.order_repository import OrderRepository  # noqa: E402


class _OrdersColl:
    """Just enough Mongo for OrderRepository.add_payment: find_one plus
    update_one with $push/$set. The repository's REAL money arithmetic
    (amount_paid / balance_due / payment_status) runs on top of this."""

    def __init__(self, docs: Optional[List[Dict[str, Any]]] = None):
        self.docs: List[Dict[str, Any]] = [copy.deepcopy(d) for d in (docs or [])]

    def find_one(self, query=None, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (query or {}).items()):
                return copy.deepcopy(d)
        return None

    def update_one(self, query, update, **_kw):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (query or {}).items()):
                for key, val in (update.get("$push") or {}).items():
                    d.setdefault(key, []).append(copy.deepcopy(val))
                for key, val in (update.get("$set") or {}).items():
                    d[key] = copy.deepcopy(val)
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()


def _order_doc(order_id="ORD-L1", grand_total=1000.0) -> Dict[str, Any]:
    return {
        "order_id": order_id,
        "order_number": order_id,
        "store_id": "BV-PUN-01",
        "customer_id": "cust-1",
        "status": "CONFIRMED",
        "grand_total": grand_total,
        "amount_paid": 0.0,
        "balance_due": grand_total,
        "payment_status": "UNPAID",
        "payments": [],
        "created_at": "2026-09-01T10:00:00",
        "items": [],
    }


def _cashier() -> Dict[str, Any]:
    return {
        "user_id": "U-cash",
        "username": "asha",
        "roles": ["SALES_CASHIER"],
        "active_store_id": "BV-PUN-01",
    }


@pytest.fixture()
def sale(monkeypatch):
    def _build(order_id="ORD-L1", grand_total=1000.0):
        coll = _OrdersColl([_order_doc(order_id, grand_total)])
        repo = OrderRepository(coll)
        monkeypatch.setattr(orders_module, "get_order_repository", lambda: repo)
        return {"coll": coll, "order_id": order_id}

    return _build


async def _pay(order_id, **kw):
    return await add_payment(order_id, PaymentCreate(**kw), current_user=_cashier())


def test_loyalty_is_a_valid_payment_method():
    # Contract 1: the leg the POS posts after a burn must not 422. Removing
    # this enum member silently reopens the burn-without-tender defect (the
    # POS catch swallows the 422 and the order keeps the full balance owing).
    assert PaymentMethod("LOYALTY") is PaymentMethod.LOYALTY
    assert PaymentMethod.LOYALTY.value == "LOYALTY"


@pytest.mark.asyncio
async def test_loyalty_leg_settles_the_order_like_real_money(sale):
    ctx = sale()
    # The points were burned by /loyalty/redeem (worth Rs 300); the POS now
    # records the matching tender, then the cash remainder.
    await _pay(ctx["order_id"], method="LOYALTY", amount=300.0, reference="300pts txn t1")

    doc = ctx["coll"].find_one({"order_id": ctx["order_id"]})
    assert [p["method"] for p in doc["payments"]] == ["LOYALTY"]
    assert doc["amount_paid"] == 300.0
    assert doc["balance_due"] == 700.0
    assert doc["payment_status"] == "PARTIAL"

    await _pay(ctx["order_id"], method="CASH", amount=700.0)
    doc = ctx["coll"].find_one({"order_id": ctx["order_id"]})
    assert doc["amount_paid"] == 1000.0
    assert doc["balance_due"] == 0.0
    assert doc["payment_status"] == "PAID"
    # The order's recorded tenders ARE the loyalty leg + the lowered cash leg.
    assert [(p["method"], p["amount"]) for p in doc["payments"]] == [
        ("LOYALTY", 300.0),
        ("CASH", 700.0),
    ]


@pytest.mark.asyncio
async def test_credit_differential_proves_the_bucket_matters(sale):
    # If LOYALTY were treated like CREDIT (a pay-later promise), the previous
    # test's amount_paid/balance_due assertions would be meaningless. Show the
    # code path genuinely distinguishes the buckets: an identical CREDIT leg
    # moves NO money.
    ctx = sale(order_id="ORD-L2")
    await _pay(ctx["order_id"], method="CREDIT", amount=300.0)
    doc = ctx["coll"].find_one({"order_id": ctx["order_id"]})
    assert doc["amount_paid"] == 0.0
    assert doc["balance_due"] == 1000.0
    assert doc["payment_status"] == "CREDIT"


@pytest.mark.asyncio
async def test_loyalty_leg_cannot_exceed_balance_due(sale):
    # Contract 4: the over-tender guard treats LOYALTY as real money, so the
    # POS cannot double-charge through the leg (e.g. replaying it).
    ctx = sale(order_id="ORD-L3")
    await _pay(ctx["order_id"], method="LOYALTY", amount=300.0)
    with pytest.raises(HTTPException) as exc:
        await _pay(ctx["order_id"], method="LOYALTY", amount=800.0)
    assert exc.value.status_code == 400
    assert "exceeds balance due" in str(exc.value.detail)
    # And the refused leg recorded nothing.
    doc = ctx["coll"].find_one({"order_id": ctx["order_id"]})
    assert doc["amount_paid"] == 300.0
    assert len(doc["payments"]) == 1
