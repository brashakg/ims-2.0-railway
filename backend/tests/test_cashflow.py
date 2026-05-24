"""
Unit tests for the cash-flow forecast engine (services/cashflow.py).

Pure math -- no DB. Covers weekly bucketing, inflow/outflow distribution,
running balance, the lowest-point (cash-crunch) detection, past-due handling,
and beyond-horizon overflow.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.cashflow import week_buckets, build_forecast  # noqa: E402


def test_week_buckets_count_and_shape():
    from datetime import datetime

    wk = week_buckets(datetime(2026, 1, 1), 28)
    assert len(wk) == 4
    assert wk[0]["start"] == "2026-01-01"
    assert wk[1]["start"] == "2026-01-08"


def test_forecast_distributes_into_weeks():
    inflows = [
        {"date": "2026-01-03", "amount": 1000},  # week 0
        {"date": "2026-01-10", "amount": 500},   # week 1
    ]
    outflows = [
        {"date": "2026-01-05", "amount": 400},   # week 0
    ]
    f = build_forecast(0, inflows, outflows, "2026-01-01", 21)
    assert f["weeks"][0]["inflow"] == 1000.0
    assert f["weeks"][0]["outflow"] == 400.0
    assert f["weeks"][0]["net"] == 600.0
    assert f["weeks"][0]["closing_balance"] == 600.0
    assert f["weeks"][1]["closing_balance"] == 1100.0
    assert f["totals"]["inflow"] == 1500.0
    assert f["totals"]["outflow"] == 400.0


def test_forecast_running_balance_and_opening_cash():
    f = build_forecast(
        5000,
        [],
        [{"date": "2026-01-02", "amount": 2000}],
        "2026-01-01",
        14,
    )
    assert f["opening_cash"] == 5000.0
    assert f["weeks"][0]["closing_balance"] == 3000.0


def test_forecast_detects_cash_crunch_low_point():
    # Big outflow week 1 drives the balance negative -> lowest point flagged.
    f = build_forecast(
        1000,
        [{"date": "2026-01-20", "amount": 5000}],  # week 2 inflow rescues it
        [{"date": "2026-01-09", "amount": 4000}],  # week 1 outflow
        "2026-01-01",
        28,
    )
    assert f["lowest"]["balance"] == -3000.0
    assert f["lowest"]["week_index"] == 1


def test_forecast_past_due_goes_to_week_zero():
    # An event dated before as_of is treated as due immediately (week 0).
    f = build_forecast(
        0,
        [],
        [{"date": "2025-12-01", "amount": 700}],
        "2026-01-01",
        14,
    )
    assert f["weeks"][0]["outflow"] == 700.0


def test_forecast_beyond_horizon_excluded_from_weeks():
    f = build_forecast(
        0,
        [{"date": "2026-06-01", "amount": 900}],  # far beyond a 14-day horizon
        [],
        "2026-01-01",
        14,
    )
    assert f["beyond_horizon"]["inflow"] == 900.0
    assert f["totals"]["inflow"] == 0.0


def test_forecast_ignores_zero_and_garbage_amounts():
    f = build_forecast(
        0,
        [{"date": "2026-01-02", "amount": 0}, {"date": "2026-01-02", "amount": "x"}],
        [],
        "2026-01-01",
        14,
    )
    assert f["totals"]["inflow"] == 0.0
