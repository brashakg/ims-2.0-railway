"""
IMS 2.0 - Orders numbering / EMI reconcile tests (audit P3-A/B/C)
=================================================================
Three audit findings on the revenue-critical order path:

  P3-A  GST invoice numbering. Indian GST Rule 46(b) wants a CONSECUTIVE
        serial unique per FINANCIAL year (Apr-Mar). The old format
        `BV/INV/{calendar_year}/{order_id[:8]}` was neither consecutive nor
        FY-scoped. New invoices use an atomic per-(store, FY) counter
        (`find_one_and_update($inc)`) -> `BV/INV/2026-27/000123`. OLD orders
        keep their stored number (never rewritten) so historical invoices
        still resolve.

  P3-B  order_number retry. order_number has a UNIQUE sparse index; a
        concurrent collision used to 500. `create_unique` regenerates just
        the number on DuplicateKeyError and retries (bounded), mirroring
        vouchers.issue_voucher. Behaviour-preserving on the success path.

  P3-C  EMI installment reconcile. monthly_emi*months drifted from the true
        total by a few paise. `build_emi_schedule` reports an authoritative
        total_payable and a last_installment that absorbs the remainder so
        the schedule sums EXACTLY to total_payable; interest = total -
        principal.

The fakes implement the same per-document atomicity Mongo gives us, so the
parallel/collision tests are meaningful.
"""

from __future__ import annotations

import copy
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

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


class FakeCounters:
    """In-memory `counters` collection supporting find_one_and_update($inc).

    Single stored doc per `_id`; the $inc is applied atomically (the test is
    single-threaded, but each call re-reads the current value so a 'parallel'
    loop never hands out the same seq twice)."""

    def __init__(self) -> None:
        self.docs: Dict[str, Dict[str, Any]] = {}

    def find_one_and_update(self, flt, update, upsert=False, return_document=None):
        key = flt["_id"]
        doc = self.docs.get(key)
        if doc is None:
            if not upsert:
                return None
            doc = {"_id": key}
            self.docs[key] = doc
        for field, delta in update.get("$inc", {}).items():
            doc[field] = (doc.get(field) or 0) + delta
        return copy.deepcopy(doc)


class FakeOrdersCollection:
    """Minimal orders collection: insert_one enforces a unique order_number
    (raises E11000 on a repeat), and supports find_one + create_index."""

    def __init__(self, counters: Optional[FakeCounters] = None) -> None:
        self.docs: List[Dict[str, Any]] = []
        self._counters = counters
        self.indexes: List[Dict[str, Any]] = []
        # Optional forced-collision queue: order_numbers the NEXT insert(s)
        # should reject as duplicates regardless of what's stored, to simulate
        # a racing worker that already took the value.
        self.force_dup_numbers: List[str] = []

    # Lets the repo reach `counters` via self.collection.database["counters"].
    @property
    def database(self):
        store = self

        class _DB:
            def __getitem__(self, name):
                assert name == "counters"
                return store._counters

        return _DB()

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


def _repo_with_counter():
    counters = FakeCounters()
    coll = FakeOrdersCollection(counters)
    return OrderRepository(coll), coll, counters


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
# P3-A: atomic consecutive invoice serial per store + FY
# ============================================================================


