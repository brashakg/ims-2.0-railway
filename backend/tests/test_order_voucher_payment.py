"""
IMS 2.0 - Order payment x gift-voucher integration tests (Stage 2)
====================================================================
This is the REVENUE-CRITICAL seam: recording a GIFT_VOUCHER payment on an
order must REDEEM the card (decrement its balance) inside the payment
endpoint, and a bad voucher must be rejected with a 400 while leaving both
the card and the order untouched.

Design under test (orders.add_payment, GIFT_VOUCHER branch):
  - validate the voucher was already done read-only at "Apply"; the spend
    happens HERE, when the payment is actually recorded, so an abandoned
    sale never burns a card.
  - on redeem failure -> HTTPException(400, "Voucher: <reason>"), nothing
    recorded.
  - on success -> falls through to the normal payment-recording path.

We drive add_payment() directly (it's an async function) with:
  - a fake order repository exposing find_by_id / add_payment / update_status
  - the SAME FakeVouchers collection wired into BOTH the vouchers module
    (so issue works) AND the seeded-db dependency add_payment reaches for
    (so redeem hits the same doc). That shared collection is what makes the
    "balance actually decremented" / "untouched on failure" assertions
    meaningful end-to-end.
"""

from __future__ import annotations

import copy
import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from api.routers import vouchers as vouchers_module
from api.routers import orders as orders_module
from api.routers.orders import PaymentCreate, add_payment


# ============================================================================
# Fake vouchers collection (atomic find_one_and_update semantics)
# ============================================================================


def _matches(doc: Dict[str, Any], flt: Dict[str, Any]) -> bool:
    for key, cond in flt.items():
        if key == "$or":
            if not any(_matches(doc, sub) for sub in cond):
                return False
            continue
        val = doc.get(key)
        if isinstance(cond, dict):
            for op, operand in cond.items():
                if op == "$gte" and not (val is not None and val >= operand):
                    return False
                if op == "$lte" and not (val is not None and val <= operand):
                    return False
                if op == "$gt" and not (val is not None and val > operand):
                    return False
                if op == "$lt" and not (val is not None and val < operand):
                    return False
        else:
            if val != cond:
                return False
    return True


class FakeVouchers:
    def __init__(self) -> None:
        self._docs: List[Dict[str, Any]] = []

    def insert_one(self, doc: Dict[str, Any]):
        self._docs.append(copy.deepcopy(doc))
        return type("R", (), {"inserted_id": doc.get("voucher_id")})()

    def create_index(self, *_a, **_k):
        return "code_1"

    def find_one(self, flt: Dict[str, Any]):
        for d in self._docs:
            if _matches(d, flt):
                return copy.deepcopy(d)
        return None

    def find_one_and_update(self, flt, update, return_document=None):
        for d in self._docs:
            if not _matches(d, flt):
                continue
            for field, delta in update.get("$inc", {}).items():
                d[field] = (d.get(field) or 0) + delta
            for field, value in update.get("$push", {}).items():
                d.setdefault(field, []).append(value)
            for field, value in update.get("$set", {}).items():
                d[field] = value
            return copy.deepcopy(d)
        return None


class _FakeDB:
    """Stands in for both vouchers._get_db() and get_seeded_db(): exposes
    get_collection so vouchers._coll(db) resolves to our shared store."""

    def __init__(self, store: FakeVouchers) -> None:
        self._store = store

    def get_collection(self, name):
        assert name == "vouchers"
        return self._store


# ============================================================================
# Fake order repository
# ============================================================================


class FakeOrderRepo:
    def __init__(self, order: Dict[str, Any]) -> None:
        self._order = order
        self.payments: List[Dict[str, Any]] = []

    def find_by_id(self, _order_id: str):
        return copy.deepcopy(self._order)

    def add_payment(self, _order_id: str, payment_data: Dict[str, Any]):
        self.payments.append(copy.deepcopy(payment_data))
        return True

    def update_status(self, _order_id: str, status: str, _user):
        self._order["status"] = status
        return True


def _order(balance_due: float = 1000.0) -> Dict[str, Any]:
    return {
        "order_id": "O-1",
        "status": "CONFIRMED",
        "grand_total": balance_due,
        "balance_due": balance_due,
        "payment_status": "PARTIAL",
    }


def _cashier() -> Dict[str, Any]:
    return {"user_id": "U-cash", "roles": ["SALES_CASHIER"], "active_store_id": "BV-01"}


@pytest.fixture
def wired(monkeypatch):
    """Wire a shared FakeVouchers into the vouchers module + the seeded-db
    dependency, and a FakeOrderRepo into the orders module."""
    store = FakeVouchers()
    fake_db = _FakeDB(store)

    # vouchers.issue_voucher / get_voucher reach for _get_db()
    monkeypatch.setattr(vouchers_module, "_get_db", lambda: fake_db)
    # orders.add_payment's GIFT_VOUCHER branch calls get_seeded_db() via
    # `from ..dependencies import get_seeded_db` -> patch it on dependencies.
    from api import dependencies as deps_module

    monkeypatch.setattr(deps_module, "get_seeded_db", lambda: fake_db)

    repo = FakeOrderRepo(_order())
    monkeypatch.setattr(orders_module, "get_order_repository", lambda: repo)
    return store, repo


async def _issue(store_amount: float, code: str):
    body = vouchers_module.VoucherCreate(amount=store_amount, code=code)
    return await vouchers_module.issue_voucher(
        body,
        current_user={"user_id": "U-admin", "roles": ["ADMIN"], "active_store_id": "BV-01"},
    )


