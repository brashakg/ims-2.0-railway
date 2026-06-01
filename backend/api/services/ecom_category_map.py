"""
IMS 2.0 - E-commerce Category Map  (BVI Phase 1 foundation)
===========================================================
Bidirectional mapping between the THREE category vocabularies that meet when
BVI's Shopify PIM is folded into IMS (BVI_MERGE_PLAN.md A.2 "Category enum
mismatch"):

    IMS category   (products.category enum: FRAME / SUNGLASS / OPTICAL_LENS /
                    CONTACT_LENS / READING_GLASSES / WATCH / ACCESSORIES ...)
        <->
    BVI category   (SPECTACLES / SUNGLASSES / SOLUTIONS / READING_GLASSES /
                    WATCHES ...)
        <->
    Shopify productType  (the storefront-facing string, e.g. "Eyeglasses")

This is the single source of truth for the enum reconciliation; it feeds the
auto-collection lineage in later phases. Pure functions over a small static
lookup table -- no DB, no I/O.

FAIL-SOFT CONTRACT (locked): an unknown / unmapped value is PASSED THROUGH
unchanged rather than raising or dropping it. A new IMS category that nobody has
mapped yet still flows to Shopify as itself instead of vanishing, and a Shopify
productType we don't recognise round-trips back without data loss. Callers can
opt into strictness by checking `is_known_*` first.

The functions are case-insensitive on input and normalise IMS/BVI keys to
UPPER_SNAKE (the enum convention) while preserving Shopify's human casing.
"""
from __future__ import annotations

from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# The canonical table. ONE row per concept; columns are the three vocabularies.
# IMS values are the products.category enum (database/schemas.py PRODUCT_SCHEMA).
# BVI values are BVI's PIM category enum. Shopify is the storefront productType.
# Add a row here to teach the bridge a new category -- nothing else changes.
# ---------------------------------------------------------------------------
_TABLE: List[Dict[str, str]] = [
    {"ims": "FRAME", "bvi": "SPECTACLES", "shopify": "Eyeglasses"},
    {"ims": "SUNGLASS", "bvi": "SUNGLASSES", "shopify": "Sunglasses"},
    {"ims": "OPTICAL_LENS", "bvi": "LENSES", "shopify": "Eyeglass Lenses"},
    {"ims": "CONTACT_LENS", "bvi": "CONTACT_LENSES", "shopify": "Contact Lenses"},
    {"ims": "COLORED_CONTACT_LENS", "bvi": "COLOR_CONTACT_LENSES", "shopify": "Color Contact Lenses"},
    {"ims": "READING_GLASSES", "bvi": "READING_GLASSES", "shopify": "Reading Glasses"},
    {"ims": "WATCH", "bvi": "WATCHES", "shopify": "Watches"},
    {"ims": "SMARTWATCH", "bvi": "SMARTWATCHES", "shopify": "Smartwatches"},
    {"ims": "SMARTGLASSES", "bvi": "SMARTGLASSES", "shopify": "Smart Glasses"},
    {"ims": "WALL_CLOCK", "bvi": "CLOCKS", "shopify": "Clocks"},
    {"ims": "ACCESSORIES", "bvi": "ACCESSORIES", "shopify": "Accessories"},
    # Contact-lens care fluids live under BVI "SOLUTIONS"; IMS files them as
    # accessories (they are not a distinct IMS product category).
    {"ims": "ACCESSORIES", "bvi": "SOLUTIONS", "shopify": "Lens Solutions"},
    {"ims": "SERVICES", "bvi": "SERVICES", "shopify": "Services"},
]

# Build directed lookups once at import. For the many->one direction (two BVI
# rows map to IMS ACCESSORIES), FIRST row wins so the reverse map is stable and
# the primary representative is the earlier, more-specific entry.
_IMS_TO_BVI: Dict[str, str] = {}
_IMS_TO_SHOPIFY: Dict[str, str] = {}
_BVI_TO_IMS: Dict[str, str] = {}
_BVI_TO_SHOPIFY: Dict[str, str] = {}
_SHOPIFY_TO_IMS: Dict[str, str] = {}
_SHOPIFY_TO_BVI: Dict[str, str] = {}

