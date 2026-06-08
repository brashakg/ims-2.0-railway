"""IMS 2.0 - F40 VIP churn prediction (#40), pure scoring core.

A VIP (LTV >= 1,00,000 AND >= 3 completed orders) is "overdue" relative to their
OWN buying rhythm, not a flat global recency rule: take the median gap between
consecutive completed purchases; overdue = days-since-last-purchase minus that
median. Risk label:
  * NONE  -- not overdue (overdue_by_days <= 0)
  * HIGH  -- overdue_by_days > 90  OR  overdue_by_days > 50% of the usual interval
  * WATCH -- overdue, but below both HIGH triggers

Pure (no DB / no engine imports) so the ORACLE EOD scan + the endpoints stay thin
and the rules are unit-testable. `now` is injected for determinism. No emoji.
"""
import statistics
from datetime import datetime
from typing import List, Optional

VIP_LTV_THRESHOLD = 100000.0   # DECISIONS sec 3 / F40 owner decision (locked)
VIP_MIN_ORDERS = 3             # need >= 2 gaps to have an interval baseline
HIGH_OVERDUE_DAYS = 90         # absolute HIGH trigger
HIGH_INTERVAL_FRACTION = 0.5   # OR overdue > 50% of the usual interval


def _to_naive(dt: Optional[datetime]) -> Optional[datetime]:
    """Strip tzinfo so an aware clock (now_ist()) and naive Mongo `created_at`
    (datetime.now()) can be subtracted without a TypeError. Mongo stores naive IST,
    so dropping the tz on an aware value keeps the same wall-clock instant."""
    if dt is not None and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def is_vip(ltv: float, order_count: int) -> bool:
    return float(ltv or 0) >= VIP_LTV_THRESHOLD and int(order_count or 0) >= VIP_MIN_ORDERS


def median_gap_days(order_dates: List[datetime]) -> Optional[int]:
    """Median gap (days) between consecutive completed orders. None when there are
    fewer than 2 positive gaps (i.e. < 3 orders), so no interval baseline exists."""
    dates = sorted(_to_naive(d) for d in (order_dates or []) if d is not None)
    if len(dates) < VIP_MIN_ORDERS:
        return None
    gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    gaps = [g for g in gaps if g >= 0]
    if len(gaps) < 2:
        return None
    return int(round(statistics.median(gaps)))


def risk_label(overdue_by_days: int, usual_interval_days: int) -> str:
    if overdue_by_days <= 0:
        return "NONE"
    if overdue_by_days > HIGH_OVERDUE_DAYS or overdue_by_days > HIGH_INTERVAL_FRACTION * usual_interval_days:
        return "HIGH"
    return "WATCH"


def compute_vip_churn(
    order_dates: List[datetime], ltv: float, order_count: int, now: datetime
) -> Optional[dict]:
    """Return the `vip_churn_risk` subdoc for a VIP, or None for a non-VIP / a VIP
    without enough history to establish an interval. `last_scanned_at` is `now`
    (caller may overwrite with an IST-naive stamp); `narrative` filled later (top-10)."""
    if not is_vip(ltv, order_count):
        return None
    interval = median_gap_days(order_dates)
    if not interval or interval <= 0:
        return None
    now = _to_naive(now)
    last = max(_to_naive(d) for d in order_dates if d is not None)
    last_ago = (now - last).days
    overdue = last_ago - interval
    score = max(0.0, min(1.0, overdue / interval)) if interval else 0.0
    return {
        "usual_interval_days": interval,
        "last_purchase_days_ago": last_ago,
        "overdue_by_days": overdue,
        "risk_score": round(score, 3),
        "risk_label": risk_label(overdue, interval),
        "last_scanned_at": now,
        "narrative": None,
    }
