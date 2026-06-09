"""
IMS 2.0 - F7 Predictive Purchasing: burn-rate reorder math
==========================================================
Pure, side-effect-free helpers ORACLE uses to decide WHICH SKUs are at risk
of stocking out inside the configured horizon and HOW MANY units to suggest
ordering. Kept separate from the agent so the math is unit-testable without a
DB and reused by any future read endpoint.

Intent (packet F7):
  - burn_rate_7d  = units sold in the last 7 days / 7      (units/day)
  - burn_rate_30d = units sold in the last 30 days / 30    (units/day)
  - effective burn rate = burn_rate_7d, falling back to (30d-units / 30) when
    the 7-day window saw zero sales (avoids a phantom "never sells" reading for
    a SKU that sells weekly). The 30d fallback is the same units/day basis.
  - days_remaining = on_hand / effective_burn_rate ; +inf when burn rate == 0
  - a reorder is suggested when days_remaining < horizon_days (default 14)
  - recommended_qty tops the SKU back up to cover (lead_time + horizon) days of
    burn, never below 1 unit and never below the reorder-point gap.

NO emoji (Windows cp1252). Everything here is deterministic + DB-free.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import math

# Days-per-month divisor for the 30-day -> "per day" basis. We keep the 30d
# window literal (sold_30d / 30) so the fallback rate is a clean daily mean;
# 4.33 (weeks/month) is NOT used here because we measure a 30-DAY window, not a
# calendar month. (The packet's "30d / 4.33" phrasing describes a weekly SKU's
# per-week-to-per-day intuition; sold_30d / 30 is the equivalent daily mean and
# is what the tests assert numerically.)
_THIRTY = 30.0
_SEVEN = 7.0


def burn_rates(units_7d: float, units_30d: float) -> Dict[str, float]:
    """Return {burn_rate_7d, burn_rate_30d, effective} units/day.

    effective = 7d rate, or the 30d rate when the 7d window is empty (so a SKU
    that sells weekly is not mistaken for a dead SKU on a quiet week).
    """
    r7 = max(0.0, float(units_7d or 0)) / _SEVEN
    r30 = max(0.0, float(units_30d or 0)) / _THIRTY
    effective = r7 if r7 > 0 else r30
    return {"burn_rate_7d": r7, "burn_rate_30d": r30, "effective": effective}


def days_remaining(on_hand: float, effective_rate: float) -> float:
    """Projected days of stock left at the effective burn rate.

    Returns +inf when nothing is selling (rate == 0) so a no-demand SKU is
    never flagged for reorder no matter how low the stock.
    """
    rate = max(0.0, float(effective_rate or 0))
    if rate <= 0:
        return math.inf
    return max(0.0, float(on_hand or 0)) / rate


def recommended_qty(
    *,
    on_hand: float,
    effective_rate: float,
    horizon_days: int,
    lead_time_days: int,
    reorder_point: float = 0,
) -> int:
    """Units to order so stock covers (lead_time + horizon) days of burn.

    The target cover is generous on purpose: by the time the order lands
    (lead_time) the SKU should still hold a full horizon of cover. Never less
    than 1 (a flagged SKU always gets a non-zero suggestion) and never below
    the reorder-point gap.
    """
    rate = max(0.0, float(effective_rate or 0))
    cover_days = max(1, int(lead_time_days) + int(horizon_days))
    target_cover = math.ceil(rate * cover_days)
    gap = target_cover - max(0.0, float(on_hand or 0))
    rp_gap = float(reorder_point or 0) - max(0.0, float(on_hand or 0))
    qty = max(gap, rp_gap, 1.0)
    return int(math.ceil(qty))


def projected_stockout_iso(now: datetime, days_left: float) -> Optional[str]:
    """ISO timestamp of projected stockout, or None when stock never runs out
    (days_left is +inf). Caps at a sane far-future horizon to avoid overflow."""
    if days_left is None or math.isinf(days_left) or math.isnan(days_left):
        return None
    days_left = max(0.0, float(days_left))
    if days_left > 3650:  # >10y: effectively never; don't emit a date
        return None
    try:
        return (now + timedelta(days=days_left)).isoformat()
    except (OverflowError, ValueError):
        return None


def _order_status_excluded(status: Any) -> bool:
    """Orders in these states never count toward demand (no real sale)."""
    s = str(status or "").upper()
    return s in {"CANCELLED", "DRAFT", "VOID", "VOIDED", "REFUNDED"}


def tally_demand_by_product_store(
    orders: List[Dict[str, Any]],
    *,
    now: datetime,
) -> Dict[tuple, Dict[str, float]]:
    """Roll booked-order line items into per-(product_id, store_id) demand.

    Pure: takes already-fetched order docs (so it works identically over a fake
    in-memory DB and real Mongo - no aggregation pipeline divergence). Counts
    units sold in the trailing 7-day and 30-day windows. ``created_at`` may be a
    datetime or an ISO string; non-parseable rows are skipped (never crash).

    Returns { (product_id, store_id): {"units_7d": x, "units_30d": y,
              "name", "brand", "category"} }.
    """
    cut7 = now - timedelta(days=7)
    cut30 = now - timedelta(days=30)
    out: Dict[tuple, Dict[str, float]] = {}

    for order in orders or []:
        if _order_status_excluded(order.get("status")):
            continue
        created = _coerce_dt(order.get("created_at"))
        if created is None or created < cut30:
            continue
        store_id = order.get("store_id") or order.get("store") or "UNKNOWN"
        in7 = created >= cut7
        for item in order.get("items") or []:
            pid = item.get("product_id") or item.get("sku")
            if not pid:
                continue
            pid = str(pid)
            qty = item.get("quantity")
            qty = 1 if qty is None else qty
            try:
                qty = float(qty)
            except (TypeError, ValueError):
                qty = 1.0
            if qty <= 0:
                continue
            key = (pid, str(store_id))
            slot = out.get(key)
            if slot is None:
                slot = out[key] = {
                    "units_7d": 0.0,
                    "units_30d": 0.0,
                    "name": item.get("product_name") or item.get("name") or "",
                    "brand": item.get("brand") or "",
                    "category": item.get("category") or "",
                }
            slot["units_30d"] += qty
            if in7:
                slot["units_7d"] += qty
            # First non-empty descriptor wins (some lines omit them).
            if not slot["name"]:
                slot["name"] = item.get("product_name") or item.get("name") or ""
            if not slot["brand"]:
                slot["brand"] = item.get("brand") or ""
            if not slot["category"]:
                slot["category"] = item.get("category") or ""
    return out


def _coerce_dt(value: Any) -> Optional[datetime]:
    """Best-effort parse of a created_at field to a naive datetime.

    Accepts datetime (tz-aware coerced to naive) or ISO-8601 string. Returns
    None for anything unparseable.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return None
        if v.endswith("Z"):
            v = v[:-1]
        try:
            dt = datetime.fromisoformat(v)
            return dt.replace(tzinfo=None) if dt.tzinfo else dt
        except ValueError:
            # Date-only or odd formats: try the leading 19 chars (YYYY-MM-DDTHH:MM:SS)
            try:
                dt = datetime.fromisoformat(v[:19])
                return dt.replace(tzinfo=None) if dt.tzinfo else dt
            except ValueError:
                return None
    return None
