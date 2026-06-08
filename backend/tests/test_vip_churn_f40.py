"""F40 VIP churn (#40) -- pure scoring INTENT tests (packet T1-T3, T14 gate).

Asserts the personalised-interval model + the HIGH/WATCH/NONE rules, distinct from
the flat-recency churn model. No DB. No emoji.
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.vip_churn import (  # noqa: E402
    is_vip, median_gap_days, risk_label, compute_vip_churn,
)

NOW = datetime(2026, 6, 8, 12, 0, 0)


def _dates_from_gaps(last_ago_days, gaps):
    """Build ascending order dates: most recent is NOW - last_ago_days, then walk
    backwards by each gap. `gaps` are the inter-purchase gaps (newest-first here)."""
    last = NOW - timedelta(days=last_ago_days)
    dates = [last]
    cur = last
    for g in gaps:
        cur = cur - timedelta(days=g)
        dates.append(cur)
    return sorted(dates)


# ---------------------------------------------------------------- T1 + T14 gate


def test_t1_vip_qualification_gate():
    assert is_vip(90000, 5) is False    # LTV below 1,00,000
    assert is_vip(150000, 2) is False   # below 3-order minimum
    assert is_vip(150000, 3) is True    # qualifies


def test_t14_non_vip_excluded_even_if_overdue():
    # LTV 80k, 10 orders, 500 days idle -> NOT a VIP -> no subdoc.
    dates = _dates_from_gaps(500, [30, 30, 30])
    assert compute_vip_churn(dates, ltv=80000, order_count=10, now=NOW) is None


# ---------------------------------------------------------------- T2 interval vs flat


def test_t2a_long_cadence_watch_not_high():
    # median gap 310, last 340 ago -> overdue 30 -> WATCH (flat-recency would miss it)
    dates = _dates_from_gaps(340, [300, 310, 330])
    sub = compute_vip_churn(dates, ltv=200000, order_count=4, now=NOW)
    assert sub["usual_interval_days"] == 310
    assert sub["overdue_by_days"] == 30
    assert sub["risk_label"] == "WATCH"


def test_t2b_short_cadence_high_not_low():
    # median gap 30, last 125 ago -> overdue 95 -> HIGH (flat-recency would call it LOW)
    dates = _dates_from_gaps(125, [30, 28, 32])
    sub = compute_vip_churn(dates, ltv=200000, order_count=4, now=NOW)
    assert sub["usual_interval_days"] == 30
    assert sub["overdue_by_days"] == 95
    assert sub["risk_label"] == "HIGH"


# ---------------------------------------------------------------- T3 HIGH rules


def test_t3_high_thresholds():
    # interval 60, last 40 ago -> not overdue -> NONE
    none_sub = compute_vip_churn(_dates_from_gaps(40, [60, 60]), 150000, 3, now=NOW)
    assert none_sub["overdue_by_days"] == -20 and none_sub["risk_label"] == "NONE"
    # interval 60, last 93 ago -> overdue 33 > 50%*60(=30) -> HIGH (interval rule)
    s1 = compute_vip_churn(_dates_from_gaps(93, [60, 60]), 150000, 3, now=NOW)
    assert s1["overdue_by_days"] == 33 and s1["risk_label"] == "HIGH"
    # interval 200, last 291 ago -> overdue 91 > 90 -> HIGH (absolute rule)
    s2 = compute_vip_churn(_dates_from_gaps(291, [200, 200]), 150000, 3, now=NOW)
    assert s2["overdue_by_days"] == 91 and s2["risk_label"] == "HIGH"


def test_risk_label_unit():
    assert risk_label(-5, 60) == "NONE"
    assert risk_label(0, 60) == "NONE"
    assert risk_label(20, 60) == "WATCH"      # 20 <= 30 (50%) and <= 90
    assert risk_label(31, 60) == "HIGH"       # 31 > 30 (50% rule)
    assert risk_label(95, 300) == "HIGH"      # 95 > 90 (absolute rule), though < 50%*300


def test_median_gap_needs_three_orders():
    assert median_gap_days([NOW, NOW - timedelta(days=30)]) is None   # 2 orders -> no baseline
    assert median_gap_days(_dates_from_gaps(10, [30, 40])) == 35      # gaps [30,40] -> median 35
