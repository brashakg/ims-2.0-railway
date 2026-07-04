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
  * `build_sku` -- a REWRITE of the SKU rule (PREFIX + BRAND + MODEL + COLORCODE
    + SIZE per the Excel spec), format-PERMISSIVE for legacy SKUs (`/` and `-`
    preserved, no length cap), atomic-counter suffix only on collision.
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

import os
import re
import uuid
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .gst_rates import gst_rate_for_category, hsn_for_category
from .pricing_caps import evaluate_offer_price

logger = logging.getLogger("ims.product_master")

# Permissive legacy-SKU pattern: letters, digits, and the two separators the
# Excel rule and Shopify both use. Deliberately NO length constraint -- the
# canonical Excel example (SGPRADAVPR19W1AB1O153 / FRBURBERRYB31421109/7155)
# already varies wildly, and legacy import must never be rejected.
_SKU_PERMISSIVE = re.compile(r"^[A-Za-z0-9/_-]+$")

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

    def __init__(self, message: str, status: int = 422, field: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.field = field
        # Optional machine code + conflict payload (Hub Phase 1 duplicate guard):
        # a 409 carries `conflict = {product_id, sku, identity_key}` of the
        # existing row so the caller/FE can link to it ("add stock / a variant").
        self.code: Optional[str] = None
        self.conflict: Optional[Dict[str, Any]] = None


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


# canonical -> CategorySpec. `required` folds the Excel/CATEGORY_FIELDS rules.
# HEARING_AID adds serial_no as REQUIRED (the catalog CATEGORY_FIELDS had it
# optional) per the PM packet, and is forced NON_DISCOUNTABLE.
_CATEGORY_SPECS: Dict[str, CategorySpec] = {
    "FRAME": CategorySpec(
        "FRAME",
        "FR",
        "Frame",
        required=("brand_name", "model_no", "colour_code"),
        # Rich eyewear field set (shared with SUNGLASS, frame-specific split).
        # UPC/GTIN are the MANUFACTURER's barcodes (captured for reference); our
        # own internal barcode is minted separately at Goods-Receipt, not here.
        optional=(
            "subbrand",
            "size",
            "frame_material",
            "frame_type",
            "label",
            "full_model_no",
            "shape",
            "frame_color",
            "temple_color",
            "temple_material",
            "lens_size",
            "bridge_width",
            "temple_length",
            "lens_usp",
            "product_usp",
            "usp_1",
            "usp_2",
            "usp",
            "gender",
            "gender_label",
            "country_of_origin",
            "warranty",
            "upc",
            "gtin",
            "blue_cut_lens",
        ),
    ),
    "SUNGLASS": CategorySpec(
        "SUNGLASS",
        "SG",
        "Sunglass",
        required=("brand_name", "model_no", "colour_code"),
        # Rich eyewear field set (shared with FRAME, sunglass-specific split:
        # lens_colour + lens_material instead of blue_cut_lens). UPC/GTIN are the
        # MANUFACTURER's barcodes; our internal barcode is minted at GRN.
        optional=(
            "subbrand",
            "lens_size",
            "polarization",
            "uv_protection",
            "tint",
            "label",
            "full_model_no",
            "shape",
            "frame_color",
            "temple_color",
            "frame_material",
            "temple_material",
            "frame_type",
            "bridge_width",
            "temple_length",
            "lens_usp",
            "product_usp",
            "usp_1",
            "usp_2",
            "usp",
            "gender",
            "gender_label",
            "country_of_origin",
            "warranty",
            "upc",
            "gtin",
            "lens_colour",
            "lens_material",
        ),
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
        optional=("subbrand", "power"),
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
        "CL",
        "Colored Contact Lens",
        required=("brand_name", "model_name", "power", "expiry_date"),
        optional=("subbrand", "colour_name", "pack"),
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
        required=("brand_name", "model_name", "colour_code"),
        optional=("subbrand",),
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
    "frame_material": "Frame Material",
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
    "label": "Label",
    "full_model_no": "Full Model No",
    "shape": "Shape",
    "frame_color": "Frame Colour",
    "temple_color": "Temple Colour",
    "temple_material": "Temple Material",
    "bridge_width": "Bridge Width (mm)",
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
                {"name": key, "label": field_label(key), "required": True}
            )
        for key in spec.optional:
            fields.append(
                {"name": key, "label": field_label(key), "required": False}
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
# SKU rule (REWRITE -- NOT a wrapper of catalog.generate_sku)
# ===========================================================================


def _sku_segment(value: Any, *, keep_separators: bool = False) -> str:
    """Uppercase a value, stripping spaces. Keeps `/` and `-` when asked
    (colour codes like `1109/71` must survive verbatim per the Excel rule)."""
    s = str(value or "").strip().upper().replace(" ", "")
    if keep_separators:
        return s
    return re.sub(r"[^A-Z0-9]", "", s)


def build_sku(category: Any, attributes: Dict[str, Any], db=None) -> str:
    """Mint a canonical SKU: PREFIX + BRAND + MODEL + COLORCODE + SIZE.

    REWRITE of the SKU rule (the legacy catalog.generate_sku is left untouched
    for the /catalog path). Key differences from generate_sku:
      * verbatim concatenation per the Excel spec (no truncation to 2/4/3 chars),
      * the colour code keeps `/` and `-` (e.g. `1109/71` stays `1109/71`),
      * the atomic counter suffix is appended ONLY on a uniqueness collision,
        not unconditionally.

    `db` (optional) is used to allocate the collision-suffix counter atomically
    + persistently (reuses catalog._next_sku_counter, falling back to an
    in-memory dict when no DB). A `find_by_sku`-style dedupe is the caller's
    responsibility; this function also resolves a collision itself when given
    the product repo via `_resolve_collision`.
    """
    spec = category_spec(category)
    if spec is None:
        raise ProductMasterError(
            f"Unknown product category '{category}'.", status=422, field="category"
        )

    brand = _sku_segment(attributes.get("brand_name") or attributes.get("brand"))
    model = _sku_segment(
        attributes.get("model_no")
        or attributes.get("model_name")
        or attributes.get("model")
    )
    # Colour code keeps separators (1109/71 -> 1109/71). Fall back to colour name.
    colour = _sku_segment(
        attributes.get("colour_code") or attributes.get("color_code"),
        keep_separators=True,
    )
    if not colour:
        colour = _sku_segment(
            attributes.get("colour_name") or attributes.get("color"),
            keep_separators=False,
        )
    size = _sku_segment(attributes.get("size"), keep_separators=True)

    return f"{spec.prefix}{brand}{model}{colour}{size}"


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


def mint_unique_sku(
    category: Any, attributes: Dict[str, Any], product_repo=None, db=None
) -> str:
    """Mint a SKU and resolve any collision by appending an atomic counter.

    The canonical SKU is tried first (deterministic per the Excel rule). If a
    product with that SKU already exists (checked via product_repo.find_by_sku),
    a counter suffix is appended so the second product of the same
    brand/model/colour is still distinct and the `sku` unique index holds.
    """
    base = build_sku(category, attributes, db=db)
    if product_repo is None:
        return base
    try:
        if product_repo.find_by_sku(base) is None:
            return base
    except Exception:  # noqa: BLE001 - fail toward minting a suffixed SKU
        pass
    spec = category_spec(category)
    prefix = spec.prefix if spec else "XX"
    # Loop a few times in the unlikely event the suffixed SKU also collides.
    for _ in range(10):
        suffix = _next_collision_suffix(prefix, db=db)
        candidate = f"{base}-{suffix}"
        try:
            if product_repo.find_by_sku(candidate) is None:
                return candidate
        except Exception:  # noqa: BLE001
            return candidate
    return f"{base}-{uuid.uuid4().hex[:8].upper()}"


def is_acceptable_sku(sku: Any) -> bool:
    """Format-PERMISSIVE legacy-SKU acceptance: allow letters/digits and the
    `/`, `-`, `_` separators with no length constraint. Legacy Shopify-style
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


def _derive_brand_model_color_size(
    attributes: Dict[str, Any],
) -> Dict[str, Optional[str]]:
    """Map category attribute keys onto the spine identity columns."""
    attrs = attributes or {}
    return {
        "brand": attrs.get("brand_name") or attrs.get("brand"),
        "model": attrs.get("model_no") or attrs.get("model_name") or attrs.get("model"),
        "color": attrs.get("colour_code")
        or attrs.get("colour_name")
        or attrs.get("color"),
        "size": attrs.get("size"),
    }


def compute_identity_key(
    brand: Any, model: Any, colour: Any = None, size: Any = None
) -> Optional[str]:
    """The brand+model+colour(+size) identity used by the Hub Phase 1 duplicate
    guard.

    Normalised so casing/spacing/PUNCTUATION variants of the same product collide:
    lowercased, and every run of [-/_. whitespace] folded to a single space (the
    same separators the SKU builder strips). Without this, "RB-2140" and "RB 2140"
    would be distinct identities yet mint the SAME SKU base -- the SKU
    collision-suffix would then create a real duplicate that the identity arm
    missed. Returns None unless BOTH brand and model are present -- an identity
    needs at least those two; a category without them (e.g. SERVICES) gets no
    identity_key and is not identity-deduped. Colour is folded in (empty when
    absent) so two colours of the same model are distinct products. SIZE is folded
    in ONLY when present (build_sku already varies on size) so the same frame in
    two sizes is two distinct products -- a sizeless product keeps the 3-part key
    (backward-compatible).
    """

    def _norm(v: Any) -> str:
        return re.sub(r"[-/_.\s]+", " ", str(v or "").strip().lower()).strip()

    b, m = _norm(brand), _norm(model)
    if not b or not m:
        return None
    parts = [b, m, _norm(colour)]
    s = _norm(size)
    if s:
        parts.append(s)
    return "|".join(parts)


def _duplicate_error(existing: Dict[str, Any]) -> "ProductMasterError":
    """Build the 409 duplicate-product error carrying the EXISTING row so the
    caller/FE can link to it ('add stock or a variant instead'). Hub Phase 1."""
    existing = existing or {}
    err = ProductMasterError(
        "A product with this identity already exists "
        f"(SKU {existing.get('sku')}). Add stock or a variant instead of a "
        "duplicate.",
        status=409,
        field="sku",
    )
    err.code = "DUPLICATE_PRODUCT"
    err.conflict = {
        "product_id": existing.get("product_id"),
        "sku": existing.get("sku"),
        "identity_key": existing.get("identity_key"),
        "barcode": existing.get("barcode"),
    }
    return err


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
    weight_grams: Optional[float] = None,
    tags: Any = None,
    created_by: Optional[str] = None,
    as_draft: bool = False,
    force_draft: bool = False,
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

    Raises ProductMasterError on any invariant breach.
    """
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
            )

    # Catalog Dictionary: when the owner has configured allowed values for a
    # field (Settings -> Catalog Dictionary / Brand Master), a present value
    # must match one of them; the match canonicalises casing. Fail-soft when
    # db is absent (unit tests / callers without a connection).
    attributes = enforce_dictionary_values(canonical, attributes or {}, db=db)

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

    # GST / HSN: explicit value wins; else derive from the canonical table so
    # the master rate always equals what POS bills (gst_rates.py).
    resolved_hsn = hsn_code or hsn_for_category(canonical)
    resolved_gst = (
        gst_rate if gst_rate is not None else gst_rate_for_category(canonical)
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

    ids = _derive_brand_model_color_size(attributes)

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
    # Identity columns only when present (additive -- keep doc lean).
    if ids["color"] is not None:
        doc["color"] = ids["color"]
    if ids["size"] is not None:
        doc["size"] = ids["size"]
    # Hub Phase 1: the brand+model+colour identity key for the duplicate guard.
    # Stamped only when brand+model are both present (the minimum that makes an
    # identity meaningful); categories without a brand/model -- e.g. SERVICES --
    # carry no identity_key and are not identity-deduped.
    _ident = compute_identity_key(ids["brand"], ids["model"], ids["color"], ids["size"])
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
    if weight_grams is not None:
        doc["weight_grams"] = float(weight_grams)
    # Normalised, governed tags (step-12). Always present as a list (possibly
    # empty) so collection rules + the tag filter have a consistent shape.
    doc["tags"] = normalise_tags(tags)
    # Door-specific additive columns -- never override a canonical key, never a
    # None value (keeps the spine lean + behaviour-preserving per door).
    for _k, _v in (extra_fields or {}).items():
        if _v is not None and _k not in doc:
            doc[_k] = _v
    # Owner decision (2026-07-04): every new product is born with
    # reorder_quantity = -1 = "no auto-reorder" (see api/services/
    # reorder_policy.py). Reorder engines skip the product until someone
    # explicitly configures a positive quantity (PUT /products/{id} /
    # Reorder dashboard). setdefault so a door that DID supply a value
    # (via extra_fields) keeps it.
    doc.setdefault("reorder_quantity", -1)
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
    return doc


# ===========================================================================
# Mirror gating (off-by-default -- NO live external write on a fresh deploy)
# ===========================================================================

_MIRROR_FLAG_KEY = "pm.mirror_enabled"


def mirror_enabled() -> bool:
    """Is the catalog/external mirror turned on?  OFF by default.

    Resolved via E2 get_policy (so it is store/entity/global scoped + env
    overridable). Defaults to False so a fresh deploy NEVER mirrors. Fail-soft:
    any error -> False (no mirror).
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


def _build_pim_doc(spine: Dict[str, Any]) -> Dict[str, Any]:
    """Project the spine + its attributes into a catalog_products PIM doc.

    The PIM doc is schemaless; it carries the Shopify/PIM superset attributes
    untouched under `ecom.category_specific` so they round-trip to NEXUS.
    """
    attrs = dict(spine.get("attributes") or {})
    return {
        "id": spine.get("pim_product_id"),
        "parent_sku": spine.get("sku"),
        "category": spine.get("category"),
        "sku_prefix": spine.get("sku_prefix"),
        "brand": spine.get("brand"),
        "model": spine.get("model"),
        "mrp": spine.get("mrp"),
        "offer_price": spine.get("offer_price"),
        "hsn_code": spine.get("hsn_code"),
        "gst_rate": spine.get("gst_rate"),
        "status": "DRAFT",
        "ecom": {"category_specific": attrs},
        # Surface the common Shopify superset fields top-level too (verbatim).
        "attributes": attrs,
    }


def _write_mirror(
    spine: Dict[str, Any],
    *,
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

    pim_doc = _build_pim_doc(spine)

    # --- catalog_products (Mongo PIM doc) -- internal, flag-gated only ---
    try:
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
        logger.warning(
            "[PM] catalog_products mirror failed for %s: %s", spine.get("sku"), exc
        )
        targets.append(_SyncTarget("catalog_products", "FAILED", str(exc)[:200]))

    # --- catalog_variants (per-SKU identity) -- internal, flag-gated only ---
    try:
        if variant_repo is not None:
            variant_repo.upsert(
                {
                    "sku": spine.get("sku"),
                    "parent_product_id": spine.get("pim_product_id"),
                    "parent_sku": spine.get("sku"),
                }
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


def _sync_status_dict(targets: List[_SyncTarget]) -> Dict[str, Any]:
    return {
        "mirrored_at": datetime.now().isoformat(),
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
    "model": "model_no",
    "color": "colour_code",
    "colour": "colour_code",
    "size": "size",
}


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
            attrs.setdefault(attr_key, val)
    # A flat top-level `model` fills BOTH model_no AND model_name (mirrors the
    # read-side _overlay_attributes). Several categories key identity on
    # model_name (CONTACT_LENS, COLORED_CONTACT_LENS, SMARTWATCH, ...), so without
    # this a flat FORM/BULK create supplying only `model` would be 422'd for a
    # "missing model_name" the status stamp would otherwise have read fine.
    _model = p.get("model")
    if _model is not None and not (isinstance(_model, str) and not _model.strip()):
        attrs.setdefault("model_name", _model)
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
        weight_grams=p.get("weight_grams"),
        tags=p.get("tags"),
        created_by=p.get("created_by") or p.get("actor"),
        as_draft=bool(p.get("as_draft", False)),
        extra_fields=extra_fields,
        product_repo=product_repo,
        db=db,
    )


def create_via_door(
    payload: Dict[str, Any],
    *,
    source: str,
    actor: str,
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
        sku=p.get("sku"),
        cost_price=p.get("cost_price"),
        discount_category=p.get("discount_category"),
        hsn_code=p.get("hsn_code"),
        gst_rate=p.get("gst_rate"),
        country_of_origin=p.get("country_of_origin"),
        warranty_months=p.get("warranty_months"),
        weight_grams=p.get("weight_grams"),
        tags=p.get("tags"),
        as_draft=bool(p.get("as_draft", False)),
        force_draft=force_draft,
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
    "weight_grams",
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

    base_attrs = _overlay_attributes(src)  # canonical attrs incl. legacy overlay
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
    sku: Optional[str] = None,
    cost_price: Optional[float] = None,
    discount_category: Optional[str] = None,
    hsn_code: Optional[str] = None,
    gst_rate: Optional[float] = None,
    country_of_origin: Optional[str] = None,
    warranty_months: Optional[int] = None,
    weight_grams: Optional[float] = None,
    tags: Any = None,
    as_draft: bool = False,
    force_draft: bool = False,
    extra_fields: Optional[Dict[str, Any]] = None,
    product_repo=None,
    catalog_repo=None,
    variant_repo=None,
    audit_repo=None,
    db=None,
) -> Dict[str, Any]:
    """SPINE-FIRST + COMPENSATION triple-write.

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
        weight_grams=weight_grams,
        tags=tags,
        created_by=actor,
        as_draft=as_draft,
        force_draft=force_draft,
        extra_fields=extra_fields,
        product_repo=product_repo,
        db=db,
    )
    # Pre-assign the PIM link id so the spine + PIM doc share it from the start.
    spine["pim_product_id"] = str(uuid.uuid4())

    if product_repo is None:
        # No DB (local/dev): echo a synthetic shape; no mirror, no audit.
        spine.setdefault("product_id", str(uuid.uuid4()))
        spine["sync_status"] = _sync_status_dict(
            [_SyncTarget("catalog_products", "SKIPPED", "no db")]
        )
        return spine

    # --- Hub Phase 1: duplicate HARD-BLOCK (409 + show-existing) ---
    # Refuse a product that already exists by SKU, by brand+model+colour identity,
    # or by barcode (when one rides along). The DB unique indexes are the
    # race-safe backstop (handled at the create below). Pre-check first so the
    # common case returns the existing row for the FE to link to.
    existing = product_repo.find_by_sku(spine["sku"])
    if (
        existing is None
        and spine.get("identity_key")
        and hasattr(product_repo, "find_by_identity_key")
    ):
        existing = product_repo.find_by_identity_key(spine["identity_key"])
    if (
        existing is None
        and spine.get("barcode")
        and hasattr(product_repo, "find_by_barcode")
    ):
        try:
            existing = product_repo.find_by_barcode(spine["barcode"])
        except Exception:  # noqa: BLE001
            existing = None
    if existing is not None:
        raise _duplicate_error(existing)

    # --- STEP 1: spine FIRST + alone (single-document atomic create) ---
    # raise_on_duplicate=True so a race lost to the unique index surfaces as a
    # clean 409 (re-querying the winner) instead of a swallowed None -> 500.
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
    product_id = created.get("product_id")

    # --- STEP 2: gated, best-effort mirror (never corrupts the spine) ---
    targets = _write_mirror(
        created, catalog_repo=catalog_repo, variant_repo=variant_repo, db=db
    )

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
                    "ts": datetime.now().isoformat(),
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


def update_product(
    *,
    product_id: str,
    patch: Dict[str, Any],
    actor: str,
    product_repo=None,
    catalog_repo=None,
    audit_repo=None,
    db=None,
) -> Dict[str, Any]:
    """Update a product's mutable fields. Enforces offer<=MRP in BOTH directions
    (raise offer above existing MRP, OR lower MRP below existing offer) by
    merging the patch against the current doc before evaluating.

    Mirrors the spine update to the PIM doc when present + the flag is on.
    Writes a before/after audit row.
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
    if new_mrp is not None and new_offer is not None:
        verdict = evaluate_offer_price(new_mrp, new_offer)
        if verdict.get("reason") == "MRP_BELOW_OFFER":
            raise ProductMasterError(
                "Offer price cannot exceed MRP", status=400, field="offer_price"
            )

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

    before = {k: current.get(k) for k in clean.keys()}

    ok = product_repo.update(product_id, clean)
    if not ok:
        # update() returns False when nothing changed; re-read to return shape.
        pass
    updated = product_repo.find_by_id(product_id) or current

    # Mirror to the PIM doc (flag-gated, best-effort).
    if mirror_enabled() and updated.get("pim_product_id"):
        try:
            pim_patch = {
                k: clean[k]
                for k in ("mrp", "offer_price", "hsn_code", "gst_rate")
                if k in clean
            }
            if pim_patch:
                if catalog_repo is not None:
                    catalog_repo.upsert({"id": updated["pim_product_id"], **pim_patch})
                elif db is not None:
                    db.get_collection("catalog_products").update_one(
                        {"id": updated["pim_product_id"]}, {"$set": pim_patch}
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[PM] PIM update mirror failed for %s: %s", product_id, exc)

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
                    "ts": datetime.now().isoformat(),
                    "before": before,
                    "after": {k: updated.get(k) for k in clean.keys()},
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[PM] audit (update) write failed for %s: %s", product_id, exc
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
                    "ts": datetime.now().isoformat(),
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
            coll.find_one_and_update(
                {"product_id": product_id, "catalog_status": CATALOG_STATUS_DRAFT},
                {"$set": fields},
            )
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
        return {}  # forward-only: live rows are never demoted or re-judged.

    # prior is an explicit DRAFT: complete -> ACTIVE (auto-flip), else refresh gaps.
    merged = {**(current or {}), **(patch or {})}
    status, gaps = compute_catalog_status(merged)
    if status == CATALOG_STATUS_ACTIVE:
        return {"catalog_status": CATALOG_STATUS_ACTIVE, "done_gaps": []}
    return {"catalog_status": CATALOG_STATUS_DRAFT, "done_gaps": gaps}
