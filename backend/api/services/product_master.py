"""
IMS 2.0 - Unified Product Master (PM / N5)
==========================================

ONE service that unifies the two divergent product surfaces:

  * `products`          -- the billing/stock SPINE (ProductRepository). Single
                           source of truth. Read by POS, inventory, finance.
  * `catalog_products`  -- the PIM superset (Shopify/BVI lineage).
  * `catalog_variants`  -- the per-SKU variant identity tier.

WHAT THIS DELIVERS (packet PM / foundation N5):
  * A canonical category registry (long-form `FRAME` ... + short SKU prefix `FR`)
    that reconciles the two pre-existing, divergent category enums.
  * `build_sku` -- THE SKU rule for new products, readable and separated
    (CATEGORY-BRAND-MODEL-COLOUR-SIZE, owner D5); legacy SKUs are never
    re-minted; atomic-counter suffix only on collision.
  * `validate_attributes` -- server-side category-conditional required-field
    validation (a Contact Lens without expiry, a Hearing Aid without serial_no,
    a Frame without colour_code are rejected -- not just on the FE wizard).
  * `normalise_payload` -- GST/HSN derived server-side from gst_rates.py, the
    offer<=MRP invariant via pricing_caps, discount_category validation.
  * `create_product` -- the SPINE-FIRST + COMPENSATION triple-write:
        1. write the Mongo `products` spine FIRST + alone (single-doc, atomic),
        2. (gated, best-effort) mirror to catalog_products / catalog_variants
           and the external Postgres(BVI)/Shopify targets,
        3. record per-target sync status back on the spine doc,
        4. write the audit row.
    A failed mirror NEVER rolls back or corrupts the spine. There are NO
    cross-collection transactions (standalone Mongo -- CORRECTIONS P0-1).

SAFETY (CORRECTIONS, binding):
  * The mirror (catalog + external) is GATED behind an off-by-default flag
    (`pm.mirror_enabled` via E2 get_policy) AND the NEXUS DISPATCH_MODE gate for
    the EXTERNAL (Postgres/Shopify) writes. NO live external write can fire on a
    fresh deploy.
  * Config via E2 get_policy; audit via AuditRepository.create (NOT
    append_audit_entry); no emoji in this file (Windows cp1252).
  * Legacy SKUs (Shopify-style / older format) are accepted as-is and never
    re-minted or rejected.
"""

from __future__ import annotations

from api.utils.event_time import utc_now_iso

import os
import re
import uuid
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from .gst_rates import (
    gst_rate_for_category,
    hsn_for_category,
    resolve_gst_rate_strict,
)
from .gtin import (
    MANUFACTURER_BARCODE_ATTRIBUTES,
    classify_gtin,
    fold_barcode_spellings,
    gtin_spellings,
    manufacturer_barcode_key,
    normalise_candidate,
    sanitise_gtin,
)
from .pricing_caps import evaluate_offer_price, offer_terms_changed, offer_write_problem
from .catalog_ecom import dirty_marks
from .product_naming import (
    build_handle,
    build_product_name,
    build_seo_description,
    build_seo_title,
    needs_name,
    smart_title,
)

logger = logging.getLogger("ims.product_master")

# Permissive legacy-SKU pattern: letters, digits, and the two separators the
# Excel rule and Shopify both use. Deliberately NO length constraint -- the
# canonical Excel example (SGPRADAVPR19W1AB1O153 / FRBURBERRYB31421109/7155)
# already varies wildly, and legacy import must never be rejected.
# `.` is allowed because build_sku mints it in a size (52.5): every SKU the
# minter makes must pass this check if it is ever sent back (a clone, an import).
_SKU_PERMISSIVE = re.compile(r"^[A-Za-z0-9/._-]+$")

# Valid discount cap tiers. Mirrors pricing_caps.CATEGORY_DISCOUNT_CAPS and the
# schemas.py PRODUCT_SCHEMA.discount_category enum (SERVICE added in PM/N5).
VALID_DISCOUNT_CATEGORIES = frozenset(
    {"MASS", "PREMIUM", "LUXURY", "SERVICE", "NON_DISCOUNTABLE"}
)


