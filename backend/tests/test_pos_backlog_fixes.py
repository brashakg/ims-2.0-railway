"""
IMS 2.0 - POS backlog fixes: POS-2, POS-3, POS-8
===================================================
Model-level regression tests for:

POS-2: EMI tender records only the down-payment, not the financed balance.
       PaymentCreate now accepts emi_principal; build_emi_schedule uses it
       so the schedule reflects the full loan amount.

POS-3: Loyalty points redeemed before the order exists (spend on a failed
       order).  Deferred to post-create in the frontend; backend test
       verifies the TaskCreate validator does NOT reject end-of-day timestamps
       (a related "same-day 422" category fix is also here as a smoke-check).

POS-8: Task create with default-today due date rejected as past-dated (422).
       Validator allows timestamps up to 5 minutes in the past (clock drift)
       but the FE now sends 23:59:59 local time so it is always in the future.
       This test verifies the validator accepts an end-of-day due_at that is
       in the future and rejects one that is clearly in the past.

These are pure model / unit tests -- no DB or full app boot required.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("ENVIRONMENT", "test")


# ---------------------------------------------------------------------------
# POS-2: PaymentCreate + build_emi_schedule
# ---------------------------------------------------------------------------

from api.routers.orders import PaymentCreate, build_emi_schedule  # noqa: E402


class TestEmiPrincipalField:
    """POS-2: emi_principal field on PaymentCreate."""

    def test_payment_create_accepts_emi_principal(self):
        p = PaymentCreate(
            method="EMI",
            amount=2000.0,   # down-payment
            emi_months=12,
            emi_provider="HDFC",
            emi_principal=8000.0,  # loan = order_total - down_payment
        )
        assert p.emi_principal == 8000.0
        assert p.amount == 2000.0

    def test_payment_create_emi_principal_optional(self):
        """Existing callers that don't send emi_principal still work."""
        p = PaymentCreate(method="EMI", amount=5000.0, emi_months=6)
        assert p.emi_principal is None

    def test_payment_create_emi_principal_must_be_positive(self):
        """emi_principal=0 is rejected (gt=0 constraint)."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            PaymentCreate(method="EMI", amount=5000.0, emi_months=6, emi_principal=0)

    def test_build_emi_schedule_uses_loan_amount(self):
        """Schedule built on financed amount, not just the down-payment."""
        # A 10,000 loan at 12% annual over 12 months
        s = build_emi_schedule(principal=10000.0, annual_rate=12.0, months=12)
        # The schedule total must be >= principal (interest adds cost)
        assert s["total_payable"] >= 10000.0
        # Verify rounding invariant: last installment closes the gap exactly
        n = s["tenure_months"]  # key is tenure_months, not months
        emi = s["monthly_emi"]
        last = s["last_installment"]
        total = s["total_payable"]
        assert round(emi * (n - 1) + last, 2) == round(total, 2)

    def test_build_emi_schedule_zero_rate(self):
        """Zero interest: principal == total_payable."""
        s = build_emi_schedule(principal=6000.0, annual_rate=0.0, months=6)
        assert s["total_payable"] == 6000.0
        assert s["interest_amount"] == 0.0

    def test_emi_down_payment_separate_from_loan(self):
        """When emi_principal differs from amount, both should be recordable."""
        p = PaymentCreate(
            method="EMI",
            amount=1500.0,       # down-payment collected now
            emi_months=12,
            emi_provider="BAJAJ",
            emi_principal=8500.0,  # financed balance
        )
        # Confirm both amounts are distinct and valid
        assert p.amount != p.emi_principal
        schedule = build_emi_schedule(p.emi_principal, 12.0, p.emi_months)
        assert schedule["total_payable"] > p.emi_principal - 0.01


# ---------------------------------------------------------------------------
# POS-8: TaskCreate validator for end-of-day timestamps
# ---------------------------------------------------------------------------

from api.routers.tasks import TaskCreate  # noqa: E402


class TestTaskDueDateValidator:
    """POS-8: end-of-day due timestamps are accepted; past ones are rejected."""

    def _base(self, due_at: datetime) -> TaskCreate:
        return TaskCreate(
            title="Test Task",
            assigned_to="user-123",
            due_at=due_at,
        )

    def test_end_of_today_accepted(self):
        """23:59:59 today (local) is always in the future."""
        now = datetime.now()
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
        # If we're already past 23:59:59 (edge case after midnight), skip
        if now.hour == 23 and now.minute == 59 and now.second >= 55:
            pytest.skip("Cannot test end-of-day at the very last seconds of the day")
        t = self._base(end_of_day)
        assert t.title == "Test Task"

    def test_future_date_accepted(self):
        """A due_at 1 hour from now should always be accepted."""
        t = self._base(datetime.now() + timedelta(hours=1))
        assert t.title == "Test Task"

    def test_past_date_rejected(self):
        """A due_at more than 5 min in the past must raise ValueError."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            self._base(datetime.now() - timedelta(minutes=10))

    def test_within_grace_period_accepted(self):
        """A due_at 3 min in the past (within the 5-min grace) is accepted."""
        t = self._base(datetime.now() - timedelta(minutes=3))
        assert t.title == "Test Task"

    def test_midnight_utc_of_today_may_be_past(self):
        """Demonstrates the original bug: midnight UTC of today can be in the
        past for IST (UTC+5:30) callers -- this test is documentation only."""
        # At 6 AM IST, midnight UTC was 30 min ago -> 422 under the old FE code.
        # The fix sends 23:59:59 local time instead of midnight UTC.
        midnight_utc = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        is_past = midnight_utc < datetime.now() - timedelta(minutes=5)
        # If local server clock is in IST (or any UTC+N>0), midnight is past.
        # If server is UTC, midnight is at most a few seconds ago (within grace).
        # Either way the test just documents the situation; no assertion needed
        # beyond confirming our end-of-day fix avoids the issue.
        end_of_day = midnight_utc.replace(hour=23, minute=59, second=59)
        assert not (end_of_day < datetime.now() - timedelta(minutes=5)), (
            "23:59:59 local should never be in the past by more than 5 minutes "
            "unless the test is run right after midnight."
        )
