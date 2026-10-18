"""
IMS 2.0 - Orders numbering / EMI reconcile tests (audit P3-A/B/C)
=================================================================
Three audit findings on the revenue-critical order path:

  P3-A  GST invoice numbering: the financial-year helpers and the UNIQUE
        partial index on `invoice_number` that makes a duplicate physically
        impossible. The mint (SHOPCODE/FYFY/NNNNN, owner ruling 2026-10-18) is
        pinned in test_gst_invoice_number_16.py.

  P3-B  order_number retry. order_number has a UNIQUE sparse index; a
        concurrent collision used to 500. `create_unique` regenerates just
        the number on DuplicateKeyError and retries (bounded), mirroring
        vouchers.issue_voucher. Behaviour-preserving on the success path.

  P3-C  EMI installment reconcile. monthly_emi*months drifted from the true
        total by a few paise. `build_emi_schedule` reports an authoritative
        total_payable and a last_installment that absorbs the remainder so
        the schedule sums EXACTLY to total_payable; interest = total -
        principal.

The fake orders collection enforces the unique order_number the way Mongo
does, so the collision tests are meaningful.
"""

from __future__ import annotations

import copy
import os
import sys
from datetime import datetime
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from api.routers import orders as orders_module
from database.repositories import order_repository as order_repo_module
from database.repositories.order_repository import (
    OrderRepository,
    fy_label,
    fy_start_year,
)


# ============================================================================
# Fakes: a counters collection with atomic $inc, and an orders collection
# whose unique index on order_number raises E11000 on a repeat insert.
# ============================================================================


def _make_dup_key(msg: str = "E11000 duplicate key error"):
    try:
        from pymongo.errors import DuplicateKeyError

        return DuplicateKeyError(msg)
    except Exception:  # pragma: no cover
        return Exception(msg)