class ProductMasterError(Exception):
    """Raised for a validation failure the router maps to HTTP 422 / 400.

    `status` carries the intended HTTP code; `field` (optional) names the
    offending attribute so the 422 body can point at it.
    """

    def __init__(
        self,
        message: str,
        status: int = 422,
        field: Optional[str] = None,
        code: Optional[str] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status = status
        self.field = field
        # Optional machine code (MISSING_FIELD, DUPLICATE_PRODUCT) + conflict
        # payload (Hub Phase 1 duplicate guard): a 409 carries `conflict =
        # {product_id, sku, identity_key}` of the existing row so the caller/FE
        # can link to it ("add stock / a variant").
        self.code: Optional[str] = code
        self.conflict: Optional[Dict[str, Any]] = None
        # EYE_SIZE_NEEDED (identity_conflict): the eye sizes already catalogued.
        self.sizes: Optional[List[str]] = None


# The code on a "required field is empty" error: the catalogue create/import
# doors answer only THAT with their historical "Missing required field: <f>".
MISSING_FIELD = "MISSING_FIELD"


# ===========================================================================
# Canonical category registry (reconciles the two divergent enums)
# ===========================================================================
# The `products.category` field stores the LONG-FORM canonical value (FRAME,
# SUNGLASS, ...). The short code (FR, SG, ...) is the SKU prefix + grid grouping
# key, stored on `products.sku_prefix`. This single map is the bridge -- the
# catalog.ProductCategory short-code enum is retained there only as the SKU
# prefix registry, not as the products.category value.


@dataclass(frozen=True)
class CategorySpec:
    canonical: str  # long-form products.category value (e.g. "FRAME")
    prefix: str  # short SKU prefix (e.g. "FR")
    display: str  # human label (e.g. "Frame")
    required: tuple  # required attribute keys for this category
    optional: tuple = ()  # optional attribute keys
    forced_discount_category: Optional[str] = (
        None  # e.g. HEARING_AID -> NON_DISCOUNTABLE
    )


# The SUNGLASS optional tail, hoisted so SMARTGLASSES can REUSE it instead of
# growing a second copy of the eyewear vocabulary (a smart glass IS a sunglass
# with electronics -- same shape/size/colour/material/lens questions). Order is
# the owner-locked Add-Product form order; SUNGLASS below is byte-identical to
# what it was before the hoist (its head ("subbrand","label","model_name") is
# spelled out at the call site because SMARTGLASSES keys identity on model_name
# as REQUIRED and carries model_no as the optional one instead).
_EYEWEAR_SUN_TAIL: tuple = (
    "lens_size",
    "bridge_width",
    "temple_length",
    "gender",
    "shape",
    "frame_type",
    "lens_colour",
    "tint",
    "polarization",
    "uv_protection",
    "frame_color",
    "temple_color",
    "lens_material",
    "frame_material",
    "temple_material",
    "usp_1",
    "usp_2",
    "country_of_origin",
    "warranty",
    "upc",
    "gtin",
)

# The electronics half of a smart glass. EVERY one of these is grounded in a
# spec bullet that appears on the LIVE Ray-Ban Meta listings on bettervision.in
# -- today that detail exists only as prose somebody typed by hand, so a new
# model cannot be catalogued and listed without re-writing HTML. Capturing them
# as fields is what lets services/smartglass_listing.py generate the bullets.
# All OPTIONAL: an older smart glass that has no camera simply omits the row.
_SMARTGLASS_TECH: tuple = (
    "generation",  # "Gen 2" -- named in the listing H2
    "camera_mp",  # 12  -> "12MP ... camera"
    "camera_type",  # "Ultra-wide"
    "video_resolution",  # "1080p"
    "audio_type",  # "Open-ear speakers"
    "microphone_count",  # 5  -> "5-microphone array"
    "voice_assistant",  # "Meta AI"
    "controls",  # "Capacitive touch controls on the temple"
    "battery_life_hours",  # 4
    "charging_case",  # Yes/No -> "plus a portable charging case"
    "connectivity",  # "Wi-Fi 6 and Bluetooth 5.2"
    "storage_gb",  # 32
    "prescription_ready",  # Yes/No
    "year_of_launch",
)

# TWO-REGISTRY RULE: a category field lives here AND in the frontend's
# CATEGORY_FIELDS (frontend/src/domain/catalog/productAdd/categoryFields.ts);
# add it to both, plus a label in _FIELD_LABELS below.
# tests/test_smartglass_listing.py pins the two lists for every category.
#
# canonical -> CategorySpec. `required` folds the Excel/CATEGORY_FIELDS rules.
# HEARING_AID adds serial_no as REQUIRED (the catalog CATEGORY_FIELDS had it
# optional) per the PM packet, and is forced NON_DISCOUNTABLE.
_CATEGORY_SPECS: Dict[str, CategorySpec] = {
    "FRAME": CategorySpec(
        "FRAME",
        "FR",
        "Frame",
        required=("brand_name", "model_no", "colour_code"),
        # Eyewear field set, owner-locked 2026-07-04 (catalog form rework):
        # shared with SUNGLASS, frame-specific split = blue_cut_lens. Tuple
        # order mirrors the Add-Product form order. UPC/GTIN are the
        # MANUFACTURER's barcodes (captured for reference); our own internal
        # barcode is minted separately at Goods-Receipt, not here.
        # REMOVED from the registry (legacy attribute values on existing docs
        # persist untouched -- attributes are stored unfiltered): size,
        # full_model_no, lens_usp, product_usp, usp, gender_label.
        optional=(
            "subbrand",
            "label",
            "model_name",
            "lens_size",
            "bridge_width",
            "temple_length",
            "gender",
            "shape",
            "frame_type",
            "blue_cut_lens",
            "frame_color",
            "temple_color",
            "frame_material",
            "temple_material",
            "usp_1",
            "usp_2",
            "country_of_origin",
            "warranty",
            "upc",
            "gtin",
        ),
    ),
    "SUNGLASS": CategorySpec(
        "SUNGLASS",
        "SG",
        "Sunglass",
        required=("brand_name", "model_no", "colour_code"),
        # Eyewear field set, owner-locked 2026-07-04 (catalog form rework):
        # shared with FRAME, sunglass-specific split = lens_colour + tint +
        # polarization + uv_protection + lens_material. Tuple order mirrors
        # the Add-Product form order. UPC/GTIN are the MANUFACTURER's
        # barcodes; our internal barcode is minted at GRN.
        # REMOVED from the registry (legacy attribute values persist):
        # full_model_no, lens_usp, product_usp, usp, gender_label.
        optional=("subbrand", "label", "model_name") + _EYEWEAR_SUN_TAIL,
    ),
    "OPTICAL_LENS": CategorySpec(
        "OPTICAL_LENS",
        "LS",
        "Optical Lens",
        required=("brand_name", "index", "coating"),
        optional=("subbrand", "lens_type", "material"),
    ),
    "READING_GLASSES": CategorySpec(
        "READING_GLASSES",
        "RG",
        "Reading Glasses",
        required=("brand_name", "model_no", "colour_code"),
        # lens_size / bridge_width / temple_length: the Add-Product form's RG
        # fields (categoryFields.ts). The eye size is part of the identity like
        # a frame's, so the PO line and the catalogue form key it alike.
        optional=("subbrand", "power", "lens_size", "bridge_width", "temple_length"),
    ),
    "CONTACT_LENS": CategorySpec(
        "CONTACT_LENS",
        "CL",
        "Contact Lens",
        # Owner-decided reconcile (step-9): a contact lens catalogue entry needs
        # BOTH power AND expiry_date -- power so the SKU/stock grid is unambiguous
        # and expiry_date so a medical-device shelf-life is always recorded.
        required=("brand_name", "model_name", "power", "expiry_date"),
        optional=("subbrand", "colour_name", "pack", "modality"),
    ),
    "COLORED_CONTACT_LENS": CategorySpec(
        "COLORED_CONTACT_LENS",
        # Own SKU prefix (was "CL", which collided with CONTACT_LENS) so the
        # two contact-lens categories stay distinct in SKUs and reports.
        "CCL",
        "Colour Contact Lens",
        # colour_name is the defining attribute of a colour lens -> required.
        required=("brand_name", "model_name", "power", "expiry_date", "colour_name"),
        optional=("subbrand", "pack", "modality"),
    ),
    "WATCH": CategorySpec(
        "WATCH",
        "WT",
        "Wrist Watch",
        required=("brand_name", "model_no", "colour_code"),
        optional=("subbrand", "dial_color", "strap_material"),
    ),
    "SMARTWATCH": CategorySpec(
        "SMARTWATCH",
        "SMTWT",
        "Smart Watch",
        required=("brand_name", "model_name", "colour_code"),
        optional=("subbrand",),
    ),
    "SMARTGLASSES": CategorySpec(
        "SMARTGLASSES",
        "SMTFR",
        "Smart Glasses",
        # required UNCHANGED (additive rework 2026-08-25): a smart glass that
        # was catalogueable yesterday is still catalogueable today.
        required=("brand_name", "model_name", "colour_code"),
        # The eyewear half is REUSED from SUNGLASS (same tail tuple, no second
        # copy) + the electronics half. `model_no` sits in the optional head
        # here because model_name is this category's REQUIRED identity key.
        optional=("subbrand", "label", "model_no")
        + _EYEWEAR_SUN_TAIL
        + _SMARTGLASS_TECH,
    ),
    "WALL_CLOCK": CategorySpec(
        "WALL_CLOCK",
        "CK",
        "Wall Clock",
        required=("brand_name", "model_no", "colour_code"),
        optional=("subbrand",),
    ),
    "ACCESSORIES": CategorySpec(
        "ACCESSORIES",
        "ACC",
        "Accessories",
        required=("brand_name", "model_name"),
        optional=("subbrand", "size", "pack"),
    ),
    "SERVICES": CategorySpec(
        "SERVICES",
        "SVC",
        "Services",
        required=("name",),
        optional=("description",),
        forced_discount_category="SERVICE",
    ),
    "HEARING_AID": CategorySpec(
        "HEARING_AID",
        "HA",
        "Hearing Aid",
        # Owner-decided reconcile (step-9): a hearing-aid CATALOGUE entry needs
        # only {brand_name, model_no}. serial_no is per-UNIT (recorded at
        # stock-in, not at catalogue), so it is NOT required here.
        required=("brand_name", "model_no"),
        optional=("subbrand", "serial_no", "machine_capacity", "machine_type"),
        forced_discount_category="NON_DISCOUNTABLE",
    ),
}

# Aliases that resolve to a canonical key (short codes + common alternates).
_CATEGORY_ALIASES: Dict[str, str] = {
    # short SKU-prefix codes (catalog.ProductCategory.value)
    "FR": "FRAME",
    "SG": "SUNGLASS",
    "LS": "OPTICAL_LENS",
    "LENS": "OPTICAL_LENS",
    "RG": "READING_GLASSES",
    "CL": "CONTACT_LENS",
    "WT": "WATCH",
    "CK": "WALL_CLOCK",
    "CLOCK": "WALL_CLOCK",
    "WRIST_WATCH": "WATCH",
    "HA": "HEARING_AID",
    "SMTSG": "SMARTGLASSES",
    "SMTFR": "SMARTGLASSES",
    "SMART_FRAME": "SMARTGLASSES",
    "SMART_SUNGLASS": "SMARTGLASSES",
    "SMTWT": "SMARTWATCH",
    "SMART_WATCH": "SMARTWATCH",
    "ACC": "ACCESSORIES",
    "SVC": "SERVICES",
    "SERVICE": "SERVICES",
    # long alternates
    "FRAMES": "FRAME",
    "SUNGLASSES": "SUNGLASS",
    "OPTICAL_LENSES": "OPTICAL_LENS",
    "CONTACT_LENSES": "CONTACT_LENS",
    # colour contact lenses (canonical = COLORED_CONTACT_LENS)
    "CCL": "COLORED_CONTACT_LENS",
    "COLOUR_CONTACT_LENS": "COLORED_CONTACT_LENS",
    "COLOUR_CONTACT_LENSES": "COLORED_CONTACT_LENS",
    "COLORED_CONTACT_LENSES": "COLORED_CONTACT_LENS",
    "COLOUR_CONTACTS": "COLORED_CONTACT_LENS",
    "WATCHES": "WATCH",
    "ACCESSORY": "ACCESSORIES",
    "RX_LENS": "OPTICAL_LENS",
    "RX_LENSES": "OPTICAL_LENS",
    "SPECTACLE_LENS": "OPTICAL_LENS",
}


def resolve_category(category: Any) -> Optional[str]:
    """Normalise any input to a canonical long-form category key, or None.

    Accepts long-form, short codes, and common aliases. Case/space/hyphen
    insensitive. Returns None for blank / unknown so the caller can 422.
    """
    if not category:
        return None
    raw = str(category).strip().upper().replace("-", "_").replace(" ", "_")
    if raw in _CATEGORY_SPECS:
        return raw
    return _CATEGORY_ALIASES.get(raw)


def category_spec(category: Any) -> Optional[CategorySpec]:
    """Return the CategorySpec for a category (any form) or None if unknown."""
    canonical = resolve_category(category)
    if canonical is None:
        return None
    return _CATEGORY_SPECS[canonical]


# (Two-registry rule: see the note above _CATEGORY_SPECS - a new field also
# goes into frontend/src/domain/catalog/productAdd/categoryFields.ts.)
# Human-friendly labels for the canonical attribute keys, so the GET
# /products/categories endpoint can hand the FE a complete, render-ready field
# spec (label + required flag) from this ONE registry -- the FE no longer has to
# maintain its own label table for the required-ness contract. A key with no
# entry here falls back to a title-cased version of the key.
_FIELD_LABELS: Dict[str, str] = {
    "brand_name": "Brand Name",
    "subbrand": "Sub Brand",
    "model_no": "Model No",
    "model_name": "Model Name",
    "name": "Name",
    "description": "Description",
    "colour_code": "Colour Code",
    "colour_name": "Colour Name",
    "size": "Size",
    "lens_size": "Lens Size (mm)",
    # Owner-locked label (2026-07-04): "Frame Front Material" (the key stays
    # frame_material -- data is untouched, only the display label changed).
    "frame_material": "Frame Front Material",
    "frame_type": "Frame Type",
    "polarization": "Polarization",
    "uv_protection": "UV Protection",
    "tint": "Tint",
    "index": "Index",
    "coating": "Coating",
    "lens_type": "Lens Type",
    "material": "Material",
    "power": "Power",
    "pack": "Pack Size",
    "modality": "Modality",
    "expiry_date": "Expiry Date",
    "dial_color": "Dial Colour",
    "strap_material": "Strap Material",
    "serial_no": "Serial No",
    "machine_capacity": "Machine Capacity",
    "machine_type": "Machine Type",
    # Rich eyewear field set (FRAME + SUNGLASS). frame_material/frame_type/
    # polarization/uv_protection/tint/lens_size are already labelled above.
    # full_model_no / lens_usp / product_usp / usp / gender_label left the
    # FRAME+SUNGLASS registry (2026-07-04 form rework) but keep their labels
    # here so LEGACY attribute values on existing docs still render nicely.
    # Smart-glass electronics (_SMARTGLASS_TECH). Labels are what a shop person
    # would say out loud, with the unit in the label so the number is unambiguous.
    "generation": "Generation",
    "camera_mp": "Camera (megapixels)",
    "camera_type": "Camera Type",
    "video_resolution": "Video Recording",
    "audio_type": "Speakers",
    "microphone_count": "Number of Microphones",
    "voice_assistant": "Voice Assistant",
    "controls": "Controls",
    "battery_life_hours": "Battery Life (hours)",
    "charging_case": "Charging Case Included",
    "connectivity": "Connectivity",
    "storage_gb": "On-board Storage (GB)",
    "prescription_ready": "Prescription Lenses Possible",
    "year_of_launch": "Year of Launch",
    "label": "Label",
    "full_model_no": "Full Model No",
    "shape": "Shape",
    "frame_color": "Frame Colour",
    "temple_color": "Temple Colour",
    "temple_material": "Temple Material",
    # Owner-locked label (2026-07-04): "Bridge Size" (key stays bridge_width).
    "bridge_width": "Bridge Size",
    "temple_length": "Temple Length (mm)",
    "lens_usp": "Lens USP",
    "product_usp": "Product USP",
    "usp_1": "Product USP 1",
    "usp_2": "Product USP 2",
    "usp": "USP",
    "gender": "Gender",
    "gender_label": "Gender Label",
    "country_of_origin": "Country of Origin",
    "warranty": "Warranty",
    "upc": "UPC (mfr)",
    "gtin": "GTIN (mfr)",
    "blue_cut_lens": "Blue-Cut Lens",
    "lens_colour": "Lens Colour",
    "lens_material": "Lens Material",
    # The money + server-derived sentinels compute_catalog_status reports as
    # done_gaps. They are not per-category attributes, but they surface in the
    # SAME "what is missing" lists (the purchase-invoice gate reads them), so
    # they belong in the one label registry rather than a second local map.
    "category": "Category",
    "mrp": "MRP",
    "offer_price": "Selling Price",
    "cost_price": "Cost Price",
    "hsn_code": "HSN Code",
    "gst_rate": "GST Rate",
    "MRP_BELOW_OFFER": "Selling price is above MRP",
}


def field_label(key: Any) -> str:
    """Human label for an attribute key (registry source for the FE forms)."""
    k = str(key or "").strip()
    if not k:
        return ""
    return _FIELD_LABELS.get(k, k.replace("_", " ").title())


def all_category_specs() -> List[Dict[str, Any]]:
    """All canonical category specs, for the GET /products/categories endpoint.

    THE single source of truth the three product-entry doors (Quick Add /
    Guided / Rapid Grid) read to know, per category, which attribute fields are
    REQUIRED vs optional. Each spec carries:
      * code          -- canonical long-form category (e.g. "FRAME"),
      * sku_prefix    -- the short SKU-prefix code the FE category picker keys on
                         (e.g. "FR"); the FE maps its picker codes via this,
      * name          -- human label,
      * required_fields / optional_fields -- bare attribute-key lists, AND
      * fields        -- render-ready [{name,label,required}] for every required
                         + optional attribute, so a door can drive required
                         markers + block-submit straight from this payload.
      * forced_discount_category -- the locked discount tier (HA/SERVICES) or
                         None when the operator must choose.
      * level (per field) / model_fields / has_families -- G1 model
                         families: 'model' fields are shared by every
                         colour/size (MODEL_LEVEL_FIELDS), the rest per variant.
    cost_price is deliberately NOT listed as a required field: it is GRN-deferred
    (a product is created without a cost and lands DRAFT; cost is filled at
    receiving). It blocks ACTIVE/sellable, never the create -- see
    compute_catalog_status.
    """
    out: List[Dict[str, Any]] = []
    for spec in _CATEGORY_SPECS.values():
        fields: List[Dict[str, Any]] = []
        for key in spec.required:
            fields.append(
                {"name": key, "label": field_label(key), "required": True, "level": field_level(key)}
            )
        for key in spec.optional:
            fields.append(
                {"name": key, "label": field_label(key), "required": False, "level": field_level(key)}
            )
        out.append(
            {
                "code": spec.canonical,
                "sku_prefix": spec.prefix,
                "name": spec.display,
                "required_fields": list(spec.required),
                "optional_fields": list(spec.optional),
                "fields": fields,
                "forced_discount_category": spec.forced_discount_category,
                # Model families (G1): whether same-model items are one
                # product's colours/sizes, and its model-level fields.
                "has_families": is_family_category(spec.canonical),
                "model_fields": [
                    k for k in spec.required + spec.optional if field_level(k) == "model"
                ],
            }
        )
    return out


def required_fields(category: Any) -> List[str]:
    spec = category_spec(category)
    return list(spec.required) if spec else []


def optional_fields(category: Any) -> List[str]:
    spec = category_spec(category)
    return list(spec.optional) if spec else []


def canonical_categories() -> List[str]:
    """The canonical long-form category keys, in registry order.

    This is THE single source of truth for the product-category taxonomy
    (unification step-8). Every other module that needs to know "what are the
    valid product categories" reads this list (or `resolve_category` to
    normalise an input to one of these) instead of hardcoding its own enum / key
    set. A copy is returned (not the live dict) so callers cannot mutate the
    registry.
    """
    return list(_CATEGORY_SPECS.keys())


def is_known_category(category: Any) -> bool:
    """True when `category` (any input form -- long, short code, or alias)
    resolves to a canonical registry category. The authoritative membership test
    other writers should use instead of maintaining their own key set."""
    return resolve_category(category) is not None


# ===========================================================================
# SKU rule -- THE one minter (owner ruling 2026-09-28, D5)
# ===========================================================================


def _sku_segment(value: Any, keep_separators: bool = False) -> str:
    """One SKU part, uppercase letters and digits. `-` separates the parts, so
    it never appears inside one: a brand or model drops every other character
    (RAY-BAN -> RAYBAN); a colour code or size (`keep_separators`) keeps its
    own `/` or `-` as `/` (901/58, 1109-71 -> 1109/71), so colour 901/58 is
    never the SKU of colour 901 in size 58, nor 1109-71 that of 110971."""
    s = str(value or "").upper()
    if not keep_separators:
        return re.sub(r"[^A-Z0-9]", "", s)
    s = re.sub(r"[^A-Z0-9/]", "", s.replace("-", "/"))
    return "/".join(p for p in s.split("/") if p)


def _plain_number(value: Any) -> Optional[str]:
    """A size or lens index written as a plain decimal, in its shortest form,
    digit for digit as typed: '1.50' and '1.5' are 1.5, '54.0' and a float
    54.0 are 54, 54.1234567 stays 54.1234567. None for anything else (a sign,
    an exponent, text): that is kept as typed. THE size rule the SKU
    (_size_segment) and the duplicate key (compute_identity_key) share, so
    the guard calls two spellings of one lens the same lens exactly when the
    SKU does. ASCII digits only: a Devanagari or fullwidth digit is not a
    plain number, so it never reaches a SKU that is_acceptable_sku refuses."""
    m = re.fullmatch(r"([0-9]*)(?:\.([0-9]*))?", str("" if value is None else value).strip())
    if not m or not (m.group(1) or m.group(2)):
        return None
    whole = m.group(1).lstrip("0") or "0"
    frac = (m.group(2) or "").rstrip("0")
    return whole + ("." + frac if frac else "")


def _size_segment(value: Any) -> str:
    """The size part. A number keeps its decimal point, so a 52.5 eye size is
    never the SKU of a 525 one, and is written in its shortest form
    (_plain_number). Anything else is an ordinary segment."""
    return _plain_number(value) or _sku_segment(value, keep_separators=True)


def build_sku(category: Any, attributes: Dict[str, Any], db=None) -> str:
    """Mint a NEW product's readable SKU: CATEGORY-BRAND-MODEL-COLOUR-SIZE,
    e.g. FR-CARRERA-CA8895-807-54 (owner ruling 2026-09-28, D5). Empty parts
    are skipped. The parts are identity_parts -- the same ones the duplicate
    key folds -- so an Optical Lens (no model, colour or size) reads its
    sub-brand, coating and index: LS-ESSILOR-CRIZAL-HC-1.56. Deterministic, so POST
    /products/sku-preview shows the form exactly what the create door will
    mint; a clash gets mint_unique_sku's counter suffix. Existing SKUs are
    never re-minted (only a create without a SKU calls this). `db` is unused
    (kept for the callers' signature)."""
    spec = category_spec(category)
    if spec is None:
        raise ProductMasterError(
            f"Unknown product category '{category}'.", status=422, field="category"
        )
    a = attributes or {}
    brand, model, colour, size = identity_parts(a, category)
    segs = [
        *map(_sku_segment, (spec.prefix, brand, model)),
        _sku_segment(colour, keep_separators=True),
        _size_segment(size) if size else "",
    ]
    return "-".join(seg for seg in segs if seg)


def _next_collision_suffix(prefix: str, db=None) -> int:
    """Atomic counter for a SKU collision suffix. Reuses the proven
    catalog._next_sku_counter ($inc on the `counters` collection, in-memory
    fallback when no DB)."""
    try:
        from ..routers.catalog import _next_sku_counter

        return _next_sku_counter(prefix, db=db)
    except Exception:  # noqa: BLE001 - never block a create
        # Local fallback: a uuid fragment guarantees uniqueness offline.
        return int(uuid.uuid4().int % 100000)


def _catalog_sku_taken(sku: Any, db=None) -> bool:
    """True when a catalog_products row already carries this top-level sku.

    WHY THE MINT MUST LOOK HERE TOO: catalog_products.sku_1 is UNIQUE+SPARSE
    and prod holds sku-carrying catalog rows with NO products spine
    (SGRAYMETARW4006601 / SB5050 etc.) -- invisible to product_repo.find_by_sku.
    build_sku is deterministic, so a create can mint exactly one of those
    strings; without this check the spine insert succeeds and the PIM upsert
    then raises DuplicateKeyError, i.e. a dangling pim_product_id behind a 201.

    FAIL-SOFT: no db / no collection / any read error -> False (behaves exactly
    as before this check existed).
    """
    key = str(sku or "").strip()
    if not key or db is None:
        return False
    try:
        if hasattr(db, "get_collection"):
            coll = db.get_collection("catalog_products")
        else:
            coll = db["catalog_products"]
        if coll is None:
            return False
        return coll.find_one({"sku": key}) is not None
    except Exception:  # noqa: BLE001 - the mint must never block a create
        return False


def catalog_sku_taken(sku: Any, db=None) -> bool:
    """PUBLIC wrapper over the predicate mint_unique_sku itself consults.

    Callers that dedupe a SKU *before* reaching the mint (the bulk-create door's
    cross-batch check) must ask the SAME question the mint will ask, or they let
    through exactly the collision they exist to reject: product_repo.find_by_sku
    only sees the `products` spine, while prod holds sku-carrying
    catalog_products rows with NO spine. Same fail-soft contract as the private
    helper -- no db / unreadable collection -> False, never raises."""
    return _catalog_sku_taken(sku, db=db)


def sku_taken(sku: Any, product_repo=None, db=None) -> bool:
    """True when a product already has `sku`: in the products spine (via
    product_repo.find_by_sku; a read error counts as taken) or as a
    catalog_products sku (_catalog_sku_taken). THE question mint_unique_sku
    asks of the SKU it builds, and the one POST /products/sku-preview asks,
    so the Review never shows a SKU the save then numbers (an edit moves a
    product's identity but never its SKU, so the identity it left can be
    added again while its SKU is taken)."""
    if product_repo is not None:
        try:
            if product_repo.find_by_sku(sku) is not None:
                return True
        except Exception:  # noqa: BLE001 - fail toward minting a suffixed SKU
            return True
    return _catalog_sku_taken(sku, db=db)


def mint_unique_sku(
    category: Any, attributes: Dict[str, Any], product_repo=None, db=None
) -> str:
    """Mint a SKU and resolve any collision by appending an atomic counter.

    The canonical SKU is tried first (deterministic per the Excel rule). If a
    product with that SKU already exists -- in the `products` spine (checked
    via product_repo.find_by_sku) OR as a catalog_products top-level sku
    (checked via db; covers the spineless sku-carrying catalog rows the repo
    cannot see, whose unique index would otherwise reject the PIM write later)
    -- a counter suffix is appended so the second product of the same
    brand/model/colour is still distinct and BOTH `sku` unique indexes hold.

    FAIL-SOFT plumbing: with neither product_repo nor db the deterministic base
    is returned untouched (no counter burn) -- exactly the pre-existing
    behaviour the bulk-row validator relies on.
    """
    base = build_sku(category, attributes, db=db)
    if product_repo is None and db is None:
        return base
    if not sku_taken(base, product_repo, db):
        return base
    spec = category_spec(category)
    prefix = spec.prefix if spec else "XX"
    # Loop a few times in the unlikely event the suffixed SKU also collides.
    for _ in range(10):
        suffix = _next_collision_suffix(prefix, db=db)
        candidate = f"{base}-{suffix}"
        if _catalog_sku_taken(candidate, db=db):
            continue
        if product_repo is None:
            return candidate
        try:
            if product_repo.find_by_sku(candidate) is None:
                return candidate
        except Exception:  # noqa: BLE001
            return candidate
    return f"{base}-{uuid.uuid4().hex[:8].upper()}"


def is_acceptable_sku(sku: Any) -> bool:
    """Format-PERMISSIVE legacy-SKU acceptance: allow letters/digits and the
    `/`, `-`, `_`, `.` separators with no length constraint. Legacy Shopify-style
    SKUs (FRBURBERRYB31421109/7155) and older formats must pass."""
    if sku is None:
        return False
    s = str(sku).strip()
    if not s:
        return False
    return bool(_SKU_PERMISSIVE.match(s))


# ===========================================================================
# Validation + normalisation
# ===========================================================================


def validate_attributes(category: Any, attributes: Dict[str, Any]) -> None:
    """Reject a payload missing a category-conditional required field.

    Raises ProductMasterError(status=422, field=<missing>) naming the first
    missing required field. A field is "missing" when absent, None, or an
    empty/whitespace string.
    """
    spec = category_spec(category)
    if spec is None:
        raise ProductMasterError(
            f"Unknown product category '{category}'.", status=422, field="category"
        )
    attrs = attributes or {}
    for fld in spec.required:
        val = attrs.get(fld)
        if val is None or (isinstance(val, str) and not val.strip()):
            raise ProductMasterError(
                f"Missing required field '{fld}' for category {spec.canonical}.",
                status=422,
                field=fld,
            )


# ============================================================================
# Typed-in-capitals entry, stored in the right case (owner request 2026-09-02)
# ============================================================================
# Staff type everything on the Add-Product screen in capitals -- no shift key,
# visually uniform -- and the server puts each value into its correct case.
#
# It cannot be one rule. A blanket title pass would store model number RB4350
# as "Rb4350" and wreck colour code 002/20, so codes stay in capitals and only
# real words are title-cased. Anything not named here is left EXACTLY as
# received: the field registry is rewritten at runtime from the server
# dictionary, so an unlisted key must degrade to today's behaviour, never to
# a guess.

# Identifiers. Upper-cased, punctuation preserved verbatim -- the SKU builder
# keeps "/" and "-" in the colour segment, so touching separators here would
# change the SKU minted for every new frame.
_CASE_CODE = frozenset({
    "model_no", "full_model_no", "colour_code", "color_code",
    "serial_no", "battery_size", "upc", "gtin", "hsn_code",
})

# Words naming a thing. Title-cased through the ONE title-caser in
# product_naming, which already protects digit-bearing tokens.
_CASE_NAME = frozenset({
    "model_name", "label", "subbrand", "brand_name",
    "colour_name", "frame_color", "temple_color", "lens_colour", "dial_colour",
    "belt_colour", "body_colour", "tint",
    "frame_material", "temple_material", "lens_material",
    "country_of_origin", "generation", "cl_series",
    # `warranty` and `size` are deliberately absent: they are number-bearing
    # phrases ("2 years", "54-18-145") where casing changes the stored value
    # and buys nothing a reader wanted.
    "audio_type", "camera_type", "connectivity", "voice_assistant",
    "add_on_1", "add_on_2", "add_on_3",
})

# `sku` is deliberately in NEITHER set: build_sku already upper-cases what it
# mints, so casing it here buys nothing and would stop a legacy lower-case
# imported SKU from matching its product.


def apply_field_casing(attributes: Any, *, only: Any = None) -> Dict[str, Any]:
    """Case the product attributes a submit actually carries.

    ``only`` is the set of keys THIS submit is changing. Both edit doors merge
    the stored attribute bag with the patch before normalising, so casing the
    merged dict would silently rewrite every value the owner had corrected by
    hand, on every save. Passing the patch keys keeps the rule to what the
    operator just typed. ``only=None`` (the create door) cases everything,
    which is correct there because everything is new.

    Values that are not strings, and keys in neither class, pass through
    untouched.
    """
    out: Dict[str, Any] = dict(attributes or {})
    for key, value in list(out.items()):
        if only is not None and key not in only:
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        # MIXED CASE IS INTENT. "Radar EV Path", "boAt", "ic! berlin" carry
        # information no rule can reconstruct once it is gone, so a value that
        # already contains both cases is left exactly as supplied. The rule
        # fires only on all-capitals (what this feature is for) or all-lower
        # (staff who never touch the shift key), where there is nothing to lose.
        if any(c.islower() for c in value) and any(c.isupper() for c in value):
            continue
        if key in _CASE_CODE:
            out[key] = " ".join(value.split()).upper()
        elif key in _CASE_NAME:
            text = " ".join(value.split())
            # An ISO country code is two or three letters and must not become
            # "Us" or "Cn". Any real country name is longer than three.
            if key == "country_of_origin" and text.isalpha() and len(text) <= 3:
                out[key] = text.upper()
            else:
                out[key] = smart_title(text)
    return out


def enforce_dictionary_values(
    category: Any, attributes: Dict[str, Any], *, db=None
) -> Dict[str, Any]:
    """Catalog Dictionary enforcement (owner rule: only values saved in
    Settings may be chosen in Catalog).

    - brand_name: validated against the ACTIVE Brand Master brands applicable
      to this category (Settings -> Brand Master). Only enforced when the
      master has at least one applicable brand -- an empty master fails OPEN
      so cataloguing is not bricked before the owner seeds brands.
    - every other attribute: validated against Settings -> Catalog Dictionary
      (`catalog_field_options`) WHEN a non-empty list is configured for that
      field; unconfigured fields stay free-form.
    - Matches are case-insensitive and the stored value is canonicalised to
      the configured casing, so 'ray-ban' saves as 'Ray-Ban'.

    Returns a (possibly canonicalised) copy of `attributes`. Fail-soft: no db
    or a config read error -> attributes returned unchanged. Raises
    ProductMasterError(422, field=<name>) on a value outside its list.
    """
    attrs = dict(attributes or {})
    if db is None:
        return attrs
    # Lazy import (module attribute access kept so tests can monkeypatch the
    # loaders on the catalog_dictionary module).
    from api.services import catalog_dictionary as _cd

    spec = category_spec(category)
    prefix = spec.prefix if spec is not None else None

    brand_val = attrs.get("brand_name")
    if isinstance(brand_val, str) and brand_val.strip():
        brands = _cd.load_brand_options(db, prefix)
        if brands:  # None (read failed) or [] (empty master) both fail open
            canonical_brand = _cd.match_canonical(brand_val, brands)
            if canonical_brand is None:
                raise ProductMasterError(
                    f"Brand '{brand_val.strip()}' is not in the Brand Master for "
                    "this category. Pick a saved brand, or add it in "
                    "Settings -> Brand Master.",
                    status=422,
                    field="brand_name",
                )
            attrs["brand_name"] = canonical_brand

        # Sub-brand: enforced ONLY when the (canonical) brand HAS subbrands in
        # the Brand Master -- a brand with none keeps subbrand free-form
        # (mirrors the lens 'series falls open per brand' rule).
        sub_val = attrs.get("subbrand")
        if isinstance(sub_val, str) and sub_val.strip():
            subs = _cd.load_subbrand_options(db, attrs.get("brand_name") or brand_val)
            if subs:  # None (read failed) and [] (no subbrands) fail open
                canonical_sub = _cd.match_canonical(sub_val, subs)
                if canonical_sub is None:
                    preview = ", ".join(subs[:6]) + ("..." if len(subs) > 6 else "")
                    raise ProductMasterError(
                        f"Sub-brand '{sub_val.strip()}' is not defined for this "
                        f"brand. Its sub-brands: {preview}. Manage them in "
                        "Settings -> Brand Master.",
                        status=422,
                        field="subbrand",
                    )
                attrs["subbrand"] = canonical_sub

    # Per-category effective lists: the category's own lists override the
    # All-categories ones, so same-named fields (lens_material, power, ...)
    # never bleed across categories.
    canonical_key = spec.canonical if spec is not None else category
    options = _cd.load_field_options(db, canonical_key)
    if options:
        for name, allowed in options.items():
            if name in _cd.BRAND_MANAGED_FIELDS:
                continue  # brand fields are governed by the Brand Master above
            val = attrs.get(name)
            if not isinstance(val, str) or not val.strip():
                continue  # absent/blank values are the required-gate's concern
            canonical_val = _cd.match_canonical(val, allowed)
            if canonical_val is None:
                preview = ", ".join(allowed[:6]) + ("..." if len(allowed) > 6 else "")
                raise ProductMasterError(
                    f"'{val.strip()}' is not an allowed value for {field_label(name)}. "
                    f"Allowed: {preview}. Manage the list in "
                    "Settings -> Catalog Dictionary.",
                    status=422,
                    field=name,
                )
            attrs[name] = canonical_val
    return attrs


def normalise_tags(tags: Any) -> List[str]:
    """Governed normalisation for product tags (step-12).

    Accepts a list of strings OR a comma-separated string (Shopify-shape).
    Each tag is trimmed, lower-cased, internal whitespace collapsed to single
    spaces; empties dropped; de-duplicated PRESERVING first-seen order so the
    same input always yields the same canonical `tags` array at every door
    (FORM/BULK/CATALOG/MASTER). Returns [] for None/empty/garbage -- never
    raises (additive; must not break the step-9 strict create gate).
    """
    if tags is None:
        return []
    raw: List[Any]
    if isinstance(tags, str):
        raw = tags.split(",")
    elif isinstance(tags, (list, tuple, set)):
        raw = list(tags)
    else:
        return []
    seen: Dict[str, None] = {}
    for item in raw:
        if item is None:
            continue
        token = " ".join(str(item).strip().lower().split())
        if token and token not in seen:
            seen[token] = None
    return list(seen.keys())


# ---------------------------------------------------------------------------
# THE TWIN'S TAG LIST -- one spelling, one writer (sync audit gap #4, owner
# 2026-09-06). `ecom.seo.tags` on the catalog_products twin is THE list IMS
# sends to Shopify (shopify_push.product_input unions the attribute-derived
# filter tags onto it and reads NO other spelling). Every door that accepts
# tags lands them here through set_twin_tags: the catalog review PUT (which
# used to write a top-level `tags` the push never read), the add-product
# form's "Shopify tags" box (which was declared and dropped), and the spine
# mirror (mirror_update_to_catalog_twin, the same normaliser by dot-path).
# twin_tags is the matching reader; the BVI import wrote a top-level `tags`,
# so a doc that has never been through a door is read from there.
# ---------------------------------------------------------------------------


def twin_tags(doc: Dict[str, Any]) -> List[str]:
    """The twin's tag list as stored: ecom.seo.tags when it is a list (an
    explicit [] is an empty list -- never the legacy copy), else the
    top-level `tags` the BVI import wrote. Pure; never raises."""
    seo = ((doc or {}).get("ecom") or {}).get("seo") or {}
    tags = seo.get("tags") if isinstance(seo, dict) else None
    if not isinstance(tags, list):
        tags = (doc or {}).get("tags")
    return [str(t) for t in tags if t is not None] if isinstance(tags, list) else []


def set_twin_tags(doc: Dict[str, Any], tags: Any) -> List[str]:
    """Land `tags` on the twin at ecom.seo.tags -- the ONLY writer of that
    field on a full document (normalise_tags rule, so every door stores the
    same list for the same input). Copy-on-write on the ecom/seo sub-dicts so
    a caller holding a shallow copy of the stored doc never mutates the store
    before its own save. Returns the stored list. Never touches
    ecom.locally_modified -- the caller's save decides whether to queue."""
    clean = normalise_tags(tags)
    ecom = dict(doc.get("ecom") or {}) if isinstance(doc.get("ecom"), dict) else {}
    seo = dict(ecom.get("seo") or {}) if isinstance(ecom.get("seo"), dict) else {}
    seo["tags"] = clean
    ecom["seo"] = seo
    doc["ecom"] = ecom
    return clean


_EYE_SIZE = re.compile(r"\s*(\d{2,3})(?!\d)")
_EYE_BRIDGE_TEMPLE = re.compile(r"\s*\d{2,3}\s*[-/xX]\s*\d{1,2}(\s*[-/xX]\s*\d{2,3})?\s*$")


def eye_size_of(value: Any) -> Optional[str]:
    """A stored size read as an EYE size, the number Add product's eye-size box
    takes: the leading 2-3 digit number -- 52, "52", "52.0" and "52-18-140" all
    read "52" -- or nothing for a value that is not one ("M", "Large")."""
    found = _EYE_SIZE.match(str(value)) if value not in (None, "") else None
    return str(int(found.group(1))) if found else None


def frame_size_of(value: Any) -> Any:
    """A frame's / sunglass's size as its identity reads it: a legacy
    eye-bridge-temple text ("52-18-140", "52/18") is its eye size, "52" -- the
    item typed with eye size 52 -- and any other value is kept as stored."""
    if value not in (None, "") and _EYE_BRIDGE_TEMPLE.match(str(value)):
        return eye_size_of(value)
    return value


_MODEL_KEYS = ("model_no", "model_name")


def _shown_attributes(attributes: Dict[str, Any], category: Any) -> Dict[str, Any]:
    """`attributes` less a model_no / model_name its category has no field
    for. main's create door folded a flat `model` into both, and main's form
    sent a lens's sub-brand (or 'STD') as that model: every Optical Lens on
    prod holds a model_no no form shows or clears, and a contact lens a
    model_no copy of its Model Name. Such a copy never outranks the field the
    form edits (and the door no longer writes one: normalise_door_payload).
    An unknown category keeps them all."""
    attrs = attributes or {}
    spec = category_spec(category)
    if spec is None:
        return attrs
    hidden = [k for k in _MODEL_KEYS if k not in spec.required + spec.optional]
    return {k: v for k, v in attrs.items() if k not in hidden}


def _derive_brand_model_color_size(
    attributes: Dict[str, Any], category: Any = None
) -> Dict[str, Optional[str]]:
    """Map category attribute keys onto the spine identity columns, read as
    the category's form shows them (_shown_attributes) -- THE one reading of
    an item's brand, model, colour and size (identity_parts, the "already
    exists" summary, the variant option and the identity migration all read
    it). A category with no model (Optical Lens) names its sub-brand in the
    model's place, so the model column and the display name say Crizal. An
    eye-size category's size is its own key as typed, else its legacy `size`
    read by frame_size_of."""
    attrs = _shown_attributes(attributes, category)
    size_key = _size_attribute_key(category) or "size"
    legacy = attrs.get("size")
    if size_key == "lens_size":
        legacy = frame_size_of(legacy)
    return {
        "brand": attrs.get("brand_name") or attrs.get("brand"),
        "model": attrs.get("model_no")
        or attrs.get("model_name")
        or attrs.get("model")
        or attrs.get("subbrand"),
        "color": attrs.get("colour_code")
        or attrs.get("color_code")
        or attrs.get("colour_name")
        or attrs.get("color"),
        # The category's OWN size key first: a frame's / sunglass's eye size
        # is its registry's lens_size, and `size` (which left their registry)
        # can still hold a legacy "52-18-140" beside it -- read first, it keyed
        # the frame apart from the same frame typed with eye size 52 (C2).
        # Each eye size is its own item (owner 09-28). A legacy row with only
        # "52-18-140" is the 52 item; a typed eye size is kept as typed (F13).
        "size": attrs.get(size_key) or legacy,
    }


_CONTACT_LENSES = ("CONTACT_LENS", "COLORED_CONTACT_LENS")
# A number as people write it: a sign (ASCII, the Unicode minus or a dash pasted
# from Word/PDF, or +), up to 3 digits, a '.' or ',' decimal part, an optional
# D/DS; PL or PLANO is 0. Digits are bounded, so an exponent or a 400-digit
# number never reaches the arithmetic.
_CL_POWER_RE = re.compile(r"([-+\u2212\u2013\u2014]?)\s*(\d{1,3})?(?:[.,](\d{1,3}))?\s*(?:DS?)?")
# The fit of a contact lens, after its power: (SKU label, name label, key).
_CL_FIT_FIELDS = (("BC", "BC", "base_curve"), ("DIA", "DIA", "diameter"), ("PK", "Pack of", "pack"))


def _cl_number(attributes: Dict[str, Any], key: str) -> Optional[Decimal]:
    """One contact-lens number as typed (_CL_POWER_RE), None when blank.
    Anything that is not a number is refused (422 naming the field): text kept
    as typed lost its sign in the SKU and the key, so -1.25D was the duplicate
    of +1.25D."""
    raw = str(attributes.get(key) if attributes.get(key) is not None else "").strip()
    if not raw:
        return None
    if raw.upper() in ("PL", "PLANO"):
        return Decimal(0)
    m = _CL_POWER_RE.fullmatch(raw.upper())
    if not (m and (m.group(2) or m.group(3))):
        example = "8.6" if key in ("base_curve", "diameter", "pack") else "-1.25"
        raise ProductMasterError(
            f"{key} is not a number: '{raw[:20]}' (write it like {example}).",
            status=422,
            field=key,
        )
    d = Decimal("%s.%s" % (m.group(2) or "0", m.group(3) or "0"))
    return d if m.group(1) in ("", "+") else -d


def _cl_parts(attributes: Dict[str, Any]) -> List[Tuple[str, str]]:
    """What tells one contact lens of a model from another, as (SKU part, name
    part) pairs -- owner 2026-09-28: each power is its own stock item, with
    power, base curve, diameter and pack in its name and SKU. SPH, C cylinder,
    X axis, A add, then BC, DIA, PK: -1.25/-0.75x180 BC 8.6 DIA 14.2 in 6s is
    M125/CM075/X180/BC86/DIA142/PK6 in the SKU and '-1.25 -0.75 x180 BC 8.6
    DIA 14.2 Pack of 6' in the name. A sign is a letter ('-' separates SKU
    parts): -1.25 is M125, +1.25 and 1.25 are P125, 0 is PL (plano); in
    hundredths, so -1.25 is never -12.50 (M1250). A zero (or blank) cylinder,
    add, base curve, diameter or pack is none, and so is an axis with no
    cylinder: an axis means nothing without one. Fit numbers are written in
    their shortest form (_plain_number), so 8.60 and 8.6 are one lens."""
    a = attributes or {}
    sph, cyl, axis, add = (_cl_number(a, k) for k in ("power", "cl_cyl", "cl_axis", "cl_add"))
    fit = [(s, n, _cl_number(a, k)) for s, n, k in _CL_FIT_FIELDS]

    def signed(label: str, d: Decimal) -> str:
        hundredths = int((abs(d) * 100).to_integral_value())
        return "%s%s%03d" % (label, "M" if d < 0 else "P", hundredths)

    out: List[Tuple[str, str]] = []
    if sph is not None:
        out.append(("PL", "Plano") if sph == 0 else (signed("", sph), format(sph, "+.2f")))
    if cyl:
        out.append((signed("C", cyl), format(cyl, "+.2f")))
        if axis is not None:
            out.append(("X%d" % int(abs(axis)), "x%d" % int(abs(axis))))
    if add:
        out.append((signed("A", add), "ADD " + format(add, "+.2f")))
    for sku_label, name_label, d in fit:
        if d:
            num = _plain_number(str(abs(d)))
            out.append((sku_label + num, "%s %s" % (name_label, num)))
    return out


def identity_parts(
    attributes: Dict[str, Any], category: Any = None
) -> Tuple[Any, Any, Any, Any]:
    """(brand, model, colour, size): what tells one product from another --
    THE parts build_sku mints from and the duplicate key (compute_identity_key)
    folds, so a lens the guard calls new also gets a SKU of its own and the
    Review preview is the SKU it saves. An Optical Lens has no colour or size:
    its coating and index stand in (Crizal 1.56 HC and Crizal 1.67 HC are two
    products; the same Crizal 1.56 HC twice is one). A frame's eye size
    (lens_size) is its size: 52 and 54 are two products (owner 2026-09-28,
    each eye size is its own variant). Each is read only for its own kind
    (`category`): the form keeps a typed field when its category is switched,
    and a Sunglass's leftover Lens Size 54 must never stand in for a lens's
    index, nor a lens's index or coating for a frame's. A lens with no sub-brand
    is the brand's own line: its coating takes the model's place, so Hoya HC
    1.56 saved twice is still one product and the SKU mints no filler. A
    lens's model_no/model_name (main's filler 'STD', or its sub-brand copied
    there) is no field of the lens form, so it is never read
    (_shown_attributes): an old row and the same lens entered today get one
    key, and a sub-brand edit re-keys either. A contact lens (`category`) is
    told apart by its power and fit in the size's place (_cl_parts): a second
    power, base curve, diameter or pack of a model is a new product, the same
    lens twice is one."""
    ids = _derive_brand_model_color_size(attributes, category)
    a = attributes or {}
    kind = resolve_category(category)
    lens = kind == "OPTICAL_LENS"
    model, colour = ids["model"], ids["color"] or (a.get("coating") if lens else None)
    if not model and not ids["color"]:
        model, colour = colour, None
    size = ids["size"] or a.get("index" if lens else "lens_size")
    if kind in _CONTACT_LENSES:
        size = "/".join(sku for sku, _ in _cl_parts(a)) or None
    return (ids["brand"], model, colour, size)


def identity_inputs(attributes: Dict[str, Any], category: Any) -> List[str]:
    """The attributes that decide identity_parts for these values: changing
    any other one (a lens colour, a USP, a Model Name under a Model No) cannot
    make it another product. Read off the rule itself -- each field the
    category has, or that was typed, is given another value and kept when the
    parts move (or stop reading, a number field) -- so no list of the rule's
    inputs is kept anywhere. The Add-product strip keeps its answer while
    only other fields change (useSimilarProducts)."""
    a = dict(attributes or {})
    spec = category_spec(category)
    fields = set(a) | set(spec.required + spec.optional if spec else ())

    def parts(attrs: Dict[str, Any]) -> Any:
        try:
            return identity_parts(attrs, category)
        except ProductMasterError:
            return None

    now = parts(a)
    return sorted(k for k in fields if parts({**a, k: "\x00"}) != now)


def normalise_identity_component(value: Any) -> str:
    """THE one normaliser every identity comparison must run through.

    This is the function that builds the stored `identity_key`
    (compute_identity_key below) AND what the live "similar products" lookup
    (find_similar_products / GET /products/similar) normalises its query
    inputs with -- council rule: matching must use the SAME normaliser as the
    spine's duplicate key, never a reimplementation.

    Two rules, both driven by how eyewear codes are really written:

    1. DELETE every separator rather than fold it to a space. Folding to a
       space left "RB4350" and "RB 4350" as different identities, so the same
       frame catalogued by two people became two products. Owner example:
       "Ray-Ban RB4350 002/20", "Ray-Ban 0RB 4350 002/20" and
       "Ray-Ban RB4350 002-20" are ONE product and were being stored as three.

    2. DROP A LEADING ZERO THAT SITS IN FRONT OF A LETTER. Luxottica prints its
       catalogue codes with a 0 prefix -- 0RB4350 is RB4350 -- and vendor
       sheets, invoices and the frame's own temple disagree about whether to
       include it. A leading zero in front of a DIGIT is deliberately kept, so
       "0123" and "123" stay different products; only the alphabetic-prefix
       case is folded, because that is the one that is provably the same frame.

    Verified against the live catalogue before landing: under this rule the
    existing 77 catalogue rows still resolve to 77 distinct identities, so no
    two products that are separate today are merged by it.
    """
    s = re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())
    return re.sub(r"^0+(?=[a-z])", "", s)


