"""
Website page type (owner 2026-10-19) -- THE rule for what a product's page on
bettervision.in does. One value per MODEL: a model-level attribute
(product_master.MODEL_LEVEL_FIELDS), set on the lead, every colour follows.

  STANDARD      the normal page; oversell blocks (owner ruling 2026-08-25).
  PRE_ORDER     buyable at 0 stock: 'Pre-order' button and 'Ships in about N
                days'. The ONLY page type that may sell without stock.
  PREVIEW_ONLY  not purchasable: 'Enquire on WhatsApp' + 'See it in store'.

Stored as attributes.web_page_type (+ attributes.preorder_ship_days, a whole
number of days, required for PRE_ORDER; it reaches the theme as the
ims.preorder_ship_days metafield like every attribute). Read by the product
door (clean_page_type), the Shopify push (templateSuffix + inventoryPolicy)
and the online order claim (a pre-order line with no unit waits for stock).
Pure; no IMS imports.
"""

from typing import Any, Dict, Optional, Tuple

STANDARD = "STANDARD"
PRE_ORDER = "PRE_ORDER"
PREVIEW_ONLY = "PREVIEW_ONLY"
PAGE_TYPES = (STANDARD, PRE_ORDER, PREVIEW_ONLY)

# The theme templates (shopify-theme/templates/product.<suffix>.json); '' is
# the theme's default product template. 'preorder' (no hyphen) is the Boost
# theme's own pre-order switch: main-product reads `template_suffix contains
# 'preorder'` for the 'Pre-order' button text and drops Buy-it-now.
TEMPLATE_SUFFIX = {STANDARD: "", PRE_ORDER: "preorder", PREVIEW_ONLY: "preview-only"}

MAX_SHIP_DAYS = 365


def _canon(raw: Any) -> str:
    return str(raw or "").strip().upper().replace("-", "_").replace(" ", "_")


def page_type_of(doc: Optional[Dict[str, Any]]) -> str:
    """The page type a product (spine or catalog twin) carries; anything
    blank or unknown is STANDARD."""
    attrs = (doc or {}).get("attributes")
    found = _canon(attrs.get("web_page_type")) if isinstance(attrs, dict) else ""
    return found if found in PAGE_TYPES else STANDARD


def is_preorder(doc: Optional[Dict[str, Any]]) -> bool:
    return page_type_of(doc) == PRE_ORDER


def _whole_days(raw: Any) -> Optional[int]:
    if isinstance(raw, bool):
        return None
    try:
        days = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if days != int(days) or not 1 <= days <= MAX_SHIP_DAYS:
        return None
    return int(days)


def ship_days_of(doc: Optional[Dict[str, Any]]) -> Optional[int]:
    attrs = (doc or {}).get("attributes")
    return _whole_days(attrs.get("preorder_ship_days")) if isinstance(attrs, dict) else None


def clean_page_type(
    attrs: Dict[str, Any],
) -> Tuple[Dict[str, Any], Optional[Tuple[str, str]]]:
    """(attributes with web_page_type canonical and preorder_ship_days a whole
    number, problem) -- problem is (field, message) when the value cannot be
    saved. Attributes without a page type are returned untouched (STANDARD)."""
    out = dict(attrs or {})
    raw = out.get("web_page_type")
    if raw is None or not str(raw).strip():
        return out, None
    page_type = _canon(raw)
    if page_type not in PAGE_TYPES:
        return out, (
            "web_page_type",
            "Website page must be Standard, Pre-order or Preview only.",
        )
    out["web_page_type"] = page_type
    if page_type == PRE_ORDER:
        days = _whole_days(out.get("preorder_ship_days"))
        if days is None:
            return out, (
                "preorder_ship_days",
                "A Pre-order product needs 'Ships in about N days': a whole "
                f"number of days from 1 to {MAX_SHIP_DAYS}.",
            )
        out["preorder_ship_days"] = days
    return out, None
