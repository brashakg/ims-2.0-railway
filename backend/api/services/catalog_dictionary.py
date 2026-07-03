"""
IMS 2.0 - Catalog Dictionary (settings-managed allowed values per field)
========================================================================
The owner-editable "dictionary" for Add-Product attribute fields, mirroring
the lens_enum_config pattern:

  - Collection `catalog_field_options`: ONE doc per attribute field,
    {"field_id": "<attribute name>", "items": [values...], "updated_at",
    "updated_by"}. Managed from Settings -> Catalog Dictionary.
  - Brand Name is NOT stored here: its allowed values come from the Brand
    Master (`brand_masters`, Settings -> Brand Master), filtered by the
    product category's short prefix (brand.categories stores short codes
    like "FR"/"SG"; an empty categories array means the brand applies to
    every category).

Consumed by:
  - GET /products/categories (products.py) — merges per-field `options`
    into the registry so the Add-Product form renders selects restricted
    to the configured values.
  - product_master.normalise_payload + PUT /products/{id} — server-side
    enforcement: when a field has a configured (non-empty) list, the value
    must match one of them (case-insensitive; canonicalised to the
    configured casing on save).

Every loader is FAIL-SOFT: no db / any error -> empty config, meaning "no
dictionary configured" (enforcement skips). NO emojis (cp1252).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

FIELD_OPTIONS_COLLECTION = "catalog_field_options"
BRAND_COLLECTION = "brand_masters"

# Field names whose values are managed elsewhere (Brand Master), never in the
# catalog_field_options collection. PATCHing them is rejected so there can
# never be two competing sources of truth for the brand list.
BRAND_MANAGED_FIELDS = frozenset({"brand_name", "subbrand"})

# Hard caps so a runaway PATCH can't store unbounded data.
MAX_VALUES_PER_FIELD = 300
MAX_VALUE_LENGTH = 80


def normalize_items(items: Any) -> List[str]:
    """Trim, drop empties, de-dupe case-insensitively (first casing wins),
    cap list + value lengths. Raises ValueError on a non-list payload or an
    over-long value so the router can 400 with a clear message."""
    if not isinstance(items, list):
        raise ValueError("items must be a list of strings")
    out: List[str] = []
    seen: set = set()
    for raw in items:
        s = str(raw or "").strip()
        if not s:
            continue
        if len(s) > MAX_VALUE_LENGTH:
            raise ValueError(
                f"Value '{s[:40]}...' is longer than {MAX_VALUE_LENGTH} characters"
            )
        key = s.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) > MAX_VALUES_PER_FIELD:
            raise ValueError(f"At most {MAX_VALUES_PER_FIELD} values per field")
    return out


# Scope key for lists that apply to every category ("All categories" docs,
# stored WITHOUT a category field).
GLOBAL_SCOPE = "*"


def load_field_options_raw(db) -> Dict[str, Dict[str, List[str]]]:
    """{scope: {field_name: [values...]}} for every CONFIGURED list, where
    scope is a canonical category key ('SUNGLASS', ...) or GLOBAL_SCOPE for
    docs saved without a category. Empty lists are treated as unconfigured
    and dropped. Fail-soft: {} on any error / no db."""
    if db is None:
        return {}
    try:
        coll = db.get_collection(FIELD_OPTIONS_COLLECTION)
        out: Dict[str, Dict[str, List[str]]] = {}
        for doc in coll.find({}):
            field = str(doc.get("field_id") or "").strip()
            items = doc.get("items")
            if not field or not isinstance(items, list):
                continue
            cleaned = [str(i).strip() for i in items if str(i or "").strip()]
            if not cleaned:
                continue
            scope = str(doc.get("category") or "").strip().upper() or GLOBAL_SCOPE
            out.setdefault(scope, {})[field] = cleaned
        return out
    except Exception as e:  # noqa: BLE001 - dictionary read must never break a caller
        logger.warning("[CATALOG-DICT] field options read failed: %s", e)
        return {}


def merged_field_options(
    raw: Dict[str, Dict[str, List[str]]], category: Optional[str]
) -> Dict[str, List[str]]:
    """The EFFECTIVE per-field lists for one category: the All-categories
    lists overlaid by that category's own lists (a category list fully
    REPLACES the global list for that field — fields that share a name across
    categories, e.g. lens_material, no longer bleed into each other)."""
    merged = dict(raw.get(GLOBAL_SCOPE, {}))
    if category:
        merged.update(raw.get(str(category).strip().upper(), {}))
    return merged


def load_field_options(db, category: Optional[str] = None) -> Dict[str, List[str]]:
    """{field_name: [allowed values...]} EFFECTIVE for `category` (global +
    per-category overrides; global-only when category is None). Fail-soft."""
    return merged_field_options(load_field_options_raw(db), category)


def load_brand_options(db, category_prefix: Optional[str] = None) -> Optional[List[str]]:
    """Active Brand Master names applicable to `category_prefix` (a short
    code like 'FR'/'SG'; a brand with an EMPTY categories array applies to
    all). Sorted case-insensitively.

    Returns None (NOT []) when the read failed / no db, so callers can
    distinguish "Brand Master is empty" (enforce nothing selectable) from
    "could not read" (fail-open, skip enforcement)."""
    if db is None:
        return None
    try:
        coll = db.get_collection(BRAND_COLLECTION)
        names: List[str] = []
        seen: set = set()
        for doc in coll.find({"is_active": {"$ne": False}}):
            if doc.get("is_active") is False:  # belt-and-braces vs mock backends
                continue
            cats = doc.get("categories") or []
            if category_prefix and cats and category_prefix not in cats:
                continue
            name = str(doc.get("name") or "").strip()
            if name and name.casefold() not in seen:
                seen.add(name.casefold())
                names.append(name)
        names.sort(key=str.casefold)
        return names
    except Exception as e:  # noqa: BLE001
        logger.warning("[CATALOG-DICT] brand options read failed: %s", e)
        return None


SUBBRAND_COLLECTION = "subbrand_masters"

# Brand Master tiers that map 1:1 onto product discount tiers.
BRAND_TIERS = frozenset({"MASS", "PREMIUM", "LUXURY"})


def _find_active_brand(db, brand_name: str) -> Optional[Dict[str, Any]]:
    """The ACTIVE brand_masters doc whose name matches case-insensitively,
    or None (not found / no db / read failure)."""
    if db is None or not str(brand_name or "").strip():
        return None
    try:
        probe = str(brand_name).strip().casefold()
        for doc in db.get_collection(BRAND_COLLECTION).find({"is_active": {"$ne": False}}):
            if doc.get("is_active") is False:
                continue
            if str(doc.get("name") or "").strip().casefold() == probe:
                return doc
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("[CATALOG-DICT] brand lookup failed: %s", e)
        return None


def load_brand_tier(db, brand_name: str) -> Optional[str]:
    """The Brand Master tier (MASS/PREMIUM/LUXURY) for a brand, or None when
    the brand is unknown / has no valid tier / read failed. Used to DERIVE a
    product's discount tier at create time (owner rule: the tier is set
    brand-wise in Settings, not re-picked per product)."""
    doc = _find_active_brand(db, brand_name)
    if doc is None:
        return None
    tier = str(doc.get("tier") or "").strip().upper()
    return tier if tier in BRAND_TIERS else None


def load_subbrand_options(db, brand_name: str) -> Optional[List[str]]:
    """Subbrand names configured for the ACTIVE Brand-Master brand matching
    `brand_name` (case-insensitive). Returns:
      - None  when the read failed / no db (fail-open, skip enforcement),
      - []    when the brand is unknown or has NO subbrands (subbrand stays
              free-form — mirrors the lens 'series falls open per brand' rule),
      - [...] the configured subbrand names otherwise.
    """
    if db is None or not str(brand_name or "").strip():
        return None
    try:
        probe = str(brand_name).strip().casefold()
        brands = db.get_collection(BRAND_COLLECTION)
        brand_doc = None
        for doc in brands.find({"is_active": {"$ne": False}}):
            if doc.get("is_active") is False:
                continue
            if str(doc.get("name") or "").strip().casefold() == probe:
                brand_doc = doc
                break
        if brand_doc is None or not brand_doc.get("brand_id"):
            return []
        subs = db.get_collection(SUBBRAND_COLLECTION)
        names: List[str] = []
        seen: set = set()
        for sb in subs.find({"brand_id": brand_doc["brand_id"]}):
            name = str(sb.get("name") or "").strip()
            if name and name.casefold() not in seen:
                seen.add(name.casefold())
                names.append(name)
        names.sort(key=str.casefold)
        return names
    except Exception as e:  # noqa: BLE001
        logger.warning("[CATALOG-DICT] subbrand options read failed: %s", e)
        return None


def match_canonical(value: str, allowed: List[str]) -> Optional[str]:
    """Case-insensitive, trimmed membership check. Returns the CONFIGURED
    casing on match (so saved data is canonical), else None."""
    probe = str(value or "").strip().casefold()
    if not probe:
        return None
    for item in allowed:
        if item.casefold() == probe:
            return item
    return None