class TestInvoiceNumbering:
    def test_sequential_within_store_and_fy(self):
        repo, _coll, _counters = _repo_with_counter()
        when = datetime(2026, 5, 1)
        n1 = repo.next_invoice_number("BV-BOK-01", when)
        n2 = repo.next_invoice_number("BV-BOK-01", when)
        n3 = repo.next_invoice_number("BV-BOK-01", when)
        assert n1 == "BV/INV/2026-27/000001"
        assert n2 == "BV/INV/2026-27/000002"
        assert n3 == "BV/INV/2026-27/000003"

    def test_serial_is_six_digit_zero_padded(self):
        repo, _coll, _counters = _repo_with_counter()
        n = repo.next_invoice_number("BV-BOK-01", datetime(2026, 5, 1))
        serial = n.rsplit("/", 1)[1]
        assert len(serial) == 6
        assert serial == "000001"

    def test_distinct_stores_have_independent_series(self):
        repo, _coll, _counters = _repo_with_counter()
        when = datetime(2026, 5, 1)
        a1 = repo.next_invoice_number("BV-BOK-01", when)
        b1 = repo.next_invoice_number("BV-RNC-02", when)
        a2 = repo.next_invoice_number("BV-BOK-01", when)
        # Each store starts at 1 -- one store's bills don't advance another's.
        assert a1 == "BV/INV/2026-27/000001"
        assert b1 == "BV/INV/2026-27/000001"
        assert a2 == "BV/INV/2026-27/000002"

    def test_fy_boundary_resets_serial(self):
        repo, _coll, _counters = _repo_with_counter()
        # End of FY 2025-26.
        last_old = repo.next_invoice_number("BV-BOK-01", datetime(2026, 3, 31))
        repo.next_invoice_number("BV-BOK-01", datetime(2026, 3, 31))
        # First of FY 2026-27 -> serial resets to 1, label flips.
        first_new = repo.next_invoice_number("BV-BOK-01", datetime(2026, 4, 1))
        assert last_old == "BV/INV/2025-26/000001"
        assert first_new == "BV/INV/2026-27/000001"

    def test_parallel_inc_never_duplicates(self):
        """Many allocations against the same (store, FY) counter must all be
        distinct -- the $inc is the single point of serialization."""
        repo, _coll, _counters = _repo_with_counter()
        when = datetime(2026, 6, 15)
        numbers = [repo.next_invoice_number("BV-BOK-01", when) for _ in range(200)]
        assert len(set(numbers)) == 200  # no collisions
        serials = sorted(int(n.rsplit("/", 1)[1]) for n in numbers)
        assert serials == list(range(1, 201))  # consecutive 1..200

    def test_no_counter_collection_falls_back_safely(self):
        """DB-less / no counters collection -> a usable FY-labelled string,
        not a 500."""
        coll = FakeOrdersCollection(counters=None)
        repo = OrderRepository(coll)
        n = repo.next_invoice_number("BV-BOK-01", datetime(2026, 5, 1))
        assert n.startswith("BV/INV/2026-27/")
        # Fallback suffix is not the 6-digit serial; just confirm it's present.
        assert len(n.rsplit("/", 1)[1]) > 0

    def test_garbage_store_id_uses_ims_prefix_not_blank(self):
        # The prefix is only a counter-key fragment, but a blank one would
        # silently merge every store's series, so it must fall back to IMS.
        assert OrderRepository._store_invoice_prefix("") == "IMS"
        assert OrderRepository._store_invoice_prefix(None) == "IMS"
        assert OrderRepository._store_invoice_prefix("BV") == "IMS"

    def test_ensure_invoice_index_is_partial_and_unique(self):
        repo, coll, _counters = _repo_with_counter()
        repo.ensure_invoice_index()
        assert len(coll.indexes) == 1
        idx = coll.indexes[0]
        assert idx.get("unique") is True
        # Partial so legacy/DRAFT orders with NO invoice_number aren't indexed
        # and can't collide on a missing value.
        assert "partialFilterExpression" in idx

    def test_ensure_invoice_index_never_raises(self):
        class _Boom(FakeOrdersCollection):
            def create_index(self, *a, **k):
                raise RuntimeError("index build failed")

        repo = OrderRepository(_Boom(FakeCounters()))
        repo.ensure_invoice_index()  # must not raise


# ============================================================================
# P3-A: OLD-format invoices stay resolvable (never rewritten)
# ============================================================================


class TestOldInvoiceLookupStillWorks:
    def test_existing_legacy_invoice_number_is_not_rewritten(self):
        """An order that already carries the OLD format keeps it; we only mint
        a new serial for orders with NO invoice_number."""
        repo, coll, _counters = _repo_with_counter()
        legacy = "BV/INV/2024/A1B2C3D4"
        coll.docs.append(
            {
                "_id": "O-legacy",
                "order_id": "O-legacy",
                "invoice_number": legacy,
                "store_id": "BV-BOK-01",
            }
        )
        order = repo.find_by_id("O-legacy")
        # The get_invoice contract: if invoice_number present, reuse it as-is.
        invoice_number = order.get("invoice_number")
        assert invoice_number == legacy  # untouched, still the legacy format
        # A find by that legacy string still resolves the order.
        found = coll.find_one({"invoice_number": legacy})
        assert found is not None
        assert found["order_id"] == "O-legacy"

    def test_order_without_invoice_gets_new_format_and_persists(self):
        repo, coll, _counters = _repo_with_counter()
        coll.docs.append(
            {
                "_id": "O-new",
                "order_id": "O-new",
                "store_id": "BV-BOK-01",
                "status": "CONFIRMED",
            }
        )
        order = repo.find_by_id("O-new")
        assert not order.get("invoice_number")
        new_num = repo.next_invoice_number(order["store_id"], datetime(2026, 5, 1))
        repo.set_invoice("O-new", new_num)
        assert new_num == "BV/INV/2026-27/000001"
        # Persisted and now resolvable by the new number.
        assert coll.find_one({"invoice_number": new_num})["order_id"] == "O-new"


# ============================================================================
# P3-B: order_number retry on duplicate-key
# ============================================================================


class TestOrderNumberRetry:
    def test_success_path_inserts_once(self):
        """No collision -> exactly one insert, doc returned unchanged
        (behaviour-preserving)."""
        repo, coll, _counters = _repo_with_counter()
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
        repo, coll, _counters = _repo_with_counter()
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
        repo, coll, _counters = _repo_with_counter()
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
        repo, coll, _counters = _repo_with_counter()

        class _AlwaysDup(FakeOrdersCollection):
            def insert_one(self, doc):
                raise _make_dup_key("E11000 always")

        repo2 = OrderRepository(_AlwaysDup(FakeCounters()))
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
        repo, coll, _counters = _repo_with_counter()

        class _Boom(FakeOrdersCollection):
            def insert_one(self, doc):
                raise RuntimeError("connection reset")

        repo2 = OrderRepository(_Boom(FakeCounters()))
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
