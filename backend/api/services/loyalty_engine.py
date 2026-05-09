"""
IMS 2.0 - Loyalty Engine
=========================
Pure earn / redeem / tier math, deliberately stateless. Routers / hooks
call into these helpers and persist the result.

Why pure functions:
  * easy to unit-test in isolation (no DB, no JWT)
  * the same math is reused by the order-create hook AND the explicit
    /loyalty/earn endpoint — keeping it stateless prevents drift.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple


# ============================================================================
# Tier helpers
# ============================================================================


TIER_ORDER: List[str] = ["BRONZE", "SILVER", "GOLD", "PLATINUM"]


def compute_tier(lifetime_earned: int, settings: Dict[str, Any]) -> str:
    """Highest tier the customer's lifetime_earned has cleared."""
    thresholds = settings.get("tier_thresholds", {}) or {}
    tier = "BRONZE"
    for candidate in ("SILVER", "GOLD", "PLATINUM"):
        threshold = int(thresholds.get(candidate, 10**9))
        if lifetime_earned >= threshold:
            tier = candidate
    return tier


def tier_multiplier(tier: str, settings: Dict[str, Any]) -> float:
    mults = settings.get("tier_multipliers", {}) or {}
    try:
        return float(mults.get(tier, 1.0))
    except (TypeError, ValueError):
        return 1.0


# ============================================================================
# Earn calculation
# ============================================================================


def category_multiplier(category: str, settings: Dict[str, Any]) -> float:
    """Default 1.0 if the category isn't configured. Lookup is case-insensitive."""
    mults = settings.get("category_multipliers", {}) or {}
    if not category:
        return 1.0
    cat_upper = str(category).upper()
    # Try exact case + uppercase + a few common aliases.
    for key in (category, cat_upper, cat_upper.replace(" ", "_")):
        if key in mults:
            try:
                return float(mults[key])
            except (TypeError, ValueError):
                continue
    return 1.0


def _line_categories(items: Iterable[Dict[str, Any]]) -> List[Tuple[float, float]]:
    """Return (line_value, category_multiplier_factor) for each line."""
    out: List[Tuple[float, float]] = []
    return out


def calc_earn_points(
    rupee_value: float,
    items: Optional[List[Dict[str, Any]]],
    tier: str,
    settings: Dict[str, Any],
) -> Dict[str, Any]:
    """Compose: per-line(value × category_multiplier) × tier_multiplier ×
    points_per_rupee. Falls back to flat rate when items aren't supplied.

    Returns a dict the caller can stamp on the EARN ledger row:
        { "points": int, "rupee_value": float, "skipped_reason": str|None,
          "tier_at_earn": str, "tier_multiplier": float, ... }
    """
    enabled = bool(settings.get("enabled", True))
    if not enabled:
        return {
            "points": 0,
            "rupee_value": float(rupee_value or 0.0),
            "skipped_reason": "loyalty_disabled",
            "tier_at_earn": tier,
            "tier_multiplier": tier_multiplier(tier, settings),
        }

    rupee_value = float(rupee_value or 0.0)
    min_order = float(settings.get("min_order_for_earn", 0.0) or 0.0)
    if rupee_value < min_order:
        return {
            "points": 0,
            "rupee_value": rupee_value,
            "skipped_reason": "below_min_order",
            "tier_at_earn": tier,
            "tier_multiplier": tier_multiplier(tier, settings),
        }

    rate = float(settings.get("points_per_rupee", 0.01) or 0.0)
    tier_mult = tier_multiplier(tier, settings)

    # Per-line earn when items are given. Each line uses its own category
    # multiplier and its own line value (item_total / line_total / amount).
    weighted_value = 0.0
    if items:
        for line in items:
            value = float(
                line.get("item_total")
                or line.get("line_total")
                or line.get("amount")
                or (float(line.get("unit_price", 0)) * float(line.get("quantity", 1)))
                or 0.0
            )
            cat = (
                line.get("category")
                or line.get("item_type")
                or line.get("product_category")
                or ""
            )
            mult = category_multiplier(cat, settings)
            weighted_value += value * mult
    else:
        # Flat fallback: rupee_value × 1.0
        weighted_value = rupee_value

    raw_points = weighted_value * rate * tier_mult
    points = int(raw_points)  # truncate — points are always integer
    return {
        "points": max(0, points),
        "rupee_value": rupee_value,
        "weighted_value": round(weighted_value, 2),
        "tier_at_earn": tier,
        "tier_multiplier": tier_mult,
        "points_per_rupee": rate,
        "skipped_reason": None if points > 0 else "rounded_to_zero",
    }


# ============================================================================
# Redeem calculation
# ============================================================================


def calc_redeem(
    points: int,
    balance_points: int,
    order_value: Optional[float],
    settings: Dict[str, Any],
) -> Dict[str, Any]:
    """Validate + cap a redeem request.

    Returns:
      ok=True, capped_points, rupee_value, was_capped, reason=None
        when the request is acceptable.
      ok=False, reason="..." when the request fails validation. The
        caller should surface this as a 400.
    """
    points = int(points or 0)
    balance_points = int(balance_points or 0)
    order_value = float(order_value or 0.0)

    if not bool(settings.get("enabled", True)):
        return {"ok": False, "reason": "loyalty_disabled"}
    if points <= 0:
        return {"ok": False, "reason": "points_must_be_positive"}
    min_redeem = int(settings.get("min_redeem_points", 100) or 0)
    if points < min_redeem:
        return {
            "ok": False,
            "reason": f"below_min_redeem ({min_redeem})",
            "min_redeem_points": min_redeem,
        }
    if points > balance_points:
        return {
            "ok": False,
            "reason": "insufficient_balance",
            "balance_points": balance_points,
        }

    rate = float(settings.get("redeem_rupee_per_point", 1.0) or 1.0)
    rupee_for_requested = points * rate

    max_pct = float(settings.get("max_redeem_pct_of_order", 50.0) or 100.0)
    capped_points = points
    capped_rupee = rupee_for_requested
    was_capped = False
    if order_value > 0 and max_pct < 100:
        cap_rupee = order_value * (max_pct / 100.0)
        if rupee_for_requested > cap_rupee:
            was_capped = True
            capped_rupee = round(cap_rupee, 2)
            # Convert rupee cap back to points (floor — never give more
            # rupees than the cap).
            if rate > 0:
                capped_points = int(cap_rupee // rate)
                capped_rupee = round(capped_points * rate, 2)
            else:
                capped_points = 0
                capped_rupee = 0.0

    if capped_points <= 0:
        return {
            "ok": False,
            "reason": "capped_to_zero",
            "max_redeem_pct_of_order": max_pct,
        }

    return {
        "ok": True,
        "requested_points": points,
        "capped_points": capped_points,
        "rupee_value": round(capped_rupee, 2),
        "was_capped": was_capped,
        "max_redeem_pct_of_order": max_pct,
        "redeem_rupee_per_point": rate,
    }


# ============================================================================
# Misc
# ============================================================================


def expiry_for_earn(settings: Dict[str, Any], now: Optional[datetime] = None) -> datetime:
    days = int(settings.get("expiry_days", 365) or 365)
    base = now or datetime.now()
    return base + timedelta(days=max(0, days))
