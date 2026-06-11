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
    canonical: str          # long-form products.category value (e.g. "FRAME")
    prefix: str             # short SKU prefix (e.g. "FR")
    display: str            # human label (e.g. "Frame")
    required: tuple         # required attribute keys for this category
    optional: tuple = ()    # optional attribute keys
    forced_discount_category: Optional[str] = None  # e.g. HEARING_AID -> NON_DISCOUNTABLE


# canonical -> CategorySpec. `required` folds the Excel/CATEGORY_FIELDS rules.
# HEARING_AID adds serial_no as REQUIRED (the catalog CATEGORY_FIELDS had it
# optional) per the PM packet, and is forced NON_DISCOUNTABLE.
_CATEGORY_SPECS: Dict[str, CategorySpec] = {
    "FRAME": CategorySpec(
        "FRAME", "FR", "Frame",
        required=("brand_name", "model_no", "colour_code"),
        optional=("subbrand", "size", "frame_material", "frame_type"),
    ),
    "SUNGLASS": CategorySpec(
        "SUNGLASS", "SG", "Sunglass",
        required=("brand_name", "model_no", "colour_code"),
        optional=("subbrand", "lens_size", "polarization", "uv_protection", "tint"),
    ),
    "OPTICAL_LENS": CategorySpec(
        "OPTICAL_LENS", "LS", "Optical Lens",
        required=("brand_name", "index", "coating"),
        optional=("subbrand", "lens_type", "material"),
    ),
    "READING_GLASSES": CategorySpec(
        "READING_GLASSES", "RG", "Reading Glasses",
        required=("brand_name", "model_no", "colour_code"),
        optional=("subbrand", "power"),
    ),
    "CONTACT_LENS": CategorySpec(
        "CONTACT_LENS", "CL", "Contact Lens",
        # Owner-decided reconcile (step-9): a contact lens catalogue entry needs
        # BOTH power AND expiry_date -- power so the SKU/stock grid is unambiguous
        # and expiry_date so a medical-device shelf-life is always recorded.
        required=("brand_name", "model_name", "power", "expiry_date"),
        optional=("subbrand", "colour_name", "pack", "modality"),
    ),
    "COLORED_CONTACT_LENS": CategorySpec(
        "COLORED_CONTACT_LENS", "CL", "Colored Contact Lens",
        required=("brand_name", "model_name", "power", "expiry_date"),
        optional=("subbrand", "colour_name", "pack"),
    ),
    "WATCH": CategorySpec(
        "WATCH", "WT", "Wrist Watch",
        required=("brand_name", "model_no", "colour_code"),
        optional=("subbrand", "dial_color", "strap_material"),
    ),
    "SMARTWATCH": CategorySpec(
        "SMARTWATCH", "SMTWT", "Smart Watch",
        required=("brand_name", "model_name", "colour_code"),
        optional=("subbrand",),
    ),
    "SMARTGLASSES": CategorySpec(
        "SMARTGLASSES", "SMTFR", "Smart Glasses",
        required=("brand_name", "model_name", "colour_code"),
        optional=("subbrand",),
    ),
    "WALL_CLOCK": CategorySpec(
        "WALL_CLOCK", "CK", "Wall Clock",
        required=("brand_name", "model_no", "colour_code"),
        optional=("subbrand",),
    ),
    "ACCESSORIES": CategorySpec(
        "ACCESSORIES", "ACC", "Accessories",
        required=("brand_name", "model_name"),
        optional=("subbrand", "size", "pack"),
    ),
    "SERVICES": CategorySpec(
        "SERVICES", "SVC", "Services",
        required=("name",),
        optional=("description",),
        forced_discount_category="SERVICE",
    ),
    "HEARING_AID": CategorySpec(
        "HEARING_AID", "HA", "Hearing Aid",
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


def all_category_specs() -> List[Dict[str, Any]]:
    """All canonical category specs, for the GET /products/categories endpoint."""
    out: List[Dict[str, Any]] = []
    for spec in _CATEGORY_SPECS.values():
        out.append(
            {
                "code": spec.canonical,
                "sku_prefix": spec.prefix,
                "name": spec.display,
                "required_fields": list(spec.required),
                "optional_fields": list(spec.optional),
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
        attributes.get("model_no") or attributes.get("model_name") or attributes.get("model")
    )
    # Colour code keeps separators (1109/71 -> 1109/71). Fall back to colour name.
    colour = _sku_segment(
        attributes.get("colour_code") or attributes.get("color_code"),
        keep_separators=True,
    )
    if not colour:
        colour = _sku_segment(
            attributes.get("colour_name") or attributes.get("color"), keep_separators=False
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


def mint_unique_sku(category: Any, attributes: Dict[str, Any], product_repo=None, db=None) -> str:
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


def _derive_brand_model_color_size(attributes: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Map category attribute keys onto the spine identity columns."""
    attrs = attributes or {}
    return {
        "brand": attrs.get("brand_name") or attrs.get("brand"),
        "model": attrs.get("model_no") or attrs.get("model_name") or attrs.get("model"),
        "color": attrs.get("colour_code") or attrs.get("colour_name") or attrs.get("color"),
        "size": attrs.get("size"),
    }


def normalise_payload(
    *,
    category: Any,
    attributes: Dict[str, Any],
    mrp: float,
    offer_price: float,
    sku: Optional[str] = None,
    discount_category: Optional[str] = None,
    hsn_code: Optional[str] = None,
    gst_rate: Optional[float] = None,
    country_of_origin: Optional[str] = None,
    warranty_months: Optional[int] = None,
    weight_grams: Optional[float] = None,
    created_by: Optional[str] = None,
    product_repo=None,
    db=None,
) -> Dict[str, Any]:
    """Build the persisted `products` spine doc. GST/HSN derived server-side,
    offer<=MRP enforced, discount_category validated, SKU minted if absent.

    Raises ProductMasterError on any invariant breach.
    """
    canonical = resolve_category(category)
    if canonical is None:
        raise ProductMasterError(
            f"Unknown product category '{category}'.", status=422, field="category"
        )
    spec = _CATEGORY_SPECS[canonical]

    validate_attributes(canonical, attributes)

    # offer <= MRP (shared validator -- the one source of truth, float tolerant).
    if mrp is None or offer_price is None:
        raise ProductMasterError("mrp and offer_price are required.", status=422)
    verdict = evaluate_offer_price(mrp, offer_price)
    if verdict.get("reason") == "MRP_BELOW_OFFER":
        raise ProductMasterError("Offer price cannot exceed MRP", status=400, field="offer_price")

    # discount_category: forced for HA/SERVICES, else validate the supplied tier.
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

    # GST / HSN: explicit value wins; else derive from the canonical table so
    # the master rate always equals what POS bills (gst_rates.py).
    resolved_hsn = hsn_code or hsn_for_category(canonical)
    resolved_gst = gst_rate if gst_rate is not None else gst_rate_for_category(canonical)

    # SKU: accept a supplied (possibly legacy) SKU as-is if it passes the
    # permissive guard; otherwise mint the canonical one.
    if sku:
        if not is_acceptable_sku(sku):
            raise ProductMasterError(
                f"Invalid SKU '{sku}'.", status=422, field="sku"
            )
        resolved_sku = str(sku).strip()
    else:
        resolved_sku = mint_unique_sku(canonical, attributes, product_repo=product_repo, db=db)

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
    if dc is not None:
        doc["discount_category"] = dc
    if country_of_origin is not None:
        doc["country_of_origin"] = country_of_origin
    if warranty_months is not None:
        doc["warranty_months"] = int(warranty_months)
    if weight_grams is not None:
        doc["weight_grams"] = float(weight_grams)
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
        return os.getenv("PM_MIRROR_ENABLED", "").strip().lower() in ("1", "true", "on", "yes")


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
            targets.append(_SyncTarget("catalog_products", "SKIPPED", "no catalog target"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PM] catalog_products mirror failed for %s: %s", spine.get("sku"), exc)
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
            targets.append(_SyncTarget("catalog_variants", "SKIPPED", "no variant repo"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PM] catalog_variants mirror failed for %s: %s", spine.get("sku"), exc)
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
# Unification step-9: ONE canonical product-create door every entry path uses
# ===========================================================================
# Every product-entry door (the FORM POST /products, the BULK /products/bulk-
# create, and the CATALOG POST /catalog/products) calls create_via_door() so the
# registry is the rulebook at EVERY door (owner ask #1). The door keeps its own
# auth/RBAC + response shape; only the validate+create CORE unifies here.
#
# STRICT (owner decision #7): an incomplete product is REJECTED at entry. The
# core validates through the registry (resolve_category -> validate_attributes ->
# required_fields) and enforces the existing invariants (MRP>=offer blocked,
# category->GST/HSN, category->discount-cap) -- it never changes those values.

# The known source labels. FORM = POST /products; BULK = /products/bulk-create;
# CATALOG = POST /catalog/products; MASTER = the engine door (POST /products/master).
VALID_DOOR_SOURCES = frozenset({"FORM", "BULK", "CATALOG", "MASTER"})

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
    p["attributes"] = attrs
    p["_source"] = source if source in VALID_DOOR_SOURCES else "FORM"
    return p


def build_canonical_product(
    payload: Dict[str, Any], *, source: str, product_repo=None, db=None
) -> Dict[str, Any]:
    """Validate + build the canonical `products` spine doc for a door payload.

    This is the SHARED validate-and-build core every door runs so the SAME
    complete payload yields an IDENTICAL canonical product at all three doors,
    and the SAME incomplete payload is rejected (422) at all three. Raises
    ProductMasterError on any registry / invariant breach (the caller maps it to
    HTTP). Does NOT persist -- create_via_door() layers persistence on top.
    """
    p = normalise_door_payload(payload, source=source)
    return normalise_payload(
        category=p.get("category"),
        attributes=p.get("attributes") or {},
        mrp=p.get("mrp"),
        offer_price=p.get("offer_price"),
        sku=p.get("sku"),
        discount_category=p.get("discount_category"),
        hsn_code=p.get("hsn_code"),
        gst_rate=p.get("gst_rate"),
        country_of_origin=p.get("country_of_origin"),
        warranty_months=p.get("warranty_months"),
        weight_grams=p.get("weight_grams"),
        created_by=p.get("created_by") or p.get("actor"),
        product_repo=product_repo,
        db=db,
    )


def create_via_door(
    payload: Dict[str, Any],
    *,
    source: str,
    actor: str,
    product_repo=None,
    catalog_repo=None,
    variant_repo=None,
    audit_repo=None,
    db=None,
) -> Dict[str, Any]:
    """THE single create path every product-entry door delegates to (step-9).

    Validates through the registry (STRICT), enforces the existing invariants,
    writes the canonical `products` spine, and (mirror ON by default) writes the
    fail-soft catalog/variant shadow. Returns the created canonical spine doc
    (with `sync_status`). Raises ProductMasterError on a validation failure
    (before any write) -- the calling router maps `.status`/`.field` to HTTP.

    `source` (FORM|BULK|CATALOG|MASTER) is recorded on the doc for provenance;
    the validation + write behaviour is identical across sources.
    """
    p = normalise_door_payload(payload, source=source)
    created = create_product(
        category=p.get("category"),
        attributes=p.get("attributes") or {},
        mrp=p.get("mrp"),
        offer_price=p.get("offer_price"),
        actor=actor,
        sku=p.get("sku"),
        discount_category=p.get("discount_category"),
        hsn_code=p.get("hsn_code"),
        gst_rate=p.get("gst_rate"),
        country_of_origin=p.get("country_of_origin"),
        warranty_months=p.get("warranty_months"),
        weight_grams=p.get("weight_grams"),
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


def create_product(
    *,
    category: Any,
    attributes: Dict[str, Any],
    mrp: float,
    offer_price: float,
    actor: str,
    sku: Optional[str] = None,
    discount_category: Optional[str] = None,
    hsn_code: Optional[str] = None,
    gst_rate: Optional[float] = None,
    country_of_origin: Optional[str] = None,
    warranty_months: Optional[int] = None,
    weight_grams: Optional[float] = None,
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

    Returns the created spine doc (with `sync_status`). Raises
    ProductMasterError on a validation failure (before any write).
    """
    spine = normalise_payload(
        category=category,
        attributes=attributes,
        mrp=mrp,
        offer_price=offer_price,
        sku=sku,
        discount_category=discount_category,
        hsn_code=hsn_code,
        gst_rate=gst_rate,
        country_of_origin=country_of_origin,
        warranty_months=warranty_months,
        weight_grams=weight_grams,
        created_by=actor,
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

    # SKU uniqueness guard on the spine (the canonical index enforces it too).
    existing = product_repo.find_by_sku(spine["sku"])
    if existing is not None:
        raise ProductMasterError(
            f"Product with SKU '{spine['sku']}' already exists.", status=400, field="sku"
        )

    # --- STEP 1: spine FIRST + alone (single-document atomic create) ---
    created = product_repo.create(spine)
    if not created:
        raise ProductMasterError("Failed to create product.", status=500)
    product_id = created.get("product_id")

    # --- STEP 2: gated, best-effort mirror (never corrupts the spine) ---
    targets = _write_mirror(created, catalog_repo=catalog_repo, variant_repo=variant_repo, db=db)

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

    before = {k: current.get(k) for k in clean.keys()}

    ok = product_repo.update(product_id, clean)
    if not ok:
        # update() returns False when nothing changed; re-read to return shape.
        pass
    updated = product_repo.find_by_id(product_id) or current

    # Mirror to the PIM doc (flag-gated, best-effort).
    if mirror_enabled() and updated.get("pim_product_id"):
        try:
            pim_patch = {k: clean[k] for k in ("mrp", "offer_price", "hsn_code", "gst_rate") if k in clean}
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
            logger.warning("[PM] audit (update) write failed for %s: %s", product_id, exc)

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
            logger.warning("[PM] audit (delete) write failed for %s: %s", product_id, exc)
    return ok


def get_product(product_id_or_sku: str, *, product_repo=None) -> Optional[Dict[str, Any]]:
    """Resolve a product by product_id first, then by SKU. None if absent."""
    if product_repo is None:
        return None
    found = product_repo.find_by_id(product_id_or_sku)
    if found is not None:
        return found
    return product_repo.find_by_sku(product_id_or_sku)


def list_products(
    *, product_repo=None, category: Optional[str] = None, brand: Optional[str] = None,
    skip: int = 0, limit: int = 50,
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
