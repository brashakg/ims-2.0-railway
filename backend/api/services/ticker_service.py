"""
IMS 2.0 - F34 Global Target Ticker shared logic
================================================
Pure, DB-light helpers shared by:
  * GET /api/v1/finance/target-ticker (privacy-stratified live card)
  * ORACLE._check_milestones (hourly milestone bell)

The MTD revenue math MUST match the Finance module exactly, so it reuses the
SAME aggregation expression + status exclusion + IST month boundary as
finance.py::get_revenue (no second source of truth). Budget targets are read
from the existing `budgets` collection ({store_id, period=YYYY-MM, head=REVENUE}).

Privacy: management roles see rupees; floor roles see ONLY pct_complete. The
masking happens here so both the live card and any future consumer strip the
same fields the same way -- the client can NEVER elevate visibility because the
money keys are simply absent from the masked dict.

No emoji (Windows cp1252).
"""
from datetime import timedelta
from typing import Any, Dict, List, Optional

from api.utils.ist import ist_today, ist_day_start_utc

# Mirror finance.py exactly (single source of truth for "real" revenue).
# HISTORICAL = pre-IMS imported order (settled outside IMS books; never revenue).
_REVENUE_EXPR = {"$ifNull": ["$grand_total", {"$ifNull": ["$total", 0]}]}
_EXCLUDED_ORDER_STATUSES = [
    "CANCELLED", "DRAFT", "cancelled", "draft", "HISTORICAL", "historical",
]
_REAL_ORDER_STATUS_FILTER = {"$nin": _EXCLUDED_ORDER_STATUSES}

# Roles that may see raw rupee figures + per-store breakdown + pace.
RAW_ROLES = {"SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT"}
# Roles that get the celebratory milestone bell (store-floor only; management
# tiers get none -- DECISIONS sec 7 recommended default).
FLOOR_NOTIFY_ROLES = {"SALES_CASHIER", "SALES_STAFF", "CASHIER"}

# Money keys stripped from the per-store payload for non-management roles.
_MASKED_KEYS = ("monthly_target", "mtd_revenue", "pace_revenue", "pace_delta")

DEFAULT_MILESTONE_PCTS = [25, 50, 75, 100]
DEFAULT_REFRESH_SECONDS = 60


def current_period() -> str:
    """Current IST calendar month as YYYY-MM (the `budgets.period` format)."""
    today = ist_today()
    return "%04d-%02d" % (today.year, today.month)


def raw_visible_for(user: Optional[dict]) -> bool:
    """Server-side privacy decision from the JWT role -- never trust the client.
    activeRole wins; falls back to the first entry of roles[]."""
    user = user or {}
    role = user.get("activeRole") or (user.get("roles") or [None])[0]
    return role in RAW_ROLES


def _month_bounds():
    """(month_start, days_elapsed, days_in_month) for the current IST month.
    month_start is the NAIVE-UTC instant of IST-midnight on the 1st (the >= bound
    matching naive-UTC `created_at`)."""
    today = ist_today()
    month_start = ist_day_start_utc(today.replace(day=1))
    if today.month == 12:
        next_month_first = today.replace(year=today.year + 1, month=1, day=1)
    else:
        next_month_first = today.replace(month=today.month + 1, day=1)
    days_in_month = (next_month_first - today.replace(day=1)).days
    days_elapsed = today.day
    return month_start, days_elapsed, days_in_month


def mtd_revenue(orders_coll, store_id: Optional[str]) -> float:
    """MTD booked revenue for a store (or all stores when store_id is None).
    Uses the SAME _REVENUE_EXPR + status exclusion + IST month start as
    finance.py::get_revenue. Fail-soft: any error -> 0.0."""
    if orders_coll is None:
        return 0.0
    month_start, _, _ = _month_bounds()
    match: Dict[str, Any] = {
        "created_at": {"$gte": month_start},
        "status": _REAL_ORDER_STATUS_FILTER,
    }
    if store_id:
        match["store_id"] = store_id
    try:
        rows = list(orders_coll.aggregate([
            {"$match": match},
            {"$group": {"_id": None, "total_revenue": {"$sum": _REVENUE_EXPR}}},
        ]))
    except Exception:  # noqa: BLE001
        return 0.0
    if not rows:
        return 0.0
    return float(rows[0].get("total_revenue") or 0.0)


def compute_store_entry(
    *,
    store_id: str,
    store_name: str,
    monthly_target: Optional[float],
    mtd: float,
    days_elapsed: int,
    days_in_month: int,
    milestones_fired: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Build the FULL (management) per-store entry. Pace is the linear target for
    today's date (target * days_elapsed / days_in_month); pace_delta>0 = ahead.
    A missing/zero target -> no_target=True and NO fabricated number."""
    milestones_fired = list(milestones_fired or [])
    no_target = not monthly_target or monthly_target <= 0
    if no_target:
        return {
            "store_id": store_id,
            "store_name": store_name,
            "monthly_target": None,
            "mtd_revenue": round(float(mtd), 2),
            "pct_complete": 0.0,
            "days_elapsed": days_elapsed,
            "days_in_month": days_in_month,
            "pace_revenue": 0,
            "pace_delta": 0,
            "milestones_fired": milestones_fired,
            "no_target": True,
        }
    target = float(monthly_target)
    pct = round(mtd / target * 100, 1) if target > 0 else 0.0
    pace = round(target * (days_elapsed / days_in_month)) if days_in_month > 0 else 0
    return {
        "store_id": store_id,
        "store_name": store_name,
        "monthly_target": target,
        "mtd_revenue": round(float(mtd), 2),
        "pct_complete": pct,
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
        "pace_revenue": pace,
        "pace_delta": round(mtd - pace),
        "milestones_fired": milestones_fired,
        "no_target": False,
    }


def mask_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Strip every rupee key from a per-store entry for floor roles. The money
    keys are ABSENT (not null) so a client cannot reveal them by flipping a flag.
    store_name is also dropped (floor sees a single company goal, no breakdown)."""
    out = {k: v for k, v in entry.items() if k not in _MASKED_KEYS}
    out.pop("store_name", None)
    return out


def crossed_milestones(pct: float, milestone_pcts: List[int], already_fired: List[int]) -> List[int]:
    """Thresholds at/below the current pct that have NOT yet fired this month."""
    fired = set(already_fired or [])
    return sorted(m for m in (milestone_pcts or []) if m <= pct and m not in fired)
