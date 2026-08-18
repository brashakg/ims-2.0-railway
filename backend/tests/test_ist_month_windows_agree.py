# ============================================================================
# The three month windows must be ONE window
# ============================================================================
# payout, reports /sales/growth and budgets each built their own
# `datetime(year, month, 1)` bound. Correcting payout alone (PR #992) made the
# incentive screen and the budget-vs-actual screen report DIFFERENT revenue for
# the same month -- a contradiction that did not exist before the fix.
#
# These assert the windows AGREE and that they TILE. A future edit to any one
# of them fails here rather than showing an owner two different numbers for the
# same June.

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

import pytest  # noqa: E402

from api.utils.ist import ist_month_window_utc  # noqa: E402
from api.routers.payout import _month_window as payout_window  # noqa: E402
from api.routers.budgets import _month_window as budgets_window  # noqa: E402

MONTHS = [(2026, 1), (2026, 4), (2026, 6), (2026, 12), (2027, 1)]


@pytest.mark.parametrize("year,month", MONTHS)
def test_payout_and_the_shared_window_start_on_the_same_instant(year, month):
    """payout returns (start, next_start, label, label); the shared helper
    returns (start, end_inclusive). The STARTS must be identical -- that is the
    bound an order is measured against."""
    assert payout_window(year, month)[0] == ist_month_window_utc(year, month)[0]


@pytest.mark.parametrize("year,month", MONTHS)
def test_budgets_and_the_shared_window_are_the_same_window(year, month):
    assert budgets_window(f"{year:04d}-{month:02d}") == ist_month_window_utc(year, month)


@pytest.mark.parametrize("year,month", MONTHS)
def test_the_window_starts_at_ist_midnight_not_utc_midnight(year, month):
    """THE DIRECTION. The bound moves BACKWARD: IST midnight on the 1st is
    18:30 UTC on the last day of the previous month. A bound at 00:00 means the
    plain naive-local one is back; a bound at 05:30 means someone moved it
    FORWARD, which is the same error mirrored."""
    start = ist_month_window_utc(year, month)[0]
    assert (start.hour, start.minute) == (18, 30)
    assert start < datetime(year, month, 1)


@pytest.mark.parametrize("year,month", MONTHS)
def test_consecutive_months_tile_with_no_gap_and_no_overlap(year, month):
    end = ist_month_window_utc(year, month)[1]
    ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
    assert end + timedelta(microseconds=1) == ist_month_window_utc(ny, nm)[0]


def test_the_one_second_hole_is_closed():
    """The hand-written copies subtracted a whole SECOND, so an order stamped
    inside that second belonged to neither month."""
    end = ist_month_window_utc(2026, 6)[1]
    nxt = ist_month_window_utc(2026, 7)[0]
    assert (nxt - end) == timedelta(microseconds=1)


def test_a_small_hours_order_on_the_1st_is_inside_its_own_month():
    """THE REQUIREMENT, stated as the order itself. 2026-05-31T20:00 UTC is
    1 June 01:30 IST -- June's order, June's window."""
    stored = datetime(2026, 5, 31, 20, 0)  # naive-UTC, as created_at is stored
    js, je = ist_month_window_utc(2026, 6)
    ms, me = ist_month_window_utc(2026, 5)
    assert js <= stored <= je, "the 1-June IST order fell outside June"
    assert not (ms <= stored <= me), "it was also counted in May"


def test_an_afternoon_order_is_unmoved_positive_control():
    """Without this, shifting every order into the next month would pass."""
    stored = datetime(2026, 6, 15, 11, 0)
    js, je = ist_month_window_utc(2026, 6)
    assert js <= stored <= je