class FakeOrdersCollection:
    """Minimal orders collection: insert_one enforces a unique order_number
    (raises E11000 on a repeat), and supports find_one + create_index."""

    def __init__(self) -> None:
        self.docs: List[Dict[str, Any]] = []
        self.indexes: List[Dict[str, Any]] = []
        # Optional forced-collision queue: order_numbers the NEXT insert(s)
        # should reject as duplicates regardless of what's stored, to simulate
        # a racing worker that already took the value.
        self.force_dup_numbers: List[str] = []

    def create_index(self, *_args, **kwargs):
        self.indexes.append(kwargs)
        return kwargs.get("name", "idx")

    def insert_one(self, doc: Dict[str, Any]):
        num = doc.get("order_number")
        if num is not None and num in self.force_dup_numbers:
            self.force_dup_numbers.remove(num)
            raise _make_dup_key(f"E11000 order_number {num}")
        for existing in self.docs:
            if (
                num is not None
                and existing.get("order_number") == num
            ):
                raise _make_dup_key(f"E11000 order_number {num}")
        self.docs.append(copy.deepcopy(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, flt: Dict[str, Any]):
        for d in self.docs:
            if all(d.get(k) == v for k, v in flt.items()):
                return copy.deepcopy(d)
        return None

    def update_one(self, flt, update):
        for d in self.docs:
            if all(d.get(k) == v for k, v in flt.items()):
                d.update(update.get("$set", {}))
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()


def _repo():
    """A repo over the fake orders collection."""
    coll = FakeOrdersCollection()
    return OrderRepository(coll), coll


# ============================================================================
# P3-A: financial-year helpers
# ============================================================================


class TestFinancialYearHelpers:
    def test_fy_start_year_april_is_same_year(self):
        assert fy_start_year(datetime(2026, 4, 1)) == 2026
        assert fy_start_year(datetime(2026, 12, 31)) == 2026

    def test_fy_start_year_jan_to_march_is_previous_year(self):
        assert fy_start_year(datetime(2027, 1, 1)) == 2026
        assert fy_start_year(datetime(2027, 3, 31)) == 2026

    def test_fy_label_format(self):
        assert fy_label(datetime(2026, 4, 1)) == "2026-27"
        assert fy_label(datetime(2027, 3, 31)) == "2026-27"
        assert fy_label(datetime(2026, 3, 31)) == "2025-26"

    def test_fy_label_century_rollover_pads(self):
        # 1 Apr 2099 -> FY 2099-00 (zero padded, not 2099-0).
        assert fy_label(datetime(2099, 4, 1)) == "2099-00"


# ============================================================================
# P3-A: the invoice_number unique index (the mint itself is pinned in
# test_gst_invoice_number_16.py -- owner ruling 2026-10-18 format)
# ============================================================================


class TestInvoiceIndex:
    def test_ensure_invoice_index_is_partial_and_unique(self):
        repo, coll = _repo()
        repo.ensure_invoice_index()
        assert len(coll.indexes) == 1
        idx = coll.indexes[0]
        assert idx.get("unique") is True
        assert idx.get("name") == "uniq_invoice_number"
        # Partial so legacy/DRAFT orders with NO invoice_number aren't indexed
        # and can't collide on a missing value -- only docs whose
        # invoice_number is a string are constrained.
        pfe = idx.get("partialFilterExpression")
        assert pfe == {"invoice_number": {"$type": "string"}}

    def test_ensure_invoice_index_never_raises(self):
        class _Boom(FakeOrdersCollection):
            def create_index(self, *a, **k):
                raise RuntimeError("index build failed")

        repo = OrderRepository(_Boom())
        repo.ensure_invoice_index()  # must not raise


# ============================================================================
# P3-B: order_number retry on duplicate-key
# ============================================================================


class TestOrderNumberRetry:
    def test_success_path_inserts_once(self):
        """No collision -> exactly one insert, doc returned unchanged
        (behaviour-preserving)."""
        repo, coll = _repo()
        calls = {"n": 0}

        def regen():
            calls["n"] += 1
            return f"ORD-REGEN-{calls['n']}"

        data = {"order_id": "O-1", "order_number": "ORD-AAA-1", "grand_total": 100}
        created = repo.create_unique(data, "order_number", regen)
        assert created is not None
        assert created["order_number"] == "ORD-AAA-1"  # original kept
        assert calls["n"] == 0  # regenerate never called
        assert len(coll.docs) == 1

    def test_collision_regenerates_and_retries(self):
        """First insert collides (a racing worker already took ORD-DUP); the
        retry regenerates the number and succeeds."""
        repo, coll = _repo()
        # Pre-seed the colliding number so the first insert hits E11000.
        coll.docs.append({"_id": "other", "order_number": "ORD-DUP"})

        seq = iter(["ORD-FREE-1", "ORD-FREE-2"])

        def regen():
            return next(seq)

        data = {"order_id": "O-2", "order_number": "ORD-DUP", "grand_total": 50}
        created = repo.create_unique(data, "order_number", regen)
        assert created is not None
        assert created["order_number"] == "ORD-FREE-1"  # regenerated value won
        # order_id / _id unchanged across the retry.
        assert created["order_id"] == "O-2"
        assert created["_id"] == "O-2"

    def test_multiple_collisions_then_success(self):
        repo, coll = _repo()
        # Force the next TWO inserts to be treated as duplicates.
        coll.force_dup_numbers = ["ORD-A", "ORD-B"]
        regen_values = iter(["ORD-B", "ORD-C"])

        data = {"order_id": "O-3", "order_number": "ORD-A"}
        created = repo.create_unique(
            data, "order_number", lambda: next(regen_values)
        )
        assert created is not None
        assert created["order_number"] == "ORD-C"
        assert len(coll.docs) == 1

    def test_persistent_collision_raises_after_retries(self):
        """Every attempt collides -> bounded loop gives up and re-raises the
        dup-key (caller turns it into a 500, but it won't loop forever)."""
        repo, coll = _repo()

        class _AlwaysDup(FakeOrdersCollection):
            def insert_one(self, doc):
                raise _make_dup_key("E11000 always")

        repo2 = OrderRepository(_AlwaysDup())
        with pytest.raises(Exception) as ei:
            repo2.create_unique(
                {"order_id": "O-x", "order_number": "n"},
                "order_number",
                lambda: "n",
                max_retries=3,
            )
        assert "e11000" in str(ei.value).lower()

    def test_non_duplicate_error_propagates_immediately(self):
        """A real DB error (not a dup-key) is NOT retried -- it surfaces so the
        caller's compensating rollback runs, matching the old create() except
        path."""
        repo, coll = _repo()

        class _Boom(FakeOrdersCollection):
            def insert_one(self, doc):
                raise RuntimeError("connection reset")

        repo2 = OrderRepository(_Boom())
        calls = {"n": 0}

        def regen():
            calls["n"] += 1
            return "x"

        with pytest.raises(RuntimeError):
            repo2.create_unique(
                {"order_id": "O-y", "order_number": "n"}, "order_number", regen
            )
        assert calls["n"] == 0  # never retried a non-dup error

    def test_generate_order_number_produces_distinct_values(self):
        # The regenerator the router actually passes must yield fresh values so
        # a retry can escape the collision.
        nums = {orders_module.generate_order_number("BV-BOK-01") for _ in range(50)}
        assert len(nums) == 50
        assert all(n.startswith("ORD-BOK01-") for n in nums)


# ============================================================================
# P3-C: EMI schedule reconciliation
# ============================================================================


class TestEmiScheduleReconcile:
    @pytest.mark.parametrize(
        "principal,rate,months",
        [
            (50000.0, 12.0, 6),
            (50000.0, 12.0, 3),
            (99999.0, 15.0, 9),
            (12345.67, 18.0, 12),
            (100000.0, 0.0, 10),  # zero-interest (no-cost EMI)
            (7777.0, 24.0, 24),
            (1.0, 13.5, 4),
        ],
    )
    def test_installments_sum_exactly_to_total_payable(
        self, principal, rate, months
    ):
        s = orders_module.build_emi_schedule(principal, rate, months)
        # (months - 1) equal installments + the reconciling last one.
        total = round(
            s["monthly_emi"] * (months - 1) + s["last_installment"], 2
        )
        assert total == s["total_payable"]

    def test_interest_equals_total_minus_principal(self):
        s = orders_module.build_emi_schedule(50000.0, 12.0, 6)
        assert s["interest_amount"] == round(s["total_payable"] - 50000.0, 2)

    def test_zero_interest_total_equals_principal(self):
        s = orders_module.build_emi_schedule(100000.0, 0.0, 10)
        assert s["total_payable"] == 100000.0
        assert s["interest_amount"] == 0.0
        assert s["monthly_emi"] == 10000.0
        assert s["last_installment"] == 10000.0

    def test_last_installment_within_a_rupee_of_monthly(self):
        # The reconciling installment only absorbs paise-level rounding, so it
        # must never drift more than a rupee from the equal installment.
        s = orders_module.build_emi_schedule(12345.67, 18.0, 12)
        assert abs(s["last_installment"] - s["monthly_emi"]) < 1.0

    def test_schedule_has_expected_keys(self):
        s = orders_module.build_emi_schedule(50000.0, 12.0, 6)
        for k in (
            "tenure_months",
            "annual_rate",
            "monthly_emi",
            "last_installment",
            "total_payable",
            "interest_amount",
        ):
            assert k in s
        assert s["tenure_months"] == 6
        assert s["annual_rate"] == 12.0

    def test_positive_interest_for_interest_bearing_plan(self):
        s = orders_module.build_emi_schedule(50000.0, 12.0, 6)
        # A 12% plan over 6 months must cost more than the principal.
        assert s["total_payable"] > 50000.0
        assert s["interest_amount"] > 0.0