def compute_identity_key(
    brand: Any, model: Any, colour: Any = None, size: Any = None
) -> Optional[str]:
    """The brand+model+colour(+size) identity used by the Hub Phase 1 duplicate
    guard.

    Normalised (normalise_identity_component) so casing/spacing/PUNCTUATION
    variants of the same product collide: lowercased, and every run of
    [-/_. whitespace] folded to a single space (the same separators the SKU
    builder strips). Without this, "RB-2140" and "RB 2140" would be distinct
    identities yet mint the SAME SKU base -- the SKU collision-suffix would then
    create a real duplicate that the identity arm missed. Returns None unless
    BOTH brand and model are present -- an identity needs at least those two; a
    category without them (e.g. SERVICES) gets no identity_key and is not
    identity-deduped. Colour is folded in (empty when absent) so two colours of
    the same model are distinct products. SIZE is folded in ONLY when present
    (build_sku already varies on size) so the same frame in two sizes is two
    distinct products -- a sizeless product keeps the 3-part key
    (backward-compatible).
    """
    _norm = normalise_identity_component
    b, m = _norm(brand), _norm(model)
    if not b or not m:
        return None
    parts = [b, m, _norm(colour)]
    s = _norm(_plain_number(size) or size)
    if s:
        parts.append(s)
    return "|".join(parts)


def existing_product_summary(existing: Dict[str, Any]) -> Dict[str, Any]:
    """The display projection of an EXISTING spine row the FE dup surfaces
    render: the duplicate-rescue popup (409 conflict payload, Phase 1) and the
    live "similar products" strip (GET /products/similar, Phase 2) both read
    THIS shape, so the two surfaces can never drift in how they extract
    name/colour/size/pricing from a product doc.

    Every value is .get()-sourced so a sparse row (e.g. the race fallback
    {'sku': ...}) still builds a valid payload with None gaps -- never a crash.
    """
    existing = existing or {}
    attrs = existing.get("attributes")
    if not isinstance(attrs, dict):
        attrs = {}

    brand = existing.get("brand") or attrs.get("brand_name")
    model = (
        existing.get("model") or attrs.get("model_no") or attrs.get("model_name")
    )
    colour = (
        attrs.get("colour_code") or attrs.get("colour_name") or existing.get("color")
    )
    size = (
        _derive_brand_model_color_size(attrs, existing.get("category"))["size"]
        or existing.get("size")
    )
    # A contact lens or reading glass is one product per power (owner
    # 2026-09-28): the create door keeps it in attributes.power (the form also
    # sends a contact lens's as cl_power); a reader takes it from here, never
    # by parsing the name.
    power = attrs.get("power")
    if power in (None, ""):
        power = existing.get("cl_power")
    # Display name: the doc's auto-minted `name` wins (product_naming stamps it
    # at create time); legacy rows created before that fall back to "Brand
    # Model".
    name = existing.get("name") or (
        " ".join(str(p) for p in (brand, model) if p) or None
    )

    return {
        # -- original keys (other tests/callers rely on these; unchanged) --
        "product_id": existing.get("product_id"),
        "sku": existing.get("sku"),
        "identity_key": existing.get("identity_key"),
        "barcode": existing.get("barcode"),
        # -- additive display payload (duplicate-rescue popup) --
        "name": name,
        "category": existing.get("category"),
        "brand": brand,
        "model": model,
        "colour_code": colour,
        "size": size,
        "power": power,
        "mrp": existing.get("mrp"),
        "offer_price": existing.get("offer_price"),
        "is_active": existing.get("is_active"),
        "catalog_status": existing.get("catalog_status"),
        # Ordered before it was catalogued and not finished (is_ordered_draft):
        # the popup leads the cataloguer to FINISH this draft (its held stock
        # goes on the shelf when he does), not to add a second product or
        # read it as archived. A finished row still marked reads finished.
        "provisional": is_ordered_draft(existing),
        # A draft an admin discarded: typed again as the SAME kind of product
        # it comes back (revive_discarded_draft); the popup says so (R1-64).
        "discarded_draft": revivable_discarded_draft(existing),
        "image_url": main_photo(existing),
    }


def _duplicate_error(existing: Dict[str, Any]) -> "ProductMasterError":
    """Build the 409 duplicate-product error carrying the EXISTING row so the
    caller/FE can link to it ('add stock or a variant instead'). Hub Phase 1.

    Duplicate-rescue (variant assist Phase 1): the conflict payload is ENRICHED
    ADDITIVELY with the existing row's display fields (name, colour, size,
    pricing, active flag, first image) so the FE popup can show the product and
    offer 'add a new colour/size of this model' without a second round-trip.
    The original keys (product_id / sku / identity_key / barcode), the 409
    status and the DUPLICATE_PRODUCT code are UNCHANGED -- additions only.
    The payload shape lives in existing_product_summary (shared with the
    Phase 2 similar-products strip).
    """
    existing = existing or {}
    err = ProductMasterError(
        "A product with this identity already exists "
        f"(SKU {existing.get('sku')}). Add stock or a variant instead of a "
        "duplicate.",
        status=409,
        field="sku",
    )
    err.code = "DUPLICATE_PRODUCT"
    err.conflict = existing_product_summary(existing)
    return err


def _identity_attributes(doc: Dict[str, Any]) -> Dict[str, Any]:
    """A stored row's attributes as the identity reads them: the attributes
    first, and a flat brand/model/color/size column only for a slot its
    attributes leave empty (a legacy row). The flat columns are copies the
    create door derived from the attributes (_derive_brand_model_color_size),
    so a copy left behind by an edit (a lens's sub-brand, a colour name) never
    outranks the attribute it came from. A flat model fills the model field
    the category's form shows; a lens has none (its flat model is a copy of
    main's hidden model_no), so it fills nothing."""
    doc = doc or {}
    category = doc.get("category")
    attrs: Dict[str, Any] = dict(doc.get("attributes") or {})
    ids = _derive_brand_model_color_size(attrs, category)
    shown = _shown_attributes(dict.fromkeys(_MODEL_KEYS, True), category)
    for slot, key in (
        ("brand", "brand_name"),
        ("model", next(iter(shown), None)),
        ("color", "colour_code"),
        ("size", "size"),
    ):
        flat = doc.get(slot) or (doc.get("colour") if slot == "color" else None)
        if key and _is_blank(ids[slot]) and not _is_blank(flat):
            attrs[key] = flat
    return attrs


def identity_key_of(doc: Dict[str, Any]) -> Optional[str]:
    """THE duplicate key a stored product has, by the create rule: identity_parts
    over its attributes, a legacy row's flat columns filling only an empty
    slot (_identity_attributes). The edit door and the key rebuild
    (scripts/migrate_identity_key_tighten.py) both read a row through here."""
    return compute_identity_key(
        *identity_parts(_identity_attributes(doc), (doc or {}).get("category"))
    )


def minted_name(doc: Dict[str, Any]) -> str:
    """The name the create door mints for a product doc, its stored name aside.
    A contact lens is named by the parts its SKU and key read (owner
    2026-09-28: power, base curve, diameter and pack in the name too)."""
    doc = doc or {}
    detail = ""
    if resolve_category(doc.get("category")) in _CONTACT_LENSES:
        detail = " ".join(name for _, name in _cl_parts(doc.get("attributes") or {}))
    # The name reads the model the form shows, never a hidden copy (_shown_attributes).
    attrs = _shown_attributes(doc.get("attributes") or {}, doc.get("category"))
    return build_product_name({**doc, "name": None, "attributes": attrs}, detail=detail)


def name_was_minted(doc: Dict[str, Any]) -> bool:
    """The stored name is one a create door minted for this doc, never a typed
    one: today's (minted_name) or main's, made before a contact lens's power
    and fit joined the name, reading the attributes as stored. Every contact
    lens on prod carries main's."""
    doc = doc or {}
    name = doc.get("name")
    if not name:
        return False
    if name == build_product_name({**doc, "name": None}):
        return True
    try:
        return name == minted_name(doc)
    except ProductMasterError:
        return False


def rekey_on_edit(
    current: Dict[str, Any], patch: Dict[str, Any], product_repo=None
) -> None:
    """An edit that changes what tells a product apart (any identity_parts
    input: an eye size, a contact lens's power or fit, a colour...) re-keys it
    by the create rule, setting patch['identity_key'], and is refused by the
    create door's own "we already have this" rule (identity_conflict): 409
    DUPLICATE_PRODUCT when ANOTHER product already has that identity (a frame
    keyed before eye size joined the key included), 422 EYE_SIZE_NEEDED when
    it leaves a frame without the eye size its siblings are catalogued by. A
    name the create door minted says what the product is
    (a lens's power, a frame's colour), so it is re-minted with the key
    (patch['name']); a typed name stays. The SKU never moves: readable SKUs
    are for new products only (owner 2026-09-28). A stored value that is not a
    number (main's free-text power) blocks no edit that leaves it as it is:
    the Edit form re-sends the whole attributes bag with a price change, so
    the key and the name stay as stored. An edit that blanks the brand or
    model keeps the stored key (ponytail: no $unset door, a unique sparse
    index refuses two null keys). The flat brand/model/color/size columns
    are copies of the attributes (_derive_brand_model_color_size): an edit
    that moves the attribute a column comes from re-derives that column, so
    the minted name and the model column say Varilux after a Crizal ->
    Varilux sub-brand edit. A flat value the request carries otherwise wins (a
    brand edit), unless it only echoes a hidden copy (_shown_attributes): the
    Edit form loads a lens's stored model_no Crizal and sends it back as the
    model with every save, which must never move the column off Varilux."""
    if not any(k in patch for k in ("attributes", "brand", "model", "color", "size", "category")):
        return
    if "attributes" in patch:
        category = patch.get("category") or current.get("category")
        attrs = patch["attributes"] or {}
        was = _derive_brand_model_color_size(current.get("attributes") or {}, category)
        echo = _derive_brand_model_color_size(attrs)  # hidden copies read too
        for slot, val in _derive_brand_model_color_size(attrs, category).items():
            if not _is_blank(val) and (val != was[slot] or patch.get(slot, val) == echo[slot] != val):
                patch[slot] = val
    merged = {**current, **patch}
    try:
        old = identity_key_of(current)
    except ProductMasterError:
        old = None  # a stored value that is not a number: the edit's key decides
    try:
        new = identity_key_of(merged)
    except ProductMasterError:
        after, before = _identity_attributes(merged), _identity_attributes(current)
        # A value this edit typed is still the 422 naming it.
        identity_parts({k: v for k, v in after.items() if v != before.get(k)}, merged.get("category"))
        # ponytail: the bad value is the stored one, untouched, so the key
        # cannot be read; key and name stay as stored until it is fixed.
        return
    if new is None or new == old:
        return
    if product_repo is not None:
        brand, model, colour, size = identity_parts(
            _identity_attributes(merged), merged.get("category")
        )
        err = identity_conflict(
            {
                "category": resolve_category(merged.get("category")),
                "brand": brand,
                "model": model,
                "color": colour,
                "size": size,
                "identity_key": new,
            },
            product_repo,
            self_id=current.get("product_id"),
        )
        if err is not None:
            raise err
    patch["identity_key"] = new
    if "name" not in patch and name_was_minted(current):
        patch["name"] = minted_name(merged) or current["name"]


# Sibling cap for the live "similar products" strip (dup-detect Phase 2). The
# strip is a hint, not a browser -- 12 chips is plenty; model_colour_count still
# reports the TRUE distinct-colour total.
SIMILAR_SIBLINGS_CAP = 12


def model_family_query(category: Any, brand: Any, model: Any) -> Optional[Dict[str, Any]]:
    """The filter for every row of one model -- same category, any colour or
    size -- or None when the category or brand/model does not normalise.
    find_similar_products lists by it; the counter stock lookup counts by it.

    "brand|model|" -- the trailing | delimiter guarantees "rb21" can never
    prefix-match "rb213". The normaliser now strips every character outside
    [a-z0-9], so no regex metacharacter can reach the key; re.escape stays as
    belt-and-braces against that ever changing.
    """
    canonical = resolve_category(category)
    if canonical is None:
        return None
    b = normalise_identity_component(brand)
    m = normalise_identity_component(model)
    if not b or not m:
        return None
    return {
        "category": canonical,
        "identity_key": {"$regex": "^" + re.escape(f"{b}|{m}|")},
    }


def find_similar_products(
    products_collection,
    *,
    category: Any,
    attributes: Optional[Dict[str, Any]] = None,
    limit: int = SIMILAR_SIBLINGS_CAP,
) -> Dict[str, Any]:
    """Live as-you-type "similar products" lookup for the Add-Product form
    (dup-detect council ruling, Phase 2).

    COUNCIL DESIGN RULE: matching runs through the SAME normaliser that builds
    the spine's identity/duplicate key (normalise_identity_component /
    compute_identity_key) -- a value differing only by case, spacing or the
    folded punctuation (- / _ .) from a stored product MUST match, exactly as
    it would 409 at create time. Never reimplement the folding here or in the
    browser.

    `attributes` is the form's attributes as typed, keyed by the create
    door's own identity_parts -- whatever it reads (an eye size, a contact
    lens's power and fit, a colour name, a lens's sub-brand, coating and
    index) -- so the exact match is the row the save would 409 against.

    Matching strategy (cheapest possible): stored docs carry `identity_key`
    (unique+sparse indexed), which was BUILT by compute_identity_key -- so an
    ^-anchored, escaped prefix regex on "brand|model|" walks that index and is
    normaliser-parity-correct by construction. `category` rides along as a
    filter (spec: siblings are same-category). No new index is needed: the
    identity_key index serves the prefix scan.

      * exact_match  -- the row a create would 409 against, by THE create
                        door's rule (identity_conflict). NOT category-filtered
                        (the door's check isn't either -- parity over
                        prettiness).
      * eye_size_needed -- the eye sizes the door's 422 EYE_SIZE_NEEDED names
                        for an eye-size item typed without one, else [].
      * siblings     -- same category + brand + model, ANY colour/size, capped
                        at `limit`, exact match excluded (the warning line
                        already shows it).
      * model_colour_count -- distinct colour segments across ALL the model's
                        rows (not capped).

    FAIL-SOFT: any DB trouble returns the empty shape -- the strip simply does
    not render; it must never block or 5xx the Add-Product flow.
    """
    # ONE shape for every answer, the empty one included (the hint reads
    # eye_size_needed whatever the lookup found).
    empty: Dict[str, Any] = {
        "exact_match": None,
        "eye_size_needed": [],
        "siblings": [],
        "model_colour_count": 0,
    }
    try:
        if products_collection is None:
            return empty
        canonical = resolve_category(category)
        if canonical is None:
            return empty
        brand, model, colour, size = identity_parts(attributes or {}, canonical)
        sibling_query = model_family_query(canonical, brand, model)
        if sibling_query is None:
            return empty

        # "Exact" is THE create door's answer (identity_conflict), never a
        # rule of its own: a frame keyed before eye size joined the key, a
        # sizeless frame catalogued by eye size and a discarded draft get the
        # same answer here as on Save.
        from database.repositories.product_repository import ProductRepository

        answer = identity_conflict(
            {
                "category": canonical,
                "brand": brand,
                "model": model,
                "color": colour,
                "size": size,
                "identity_key": compute_identity_key(brand, model, colour, size),
            },
            ProductRepository(products_collection),
        )
        code = getattr(answer, "code", None)
        exact = answer.conflict if code == "DUPLICATE_PRODUCT" else None

        docs = list(
            products_collection.find(sibling_query)
            .sort("identity_key", 1)
            .limit(int(limit) + 1)
        )
        exact_ident = (exact or {}).get("identity_key")
        siblings = [
            existing_product_summary(d)
            for d in docs
            if not (exact_ident and d.get("identity_key") == exact_ident)
        ][: int(limit)]

        # Distinct colours across ALL of the model's rows (uncapped): the
        # colour is segment 3 of the identity_key, already normalised.
        try:
            keys = products_collection.distinct("identity_key", sibling_query)
            colours = {
                k.split("|")[2]
                for k in keys
                if isinstance(k, str) and len(k.split("|")) >= 3
            }
            model_colour_count = len(colours)
        except Exception:  # noqa: BLE001 - count is a nicety, never a blocker
            seen = [d.get("identity_key") for d in docs] + [exact_ident]
            model_colour_count = len(
                {
                    k.split("|")[2]
                    for k in seen
                    if isinstance(k, str) and len(k.split("|")) >= 3
                }
            )

        return {
            "exact_match": exact,
            # Typed without the eye size it is catalogued by: Save asks for it.
            "eye_size_needed": list(answer.sizes) if code == "EYE_SIZE_NEEDED" else [],
            "siblings": siblings,
            "model_colour_count": model_colour_count,
            # G1: the model this item will join (Add Product's banner).
            "family": _family_hint(products_collection, canonical, attributes or {}),
            # What decides this answer: the form keeps it while only other
            # fields change, so the strip never reflows the grid needlessly.
            "identity_fields": identity_inputs(attributes or {}, canonical),
        }
    except Exception as exc:  # noqa: BLE001 - fail-soft: hint only, never a 5xx
        logger.warning("[PM] find_similar_products failed: %s", exc)
        return empty


