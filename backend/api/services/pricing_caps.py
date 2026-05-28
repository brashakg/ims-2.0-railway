"""
IMS 2.0 - Pricing discount-cap resolver (single source of truth)
================================================================

ONE pure module that answers: "for THIS product, what is the maximum discount
percentage off MRP that is allowed, and does a proposed offer_price violate
it?"  Both bulk-pricing endpoints (POST /products/bulk-price and
POST /products/bulk-offer) call this so the cap rules are enforced identically.

The caps are the non-negotiable business rules from CLAUDE.md
(section "Non-negotiable business rules" -> Pricing):

  Category caps  (by product `discount_category`):
      MASS              15%
      PREMIUM           20%
      LUXURY             5%
      SERVICE           10%
      NON_DISCOUNTABLE   0%

  Luxury brand caps  (override the category cap when LOWER):
      Cartier / Chopard / Bvlgari      2%
      Gucci / Prada / Versace / Burberry 5%

These mirror the canonical defaults exposed by the admin discount-rules
endpoint (api/routers/admin_extras.py get_discount_rules), which is the CRUD
surface for the same rules. Kept here as plain constants so the resolver is a
pure function with no DB dependency -- it never raises, so a bulk dry-run can
classify thousands of rows quickly and deterministically.

Resolution rule:
  effective_cap = min(category_cap, brand_cap_if_luxury_brand)

  - A product's `discount_category` drives the category cap. Unknown / missing
    category falls back to MASS (15%), matching orders.py POS-line behaviour
    (a missing category is treated as the most permissive ordinary tier, never
    as unlimited).
  - If the product's brand is one of the named luxury brands, its (lower) brand
    cap further constrains the category cap. Brand match is case-insensitive
    and ignores surrounding whitespace.
  - NON_DISCOUNTABLE is always 0 regardless of brand.

A proposed `offer_price` implies a discount off MRP:
      implied_discount_pct = (mrp - offer_price) / mrp * 100
  - offer_price > mrp  -> "MRP > offer_price" is the blocked-at-DB rule; the
    proposed price is INVALID (reason MRP_BELOW_OFFER).
  - implied_discount_pct > effective_cap (+ small float tolerance) -> the
    proposed price VIOLATES the cap (reason CAP_EXCEEDED).
  - otherwise the proposed price is allowed.

Nothing here mutates state; the router decides whether to persist.
"""

from __future__ import annotations

from typing import Optional

# --- Category caps (percent off MRP), keyed by product `discount_category`. ---
# Source of truth: CLAUDE.md "Non-negotiable business rules" -> Pricing.
CATEGORY_DISCOUNT_CAPS: dict[str, float] = {
    "MASS": 15.0,
    "PREMIUM": 20.0,
    "LUXURY": 5.0,
    "SERVICE": 10.0,
    "NON_DISCOUNTABLE": 0.0,
}

# Tier used when a product carries no (or an unknown) discount_category. MASS
# is the ordinary, most-permissive consumer tier -- never default to unlimited.
DEFAULT_DISCOUNT_CATEGORY = "MASS"

# --- Luxury brand caps (percent off MRP). Override the category cap when the
# brand cap is LOWER. Keys are normalised (upper + stripped) for matching. ---
LUXURY_BRAND_CAPS: dict[str, float] = {
    "CARTIER": 2.0,
    "CHOPARD": 2.0,
    "BVLGARI": 2.0,
    "GUCCI": 5.0,
    "PRADA": 5.0,
    "VERSACE": 5.0,
    "BURBERRY": 5.0,
}

# Float comparison tolerance so a price computed to a clean percentage (e.g.
# exactly the cap) is never spuriously rejected by binary-float dust.
_EPS = 1e-6


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().upper()


def category_cap_for(discount_category: Optional[str]) -> float:
    """Max discount % allowed by the product's discount_category. Unknown /
    missing -> the MASS default (most permissive ordinary tier)."""
    cat = _norm(discount_category) or DEFAULT_DISCOUNT_CATEGORY
    return CATEGORY_DISCOUNT_CAPS.get(
        cat, CATEGORY_DISCOUNT_CAPS[DEFAULT_DISCOUNT_CATEGORY]
    )


