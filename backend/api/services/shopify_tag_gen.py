"""
IMS 2.0 - Shopify attribute -> tag generator  (BVI parity for the IMS push)
===========================================================================
When the IMS -> Shopify push (api/services/shopify_push.py) flips LIVE, a
`productUpdate` REPLACES the product's whole `tags` array on Shopify. The
storefront (bettervision.in) filters/facets on those tags. So the IMS push
must reproduce the SAME `<prefix>_<value>` tag tokens that the BVI admin app
auto-generates today, or turning writes on would wipe the storefront's filter
tags.

This module ports the BVI attribute->tag registry into Python so IMS can
derive those tokens from a catalog product's `category` + `attributes`.

SOURCE OF TRUTH (read-only in this repo -- the BVI Next.js app):
  * ecommerce/src/lib/categoryAttributes.ts
      - ATTRIBUTES registry: which attributes carry `tag: true` + their prefix.
        A tag prefix defaults to key.toLowerCase() unless `tagPrefix` overrides
        it (e.g. frameMaterial -> "framematerial", countryOfOrigin -> "origin").
      - CATEGORY_ATTRIBUTES: which attributes apply per category.
      - tagsForProductAttributes(): emits `<prefix>_<slug(value)>` for every
        applicable tag:true attribute (booleans -> "yes"/"no").
      - slugifyTagValue(): lower-case, [^a-z0-9]+ -> "-", strip leading/trailing "-".
  * ecommerce/src/lib/autoGenerate.ts
      - generateTags(): calls tagsForProductAttributes() then adds the manual
        VARIANT-level measurement fallbacks framesize_/bridge_/templelength_/
        framecolor_/templecolor_ from the product body.

KEY TRANSLATION (BVI -> IMS): the BVI registry keys by BVI category names
(SPECTACLES, SUNGLASSES, WATCHES, ...) and camelCase attribute keys
(frameMaterial, frameColor, dialColor). IMS calls us with ITS OWN canonical
category (FRAME, SUNGLASS, WATCH, ... from product_master._CATEGORY_SPECS) and
snake_case attribute keys (frame_material, frame_color, dial_color). This
module therefore keys the ported registry by IMS canonical category + IMS
snake_case attribute keys, mapping each to the BVI-derived tag PREFIX so the
emitted token matches the exact Shopify vocabulary in production
(confirmed against a live tag dump: gender_men, framecolor_havana, shape_round,
frametype_full-frame, framematerial_acetate, dialcolor_blue, watch tokens...).

WHAT IS DELIBERATELY *NOT* EMITTED (matches current BVI generateTags):
  * `category_<slug>` -- BVI's tagsForProductAttributes emits one, but IMS
    already sends the category via Shopify `productType` (build_product_input),
    and the fail-soft contract wants an empty attributes dict -> empty list.
    This generator emits attribute (`prefix_value`) tokens ONLY.
  * subbrand_ / origin_ / productusp_ / lensusp_ -- these are LEGACY prefixes
    from the old Excel-era generator; the CURRENT BVI generateTags does NOT
    emit them (subBrand/countryOfOrigin/productUSP* are tag:false), so neither
    do we (parity with BVI is the invariant).

FAIL-SOFT: an unknown category or attribute is skipped, never raised. This is a
pure, deterministic, network-free payload helper (no emojis -- Windows cp1252).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Value slugifier -- a byte-for-byte port of BVI slugifyTagValue()
# (ecommerce/src/lib/categoryAttributes.ts): lower-case, collapse every run of
# non [a-z0-9] to a single "-", then strip leading/trailing "-".
#   "Ray-Ban"   -> "ray-ban"
#   "Full Frame"-> "full-frame"
#   "1109/71"   -> "1109-71"
#   "UV 400"    -> "uv-400"
# ---------------------------------------------------------------------------
_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SLUG_EDGE_DASH = re.compile(r"^-+|-+$")


def slugify_tag_value(value: Any) -> str:
    """Slugify a value to the Shopify tag convention. Booleans map to yes/no
    (mirrors BVI tagsForProductAttributes). Returns "" for blank/None so a
    whitespace-only value never yields an empty "prefix_" token."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    s = str(value).strip().lower()
    s = _SLUG_NON_ALNUM.sub("-", s)
    s = _SLUG_EDGE_DASH.sub("", s)
    return s