for _row in _TABLE:
    _ims, _bvi, _shop = _row["ims"], _row["bvi"], _row["shopify"]
    _IMS_TO_BVI.setdefault(_ims, _bvi)
    _IMS_TO_SHOPIFY.setdefault(_ims, _shop)
    _BVI_TO_IMS.setdefault(_bvi, _ims)
    _BVI_TO_SHOPIFY.setdefault(_bvi, _shop)
    _SHOPIFY_TO_IMS.setdefault(_shop.lower(), _ims)
    _SHOPIFY_TO_BVI.setdefault(_shop.lower(), _bvi)


# ---------------------------------------------------------------------------
# Normalisers
# ---------------------------------------------------------------------------

def _norm_enum(value: Optional[str]) -> str:
    """Normalise an IMS/BVI enum-style key: trim, upper, spaces/hyphens -> '_'."""
    if not value:
        return ""
    return value.strip().upper().replace("-", "_").replace(" ", "_")


def _norm_shopify(value: Optional[str]) -> str:
    """Normalise a Shopify productType for case-insensitive matching."""
    if not value:
        return ""
    return value.strip()


# ---------------------------------------------------------------------------
# IMS <-> BVI
# ---------------------------------------------------------------------------

def ims_to_bvi(ims_category: Optional[str]) -> str:
    """IMS category -> BVI category. Unknown -> passthrough (normalised)."""
    key = _norm_enum(ims_category)
    return _IMS_TO_BVI.get(key, key)


def bvi_to_ims(bvi_category: Optional[str]) -> str:
    """BVI category -> IMS category. Unknown -> passthrough (normalised)."""
    key = _norm_enum(bvi_category)
    return _BVI_TO_IMS.get(key, key)


# ---------------------------------------------------------------------------
# IMS <-> Shopify productType
# ---------------------------------------------------------------------------

def ims_to_shopify_type(ims_category: Optional[str]) -> str:
    """IMS category -> Shopify productType. Unknown -> passthrough (normalised
    enum key, e.g. an unmapped 'GADGET' returns 'GADGET')."""
    key = _norm_enum(ims_category)
    return _IMS_TO_SHOPIFY.get(key, key)


def shopify_type_to_ims(shopify_type: Optional[str]) -> str:
    """Shopify productType -> IMS category. Case-insensitive. Unknown ->
    passthrough (the original trimmed string)."""
    raw = _norm_shopify(shopify_type)
    return _SHOPIFY_TO_IMS.get(raw.lower(), raw)


# ---------------------------------------------------------------------------
# BVI <-> Shopify productType
# ---------------------------------------------------------------------------

def bvi_to_shopify_type(bvi_category: Optional[str]) -> str:
    """BVI category -> Shopify productType. Unknown -> passthrough."""
    key = _norm_enum(bvi_category)
    return _BVI_TO_SHOPIFY.get(key, key)


def shopify_type_to_bvi(shopify_type: Optional[str]) -> str:
    """Shopify productType -> BVI category. Case-insensitive. Unknown ->
    passthrough (the original trimmed string)."""
    raw = _norm_shopify(shopify_type)
    return _SHOPIFY_TO_BVI.get(raw.lower(), raw)


# ---------------------------------------------------------------------------
# Introspection helpers (let callers opt into strictness)
# ---------------------------------------------------------------------------

def is_known_ims(ims_category: Optional[str]) -> bool:
    return _norm_enum(ims_category) in _IMS_TO_BVI


def is_known_bvi(bvi_category: Optional[str]) -> bool:
    return _norm_enum(bvi_category) in _BVI_TO_IMS


def is_known_shopify_type(shopify_type: Optional[str]) -> bool:
    return _norm_shopify(shopify_type).lower() in _SHOPIFY_TO_IMS


def all_mappings() -> List[Dict[str, str]]:
    """Return a copy of the full mapping table (for the module summary / UI)."""
    return [dict(r) for r in _TABLE]