# ============================================================================
# Tests
# ============================================================================


@pytest.mark.asyncio
class TestGiftVoucherPayment:
    async def test_payment_redeems_voucher_balance_decrements(self, wired):
        """Recording a GIFT_VOUCHER payment redeems the card: balance drops
        by the paid amount and the payment is recorded on the order."""
        store, repo = wired
        await _issue(1000.0, "GC-PAY1")

        resp = await add_payment(
            "O-1",
            PaymentCreate(method="GIFT_VOUCHER", amount=400.0, reference="GC-PAY1"),
            current_user=_cashier(),
        )

        # Payment recorded.
        assert resp["amount"] == 400.0
        assert len(repo.payments) == 1
        assert repo.payments[0]["method"] == "GIFT_VOUCHER"
        # Card decremented atomically.
        doc = store.find_one({"code": "GC-PAY1"})
        assert doc["balance"] == 600.0
        assert doc["status"] == "ACTIVE"
        assert len(doc["redemptions"]) == 1
        assert doc["redemptions"][0]["order_id"] == "O-1"

    async def test_full_redeem_flips_status_and_records(self, wired):
        store, repo = wired
        await _issue(1000.0, "GC-FULL")
        # balance_due is 1000, redeem the whole 1000 -> card drained.
        await add_payment(
            "O-1",
            PaymentCreate(method="GIFT_VOUCHER", amount=1000.0, voucher_code="GC-FULL"),
            current_user=_cashier(),
        )
        doc = store.find_one({"code": "GC-FULL"})
        assert doc["balance"] == 0.0
        assert doc["status"] == "REDEEMED"
        assert len(repo.payments) == 1

    async def test_voucher_code_field_preferred_over_reference(self, wired):
        store, _repo = wired
        await _issue(1000.0, "GC-PREF")
        # reference points at a different (nonexistent) code; voucher_code
        # is the real one and must win.
        await add_payment(
            "O-1",
            PaymentCreate(
                method="GIFT_VOUCHER",
                amount=100.0,
                reference="GC-WRONG",
                voucher_code="GC-PREF",
            ),
            current_user=_cashier(),
        )
        assert store.find_one({"code": "GC-PREF"})["balance"] == 900.0

    async def test_invalid_voucher_400_and_nothing_recorded(self, wired):
        """A code that doesn't exist -> 400, no payment recorded."""
        from fastapi import HTTPException

        _store, repo = wired
        with pytest.raises(HTTPException) as ei:
            await add_payment(
                "O-1",
                PaymentCreate(
                    method="GIFT_VOUCHER", amount=100.0, reference="GC-NOPE"
                ),
                current_user=_cashier(),
            )
        assert ei.value.status_code == 400
        assert "voucher" in str(ei.value.detail).lower()
        assert repo.payments == []  # nothing recorded

    async def test_over_balance_voucher_400_and_unchanged(self, wired):
        """Voucher balance (300) < payment amount (300 ok) but here we make
        the card smaller than the requested redeem to force the redeem guard
        to reject -> 400, card untouched, no payment recorded.

        balance_due is 1000 so the order's own balance check passes; the
        FAILURE must come from the voucher engine, proving the branch runs
        before recording."""
        from fastapi import HTTPException

        store, repo = wired
        await _issue(200.0, "GC-SMALL")  # card only has 200

        with pytest.raises(HTTPException) as ei:
            await add_payment(
                "O-1",
                PaymentCreate(
                    method="GIFT_VOUCHER", amount=250.0, reference="GC-SMALL"
                ),
                current_user=_cashier(),
            )
        assert ei.value.status_code == 400
        assert "voucher" in str(ei.value.detail).lower()
        # Card untouched, still ACTIVE, no redemption, no payment.
        doc = store.find_one({"code": "GC-SMALL"})
        assert doc["balance"] == 200.0
        assert doc["status"] == "ACTIVE"
        assert doc["redemptions"] == []
        assert repo.payments == []

    async def test_missing_code_400(self, wired):
        from fastapi import HTTPException

        _store, repo = wired
        with pytest.raises(HTTPException) as ei:
            await add_payment(
                "O-1",
                PaymentCreate(method="GIFT_VOUCHER", amount=100.0),
                current_user=_cashier(),
            )
        assert ei.value.status_code == 400
        assert repo.payments == []


@pytest.mark.asyncio
class TestNonVoucherPaymentsUnaffected:
    async def test_cash_payment_records_without_touching_vouchers(self, wired):
        """A CASH payment must NOT invoke the voucher engine and records
        exactly as before — proves the branch is gated on GIFT_VOUCHER."""
        _store, repo = wired
        resp = await add_payment(
            "O-1",
            PaymentCreate(method="CASH", amount=500.0),
            current_user=_cashier(),
        )
        assert resp["amount"] == 500.0
        assert len(repo.payments) == 1
        assert repo.payments[0]["method"] == "CASH"

    async def test_cash_over_balance_still_rejected(self, wired):
        """Existing balance_due guard is unchanged for non-voucher methods."""
        from fastapi import HTTPException

        _store, repo = wired
        with pytest.raises(HTTPException) as ei:
            await add_payment(
                "O-1",
                PaymentCreate(method="CASH", amount=5000.0),  # > balance_due 1000
                current_user=_cashier(),
            )
        assert ei.value.status_code == 400
        assert "exceeds balance due" in str(ei.value.detail).lower()
        assert repo.payments == []