# ---------------------------------------------------------------------------
# The ported registry: IMS canonical category -> ordered [(ims_attr_key, prefix)].
#
# Prefix values are taken verbatim from the BVI ATTRIBUTES registry
# (categoryAttributes.ts): prefix = the attribute's tagPrefix, else
# key.toLowerCase(). The IMS attribute KEY on the left is the snake_case field
# that IMS stores in product `attributes` (product_master._CATEGORY_SPECS).
#
# Only attributes that are `tag: true` in the BVI registry AND that IMS
# actually collects (or may carry from a BVI import) are listed. Order is
# deterministic (brand-identity first, then body, then lens/variant), so the
# emitted list is stable.
# ---------------------------------------------------------------------------

# Identity attrs BVI tags on essentially every category (brand/modelNo/gender/
# warranty are all tag:true). Absent keys are simply skipped, so this base is
# safe on categories that don't collect them (e.g. contact lenses have no gender).
_UNIVERSAL: Tuple[Tuple[str, str], ...] = (
    ("brand_name", "brand"),        # BVI brand        tag:true
    ("model_no", "modelno"),        # BVI modelNo      tag:true
    ("gender", "gender"),           # BVI gender       tag:true
    ("warranty", "warranty"),       # BVI warranty     tag:true
)

# Eyewear FRAME base (BVI SPECTACLES tag:true set + the framesize/bridge/
# templelength/framecolor/templecolor variant fallbacks from generateTags).
_EYEWEAR_FRAME: Tuple[Tuple[str, str], ...] = _UNIVERSAL + (
    ("colour_code", "colorcode"),       # BVI colorCode      tag:true
    ("shape", "shape"),                 # BVI shape          tag:true
    ("frame_type", "frametype"),        # BVI frameType      tag:true
    ("frame_material", "framematerial"),# BVI frameMaterial  tag:true
    ("temple_material", "templematerial"),  # BVI templeMaterial tag:true
    ("frame_color", "framecolor"),      # BVI frameColor + generateTags fallback
    ("temple_color", "templecolor"),    # BVI templeColor + generateTags fallback
    ("lens_size", "framesize"),         # generateTags framesize_ fallback (frameSize)
    ("bridge_width", "bridge"),         # generateTags bridge_ fallback
    ("temple_length", "templelength"),  # generateTags templelength_ fallback
)

# Eyewear SUNGLASS = frame base + the sunglass lens split (BVI SUNGLASSES adds
# lensMaterial/lensColour/tint/polarization/uvProtection, all tag:true).
_EYEWEAR_SUN: Tuple[Tuple[str, str], ...] = _EYEWEAR_FRAME + (
    ("lens_colour", "lenscolour"),      # BVI lensColour     tag:true
    ("tint", "tint"),                   # BVI tint           tag:true
    ("polarization", "polarization"),   # BVI polarization   tag:true
    ("uv_protection", "uvprotection"),  # BVI uvProtection   tag:true
    ("lens_material", "lensmaterial"),  # BVI lensMaterial   tag:true
)

