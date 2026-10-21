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
door (clean_page_type), the Shopify push (templateSuffix + inventoryPolicy +
a preview-only listing's 0 quantity) and the online order claim (a pre-order
line with no unit waits for stock).

THE GATE: ``system_settings.web_page_types_enabled`` (SUPERADMIN, Settings >
System; default False). While it is off every reader takes STANDARD --
``effective_page_type`` is the one door every behaviour reads, always off the
model's LEAD (a colour/size never decides its listing's page). No IMS imports.
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


def pages_enabled(db) -> bool:
    """The SUPERADMIN switch (system_settings 'default' doc). Fail-closed:
    unreadable or unset -> False (every page reads STANDARD)."""
    try:
        coll = db.get_collection("system_settings") if hasattr(db, "get_collection") else db["system_settings"]
        doc = coll.find_one({"_id": "default"}) if coll is not None else None
    except Exception:  # noqa: BLE001
        return False
    return bool((doc or {}).get("web_page_types_enabled") is True)


def lead_attributes(db, spines: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """{key: the attributes its LISTING's page is read from} for spine docs
    under any key (product_id, sku): a colour/size (variant_of) reads its
    lead's, one read for every lead. A lead that cannot be found falls back to the doc's
    own. Raises when the products read fails (the caller decides)."""
    leads = {str(d.get("variant_of")) for d in spines.values() if d.get("variant_of")}
    found: Dict[str, Dict[str, Any]] = {}
    if leads:
        coll = db.get_collection("products") if hasattr(db, "get_collection") else db["products"]
        for doc in coll.find({"product_id": {"$in": sorted(leads)}}, {"product_id": 1, "attributes": 1}):
            found[str(doc.get("product_id"))] = doc.get("attributes") or {}
    return {
        pid: found.get(str(d.get("variant_of")), d.get("attributes") or {})
        if d.get("variant_of") else (d.get("attributes") or {})
        for pid, d in spines.items()
    }


def effective_page_type(db, doc: Optional[Dict[str, Any]], *, enabled: Optional[bool] = None) -> str:
    """THE page type behaviour follows: STANDARD while the gate is off, else
    the doc's own (a listing twin or a lead spine -- callers holding a
    colour/size resolve its lead with lead_attributes first). ``enabled``
    skips the gate read when the caller already has it (a batch)."""
    if not (pages_enabled(db) if enabled is None else enabled):
        return STANDARD
    return page_type_of(doc)


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