def _family_hint(products_collection, category: str, attributes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The model a new item typed with these attributes joins on Save (the
    create door's find_family_lead): its lead, item count and colour/size
    chips; None for no model / a category without colours (or an unread
    catalogue: the hint never blocks the strip)."""
    try:
        return _family_hint_or_raise(products_collection, category, attributes)
    except Exception:  # noqa: BLE001
        logger.warning("[PM] family hint failed", exc_info=True)
        return None


def _family_hint_or_raise(products_collection, category: str, attributes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    probe = {"category": category, "attributes": attributes}
    lead = find_family_lead(products_collection, probe)
    if lead is None:
        return None
    key = family_key(probe)
    rows = [
        d
        for d in products_collection.find({"$or": [{"product_id": lead["product_id"]}, {"variant_of": lead["product_id"]}]})
        if not _is_archived(d)
    ]
    ids = {d.get("product_id") for d in rows}
    lead_attrs = lead.get("attributes") or {}
    q = _brand_rows_query(category, lead_attrs.get("brand_name") or lead.get("brand"))
    for d in products_collection.find(q) if q else ():
        if d.get("product_id") not in ids and not d.get("variant_of") and not d.get("family_opt_out") \
                and not _is_archived(d) and family_key(d) == key:
            rows.append(d)
    options, _ = family_option_values(rows)
    return {
        "lead_id": lead.get("product_id"),
        "lead_sku": lead.get("sku"),
        "label": " ".join(
            str(v) for v in (lead_attrs.get("brand_name") or lead.get("brand"), lead.get("model")) if v
        ),
        "count": len(rows),
        "chips": [
            " ".join(x for x in (o["option_color"], o["option_size"]) if x)
            for o in (options.get(d.get("sku")) for d in sorted(rows, key=lead_order))
            if o
        ][:24],
    }


def _drop_boxless_junk(attrs: Dict[str, Any], category: Any) -> Dict[str, Any]:
    """`attrs` (mutated, returned) without a junk manufacturer barcode the
    category's form has NO box for: dropped and logged, never a 422. gtin
    always has one (Manage Barcode edits it on any product); upc only where the
    form shows 'UPC (mfr)' (FRAME, SUNGLASS, SMARTGLASSES). A WATCH holding
    upc 'NA' 422'd every Quick Add save and every Approve with nothing on
    screen to fix -- the same treatment the fold gives a junk 'GTIN' key."""
    spec = category_spec(category)
    fields = (spec.required + spec.optional) if spec else ()
    for key in MANUFACTURER_BARCODE_ATTRIBUTES:
        if key == "gtin" or key in fields or key not in attrs:
            continue
        raw = attrs[key]
        if sanitise_gtin(raw) or not normalise_candidate(raw):
            continue
        logger.warning(
            "[PM] dropping invalid %s (no %s box on %s): reason=%s value=%.60r",
            key,
            key.upper(),
            category,
            classify_gtin(raw),
            raw,
        )
        del attrs[key]
    return attrs


def _guard_gtin_attribute(
    attributes: Dict[str, Any], *, strict: bool, category: Any
) -> Dict[str, Any]:
    """Validate `attributes['gtin']` -- the PUBLIC barcode -- at the create door.

    `gtin` is pushed to Shopify as ProductVariant.barcode and republished into
    the Google/Meta Shopping feeds, so a wrong value is customer-visible: the
    feeds either reject the item or match it to another manufacturer's product.
    Nothing validated this field before, so the door persisted whatever it was
    handed. The 2026-07-29 prod audit found 353 of 2,815 gtin-bearing variants
    holding junk: supplier item codes ("2511661"), two EANs pasted into one
    cell ("8056597720373 8056597720380"), model numbers ("TW003HG14"), and one
    row carrying the comma-joined Shopify browse-TAG string.

    Both live doors feed straight into here, which is why the check belongs at
    this chokepoint rather than in either of them:
      - the Add-Product form's free-text "GTIN (mfr)" box;
      - legacy Catalog Autopilot docs (feature removed 2026-08-30), which mapped
        row labelled gtin/ean/"ean code"/barcode into this attribute verbatim
        manufacturer spec-table keys onto category fields.

    STRICT (the interactive form): raise 422 so the cataloguer sees the bad
    value and fixes or clears it -- silently discarding what someone just typed
    would be worse. DRAFT/IMPORT (bulk + clone doors): drop the value and log
    it, so one bad cell never blocks a 2,000-row import while still never
    persisting garbage. Empty stays empty in both modes. A valid GTIN is stored
    SANITISED, digits only ('8 056597 720373', '805-6597-72037-3' ->
    '8056597720373'), so one GTIN is one value and a scan finds it.

    The `upc` attribute ('UPC (mfr)') is the same kind of code -- a UPC is a
    GTIN-12 -- and reaches Shopify as the ims.upc metafield and the
    description's 'UPC Code' row, so it gets the same rule.

    Keys match in any letter case ('GTIN', 'Upc' publish as ims.gtin / ims.upc
    too) and fold onto the one key (gtin.fold_barcode_spellings, THE fold): the
    exact key wins, and a junk code under another spelling is DROPPED and
    logged in BOTH modes, never a 422. No screen shows a 'GTIN' key (the GTIN
    box and Manage Barcode read 'gtin'), yet Quick Add sends every stored key
    back, so a 422 on an old stored 'GTIN' left the product unsaveable with
    nothing on screen to fix. For the same reason a junk upc in a `category`
    whose form has no UPC box is dropped, never refused (_drop_boxless_junk).
    """
    attrs = _drop_boxless_junk(fold_barcode_spellings(attributes), category)
    for key in MANUFACTURER_BARCODE_ATTRIBUTES:
        if key not in attrs:
            continue
        raw = attrs[key]
        clean = sanitise_gtin(raw)
        if clean or not normalise_candidate(raw):
            attrs[key] = clean or raw
            continue
        reason = classify_gtin(raw)
        if strict:
            raise ProductMasterError(
                f"'{str(raw)[:40]}' is not a valid {key.upper()} ({reason}). A "
                "GTIN/UPC is 8, 12, 13 or 14 digits with a valid check digit. "
                "Leave it blank if the manufacturer barcode is unknown.",
                status=422,
                field=key,
            )
        logger.warning(
            "[PM] dropping invalid %s: reason=%s value=%.60r", key, reason, raw
        )
        del attrs[key]
    return attrs


def twin_barcode_fields(gtin: Any) -> Dict[str, Any]:
    """The catalog-twin (or catalog_variants row) barcode fields for the
    product's gtin attribute: the top-level `gtin` and, when the GTIN is
    REMOVED, the legacy `barcode` too. A sibling row's push sends its `gtin`
    (product_input.variant_barcode), and the one-holder rule reads both on a
    twin (find_twin_by_barcode), so leaving either would send, or hold, the
    removed code. ONE rule for every door that moves the gtin attribute."""
    if gtin:
        return {"gtin": gtin}
    return {"gtin": None, "barcode": None}


def drop_legacy_spine_barcode(product_repo, product_id: Any) -> None:
    """The gtin attribute is a product's ONE manufacturer-barcode home, so a
    door that writes it drops the legacy products.barcode (main's old Manage
    Barcode wrote there). Left behind, a removed code stayed on the product:
    assert_gtin_free still found it and no screen could clear it. $unset, never
    "" or None: products.barcode carries a unique sparse index. The gtin key
    in another spelling ('GTIN') goes too: the gtin just written wins
    (gtin.fold_barcode_spellings), and the catalogue editor writes the spine's
    attributes.gtin alone, so nothing else would remove it."""
    coll = getattr(product_repo, "collection", None)
    if coll is None or not product_id:
        return
    # No projection: the no-Mongo MockCollection.find_one takes a filter only.
    attrs = (coll.find_one({"product_id": product_id}) or {}).get("attributes")
    unset = {"barcode": ""}
    if isinstance(attrs, dict):
        unset.update(
            {
                f"attributes.{k}": ""
                for k in attrs
                if k != "gtin" and manufacturer_barcode_key(k) == "gtin"
            }
        )
    coll.update_one({"product_id": product_id}, {"$unset": unset})


def spine_holds_gtin(spine: Optional[Dict[str, Any]], gtin: Any) -> bool:
    """True when the spine's attributes.gtin -- the key write_spine_gtin writes
    and the till reads -- is `gtin` in any of its spellings: the till,
    find_by_barcode and the one-holder check read a UPC-A and its 13-digit
    form as ONE code (gtin_spellings), so this does too. A blank `gtin` (a
    Remove) is held only by a blank."""
    attrs = (spine or {}).get("attributes")
    return isinstance(attrs, dict) and attrs.get("gtin") in (
        gtin_spellings(gtin) or [gtin]
    )


def write_spine_gtin(product_repo, product_id: Any, gtin: str, *, write_token=None) -> None:
    """Write the spine's ONE manufacturer barcode (attributes.gtin), then drop
    its legacy products.barcode -- ONLY once the gtin write is confirmed, or
    the spine would lose both codes. A spine whose attributes is not a dict
    (null, a list) cannot take a $set on attributes.gtin -- Mongo refuses it
    and BaseRepository.update answers False -- so it gets the dict {gtin} in
    the same write. ProductMasterError 503 when the write is not confirmed:
    the legacy barcode then stays where it was. update() answers
    modified_count > 0, so a write that changed nothing (the spine already held
    this gtin and its updated_at landed in the same millisecond as the write
    before it) answers False too: the stored code is the confirmation."""
    current = product_repo.find_by_id(product_id) or {}
    if isinstance(current.get("attributes"), dict):
        patch: Dict[str, Any] = {"attributes.gtin": gtin}
    else:
        patch = {"attributes": {"gtin": gtin}}
    if write_token is not None:
        patch["_gtin_write_token"] = write_token
    if not product_repo.update(product_id, patch) and not spine_holds_gtin(
        product_repo.find_by_id(product_id), gtin
    ):
        raise ProductMasterError(
            "The billing product's barcode could not be updated, so nothing "
            "was saved. Please save again.",
            status=503,
            field="gtin",
        )
    drop_legacy_spine_barcode(product_repo, product_id)


def undo_catalogue_barcode_write(
    collection, key: dict, before: dict, token: str, *, spine: bool,
    winning_attributes=None, previous_attributes=None,
) -> None:
    """Compensate only the barcode fields this refused edit still owns.

    A newer barcode writer has a different token and is never overwritten.
    If the catalogue CAS winner changed its GTIN, restore that winner's code
    rather than a snapshot taken before its edit. Other attributes, prices
    and the winning catalogue document are untouched.
    """
    old_attrs = before.get("attributes")
    winning = fold_barcode_spellings(winning_attributes or {})
    previous = fold_barcode_spellings(previous_attributes or {})
    winner_changed_code = ("gtin" in winning) and (
        winning.get("gtin") != previous.get("gtin") or "gtin" not in previous
    )
    fields = {"barcode": before.get("barcode")} if "barcode" in before else {}
    unset = {"barcode": ""} if "barcode" not in before else {}
    if spine:
        if winner_changed_code:
            fields["attributes.gtin"] = winning["gtin"]
            fields.pop("barcode", None)
            unset["barcode"] = ""
        elif isinstance(old_attrs, dict):
            if "gtin" in old_attrs:
                fields["attributes.gtin"] = old_attrs["gtin"]
            else:
                unset["attributes.gtin"] = ""
            for name, value in old_attrs.items():
                if name != "gtin" and manufacturer_barcode_key(name) == "gtin":
                    fields[f"attributes.{name}"] = value
        else:
            # This request replaced a missing/null legacy bag with {gtin: ...}.
            # Remove only its code: a later dotted-field update may have added
            # an unrelated attribute without becoming a barcode writer.
            unset["attributes.gtin"] = ""
    elif winner_changed_code:
        fields.update(twin_barcode_fields(winning["gtin"]))
        unset.pop("barcode", None)
    elif "gtin" in before:
        fields["gtin"] = before["gtin"]
    else:
        unset["gtin"] = ""
    if "_gtin_write_token" in before:
        fields["_gtin_write_token"] = before["_gtin_write_token"]
    else:
        unset["_gtin_write_token"] = ""
    patch = {}
    if fields:
        patch["$set"] = fields
    if unset:
        patch["$unset"] = unset
    collection.update_one({**key, "_gtin_write_token": token}, patch)


def assert_gtin_free(code: Any, product_repo, this_product_id: Optional[str]) -> None:
    """409 when ANOTHER product already holds this manufacturer barcode.

    THE uniqueness rule for both manufacturer-barcode fields (products.barcode
    and the gtin attribute), at every door that sets one: create, the spine
    edit and the catalogue review editor. ProductRepository.find_by_barcode
    reads both fields in every spelling of the one GTIN. A GTIN names ONE
    maker's item: two products holding it would go to Shopify/Google as the
    same thing. `this_product_id` None (a create) clashes with any holder.
    Catalogue twins count too (find_twin_by_barcode): the bulk import writes
    only a twin, and the push sends a twin's gtin."""
    if not normalise_candidate(code) or not hasattr(product_repo, "find_by_barcode"):
        return
    clash = product_repo.find_by_barcode(code, exclude_product_id=this_product_id)
    if clash is None and hasattr(product_repo, "find_twin_by_barcode"):
        clash = product_repo.find_twin_by_barcode(
            code, exclude_product_id=this_product_id
        )
    if clash is not None:
        holder = clash.get("sku") or clash.get("product_id") or clash.get("id")
        raise ProductMasterError(
            f"Barcode '{normalise_candidate(code)[:40]}' is already assigned to "
            f"another product ({holder}). "
            "Barcodes must be unique.",
            status=409,
            field="gtin",
        )


def merge_attributes_edit(
    stored: Optional[Dict[str, Any]],
    patch: Dict[str, Any],
    product_repo,
    this_product_id: Optional[str],
    category: Any,
) -> Tuple[Dict[str, Any], bool]:
    """(the attributes a partial edit stores, whether that write sets the gtin).
    THE merge for both attribute edit doors (the spine PUT, the catalogue review
    editor), on a `patch` already through _guard_gtin_attribute. Another stored
    spelling of a barcode key folds onto the one key (fold_barcode_spellings),
    so a stored 'GTIN' becomes the gtin even when the edit never sent one. The
    gtin is WRITTEN when the edit sent it or the fold made or changed it, and a
    written gtin passes the one-holder check: a folded 'GTIN' another product
    holds 409s exactly as a typed one does. The caller then runs the barcode
    projections (twin gtin, spine attributes.gtin, legacy barcode) on it. A
    stored junk upc the `category` has no box for leaves storage here
    (_drop_boxless_junk): the guard dropping it from the patch alone would let
    the stored copy back in."""
    stored = stored or {}
    merged = _drop_boxless_junk(fold_barcode_spellings({**stored, **patch}), category)
    written = "gtin" in patch or (merged.get("gtin") or "") != (
        stored.get("gtin") or ""
    )
    if written:
        assert_gtin_free(merged.get("gtin"), product_repo, this_product_id)
    return merged, written


def normalise_payload(
    *,
    category: Any,
    attributes: Dict[str, Any],
    mrp: float,
    offer_price: float,
    sku: Optional[str] = None,
    cost_price: Optional[float] = None,
    discount_category: Optional[str] = None,
    hsn_code: Optional[str] = None,
    gst_rate: Optional[float] = None,
    country_of_origin: Optional[str] = None,
    warranty_months: Optional[int] = None,
    weight: Optional[float] = None,
    tags: Any = None,
    created_by: Optional[str] = None,
    created_by_name: Optional[str] = None,
    as_draft: bool = False,
    force_draft: bool = False,
    provisional: bool = False,
    extra_fields: Optional[Dict[str, Any]] = None,
    product_repo=None,
    db=None,
) -> Dict[str, Any]:
    """Build the persisted `products` spine doc. GST/HSN derived server-side,
    offer<=MRP enforced, discount_category validated, SKU minted if absent.

    `extra_fields` are ADDITIVE door-specific top-level columns (e.g. the FORM
    door's CL/spectacle power identity that POS power-grid reads top-level, or a
    `variant`). They are merged onto the spine WITHOUT overriding any canonical
    key, and only for non-None values -- so a door's persisted shape is preserved
    while the canonical validation + GST/HSN/discount derivation stays unified.

    `provisional` (owner ruling 13, the buy-first-catalogue-later PO line): the
    row is created so a purchase order can reference a REAL product_id for an
    item nobody has catalogued yet. It is born `is_active=False` and, crucially,
    with NO offer_price -- the selling price is FORCED to None here rather than
    merely omitted by the caller, so compute_catalog_status below can only ever
    stamp DRAFT with `offer_price` among the gaps. That is what makes it
    structurally impossible for this door to emit a sellable row: it is an
    invariant of the door, not a convention its callers are trusted to keep.

    Raises ProductMasterError on any invariant breach.
    """
    if provisional:
        offer_price = None
    canonical = resolve_category(category)
    if canonical is None:
        raise ProductMasterError(
            f"Unknown product category '{category}'.", status=422, field="category"
        )
    spec = _CATEGORY_SPECS[canonical]

    # --- Always-enforced hard invariants (apply to STRICT and as_draft alike) ---
    # offer <= MRP is a BLOCKER the done-rule treats as MRP_BELOW_OFFER: even a
    # draft may never persist an offer above MRP (the pricing invariant), so it
    # raises here in BOTH modes (MRP>=offer regression, both doors).
    verdict = evaluate_offer_price(mrp, offer_price)
    if verdict.get("reason") == "MRP_BELOW_OFFER":
        raise ProductMasterError(
            "Offer price cannot exceed MRP", status=400, field="offer_price"
        )

    if as_draft:
        # DRAFT FLOOR: a draft must still carry a resolvable category + brand +
        # model (assert_draft_floor 422s below this floor). Completeness is NOT
        # required -- the row persists as catalog_status=DRAFT + done_gaps.
        assert_draft_floor({"category": canonical, "attributes": attributes or {}})
    else:
        # STRICT (default): the per-category required-attribute gate + the pricing
        # invariant (mrp/offer > 0). This is BEHAVIOUR-PRESERVING -- it is the
        # pre-Phase-0 step-9 gate, unchanged. cost_price is deliberately NOT a
        # create-blocker: the owner-locked rule gates PURCHASE, not creation (a
        # product may be created without a known cost and land catalog_status=
        # DRAFT, then be completed later -- e.g. cost auto-filled from the PO at
        # GRN receiving). compute_catalog_status below stamps DRAFT + names
        # cost_price in done_gaps; it is never silently treated as complete.
        if mrp is None or offer_price is None:
            raise ProductMasterError("mrp and offer_price are required.", status=422)
        missing = missing_required_fields(canonical, attributes or {})
        if not _positive_number(mrp):
            missing.append("mrp")
        if not _positive_number(offer_price):
            missing.append("offer_price")
        if missing:
            # De-dupe preserving order; name them all in the message + point
            # `field` at the first one for the FE highlight.
            seen: Dict[str, None] = {}
            for g in missing:
                if g not in seen:
                    seen[g] = None
            names = list(seen.keys())
            raise ProductMasterError(
                "Cannot save product -- missing required: " + ", ".join(names),
                status=422,
                field=names[0],
                code=MISSING_FIELD,
            )

    # Catalog Dictionary: when the owner has configured allowed values for a
    # field (Settings -> Catalog Dictionary / Brand Master), a present value
    # must match one of them; the match canonicalises casing. Fail-soft when
    # db is absent (unit tests / callers without a connection).
    # Typed-in-capitals entry -> stored case. BEFORE the dictionary pass, never
    # after: enforce_dictionary_values canonicalises brand/enum values to the
    # owner's Settings spelling, and running the caser afterwards would turn
    # its "boAt" back into "Boat". Case first, dictionary second, always.
    attributes = apply_field_casing(attributes)
    attributes = enforce_dictionary_values(canonical, attributes or {}, db=db)

    # A bad public GTIN is worse than none -- reject it here, before it can be
    # persisted and pushed to Shopify / the Shopping feeds. See the helper.
    attributes = _guard_gtin_attribute(
        attributes, strict=not (as_draft or force_draft), category=canonical
    )

    # discount_category: forced for HA/SERVICES, else an explicit value wins
    # (validated), else DERIVED from the brand's Brand Master tier (owner rule:
    # the tier is set brand-wise + category-wise in Settings, never re-picked
    # per product). Underivable (brand not in master / no db) -> None, which
    # compute_catalog_status surfaces as a DRAFT gap -- visible, never a
    # silent MASS default.
    if spec.forced_discount_category:
        dc = spec.forced_discount_category
    elif discount_category is not None:
        dc = str(discount_category).strip().upper()
        if dc not in VALID_DISCOUNT_CATEGORIES:
            raise ProductMasterError(
                f"Invalid discount_category '{discount_category}'. "
                f"Allowed: {sorted(VALID_DISCOUNT_CATEGORIES)}",
                status=422,
                field="discount_category",
            )
    else:
        dc = None
        if db is not None:
            try:
                from api.services import catalog_dictionary as _cd

                dc = _cd.load_brand_tier(db, (attributes or {}).get("brand_name"))
            except Exception:  # noqa: BLE001 - derivation is fail-soft
                dc = None

    # GST / HSN: THE HSN DECIDES THE RATE.
    #
    # A GST rate is a legal consequence of the HSN, not a second opinion typed
    # next to it, so whatever the HSN settles is what gets stored -- the
    # owner-editable hsn_gst_master first, then the canonical table
    # (resolve_gst_rate_strict, the same call the purchase side makes in
    # vendors._po_line_gst_rate off the same hsn_code). Deriving it HERE is what
    # lets every screen show a product's catalogued rate and be showing the rate
    # the server will actually charge, instead of each screen keeping an
    # HSN -> rate table of its own and drifting.
    #
    # An explicit rate, then the category default, remain the fallback for an
    # HSN the tables do not settle (a typed tariff code, an ambiguous heading).
    resolved_hsn = hsn_code or hsn_for_category(canonical)
    rate_from_hsn, _hsn_unsettled = resolve_gst_rate_strict(resolved_hsn)
    resolved_gst = (
        rate_from_hsn
        if rate_from_hsn is not None
        else (gst_rate if gst_rate is not None else gst_rate_for_category(canonical))
    )

    # SKU: accept a supplied (possibly legacy) SKU as-is if it passes the
    # permissive guard; otherwise mint the canonical one.
    if sku:
        if not is_acceptable_sku(sku):
            raise ProductMasterError(f"Invalid SKU '{sku}'.", status=422, field="sku")
        resolved_sku = str(sku).strip()
    else:
        resolved_sku = mint_unique_sku(
            canonical, attributes, product_repo=product_repo, db=db
        )

    ids = _derive_brand_model_color_size(attributes, canonical)

    # THE offer-write check (pricing_caps.offer_write_problem): an offer
    # deeper than the item's category / luxury-brand cap -- with the tier this
    # door just settled -- is refused at every create door and import.
    _cap_problem = offer_write_problem(mrp, offer_price, dc, ids["brand"])
    if _cap_problem:
        raise ProductMasterError(_cap_problem, status=400, field="offer_price")

    doc: Dict[str, Any] = {
        "sku": resolved_sku,
        "category": canonical,
        "sku_prefix": spec.prefix,
        "brand": ids["brand"],
        "model": ids["model"],
        "mrp": mrp,
        "offer_price": offer_price,
        "hsn_code": resolved_hsn,
        "gst_rate": resolved_gst,
        "attributes": dict(attributes or {}),
        "is_active": True,
        "created_by": created_by,
    }
    # Cataloguer attribution: the creator's human-readable name rides next to
    # the id so the Inventory "catalogued by" surface never needs a per-row
    # users join. Additive -- only stamped when known (older docs simply lack it).
    if created_by_name:
        doc["created_by_name"] = created_by_name
    # Identity columns only when present (additive -- keep doc lean).
    if ids["color"] is not None:
        doc["color"] = ids["color"]
    if ids["size"] is not None:
        doc["size"] = ids["size"]
    # Hub Phase 1: the brand+model+colour identity key for the duplicate guard.
    # Stamped only when brand+model are both present (the minimum that makes an
    # identity meaningful); categories without a brand/model -- e.g. SERVICES --
    # carry no identity_key and are not identity-deduped.
    _ident = compute_identity_key(*identity_parts(attributes, canonical))
    if _ident:
        doc["identity_key"] = _ident
    if dc is not None:
        doc["discount_category"] = dc
    # cost_price (Phase 0): persisted whenever supplied -- the done-rule reads it.
    if cost_price is not None:
        doc["cost_price"] = float(cost_price)
    if country_of_origin is not None:
        doc["country_of_origin"] = country_of_origin
    if warranty_months is not None:
        doc["warranty_months"] = int(warranty_months)
    if weight is not None:
        doc["weight"] = float(weight)
    # Normalised, governed tags (step-12). Always present as a list (possibly
    # empty) so collection rules + the tag filter have a consistent shape.
    doc["tags"] = normalise_tags(tags)
    # Door-specific additive columns -- never override a canonical key, never a
    # None value (keeps the spine lean + behaviour-preserving per door). Never a
    # website flag either: the brand default ALWAYS decides (owner D6), read
    # live by shopify_push.product_push_refusal, so a stored copy could only go
    # stale after a brand edit or a Brand Master change.
    for _k, _v in (extra_fields or {}).items():
        if _v is not None and _k not in doc and _k != "sync_to_shopify":
            doc[_k] = _v
    # Owner decision (2026-07-04): every new product is born with
    # reorder_quantity = -1 = "no auto-reorder" (see api/services/
    # reorder_policy.py). Reorder engines skip the product until someone
    # explicitly configures a positive quantity (PUT /products/{id} /
    # Reorder dashboard). setdefault so a door that DID supply a value
    # (via extra_fields) keeps it.
    doc.setdefault("reorder_quantity", -1)
    # No reorder level here: levels are per shop (owner ruling D12,
    # products.reorder_levels), a new product has none = not set.
    # --- SEO auto-naming: mint a display name when the payload leaves it blank ---
    # The spine has historically had NO `name` column (brand+model was the
    # implicit display identity), so products created via the Add-Product flow
    # landed blank on bills / POS lines / the online catalog. Auto-mint a
    # deterministic, SEO-shaped name from the attributes at save time. An
    # explicit door-supplied name (via extra_fields, e.g. a SERVICE's name)
    # is NEVER overwritten -- needs_name() only fires on a blank.
    if needs_name(doc):
        minted = minted_name(doc)
        if minted:
            doc["name"] = minted
    # --- Phase 0: stamp the catalog-done chokepoint on the spine ---
    # compute_catalog_status reads the doc we just built (cost_price + the
    # derived hsn/gst are now present), so the stamp is consistent with what was
    # validated. In STRICT mode it is ACTIVE when complete, or DRAFT naming
    # cost_price when the only missing piece is the not-yet-known cost; in
    # as_draft mode it reflects the real gaps so the Buy Desk + completion UI can
    # name them.
    status, gaps = compute_catalog_status(doc)
    # force_draft (IMPORT / CLONE doors): a COMPLETE payload would otherwise be
    # born ACTIVE (as_draft only relaxes the strict 422). These doors must land
    # DRAFT for review, so stamp DRAFT AT WRITE TIME -- no ACTIVE window, no
    # fail-soft post-insert demote that could leave a variant sellable.
    if force_draft:
        status = CATALOG_STATUS_DRAFT
    doc["catalog_status"] = status
    doc["done_gaps"] = gaps
    # provisional (ruling 13): ordered before it was catalogued. Marked
    # explicitly so it is never confused with a product someone deliberately
    # deactivated (discontinued), and inactive so it cannot surface in the
    # active-only /products search POS sells from.
    if provisional:
        doc["provisional"] = True
        doc["is_active"] = False
    return doc


# ===========================================================================
# Mirror gating (off-by-default -- NO live external write on a fresh deploy)
# ===========================================================================

_MIRROR_FLAG_KEY = "pm.mirror_enabled"


def mirror_enabled() -> bool:
    """Is the catalog/external mirror turned on?  ON by default (the registry
    spec pm.mirror_enabled defaults to True; the external Postgres/Shopify legs
    are additionally gated by DISPATCH_MODE).

    Resolved via E2 get_policy (so it is store/entity/global scoped + env
    overridable). The `default=False` below applies only if the registry does
    not know the key. Fail-soft: any error -> the PM_MIRROR_ENABLED env, else
    False (no mirror).
    """
    try:
        from .policy_engine import get_policy

        return bool(get_policy(_MIRROR_FLAG_KEY, default=False))
    except Exception:  # noqa: BLE001
        # Last-resort env fallback so ops can still gate without the registry.
        return os.getenv("PM_MIRROR_ENABLED", "").strip().lower() in (
            "1",
            "true",
            "on",
            "yes",
        )


def external_mirror_enabled() -> bool:
    """May a LIVE external (Postgres/BVI/Shopify) write fire?

    Requires BOTH the PM mirror flag AND the NEXUS DISPATCH_MODE=live gate --
    exactly like NEXUS. On a fresh deploy DISPATCH_MODE defaults to `off`, so
    this is False and no live external write can occur regardless of the flag.
    """
    if not mirror_enabled():
        return False
    try:
        from agents.providers import dispatch_mode

        return dispatch_mode() == "live"
    except Exception:  # noqa: BLE001
        return False


# ===========================================================================
# Spine-first triple-write
# ===========================================================================


@dataclass
class _SyncTarget:
    name: str
    status: str  # OK | FAILED | SKIPPED
    detail: Optional[str] = None


def _assert_pim_sku(spine: Dict[str, Any]) -> None:
    """Refuse to write a catalog_products PIM doc without a `sku`.

    WHY THIS CANNOT BE A SILENT SKIP: `catalog_products.sku_1` is UNIQUE +
    SPARSE. A doc with the key ABSENT is exempt from the index, but a doc
    carrying an explicit `sku: None` IS indexed -- so a null would insert once
    and then DuplicateKeyError forever, swallowed into a FAILED sync_status
    nobody reads. Failing loud inside the writer's own try block turns that into
    a recorded FAILED target on the FIRST write instead of a silent divergence.

    Deliberately NOT called from _build_pim_doc: that builder runs OUTSIDE the
    try block in _write_mirror, whose contract is "NEVER raises". A raise there
    would escape as a 500 AFTER the spine row is already committed and would
    skip _stage_catalog_draft, leaving exactly the dangling pim_product_id that
    staging exists to prevent. Each writer calls this from INSIDE its own try.
    """
    if not str(spine.get("sku") or "").strip():
        raise ProductMasterError(
            "Cannot mirror a product without a SKU (catalog_products.sku is "
            "unique+sparse; a null would poison the index).",
            status=500,
            field="sku",
        )


def _is_duplicate_key_error(exc: BaseException) -> bool:
    """True for pymongo's DuplicateKeyError (matched by class NAME, the same
    convention create_product uses, so fakes raising the real class or a
    same-named stand-in both match without importing pymongo here)."""
    return any(
        klass.__name__ == "DuplicateKeyError" for klass in type(exc).__mro__
    )


def _sku_conflict_target(
    name: str, spine: Dict[str, Any], exc: BaseException, *, catalog_repo=None, db=None
) -> _SyncTarget:
    """Build the DISTINCT FAILED_SKU_CONFLICT sync target for a catalog_products
    unique-index (sku_1) collision, logging the colliding sku AND the existing
    row's id at ERROR.

    WHY DISTINCT: prod holds sku-carrying catalog rows with NO spine, invisible
    to the spine dedupe -- a deterministic mint can collide with one, the upsert
    raises DuplicateKeyError, and before this the failure was swallowed into a
    generic FAILED target while the API returned 201 (dangling pim_product_id,
    no catalog row). A named status + an ERROR log make that diagnosable.
    Fail-soft: the existing-row lookup never raises (id logged as None if
    unreadable)."""
    sku = spine.get("sku")
    existing_id = None
    try:
        row = None
        if catalog_repo is not None and hasattr(catalog_repo, "find_by_sku"):
            row = catalog_repo.find_by_sku(sku)
        elif db is not None:
            if hasattr(db, "get_collection"):
                coll = db.get_collection("catalog_products")
            else:
                coll = db["catalog_products"]
            if coll is not None:
                row = coll.find_one({"sku": sku})
        existing_id = (row or {}).get("id")
    except Exception:  # noqa: BLE001 - diagnostics must never raise
        existing_id = None
    logger.error(
        "[PM] catalog_products sku CONFLICT (%s): sku %r already belongs to "
        "existing catalog row id=%r -- PIM write rejected by the sku_1 unique "
        "index: %s",
        name,
        sku,
        existing_id,
        exc,
    )
    return _SyncTarget(
        name,
        "FAILED_SKU_CONFLICT",
        f"sku {sku!r} already on catalog row id={existing_id!r}: {str(exc)[:120]}",
    )


def pim_display_name(spine: Dict[str, Any]) -> Optional[str]:
    """THE twin's display name / Shopify title, from the spine: the spine's own
    ``name`` (minted at create by product_naming, or set by a rename), else the
    deterministic builder. ONE formula -- _build_pim_doc uses it at create and
    mirror_update_to_catalog_twin re-derives it after an edit (sync audit gap
    #3: a rename never reached Shopify's title). Pure; never raises."""
    return spine.get("name") or build_product_name(spine) or None


def _build_pim_doc(
    spine: Dict[str, Any], parent: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Project the spine + its attributes into a catalog_products PIM doc.

    ``parent`` (the resolved parent SPINE, create_product's variant_of) marks
    the twin as a SIZE VARIANT: ``ecom.variant_of`` names the parent in every
    id space a reader needs (spine product_id / twin id / sku -- one key, no
    wrong-space join) and the twin is born CLEAN (locally_modified False): it
    owns no listing, so it is never queued for a push; its price, barcode and
    stock ride the parent's listing through its catalog_variants row.

    PURE: builds and returns a dict, never raises, never touches the DB. The
    SKU precondition lives in _assert_pim_sku, called from inside each writer's
    try block (see that docstring for why it must not raise from here).

    The PIM doc is schemaless; it carries the Shopify/PIM superset attributes
    untouched under `ecom.category_specific` so they round-trip to NEXUS. It
    lands `ecom.status = DRAFT` (never live) with an SEO title + handle so the
    Online Store push (shopify_push.build_product_input reads ecom.status /
    ecom.handle / ecom.seo.{title,description,tags} + top-level name) has a
    complete, reviewable-but-not-sellable object the moment it is created. It
    also lands `ecom.locally_modified = True` so the row is QUEUED for the
    manual Online Store push (see the comment on that field below).
    """
    attrs = dict(spine.get("attributes") or {})
    name = pim_display_name(spine)
    seo_title = build_seo_title(spine)
    handle = build_handle(spine)
    seo_description = build_seo_description(spine)
    description = spine.get("description")
    tags = list(spine.get("tags") or [])

    # Owner 2026-08-25 -- SMART GLASSES LIST THEMSELVES. A smart glass's spec
    # sheet (camera / audio / assistant / battery / connectivity / storage /
    # prescription-ready) is now CAPTURED as fields at cataloguing, so the
    # storefront listing is DERIVED from them instead of being hand-written
    # HTML. Only fills what the cataloguer did not supply: a typed/AI-written
    # description always wins, and generated tags are UNIONED onto the product's
    # own. Runs at CREATE ONLY (both _build_pim_doc callers are create_product),
    # so no existing live listing is ever re-derived.
    if resolve_category(spine.get("category")) == "SMARTGLASSES":
        try:
            from .smartglass_listing import build_listing

            listing = build_listing(attrs, paragraph=None)
            if not str(description or "").strip():
                description = listing["description"] or None
            if listing["seo_title"]:
                seo_title = listing["seo_title"]
            if listing["seo_description"]:
                seo_description = listing["seo_description"]
            for tag in listing["tags"]:
                if tag not in tags:
                    tags.append(tag)
        except Exception:  # noqa: BLE001 - listing copy must never fail a create
            logger.warning("[PM] smartglass listing build failed", exc_info=True)

    doc: Dict[str, Any] = {
        "id": spine.get("pim_product_id"),
        # ROOT FIX: the PIM row IS the parent and carries its OWN `sku`. Every
        # online consumer joins catalog_products on `sku`, never `parent_sku` --
        # the push block classifier (online_store_push: doc.get("sku") in
        # blocked_set), online_block._membership_hit, shopify_push's collection
        # member lookup (find_one({"sku": sku})) and ecom_smart_rules all read
        # the top-level `sku`. Without it those guards silently no-op. A
        # PM-created parent has exactly ONE default variant whose sku == the
        # spine sku, the same convention the CATALOG door uses (it stamps the
        # SAME sku on the catalog doc and the spine).
        "sku": spine.get("sku"),
        # KEEP parent_sku: still read by online_store_push's id/sku/parent_sku
        # fallback + variant linkage, online_discount_engine's variant lookup,
        # and the frontend (raw.sku ?? raw.parent_sku).
        "parent_sku": spine.get("sku"),
        "category": spine.get("category"),
        "sku_prefix": spine.get("sku_prefix"),
        "brand": spine.get("brand"),
        "model": spine.get("model"),
        "mrp": spine.get("mrp"),
        "offer_price": spine.get("offer_price"),
        "hsn_code": spine.get("hsn_code"),
        "gst_rate": spine.get("gst_rate"),
        # The PUBLIC barcode's top-level projection (the push itself reads the
        # gtin ATTRIBUTE, product_input.variant_barcode; the one-holder rule,
        # find_twin_by_barcode, reads this too).
        "gtin": attrs.get("gtin") or spine.get("gtin") or None,
        # Display identity the storefront + push read (top-level name/title).
        "name": name or None,
        "title": name or None,
        # The storefront body. shopify_push.build_product_input reads
        # `ecom.seo.html or description` -- this key was simply never projected,
        # so an IMS-born product reached Shopify with NO description at all.
        "description": description or None,
        # Legacy top-level status kept for existing readers; ecom.status is the
        # one the Shopify push honours.
        "status": "DRAFT",
        "ecom": {
            "status": "DRAFT",
            # BORN DIRTY -- the same convention ecom_collection_repository.create
            # and ecom_menu_repository.create use for a new collection / menu: a
            # row that has never reached Shopify belongs in the MANUAL push queue
            # from the moment it is catalogued. Without it a freshly catalogued
            # product was invisible to BOTH readers of this flag -- the operator's
            # "push all pending" sweep (online_store_push.py, the dirty_products
            # loop) and the `pending` count on the Online Store screen
            # (online_store_push._product_counts) -- so the screen reported
            # "pending: 0" while nothing had been queued: a SILENT miss, not an
            # error. Queuing is NOT publishing: a human still presses the push
            # button, and the flag is cleared only by a SUCCESSFUL Shopify write
            # (shopify_push._writeback_product).
            "locally_modified": True,
            "handle": handle or None,
            "seo": {
                "title": seo_title or name or None,
                "description": seo_description or None,
                "tags": tags,
            },
            "category_specific": attrs,
        },
        # Surface the common Shopify superset fields top-level too (verbatim).
        "attributes": attrs,
        # THE PHOTOGRAPH. The publish gate ("no photo, no publish") and the
        # media the push attaches both read the TWIN's image fields
        # (shopify_push.product_photo_urls), and this projection never carried
        # them: prod 2026-09-04 had 69 of 76 spine products with an absolute
        # photo URL and only 6 twins -- every IMS-catalogued product was
        # refused as "no photograph" while its photo sat one collection over.
        "images": _spine_images(spine),
    }
    if parent:
        doc["ecom"]["variant_of"] = {
            "product_id": parent.get("product_id"),
            "twin_id": parent.get("pim_product_id") or parent.get("product_id"),
            "sku": parent.get("sku"),
        }
        doc["ecom"]["locally_modified"] = False
    if is_ordered_draft(spine):
        # Ordered before it was catalogued (ruling 13): the cataloguer's work.
        # It sits in the Needs-review queue (count, badge and list read
        # is_ordered_draft via in_needs_review; the catalog population
        # excludes it) until it is
        # finished, and names its spine so the queue opens the spine editor --
        # it already HAS a billing row, so the import "approve" path does not
        # apply. mirror_update_to_catalog_twin clears the flag when it is
        # finished. No is_active here: a twin never carries a projected flag
        # (the drawer's spine sync relies on that, test_variant_of_rule).
        doc["needs_review"] = True
        doc["spine_product_id"] = spine.get("product_id")
    return doc


def main_photo(doc: Dict[str, Any]) -> Optional[str]:
    """THE main-photo rule (owner 2026-10-12: "photo 1 is the main photo on
    the website and at the till"). The order of a product's photos IS its
    ``images`` array, so the main photo is its first usable entry (a string
    or a ``{url|src}`` dict); only a product with no ``images`` falls back to
    the legacy singular ``image_url``, then ``image``. A stale ``image_url``
    never outranks photo 1. Every reader that shows ONE photo for a product
    asks here. Pure; never raises."""
    doc = doc if isinstance(doc, dict) else {}
    imgs = doc.get("images")
    for item in imgs if isinstance(imgs, (list, tuple)) else ():
        if isinstance(item, dict):
            item = item.get("url") or item.get("src")
        if isinstance(item, str) and item.strip():
            return item.strip()
    for key in ("image_url", "image"):
        value = doc.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _spine_images(spine: Dict[str, Any]) -> List[str]:
    """The spine's photo URLs as the twin stores them: strings, trimmed, blanks
    dropped, order kept. The spine carries `images[]` only -- its `image_url`
    is a read-time alias the list endpoints stamp, never stored."""
    imgs = spine.get("images")
    if not isinstance(imgs, (list, tuple)):
        return []
    return [u.strip() for u in imgs if isinstance(u, str) and u.strip()]


_SAME_AS_PARENT: Any = object()


def _write_mirror(
    spine: Dict[str, Any],
    *,
    parent: Optional[Dict[str, Any]] = None,
    row_parent: Any = _SAME_AS_PARENT,
    catalog_repo=None,
    variant_repo=None,
    db=None,
) -> List[_SyncTarget]:
    """Best-effort, GATED mirror of the spine to catalog_products,
    catalog_variants, and (when live) the external Postgres/Shopify targets.

    Returns a per-target status list. NEVER raises -- a mirror failure is logged
    + recorded, the spine is untouched. Skips entirely when the flag is off.
    """
    targets: List[_SyncTarget] = []

    if not mirror_enabled():
        # Off-by-default: no catalog write, no external write. Record SKIPPED.
        return [
            _SyncTarget("catalog_products", "SKIPPED", "mirror flag off"),
            _SyncTarget("catalog_variants", "SKIPPED", "mirror flag off"),
            _SyncTarget("external", "SKIPPED", "mirror flag off"),
        ]

    pim_doc = _build_pim_doc(spine, parent=parent)

    # --- catalog_products (Mongo PIM doc) -- internal, flag-gated only ---
    try:
        # Precondition INSIDE the try: a skuless PIM doc is refused loudly and
        # recorded as FAILED, never written as an index-poisoning null.
        _assert_pim_sku(spine)
        if catalog_repo is not None:
            catalog_repo.upsert(pim_doc)
            targets.append(_SyncTarget("catalog_products", "OK"))
        elif db is not None:
            db.get_collection("catalog_products").update_one(
                {"id": pim_doc["id"]}, {"$set": pim_doc}
            )
            targets.append(_SyncTarget("catalog_products", "OK"))
        else:
            # No catalog target wired -> record SKIPPED, never a false OK.
            targets.append(
                _SyncTarget("catalog_products", "SKIPPED", "no catalog target")
            )
    except Exception as exc:  # noqa: BLE001
        if _is_duplicate_key_error(exc):
            # sku_1 unique-index collision: DISTINCT status + ERROR naming the
            # colliding sku and the existing row, never a generic FAILED.
            targets.append(
                _sku_conflict_target(
                    "catalog_products", spine, exc, catalog_repo=catalog_repo, db=db
                )
            )
        else:
            logger.warning(
                "[PM] catalog_products mirror failed for %s: %s", spine.get("sku"), exc
            )
            targets.append(_SyncTarget("catalog_products", "FAILED", str(exc)[:200]))

    # --- catalog_variants (per-SKU identity) -- internal, flag-gated only ---
    try:
        if variant_repo is not None:
            variant_repo.upsert(
                _variant_row_for(spine, parent if row_parent is _SAME_AS_PARENT else row_parent)
            )
            targets.append(_SyncTarget("catalog_variants", "OK"))
        else:
            targets.append(
                _SyncTarget("catalog_variants", "SKIPPED", "no variant repo")
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[PM] catalog_variants mirror failed for %s: %s", spine.get("sku"), exc
        )
        targets.append(_SyncTarget("catalog_variants", "FAILED", str(exc)[:200]))

    # --- external (Postgres/BVI + Shopify) -- DISPATCH_MODE-gated ---
    if external_mirror_enabled():
        # A real external push would happen here via nexus_providers. We DO NOT
        # fire it inline (it is async + owned by the NEXUS agent); the spine is
        # the SoR and NEXUS reconciles from it. Recording it as deferred keeps
        # the compensation log honest without a synchronous external call.
        targets.append(_SyncTarget("external", "OK", "queued for NEXUS dispatch"))
    else:
        targets.append(
            _SyncTarget("external", "SKIPPED", "DISPATCH_MODE not live / flag off")
        )

    return targets


def _variant_row_for(
    spine: Dict[str, Any], parent: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """The catalog_variants row the create door writes for a spine. Pure.

    A regular product: its own self-row (sku == parent_sku, parent_product_id
    = its own twin) -- the one row every online consumer joins on.

    A SIZE VARIANT (``parent`` given): the row points at the PARENT twin. That
    ONE link is what variants_for_product / _gid_products_with_variants /
    the discount engine already join on, so the parent's stock pass, price
    pass (push_variant_prices) and discount recompute carry the child with no
    new pipeline code. The row MUST carry the child's own ``mrp``: the price
    push resolves row.discounted_price, else row.mrp, else the PARENT's price
    -- without it the Large would ship at the base size's price. ``gtin`` is
    the child's own public barcode (the repo drops an invalid one);
    ``option_size`` is the Size option value a first-publish seed matches
    against Shopify's. Never discounted_price/compare_at_price: those belong
    to the online discount engine (a child's online price = its own MRP under
    the family's rule; its in-store offer_price stays a POS number)."""
    if not parent:
        # A product of its own: no options (its listing is one item). A model's
        # LEAD gets its colour/size from refresh_family_options (G1).
        return {
            "sku": spine.get("sku"),
            "parent_product_id": spine.get("pim_product_id") or spine.get("product_id"),
            "parent_sku": spine.get("sku"),
        }
    attrs = spine.get("attributes") or {}
    row: Dict[str, Any] = {
        "sku": spine.get("sku"),
        "parent_product_id": parent.get("pim_product_id") or parent.get("product_id"),
        "parent_sku": parent.get("sku"),
        # The colour and size the variant is chosen by (G1, owner 2026-10-18;
        # refresh_family_options settles a clash between two members).
        **variant_option_values(spine),
        "mrp": spine.get("mrp"),
    }
    if attrs.get("gtin"):
        row["gtin"] = attrs.get("gtin")
    return row


def _stage_catalog_draft(
    spine: Dict[str, Any],
    *,
    parent: Optional[Dict[str, Any]] = None,
    catalog_repo=None,
    db=None,
) -> _SyncTarget:
    """Stage the spine as a catalog_products PIM DRAFT doc -- ALWAYS, regardless
    of the mirror flag.

    ROOT-CAUSE FIX: create_product pre-assigns every spine a `pim_product_id`
    (the intended catalog_products link), but the ONLY writer of that
    catalog_products doc used to be `_write_mirror`, which is gated behind the
    off-by-default `pm.mirror_enabled` flag. So on a normal deploy the
    pim_product_id pointed at a doc that was never created -- a DANGLING link,
    and the product could never be pushed online (the Online Store products
    card counts `catalog_products` with an `ecom` sub-doc).

    Staging a LOCAL Mongo DRAFT doc is NOT an external write: the mirror flag /
    DISPATCH_MODE gate exists to guard the EXTERNAL (Postgres/Shopify) push,
    which stays gated (ecom.status=DRAFT is never live). So the internal DRAFT
    doc is created unconditionally to make the link valid and the product
    push-able later.

    Idempotent (upsert on `id`), fail-soft (NEVER raises -- returns a
    _SyncTarget the caller records). Recorded under its OWN target key
    ("catalog_draft") so the flag-gated `catalog_products` mirror target
    semantics are untouched.
    """
    pim_id = spine.get("pim_product_id")
    if not pim_id:
        return _SyncTarget("catalog_draft", "SKIPPED", "no pim_product_id")
    try:
        # Same precondition as _write_mirror, inside this writer's own try so
        # staging records a FAILED target instead of raising into the create.
        _assert_pim_sku(spine)
        pim_doc = _build_pim_doc(spine, parent=parent)
        if catalog_repo is not None:
            catalog_repo.upsert(pim_doc)
            return _SyncTarget("catalog_draft", "OK")
        if db is not None:
            coll = db.get_collection("catalog_products")
            if coll is None:
                return _SyncTarget("catalog_draft", "SKIPPED", "no catalog collection")
            coll.update_one({"id": pim_id}, {"$set": pim_doc}, upsert=True)
            return _SyncTarget("catalog_draft", "OK")
        return _SyncTarget("catalog_draft", "SKIPPED", "no catalog target")
    except Exception as exc:  # noqa: BLE001 - staging must never fail a create
        if _is_duplicate_key_error(exc):
            # Same distinct handling as _write_mirror: a sku_1 collision is a
            # NAMED failure, never a generic FAILED nobody investigates.
            return _sku_conflict_target(
                "catalog_draft", spine, exc, catalog_repo=catalog_repo, db=db
            )
        logger.warning(
            "[PM] catalog_products DRAFT staging failed for %s: %s",
            spine.get("sku"),
            exc,
        )
        return _SyncTarget("catalog_draft", "FAILED", str(exc)[:200])


def _sync_status_dict(targets: List[_SyncTarget]) -> Dict[str, Any]:
    return {
        "mirrored_at": utc_now_iso(),
        "targets": {t.name: {"status": t.status, "detail": t.detail} for t in targets},
    }


# ===========================================================================
# Unification step-9: ONE canonical product-create door the spine entry paths use
# ===========================================================================
# The SPINE-WRITING doors -- FORM (POST /products), BULK (/products/bulk-create),
# and MASTER (POST /products/master) -- call create_via_door() so the registry is
# the rulebook AND the Phase-1 duplicate hard-block applies at every spine write.
# The CATALOG door (POST /catalog/products) currently runs the SAME validate-and-
# build core (build_canonical_product) for parity, but persists to the SEPARATE
# catalog_products collection with its own minted SKU -- it does NOT yet write the
# `products` spine or run the spine dup-block. Routing it through create_via_door
# is the owner-gated step-10 spine-unification (tracked; not in Phase 1). Each
# door keeps its own auth/RBAC + response shape; only the validate+build CORE
# unifies here.
#
# STRICT (owner decision #7): an incomplete product is REJECTED at entry. The
# core validates through the registry (resolve_category -> validate_attributes ->
# required_fields) and enforces the existing invariants (MRP>=offer blocked,
# category->GST/HSN, category->discount-cap) -- it never changes those values.

# The known source labels. FORM = POST /products; BULK = /products/bulk-create;
# CATALOG = POST /catalog/products; MASTER = the engine door (POST /products/master);
# IMPORT = Hub Phase 3 vendor price-list import (rows land as_draft for review).
VALID_DOOR_SOURCES = frozenset({"FORM", "BULK", "CATALOG", "MASTER", "IMPORT", "CLONE"})

# Top-level identity fields some doors (FORM/BULK) carry OUTSIDE `attributes`.
# Mapped INTO the canonical attribute keys the registry validates, so a frame
# created via the flat /products schema is gated identically to one created via
# the attribute-dict /catalog schema. Only fills a key the caller did not
# already set in attributes (attributes win -- they are the explicit source).
_DOOR_IDENTITY_ALIASES = {
    "brand": "brand_name",
    "color": "colour_code",
    "colour": "colour_code",
    "size": "size",
}


def _size_attribute_key(category: Any) -> Optional[str]:
    """Where a flat top-level `size` lands: the category's OWN registry key --
    or nowhere (None) for a category that records no size at all (WATCH,
    CONTACT_LENS, ...): its catalogue form has no size field, so a size typed
    on a PO line would key the draft apart from the catalogued item and mint
    a hidden twin (audit C2/C3).

    A frame's (and sunglass's) eye size lives in `lens_size` -- `size` was
    REMOVED from their registry, and the Add-Product form saves lens_size. The
    alias used to write `size` regardless, so the same Boss 1700 C2 52 got a
    4-part identity_key off the PO's "not in the catalogue?" line and a 3-part
    one off the catalogue form, the duplicate guard never matched the two, and
    every typed-in frame became a hidden twin (audit C2/C3). Categories that do
    keep `size` in their registry (ACCESSORIES) keep it."""
    spec = category_spec(category)
    if spec is not None:
        fields = spec.required + spec.optional
        if "size" in fields:
            return "size"
        return "lens_size" if "lens_size" in fields else None
    return "size"


def normalise_door_payload(payload: Dict[str, Any], *, source: str) -> Dict[str, Any]:
    """Fold a door's create payload into the canonical create kwargs.

    Produces the attribute dict the registry validates by merging any top-level
    identity fields (brand/model/color/size on the flat FORM/BULK schema) into
    `attributes` under the registry's canonical keys, WITHOUT clobbering a value
    already present in `attributes`. Behaviour-preserving: a CATALOG payload that
    already carries brand_name/model_no in `attributes` is untouched.
    """
    p = dict(payload or {})
    attrs: Dict[str, Any] = dict(p.get("attributes") or {})
    for top_key, attr_key in _DOOR_IDENTITY_ALIASES.items():
        val = p.get(top_key)
        if val is not None and not (isinstance(val, str) and not val.strip()):
            if top_key == "size":
                attr_key = _size_attribute_key(p.get("category"))
                if attr_key is None:
                    continue
            attrs.setdefault(attr_key, val)
    # The model lands in the field its category's form shows: Model No and/or
    # Model Name (a frame shows both: both, as before), else an Optical Lens's
    # Sub Brand. A flat `model` (the PO 'new item' line) or a model_no /
    # model_name the form has no field for (a price-list import, a clone of a
    # main-era row) moves there, so the door stores no hidden copy and the SKU,
    # the name and the duplicate key read what was typed (_shown_attributes).
    spec = category_spec(p.get("category"))
    fields = spec.required + spec.optional if spec else _MODEL_KEYS
    copies = [attrs.pop(k) for k in _MODEL_KEYS if k not in fields and k in attrs]
    model = next((v for v in (*copies, p.get("model")) if not _is_blank(v)), None)
    shown = [k for k in _MODEL_KEYS if k in fields] or [k for k in ("subbrand",) if k in fields]
    for k in shown if model is not None else ():
        if _is_blank(attrs.get(k)):
            attrs[k] = model
    p["attributes"] = attrs
    p["_source"] = source if source in VALID_DOOR_SOURCES else "FORM"
    return p


def build_canonical_product(
    payload: Dict[str, Any],
    *,
    source: str,
    extra_fields: Optional[Dict[str, Any]] = None,
    product_repo=None,
    db=None,
) -> Dict[str, Any]:
    """Validate + build the canonical `products` spine doc for a door payload.

    This is the SHARED validate-and-build core every door runs so the SAME
    complete payload yields an IDENTICAL canonical product at all three doors,
    and the SAME incomplete payload is rejected (422) at all three. Raises
    ProductMasterError on any registry / invariant breach (the caller maps it to
    HTTP). Does NOT persist -- create_via_door() layers persistence on top.

    `extra_fields` are additive door-specific top-level columns (see
    normalise_payload) merged onto the spine without overriding a canonical key.
    """
    p = normalise_door_payload(payload, source=source)
    return normalise_payload(
        category=p.get("category"),
        attributes=p.get("attributes") or {},
        mrp=p.get("mrp"),
        offer_price=p.get("offer_price"),
        sku=p.get("sku"),
        cost_price=p.get("cost_price"),
        discount_category=p.get("discount_category"),
        hsn_code=p.get("hsn_code"),
        gst_rate=p.get("gst_rate"),
        country_of_origin=p.get("country_of_origin"),
        warranty_months=p.get("warranty_months"),
        weight=p.get("weight"),
        tags=p.get("tags"),
        created_by=p.get("created_by") or p.get("actor"),
        created_by_name=p.get("created_by_name") or p.get("actor_name"),
        as_draft=bool(p.get("as_draft", False)),
        provisional=bool(p.get("provisional", False)),
        extra_fields=extra_fields,
        product_repo=product_repo,
        db=db,
    )


def resolve_actor_name(actor: Optional[str], db=None) -> Optional[str]:
    """Best-effort display name for an actor user_id (cataloguer attribution).

    Routers that hold the JWT pass the username straight through as
    `actor_name`; this fallback covers doors that only carry the id (the
    catalog promote/import paths) by looking the user up once at write time.
    FAIL-SOFT: no db / unknown user / any error -> None (the doc simply
    carries no created_by_name, exactly like legacy rows).
    """
    if not actor or db is None:
        return None
    try:
        users = db.get_collection("users")
        if users is None:
            return None
        rec = users.find_one(
            {"user_id": actor}, {"_id": 0, "username": 1, "full_name": 1}
        )
        if not rec:
            return None
        return rec.get("username") or rec.get("full_name") or None
    except Exception:  # noqa: BLE001 - attribution must never block a write
        return None


def create_via_door(
    payload: Dict[str, Any],
    *,
    source: str,
    actor: str,
    actor_name: Optional[str] = None,
    extra_fields: Optional[Dict[str, Any]] = None,
    product_repo=None,
    catalog_repo=None,
    variant_repo=None,
    audit_repo=None,
    force_draft: bool = False,
    db=None,
) -> Dict[str, Any]:
    """THE single create path every product-entry door delegates to (step-9).

    `force_draft` (IMPORT / CLONE doors) stamps the spine catalog_status=DRAFT AT
    WRITE TIME even for a complete payload, so an imported/cloned product is born
    DRAFT (reviewable, not sellable) -- no ACTIVE window, no fail-soft post-insert
    demote.

    Validates through the registry (STRICT), enforces the existing invariants,
    writes the canonical `products` spine, and (mirror ON by default) writes the
    fail-soft catalog/variant shadow. Returns the created canonical spine doc
    (with `sync_status`). Raises ProductMasterError on a validation failure
    (before any write) -- the calling router maps `.status`/`.field` to HTTP.

    `source` (FORM|BULK|CATALOG|MASTER) is recorded on the doc for provenance;
    the validation + write behaviour is identical across sources. `extra_fields`
    are additive door-specific top-level columns (e.g. the FORM door's CL/lens
    power identity) merged onto the spine without overriding a canonical key.
    """
    p = normalise_door_payload(payload, source=source)
    created = create_product(
        category=p.get("category"),
        attributes=p.get("attributes") or {},
        mrp=p.get("mrp"),
        offer_price=p.get("offer_price"),
        actor=actor,
        actor_name=actor_name,
        sku=p.get("sku"),
        cost_price=p.get("cost_price"),
        discount_category=p.get("discount_category"),
        hsn_code=p.get("hsn_code"),
        gst_rate=p.get("gst_rate"),
        country_of_origin=p.get("country_of_origin"),
        warranty_months=p.get("warranty_months"),
        weight=p.get("weight"),
        tags=p.get("tags"),
        as_draft=bool(p.get("as_draft", False)),
        force_draft=force_draft,
        provisional=bool(p.get("provisional", False)),
        variant_of=p.get("variant_of"),
        family_opt_out=bool(p.get("family_opt_out", False)),
        # The size-variant runbook (scripts/create_large_variants.py, the
        # MASTER door) creates the variant on the live listing itself.
        live_lead_rows=p["_source"] == "MASTER",
        extra_fields=extra_fields,
        product_repo=product_repo,
        catalog_repo=catalog_repo,
        variant_repo=variant_repo,
        audit_repo=audit_repo,
        db=db,
    )
    # Record the entry door for provenance (additive; never affects validation).
    try:
        if created is not None:
            created.setdefault("source_door", p["_source"])
    except Exception:  # noqa: BLE001
        pass
    return created


# Catalog fields copied when cloning a product into a variant. Identity (sku,
# product_id, _id), stock, and ecom/sync state are NEVER cloned -- each variant
# is a brand-new spine row with its own minted SKU.
_CLONE_CATALOG_FIELDS = (
    "category",
    "mrp",
    "offer_price",
    "cost_price",
    "discount_category",
    "hsn_code",
    "gst_rate",
    "country_of_origin",
    "warranty_months",
    "weight",
)


def clone_and_vary(
    *,
    source_id: str,
    variations: List[Dict[str, Any]],
    actor: str,
    product_repo=None,
    catalog_repo=None,
    variant_repo=None,
    audit_repo=None,
    db=None,
) -> Dict[str, Any]:
    """Hub Phase 4: clone one product across N attribute variations (e.g. the
    same frame in 20 colours, or a lens in 3 indices) into N new catalog_status=
    DRAFT products for review. Each variant inherits the source's catalog fields
    + attributes, overlays the per-variation attribute overrides (colour_code /
    size / power / ...), mints its OWN unique SKU, and lands DRAFT (as_draft) via
    the canonical create door -- so the Phase-1 duplicate guard + the done-rule
    apply to every variant. A variation that collides with an existing product
    (409) is collected as an error, never aborts the batch.

    Returns {source_id, created: [{product_id, sku, attributes}], errors:
    [{index, error, ...}]}. Raises ProductMasterError(404) if the source is gone.
    """
    if product_repo is None:
        raise ProductMasterError("No product repository.", status=500)
    src = get_product(source_id, product_repo=product_repo)
    if src is None:
        raise ProductMasterError("Source product not found.", status=404)

    # canonical attrs incl. legacy overlay -- never the source's manufacturer
    # barcodes: a GTIN names ONE trade item, so a new SKU never inherits one (a
    # variation may still carry its own).
    base_attrs = {
        k: v
        for k, v in _overlay_attributes(src).items()
        if manufacturer_barcode_key(k) is None
    }
    base_payload: Dict[str, Any] = {}
    for f in _CLONE_CATALOG_FIELDS:
        if src.get(f) is not None:
            base_payload[f] = src.get(f)

    created: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for idx, variation in enumerate(variations or []):
        if not isinstance(variation, dict):
            errors.append({"index": idx, "error": "variation must be an object"})
            continue
        # overlay the variation's attribute overrides onto the cloned base attrs
        merged_attrs = dict(base_attrs)
        for k, v in variation.items():
            if v is not None and not (isinstance(v, str) and not v.strip()):
                merged_attrs[k] = v
        payload = dict(base_payload)
        payload["attributes"] = merged_attrs
        payload["sku"] = None  # auto-mint a unique SKU per variant
        # force_draft below stamps DRAFT at write time -- a complete variant is
        # born DRAFT (reviewable, not sellable), no ACTIVE window.
        payload["as_draft"] = True
        try:
            doc = create_via_door(
                payload,
                source="CLONE",
                actor=actor,
                product_repo=product_repo,
                catalog_repo=catalog_repo,
                variant_repo=variant_repo,
                audit_repo=audit_repo,
                force_draft=True,
                db=db,
            )
        except ProductMasterError as err:
            errors.append(
                {
                    "index": idx,
                    "error": err.message,
                    "code": getattr(err, "code", None),
                    "existing": getattr(err, "conflict", None),
                }
            )
            continue
        except (
            Exception
        ) as exc:  # noqa: BLE001 - one infra blip must not abort the batch
            logger.warning("[CLONE] variant %d failed unexpectedly: %s", idx, exc)
            errors.append({"index": idx, "error": "unexpected error creating variant"})
            continue
        created.append(
            {
                "product_id": (doc or {}).get("product_id"),
                "sku": (doc or {}).get("sku"),
                "catalog_status": (doc or {}).get("catalog_status"),
                "attributes": merged_attrs,
            }
        )
    return {"source_id": source_id, "created": created, "errors": errors}


def create_product(
    *,
    category: Any,
    attributes: Dict[str, Any],
    mrp: float,
    offer_price: float,
    actor: str,
    actor_name: Optional[str] = None,
    sku: Optional[str] = None,
    cost_price: Optional[float] = None,
    discount_category: Optional[str] = None,
    hsn_code: Optional[str] = None,
    gst_rate: Optional[float] = None,
    country_of_origin: Optional[str] = None,
    warranty_months: Optional[int] = None,
    weight: Optional[float] = None,
    tags: Any = None,
    as_draft: bool = False,
    force_draft: bool = False,
    provisional: bool = False,
    variant_of: Optional[str] = None,
    family_opt_out: bool = False,
    live_lead_rows: bool = False,
    extra_fields: Optional[Dict[str, Any]] = None,
    product_repo=None,
    catalog_repo=None,
    variant_repo=None,
    audit_repo=None,
    db=None,
) -> Dict[str, Any]:
    """SPINE-FIRST + COMPENSATION triple-write.

    MODEL FAMILIES (G1, owner 2026-10-18): with no ``variant_of``, a product
    whose model already exists is linked to that model's lead automatically
    (find_family_lead) -- unless ``family_opt_out`` ('This is a different
    model'), which is stored so nothing links it later. A new member takes
    the lead's shape and gender (model-level). Under a lead that is ON
    SHOPIFY the new member keeps its own catalog_variants row and waits for
    the website step (G2); only ``live_lead_rows`` (the size-variant
    runbook, which creates the variant on the listing itself) writes the row
    under a live lead.

    ``variant_of`` (owner ruling 2026-09-06): the parent spine's product_id
    when this product is a SIZE VARIANT of another -- its own spine, SKU,
    stock and POS sale, but NO Shopify listing of its own. Validated at this
    ONE door (parent exists, same category, a parent is never itself a
    variant) and written ONCE into the three id spaces the codebase already
    has: spine ``variant_of`` (parent product_id), twin ``ecom.variant_of``
    (_build_pim_doc), and the catalog_variants row's ``parent_product_id``
    (_write_mirror) -- the link every online pass already joins on.

    Order (CORRECTIONS-binding):
      1. write the `products` spine FIRST + alone (single-document, atomic).
         This is the durable source of truth.
      2. (gated, best-effort) mirror to catalog_products / catalog_variants /
         external. A mirror failure NEVER rolls back the spine.
      3. write the per-target sync status back on the spine (single-doc update).
      4. write the immutable audit row (AuditRepository.create).

    `extra_fields` are additive door-specific top-level columns merged onto the
    spine without overriding a canonical key (see normalise_payload).

    Returns the created spine doc (with `sync_status`). Raises
    ProductMasterError on a validation failure (before any write).
    """
    spine = normalise_payload(
        category=category,
        attributes=attributes,
        mrp=mrp,
        offer_price=offer_price,
        sku=sku,
        cost_price=cost_price,
        discount_category=discount_category,
        hsn_code=hsn_code,
        gst_rate=gst_rate,
        country_of_origin=country_of_origin,
        warranty_months=warranty_months,
        weight=weight,
        tags=tags,
        created_by=actor,
        # Attribution: a router-supplied username wins (no DB hit); doors that
        # only carry the id (catalog promote / import) fall back to a one-shot,
        # fail-soft users lookup so EVERY create door stamps the name coherently.
        created_by_name=actor_name or resolve_actor_name(actor, db=db),
        as_draft=as_draft,
        force_draft=force_draft,
        provisional=provisional,
        extra_fields=extra_fields,
        product_repo=product_repo,
        db=db,
    )
    # Pre-assign the PIM link id so the spine + PIM doc share it from the start.
    spine["pim_product_id"] = str(uuid.uuid4())

    parent: Optional[Dict[str, Any]] = None
    if variant_of:
        parent = _resolve_variant_parent(variant_of, spine, product_repo)
    elif family_opt_out:
        spine["family_opt_out"] = True
    elif product_repo is not None:
        try:
            parent = find_family_lead(getattr(product_repo, "collection", None), spine)
        except Exception:  # noqa: BLE001 - an unread family never blocks a create
            logger.warning("[PM] family lookup failed for %s", spine.get("sku"), exc_info=True)
    if parent is not None:
        spine["variant_of"] = parent.get("product_id")
        if not variant_of or not live_lead_rows:
            # Model-level fields are the model's (owner 2026-10-18).
            lead_attrs = parent.get("attributes") or {}
            for key in ("shape", "gender"):
                if not _is_blank(lead_attrs.get(key)):
                    spine.setdefault("attributes", {})[key] = lead_attrs[key]
    # The row goes under the lead's listing only while that listing is not on
    # Shopify (or no one can check: then it waits too).
    row_parent = parent
    if parent is not None and not live_lead_rows and (db is None or has_listing(db, parent)):
        row_parent = None

    if product_repo is None:
        # No DB (local/dev): echo a synthetic shape; no mirror, no audit.
        spine.setdefault("product_id", str(uuid.uuid4()))
        spine["sync_status"] = _sync_status_dict(
            [_SyncTarget("catalog_products", "SKIPPED", "no db")]
        )
        return spine

    # --- STEP 1: spine FIRST + alone (single-document atomic create) ---
    created = persist_new_spine(spine, product_repo)
    product_id = created.get("product_id")

    # --- STEP 2: gated, best-effort mirror (never corrupts the spine) ---
    targets = _write_mirror(
        created,
        parent=parent,
        row_parent=row_parent,
        catalog_repo=catalog_repo,
        variant_repo=variant_repo,
        db=db,
    )

    # --- STEP 2b: ALWAYS stage the catalog_products DRAFT doc (linkage fix) ---
    # Makes the pre-assigned pim_product_id resolve to a real (DRAFT, not live)
    # catalog_products doc so the product can be pushed online later. Runs
    # regardless of the mirror flag; fail-soft; recorded under its own target
    # key so the flag-gated mirror targets above are unaffected.
    targets.append(
        _stage_catalog_draft(created, parent=parent, catalog_repo=catalog_repo, db=db)
    )
    if row_parent is not None and db is not None:
        # The new member's row (the flag-gated mirror above may have skipped
        # it), the lead's own row and every colour label of the family (a
        # clash with the new member changes both). Fail-soft: the link stands.
        try:
            _write_row(db, created, row_parent)
            _settle_lead(db, row_parent)
        except Exception:  # noqa: BLE001
            logger.warning("[PM] family options refresh failed for %s", product_id, exc_info=True)

    # --- STEP 3: record compensation/sync status back on the spine ---
    sync_status = _sync_status_dict(targets)
    try:
        product_repo.update(product_id, {"sync_status": sync_status})
        created["sync_status"] = sync_status
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PM] failed to record sync_status for %s: %s", product_id, exc)
        created["sync_status"] = sync_status

    # --- STEP 4: immutable audit (AuditRepository.create, NOT append_audit_entry) ---
    if audit_repo is not None:
        try:
            audit_repo.create(
                {
                    "action": "product.created",
                    "actor": actor,
                    "user_id": actor,
                    "entity_type": "product",
                    "entity_id": product_id,
                    "timestamp": datetime.now(),
                    "ts": utc_now_iso(),
                    "after": {
                        "product_id": product_id,
                        "sku": created.get("sku"),
                        "category": created.get("category"),
                        "mrp": created.get("mrp"),
                        "offer_price": created.get("offer_price"),
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[PM] audit write failed for %s: %s", product_id, exc)

    return created


def persist_new_spine(spine: Dict[str, Any], product_repo) -> Dict[str, Any]:
    """Persist a validated product with the shared identity and GTIN guards.

    Catalogue has its own twin shape and preassigned id, but its billing
    identity must obey the same create rule. A concurrent duplicate is a
    refusal naming the winner, never permission to save an orphan twin.
    Callers must build/validate the spine before entering this write gate.
    """
    conflict = identity_conflict(spine, product_repo)
    if conflict is not None:
        raise conflict
    assert_gtin_free((spine.get("attributes") or {}).get("gtin"), product_repo, None)
    try:
        created = product_repo.create(spine, raise_on_duplicate=True)
    except Exception as exc:  # noqa: BLE001
        if exc.__class__.__name__ == "DuplicateKeyError":
            winner = product_repo.find_by_sku(spine["sku"])
            if (
                winner is None
                and spine.get("identity_key")
                and hasattr(product_repo, "find_by_identity_key")
            ):
                winner = product_repo.find_by_identity_key(spine["identity_key"])
            raise _duplicate_error(winner or {"sku": spine.get("sku")}) from exc
        raise
    if not created:
        raise ProductMasterError("Failed to create product.", status=500)
    return created


def strict_find_many(product_repo, flt: Dict[str, Any]) -> List[Dict[str, Any]]:
    """find_many that RAISES on a read error. BaseRepository.find_many prints
    and returns [] -- for a duplicate check that is "nothing exists", a
    licence to create a twin. Reads the raw collection when there is one; a
    repo may offer its own `find_many_strict` (the PO's order-aware view)."""
    own = getattr(product_repo, "find_many_strict", None)
    if callable(own):
        return list(own(flt))
    coll = getattr(product_repo, "collection", None)
    if coll is not None and callable(getattr(coll, "find", None)):
        return list(coll.find(flt))
    if callable(getattr(product_repo, "find_many", None)):
        return list(product_repo.find_many(flt) or [])
    # A minimal store with no list read: an exact identity key it can answer.
    if set(flt) == {"identity_key"} and isinstance(flt["identity_key"], str):
        row = product_repo.find_by_identity_key(flt["identity_key"])
        return [row] if row else []
    return []


def _catalogue_unreadable() -> "ProductMasterError":
    err = ProductMasterError(
        "Could not check the catalogue for this item, so nothing was created. "
        "Try again.",
        status=503,
        field="lens_size",
    )
    err.code = "CATALOGUE_UNREADABLE"
    return err


def _keyed_before_eye_size(
    spine: Dict[str, Any], key: str, product_repo
) -> Optional[Dict[str, Any]]:
    """A frame catalogued before eye size joined the key (0cfb09d) still
    carries the 3-part brand|model|colour key until
    scripts/migrate_identity_key_tighten.py has re-keyed it. Typed WITH its
    eye size, the same frame keys 4-part and would miss it -- a silent twin.
    So an eye-size item also looks up its key without the size, and a row
    found there is the same product when ITS eye size (derived the one way,
    _derive_brand_model_color_size) is the one typed. A row there with no eye
    size, or another one, is a different item."""
    if _size_attribute_key(spine.get("category")) != "lens_size" or key.count("|") < 3:
        return None
    sizeless, typed_size = key.rsplit("|", 1)
    # Strict: a read error raises (identity_conflict answers 503), never "no row".
    # A store that offers no list read at all (a minimal stub) is asked by key.
    for row in strict_find_many(product_repo, {"identity_key": sizeless}):
        if row.get("identity_key") != sizeless:
            continue
        if _row_eye_size(row) == typed_size:
            return row
    return None


def _row_eye_size(row: Dict[str, Any]) -> str:
    """A stored row's EYE size, normalised: the derived size, else a legacy
    top-level `size`, read by eye_size_of. A row keyed before eye size joined
    the key that HAS one is a sized item: the migration re-keys it with it.
    One whose size is no eye size ("M") is not -- Save never asks for a size
    the eye-size box cannot take, and a sizeless line matches it by its key."""
    attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    derived = _derive_brand_model_color_size(attrs, row.get("category"))["size"] or row.get("size")
    return normalise_identity_component(eye_size_of(derived))


def identity_conflict(
    spine: Dict[str, Any], product_repo, self_id: Optional[str] = None
) -> Optional[ProductMasterError]:
    """THE one "we already have this" rule. The create door's guard (above),
    the PO's typed-line check (purchase_orders) and an edit's re-key
    (rekey_on_edit, `self_id` the product being edited, which is never its
    own twin) all run it, so no two doors answer the same item differently.

    A product that exists by SKU, brand+model+colour(+size) identity or barcode
    -> the 409 naming it. An eye-size category item WITHOUT its eye size whose
    brand/model/colour is catalogued BY eye size (each eye size is its own item,
    owner 09-28) -> 422 EYE_SIZE_NEEDED naming the sizes: it cannot be told
    apart from them, so it is never created as a sizeless twin."""
    def _other(row):
        return None if row is not None and self_id and row.get("product_id") == self_id else row

    # A blank SKU names no product (find_one({"sku": None}) would match any row
    # without one).
    existing = _other(product_repo.find_by_sku(spine["sku"])) if spine.get("sku") else None
    key = spine.get("identity_key")
    eye_item = _size_attribute_key(spine.get("category")) == "lens_size"
    sizeless = not normalise_identity_component(spine.get("size"))
    legacy_sized: List[Dict[str, Any]] = []
    if existing is None and key and hasattr(product_repo, "find_by_identity_key"):
        existing = _other(product_repo.find_by_identity_key(key))
        # A frame keyed before eye size joined the key still carries the 3-part
        # key a SIZELESS typed line makes -- but it has an eye size, so it is a
        # sized item (the migration re-keys it so): EYE_SIZE_NEEDED below,
        # never "use it?" (R2-15). One answer before and after the migration.
        # (A discarded draft there stays the match: its key is the typed one,
        # and a second row on one key is never made.)
        if (
            existing is not None
            and eye_item
            and sizeless
            and _row_eye_size(existing)
            and not revivable_discarded_draft(existing)
        ):
            legacy_sized, existing = [existing], None
    if existing is None and key and hasattr(product_repo, "find_by_identity_key"):
        try:
            existing = _other(_keyed_before_eye_size(spine, key, product_repo))
        except Exception as exc:  # noqa: BLE001 - fail loud, never a silent twin
            logger.error("[PRODUCT_MASTER] legacy-key check could not read %s: %s", key, exc)
            return _catalogue_unreadable()
    if (
        existing is None
        and spine.get("barcode")
        and hasattr(product_repo, "find_by_barcode")
    ):
        try:
            existing = _other(product_repo.find_by_barcode(spine["barcode"]))
        except Exception:  # noqa: BLE001
            existing = None
    if existing is not None:
        return _duplicate_error(existing)
    if not key or not sizeless or not eye_item:
        return None
    prefix = key + "|"
    rows: List[Dict[str, Any]] = []
    if hasattr(product_repo, "find_many"):
        try:
            rows = strict_find_many(
                product_repo, {"identity_key": {"$regex": "^" + re.escape(prefix)}}
            )
        except Exception as exc:  # noqa: BLE001
            # Fail loud: a swallowed read would answer "no sized rows" and let a
            # sizeless twin through.
            logger.error("[PRODUCT_MASTER] eye-size check could not read %s*: %s", prefix, exc)
            return _catalogue_unreadable()
    # A discarded draft is not in the catalogue: it never names an eye size
    # the catalogue "has" (R1-98).
    sized = legacy_sized + [
        p
        for p in rows
        if str(p.get("identity_key") or "").startswith(prefix)
        and not revivable_discarded_draft(p)
        and _other(p) is not None
    ]
    if not sized:
        return None
    # The eye sizes Add product can type (eye_size_of), never a legacy text.
    sizes = sorted({_row_eye_size(p) or str(existing_product_summary(p).get("size")) for p in sized})
    name = " ".join(
        str(v) for v in (spine.get("brand"), spine.get("model"), spine.get("color")) if v
    )
    err = ProductMasterError(
        f"{name} is in the catalogue by eye size ({', '.join(sizes)}). Type the "
        "eye size, or pick the item from the catalogue.",
        status=422,
        field="lens_size",
    )
    err.code = "EYE_SIZE_NEEDED"
    err.sizes = sizes
    return err


def revivable_discarded_draft(spine: Optional[Dict[str, Any]], category: Any = None) -> bool:
    """A draft an admin discarded (catalog DELETE's `discarded_draft` mark)
    that is STILL a discarded draft: switched off and unfinished
    (catalog_status DRAFT). A row finished or switched on since is an ordinary
    product, never quietly turned back into a draft. With `category`, the typed
    item must be the same kind of product (a sunglass typed against a
    discarded frame is not the same item -- tax and fields differ)."""
    spine = spine or {}
    if not (
        spine.get("discarded_draft")
        and spine.get("is_active") is False
        and str(spine.get("catalog_status") or "").upper() == "DRAFT"
    ):
        return False
    if category is None:
        return True
    return str(spine.get("category") or "").upper() == str(category or "").upper()


def revive_discarded_draft(
    product_id: str,
    product_repo,
    db=None,
    actor: Optional[str] = None,
    mrp: Any = None,
    audit_repo=None,
    po_number: Optional[str] = None,
) -> bool:
    """A discarded draft (revivable_discarded_draft) that a manager orders
    AGAIN by typing it in comes back as the ordered draft it was: provisional
    on the spine, in Needs review on its catalogue copy, never switched on, at
    the MRP the manager typed this time. Its identity key is unique, so a
    second row for the item can never be made -- without this the discarded
    row would block every new order of it behind a product nobody can see.

    Called AFTER the order (or the draft edit) that names it is saved
    (purchase_orders.create_typed_in_products), so a refused order never
    undoes an admin's discard. The catalogue copy is written
    first: a failure there leaves the spine still discarded (the order's send
    then refuses the line, loudly), never a provisional spine whose copy is
    missing from Needs review. True when revived; False (nothing written) for
    any other product."""
    if product_repo is None or not product_id:
        return False
    spine = product_repo.find_by_id(product_id)
    if not revivable_discarded_draft(spine):
        return False
    now = utc_now_iso()
    price: Dict[str, Any] = {}
    if mrp not in (None, "") and mrp != spine.get("mrp"):
        price = {"mrp": mrp}
    # `db is not None`, never a truth test of an attribute: on a pymongo
    # Database any attribute is a Collection, and bool() of one raises
    # (the same trap services/online_delist._raw_db works around).
    if db is not None:
        cat = db.get_collection("catalog_products")
        twin_id = spine.get("pim_product_id") or product_id
        # Back as _build_pim_doc made it: in Needs review, naming its
        # spine, with no projected is_active and no delete stamp.
        cat.update_one(
            {"id": twin_id},
            {
                "$set": {
                    "needs_review": True,
                    "spine_product_id": product_id,
                    **price,
                    **({"pricing.mrp": price["mrp"]} if price else {}),
                },
                "$unset": {"is_active": "", "deleted_at": "", "deleted_by": ""},
            },
        )
    product_repo.update(
        product_id,
        {
            "provisional": True,
            "is_active": False,
            # No longer deleted: a revived draft is a new buy again, not a
            # discontinued product (reorder_policy.discontinued reads this).
            "deleted_at": None,
            "discarded_draft": False,
            "revived_at": now,
            "revived_by": actor,
            **price,
        },
    )
    logger.info(
        "[PRODUCT_MASTER] discarded draft %s ordered again by %s: back in Needs review",
        product_id,
        actor,
    )
    if audit_repo is not None:
        try:
            audit_repo.create(
                {
                    "action": "product.discarded_draft_revived",
                    "entity_type": "product",
                    "entity_id": product_id,
                    "user_id": actor,
                    "detail": {
                        "sku": spine.get("sku"),
                        "po_number": po_number,
                        "discarded_at": spine.get("discarded_at"),
                        **({"mrp_was": spine.get("mrp"), "mrp": price["mrp"]} if price else {}),
                    },
                }
            )
        except Exception:  # noqa: BLE001 - the audit never undoes the revive
            logger.warning("[PRODUCT_MASTER] revive audit failed for %s", product_id, exc_info=True)
    return True


def _resolve_variant_parent(
    variant_of: str, spine: Dict[str, Any], product_repo
) -> Dict[str, Any]:
    """The parent SPINE a variant_of create points at, or a 422. Needs the
    repo (no unvalidated link is ever stamped): parent by product_id, same
    category, and never a chain (a parent is not itself a variant)."""
    if product_repo is None:
        raise ProductMasterError(
            "variant_of needs a product store to resolve the parent.",
            status=503,
            field="variant_of",
        )
    parent = product_repo.find_by_id(str(variant_of).strip())
    if parent is None:
        raise ProductMasterError(
            f"variant_of: no product with product_id '{variant_of}'.",
            status=422,
            field="variant_of",
        )
    if resolve_category(parent.get("category")) != spine.get("category"):
        raise ProductMasterError(
            "variant_of: the parent must be in the same category "
            f"('{parent.get('category')}' vs '{spine.get('category')}').",
            status=422,
            field="variant_of",
        )
    if parent.get("variant_of"):
        raise ProductMasterError(
            "variant_of: the parent is itself a variant -- link to its parent "
            f"('{parent.get('variant_of')}') instead; no chains.",
            status=422,
            field="variant_of",
        )
    if _model_identity(parent) != _model_identity(spine):
        raise ProductMasterError(
            "variant_of: a variant is the same model in another colour or size -- "
            "the parent must have the same brand and model number.",
            status=422,
            field="variant_of",
        )
    return parent


# ===========================================================================
# MODEL FAMILIES (owner 2026-10-18, G1): one model -> its colours and sizes
# ===========================================================================
# Same model number in other colours / eye sizes = VARIANTS of one product.
# Each variant keeps its own spine (SKU, barcode, price, stock, GST); the
# family is ONE link: spine `variant_of` = the LEAD's product_id (the first
# product catalogued for the model: earliest created_at, tie -> SKU), mirrored
# as the twin's ecom.variant_of and the catalog_variants row's
# parent_product_id. Every door goes through link_to_family /
# unlink_from_family / promote_new_lead below.

# THE model-level fields: shared by every colour/size of a model, filled from
# the lead and edited only on the model. EVERYTHING ELSE in the registry is
# per variant (owner correction 2026-10-18: materials, polarization, tint and
# lens colour can differ inside a model). all_category_specs hands each field
# its level, so the Add Product form reads this one list.
MODEL_LEVEL_FIELDS: Tuple[str, ...] = (
    "brand_name",
    "model_no",
    "model_name",
    "shape",
    "gender",
)


def field_level(key: Any) -> str:
    """'model' for a MODEL_LEVEL_FIELDS key, else 'variant'."""
    return "model" if key in MODEL_LEVEL_FIELDS else "variant"


def is_family_category(category: Any) -> bool:
    """A category whose same-model items are colours/sizes of ONE product:
    the ones whose registry requires a colour code (frames, sunglasses,
    smart glasses, readers, watches, clocks). An optical lens's 'model' is
    its sub-brand and a contact lens differs by power -- neither is one
    product in many colours."""
    spec = category_spec(category)
    return spec is not None and "colour_code" in spec.required


def _model_identity(doc: Dict[str, Any]) -> Tuple[Any, str, str]:
    """(category, brand, model) as the identity key reads them -- the rule an
    explicit variant_of (the size-variant runbook's door) is checked by."""
    category = resolve_category((doc or {}).get("category"))
    ids = _derive_brand_model_color_size((doc or {}).get("attributes") or {}, category)
    return (
        category,
        normalise_identity_component(ids["brand"] or doc.get("brand")),
        normalise_identity_component(ids["model"] or doc.get("model")),
    )


def family_key(doc: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
    """(category, brand, model number) normalised by THE identity normaliser,
    or None for a product that cannot be in a family. The model is the MODEL
    NUMBER only (a blank model_no is no family -- a smart glass named only by
    its model name is not grouped), with a brand typed in front of it folded
    away, separator or not: 'CARRERA 1001/S', 'CARRERA1001/S' and '1001/S'
    under Carrera are one model. Stored values are never rewritten."""
    category = resolve_category((doc or {}).get("category"))
    if not is_family_category(category):
        return None
    attrs = _shown_attributes((doc or {}).get("attributes") or {}, category)
    brand = normalise_identity_component(attrs.get("brand_name") or attrs.get("brand") or doc.get("brand"))
    model = _without_brand(brand, attrs.get("model_no"))
    return (category, brand, model) if brand and model else None


_SEPARATOR = r"[^a-z0-9]"
# What a model number looks like right after a brand typed without a gap:
# a digit, or one or two letters then a digit ('CARRERA1001/S' -> 1001S,
# 'RAYBANRB2140' -> RB2140). ponytail: 'TITANIUM12' under Titan stays
# 'TITANIUM12' (three letters); widen only for a real model the rule misses.
_MODEL_AFTER_BRAND = re.compile(r"^(?:\d|[a-z]{1,2}\d)")


def _without_brand(brand: str, raw_model: Any) -> str:
    """The normalised model number with a leading copy of the brand folded
    away: always when a separator follows the brand ('CARRERA 1001/S'),
    without one only before a model-number shape (_MODEL_AFTER_BRAND)."""
    model = normalise_identity_component(raw_model)
    if not brand or not model.startswith(brand) or len(model) == len(brand):
        return model
    rest = model[len(brand):]
    typed_apart = re.match(
        r"^\W*" + (_SEPARATOR + "*").join(map(re.escape, brand)) + _SEPARATOR + "+",
        str(raw_model or "").strip().lower(),
    )
    return rest if typed_apart or _MODEL_AFTER_BRAND.match(rest) else model


def _brand_rows_query(category: Any, brand: Any) -> Optional[Dict[str, Any]]:
    """Every row of one brand in one category (the identity key's "brand|"
    prefix): the candidates family_key narrows to one model. A model typed
    with the brand in front keys apart, so the model-prefix scan
    (model_family_query) cannot find it."""
    canonical = resolve_category(category)
    b = normalise_identity_component(brand)
    if canonical is None or not b:
        return None
    return {"category": canonical, "identity_key": {"$regex": "^" + re.escape(f"{b}|")}}


def _created_sort_value(value: Any) -> float:
    """created_at as a sortable number (naive = UTC; missing/unreadable last)."""
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    if isinstance(value, str) and value.strip():
        try:
            return _created_sort_value(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
        except ValueError:
            pass
    return float("inf")


def lead_order(doc: Dict[str, Any]) -> Tuple[float, str]:
    """THE lead rule: earliest created_at, tie -> SKU."""
    return (_created_sort_value(doc.get("created_at")), str(doc.get("sku") or ""))


def _is_archived(doc: Dict[str, Any]) -> bool:
    return bool((doc or {}).get("deleted_at"))


def lead_of(members: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The lead among one model's rows: the product the linked variants point
    at, else the first catalogued (lead_order) that is not archived, not a
    variant and not opted out ('This is a different model')."""
    by_id = {m.get("product_id"): m for m in members}
    pointed: Dict[str, int] = {}
    for m in members:
        if m.get("variant_of") and not _is_archived(m):
            pointed[m["variant_of"]] = pointed.get(m["variant_of"], 0) + 1
    for lead_id in sorted(pointed, key=lambda k: -pointed[k]):
        lead = by_id.get(lead_id)
        if lead is not None and not lead.get("variant_of"):
            return lead
    candidates = [
        m
        for m in members
        if not m.get("variant_of") and not m.get("family_opt_out") and not _is_archived(m)
    ]
    return min(candidates, key=lead_order) if candidates else None


def find_family_lead(
    products_collection, doc: Dict[str, Any], *, exclude_id: Any = None
) -> Optional[Dict[str, Any]]:
    """The lead of `doc`'s model (another product), or None. Reads the
    brand's rows (the identity-key prefix walk), then keeps the exact
    family_key (the key is the rule)."""
    key = family_key(doc)
    if key is None or products_collection is None:
        return None
    attrs = doc.get("attributes") or {}
    q = _brand_rows_query(key[0], attrs.get("brand_name") or attrs.get("brand") or doc.get("brand"))
    if q is None:
        return None
    members = [
        m
        for m in products_collection.find(q)
        if m.get("product_id") != exclude_id and family_key(m) == key
    ]
    return lead_of(members)


def variant_option_values(spine: Dict[str, Any]) -> Dict[str, str]:
    """The colour and size a variant is chosen by: option_color = colour name
    + code ('Gold (001)'; either alone when only one is known), option_size =
    its eye size (blank when the model does not vary by size)."""
    attrs = (spine or {}).get("attributes") or {}
    name = next(
        (
            str(attrs[k]).strip()
            for k in ("colour_name", "color_name", "frame_color", "dial_color")
            if str(attrs.get(k) or "").strip()
        ),
        "",
    )
    code = str(attrs.get("colour_code") or attrs.get("color_code") or "").strip()
    if name and code and name.lower() != code.lower():
        colour = f"{name} ({code})"
    else:
        colour = name or code
    size = _derive_brand_model_color_size(attrs, spine.get("category"))["size"] or spine.get("size")
    return {"option_color": colour, "option_size": str(size or "").strip()}


def _lens_label(spine: Dict[str, Any]) -> str:
    """What tells two variants of one frame colour apart: lens colour, tint,
    polarized (owner 2026-10-18: 'Gold / Green Polarized')."""
    attrs = (spine or {}).get("attributes") or {}
    bits = []
    for k in ("lens_colour", "tint"):
        v = str(attrs.get(k) or "").strip()
        if v and v.lower() not in (b.lower() for b in bits):
            bits.append(v)
    pol = str(attrs.get("polarization") or "").strip().lower()
    if pol in ("yes", "true", "polarized", "polarised"):
        bits.append("Polarized")
    return " ".join(bits)


def family_option_values(members: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, str]], List[List[str]]]:
    """Option values for every member of one family, by SKU. Two variants with
    the same colour and size get their lens label appended to the colour;
    pairs still equal after that are returned (as SKU lists) for a person."""
    values = {m.get("sku"): variant_option_values(m) for m in members if m.get("sku")}

    def clashes() -> List[List[str]]:
        seen: Dict[Tuple[str, str], List[str]] = {}
        for sku, v in values.items():
            seen.setdefault((v["option_color"].lower(), v["option_size"].lower()), []).append(sku)
        return [skus for skus in seen.values() if len(skus) > 1]

    by_sku = {m.get("sku"): m for m in members}
    for skus in clashes():
        for sku in skus:
            lens = _lens_label(by_sku[sku])
            if lens:
                base = values[sku]["option_color"]
                values[sku]["option_color"] = f"{base} / {lens}" if base else lens
    return values, clashes()


def family_members(db, lead_id: str) -> List[Dict[str, Any]]:
    """The lead and every product linked to it (archived ones too)."""
    products = db.get_collection("products")
    lead = products.find_one({"product_id": lead_id})
    rows = list(products.find({"variant_of": lead_id}))
    return ([lead] if lead else []) + rows


def _twin_id(spine: Dict[str, Any]) -> Any:
    return spine.get("pim_product_id") or spine.get("product_id")


def _variant_repo(db):
    from database.repositories.catalog_variant_repository import CatalogVariantRepository

    return CatalogVariantRepository(db.get_collection("catalog_variants"))


# What belongs to the LISTING a row sat on, dropped when the row moves to
# another one: the online discount engine's per-row prices (re-derived on the
# new listing's next pass; the push falls back to row mrp / the product's own
# price meanwhile) and the old listing's Shopify variant ids.
_ENGINE_ROW_FIELDS = (
    "discounted_price",
    "compare_at_price",
    "shopify_variant_id",
    "shopify_inventory_item_id",
)


def has_listing(db, spine: Dict[str, Any]) -> bool:
    """Is this product's twin on Shopify (it carries a product gid)? The twin
    by the take-down's own resolver (online_delist._resolve_twin), read
    STRICTLY: a catalogue that cannot be read counts as on Shopify -- nothing
    is re-pointed under a listing nobody could check (fail closed)."""
    from .online_delist import _raw_db, _resolve_twin

    try:
        twin = _resolve_twin(_raw_db(db), spine or {}, strict=True)
    except Exception:  # noqa: BLE001
        return True
    return bool(((twin or {}).get("ecom") or {}).get("shopify_product_id"))


_OPTION_FIELDS = ("option_color", "option_size")


def _write_row(db, spine: Dict[str, Any], parent: Optional[Dict[str, Any]]) -> None:
    """The member's catalog_variants row for its place in the family:
    _variant_row_for's shape, engine prices dropped. A row of its own keeps
    no mrp copy (its price is the product itself: one holder) and no options
    until refresh_family_options gives a LEAD its colour/size."""
    if not spine.get("sku"):
        return
    _variant_repo(db).upsert(_variant_row_for(spine, parent))
    unset = {f: "" for f in _ENGINE_ROW_FIELDS}
    if parent is None:
        unset["mrp"] = ""
        unset.update({f: "" for f in _OPTION_FIELDS})
    db.get_collection("catalog_variants").update_one({"sku": spine["sku"]}, {"$unset": unset})


def _own_row(db, spine: Dict[str, Any]) -> None:
    """The product's row as a product of its own: written when missing,
    re-pointed (and cleaned, _write_row) only when it sat under another
    listing -- never rewritten in place, so a lead's own engine prices and
    Shopify ids are never touched."""
    if not spine.get("sku"):
        return
    row = db.get_collection("catalog_variants").find_one({"sku": spine["sku"]})
    if row is None or row.get("parent_product_id") != _twin_id(spine):
        _write_row(db, spine, None)


def refresh_family_options(db, lead_id: str) -> List[List[str]]:
    """Re-derive option_color / option_size on every row of the family (a
    colour clash between two members changes BOTH labels). Returns the SKU
    groups whose options are still identical. A model whose lead is on
    Shopify is left exactly as its listing has it (G2 changes live options)."""
    members = family_members(db, lead_id)
    if members and members[0].get("product_id") == lead_id and has_listing(db, members[0]):
        return []
    variants = db.get_collection("catalog_variants")
    if len(members) < 2:
        # No model any more (the last variant left): a product of its own,
        # whose one-item listing carries no options.
        for m in members:
            if m.get("sku"):
                variants.update_one({"sku": m["sku"]}, {"$unset": {f: "" for f in _OPTION_FIELDS}})
        return []
    values, clashes = family_option_values(members)
    for sku, v in values.items():
        variants.update_one({"sku": sku}, {"$set": v})
    return clashes


def _family_audit(audit_repo, action: str, product_id: Any, actor: Any, detail: Dict[str, Any]) -> None:
    if audit_repo is None:
        return
    try:
        audit_repo.create(
            {
                "action": action,
                "entity_type": "product",
                "entity_id": product_id,
                "user_id": actor,
                "ts": utc_now_iso(),
                "detail": detail,
            }
        )
    except Exception:  # noqa: BLE001 - the audit never undoes the link
        logger.warning("[PM] %s audit failed for %s", action, product_id, exc_info=True)


def _set_twin_link(db, spine: Dict[str, Any], parent: Optional[Dict[str, Any]]) -> None:
    """The twin's half of the link: ecom.variant_of naming the lead in every
    id space (as _build_pim_doc writes it), never queued -- a variant owns no
    listing; unlinked, it is its own (unpublished) listing, queued."""
    cat = db.get_collection("catalog_products")
    if parent is not None:
        cat.update_one(
            {"id": _twin_id(spine)},
            {
                "$set": {
                    "ecom.variant_of": {
                        "product_id": parent.get("product_id"),
                        "twin_id": _twin_id(parent),
                        "sku": parent.get("sku"),
                    },
                    "ecom.locally_modified": False,
                }
            },
        )
    else:
        cat.update_one(
            {"id": _twin_id(spine)},
            {"$unset": {"ecom.variant_of": ""}, "$set": {"ecom.locally_modified": True}},
        )


def _link_one(db, child: Dict[str, Any], parent: Dict[str, Any]) -> bool:
    """Write one link: spine and twin always; the row under the lead's
    listing only while the lead is NOT on Shopify. Under a live listing the
    child keeps its own row and waits for the website step (G2): a row
    re-pointed there is paired onto the listing's variants by its next push.
    Returns whether the row moved. No other checks."""
    db.get_collection("products").update_one(
        {"product_id": child.get("product_id")},
        {"$set": {"variant_of": parent.get("product_id")}, "$unset": {"family_opt_out": ""}},
    )
    _set_twin_link(db, child, parent)
    if has_listing(db, parent):
        _own_row(db, child)
        return False
    _write_row(db, child, parent)
    return True


def _settle_lead(db, lead: Dict[str, Any]) -> List[List[str]]:
    """After a link: the lead's own row exists (never rewritten in place) and
    the model's colour/size labels are re-derived -- both skipped for a lead
    on Shopify."""
    if has_listing(db, lead):
        return []
    _own_row(db, lead)
    return refresh_family_options(db, lead.get("product_id"))


def link_to_family(
    db,
    child_id: str,
    parent_id: str,
    *,
    actor: Any = None,
    audit_repo=None,
) -> Dict[str, Any]:
    """Make `child_id` a colour/size of the model led by `parent_id`. THE one
    link door (catalogue review's Link to model, the catalog create door, the
    migration). Refuses (ProductMasterError, nothing written): unknown ids,
    linking to itself, another category or model number, a parent that is
    itself a variant, a child that leads variants of its own (no chains), and
    a child that is on the website as its own listing (moving a live listing
    under another one is the website step, G2). Re-linking an already-linked
    child is a no-op."""
    products = db.get_collection("products")
    child = products.find_one({"product_id": child_id})
    parent = products.find_one({"product_id": parent_id})
    if child is None or parent is None:
        raise ProductMasterError("Product not found.", status=404, field="variant_of")
    if _is_archived(child) or _is_archived(parent):
        raise ProductMasterError(
            "An archived product cannot be linked to a model. Restore it first.",
            status=422,
            field="variant_of",
        )
    if child_id == parent_id:
        raise ProductMasterError("A product cannot be a variant of itself.", status=422, field="variant_of")
    if family_key(child) is None or family_key(child) != family_key(parent):
        raise ProductMasterError(
            "Only the same model (same category, brand and model number) in another "
            "colour or size can be linked to it.",
            status=422,
            field="variant_of",
        )
    if parent.get("variant_of"):
        raise ProductMasterError(
            "That product is itself a colour/size of another model -- link to the "
            "model it belongs to.",
            status=422,
            field="variant_of",
        )
    if child.get("variant_of") == parent_id:
        return {"lead_id": parent_id, "product_id": child_id, "changed": False}
    if products.find_one({"variant_of": child_id}) is not None:
        raise ProductMasterError(
            "This product is the model other colours/sizes are linked to. Unlink "
            "them first.",
            status=422,
            field="variant_of",
        )
    if has_listing(db, child):
        raise ProductMasterError(
            "This product is on the website as its own listing, so it cannot join "
            "a model yet (that comes with the website update).",
            status=409,
            field="variant_of",
        )
    row_moved = _link_one(db, child, parent)
    clashes = _settle_lead(db, parent)
    _family_audit(
        audit_repo,
        "product.linked_to_model",
        child_id,
        actor,
        {"sku": child.get("sku"), "lead_id": parent_id, "lead_sku": parent.get("sku"), "row_moved": row_moved},
    )
    return {
        "lead_id": parent_id,
        "product_id": child_id,
        "changed": True,
        "clashes": clashes,
        # False: the model is on the website; this colour/size joins its
        # listing with the website step (G2).
        "on_listing": row_moved,
    }


def unlink_from_family(
    db, child_id: str, *, actor: Any = None, audit_repo=None
) -> Dict[str, Any]:
    """Take a variant out of its model ('this is a different model'): it
    becomes its own product again and is marked family_opt_out so neither the
    create door nor the migration links it back. Refused while it is a variant
    on the model's live website listing (G2)."""
    products = db.get_collection("products")
    child = products.find_one({"product_id": child_id})
    if child is None:
        raise ProductMasterError("Product not found.", status=404, field="variant_of")
    lead_id = child.get("variant_of")
    if not lead_id:
        raise ProductMasterError("This product is not linked to a model.", status=422, field="variant_of")
    row = db.get_collection("catalog_variants").find_one({"sku": child.get("sku")}) or {}
    if row.get("shopify_variant_id"):
        raise ProductMasterError(
            "This colour/size is on the model's website listing, so it cannot be "
            "unlinked yet (that comes with the website update).",
            status=409,
            field="variant_of",
        )
    products.update_one(
        {"product_id": child_id},
        {"$unset": {"variant_of": ""}, "$set": {"family_opt_out": True}},
    )
    _set_twin_link(db, child, None)
    _own_row(db, child)
    refresh_family_options(db, lead_id)
    _family_audit(
        audit_repo, "product.unlinked_from_model", child_id, actor, {"sku": child.get("sku"), "lead_id": lead_id}
    )
    return {"lead_id": lead_id, "product_id": child_id, "changed": True}


def promote_new_lead(
    db, old_lead_id: str, *, actor: Any = None, audit_repo=None
) -> Optional[str]:
    """The lead was archived: the next first-catalogued live variant leads
    the model, and every other member (the archived old lead too) is linked to
    it. Returns the new lead's product_id, or None when nothing is linked to
    the old lead or every variant is archived (the family stays as it is).
    ponytail: IMS only -- a live listing owned by the old lead is moved by
    the website step (G2)."""
    products = db.get_collection("products")
    old = products.find_one({"product_id": old_lead_id})
    children = list(products.find({"variant_of": old_lead_id}))
    live = [c for c in children if not _is_archived(c)]
    if old is None or not live:
        return None
    new = min(live, key=lead_order)
    products.update_one({"product_id": new["product_id"]}, {"$unset": {"variant_of": ""}})
    new.pop("variant_of", None)
    _set_twin_link(db, new, None)
    _own_row(db, new)
    for member in [c for c in children if c.get("product_id") != new["product_id"]] + [old]:
        _link_one(db, member, new)
    _settle_lead(db, new)
    _family_audit(
        audit_repo,
        "product.model_lead_promoted",
        new["product_id"],
        actor,
        {"sku": new.get("sku"), "old_lead_id": old_lead_id, "old_lead_sku": old.get("sku")},
    )
    return new["product_id"]


def family_summary(db, product_id: str) -> Dict[str, Any]:
    """The drawer's view of a product's model: the lead, every member with
    its colour/size/price/state, and -- for a product in no family -- the
    lead it could be linked to (Link to model)."""
    products = db.get_collection("products")
    doc = products.find_one({"product_id": product_id})
    if doc is None:
        raise ProductMasterError("Product not found.", status=404)
    lead_id = doc.get("variant_of") or product_id
    members = family_members(db, lead_id)
    if len(members) < 2:
        members = [doc]
    values, _ = family_option_values(members)
    candidate = None
    if len(members) == 1:
        lead = find_family_lead(products, doc, exclude_id=product_id)
        if lead is not None:
            candidate = {"product_id": lead.get("product_id"), "sku": lead.get("sku"), "name": lead.get("name")}
    return {
        "product_id": product_id,
        "lead_id": lead_id if len(members) > 1 else None,
        "family_category": is_family_category(doc.get("category")),
        "opted_out": bool(doc.get("family_opt_out")),
        "candidate_lead": candidate,
        "members": [
            {
                "product_id": m.get("product_id"),
                "sku": m.get("sku"),
                "name": m.get("name"),
                "is_lead": m.get("product_id") == lead_id,
                "color": values.get(m.get("sku"), {}).get("option_color", ""),
                "size": values.get(m.get("sku"), {}).get("option_size", ""),
                "mrp": m.get("mrp"),
                "is_active": m.get("is_active", True) is not False,
                "archived": _is_archived(m),
            }
            for m in sorted(members, key=lead_order)
        ],
    }


def assert_size_row(sku: Any, db) -> None:
    """A size variant's barcode reaches the website only on its
    catalog_variants row. With no row for its sku the barcode can never land
    on every copy and saving again cannot help, so both barcode doors (Manage
    Barcode, the catalogue review editor) call this BEFORE their first write:
    422 with nothing saved, so the billing product, the catalogue copy and the
    row never disagree. A failed read is a 503, nothing saved either."""
    try:
        found = bool(sku) and db.get_collection("catalog_variants").find_one(
            {"sku": sku}, {"_id": 1}
        ) is not None
    except Exception as exc:  # noqa: BLE001
        raise ProductMasterError(
            "Could not check this size's website row, so nothing was saved. "
            "Please save again.",
            status=503,
            field="gtin",
        ) from exc
    if not found:
        raise ProductMasterError(
            f"This size ({sku or 'no SKU'}) has no row on its product's website "
            "listing, so its barcode cannot be sent there. Nothing was saved. "
            "Saving again will not help until the size is back on the listing.",
            status=422,
            field="gtin",
        )


def _mirror_variant_row_update(
    *, current: Dict[str, Any], patch: Dict[str, Any], db, mark_dirty: bool = True,
    write_token=None,
) -> bool:
    """The SIZE-VARIANT half of the spine-edit mirror: a child's mrp / gtin
    live on its catalog_variants row (the price push reads the ROW, never the
    child twin), and the listing that must carry them is the PARENT's.

    So: $set the row (the discount engine's _persist_variant idiom, keyed on
    the unique sku), let the engine -- the owner of discounted_price /
    compare_at_price -- recompute the parent so a stale discounted_price
    never ships the OLD price with the NEW mrp as compare-at, then queue the
    PARENT twin. The child twin is never queued. ``mark_dirty=False`` (the
    #1137 opt-out: a write that only makes IMS agree with Shopify) still
    mirrors the row but skips the engine AND the queue -- the engine queues
    the parent itself, so it waits for its next product-level pass. False
    when NO row has that sku (nothing written): a barcode door answers that
    as a failed size write. True otherwise (written, or nothing to write)."""
    if "mrp" not in patch and "gtin" not in (patch.get("attributes") or {}):
        return True
    sku = current.get("sku")
    if not sku:
        return False
    row_patch: Dict[str, Any] = {}
    if "mrp" in patch:
        row_patch["mrp"] = patch["mrp"]
    if "gtin" in (patch.get("attributes") or {}):
        row_patch.update(twin_barcode_fields(patch["attributes"].get("gtin")))
        row_patch["_gtin_write_token"] = write_token or uuid.uuid4().hex
    variants = db.get_collection("catalog_variants")
    res = variants.update_one({"sku": sku}, {"$set": row_patch})
    if not getattr(res, "matched_count", 1):
        return False
    if not mark_dirty:
        return True
    row = variants.find_one({"sku": sku}) or {}
    parent_twin_id = row.get("parent_product_id")
    if not parent_twin_id:
        return True
    cat = db.get_collection("catalog_products")
    parent_twin = cat.find_one({"id": parent_twin_id})
    if parent_twin is None:
        return True
    if "mrp" in row_patch:
        try:
            from .online_discount_engine import recompute_online_price

            # The engine subscripts db[...] (fail-soft: a wrapper without
            # __getitem__ makes every load/persist a silent no-op). Both real
            # doors pass the DatabaseConnection wrapper; hand it the raw db.
            raw_db = db if hasattr(type(db), "__getitem__") else getattr(db, "db", db)
            recompute_online_price(parent_twin, db=raw_db)
        except Exception:  # noqa: BLE001 -- the engine is fail-soft by contract
            logger.warning("[PM] online price recompute skipped for %s", sku, exc_info=True)
    cat.update_one(
        {"id": parent_twin_id},
        {"$set": {f"ecom.{k}": v for k, v in dirty_marks().items()}},
    )
    return True


def mirror_update_to_catalog_twin(
    *,
    product_id: str,
    current: Dict[str, Any],
    patch: Dict[str, Any],
    db=None,
    mark_dirty: bool = True,
) -> bool:
    """THE spine-edit -> catalog-twin mirror rule (products convergence).

    ``mark_dirty=False`` is the explicit opt-out (the catalog door's
    _save_catalog_product idiom): the fields still mirror, but the twin is
    NOT queued for the Online Store push -- for a write that only makes IMS
    agree with what Shopify already shows (scripts/adopt_shopify_media_map.py
    --replace-photos-from-shopify), where a push would change nothing.

    This rule used to live TWICE -- inline in the PUT /products route AND
    inside update_product below -- and the two copies drifted (PR #1029
    follow-up: different field sets, different price spellings, a
    description-only edit queued on one door and not the other). Both doors
    now call HERE; do not re-inline a copy at a call site.

    The one behaviour (decided per disagreement, differential probe 2026-08-30):

      * mrp / offer_price are written in BOTH spellings -- top-level AND
        pricing.* -- because the Shopify push resolves the TOP-LEVEL value
        first (shopify_push._resolve_variant_pricing), so a PM-born twin with
        a stale top-level price would ship the OLD price if only pricing.*
        moved; catalog-door twins and the PIM screens read the nested shape.
      * cost_price / discount_category (uppercased) go to pricing.* only --
        that is the only spelling any twin carries or any reader consults.
      * hsn_code / gst_rate / is_active / description / brand / model /
        category / attributes mirror top-level; tags mirror to ecom.seo.tags
        (the only spelling the push reads); an attributes.gtin projects to the
        twin's top-level gtin (twin_barcode_fields).
      * the twin QUEUES for the manual Online Store push
        (ecom.locally_modified) only when a field the storefront actually
        shows moved: mrp / offer_price (the variant price fallbacks),
        description (descriptionHtml), images (the photograph the push
        attaches and the publish gate reads), brand (vendor + brand_ tag),
        category (productType), attributes (ims.* metafields + filter tags)
        or tags, or when the re-derived display name (pim_display_name --
        the Shopify title) differs from the twin's. A description-only edit
        DOES queue -- the pending count must be truthful about copy changes.
        cost / tier / hsn / gst / is_active / model are in NO pushed payload:
        queuing on them would send a push that changes nothing on Shopify.
      * twin key: current.pim_product_id first (door-created products key the
        twin on that separate uuid), spine product_id as the legacy /
        convergence fallback.
      * a MISSING twin is a NO-OP -- never an upsert. A twin is born only at
        the create door; upserting here would mint a fragment doc with no
        sku/name/ecom identity, invisible to every sku-joined consumer.
      * UNCONDITIONAL (no mirror_enabled() gate): a local Mongo twin write is
        not an external write -- the flag guards the external Postgres/Shopify
        legs, exactly the _stage_catalog_draft precedent.
      * a queued row must belong to a status bucket: ecom.status defaults to
        DRAFT only when ABSENT, so an edit can never demote a live product.

    Fail-soft: never raises; a mirror error is logged and the spine save
    stands -- False then (True = written, or nothing to write), so a door
    whose field must land on both copies (Manage Barcode's gtin) can say it
    did not. Queuing is not publishing: a human still presses the push button.
    """
    if "attributes" in (patch or {}):
        _refresh_family_of(db, current)
    try:
        if db is None or not getattr(db, "is_connected", True):
            return True
        cat = db.get_collection("catalog_products")
        if cat is None:
            return True
        cat_patch: Dict[str, Any] = {}
        for key in ("mrp", "offer_price"):
            if key in patch:
                cat_patch[key] = patch[key]
                cat_patch[f"pricing.{key}"] = patch[key]
        if "cost_price" in patch:
            cat_patch["pricing.cost_price"] = patch["cost_price"]
        if "discount_category" in patch:
            dc = patch["discount_category"]
            cat_patch["pricing.discount_category"] = (
                dc.upper() if isinstance(dc, str) else dc
            )
        # Every field the spine edit can move that the push reads off the TWIN:
        # brand -> vendor + the brand_ filter tag, category -> productType,
        # attributes -> the ims.* metafields + the attribute filter tags, and
        # tags -> ecom.seo.tags (build_product_input reads ONLY that spelling).
        # These used to stop at the spine, so the twin -- and therefore Shopify
        # -- went stale after an edit (prod 2026-09-06: one twin had already
        # drifted from its spine on attributes). model / hsn / gst / is_active
        # mirror for the PIM screens but queue nothing: no pushed payload
        # reads them (a name that moves arrives as `name`, below).
        for key in (
            "hsn_code", "gst_rate", "is_active", "description",
            "brand", "model", "category", "attributes",
        ):
            if key in patch:
                cat_patch[key] = patch[key]
        if patch.get("provisional") is False:
            # A finished provisional draft leaves the Needs-review queue it
            # entered at the PO door (_build_pim_doc).
            cat_patch["needs_review"] = False
        elif not (current or {}).get("provisional") and patch.get("provisional") is not True:
            # A spine that is no longer an ordered draft (finished by another
            # door -- the PO/GRN cost promote -- or before this rule) never
            # leaves its copy in Needs review as "Ordered - finish it": any
            # later save heals it. Only the ordered-draft mark (spine_product_id)
            # is touched; an import awaiting review keeps its flag.
            cat.update_one(
                {"spine_product_id": product_id, "needs_review": True},
                {"$set": {"needs_review": False}},
            )
        if "tags" in patch:
            # The dot-path form of set_twin_tags: same field, same normaliser.
            cat_patch["ecom.seo.tags"] = normalise_tags(patch["tags"])
        if "gtin" in (patch.get("attributes") or {}):
            # Same projection as _build_pim_doc (twin_barcode_fields).
            cat_patch.update(twin_barcode_fields(patch["attributes"].get("gtin")))
        # The photograph moves with the spine and QUEUES: it is the one field
        # the storefront shows more prominently than the price, and the push
        # reads it off the twin (see _build_pim_doc).
        if "images" in patch:
            cat_patch["images"] = _spine_images({"images": patch["images"]})
        if mark_dirty and any(
            k in patch
            for k in (
                "mrp", "offer_price", "description", "images",
                "brand", "category", "attributes", "tags",
            )
        ):
            cat_patch.update({f"ecom.{k}": v for k, v in dirty_marks().items()})
        twin_id = current.get("pim_product_id") or product_id
        # THE NAME (sync audit gap #3, owner 2026-09-06: "renaming updates
        # Shopify"). The twin's name/title is what build_product_input sends
        # as the Shopify title, and no edit ever recomputed it. On an explicit
        # rename re-derive it with the SAME formula the create door uses
        # (pim_display_name) and queue the twin only when the title actually
        # changed. A rename, or an identity edit that re-mints a minted name
        # (rekey_on_edit: a lens's new power); product_naming honours a set
        # name verbatim, so nothing else can move the title.
        twin: Optional[Dict[str, Any]] = None
        if "name" in patch:
            twin = cat.find_one({"id": twin_id}) or {}
            new_name = pim_display_name({**current, **patch})
            # Compare against what Shopify actually shows: build_product_input
            # reads the twin's `title` first, then `name`.
            if new_name and new_name != (twin.get("title") or twin.get("name")):
                cat_patch["name"] = new_name
                cat_patch["title"] = new_name
                if mark_dirty:
                    cat_patch.update({f"ecom.{k}": v for k, v in dirty_marks().items()})
        row_ok = True
        if current.get("variant_of"):
            # A SIZE VARIANT owns no listing: its twin is NEVER queued (a
            # rename never expects Shopify -- the title is the parent's), and
            # its price / barcode reach the storefront through its
            # catalog_variants row on the PARENT's next push. No row for its
            # sku is a failed mirror (False), as a refused row write is.
            cat_patch.pop("ecom.locally_modified", None)
            cat_patch.pop("ecom.edit_rev", None)
            row_ok = _mirror_variant_row_update(
                current=current, patch=patch, db=db, mark_dirty=mark_dirty
            )
        if not cat_patch:
            return row_ok
        if cat_patch.get("ecom.locally_modified"):
            if twin is None:
                twin = cat.find_one({"id": twin_id}) or {}
            if not (twin.get("ecom") or {}).get("status"):
                cat_patch["ecom.status"] = "DRAFT"
        cat.update_one({"id": twin_id}, {"$set": cat_patch})
        return row_ok
    except Exception:  # noqa: BLE001 -- the spine save must stand regardless
        logger.warning(
            "[PM] catalog twin mirror skipped for %s", product_id, exc_info=True
        )
        return False


def _refresh_family_of(db, spine: Dict[str, Any]) -> None:
    """A member's colour/size moved: its family's option labels follow.
    Fail-soft (the edit stands)."""
    try:
        if db is None or not getattr(db, "is_connected", True):
            return
        lead_id = spine.get("variant_of") or spine.get("product_id")
        products = db.get_collection("products")
        if spine.get("variant_of") or products.find_one({"variant_of": lead_id}):
            refresh_family_options(db, lead_id)
    except Exception:  # noqa: BLE001
        logger.warning("[PM] family options refresh skipped for %s", spine.get("sku"), exc_info=True)


def model_field_changes(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """The model-level attribute values `after` sets differently from
    `before` (a key `after` does not carry is not a change: a partial
    attributes patch, a barcode alone, never reads as clearing the model)."""
    b, a = before or {}, after or {}
    return {
        k: a.get(k)
        for k in MODEL_LEVEL_FIELDS
        # Canonical (the identity normaliser): 'rb4165' / 'RB 4165' and
        # 'RAY-BAN' / 'Ray-Ban' are the same value, never an edit.
        if k in a and normalise_identity_component(b.get(k)) != normalise_identity_component(a.get(k))
    }


def assert_variant_model_fields(
    existing: Dict[str, Any],
    new_attributes: Optional[Dict[str, Any]] = None,
    *,
    brand: Any = None,
    model: Any = None,
    category: Any = None,
) -> None:
    """THE guard every edit door calls (PUT /products, PUT /products/master
    via update_product, PUT /catalog/products): a variant's model-level
    fields belong to its model -- an edit that changes one on a variant (in
    the attributes, or the flat brand / model / category columns) is refused,
    naming where to make it. Values compare canonically."""
    if not existing.get("variant_of"):
        return
    stored = existing.get("attributes") or {}
    changed = model_field_changes(stored, new_attributes or {})
    canon = normalise_identity_component
    if brand is not None and canon(brand) != canon(stored.get("brand_name") or existing.get("brand")):
        changed["brand_name"] = brand
    if model is not None and canon(model) not in (canon(existing.get("model")), canon(stored.get("model_no"))):
        changed["model_no"] = model
    if category is not None and resolve_category(category) != resolve_category(existing.get("category")):
        changed["category"] = category
    if changed:
        key = next(iter(changed))
        raise ProductMasterError(
            f"{field_label(key)} is shared by every colour and size of this model. "
            "Change it on the model (Edit on the model).",
            status=422,
            field=key,
        )


def propagate_model_fields(
    *,
    lead: Dict[str, Any],
    changes: Dict[str, Any],
    actor: Any,
    actor_name: Optional[str] = None,
    product_repo=None,
    audit_repo=None,
    db=None,
) -> List[str]:
    """An edit to a model-level field on the LEAD reaches every variant
    (owner 2026-10-18), each through update_product so its key, name and twin
    follow by the one edit rule. Returns the SKUs that refused (e.g. a new
    model number another product already has); the rest are saved."""
    if not changes or product_repo is None or lead.get("variant_of"):
        return []
    failed: List[str] = []
    for child in strict_find_many(product_repo, {"variant_of": lead.get("product_id")}):
        attrs = {**(child.get("attributes") or {}), **changes}
        try:
            update_product(
                product_id=child["product_id"],
                patch={"attributes": attrs},
                actor=actor,
                actor_name=actor_name,
                product_repo=product_repo,
                audit_repo=audit_repo,
                db=db,
                from_model=True,
            )
        except ProductMasterError:
            logger.warning("[PM] model field did not reach %s", child.get("sku"), exc_info=True)
            failed.append(str(child.get("sku")))
    return failed


def update_product(
    *,
    product_id: str,
    patch: Dict[str, Any],
    actor: str,
    actor_name: Optional[str] = None,
    product_repo=None,
    audit_repo=None,
    db=None,
    mark_dirty: bool = True,
    from_model: bool = False,
) -> Dict[str, Any]:
    """Update a product's mutable fields. Enforces offer<=MRP in BOTH directions
    (raise offer above existing MRP, OR lower MRP below existing offer) by
    merging the patch against the current doc before evaluating.

    Mirrors the spine update to the catalog twin via
    mirror_update_to_catalog_twin (the ONE mirror rule, shared with the
    PUT /products route); ``mark_dirty`` passes through to it (False = the
    twin follows but is not queued). Writes a before/after audit row.
    """
    if product_repo is None:
        raise ProductMasterError("No product repository.", status=500)
    current = product_repo.find_by_id(product_id)
    if current is None:
        raise ProductMasterError("Product not found.", status=404)

    clean: Dict[str, Any] = {k: v for k, v in (patch or {}).items() if v is not None}

    # Merge price fields against the current doc for the both-directions check.
    new_mrp = clean.get("mrp", current.get("mrp"))
    new_offer = clean.get("offer_price", current.get("offer_price"))
    # THE offer-write check, against the EFFECTIVE tier and brand. The cap is
    # judged when the edit CHANGES the MRP, offer, tier or brand (any can make
    # the standing offer too deep); otherwise only the MRP rule -- a legacy
    # row's offer never blocks an unrelated edit (a rename, a model edit
    # reaching its variants).
    _cap_problem = offer_write_problem(
        new_mrp,
        new_offer,
        str(clean.get("discount_category") or current.get("discount_category") or "") or None,
        clean.get("brand", current.get("brand")),
        judge_cap=offer_terms_changed(clean, current),
    )
    if _cap_problem:
        raise ProductMasterError(_cap_problem, status=400, field="offer_price")

    # discount_category: validate + enforce LOWER-ONLY for LUXURY (cannot move a
    # LUXURY tier to a laxer tier -- CORRECTIONS E2 luxury-cap invariant).
    if "discount_category" in clean:
        dc = str(clean["discount_category"]).strip().upper()
        if dc not in VALID_DISCOUNT_CATEGORIES:
            raise ProductMasterError(
                f"Invalid discount_category '{clean['discount_category']}'.",
                status=422,
                field="discount_category",
            )
        if current.get("discount_category") == "LUXURY" and dc != "LUXURY":
            raise ProductMasterError(
                "A LUXURY product's discount tier may not be loosened.",
                status=400,
                field="discount_category",
            )
        clean["discount_category"] = dc

    # Tags: normalise on edit too so the canonical shape is identical to create
    # (step-12). An explicit [] clears tags, hence we re-admit it after the
    # None-strip above (a caller intending to clear sends [] not None).
    if "tags" in (patch or {}) and patch.get("tags") is not None:
        clean["tags"] = normalise_tags(patch.get("tags"))

    # Model families: a variant's model-level fields are its model's
    # (`from_model` = this write IS the model's edit reaching it).
    model_changes: Dict[str, Any] = {}
    if not from_model:
        assert_variant_model_fields(
            current,
            clean.get("attributes"),
            brand=clean.get("brand"),
            model=clean.get("model"),
            category=clean.get("category"),
        )
    if "attributes" in clean:
        model_changes = model_field_changes(current.get("attributes") or {}, clean["attributes"] or {})

    rekey_on_edit(current, clean, product_repo=product_repo)

    # Cataloguer attribution: stamp WHO edited (id + display name) on the doc.
    # Creation attribution is immutable -- a patch may never rewrite
    # created_by / created_by_name, so strip them defensively before the $set.
    clean.pop("created_by", None)
    clean.pop("created_by_name", None)
    if actor:
        # THE edit stamp (catalog_list.edit_stamp): who and when. The name is
        # ALWAYS written alongside updated_by (raw-id fallback when it is
        # unresolvable) so a previous editor's stale name can never survive
        # next to the new editor's id.
        from .catalog_list import edit_stamp

        clean.update(edit_stamp(actor, actor_name or resolve_actor_name(actor, db=db)))

    before = {k: current.get(k) for k in clean.keys()}

    ok = product_repo.update(product_id, clean)
    if not ok:
        # update() returns False when nothing changed; re-read to return shape.
        pass
    updated = product_repo.find_by_id(product_id) or current

    # Mirror to the catalog twin -- the ONE shared rule (see its docstring).
    mirror_update_to_catalog_twin(
        product_id=product_id, current=updated, patch=clean, db=db,
        mark_dirty=mark_dirty,
    )

    if audit_repo is not None:
        try:
            audit_repo.create(
                {
                    "action": "product.updated",
                    "actor": actor,
                    "user_id": actor,
                    "entity_type": "product",
                    "entity_id": product_id,
                    "timestamp": datetime.now(),
                    "ts": utc_now_iso(),
                    "before": before,
                    "after": {k: updated.get(k) for k in clean.keys()},
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[PM] audit (update) write failed for %s: %s", product_id, exc
            )

    if model_changes and not from_model:
        updated["family_not_updated"] = propagate_model_fields(
            lead=updated, changes=model_changes, actor=actor, actor_name=actor_name,
            product_repo=product_repo, audit_repo=audit_repo, db=db,
        )
    return updated


def soft_delete_product(
    *, product_id: str, actor: str, product_repo=None, audit_repo=None
) -> bool:
    """Soft-delete (is_active=False) + audit row."""
    if product_repo is None:
        raise ProductMasterError("No product repository.", status=500)
    current = product_repo.find_by_id(product_id)
    if current is None:
        raise ProductMasterError("Product not found.", status=404)
    ok = product_repo.soft_delete(product_id)
    if audit_repo is not None:
        try:
            audit_repo.create(
                {
                    "action": "product.deleted",
                    "actor": actor,
                    "user_id": actor,
                    "entity_type": "product",
                    "entity_id": product_id,
                    "timestamp": datetime.now(),
                    "ts": utc_now_iso(),
                    "before": {"is_active": True},
                    "after": {"is_active": False},
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[PM] audit (delete) write failed for %s: %s", product_id, exc
            )
    return ok


def get_product(
    product_id_or_sku: str, *, product_repo=None
) -> Optional[Dict[str, Any]]:
    """Resolve a product by product_id first, then by SKU. None if absent."""
    if product_repo is None:
        return None
    found = product_repo.find_by_id(product_id_or_sku)
    if found is not None:
        return found
    return product_repo.find_by_sku(product_id_or_sku)


def list_products(
    *,
    product_repo=None,
    category: Optional[str] = None,
    brand: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    """List active products with optional category/brand filters."""
    if product_repo is None:
        return {"products": [], "total": 0}
    flt: Dict[str, Any] = {"is_active": True}
    canonical = resolve_category(category) if category else None
    if canonical:
        flt["category"] = canonical
    if brand:
        flt["brand"] = brand
    rows = product_repo.find_many(flt, skip=skip, limit=limit)
    return {"products": rows, "total": len(rows)}


# ===========================================================================
# Hub Phase 0 -- the catalog-done chokepoint (ONE rule, ONE module)
# ===========================================================================
# The foundation every later hub phase depends on. "Catalog done" is the single
# server-side predicate that unlocks purchase. There is NO frontend copy of this
# rule -- the FE reads catalog_readiness() / the persisted catalog_status.
#
# Done-rule (owner-locked, PRODUCT_HUB_RECOMMENDATION sec 3/5b):
#   * category resolves to a canonical key, AND
#   * every per-category REQUIRED attribute is present (collect-all-missing,
#     not raise-on-first -- the owner wants the full gap list named at once), AND
#   * mrp > 0  AND  offer_price > 0  AND  offer_price <= mrp
#     (reuse evaluate_offer_price; MRP_BELOW_OFFER surfaces as a BLOCKER), AND
#   * cost_price > 0   (NEW in Phase 0 -- purchase needs a known cost), AND
#   * hsn_code present  AND  gst_rate is not None  (both still server-derived).
# Images are NOT required. Shopify is NOT required. purchasable = complete AND
# is_active.
#
# Status values: "ACTIVE" (done) | "DRAFT" (incomplete, persisted via as_draft).
# A MISSING catalog_status reads as ACTIVE so the gate is deploy-order-
# independent (legacy rows + rows written before this code shipped are ACTIVE).

CATALOG_STATUS_ACTIVE = "ACTIVE"
CATALOG_STATUS_DRAFT = "DRAFT"

# The as_draft FLOOR: below these three identity fields a payload is rejected
# (422) even when as_draft=true -- a draft must at least name what it is.
_DRAFT_FLOOR_ATTRS = ("brand_name", "model_no")
# brand_name + (model_no OR model_name) + a resolvable category. Several
# categories use model_name (CL, SMARTWATCH, ...) instead of model_no, so the
# floor accepts either as the "model" identity.
_MODEL_FLOOR_KEYS = ("model_no", "model_name")


def _is_blank(val: Any) -> bool:
    """A field is 'missing' when absent, None, or an empty/whitespace string."""
    return val is None or (isinstance(val, str) and not val.strip())


def _overlay_attributes(product_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Build the attribute view the done-rule checks, applying the INVERSE flat-
    field overlay so a legacy row (identity stored top-level as brand/model/
    color/size/name, no canonical attribute keys) does NOT read falsely
    incomplete.

    Precedence: an explicit value already in `attributes` wins; otherwise the
    top-level flat column fills the canonical key. Mirrors normalise_door_payload
    in reverse (brand->brand_name, model->model_no, color->colour_code, size,
    name) without mutating the input doc.
    """
    doc = product_doc or {}
    attrs: Dict[str, Any] = dict(doc.get("attributes") or {})
    # Flat top-level identity -> canonical attribute keys (fill-only).
    overlay = {
        "brand_name": doc.get("brand"),
        "model_no": doc.get("model"),
        "model_name": doc.get("model"),
        "colour_code": doc.get("color") or doc.get("colour"),
        "size": doc.get("size"),
        "name": doc.get("name"),
    }
    for key, val in overlay.items():
        if not _is_blank(val) and _is_blank(attrs.get(key)):
            attrs[key] = val
    return attrs


def missing_required_fields(category: Any, attributes: Dict[str, Any]) -> List[str]:
    """Collect ALL missing per-category required attributes (not raise-on-first).

    Returns the ordered list of required attribute keys that are absent/blank
    for `category`. An unknown/blank category yields ["category"] (the category
    itself is the first thing missing). Pure -- never raises, never mutates.
    """
    spec = category_spec(category)
    if spec is None:
        return ["category"]
    attrs = attributes or {}
    return [fld for fld in spec.required if _is_blank(attrs.get(fld))]


def gaps_before_cost(doc: Dict[str, Any]) -> List[str]:
    """The catalogue gaps a purchase waits on: every gap but cost_price, which
    the purchase itself brings (rulings 11-13 -- the PO rate, the receipt, the
    vendor bill write it). THE one rule for the bill's booking gate, send_po
    and the asks for cataloguing; compute_catalog_status's order is kept."""
    return [g for g in compute_catalog_status(doc)[1] if g != "cost_price"]


def compute_catalog_status(doc: Dict[str, Any]) -> tuple:
    """THE catalog-done chokepoint (write-side stamp).

    Returns (catalog_status, done_gaps) where catalog_status is "ACTIVE" when
    the done-rule passes and "DRAFT" otherwise, and done_gaps is the ordered,
    de-duplicated list of what is missing/blocking (machine field keys + the
    sentinel reasons "mrp", "offer_price", "cost_price", "hsn_code", "gst_rate",
    and the blocker "MRP_BELOW_OFFER").

    Pure + deterministic. Reads the INVERSE flat-field overlay so legacy rows are
    judged on their real (possibly top-level) identity. Never raises.
    """
    doc = doc or {}
    gaps: List[str] = []

    # 1) category resolves + per-category required attributes (collect-all).
    canonical = resolve_category(doc.get("category"))
    if canonical is None:
        gaps.append("category")
        # Without a category we cannot know the required attrs; still report the
        # money/derived gaps below so the FE shows the full picture.
    else:
        attrs = _overlay_attributes(doc)
        gaps.extend(missing_required_fields(canonical, attrs))

    # 2) pricing: mrp > 0, offer_price > 0, offer <= mrp.
    mrp = doc.get("mrp")
    offer = doc.get("offer_price")
    if not _positive_number(mrp):
        gaps.append("mrp")
    if not _positive_number(offer):
        gaps.append("offer_price")
    # offer<=mrp invariant via the shared validator (only when both are usable).
    if _positive_number(mrp) and _positive_number(offer):
        verdict = evaluate_offer_price(mrp, offer)
        if verdict.get("reason") == "MRP_BELOW_OFFER":
            gaps.append("MRP_BELOW_OFFER")

    # 3) cost_price > 0  (NEW in Phase 0).
    if not _positive_number(doc.get("cost_price")):
        gaps.append("cost_price")

    # 4) server-derived tax fields must be stamped on the doc.
    if _is_blank(doc.get("hsn_code")):
        gaps.append("hsn_code")
    if doc.get("gst_rate") is None:
        gaps.append("gst_rate")

    # De-dupe preserving first-seen order.
    seen: Dict[str, None] = {}
    for g in gaps:
        if g not in seen:
            seen[g] = None
    ordered_gaps = list(seen.keys())

    status = CATALOG_STATUS_ACTIVE if not ordered_gaps else CATALOG_STATUS_DRAFT
    return status, ordered_gaps


def _positive_number(val: Any) -> bool:
    """True when val is a number strictly > 0. Tolerant of numeric strings;
    None / blank / non-numeric -> False."""
    if val is None:
        return False
    try:
        return float(val) > 0
    except (TypeError, ValueError):
        return False


def catalog_readiness(product_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Read-side view of the SAME chokepoint (no second rule).

    Returns {complete, missing, blockers, purchasable}:
      * complete   -- the done-rule passes (catalog_status would be ACTIVE),
      * missing    -- the missing required FIELD keys (gaps that are field names),
      * blockers   -- the hard blockers (currently just "MRP_BELOW_OFFER"),
      * purchasable -- complete AND is_active (a MISSING is_active reads True so
                       legacy rows without the flag stay purchasable).

    Built directly on compute_catalog_status so the read view can never drift
    from the write stamp.
    """
    status, gaps = compute_catalog_status(product_doc or {})
    blockers = [g for g in gaps if g in _CATALOG_BLOCKERS]
    missing = [g for g in gaps if g not in _CATALOG_BLOCKERS]
    complete = status == CATALOG_STATUS_ACTIVE
    is_active = (product_doc or {}).get("is_active", True)
    return {
        "complete": complete,
        "missing": missing,
        "blockers": blockers,
        "purchasable": bool(complete and is_active),
    }


# Gaps that are HARD blockers (a rule violation), not merely "a field to fill".
_CATALOG_BLOCKERS = frozenset({"MRP_BELOW_OFFER"})


def effective_catalog_status(doc: Dict[str, Any]) -> str:
    """The status a READER should treat the doc as having.

    A MISSING / blank catalog_status reads as ACTIVE (deploy-order-independent:
    rows written before this code, and the migration backfill, are ACTIVE). An
    explicit DRAFT stays DRAFT. Any OTHER non-blank value is read fail-CLOSED as
    DRAFT (not-purchasable) -- nothing in this codebase writes a status other than
    ACTIVE/DRAFT, so this only ever guards against a future/foreign value being
    mistaken for purchasable. The returned value is always exactly one of
    {ACTIVE, DRAFT}.
    """
    raw = (doc or {}).get("catalog_status")
    if _is_blank(raw):
        return CATALOG_STATUS_ACTIVE
    normalised = str(raw).strip().upper()
    return normalised if normalised == CATALOG_STATUS_ACTIVE else CATALOG_STATUS_DRAFT


def is_ordered_draft(spine: Optional[Dict[str, Any]]) -> bool:
    """THE answer to 'is this an item ordered on a PO before it was
    catalogued, still waiting for the cataloguer' (audit C1): marked at the PO
    door (`provisional`) AND not finished (its catalogue status is not
    ACTIVE). A row finished before the finishing save cleared the mark (main
    never cleared it) is finished: POS-ready, never 'Ordered - finish it'.
    Every reader asks this -- the catalogue chips and drawer (via the
    `ordered_draft` the server stamps), Needs review's list / count / badge
    (stamp_ordered_drafts), the import review's refusal, DELETE's discard
    rule, the Add-product popup and the PO doors."""
    return bool((spine or {}).get("provisional")) and (
        effective_catalog_status(spine or {}) != CATALOG_STATUS_ACTIVE
    )


def stamp_ordered_drafts(copies: List[Dict[str, Any]], products_coll) -> List[Dict[str, Any]]:
    """Stamp each catalogue copy with `ordered_draft` -- is_ordered_draft of
    the product it copies -- and, on one, that product's id
    (`spine_product_id`: a copy made before PR #1171 never carried it). One
    read of the provisional products; a copy names its product by the
    product's id (its own spine_product_id, or a shared legacy id), its
    pim_product_id (a door-made copy's id) or its sku. Mutates and returns
    `copies`; a read error raises (no queue answers from half the data)."""
    drafts: Dict[str, str] = {}
    if products_coll is not None:
        for spine in products_coll.find({"provisional": True}):
            if is_ordered_draft(spine):
                for key in (spine.get("product_id"), spine.get("pim_product_id"), spine.get("sku")):
                    if key:
                        drafts[str(key)] = spine.get("product_id")
    for doc in copies:
        spine_id = next(
            (
                drafts[str(k)]
                for k in (doc.get("spine_product_id"), doc.get("id"), doc.get("sku"))
                if k and str(k) in drafts
            ),
            None,
        )
        doc["ordered_draft"] = spine_id is not None
        if spine_id:
            doc["spine_product_id"] = spine_id
    return copies


def in_needs_review(doc: Dict[str, Any]) -> bool:
    """The Needs-review queue (its list, count and badge) over a stamped copy
    (stamp_ordered_drafts): an ordered draft, or an import awaiting review. A
    copy still marked for review whose product is finished is in neither."""
    return bool(doc.get("ordered_draft")) or (
        bool(doc.get("needs_review")) and not doc.get("spine_product_id")
    )


def assert_draft_floor(doc: Dict[str, Any]) -> None:
    """Reject (422) a payload below the as_draft FLOOR: even a draft must carry a
    resolvable category PLUS the identity its category actually keys on.

    The floor is DERIVED from the category's own required set so the looser draft
    mode can never reject a payload the stricter (as_draft=False) mode would
    accept. A category that does not require a brand (SERVICES requires only
    `name`) or a model (OPTICAL_LENS requires brand+index+coating; SERVICES has
    neither) is not asked for one. Raises naming the first missing floor field.
    """
    canonical = resolve_category((doc or {}).get("category"))
    if canonical is None:
        raise ProductMasterError(
            "A draft product still needs a valid category.",
            status=422,
            field="category",
        )
    spec = category_spec(canonical)
    required = set(spec.required if spec else ())
    attrs = _overlay_attributes(doc or {})
    # Brand floor ONLY when the category requires a brand.
    if "brand_name" in required and _is_blank(attrs.get("brand_name")):
        raise ProductMasterError(
            "A draft product still needs a brand.", status=422, field="brand_name"
        )
    # Model floor ONLY when the category keys identity on a model (model_no or
    # model_name appears in its required set); then at least one must be present.
    if any(k in required for k in _MODEL_FLOOR_KEYS) and all(
        _is_blank(attrs.get(k)) for k in _MODEL_FLOOR_KEYS
    ):
        raise ProductMasterError(
            "A draft product still needs a model.", status=422, field="model_no"
        )


def apply_restamp_atomic(
    product_id: str,
    current: Dict[str, Any],
    patch: Dict[str, Any],
    *,
    product_repo=None,
) -> Dict[str, Any]:
    """Apply the Phase-0 catalog_status restamp as a GUARDED single-doc write.

    Computes restamp_on_update(current, patch) -- which yields status fields ONLY
    for a row whose CURRENT status is an explicit DRAFT (an effective-ACTIVE row,
    incl. a legacy row with no catalog_status, returns {} and is never touched) --
    then applies them via find_one_and_update keyed on {catalog_status: DRAFT}.
    The guard makes the write a no-op when a concurrent edit already promoted the
    row to ACTIVE, so two near-simultaneous PUTs cannot clobber each other's
    promotion (mirrors the vouchers.redeem_voucher_atomic concurrency pattern).

    Falls back to a plain product_repo.update when no atomic primitive is
    available (test stub / no live collection). Returns the status fields written
    (or {}). Fail-soft: any write error leaves the row as-is and returns {}.
    """
    fields = restamp_on_update(current, patch)
    if not fields or product_repo is None:
        return {}
    coll = getattr(product_repo, "collection", None)
    if coll is not None and hasattr(coll, "find_one_and_update"):
        try:
            guard = (
                {"catalog_status": CATALOG_STATUS_DRAFT}
                if "catalog_status" in fields
                else {"provisional": True}  # the finished-but-still-ordered repair
            )
            coll.find_one_and_update({"product_id": product_id, **guard}, {"$set": fields})
            return fields
        except Exception as exc:  # noqa: BLE001 - a restamp must never break an edit
            logger.warning("[PM] atomic restamp failed for %s: %s", product_id, exc)
            return {}
    # Fallback: no atomic primitive (test stub / no live collection). The guard
    # is unnecessary single-threaded; a plain update preserves correctness.
    try:
        product_repo.update(product_id, fields)
        return fields
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[PM] restamp fallback update failed for %s: %s", product_id, exc
        )
        return {}


def restamp_on_update(current: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """Recompute catalog_status on the MERGED (current + patch) doc for the
    route-level restamp hook (products.py update_product).

    Returns a dict of fields to fold into the update payload:
      * a DRAFT that the edit completes  -> {catalog_status: ACTIVE, done_gaps: []}
      * a DRAFT that stays incomplete     -> {catalog_status: DRAFT, done_gaps: [...]}
      * a row that READS as ACTIVE (explicit ACTIVE *or* a missing/blank status)
        -> {} (no-op).

    NEVER-DEMOTE is the owner-locked rule (DECISION A: the catalog-done gate is
    FORWARD-ONLY -- it only ever PROMOTES a draft, it never demotes or blocks an
    edit to a live row). This is load-bearing: the ~10,800 backfilled/legacy rows
    are all ACTIVE yet most are incomplete-by-done-rule (no cost_price), so
    touching an effective-ACTIVE row here would 422/flip the entire existing
    catalog on any routine edit. An effective-ACTIVE row is therefore left
    EXACTLY as-is -- the status field is never written, never recomputed.
    """
    prior = effective_catalog_status(current or {})
    if prior == CATALOG_STATUS_ACTIVE:
        # forward-only: live rows are never demoted or re-judged. One repair:
        # a FINISHED row still marked as an ordered draft (finished while
        # switched off before this rule) leaves the ordered-draft state --
        # Needs review and the PIM doors' "finish it in the editor" refusal --
        # on its next save; its is_active is the cataloguer's, untouched.
        if (current or {}).get("provisional"):
            # Still a PO-door buy for reorder_policy.discontinued: a finished
            # row nobody switched on reads new, never 'Discontinued'.
            return {"provisional": False, "bought_before_catalogued": True}
        return {}

    # prior is an explicit DRAFT: complete -> ACTIVE (auto-flip), else refresh gaps.
    merged = {**(current or {}), **(patch or {})}
    status, gaps = compute_catalog_status(merged)
    if status == CATALOG_STATUS_ACTIVE:
        fields = {"catalog_status": CATALOG_STATUS_ACTIVE, "done_gaps": []}
        if (current or {}).get("provisional"):
            # Ordered before it was catalogued: born inactive ONLY because it
            # was incomplete (normalise_payload), so finishing it is what makes
            # it sellable. An is_active False sent in the same save wins -- but
            # either way it is finished: no longer an ordered draft.
            fields["provisional"] = False
            # The PO-door mark reorder_policy.discontinued reads once
            # `provisional` is gone: finished but never switched on is new.
            fields["bought_before_catalogued"] = True
            if (patch or {}).get("is_active") is not False:
                fields["is_active"] = True
                # The restamp writes around ProductRepository.update, which is
                # what stamps this: switched on here, so a later switch-off
                # reads 'Discontinued' (reorder_policy.discontinued).
                fields["switched_on_at"] = datetime.now(timezone.utc)
        return fields
    return {"catalog_status": CATALOG_STATUS_DRAFT, "done_gaps": gaps}