# WATCH -- BVI WATCHES + WATCH_ATTRS tag:true set, in snake_case. IMS's
# _CATEGORY_SPECS currently only collects dial_color + strap_material for a
# watch, but the registry recognises the full BVI watch attribute set so any
# richer watch doc (e.g. imported from BVI) round-trips its filter tags.
_WATCH: Tuple[Tuple[str, str], ...] = _UNIVERSAL + (
    ("colour_code", "colorcode"),
    ("movement", "movement"),               # BVI movement        tag:true
    ("movement_type", "movementtype"),      # BVI movementType    tag:true
    ("features", "features"),               # BVI features        tag:true
    ("watch_functions", "watchfunctions"),  # BVI watchFunctions  tag:true
    ("case_color", "casecolor"),            # BVI caseColor       tag:true
    ("case_material", "casematerial"),      # BVI caseMaterial    tag:true
    ("case_shape", "caseshape"),            # BVI caseShape       tag:true
    ("glass_type", "glasstype"),            # BVI glassType       tag:true
    ("strap_color", "strapcolor"),          # BVI strapColor      tag:true
    ("strap_material", "strapmaterial"),    # BVI strapMaterial   tag:true
    ("dial_color", "dialcolor"),            # BVI dialColor       tag:true
    ("dial_pattern", "dialpattern"),        # BVI dialPattern     tag:true
    ("water_resistance", "waterresistance"),# BVI waterResistance tag:true
)

# SMARTWATCH -- BVI SMARTWATCHES tag:true set, snake_case.
_SMARTWATCH: Tuple[Tuple[str, str], ...] = _UNIVERSAL + (
    ("colour_code", "colorcode"),
    ("case_color", "casecolor"),
    ("case_material", "casematerial"),
    ("strap_color", "strapcolor"),
    ("strap_material", "strapmaterial"),
    ("display_type", "displaytype"),        # BVI displayType     tag:true
    ("os", "os"),                           # BVI os              tag:true
    ("os_compatibility", "oscompatibility"),# BVI osCompatibility tag:true
    ("bluetooth", "bluetooth"),             # BVI bluetooth       tag:true
    ("ai_features", "aifeatures"),          # BVI aiFeatures      tag:true
    ("ai_assistant", "aiassistant"),        # BVI aiAssistant     tag:true
    ("health_sensors", "healthsensors"),    # BVI healthSensors   tag:true
    ("gps", "gps"),                         # BVI gps             tag:true
    ("connectivity", "connectivity"),       # BVI connectivity    tag:true
    ("water_resistance", "waterresistance"),
    ("ipx_rating", "ipxrating"),            # BVI ipxRating       tag:true
)

# CONTACT_LENS -- BVI CONTACT_LENSES tag:true set. IMS stores the wear schedule
# as `modality`, which maps onto BVI's wearSchedule -> wearschedule_ prefix.
_CONTACT_LENS: Tuple[Tuple[str, str], ...] = _UNIVERSAL + (
    ("colour_name", "colorname"),           # BVI colorName       tag:true
    ("modality", "wearschedule"),           # BVI wearSchedule    tag:true (IMS key=modality)
    ("lens_type", "lenstype"),              # BVI lensType        tag:true
    ("material", "contactlensmaterial"),    # BVI contactLensMaterial tag:true
    ("uv_protection", "uvprotection"),      # BVI uvProtection    tag:true
)

# COLORED_CONTACT_LENS = contact-lens set + tint (BVI COLOR_CONTACT_LENSES).
_COLOR_CONTACT_LENS: Tuple[Tuple[str, str], ...] = _CONTACT_LENS + (
    ("tint", "tint"),
)

# ACCESSORIES -- BVI ACCESSORIES tag:true set.
_ACCESSORIES: Tuple[Tuple[str, str], ...] = _UNIVERSAL + (
    ("accessory_type", "accessorytype"),    # BVI accessoryType   tag:true
    ("material", "material"),               # BVI material        tag:true
    ("compatibility", "compatibility"),     # BVI compatibility   tag:true
)

# WALL_CLOCK / OPTICAL_LENS / HEARING_AID -- not BVI categories. Emit the
# identity base only (brand/modelno/warranty when present).
_MINIMAL: Tuple[Tuple[str, str], ...] = _UNIVERSAL