def brand_cap_for(brand: Optional[str]) -> Optional[float]:
    """Luxury brand cap for `brand`, or None when the brand is not one of the
    named luxury brands (i.e. no brand-level constraint applies)."""
    return LUXURY_BRAND_CAPS.get(_norm(brand))


def effective_discount_cap(
    discount_category: Optional[str],
    brand: Optional[str] = None,
) -> float:
    """The maximum discount % off MRP allowed for a product, combining the
    category cap with the (lower) luxury brand cap when applicable.

        effective_cap = min(category_cap, brand_cap?)

    Always returns a float in [0, 100]. Never raises.
    """
    cap = category_cap_for(discount_category)
    bcap = brand_cap_for(brand)
    if bcap is not None:
        cap = min(cap, bcap)
    return max(0.0, min(100.0, cap))


def implied_discount_pct(mrp: float, offer_price: float) -> float:
    """Discount percentage off MRP implied by an offer_price.

    Returns 0.0 when mrp <= 0 (cannot compute a meaningful percentage) or when
    offer_price >= mrp (no discount). A negative result (offer_price > mrp) is
    clamped to 0 here; the offer_price > mrp case is caught separately as the
    MRP_BELOW_OFFER block in evaluate_offer_price().
    """
    try:
        mrp_f = float(mrp)
        offer_f = float(offer_price)
    except (TypeError, ValueError):
        return 0.0
    if mrp_f <= 0:
        return 0.0
    pct = (mrp_f - offer_f) / mrp_f * 100.0
    return pct if pct > 0 else 0.0


def evaluate_offer_price(
    mrp: float,
    offer_price: float,
    discount_category: Optional[str] = None,
    brand: Optional[str] = None,
) -> dict:
    """Classify a proposed (mrp, offer_price) against the discount caps.

    Returns a dict:
        {
          "ok": bool,                # True when the price is allowed
          "reason": str | None,      # machine code when not ok
          "message": str | None,     # human explanation when not ok
          "effective_cap_pct": float,
          "implied_discount_pct": float,
        }

    Reasons (when ok is False):
      - "INVALID_MRP"       mrp is non-positive (cannot price)
      - "MRP_BELOW_OFFER"   offer_price exceeds MRP (MRP > offer_price block)
      - "CAP_EXCEEDED"      implied discount exceeds the effective cap

    Pure + deterministic. Never raises; bad inputs classify as INVALID_MRP.
    """
    cap = effective_discount_cap(discount_category, brand)

    try:
        mrp_f = float(mrp)
        offer_f = float(offer_price)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "reason": "INVALID_MRP",
            "message": "MRP and offer price must be numbers.",
            "effective_cap_pct": cap,
            "implied_discount_pct": 0.0,
        }

    if mrp_f <= 0:
        return {
            "ok": False,
            "reason": "INVALID_MRP",
            "message": "MRP must be greater than zero.",
            "effective_cap_pct": cap,
            "implied_discount_pct": 0.0,
        }

    if offer_f <= 0:
        return {
            "ok": False,
            "reason": "INVALID_MRP",
            "message": "Offer price must be greater than zero.",
            "effective_cap_pct": cap,
            "implied_discount_pct": 0.0,
        }

    # MRP > offer_price is the rule blocked at the DB layer: offer can never
    # exceed MRP.
    if offer_f > mrp_f + _EPS:
        return {
            "ok": False,
            "reason": "MRP_BELOW_OFFER",
            "message": (
                f"Offer price Rs {offer_f:,.2f} cannot exceed MRP Rs {mrp_f:,.2f}."
            ),
            "effective_cap_pct": cap,
            "implied_discount_pct": 0.0,
        }

    pct = implied_discount_pct(mrp_f, offer_f)
    if pct > cap + _EPS:
        return {
            "ok": False,
            "reason": "CAP_EXCEEDED",
            "message": (
                f"Discount {pct:.2f}% exceeds the {cap:.0f}% cap for "
                f"{(_norm(discount_category) or DEFAULT_DISCOUNT_CATEGORY).title()}"
                + (
                    f" / {(brand or '').strip()}"
                    if brand_cap_for(brand) is not None
                    else ""
                )
                + "."
            ),
            "effective_cap_pct": cap,
            "implied_discount_pct": round(pct, 2),
        }

    return {
        "ok": True,
        "reason": None,
        "message": None,
        "effective_cap_pct": cap,
        "implied_discount_pct": round(pct, 2),
    }
