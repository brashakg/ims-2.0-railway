"""
IMS 2.0 — Lens pricing resolver (pure logic)
==============================================

Resolves the price of a lens given (brand, index, category) + the
customer's Rx params. Used by the POS quote endpoint and the lens-grid
suggestion panel.

Lookup priority:
  1. Exact match in `lens_pricing_masters` (per-SKU pricing — highest precedence)
  2. Range match in `lens_pricing_ranges` (NEW May 2026 — tier pricing
     by sphere/cylinder/addition value range)
  3. Fallback: 404 with a hint (no pricing configured)

Range-match details:
  - Multiple ranges may match (one for sphere, one for cylinder).
    We pick the higher base_price across all matches — "charge for
    the harder lens."
  - Inactive (is_active=False) ranges are excluded.
  - Brand tier multiplier + index multiplier are applied AFTER range
    selection. Coating prices are summed on top.

The function is PURE — no I/O. The router is responsible for fetching
the masters (ranges, exact-pricing, brand, index, coatings) and
passing them in. This makes unit tests trivial.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Any


def _abs(x: Any) -> float:
    """abs(float(x)) with None / non-numeric → 0.0."""
    try:
        return abs(float(x or 0))
    except (TypeError, ValueError):
        return 0.0


def _within_range(value: float, min_value: float, max_value: float) -> bool:
    """Inclusive range membership; uses absolute value because
    optical Rx ranges are typically symmetric around zero
    (e.g. ±2.00 means -2.00 to +2.00).
    """
    return min_value <= value <= max_value or min_value <= -value <= max_value


def find_matching_ranges(
    rx: Dict[str, Any],
    ranges: List[Dict[str, Any]],
    *,
    brand_id: str,
    index_id: str,
    category: str,
) -> List[Dict[str, Any]]:
    """Filter `ranges` to those that match the given Rx + lens config.

    Each range carries `parameter` ('sphere' | 'cylinder' | 'addition')
    and a min/max bracket. Active-only.
    """
    matches: List[Dict] = []
    for r in ranges or []:
        if not r.get("is_active", True):
            continue
        if r.get("brand_id") != brand_id:
            continue
        if r.get("index_id") != index_id:
            continue
        if r.get("category") != category:
            continue
        param = r.get("parameter")
        if param not in {"sphere", "cylinder", "addition"}:
            continue
        rx_val = _abs(rx.get(param))
        try:
            mn = float(r.get("min_value", 0) or 0)
            mx = float(r.get("max_value", 0) or 0)
        except (TypeError, ValueError):
            continue
        if _within_range(rx_val, _abs(mn), _abs(mx)):
            matches.append(r)
    return matches


def find_exact_match(
    exact_pricing: List[Dict[str, Any]],
    *,
    brand_id: str,
    index_id: str,
    category: str,
) -> Optional[Dict[str, Any]]:
    """The legacy exact-match lookup (per-SKU pricing). Returns the
    highest-precedence single row when both brand+index+category match."""
    for p in exact_pricing or []:
        if (
            p.get("brandId") == brand_id
            and p.get("indexId") == index_id
            and p.get("category") == category
        ):
            return p
    return None


def resolve_price(
    *,
    rx: Dict[str, Any],
    brand_id: str,
    index_id: str,
    category: str,
    coatings: Optional[List[str]] = None,
    exact_pricing: Optional[List[Dict[str, Any]]] = None,
    ranges: Optional[List[Dict[str, Any]]] = None,
    brand: Optional[Dict[str, Any]] = None,
    index_master: Optional[Dict[str, Any]] = None,
    coating_masters: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Resolve the total price for a lens given Rx + chosen lens config.

    Returns:
        {
          "ok": bool,
          "source": "exact_match" | "range_match" | "no_pricing",
          "base_price": float,
          "brand_multiplier": float,
          "index_multiplier": float,
          "lens_subtotal": float,
          "coating_total": float,
          "total_price": float,
          "matched_ranges": [list of range docs that contributed],
          "hint": str (only when ok=False),
        }

    Pure function — no DB access. Caller fetches the masters.
    """
    coatings = coatings or []
    coating_masters = coating_masters or []

    base_price: Optional[float] = None
    source = "no_pricing"
    matched_ranges: List[Dict[str, Any]] = []

    # 1. Exact match (highest precedence)
    exact = find_exact_match(
        exact_pricing or [],
        brand_id=brand_id,
        index_id=index_id,
        category=category,
    )
    if exact is not None:
        try:
            base_price = float(exact.get("basePrice") or 0)
            source = "exact_match"
        except (TypeError, ValueError):
            pass

    # 2. Range match — only if no exact-match base_price yet
    if base_price is None:
        matched_ranges = find_matching_ranges(
            rx,
            ranges or [],
            brand_id=brand_id,
            index_id=index_id,
            category=category,
        )
        if matched_ranges:
            try:
                # Pick the highest base_price across all matching ranges.
                # Rationale: charge for the harder lens. Customer with
                # high cyl AND high sphere should pay the higher tier.
                base_price = max(
                    float(r.get("base_price") or 0) for r in matched_ranges
                )
                source = "range_match"
            except (TypeError, ValueError):
                pass

    if base_price is None:
        return {
            "ok": False,
            "source": "no_pricing",
            "base_price": 0.0,
            "brand_multiplier": 1.0,
            "index_multiplier": 1.0,
            "lens_subtotal": 0.0,
            "coating_total": 0.0,
            "total_price": 0.0,
            "matched_ranges": [],
            "hint": (
                f"No exact or range pricing configured for "
                f"brand={brand_id}, index={index_id}, category={category}. "
                f"Add a range under Settings → Lens Master → Pricing Ranges."
            ),
        }

    # 3. Apply multipliers
    brand_mult = 1.0
    if brand:
        try:
            tier = brand.get("tier") or "STANDARD"
            tier_to_mult = {"STANDARD": 1.0, "PREMIUM": 1.2, "LUXURY": 1.5}
            brand_mult = tier_to_mult.get(tier, 1.0)
        except Exception:
            pass

    index_mult = 1.0
    if index_master:
        try:
            index_mult = float(index_master.get("multiplier") or 1.0)
        except (TypeError, ValueError):
            pass

    lens_subtotal = round(base_price * brand_mult * index_mult, 2)

    # 4. Sum coating prices (string match on coating master `code`)
    coating_total = 0.0
    coatings_set = {c.upper().strip() for c in coatings if c}
    for cm in coating_masters:
        if not cm.get("code"):
            continue
        if cm.get("code", "").upper().strip() in coatings_set:
            try:
                coating_total += float(cm.get("price") or 0)
            except (TypeError, ValueError):
                pass

    total = round(lens_subtotal + coating_total, 2)

    return {
        "ok": True,
        "source": source,
        "base_price": round(base_price, 2),
        "brand_multiplier": brand_mult,
        "index_multiplier": index_mult,
        "lens_subtotal": lens_subtotal,
        "coating_total": round(coating_total, 2),
        "total_price": total,
        "matched_ranges": [
            {
                "range_id": r.get("range_id"),
                "parameter": r.get("parameter"),
                "min_value": r.get("min_value"),
                "max_value": r.get("max_value"),
                "base_price": r.get("base_price"),
            }
            for r in matched_ranges
        ],
    }


def detect_overlap(
    new_range: Dict[str, Any],
    existing_ranges: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Returns the first existing range that overlaps with `new_range`,
    or None if there's no conflict. Same-key (brand, index, category,
    parameter) ranges with overlapping min-max brackets are flagged."""
    nb = new_range.get("brand_id")
    ni = new_range.get("index_id")
    nc = new_range.get("category")
    np = new_range.get("parameter")
    try:
        n_mn = _abs(new_range.get("min_value"))
        n_mx = _abs(new_range.get("max_value"))
    except Exception:
        return None
    for r in existing_ranges or []:
        if not r.get("is_active", True):
            continue
        if r.get("range_id") == new_range.get("range_id"):
            # Same row (mid-update) — ignore self
            continue
        if (
            r.get("brand_id"),
            r.get("index_id"),
            r.get("category"),
            r.get("parameter"),
        ) != (nb, ni, nc, np):
            continue
        try:
            r_mn = _abs(r.get("min_value"))
            r_mx = _abs(r.get("max_value"))
        except Exception:
            continue
        # Two brackets [a,b] and [c,d] overlap iff a <= d and c <= b
        if n_mn <= r_mx and r_mn <= n_mx:
            return r
    return None