# IMS canonical category -> its ordered tag map. Keyed by the long-form
# canonical value from product_master._CATEGORY_SPECS. Anything not listed
# (that still resolves to a known IMS category) falls back to _MINIMAL;
# SERVICES intentionally emits no tags.
_CATEGORY_TAG_MAP: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "FRAME": _EYEWEAR_FRAME,
    "SUNGLASS": _EYEWEAR_SUN,
    "READING_GLASSES": _EYEWEAR_FRAME,
    "SMARTGLASSES": _EYEWEAR_SUN,
    "CONTACT_LENS": _CONTACT_LENS,
    "COLORED_CONTACT_LENS": _COLOR_CONTACT_LENS,
    "WATCH": _WATCH,
    "SMARTWATCH": _SMARTWATCH,
    "ACCESSORIES": _ACCESSORIES,
    "OPTICAL_LENS": _MINIMAL,
    "WALL_CLOCK": _MINIMAL,
    "HEARING_AID": _MINIMAL,
    "SERVICES": (),  # a service has no filterable storefront attributes
}


def _canonical_category(category: Any) -> Optional[str]:
    """Resolve any category input (long-form / short SKU code / alias) to an IMS
    canonical key, reusing product_master.resolve_category so this stays in
    lock-step with the catalog taxonomy. Fail-soft: on any import/lookup error,
    fall back to a plain uppercase normalisation and match the registry directly."""
    try:
        from .product_master import resolve_category

        canonical = resolve_category(category)
        if canonical:
            return canonical
    except Exception:  # noqa: BLE001 -- never let a registry read break a push
        pass
    if not category:
        return None
    raw = str(category).strip().upper().replace("-", "_").replace(" ", "_")
    return raw if raw in _CATEGORY_TAG_MAP else None


def generate_attribute_tags(
    category: Any,
    attributes: Optional[Dict[str, Any]] = None,
    extras: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Derive the Shopify `<prefix>_<value>` filter tags for a catalog product
    from its category + attributes, matching what the BVI admin app generates.

    Args:
        category:   IMS category (long-form, short SKU code, or alias).
        attributes: the product's `attributes` dict (IMS snake_case keys).
        extras:     optional values that OVERRIDE `attributes` where non-blank --
                    used to inject a top-level field (e.g. product.brand -> the
                    brand_name tag) or variant-level values not in `attributes`.

    Returns a de-duped, deterministically ordered list of lower-case tag tokens.
    Fail-soft: an unknown category or unmapped attribute is skipped; any
    unexpected error yields [] instead of raising (a tag build must never take
    down the push)."""
    try:
        canonical = _canonical_category(category)
        if canonical is None:
            return []
        tag_map: Sequence[Tuple[str, str]] = _CATEGORY_TAG_MAP.get(canonical, _MINIMAL)

        merged: Dict[str, Any] = dict(attributes or {})
        if extras:
            for k, v in extras.items():
                if v is not None and str(v).strip() != "":
                    merged[k] = v

        out: List[str] = []
        seen: set = set()
        for attr_key, prefix in tag_map:
            if attr_key not in merged:
                continue
            raw = merged[attr_key]
            if raw is None or (isinstance(raw, str) and raw.strip() == ""):
                continue
            value = slugify_tag_value(raw)
            if not value:
                continue
            token = f"{prefix}_{value}"
            if token in seen:
                continue
            seen.add(token)
            out.append(token)
        return out
    except Exception:  # noqa: BLE001 -- pure payload helper, never raise
        return []


def merge_tag_lists(
    existing: Optional[Sequence[Any]], generated: Optional[Sequence[Any]]
) -> List[str]:
    """Union the product's existing (browse/manual) tags with the generated
    attribute tags for the Shopify push payload.

    Order is deterministic: existing tags first (in their given order), then any
    generated tags not already present. Every token is lower-cased to match the
    Shopify tag convention (tag matching is case-insensitive) and de-duped on
    that lower-cased form. Blank entries are dropped."""
    out: List[str] = []
    seen: set = set()

    def _add(seq: Optional[Sequence[Any]]) -> None:
        for t in seq or []:
            if t is None:
                continue
            token = str(t).strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)

    _add(existing)
    _add(generated)
    return out
