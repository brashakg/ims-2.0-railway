"""
IMS 2.0 - Products Router
==========================
Product catalog management endpoints
"""

import io
from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from typing import Any, Dict, List, Optional, Union
from datetime import datetime, timedelta
import logging
import random
import uuid
from .auth import get_current_user, require_roles
from ..dependencies import get_product_repository
from ..services.file_store import (
    get_file_store,
    ALLOWED_MIME_TYPES,
    MAX_FILE_SIZE_BYTES,
)

# Product-image editor (Photoroom background-removal + catalog-standard resize).
# Imported at module level so the edit endpoint below (and its test) resolve the
# editor through THIS module -- tests monkeypatch `get_image_editor` here.
from ..services.image_editor import get_image_editor, EditSpec

router = APIRouter()

logger = logging.getLogger("ims.products")

# Roles permitted to mutate the product catalog. Mirrors the frontend
# `catalog/add` route guard. SUPERADMIN auto-passes via require_roles.
_CATALOG_ROLES = ("ADMIN", "CATALOG_MANAGER")

# Roles permitted to run BULK price / offer operations. Bulk pricing is
# revenue-sensitive, so it is restricted to the catalog-mutation roles
# (ADMIN, CATALOG_MANAGER); SUPERADMIN auto-passes via require_roles.
_BULK_PRICING_ROLES = ("ADMIN", "CATALOG_MANAGER")

# Valid discount cap tiers (BVI-10). Mirrors pricing_caps.CATEGORY_DISCOUNT_CAPS.
# Validated on create/update so an unknown tier is rejected loudly instead of
# silently defaulting to MASS (15%) at POS -- which would over-permit LUXURY.
_VALID_DISCOUNT_CATEGORIES = frozenset(
    {"MASS", "PREMIUM", "LUXURY", "SERVICE", "NON_DISCOUNTABLE"}
)


# ============================================================================
# DATABASE HELPER FUNCTIONS
# ============================================================================


def _get_categories_from_db() -> List[str]:
    """Fetch product categories from database"""
    try:
        from database.connection import get_db

        db = get_db()
        if db and db.is_connected:
            # Get categories collection or distinct from products.
            # NOTE: PyMongo Collection.__bool__ raises NotImplementedError, so
            # a bare `if categories_collection:` raises -- it was swallowed by
            # the surrounding try/except and silently returned [] every time,
            # so DB-backed categories never loaded. Use `is not None`.
            categories_collection = db.db.get_collection("product_categories")
            if categories_collection is not None:
                cats = list(categories_collection.find({}, {"name": 1}))
                if cats:
                    return [c.get("name") for c in cats if c.get("name")]

            # Fallback: get distinct categories from products collection
            products_collection = db.db.get_collection("products")
            if products_collection is not None:
                categories = products_collection.distinct("category")
                if categories:
                    return categories
    except Exception:
        pass
    return []


# ============================================================================
# SCHEMAS
# ============================================================================


# Contact-lens product categories. CL identity fields only apply to these.
# Kept in sync with schemas.py PRODUCT_SCHEMA category enum.
CL_CATEGORIES = ("CONTACT_LENS", "COLORED_CONTACT_LENS", "CL")
# India: contact lenses are HSN 9001 (90013000) at 5% GST under GST 2.0
# (effective 22 Sep 2025; verified against the 56th GST Council press release
# Annexure-I Sr. 351 -- the 12% slab was eliminated, so 5% is now correct, not
# 12%). HSN/GST defaults for EVERY category come from the canonical table in
# api/services/gst_rates.py, which the POS billing engine (orders.py) also
# reads, so the product master rate always equals what POS bills.
from ..services.gst_rates import (
    gst_rate_for_category,
    hsn_for_category,
    GST_CATEGORY_TABLE,
)

CL_HSN_DEFAULT = "90013000"
CL_GST_DEFAULT = 5.0
CL_MODALITIES = ("DAILY", "FORTNIGHTLY", "MONTHLY", "QUARTERLY", "YEARLY", "COLOR")

# Unification step-8: the canonical product-category taxonomy lives in ONE
# place -- the product_master registry. This guard reads its canonical list from
# there instead of hand-maintaining a duplicate.
from ..services.product_master import canonical_categories as _pm_canonical_categories

# Accepted-category superset for the create/update guard. Behaviour-preserving:
# kept as the GST/HSN table keys (the canonical 13 registry categories PLUS the
# legacy aliases + 2-letter UI codes + order-only item_types that older data and
# other call sites still use). The registry's canonical list is a subset of this
# set, so every category the registry knows is accepted, and nothing that was
# accepted before is now rejected. A category MUST normalize (upper/trim) to one
# of these keys; anything blank/null/missing or unrecognized is rejected.
_VALID_CATEGORY_KEYS = frozenset(GST_CATEGORY_TABLE.keys())
# Human-friendly list surfaced in the 422 message: the canonical product
# categories, sourced from the registry (the single source of truth) rather than
# a copy. Same 13 categories as before -- only the source moved.
_VALID_CATEGORY_DISPLAY = tuple(_pm_canonical_categories())


def _pm_error_detail(err):
    """Map a ProductMasterError to the HTTPException `detail`. A duplicate 409
    carries a `conflict` payload, which we surface as a structured body so the FE
    can link to the existing row ('add stock / a variant'); everything else keeps
    the plain string message it always returned (behaviour-preserving)."""
    conflict = getattr(err, "conflict", None)
    if conflict:
        return {
            "message": err.message,
            "code": getattr(err, "code", None) or "DUPLICATE_PRODUCT",
            "existing": conflict,
        }
    return err.message


def _validate_category_or_422(category) -> str:
    """Reject a blank / null / missing / unrecognized product category.

    QA found an uncategorized product billed at the wrong GST rate. The
    AddProductPage already forces category selection at Step 1; this is the
    server-side guard so a direct API call cannot persist a category-less
    product (which would then fall back to a default GST rate at POS).

    Returns the trimmed category string on success; raises HTTP 422 otherwise.
    """
    norm = str(category).strip() if category is not None else ""
    if not norm:
        raise HTTPException(
            status_code=422,
            detail=(
                "Product category is required and cannot be blank. "
                f"Valid categories: {', '.join(_VALID_CATEGORY_DISPLAY)}."
            ),
        )
    if norm.upper() not in _VALID_CATEGORY_KEYS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown product category '{norm}'. "
                f"Valid categories: {', '.join(_VALID_CATEGORY_DISPLAY)}."
            ),
        )
    return norm


def _resolve_hsn_or_400(category: str, hsn_code) -> str:
    """Guarantee a product is never persisted with a blank HSN.

    STATUTORY GAP (P3): a product created without an hsn_code still resolved a
    gst_rate via the category fallback, but persisted a blank HSN -- so the
    invoice and the GSTR-1 HSN summary (and the Tally export) showed no HSN for
    that line. The GST law requires an HSN on every taxable line.

    Behaviour:
      - an explicit non-blank hsn_code always wins (returned trimmed),
      - otherwise AUTO-MINT the canonical HSN from the category via the single
        source of truth (services/gst_rates.py::hsn_for_category), which is the
        same table POS billing reads, so master == billing,
      - if the category has NO canonical HSN, reject with a clear 400 asking for
        an explicit hsn_code (never silently save a blank one).

    Assumes `category` has already been normalized by _validate_category_or_422.
    Returns the resolved HSN string.
    """
    explicit = str(hsn_code).strip() if hsn_code is not None else ""
    if explicit:
        return explicit
    minted = hsn_for_category(category)
    if minted:
        return str(minted)
    raise HTTPException(
        status_code=400,
        detail=(
            f"HSN code is required: category '{category}' has no canonical HSN "
            "to auto-fill. Please provide an explicit hsn_code so the invoice "
            "and GSTR-1 export carry a valid HSN."
        ),
    )


def _assert_mrp_ge_offer(mrp, offer_price) -> None:
    """Enforce the non-negotiable MRP >= offer_price rule via the SHARED
    pricing_caps validator -- the single source of truth also used by the
    bulk-price / bulk-offer endpoints (see services/pricing_caps.py). Sharing
    it keeps the "offer can never exceed MRP" check (incl. its float tolerance)
    in one place instead of duplicated inline comparisons.

    Single-product create/update intentionally enforce ONLY this rule:
      - A CAP_EXCEEDED verdict (discount-cap breach) is NOT raised here -- caps
        are a bulk-pricing / POS concern; the single-product path has never
        enforced them and doing so now could block legitimate catalog edits.
      - INVALID_MRP is left to pydantic's gt=0 field guards (create) and to
        fail-soft handling of legacy data (update); we act solely on the
        MRP-below-offer verdict so behavior is unchanged. Skips silently when
        either value is missing (partial update with no price merge).
    """
    if mrp is None or offer_price is None:
        return
    from ..services.pricing_caps import evaluate_offer_price

    verdict = evaluate_offer_price(mrp, offer_price)
    if verdict["reason"] == "MRP_BELOW_OFFER":
        raise HTTPException(status_code=400, detail="Offer price cannot exceed MRP")


def _validate_product_barcode_or_400(barcode, repo, this_product_id: str) -> None:
    """Validate a scan-to-sell product barcode, failing LOUDLY on a bad value.

    A product master barcode must be a real, scannable code that resolves to
    exactly one product, so:
      - Format + check digit: it must be a valid 13-digit EAN-13 (the symbology
        every other unit barcode in the system uses -- see services/barcode.py).
        A malformed / wrong-check-digit value is rejected with HTTP 400 instead
        of being silently persisted (a scanner would never decode it -> the
        product becomes un-scannable, the exact Fail-Loudly violation this
        guards against).
      - Uniqueness: a barcode already on a DIFFERENT product is rejected with
        HTTP 409 (the DB also enforces this via the unique sparse index; this
        check gives a clear message before the write).

    A blank / null barcode means "no change / clear it" and is intentionally
    allowed (skipped) -- only a non-empty value is validated.
    """
    if barcode is None:
        return
    code = str(barcode).strip()
    if not code:
        # Explicit clear -- nothing to validate.
        return

    from ..services import barcode as barcode_svc

    if not barcode_svc.validate_ean13(code):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid barcode '{code}'. A product barcode must be a valid "
                "13-digit EAN-13 (numeric, with a correct check digit)."
            ),
        )

    if repo is not None:
        clash = repo.find_one({"barcode": code})
        if clash is not None and clash.get("product_id") != this_product_id:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Barcode '{code}' is already assigned to another product "
                    f"({clash.get('sku') or clash.get('product_id')}). "
                    "Barcodes must be unique."
                ),
            )


# Fields persisted top-level on the product doc only when provided (additive).
# Shared by the single + bulk create paths so neither drifts.
_OPTIONAL_PRODUCT_FIELDS = (
    # BVI-10: discount_category is optional but must be persisted when provided
    # so POS pricing-cap logic can read the right tier (MASS/PREMIUM/LUXURY/…)
    # rather than falling back to MASS 15% on every product.
    "discount_category",
    "cl_series",
    "modality",
    "base_curve",
    "diameter",
    "cl_power",
    "cl_cyl",
    "cl_axis",
    "cl_add",
    "pack_size",
    "sph",
    "cyl",
    "axis",
    "add",
    # Product images: self-hosted /products/image URLs (or vetted absolute
    # URLs) the Add-Product form uploads BEFORE create. This field was
    # collected by the FE but never modelled here, so pydantic silently
    # DROPPED it and every product saved without its images (masked until
    # the GridFS store fix made uploads work at all).
    "images",
)


def _clean_image_urls(v):
    """Shared ProductCreate/ProductUpdate images validator: trim, drop blanks,
    cap 12 entries, each a plausible URL (http(s) absolute or app-relative).
    Returns None for None (field absent) so exclude_unset semantics hold."""
    if v is None:
        return None
    if not isinstance(v, list):
        raise ValueError("images must be a list of URLs")
    out = []
    for item in v:
        s = str(item or "").strip()
        if not s:
            continue
        if len(s) > 600:
            raise ValueError("image URL too long (max 600 chars)")
        if not (
            s.startswith("http://") or s.startswith("https://") or s.startswith("/")
        ):
            raise ValueError(f"image URL must be absolute or app-relative: '{s[:60]}'")
        out.append(s)
        if len(out) > 12:
            raise ValueError("at most 12 images per product")
    return out


# Unification step-9: the ONE canonical product-create door. The FORM
# (POST /products) and BULK (/products/bulk-create) paths build their canonical
# payload here and delegate to product_master.create_via_door so the registry is
# the rulebook at this door too (strict required-field validation), while the
# persisted {product_id, sku} response shape + the additive FORM-only identity
# columns (CL/spectacle power, variant, discount_category) are preserved.
from ..services import product_master as _pm

# FORM/BULK-only optional top-level columns the canonical core doesn't model but
# this door has always persisted (POS power-grid reads cl_power/sph/base_curve
# top-level; discount_category drives the POS cap tier). Passed to the core as
# `extra_fields` so they round-trip onto the spine without re-deriving anything.
_FORM_EXTRA_FIELDS = ("variant",) + _OPTIONAL_PRODUCT_FIELDS


def _refresh_collections_after_product(created_or_updated) -> None:
    """Recompute SMART collection membership after a product create/update
    (step-13). The new/edited product's tags/category/brand may change which
    SMART collections it belongs to. FULLY fail-soft: never raises into the
    create/update path; a no-op when there is no live DB."""
    try:
        from ..dependencies import get_db as _get_db_dep
        from ..services import collection_materializer as _mat

        conn = _get_db_dep()
        if conn is not None and getattr(conn, "is_connected", False):
            _mat.refresh_for_product(conn.db, created_or_updated)
    except Exception:  # noqa: BLE001 - membership refresh must never block a write
        pass


def _canonical_door_payload(
    product: "ProductCreate", *, as_draft: bool = False
) -> dict:
    """Map a validated flat ProductCreate into the canonical create payload the
    product_master door expects (category + attributes + pricing + identity).

    `as_draft` (Hub Phase 0) flows through to the canonical door so an incomplete
    row persists as catalog_status=DRAFT instead of 422'ing -- still gated by the
    brand+model+category draft floor inside product_master."""
    return {
        "category": product.category,
        "attributes": dict(product.attributes or {}),
        "mrp": product.mrp,
        "offer_price": product.offer_price,
        "sku": product.sku,
        "cost_price": product.cost_price,
        "as_draft": bool(as_draft),
        "discount_category": product.discount_category,
        "hsn_code": product.hsn_code,
        "gst_rate": product.gst_rate,
        "tags": product.tags,
        # Flat identity columns -- normalise_door_payload folds these into the
        # registry's attribute keys (brand->brand_name, model->model_no,
        # color->colour_code) so the required-field gate sees them.
        "brand": product.brand,
        "model": product.model,
        "color": product.color,
        "size": product.size,
    }


def _form_extra_fields(product: "ProductCreate") -> dict:
    """The additive FORM-only top-level columns to persist on the spine
    (variant + the CL/spectacle identity + discount_category). None values are
    dropped by the core; canonical keys are never overridden."""
    out: dict = {}
    for f in _FORM_EXTRA_FIELDS:
        v = getattr(product, f, None)
        if v is not None:
            out[f] = v
    return out


def _resolve_sync_to_shopify(product: "ProductCreate", db) -> bool:
    """The `sync_to_shopify` INTENT to stamp on a new spine product.

    NOTE: nothing pushes to Shopify from IMS anymore (IMS->Shopify is
    retired; the BVI app owns Shopify) -- the stamp records the owner's
    intent so the FUTURE BVI-side push knows which products to list.

    An explicit payload value wins; when omitted (None) the brand's
    Brand Master `sync_to_shopify_default` decides (case-insensitive name
    match). FAIL-SOFT: unknown brand / no db / read trouble -> False
    (never sync by accident)."""
    explicit = getattr(product, "sync_to_shopify", None)
    if explicit is not None:
        return bool(explicit)
    try:
        from ..dependencies import get_db as _get_db_dep
        from ..services import catalog_dictionary as _cd

        db = db if db is not None else _get_db_dep()
        if db is not None and getattr(db, "is_connected", False):
            return _cd.load_brand_sync_default(db, product.brand)
    except Exception:  # noqa: BLE001 - intent stamp must never block a create
        pass
    return False


def _create_via_canonical_door(
    product: "ProductCreate", current_user: dict, *, source: str, as_draft: bool = False
) -> dict:
    """Delegate a FORM/BULK create to the ONE canonical product_master door.

    Maps ProductMasterError -> the same HTTP codes this router already used
    (422 validation / 400 MRP-or-SKU). Returns the created canonical spine doc
    (with product_id + sku). Assumes the repo is available (caller checked).

    `as_draft` (Hub Phase 0) lets an incomplete product persist as a DRAFT."""
    from ..dependencies import (
        get_audit_repository as _get_audit_repository,
        get_db as _get_db_dep,
    )

    db = _get_db_dep()
    variant_repo = None
    try:
        if db is not None and getattr(db, "is_connected", False):
            from database.repositories.catalog_variant_repository import (
                CatalogVariantRepository,
            )

            variant_repo = CatalogVariantRepository(
                db.get_collection("catalog_variants")
            )
    except Exception:  # noqa: BLE001 - mirror is fail-soft; never block a create
        variant_repo = None

    # Additive door columns + the resolved Shopify-sync INTENT (explicit
    # payload value, else the brand's Brand Master default, fail-soft False).
    extra = _form_extra_fields(product)
    extra["sync_to_shopify"] = _resolve_sync_to_shopify(product, db)

    try:
        return _pm.create_via_door(
            _canonical_door_payload(product, as_draft=as_draft),
            source=source,
            actor=current_user.get("user_id"),
            # Cataloguer attribution: the JWT already carries the username, so
            # the display name is stamped without a users lookup.
            actor_name=current_user.get("username"),
            extra_fields=extra,
            product_repo=get_product_repository(),
            variant_repo=variant_repo,
            audit_repo=_get_audit_repository(),
            db=db,
        )
    except _pm.ProductMasterError as err:
        raise HTTPException(
            status_code=err.status, detail=_pm_error_detail(err)
        ) from err


def _build_product_data(product: "ProductCreate", created_by, created_by_name=None) -> dict:
    """Map a validated ProductCreate into the persisted product doc.

    Single source of truth for the create payload shape, shared by the single
    create_product path and the batch bulk_create_products path so the two can
    never drift. Assumes the caller has ALREADY run _validate_category_or_422
    (so product.category is normalized) and _assert_mrp_ge_offer.

    HSN / GST: an explicit value wins; otherwise fall back to the canonical
    category->(hsn, rate) table so the master rate a product is created with
    equals what POS bills it (see api/services/gst_rates.py).
    """
    hsn_code = product.hsn_code or hsn_for_category(product.category)
    if product.gst_rate is not None:
        gst_rate = product.gst_rate
    else:
        gst_rate = gst_rate_for_category(product.category)

    product_data = {
        "sku": product.sku,
        "category": product.category,
        "brand": product.brand,
        "model": product.model,
        "variant": product.variant,
        "color": product.color,
        "size": product.size,
        "mrp": product.mrp,
        "offer_price": product.offer_price,
        "hsn_code": hsn_code,
        "gst_rate": gst_rate,
        "attributes": product.attributes or {},
        "is_active": True,
        "created_by": created_by,
    }
    # Cataloguer attribution (additive): display name alongside the id, matching
    # the canonical door's normalise_payload stamp.
    if created_by_name:
        product_data["created_by_name"] = created_by_name

    # Persist optional identity fields top-level only when provided (additive).
    for _f in _OPTIONAL_PRODUCT_FIELDS:
        _v = getattr(product, _f, None)
        if _v is not None:
            product_data[_f] = _v

    return product_data


class ProductCreate(BaseModel):
    # SKU is AUTO-MINTED by the canonical door (product_master.mint_unique_sku)
    # when not supplied, so it is OPTIONAL. A supplied (legacy/imported) SKU is
    # still accepted as-is and never re-minted. The FE no longer fabricates a
    # client-side SKU; it omits this field so the clean semantic SKU is minted.
    sku: Optional[str] = None
    category: str
    brand: str
    model: str
    variant: Optional[str] = None
    color: Optional[str] = None
    size: Optional[str] = None
    mrp: float = Field(..., gt=0)
    offer_price: float = Field(..., gt=0)
    # Hub Phase 0: the per-unit COST that purchase needs. Required for a product
    # to reach catalog "done" (catalog_status ACTIVE) and be purchasable; it is
    # NOT a create-blocker -- a create without it succeeds and lands DRAFT with
    # cost_price named in done_gaps (cost is often only known later, at GRN).
    cost_price: Optional[float] = Field(default=None, ge=0)
    hsn_code: Optional[str] = None
    # Persisted as `gst_rate` — the key seed data, reports and billing all
    # read. Was previously `tax_rate`, which no reader looked at.
    gst_rate: Optional[float] = None
    attributes: Optional[dict] = None
    # Governed product tags (step-12). Accepts a list or a comma-separated
    # string; normalised (lowercase/trim/dedupe) server-side via the canonical
    # door so FORM/BULK/CATALOG all yield an identical `tags` array. Tags back
    # the Shopify-shape smart-collection tag rules (step-13).
    tags: Optional[Union[List[str], str]] = None
    # BVI-10 fix: discount_category drives the pricing-cap tier (MASS/PREMIUM/
    # LUXURY/NON_DISCOUNTABLE). Was silently dropped on create so POS always
    # fell back to MASS 15% even for LUXURY products. Optional + additive;
    # None means the POS fallback reads the product's `category` field instead.
    discount_category: Optional[str] = None

    @field_validator("discount_category", mode="before")
    @classmethod
    def _validate_discount_category(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        normed = str(v).strip().upper()
        if normed not in _VALID_DISCOUNT_CATEGORIES:
            raise ValueError(
                f"Invalid discount_category '{v}'. "
                f"Allowed: {sorted(_VALID_DISCOUNT_CATEGORIES)}"
            )
        return normed

    # ---- Contact-lens (CL) identity fields. All optional + additive. ----
    cl_series: Optional[str] = None
    modality: Optional[str] = None
    base_curve: Optional[float] = None
    diameter: Optional[float] = None
    cl_power: Optional[float] = None
    cl_cyl: Optional[float] = None
    cl_axis: Optional[int] = Field(default=None, ge=0, le=180)
    cl_add: Optional[float] = None
    pack_size: Optional[int] = Field(default=None, ge=1)
    # ---- Spectacle-lens power identity (drives the stock power grid). Additive. ----
    sph: Optional[float] = None
    cyl: Optional[float] = None
    axis: Optional[int] = Field(default=None, ge=0, le=180)
    add: Optional[float] = None
    # ---- Product images (self-hosted /products/image URLs uploaded before
    # create; the door persists them onto the spine via extra_fields). ----
    images: Optional[List[str]] = None

    @field_validator("images", mode="before")
    @classmethod
    def _validate_images(cls, v):
        return _clean_image_urls(v)

    # Shopify-sync INTENT for the new product. NOTE: nothing pushes to
    # Shopify from IMS anymore (IMS->Shopify is retired; the BVI app owns
    # Shopify) -- this stamps the owner's intent for the FUTURE BVI-side
    # push. None (default) = resolve from the brand's Brand Master
    # `sync_to_shopify_default`; an explicit true/false is honoured as-is.
    sync_to_shopify: Optional[bool] = None


class ProductUpdate(BaseModel):
    category: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    color: Optional[str] = None
    # Hub Phase 0: a partial attributes patch. This is the ONLY spine-restamp
    # door, and several categories carry their catalog-done required fields IN
    # attributes, not in the flat brand/model/color columns the overlay covers --
    # CONTACT_LENS/COLORED_CONTACT_LENS need power + expiry_date, OPTICAL_LENS
    # needs index + coating. Without an attributes channel a DRAFT missing only
    # one of those could NEVER be completed/promoted here. Deep-merged onto the
    # existing attributes before the restamp so the done-rule sees the filled gap.
    attributes: Optional[dict] = None
    mrp: Optional[float] = Field(None, gt=0)
    offer_price: Optional[float] = Field(None, gt=0)
    # Hub Phase 0: editing cost_price can complete a DRAFT (auto-flip ACTIVE) or,
    # if cleared on a live row, trip the never-demote guard.
    cost_price: Optional[float] = Field(None, ge=0)
    hsn_code: Optional[str] = None
    gst_rate: Optional[float] = None
    is_active: Optional[bool] = None
    # Governed product tags (step-12). Same normalisation as create; an explicit
    # empty list clears all tags.
    tags: Optional[Union[List[str], str]] = None
    # BVI-10 fix: allow updating the discount cap tier on existing products.
    discount_category: Optional[str] = None

    @field_validator("discount_category", mode="before")
    @classmethod
    def _validate_discount_category(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        normed = str(v).strip().upper()
        if normed not in _VALID_DISCOUNT_CATEGORIES:
            raise ValueError(
                f"Invalid discount_category '{v}'. "
                f"Allowed: {sorted(_VALID_DISCOUNT_CATEGORIES)}"
            )
        return normed

    # Scan-to-sell barcode persisted on the product master. Moved here from the
    # retired (unvalidated) /admin/products PUT so the Inventory "Save barcode"
    # action writes through the SAME validated update path as every other field.
    barcode: Optional[str] = None
    # Catalog Manager edit-in-place: the two QuickAdd-captured fields the update
    # schema lacked. `description` mirrors onto the catalog_products twin below
    # (same fail-soft block as price/gst); `weight` is grams, matching the
    # create form's top-level field.
    description: Optional[str] = None
    weight: Optional[float] = Field(None, ge=0)
    # Product images (replaces the whole array; same validation as create).
    images: Optional[List[str]] = None

    @field_validator("images", mode="before")
    @classmethod
    def _validate_images(cls, v):
        return _clean_image_urls(v)

    # ---- Per-product reorder configuration. Moved here from the retired
    # /admin/products PUT (the Reorder dashboard's only writer) so reorder
    # settings persist through the validated path. All optional + additive. ----
    reorder_point: Optional[int] = Field(None, ge=0)
    # ge=-1: -1 is the owner's "no auto-reorder" sentinel (reorder_policy.py),
    # so the Reorder dashboard can explicitly disable a product again.
    reorder_quantity: Optional[int] = Field(None, ge=-1)
    max_stock: Optional[int] = Field(None, ge=0)
    lead_time_days: Optional[int] = Field(None, ge=0)
    # ---- Contact-lens (CL) identity fields. All optional + additive. ----
    cl_series: Optional[str] = None
    modality: Optional[str] = None
    base_curve: Optional[float] = None
    diameter: Optional[float] = None
    cl_power: Optional[float] = None
    cl_cyl: Optional[float] = None
    cl_axis: Optional[int] = Field(None, ge=0, le=180)
    cl_add: Optional[float] = None
    pack_size: Optional[int] = Field(None, ge=1)
    # ---- Spectacle-lens power identity (drives the stock power grid). Additive. ----
    sph: Optional[float] = None
    cyl: Optional[float] = None
    axis: Optional[int] = Field(None, ge=0, le=180)
    add: Optional[float] = None


# ----------------------------------------------------------------------------
# Bulk CREATE schema (Rapid Grid -- Phase B of the product-add redesign)
# ----------------------------------------------------------------------------
# Accepts many ProductCreate-shaped rows in one call. The in-app Rapid Grid
# (frontend RapidGridPage) is the only caller; there is NO CSV / file import.
# Each row reuses the SAME validators + persist helper as the single-create
# path -- valid rows are created, invalid rows are skipped and reported.


class BulkCreateRequest(BaseModel):
    """A batch of products to create. Each item is ProductCreate-shaped.

    Capped at 500 rows per call -- the Rapid Grid is a manual, in-app entry
    surface (the owner forbade file import), so a single batch is never huge;
    the cap is a guard against an accidental/abusive oversized POST.
    """

    products: List[ProductCreate] = Field(..., min_length=1, max_length=500)
    # Hub Phase 0: a batch flag. as_draft=true persists incomplete rows as
    # catalog_status=DRAFT (above the brand+model+category floor) instead of
    # failing them; default (strict) keeps today's loud per-row rejection on
    # missing required attributes. A row without cost_price is NOT failed in
    # either mode -- it persists DRAFT with cost_price named in done_gaps.
    as_draft: bool = False


# ----------------------------------------------------------------------------
# Bulk pricing / offer schemas
# ----------------------------------------------------------------------------
# Both bulk operations share the same scope filter (category / brand / store)
# and a dry-run-first contract: `apply` defaults to False so the caller always
# previews the per-row before/after + cap classification before any write.


class BulkScopeFilter(BaseModel):
    """Optional filters narrowing the set of products a bulk op touches.
    All combine with AND. `store_id` restricts to products that have stock at
    that store (price itself is a global product-master field, not per-store)."""

    category: Optional[str] = None
    brand: Optional[str] = None
    store_id: Optional[str] = None
    # Hard ceiling on the rows a single op can touch -- a guard against an empty
    # filter sweeping the whole catalog by accident.
    limit: int = Field(default=5000, ge=1, le=20000)


class BulkPriceRequest(BulkScopeFilter):
    """Apply a percentage or flat-amount delta to the offer (selling) price,
    the MRP, or both, across the filtered set.

      mode      = "PERCENT" | "FLAT"
      target    = "OFFER" | "MRP" | "BOTH"
      amount    = signed delta. PERCENT: +5 raises 5%, -5 cuts 5%.
                  FLAT: +100 adds Rs 100, -100 subtracts Rs 100.

    Dry-run by default; pass apply=True to commit the rows that pass validation.
    """

    mode: str = Field(default="PERCENT")
    target: str = Field(default="OFFER")
    amount: float = Field(...)
    apply: bool = False
    reason: Optional[str] = None

    @field_validator("amount")
    @classmethod
    def _amount_finite(cls, v: float) -> float:
        # BUG-108 residual: amount was unbounded, so NaN/Infinity slipped through
        # (the handler's `new_offer <= 0` guard can't catch NaN -> mrp/offer_price
        # written as NaN). Reject non-finite up front. Left otherwise unbounded so
        # a legitimate large flat delta still applies.
        import math

        if v is None or not math.isfinite(v):
            raise ValueError("amount must be a finite number")
        return v


class BulkOfferRequest(BulkScopeFilter):
    """Create or clear an offer across the filtered set.

      action = "SET"   -> set offer_price. Either:
                            - discount_percent (e.g. 10 -> offer = MRP * 0.90), or
                            - offer_price (an explicit flat price)
               "CLEAR" -> reset offer_price back up to MRP (removes the offer).

    Dry-run by default; pass apply=True to commit the rows that pass validation.
    """

    action: str = Field(default="SET")
    discount_percent: Optional[float] = Field(default=None, ge=0, le=100)
    offer_price: Optional[float] = Field(default=None, gt=0)
    apply: bool = False
    reason: Optional[str] = None


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("")
async def list_products(
    category: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    tag: Optional[str] = Query(
        None, description="Filter to products carrying this normalised tag"
    ),
    created_by: Optional[str] = Query(
        None,
        description=(
            "Cataloguer attribution filter: only products created by this "
            "user_id (the value stamped in each product's created_by)."
        ),
    ),
    store_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    is_active: Optional[str] = Query(
        None,
        pattern="^(true|false|all)$",
        description=(
            "Tri-state active filter. Absent = legacy per-path behaviour "
            "(default path returns inactive too; search/brand/category paths "
            "are active-only). 'true'/'false' filter explicitly; 'all' returns "
            "everything (Catalog Manager 'include inactive')."
        ),
    ),
    current_user: dict = Depends(get_current_user),
):
    """List active catalog products with optional search / brand / category
    filters. `store_id` is accepted (kept for backwards-compat) but does NOT
    filter the result set - the canonical Stock Ledger at /inventory/stock
    is the per-store on-hand view. POS search uses this endpoint so a SKU
    that's in the catalog can always be looked up at any store the user has
    access to."""
    from ..services.cache import cache
    from ..services.product_master import resolve_category

    # Direct-call safety: several unit suites invoke this handler as a plain
    # function WITHOUT the newer kwargs, so `created_by` arrives as its
    # truthy FastAPI Query default object. Treat anything that is not a
    # non-blank string as "absent" (HTTP callers always deliver a str/None).
    if not isinstance(created_by, str) or not created_by.strip():
        created_by = None

    # Cataloguer-attribution posture (panel finding 4): attribution is a
    # MANAGER surface. Non-manager roles get neither the created_by filter
    # nor the attribution fields on the returned docs -- otherwise any staff
    # account could reconstruct the exact roster + per-user counts that
    # GET /products/cataloguers deliberately withholds from them.
    can_see_attribution = bool(
        set(current_user.get("roles") or [])
        & ({"SUPERADMIN"} | set(_CATALOGUER_VIEW_ROLES))
    )
    if created_by and not can_see_attribution:
        raise HTTPException(
            status_code=403,
            detail="Your role does not have access to the cataloguer filter",
        )

    # Category-filter fix: products store CANONICAL categories (SUNGLASS/FRAME),
    # but callers send short codes (SG/FR) or legacy plurals. Normalise the param
    # to canonical when resolvable (fail-open pass-through otherwise) BEFORE the
    # cache key so both spellings share one cache entry.
    if category:
        category = resolve_category(category) or category

    # Build cache key from query params. NOTE: store_id is cache-key/back-compat
    # only -- list_products is a GLOBAL catalog lookup (POS must find any SKU at
    # any store the user can access), so it is intentionally NOT store-scoped and
    # must not call validate_store_access here.
    active_store = store_id or current_user.get("active_store_id", "")
    # The caller's attribution tier is part of the key: manager responses
    # carry created_by/updated_by fields that are STRIPPED for staff, and the
    # two shapes must never cross-serve from the cache.
    _tier = "mgr" if can_see_attribution else "staff"
    cache_key = (
        f"products:{active_store}:{category}:{brand}:{search}:{tag}:{skip}:{limit}"
        f":{is_active}:{created_by}:{_tier}"
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    repo = get_product_repository()

    if repo is not None:
        # Tri-state is_active resolution. When the param is ABSENT each path
        # keeps its legacy behaviour EXACTLY: the filtered paths force
        # active-only (repo default True) and the default no-filter path
        # returns inactive products too (no filter). An explicit value applies
        # uniformly: true -> active only, false -> inactive only, all -> no
        # filter.
        if is_active is None:
            filtered_active: Optional[bool] = True
            default_active: Optional[bool] = None
        elif is_active == "all":
            filtered_active = None
            default_active = None
        else:
            filtered_active = is_active == "true"
            default_active = filtered_active

        if search:
            products = repo.search_products(
                search,
                category,
                is_active=filtered_active,
                created_by=created_by,
                skip=skip,
                limit=limit,
            )
            total_count = repo.count_search_products(
                search, category, is_active=filtered_active, created_by=created_by
            )
        elif brand:
            products = repo.find_by_brand(
                brand,
                category,
                is_active=filtered_active,
                created_by=created_by,
                skip=skip,
                limit=limit,
            )
            total_count = repo.count_by_brand(
                brand, category, is_active=filtered_active, created_by=created_by
            )
        elif category:
            products = repo.find_by_category(
                category,
                is_active=filtered_active,
                created_by=created_by,
                skip=skip,
                limit=limit,
            )
            total_count = repo.count_by_category(
                category, is_active=filtered_active, created_by=created_by
            )
        else:
            _default_filter = (
                {} if default_active is None else {"is_active": default_active}
            )
            if created_by:
                _default_filter["created_by"] = created_by
            products = repo.find_many(_default_filter, skip=skip, limit=limit)
            total_count = repo.count(_default_filter)

        # Tag filter (step-12). Normalise the requested tag the SAME way tags are
        # stored, then keep products whose normalised `tags` array contains it.
        if tag:
            from ..services import product_master as _pm_tags

            wanted = _pm_tags.normalise_tags(tag)
            if wanted:
                want = wanted[0]
                products = [
                    p
                    for p in products
                    if want in _pm_tags.normalise_tags(p.get("tags"))
                ]

        # NOTE: `store_id` is INTENTIONALLY a no-op here for the product
        # search. The earlier in-place filter attempted `async for` on a
        # SYNC pymongo cursor (TypeError: 'Cursor' object is not async
        # iterable) -> the wrapping `except Exception: pass` swallowed it
        # silently, leaving the list unfiltered in prod. We now skip that
        # filter on purpose so POS search returns every catalog product
        # the store CAN sell (matching /inventory/stock semantics: every
        # active product appears, on_hand may be zero). This keeps the
        # QA-reported invariant - if POS shows a SKU at a store, the
        # Stock Ledger MUST show the same SKU at the same store. Per-SKU
        # on-hand quantities are surfaced via /inventory/stock, not here.

        # Owner bug 2026-07-05 ("unable to see images of the products"): the
        # POS grid + other consumers read the SINGULAR `image_url`, but spine
        # docs carry an `images` ARRAY -- so every image silently rendered as
        # the placeholder icon even though the files load fine. Stamp the
        # first image as the alias here (single-brain fix for every consumer
        # of this list); an existing explicit image_url is never overwritten.
        for p in products:
            if not p.get("image_url"):
                imgs = p.get("images")
                if isinstance(imgs, list) and imgs and isinstance(imgs[0], str):
                    p["image_url"] = imgs[0]

        # Attribution stripping for non-manager callers (panel finding 4):
        # covers the newly stamped names AND the pre-existing raw created_by
        # user_id, so the roster stays non-derivable from this endpoint.
        if not can_see_attribution:
            for p in products:
                for _f in (
                    "created_by",
                    "created_by_name",
                    "updated_by",
                    "updated_by_name",
                ):
                    p.pop(_f, None)

        # LEGACY CONTRACT: `total` stays the PAGE length (pinned by existing
        # consumers/tests). `total_count` is the additive true pre-slice count
        # of the applied repo filter, feeding the Catalog Manager pagination.
        total = len(products)
        result = {"products": products, "total": total, "total_count": total_count}
        cache.set(cache_key, result, ttl=cache.TTL_MEDIUM)
        return result

    return {"products": [], "total": 0, "total_count": 0}


@router.post("", status_code=201)
async def create_product(
    product: ProductCreate,
    current_user: dict = Depends(require_roles(*_CATALOG_ROLES)),
    as_draft: bool = False,
):
    """Create a new product.

    Unification step-9: delegates the validate + create CORE to the ONE
    canonical product-create door (product_master.create_via_door, source=FORM)
    so this door enforces the SAME registry rulebook as /products/bulk-create
    and /catalog/products. Category, MRP>=offer, and category->GST/HSN are still
    enforced; STRICT now adds the registry's category-conditional required-field
    gate (e.g. a FRAME without colour_code is rejected at entry). Auth/RBAC and
    the {product_id, sku} response shape are unchanged.

    Hub Phase 0: `?as_draft=true` lets an incomplete product persist as
    catalog_status=DRAFT (still above the brand+model+category floor) instead of
    422'ing on a missing required attribute. cost_price is part of the catalog-
    done rule but NOT a create-blocker: a STRICT create without it succeeds and
    lands DRAFT (not purchasable until cost is filled). The persisted row always
    carries catalog_status + done_gaps so the Buy Desk can name what is missing.
    """
    # Block save when category is blank/null/missing (server-side guard for the
    # GST-default bug: an uncategorized product would otherwise fall back to a
    # default GST rate). Normalizes the category to the validated value.
    product.category = _validate_category_or_422(product.category)

    # Statutory P3: a product must never persist a blank HSN. Auto-mint it from
    # the category when not supplied, or 400 if the category has no canonical
    # HSN. Stamp the resolved value so the canonical door + GSTR-1/Tally carry it.
    product.hsn_code = _resolve_hsn_or_400(product.category, product.hsn_code)

    # Validate MRP >= Offer Price via the shared pricing_caps validator.
    _assert_mrp_ge_offer(product.mrp, product.offer_price)

    if product.modality and product.modality not in CL_MODALITIES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid modality. Allowed: {', '.join(CL_MODALITIES)}",
        )

    # STRICT registry gate (step-9) -- runs at this door regardless of DB state so
    # an incomplete product (e.g. FRAME w/o colour_code) is rejected the same way
    # in stub mode as it is with a live repo. The create path below re-validates
    # via the same core; this is the early loud 422 with the offending field.
    # With as_draft the gate relaxes to the DRAFT floor (brand+model+category).
    try:
        _pm.build_canonical_product(
            _canonical_door_payload(product, as_draft=as_draft), source="FORM"
        )
    except _pm.ProductMasterError as err:
        raise HTTPException(
            status_code=err.status, detail=_pm_error_detail(err)
        ) from err

    repo = get_product_repository()

    if repo is not None:
        created = _create_via_canonical_door(
            product, current_user, source="FORM", as_draft=as_draft
        )
        if created:
            # Invalidate product cache for this store
            from ..services.cache import cache

            cache.delete_pattern(
                f"products:{current_user.get('active_store_id', '')}:*"
            )
            # Step-13: recompute SMART collections (fail-soft, never blocks).
            _refresh_collections_after_product(created)
            return {"product_id": created["product_id"], "sku": created["sku"]}

        raise HTTPException(status_code=500, detail="Failed to create product")

    # Stub mode (no repo): mint the canonical SKU when none was supplied so the
    # response still carries a clean SKU (mirrors the canonical door's minting).
    stub_sku = product.sku
    if not stub_sku:
        try:
            stub_sku = _pm.mint_unique_sku(
                product.category, dict(product.attributes or {})
            )
        except Exception:  # noqa: BLE001 - never block the (stub) create
            stub_sku = None
    return {"product_id": str(uuid.uuid4()), "sku": stub_sku}


# ============================================================================
# BULK CREATE  (Rapid Grid -- Phase B of the product-add redesign)
# ============================================================================
# POST /products/bulk-create -- create many products in one call. Each row is
# validated with the SAME helpers as the single-create path
# (_validate_category_or_422, _assert_mrp_ge_offer, _build_product_data so the
# HSN/GST defaults match). Valid rows are created; invalid rows are SKIPPED and
# reported with per-row error reasons. SKUs are deduped within the batch and
# against existing products. There is NO CSV / file import -- the in-app Rapid
# Grid is the only caller.


def _validate_bulk_row(
    product: ProductCreate, seen_skus: set, *, as_draft: bool = False
) -> List[str]:
    """Validate one bulk-create row WITHOUT raising. Returns a list of human
    error strings (empty == valid). Mirrors the single-create checks:
      - category present + recognized (_validate_category_or_422)
      - registry category-conditional required fields (STRICT, step-9) so a bulk
        row is rejected for the SAME incomplete payload the FORM door 422s on
      - MRP >= offer_price (_assert_mrp_ge_offer)
      - modality (CL) within the allowed set
      - SKU not duplicated earlier in THIS batch
    Normalizes product.category in place on success so the persist step uses
    the canonical value. (pydantic already enforced mrp/offer_price > 0 and the
    cl_axis/axis 0-180 + pack_size >= 1 ranges before this is reached.)

    Hub Phase 0: with `as_draft` the registry gate relaxes to the DRAFT floor
    (brand+model+category) so an incomplete row is accepted (persisted DRAFT)
    rather than reported as a failure -- mirroring the FORM door.
    """
    errors: List[str] = []

    # Category (reuse the single-create validator; capture its 422 message).
    category_ok = True
    try:
        product.category = _validate_category_or_422(product.category)
    except HTTPException as exc:
        category_ok = False
        errors.append(str(exc.detail))

    # Statutory P3: never persist a blank HSN. Auto-mint from category (or
    # capture the 400 when the category has no canonical HSN). Only attempt this
    # once the category resolved, and stamp the resolved value so the persist
    # step carries a valid HSN. Mirrors the single-create FORM door.
    if category_ok:
        try:
            product.hsn_code = _resolve_hsn_or_400(product.category, product.hsn_code)
        except HTTPException as exc:
            errors.append(str(exc.detail))

    # Registry required-field gate (step-9). Build the canonical payload the same
    # way the FORM door does so a missing colour_code / power / expiry / cost is
    # reported here exactly as the FORM door would 422 on it (or, with as_draft,
    # relaxed to the floor). Captures the message instead of raising.
    try:
        _pm.build_canonical_product(
            _canonical_door_payload(product, as_draft=as_draft), source="BULK"
        )
    except _pm.ProductMasterError as exc:
        errors.append(exc.message)
    except Exception:  # noqa: BLE001 - any builder error is a row-level failure
        errors.append("Product failed validation")

    # MRP >= offer_price (reuse the single-create validator; capture its 400).
    try:
        _assert_mrp_ge_offer(product.mrp, product.offer_price)
    except HTTPException as exc:
        errors.append(str(exc.detail))

    if product.modality and product.modality not in CL_MODALITIES:
        errors.append(f"Invalid modality. Allowed: {', '.join(CL_MODALITIES)}")

    sku_norm = str(product.sku or "").strip()
    if not sku_norm:
        errors.append("SKU is required")
    elif sku_norm in seen_skus:
        errors.append("Duplicate SKU within this batch")

    return errors


@router.post("/bulk-create", status_code=201)
async def bulk_create_products(
    body: BulkCreateRequest,
    current_user: dict = Depends(require_roles(*_CATALOG_ROLES)),
):
    """Create many products in one call (Rapid Grid).

    For each row: validate via the shared single-create validators, skip the
    invalid ones (reporting why), and create only the valid rows. Returns a
    per-row result array plus a summary. SKUs are deduped within the batch and
    against existing products. Catalog-write gated (ADMIN / CATALOG_MANAGER;
    SUPERADMIN auto-passes)."""
    repo = get_product_repository()

    results: List[dict] = []
    created_count = 0
    failed_count = 0
    # SKUs already accepted/seen in THIS batch (in-batch dedupe). Seeded as we
    # iterate so an earlier row "wins" a duplicate SKU and later ones fail.
    seen_skus: set = set()

    for index, product in enumerate(body.products):
        sku_norm = str(product.sku or "").strip()
        errors = _validate_bulk_row(product, seen_skus, as_draft=body.as_draft)

        # Cross-batch dedupe: reject a SKU that already exists in the catalog.
        # Only checked once the SKU is otherwise valid (avoids a pointless DB
        # hit on a row that's already failing for another reason).
        if not errors and repo is not None:
            try:
                if repo.find_by_sku(sku_norm) is not None:
                    errors.append("Product with this SKU already exists")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[BULK-CREATE] SKU lookup failed for %s: %s", sku_norm, exc
                )
                errors.append("Could not verify SKU uniqueness")

        if errors:
            failed_count += 1
            results.append(
                {"index": index, "ok": False, "errors": errors, "sku": sku_norm}
            )
            continue

        # Reserve the SKU in-batch BEFORE the write so a later duplicate fails
        # even if the create itself errors.
        seen_skus.add(sku_norm)

        if repo is None:
            # No DB (local dev / mock mode): echo a synthetic id so the shape is
            # stable. Mirrors create_product's no-repo fallback.
            created_count += 1
            results.append(
                {
                    "index": index,
                    "ok": True,
                    "errors": [],
                    "sku": product.sku,
                    "product_id": str(uuid.uuid4()),
                }
            )
            continue

        try:
            # Delegate to the ONE canonical product-create door (step-9,
            # source=BULK) so a bulk row is created through the SAME registry +
            # invariant core as the FORM door. The row was already strict-
            # validated above, so this should not raise; a stray
            # ProductMasterError is treated as a row-level create failure.
            created = _create_via_canonical_door(
                product, current_user, source="BULK", as_draft=body.as_draft
            )
        except HTTPException as exc:
            logger.warning(
                "[BULK-CREATE] create rejected for %s: %s", sku_norm, exc.detail
            )
            created = None
        except Exception as exc:  # noqa: BLE001
            logger.warning("[BULK-CREATE] create failed for %s: %s", sku_norm, exc)
            created = None

        if created:
            created_count += 1
            results.append(
                {
                    "index": index,
                    "ok": True,
                    "errors": [],
                    "sku": created.get("sku", product.sku),
                    "product_id": created.get("product_id"),
                }
            )
        else:
            failed_count += 1
            # Roll back the in-batch reservation so the SKU isn't wrongly
            # blocked for a (hypothetical) later retry row.
            seen_skus.discard(sku_norm)
            results.append(
                {
                    "index": index,
                    "ok": False,
                    "errors": ["Failed to create product"],
                    "sku": sku_norm,
                }
            )

    # Invalidate the product list cache once if anything was actually created.
    if created_count and repo is not None:
        try:
            from ..services.cache import cache

            cache.delete_pattern("products:*")
        except Exception:  # noqa: BLE001
            pass

    return {
        "summary": {
            "total": len(body.products),
            "created": created_count,
            "failed": failed_count,
        },
        "results": results,
    }


# ============================================================================
# BULK PRICING / OFFERS  (revenue-sensitive; dry-run-first + cap-enforced)
# ============================================================================
# POST /products/bulk-price  -- apply a % or flat delta to a filtered set.
# POST /products/bulk-offer  -- set/clear an offer (offer_price) on a set.
#
# Contract shared by both:
#   - Dry-run by default (apply=False): returns per-row before/after plus a
#     per-row cap classification. Mutates NOTHING.
#   - apply=True: commits ONLY the rows that pass validation (cap-respecting,
#     MRP >= offer_price). Rows that would VIOLATE a cap or the MRP rule are
#     skipped and reported with a machine reason + human message -- never
#     silently clamped (a clamp would hide a mis-scoped bulk op).
#   - Every committed row is written to the immutable `audit_logs` collection
#     via the audit repository (action BULK_PRICE_UPDATE / BULK_OFFER_UPDATE).
#   - Idempotent: re-running the same op when every row already sits at the
#     target value changes nothing (rows are reported "unchanged").
#
# The cap rules live in api/services/pricing_caps.py (single source of truth,
# shared so both endpoints classify identically).


def _round_money(value: float) -> float:
    """Round a rupee amount to 2 dp. Prices are stored as floats elsewhere in
    the catalog, so we keep paise precision rather than integer-rupee."""
    return round(float(value), 2)


def _store_scoped_product_ids(store_id: str) -> Optional[set]:
    """Set of product_ids that have at least one stock unit at `store_id`.
    Returns None if the stock collection is unavailable (so the caller falls
    back to NOT store-filtering rather than returning an empty set)."""
    try:
        from database.connection import get_db

        db = get_db()
        if not (db and db.is_connected):
            return None
        coll = db.get_collection("stock_units")
        if coll is None:
            return None
        return {
            pid for pid in coll.distinct("product_id", {"store_id": store_id}) if pid
        }
    except Exception:  # noqa: BLE001
        return None


def _fetch_scoped_products(flt: BulkScopeFilter) -> List[dict]:
    """Fetch active products matching the bulk scope filter.

    category / brand filter at the DB level; store_id restricts to products
    that have stock at that store. Returns [] when the repo is offline.
    """
    repo = get_product_repository()
    if repo is None:
        return []

    query: dict = {"is_active": True}
    if flt.category:
        query["category"] = flt.category
    if flt.brand:
        query["brand"] = flt.brand

    products = repo.find_many(query, limit=flt.limit)

    if flt.store_id:
        allowed = _store_scoped_product_ids(flt.store_id)
        if allowed is not None:
            products = [p for p in products if p.get("product_id") in allowed]
    return products


def _audit_bulk(
    action: str,
    user: dict,
    summary: dict,
    rows: List[dict],
) -> None:
    """Append one immutable audit entry summarising a committed bulk op, plus
    the per-row diffs (was -> now). Fail-soft: a logging failure never blocks
    the price change that already happened."""
    try:
        from ..dependencies import get_audit_repository

        audit = get_audit_repository()
        if audit is None:
            return
        audit.create(
            {
                "action": action,
                "entity_type": "product_price",
                "store_id": summary.get("store_id"),
                "user_id": user.get("user_id"),
                "severity": "WARNING",  # bulk price moves are notable events
                "timestamp": datetime.utcnow(),
                "created_at": datetime.utcnow(),
                "details": {
                    "summary": summary,
                    # Cap the embedded diff list so a 5,000-row op doesn't write
                    # a multi-MB audit doc; the summary always has full counts.
                    "changes": rows[:500],
                    "changes_truncated": len(rows) > 500,
                },
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[BULK-PRICING] audit log skipped: %s", exc)


class CloneVaryRequest(BaseModel):
    """Hub Phase 4 clone-and-vary: clone `source_id` across N attribute
    variations into N DRAFT variants. Each variation is a dict of attribute
    overrides, e.g. {"colour_code": "BLK", "size": "52"}."""

    source_id: str
    variations: List[dict] = Field(..., min_length=1, max_length=100)


@router.post("/clone-vary", status_code=201)
async def clone_vary_products(
    body: CloneVaryRequest,
    current_user: dict = Depends(require_roles(*_CATALOG_ROLES)),
):
    """Clone a product across colour/size/... variations into catalog_status=
    DRAFT variants (Hub Phase 4). Each variant inherits the source's catalog
    fields + attributes, applies the per-variation overrides, mints a unique SKU,
    and lands DRAFT for review -- never auto-published. The Phase-1 duplicate
    guard applies, so a variation matching an existing product is returned in
    `errors`, not created. Returns {source_id, created:[...], errors:[...]}.
    """
    repo = get_product_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Product repository unavailable")
    from ..dependencies import get_db as _get_db_dep, get_audit_repository

    try:
        return _pm.clone_and_vary(
            source_id=body.source_id,
            variations=body.variations,
            actor=current_user.get("user_id"),
            product_repo=repo,
            audit_repo=get_audit_repository(),
            db=_get_db_dep(),
        )
    except _pm.ProductMasterError as err:
        raise HTTPException(status_code=err.status, detail=err.message) from err


def _apply_delta(original: float, mode: str, amount: float) -> float:
    """Compute a new price from an original given a PERCENT or FLAT delta."""
    if mode == "PERCENT":
        return _round_money(original * (1.0 + amount / 100.0))
    return _round_money(original + amount)


@router.post("/bulk-price")
async def bulk_price_update(
    body: BulkPriceRequest,
    current_user: dict = Depends(require_roles(*_BULK_PRICING_ROLES)),
):
    """Apply a percentage or flat delta to MRP / offer / both across a filtered
    set of products. DRY-RUN by default (apply=False) -- returns per-row
    before/after and which rows would VIOLATE a discount cap or the MRP-rule.
    Pass apply=True to commit only the valid rows (violations are skipped with
    a reason, never clamped). Every committed change is audit-logged."""
    from ..services.pricing_caps import evaluate_offer_price, effective_discount_cap

    mode = (body.mode or "PERCENT").strip().upper()
    target = (body.target or "OFFER").strip().upper()
    if mode not in ("PERCENT", "FLAT"):
        raise HTTPException(status_code=400, detail="mode must be PERCENT or FLAT")
    if target not in ("OFFER", "MRP", "BOTH"):
        raise HTTPException(
            status_code=400, detail="target must be OFFER, MRP, or BOTH"
        )

    products = _fetch_scoped_products(body)

    rows: List[dict] = []
    committed: List[dict] = []
    counts = {"total": len(products), "valid": 0, "violations": 0, "unchanged": 0}

    repo = get_product_repository()

    for p in products:
        pid = p.get("product_id")
        old_mrp = float(p.get("mrp") or 0)
        old_offer = float(p.get("offer_price") or 0)
        disc_cat = p.get("discount_category") or p.get("category")
        brand = p.get("brand")

        new_mrp = old_mrp
        new_offer = old_offer
        if target in ("MRP", "BOTH"):
            new_mrp = _apply_delta(old_mrp, mode, body.amount)
        if target in ("OFFER", "BOTH"):
            new_offer = _apply_delta(old_offer, mode, body.amount)

        # Clamp obviously-broken negatives to a sentinel so the validator can
        # reject them with a clear reason rather than producing NaNs downstream.
        if new_mrp <= 0 or new_offer <= 0:
            verdict = {
                "ok": False,
                "reason": "INVALID_MRP",
                "message": "Resulting MRP / offer price must be greater than zero.",
                "effective_cap_pct": effective_discount_cap(disc_cat, brand),
                "implied_discount_pct": 0.0,
            }
        else:
            verdict = evaluate_offer_price(new_mrp, new_offer, disc_cat, brand)

        changed = _round_money(new_mrp) != _round_money(old_mrp) or _round_money(
            new_offer
        ) != _round_money(old_offer)

        row = {
            "product_id": pid,
            "sku": p.get("sku"),
            "brand": brand,
            "model": p.get("model"),
            "category": p.get("category"),
            "discount_category": p.get("discount_category"),
            "old_mrp": _round_money(old_mrp),
            "old_offer_price": _round_money(old_offer),
            "new_mrp": _round_money(new_mrp),
            "new_offer_price": _round_money(new_offer),
            "effective_cap_pct": verdict["effective_cap_pct"],
            "implied_discount_pct": verdict["implied_discount_pct"],
            "ok": verdict["ok"],
            "reason": verdict["reason"],
            "message": verdict["message"],
            "changed": changed,
        }

        if not verdict["ok"]:
            counts["violations"] += 1
        elif not changed:
            counts["unchanged"] += 1
        else:
            counts["valid"] += 1

        # Commit path: only valid + actually-changed rows are written.
        if body.apply and verdict["ok"] and changed and repo is not None and pid:
            try:
                ok = repo.update(
                    pid,
                    {
                        "mrp": _round_money(new_mrp),
                        "offer_price": _round_money(new_offer),
                        "price_updated_at": datetime.utcnow(),
                        "price_updated_by": current_user.get("user_id"),
                    },
                )
                row["applied"] = bool(ok)
                if ok:
                    committed.append(
                        {
                            "product_id": pid,
                            "sku": p.get("sku"),
                            "mrp": [row["old_mrp"], row["new_mrp"]],
                            "offer_price": [
                                row["old_offer_price"],
                                row["new_offer_price"],
                            ],
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[BULK-PRICING] update failed for %s: %s", pid, exc)
                row["applied"] = False
        rows.append(row)

    summary = {
        "operation": "bulk_price",
        "mode": mode,
        "target": target,
        "amount": body.amount,
        "store_id": body.store_id,
        "category": body.category,
        "brand": body.brand,
        "reason": body.reason,
        "applied": body.apply,
        "counts": counts,
        "committed": len(committed),
    }

    if body.apply and committed:
        _audit_bulk("BULK_PRICE_UPDATE", current_user, summary, committed)
        # Invalidate the product list cache so the catalog reflects new prices.
        try:
            from ..services.cache import cache

            cache.delete_pattern("products:*")
        except Exception:  # noqa: BLE001
            pass

    return {"dry_run": not body.apply, "summary": summary, "rows": rows}


@router.post("/bulk-offer")
async def bulk_offer_update(
    body: BulkOfferRequest,
    current_user: dict = Depends(require_roles(*_BULK_PRICING_ROLES)),
):
    """Create or clear an offer (offer_price) across a filtered set. SET takes
    either discount_percent (offer = MRP * (1 - pct/100)) or an explicit flat
    offer_price; CLEAR resets offer_price up to MRP. DRY-RUN by default; pass
    apply=True to commit only the cap-respecting rows (violations skipped with
    a reason). Every committed change is audit-logged."""
    from ..services.pricing_caps import evaluate_offer_price, effective_discount_cap

    action = (body.action or "SET").strip().upper()
    if action not in ("SET", "CLEAR"):
        raise HTTPException(status_code=400, detail="action must be SET or CLEAR")
    if action == "SET" and body.discount_percent is None and body.offer_price is None:
        raise HTTPException(
            status_code=400,
            detail="SET requires either discount_percent or offer_price",
        )

    products = _fetch_scoped_products(body)

    rows: List[dict] = []
    committed: List[dict] = []
    counts = {"total": len(products), "valid": 0, "violations": 0, "unchanged": 0}

    repo = get_product_repository()

    for p in products:
        pid = p.get("product_id")
        mrp = float(p.get("mrp") or 0)
        old_offer = float(p.get("offer_price") or 0)
        disc_cat = p.get("discount_category") or p.get("category")
        brand = p.get("brand")

        if action == "CLEAR":
            # Clearing an offer means selling at MRP -> no discount, always valid.
            new_offer = _round_money(mrp)
        elif body.discount_percent is not None:
            new_offer = _round_money(mrp * (1.0 - body.discount_percent / 100.0))
        else:
            new_offer = _round_money(body.offer_price)

        if mrp <= 0 or new_offer <= 0:
            verdict = {
                "ok": False,
                "reason": "INVALID_MRP",
                "message": "MRP and resulting offer price must be greater than zero.",
                "effective_cap_pct": effective_discount_cap(disc_cat, brand),
                "implied_discount_pct": 0.0,
            }
        else:
            verdict = evaluate_offer_price(mrp, new_offer, disc_cat, brand)

        changed = _round_money(new_offer) != _round_money(old_offer)

        row = {
            "product_id": pid,
            "sku": p.get("sku"),
            "brand": brand,
            "model": p.get("model"),
            "category": p.get("category"),
            "discount_category": p.get("discount_category"),
            "mrp": _round_money(mrp),
            "old_offer_price": _round_money(old_offer),
            "new_offer_price": _round_money(new_offer),
            "effective_cap_pct": verdict["effective_cap_pct"],
            "implied_discount_pct": verdict["implied_discount_pct"],
            "ok": verdict["ok"],
            "reason": verdict["reason"],
            "message": verdict["message"],
            "changed": changed,
        }

        if not verdict["ok"]:
            counts["violations"] += 1
        elif not changed:
            counts["unchanged"] += 1
        else:
            counts["valid"] += 1

        if body.apply and verdict["ok"] and changed and repo is not None and pid:
            try:
                ok = repo.update(
                    pid,
                    {
                        "offer_price": _round_money(new_offer),
                        "price_updated_at": datetime.utcnow(),
                        "price_updated_by": current_user.get("user_id"),
                    },
                )
                row["applied"] = bool(ok)
                if ok:
                    committed.append(
                        {
                            "product_id": pid,
                            "sku": p.get("sku"),
                            "offer_price": [
                                row["old_offer_price"],
                                row["new_offer_price"],
                            ],
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[BULK-OFFER] update failed for %s: %s", pid, exc)
                row["applied"] = False
        rows.append(row)

    summary = {
        "operation": "bulk_offer",
        "action": action,
        "discount_percent": body.discount_percent,
        "offer_price": body.offer_price,
        "store_id": body.store_id,
        "category": body.category,
        "brand": body.brand,
        "reason": body.reason,
        "applied": body.apply,
        "counts": counts,
        "committed": len(committed),
    }

    if body.apply and committed:
        _audit_bulk("BULK_OFFER_UPDATE", current_user, summary, committed)
        try:
            from ..services.cache import cache

            cache.delete_pattern("products:*")
        except Exception:  # noqa: BLE001
            pass

    return {"dry_run": not body.apply, "summary": summary, "rows": rows}


@router.get("/gst-rates")
async def get_gst_rates(current_user: dict = Depends(get_current_user)):
    """Read-only HSN->GST lookup for any authenticated user (POS cashiers
    included) so the cart preview + invoice show the SAME rates the backend
    bills from (the SUPERADMIN-editable master overrides the static table).
    Edits live at /api/v1/admin/hsn. Fail-soft: empty maps when DB is offline
    (frontend then uses its static GST 2.0 constants)."""
    try:
        from ..services.gst_rates import _load_lookup, seed_hsn_gst_master

        seed_hsn_gst_master()
        lk = _load_lookup()
        return {"by_hsn": lk.get("by_hsn", {}), "by_cat": lk.get("by_cat", {})}
    except Exception:
        return {"by_hsn": {}, "by_cat": {}}


# NOTE: Specific routes MUST come before /{product_id}
@router.get("/brands/list")
async def list_brands(
    category: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """List all brands, optionally filtered by category"""
    from ..services.product_master import resolve_category

    # Normalise short codes / plurals to the canonical category the docs store.
    if category:
        category = resolve_category(category) or category
    repo = get_product_repository()

    if repo is not None:
        brands = repo.get_brands(category)
        return {"brands": brands}

    return {"brands": []}


@router.get("/categories")
async def get_category_registry(current_user: dict = Depends(get_current_user)):
    """THE canonical per-category field registry -- the single source of truth.

    Returns, for every canonical product category, the field list with
    required/optional flags sourced from the backend product_master CATEGORY_SPECS
    registry (the SAME registry the create/update enforcement uses). The three
    product-entry doors (Quick Add / Guided / Rapid Grid) read this so they all
    render -- and client-side validate -- the IDENTICAL required-field set the
    server enforces at create. There is NO second copy of the required-ness rule
    on the frontend: it derives from this endpoint.

    Shape: {"categories": [{code, sku_prefix, name, required_fields,
    optional_fields, fields:[{name,label,required,options?}], forced_discount_category}]}.

    Per-field `options` (Catalog Dictionary): when the owner configured an
    allowed-value list for a field (Settings -> Catalog Dictionary) it is
    attached to that field entry, and `brand_name` ALWAYS carries the ACTIVE
    Brand Master brands applicable to the category (possibly []) -- the
    Add-Product form renders these as restricted selects, and the create/update
    doors enforce the same lists server-side. Fail-soft: db trouble -> the raw
    registry without options.

    cost_price is intentionally NOT a required field here: it is GRN-deferred
    (required to make a product ACTIVE/sellable, not to save it).

    Available to any authenticated user (read-only metadata; the create gate is
    role-protected separately).
    """
    categories = _pm.all_category_specs()
    try:
        from ..dependencies import get_db as _get_db_dep
        from ..services import catalog_dictionary as _cd

        db = _get_db_dep()
        if db is not None and getattr(db, "is_connected", False):
            # ONE raw read; per-category effective lists are merged in memory
            # (category-scoped lists override the All-categories ones).
            raw_options = _cd.load_field_options_raw(db)
            brand_cache: dict = {}
            for cat in categories:
                prefix = cat.get("sku_prefix")
                if prefix not in brand_cache:
                    brand_cache[prefix] = _cd.load_brand_options(db, prefix)
                brands = brand_cache[prefix]
                field_options = _cd.merged_field_options(raw_options, cat.get("code"))
                for fld in cat.get("fields", []):
                    name = fld.get("name")
                    if name == "brand_name":
                        # Always attach (even []) -- the Brand Master is the
                        # single source of truth for brands; None = read failed.
                        if brands is not None:
                            fld["options"] = brands
                    elif name in field_options:
                        fld["options"] = field_options[name]
    except Exception as e:  # noqa: BLE001 - options are an enrichment, never a blocker
        logger.warning("[CATALOG-DICT] registry options merge skipped: %s", e)
    return {"categories": categories}


@router.get("/brand-options")
async def get_brand_options(
    category: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Brand Master brands (+ their sub-brands) for the Add-Product form.

    The admin Brand Master CRUD lives behind SUPERADMIN/ADMIN gates; this is
    the READ-ONLY projection any authenticated catalog operator can use:
    active brands applicable to `category` (short prefix like 'FR' or a
    canonical name like 'FRAME'; omit for all), each with its sub-brand names
    so the form can restrict the Sub Brand select per selected brand.

    Shape: {"brands": [{"name": str, "subbrands": [str, ...], "tier": str|None,
    "sync_to_shopify_default": bool}, ...]}.
    Fail-soft: db trouble -> {"brands": []}.
    """
    try:
        from ..dependencies import get_db as _get_db_dep
        from ..services import catalog_dictionary as _cd

        db = _get_db_dep()
        if db is None or not getattr(db, "is_connected", False):
            return {"brands": []}
        prefix = None
        if category:
            spec = _pm.category_spec(category)
            prefix = spec.prefix if spec is not None else str(category).strip()
        names = _cd.load_brand_options(db, prefix) or []
        brands = []
        for name in names:
            subs = _cd.load_subbrand_options(db, name) or []
            # tier: shown read-only in the form's Review (the product's
            # discount band derives from it at create time).
            tier = _cd.load_brand_tier(db, name)
            brands.append(
                {
                    "name": name,
                    "subbrands": subs,
                    "tier": tier,
                    # Default Shopify-sync INTENT for the brand (Settings ->
                    # Brand Master); the create door stamps sync_to_shopify
                    # from it when the payload doesn't say explicitly.
                    "sync_to_shopify_default": _cd.load_brand_sync_default(db, name),
                }
            )
        return {"brands": brands}
    except Exception as e:  # noqa: BLE001 - read-only projection, never a blocker
        logger.warning("[CATALOG-DICT] brand-options read failed: %s", e)
        return {"brands": []}


# NOTE: registered here (with the other literal /products/* paths like
# /brand-options and /generate-description) so it stays ABOVE the
# GET /products/{product_id} catch-all -- the literal path must win.
@router.get("/similar")
async def get_similar_products(
    category: str,
    brand: str = "",
    model_no: str = "",
    colour_code: str = "",
    size: str = "",
    current_user: dict = Depends(get_current_user),
):
    """Live as-you-type "similar products" check for the Add-Product form
    (dup-detect council ruling, Phase 2).

    Any authenticated catalog operator may call it (mirrors GET /products).
    The matching runs through product_master.find_similar_products, which
    reuses THE same normaliser that builds the spine's identity/duplicate key
    (council rule) -- so what this reports as an exact match is exactly what
    the create door would 409.

    Shape: {"exact_match": {summary}|null, "siblings": [summary, ...] (cap 12),
    "model_colour_count": N}. FAIL-SOFT: any DB trouble returns the empty
    shape with 200 -- the FE strip simply doesn't render; never a 5xx.
    """
    empty = {"exact_match": None, "siblings": [], "model_colour_count": 0}
    try:
        from database.connection import get_db

        db = get_db()
        if not (db and getattr(db, "is_connected", False)):
            return empty
        return _pm.find_similar_products(
            db.db.get_collection("products"),
            category=category,
            brand=brand,
            model=model_no,
            colour=colour_code,
            size=size,
        )
    except Exception as e:  # noqa: BLE001 - hint endpoint, never a blocker
        logger.warning("[SIMILAR] lookup failed: %s", e)
        return empty


class DescriptionGenerateRequest(BaseModel):
    """Input for the Add-Product form's "Auto-fill with AI" button."""

    category: str
    attributes: Dict[str, Any]
    max_length: int = Field(default=350, ge=80, le=1000)


@router.post("/generate-description")
async def generate_product_description(
    body: DescriptionGenerateRequest,
    current_user: dict = Depends(require_roles(*_CATALOG_ROLES)),
):
    """Draft the product description from the filled attribute fields -- the
    Add-Product form's "Auto-fill with AI" button.

    Owner 2026-07-05: the output now matches the DELIBERATE Shopify format the
    live store already uses (Product Details header + model line + ONE
    marketing paragraph + Technical Specifications table + General Information
    table + Warranty section). Shopify has no structured spec fields, so the
    description IS the spec sheet. The tables are built deterministically in
    code from the filled fields (services/product_description.py) -- Claude
    (haiku) writes ONLY the marketing paragraph, in the store's established
    voice, so the structure can never drift and no spec can be invented.

    ALWAYS returns 200 with a `status` the FE keys on -- never a 5xx -- so an
    unavailable model can never block the product-create flow:
      GENERATED         -> description holds the full HTML draft
      EMPTY_ATTRIBUTES  -> nothing usable to write from (fill brand/model first)
      FAILED_NO_KEY     -> no Anthropic key configured (Settings -> Integrations)
      FAILED_GENERATION -> model call failed/timed out (retry later)
    """
    from agents.claude_client import call_claude, is_claude_available
    from ..services.product_description import (
        build_model_line,
        build_shopify_description_html,
    )

    # Only non-empty, human-meaningful fields feed the prompt.
    filled = {
        str(k): str(v).strip()
        for k, v in (body.attributes or {}).items()
        if v is not None and str(v).strip() != ""
    }
    if not filled:
        return {"description": "", "status": "EMPTY_ATTRIBUTES"}

    if not is_claude_available():
        return {"description": "", "status": "FAILED_NO_KEY"}

    spec = _pm.category_spec(body.category)
    category_name = spec.display if spec is not None else str(body.category)
    model_line = build_model_line(category_name, filled)

    system = (
        "You are the product copywriter for Better Vision, an Indian optical "
        "retail chain. Write ONE marketing paragraph (the store's established "
        "voice) for the product described by the attributes given. It will be "
        "placed under the product's spec tables on the storefront. STYLE "
        "(match the store's existing descriptions): open with a line like "
        "'Reflect your style with {brand + model + colour + product type}', "
        "mention the colour/material/look, who it suits (gender) and the size "
        "when given, then close with the store promise sentence: 'Enjoy best "
        "discounts and guaranteed authentic and genuine products, ensuring "
        "you have the ultimate eyewear shopping experience. Discover your "
        "perfect pair at Better Vision today!'. Rules: 3-4 sentences, under "
        f"{body.max_length} characters, PLAIN TEXT only (no markdown, no HTML, "
        "no headings, no bullets, no emojis). Use ONLY the attributes provided "
        "-- never invent specifications, certifications or claims. SEO: "
        "naturally include the brand name, model number, product-type word, "
        "gender and colour; no keyword stuffing, each phrase at most twice; "
        "front-load brand + model + product type in the first sentence."
    )
    lines = "\n".join(f"{k}: {v}" for k, v in sorted(filled.items()))
    user_msg = f"Product category: {category_name}\nAttributes:\n{lines}"

    text = await call_claude(system, user_msg, max_tokens=300, timeout=20.0)
    if not text or not text.strip():
        return {"description": "", "status": "FAILED_GENERATION"}

    # Hard-trim the PARAGRAPH to the requested cap at a word boundary (the
    # spec tables are deterministic and not counted against the cap).
    paragraph = text.strip()
    if len(paragraph) > body.max_length:
        paragraph = (
            paragraph[: body.max_length].rsplit(" ", 1)[0].rstrip(" ,;:.") + "."
        )

    html = build_shopify_description_html(category_name, filled, paragraph)
    return {
        "description": html,
        "paragraph": paragraph,
        "model_line": model_line,
        "status": "GENERATED",
    }


@router.get("/categories/list")
async def list_categories(current_user: dict = Depends(get_current_user)):
    """List all product categories"""
    categories = _get_categories_from_db()
    return {"categories": categories}


@router.get("/tags/list")
async def list_tags(
    prefix: Optional[str] = Query(
        None, description="Typeahead prefix (case-insensitive)"
    ),
    limit: int = Query(200, ge=1, le=1000),
    current_user: dict = Depends(get_current_user),
):
    """Known product tags (step-12) for filtering + autocomplete.

    Returns the distinct normalised tags across active products, most-used
    first. `prefix` narrows for typeahead. Fail-soft: no repo -> empty list."""
    repo = get_product_repository()
    if repo is not None:
        try:
            return {"tags": repo.get_tags(prefix=prefix, limit=limit)}
        except Exception:  # noqa: BLE001 - autocomplete must never 500
            return {"tags": []}
    return {"tags": []}


# ============================================================================
# CATALOGUER ATTRIBUTION — who catalogued what
# ============================================================================
# Roles that may see the cataloguer roster + per-user counts (performance
# tracking is a manager concern; regular staff don't need the roster).
# SUPERADMIN auto-passes via require_roles.
_CATALOGUER_VIEW_ROLES = (
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "CATALOG_MANAGER",
)


@router.get("/cataloguers")
async def list_cataloguers(
    current_user: dict = Depends(require_roles(*_CATALOGUER_VIEW_ROLES)),
):
    """Distinct product cataloguers with per-user counts (attribution feature).

    One row per distinct `created_by` on the products collection:
      {user_id, name, created_count, last_created_at}
    `name` prefers the stamped created_by_name; legacy docs created before the
    name stamp existed fall back to a batched users lookup, then to the raw id.
    Drives the Inventory "Catalogued by" filter dropdown + performance counts.

    NOTE (route order): registered BEFORE the dynamic GET /products/{product_id}
    in this file, so the literal path is matched first and never shadowed.
    Fail-soft: no repo / aggregation trouble -> empty list, never a 500.
    """
    repo = get_product_repository()
    if repo is None:
        return {"cataloguers": []}

    try:
        rows = repo.cataloguer_stats()
    except Exception:  # noqa: BLE001 - roster must never 500 the page
        rows = []

    # Legacy-name backfill: docs created before created_by_name existed carry
    # only the user_id. Resolve those names in ONE batched users query
    # (fail-soft: unknown ids just render as the raw id).
    missing = [r["_id"] for r in rows if r.get("_id") and not r.get("name")]
    resolved = _resolve_user_names(missing)

    cataloguers = []
    for r in rows:
        uid = r.get("_id")
        if not uid:
            continue
        last = r.get("last_created_at")
        cataloguers.append(
            {
                "user_id": uid,
                "name": r.get("name") or resolved.get(uid) or uid,
                "created_count": int(r.get("created_count") or 0),
                "last_created_at": (
                    last.isoformat() if isinstance(last, datetime) else last
                ),
            }
        )
    return {"cataloguers": cataloguers}


# ============================================================================
# CATALOGUING SCORECARD + QC SAMPLING (attribution phase 2)
# ============================================================================
# Owner requirement: per-user cataloguing performance (volume, approvals,
# corrections received, QC error rate) + a random-sample QC review workflow.
# Manager-ladder gated (same posture as /products/cataloguers).

# Creator ids that are never a human cataloguer.
_NON_CATALOGUER_IDS = (None, "", "system", "SYSTEM")

# Patch keys that count as CATALOGUING fields when classifying a correction
# from a `product.updated` audit row's before/after. Pricing (mrp/offer_price/
# cost_price/discount_category), stock and is_active changes NEVER count.
# Any `attributes.*` dotted key also counts (checked by prefix).
_CATALOGUING_AUDIT_KEYS = frozenset(
    {
        "category",
        "brand",
        "model",
        "name",
        "title",
        "description",
        "images",
        "hsn_code",
        "gst_rate",
        "attributes",
    }
)

# QC verdict error-field vocabulary (mirrored by the FE checkboxes).
_QC_ERROR_FIELDS = (
    "category",
    "brand_model",
    "attributes",
    "pricing",
    "hsn_gst",
    "images",
    "description",
    "name",
)


def _resolve_user_names(user_ids: List[str]) -> Dict[str, str]:
    """Batched user_id -> display-name lookup on the users collection.
    Fail-soft: no db / unknown ids simply resolve to nothing (callers fall
    back to the raw id)."""
    resolved: Dict[str, str] = {}
    ids = [u for u in (user_ids or []) if u]
    if not ids:
        return resolved
    try:
        from ..dependencies import get_db as _get_db_dep

        _db = _get_db_dep()
        if _db is not None:
            users = _db.get_collection("users")
            if users is not None:
                for u in users.find(
                    {"user_id": {"$in": ids}},
                    {"_id": 0, "user_id": 1, "username": 1, "full_name": 1},
                ):
                    uid = u.get("user_id")
                    nm = u.get("username") or u.get("full_name")
                    if uid and nm:
                        resolved[uid] = nm
    except Exception:  # noqa: BLE001 - name enrichment is best-effort
        return {}
    return resolved


def _as_dt(value) -> Optional[datetime]:
    """Coerce a stored timestamp (datetime or ISO string) to a NAIVE datetime
    for windowing comparisons. None when unparseable. Mixed tz sources (audit
    domain rows are local now(), the activity middleware stamps utcnow()) skew
    by a few hours at most, which is immaterial for day-granularity windows."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _scorecard_db():
    """The shared db connection, or None (fail-soft) when unavailable."""
    try:
        from ..dependencies import get_db as _get_db_dep

        db = _get_db_dep()
    except Exception:  # noqa: BLE001
        return None
    if db is None or not getattr(db, "is_connected", True):
        return None
    return db


def _bulk_write_duplicate_indices(exc) -> Optional[set]:
    """For a pymongo BulkWriteError whose failures are ALL duplicate-key
    (code 11000), return the failing doc indices; None for anything else
    (caller then treats the exception as a real insert failure). Matched by
    class name so no hard pymongo import is needed."""
    if exc.__class__.__name__ != "BulkWriteError":
        return None
    details = getattr(exc, "details", None) or {}
    write_errors = details.get("writeErrors") or []
    if not write_errors:
        return None
    dup: set = set()
    for we in write_errors:
        if we.get("code") != 11000:
            return None
        dup.add(int(we.get("index", -1)))
    return dup


@router.get("/cataloguing-scorecard")
async def cataloguing_scorecard(
    days: int = Query(30, ge=1, le=365, description="Rolling window in days"),
    current_user: dict = Depends(require_roles(*_CATALOGUER_VIEW_ROLES)),
):
    """Per-user cataloguing performance over a rolling window (default 30d).

    One row per user who created products (or approved promotions) in the
    window: created_count + per-day rate + created_today, approvals (catalog
    promotes), corrections_received, category_coverage, and QC stats from the
    qc_samples verdicts.

    CORRECTIONS METRIC - what it can and cannot see
    -----------------------------------------------
    corrections_received counts DISTINCT products this user created in the
    window that a DIFFERENT user edited within 30 days of creation. Two
    signal sources, best available first:

      1. CLASSIFIED (corrections_classified): `product.updated` rows in the
         hash-chained audit_logs (written ONLY by the product_master engine
         door, PUT /products/master/{id}) carry before/after patch keys, so
         these are field-classified: they count ONLY when a cataloguing field
         changed (category/brand/model/name/title/description/images/
         attributes[.*]/hsn_code/gst_rate). Pricing (mrp/offer_price/
         cost_price/discount_category), stock and is_active changes NEVER
         count here.
      2. APPROXIMATE (corrections_approximate): ONLY the catalog-side PIM
         door (PUT /catalog/products/{id}) still writes no field-level
         history, so for it an other-user edit within 30 days of creation is
         counted WITHOUT field classification (a pricing-only edit through
         that door IS over-counted). The SPINE door (PUT /products/{id}) now
         writes a compact product.updated row carrying the patch's changed
         keys, so spine edits are fully CLASSIFIED -- pricing/is_active-only
         spine edits never count -- and its baseline middleware rows are
         EXCLUDED from the approximate bucket. The catalog door gains the
         same classification in a follow-up once PR #911 (which owns
         routers/catalog.py) lands; the approximate bucket then disappears.

    A product flagged by source 1 is excluded from source 2 (no double count).
    corrections_received = classified + approximate (distinct products).
    """
    now = datetime.now()
    cutoff = now - timedelta(days=days)
    db = _scorecard_db()
    if db is None:
        return {"days": days, "generated_at": now.isoformat(), "rows": []}

    per_user: Dict[str, Dict[str, Any]] = {}

    def _row(uid: str) -> Dict[str, Any]:
        return per_user.setdefault(
            uid,
            {
                "user_id": uid,
                "name": None,
                "created_count": 0,
                "created_today": 0,
                "per_day_rate": 0.0,
                "approvals": 0,
                "corrections_received": 0,
                "corrections_classified": 0,
                "corrections_approximate": 0,
                "category_coverage": {},
                "qc": {"sampled": 0, "errors": 0, "error_rate": 0.0},
            },
        )

    # ---- 1. Creations in window (spine products) --------------------------
    creators: Dict[str, tuple] = {}  # product_id -> (creator_id, created_at)
    # created_today is a DAY-BOUNDARY metric for IST stores: the boundary is
    # IST midnight expressed in the naive-UTC frame created_at is stored in
    # (BUG-104 helper; Railway runs UTC, business calendar is Asia/Kolkata).
    from ..utils.ist import ist_day_start_utc

    today_start = ist_day_start_utc()
    try:
        prods = list(
            db.get_collection("products").find(
                {
                    "created_at": {"$gte": cutoff},
                    "created_by": {"$nin": list(_NON_CATALOGUER_IDS)},
                },
                {
                    "_id": 0,
                    "product_id": 1,
                    "created_by": 1,
                    "created_by_name": 1,
                    "category": 1,
                    "created_at": 1,
                },
            )
        )
    except Exception:  # noqa: BLE001 - scorecard must never 500
        prods = []
    for p in prods:
        uid = p.get("created_by")
        if not uid:
            continue
        row = _row(uid)
        row["created_count"] += 1
        if not row["name"] and p.get("created_by_name"):
            row["name"] = p.get("created_by_name")
        cat = p.get("category") or "UNKNOWN"
        row["category_coverage"][cat] = row["category_coverage"].get(cat, 0) + 1
        created_at = _as_dt(p.get("created_at"))
        if created_at is not None and created_at >= today_start:
            row["created_today"] += 1
        pid = p.get("product_id")
        if pid:
            creators[pid] = (uid, created_at)

    # ---- 2. Approvals (catalog_products promote stamps; promoted_at is an
    # ISO string, so the window bound is a string compare -- same format). ----
    try:
        promos = db.get_collection("catalog_products").find(
            {"promoted_at": {"$gte": cutoff.isoformat()}},
            {"_id": 0, "promoted_by": 1},
        )
        for c in promos:
            uid = c.get("promoted_by")
            if uid and uid not in _NON_CATALOGUER_IDS:
                _row(uid)["approvals"] += 1
    except Exception:  # noqa: BLE001
        pass

    # ---- 3. Corrections (see docstring for the two-source design) ---------
    corrected_classified: set = set()
    corrected_approx: set = set()
    pids = list(creators.keys())
    if pids:
        try:
            audit = db.get_collection("audit_logs")
            for i in range(0, len(pids), 500):
                chunk = pids[i : i + 500]
                audit_rows = audit.find(
                    {
                        "entity_id": {"$in": chunk},
                        "action": {"$in": ["product.updated", "UPDATE"]},
                        # Window bound so the (action, timestamp) / (entity_id,
                        # action) indexes can prune the append-only trail: any
                        # counted edit satisfies edited_at >= created_at >=
                        # cutoff (pre-creation edits are skipped below), and
                        # both row sources always stamp `timestamp`.
                        "timestamp": {"$gte": cutoff},
                    },
                    {
                        "_id": 0,
                        "entity_id": 1,
                        "action": 1,
                        "actor": 1,
                        "user_id": 1,
                        "timestamp": 1,
                        "ts": 1,
                        "before": 1,
                        "after": 1,
                        "path": 1,
                        "method": 1,
                    },
                )
                for r in audit_rows:
                    pid = r.get("entity_id")
                    info = creators.get(pid)
                    if not info:
                        continue
                    creator_id, created_at = info
                    actor = r.get("actor") or r.get("user_id")
                    if not actor or actor == creator_id:
                        continue  # self-edits are never corrections
                    edited_at = _as_dt(r.get("timestamp")) or _as_dt(r.get("ts"))
                    if edited_at is None or created_at is None:
                        continue
                    # Recency rule: only edits within 30 days of creation count.
                    if edited_at < created_at or edited_at > created_at + timedelta(
                        days=30
                    ):
                        continue
                    if r.get("action") == "product.updated":
                        keys = set((r.get("before") or {}).keys()) | set(
                            (r.get("after") or {}).keys()
                        )
                        if any(
                            k in _CATALOGUING_AUDIT_KEYS
                            or str(k).startswith("attributes.")
                            for k in keys
                        ):
                            corrected_classified.add(pid)
                    else:
                        # Baseline middleware row (no field detail): count ONLY
                        # the catalog PIM PUT path -- the one door still without
                        # field-level history (classification follows once
                        # PR #911 lands). Spine PUTs (/products/{pid}) and
                        # engine-door edits (/products/master/{pid}) both write
                        # classified product.updated rows now, so their
                        # baseline twins are deliberately NOT counted here.
                        if str(r.get("method") or "") not in ("PUT", "PATCH"):
                            continue
                        path = str(r.get("path") or "")
                        if path == f"/api/v1/catalog/products/{pid}":
                            corrected_approx.add(pid)
        except Exception as exc:  # noqa: BLE001
            # Visible (not silent): a cursor timeout here would otherwise
            # masquerade as "zero corrections".
            logger.warning("[SCORECARD] corrections scan failed: %s", exc)
    corrected_approx -= corrected_classified
    for pid in corrected_classified:
        _row(creators[pid][0])["corrections_classified"] += 1
    for pid in corrected_approx:
        _row(creators[pid][0])["corrections_approximate"] += 1
    for row in per_user.values():
        row["corrections_received"] = (
            row["corrections_classified"] + row["corrections_approximate"]
        )

    # ---- 4. QC stats (verdicts on samples drawn in the window) ------------
    try:
        samples = db.get_collection("qc_samples").find(
            {"sampled_at": {"$gte": cutoff}},
            {"_id": 0, "cataloguer_id": 1, "cataloguer_name": 1, "verdict": 1},
        )
        for s in samples:
            uid = s.get("cataloguer_id")
            if not uid or uid in _NON_CATALOGUER_IDS:
                continue
            if not s.get("verdict"):
                continue  # pending items don't count until reviewed
            row = _row(uid)
            if not row["name"] and s.get("cataloguer_name"):
                row["name"] = s.get("cataloguer_name")
            row["qc"]["sampled"] += 1
            if s.get("verdict") == "ERROR":
                row["qc"]["errors"] += 1
    except Exception:  # noqa: BLE001
        pass

    # ---- 5. Finalise rows --------------------------------------------------
    missing_names = [u for u, r in per_user.items() if not r["name"]]
    resolved = _resolve_user_names(missing_names)
    rows = []
    for uid, row in per_user.items():
        row["name"] = row["name"] or resolved.get(uid) or uid
        row["per_day_rate"] = round(row["created_count"] / float(days), 2)
        qc = row["qc"]
        qc["error_rate"] = (
            round(qc["errors"] * 100.0 / qc["sampled"], 1) if qc["sampled"] else 0.0
        )
        rows.append(row)
    rows.sort(key=lambda r: (-r["created_count"], r["user_id"]))
    return {"days": days, "generated_at": now.isoformat(), "rows": rows}


class QcGenerateRequest(BaseModel):
    """POST /products/qc-samples/generate body."""

    days: int = Field(default=7, ge=1, le=90)
    per_user: int = Field(default=10, ge=1, le=50)


class QcVerdictRequest(BaseModel):
    """POST /products/qc-samples/{item_id}/verdict body."""

    verdict: str
    error_fields: Optional[List[str]] = None
    note: Optional[str] = None

    @field_validator("verdict", mode="before")
    @classmethod
    def _validate_verdict(cls, v):
        normed = str(v or "").strip().upper()
        if normed not in ("OK", "ERROR"):
            raise ValueError("verdict must be OK or ERROR")
        return normed

    @field_validator("error_fields")
    @classmethod
    def _validate_error_fields(cls, v):
        if v is None:
            return None
        bad = [f for f in v if f not in _QC_ERROR_FIELDS]
        if bad:
            raise ValueError(
                f"Invalid error_fields {bad}. Allowed: {list(_QC_ERROR_FIELDS)}"
            )
        return v


@router.post("/qc-samples/generate", status_code=201)
async def generate_qc_samples(
    body: QcGenerateRequest,
    current_user: dict = Depends(require_roles(*_CATALOGUER_VIEW_ROLES)),
):
    """Draw a random QC sample batch: up to `per_user` random products per
    distinct cataloguer with creations in the last `days` days.

    A (cataloguer, product) pair that has EVER been sampled -- PENDING or
    already reviewed -- is excluded, so one product yields at most one QC
    datapoint per cataloguer (re-generating never re-queues an unreviewed
    item, and a reviewed defect can never be double-counted into the
    scorecard error rate). One qc_samples doc per sampled item, sharing a
    batch_id. Random pick is server-side random.sample over the eligible
    pool. Concurrent-generate races are backstopped by the partial unique
    index on (cataloguer_id, product_id, status=PENDING): the insert runs
    ordered=False and duplicate-key losers are dropped from the reported
    counts."""
    db = _scorecard_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    now = datetime.now()
    cutoff = now - timedelta(days=body.days)
    try:
        pool_docs = list(
            db.get_collection("products").find(
                {
                    "created_at": {"$gte": cutoff},
                    "created_by": {"$nin": list(_NON_CATALOGUER_IDS)},
                },
                {
                    "_id": 0,
                    "product_id": 1,
                    "created_by": 1,
                    "created_by_name": 1,
                    "category": 1,
                    "brand": 1,
                    "model": 1,
                    "name": 1,
                    "sku": 1,
                    "images": 1,
                    "created_at": 1,
                },
            )
        )
    except Exception:  # noqa: BLE001
        pool_docs = []

    pools: Dict[str, List[Dict]] = {}
    for p in pool_docs:
        uid = p.get("created_by")
        pid = p.get("product_id")
        if uid and pid:
            pools.setdefault(uid, []).append(p)

    # Pairs already sampled for these cataloguers -- ANY status. A reviewed
    # pair must never be re-sampled (one product = at most one QC datapoint
    # per cataloguer), and a PENDING pair is still awaiting review.
    qc_coll = db.get_collection("qc_samples")
    try:
        already = {
            (r.get("cataloguer_id"), r.get("product_id"))
            for r in qc_coll.find(
                {"cataloguer_id": {"$in": list(pools.keys())}},
                {"_id": 0, "cataloguer_id": 1, "product_id": 1},
            )
        }
    except Exception:  # noqa: BLE001
        already = set()

    names = _resolve_user_names(
        [u for u, docs in pools.items() if not any(d.get("created_by_name") for d in docs)]
    )

    batch_id = str(uuid.uuid4())
    items: List[Dict[str, Any]] = []
    summary: List[Dict[str, Any]] = []
    for uid in sorted(pools.keys()):
        pool = pools[uid]
        eligible = [p for p in pool if (uid, p.get("product_id")) not in already]
        if not eligible:
            continue
        picked = random.sample(eligible, min(body.per_user, len(eligible)))
        cataloguer_name = (
            next((p.get("created_by_name") for p in pool if p.get("created_by_name")), None)
            or names.get(uid)
            or uid
        )
        for p in picked:
            brand = p.get("brand") or ""
            model = p.get("model") or ""
            pname = (
                p.get("name") or f"{brand} {model}".strip() or p.get("sku") or ""
            )
            imgs = p.get("images")
            image_url = (
                imgs[0]
                if isinstance(imgs, list) and imgs and isinstance(imgs[0], str)
                else None
            )
            items.append(
                {
                    "item_id": str(uuid.uuid4()),
                    "batch_id": batch_id,
                    "product_id": p.get("product_id"),
                    "product_name": pname,
                    "sku": p.get("sku"),
                    "category": p.get("category"),
                    "image_url": image_url,
                    "cataloguer_id": uid,
                    "cataloguer_name": cataloguer_name,
                    "product_created_at": p.get("created_at"),
                    "status": "PENDING",
                    "sampled_at": now,
                    "sampled_by": current_user.get("user_id"),
                    "sampled_by_name": current_user.get("username"),
                    "window_days": body.days,
                    "per_user_cap": body.per_user,
                }
            )
        summary.append(
            {"user_id": uid, "name": cataloguer_name, "sampled": len(picked)}
        )

    if items:
        # ordered=False + the partial unique index on (cataloguer_id,
        # product_id, status=PENDING) make concurrent generates race-safe:
        # a pair another in-flight generate just inserted fails ITS row only
        # (duplicate key); every other row still lands. Losers are dropped
        # from the reported counts so total_items/summary reflect reality.
        try:
            # Copy each doc: insert_many mutates its args (adds _id), and the
            # response below must stay JSON-clean.
            qc_coll.insert_many([dict(i) for i in items], ordered=False)
        except Exception as exc:  # noqa: BLE001
            dup_indices = _bulk_write_duplicate_indices(exc)
            if dup_indices is None:
                logger.warning("[QC] sample batch insert failed: %s", exc)
                raise HTTPException(
                    status_code=500, detail="Failed to store the QC sample batch"
                ) from exc
            logger.info(
                "[QC] %d duplicate pair(s) skipped (concurrent generate)",
                len(dup_indices),
            )
            dropped = [items[i] for i in dup_indices if i < len(items)]
            items = [it for i, it in enumerate(items) if i not in dup_indices]
            dropped_by_user: Dict[str, int] = {}
            for it in dropped:
                uid = it.get("cataloguer_id")
                dropped_by_user[uid] = dropped_by_user.get(uid, 0) + 1
            for s in summary:
                s["sampled"] = max(0, s["sampled"] - dropped_by_user.get(s["user_id"], 0))
            summary = [s for s in summary if s["sampled"] > 0]

    return {
        "batch_id": batch_id,
        "days": body.days,
        "per_user": body.per_user,
        "total_items": len(items),
        "cataloguers": summary,
    }


@router.get("/qc-samples")
async def list_qc_samples(
    status: Optional[str] = Query(None, pattern="^(PENDING|REVIEWED)$"),
    batch_id: Optional[str] = Query(None),
    cataloguer: Optional[str] = Query(None, description="Filter by cataloguer user_id"),
    limit: int = Query(500, ge=1, le=2000),
    current_user: dict = Depends(require_roles(*_CATALOGUER_VIEW_ROLES)),
):
    """List QC sample items, newest batch first, plus per-batch progress
    summaries (total vs reviewed -- the batch progress is computed over the
    batch_id/cataloguer scope BEFORE the status filter narrows the items).

    Both halves run SERVER-SIDE (qc_samples grows monotonically, so a full
    client-side materialization would degrade with age): the batch progress
    is a $group aggregation over the pre-status scope, and the item fetch
    puts status into the Mongo filter with cursor sort + limit."""
    db = _scorecard_db()
    if db is None:
        return {"items": [], "total": 0, "batches": []}
    qc_coll = db.get_collection("qc_samples")

    flt: Dict[str, Any] = {}
    if batch_id:
        flt["batch_id"] = batch_id
    if cataloguer:
        flt["cataloguer_id"] = cataloguer

    # Per-batch progress: Mongo-side $group over the pre-status scope.
    batches: List[Dict[str, Any]] = []
    try:
        pipeline: List[Dict[str, Any]] = []
        if flt:
            pipeline.append({"$match": dict(flt)})
        pipeline += [
            {
                "$group": {
                    "_id": "$batch_id",
                    "total": {"$sum": 1},
                    "reviewed": {
                        "$sum": {
                            "$cond": [{"$eq": ["$status", "REVIEWED"]}, 1, 0]
                        }
                    },
                    "sampled_at": {"$max": "$sampled_at"},
                }
            },
            {"$sort": {"sampled_at": -1}},
            {"$limit": 20},
        ]
        for b in qc_coll.aggregate(pipeline):
            batches.append(
                {
                    "batch_id": b.get("_id") or "",
                    "total": int(b.get("total") or 0),
                    "reviewed": int(b.get("reviewed") or 0),
                    "sampled_at": b.get("sampled_at"),
                }
            )
    except Exception:  # noqa: BLE001 - progress strip is best-effort
        batches = []

    # Items: status pushed into the Mongo filter; sort + limit on the cursor.
    item_flt = dict(flt)
    if status:
        item_flt["status"] = status
    try:
        rows = list(
            qc_coll.find(item_flt, {"_id": 0}).sort("sampled_at", -1).limit(limit)
        )
    except Exception:  # noqa: BLE001
        rows = []
    return {"items": rows, "total": len(rows), "batches": batches}


@router.post("/qc-samples/{item_id}/verdict")
async def qc_sample_verdict(
    item_id: str,
    body: QcVerdictRequest,
    current_user: dict = Depends(require_roles(*_CATALOGUER_VIEW_ROLES)),
):
    """Record a QC verdict (OK / ERROR + error_fields + note) on a sample item.

    HARD RULES:
      * NO SELF-QC: the item's cataloguer may never verdict their own item
        (403), regardless of role -- another manager must review it.
      * IMMUTABLE: a set verdict returns 409 on re-verdict. Only ADMIN /
        SUPERADMIN may overwrite, and the overwrite stamps overwritten_by
        (+ keeps the previous verdict in previous_verdict)."""
    db = _scorecard_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    qc_coll = db.get_collection("qc_samples")
    item = qc_coll.find_one({"item_id": item_id}, {"_id": 0})
    if item is None:
        raise HTTPException(status_code=404, detail="QC sample item not found")

    if item.get("cataloguer_id") == current_user.get("user_id"):
        raise HTTPException(
            status_code=403,
            detail=(
                "You catalogued this product -- another manager must review it "
                "(no self-QC)."
            ),
        )

    update: Dict[str, Any] = {}
    is_overwrite = bool(item.get("verdict"))
    if is_overwrite:
        roles = set(current_user.get("roles") or [])
        if not (roles & {"ADMIN", "SUPERADMIN"}):
            raise HTTPException(
                status_code=409,
                detail="This item already has a verdict (verdicts are immutable).",
            )
        update["previous_verdict"] = {
            "verdict": item.get("verdict"),
            "error_fields": item.get("error_fields"),
            "note": item.get("note"),
            "reviewed_by": item.get("reviewed_by"),
            "reviewed_by_name": item.get("reviewed_by_name"),
            "reviewed_at": item.get("reviewed_at"),
        }
        update["overwritten_by"] = current_user.get("user_id")
        update["overwritten_by_name"] = current_user.get("username")
        update["overwritten_at"] = datetime.now()

    update.update(
        {
            "verdict": body.verdict,
            # error_fields only mean something on an ERROR verdict.
            "error_fields": (body.error_fields or []) if body.verdict == "ERROR" else [],
            "note": body.note,
            "reviewed_by": current_user.get("user_id"),
            "reviewed_by_name": current_user.get("username"),
            "reviewed_at": datetime.now(),
            "status": "REVIEWED",
        }
    )
    # CONDITIONAL writes: prod runs multiple uvicorn workers, so the
    # read-then-check above can race a concurrent verdict. The filter
    # re-asserts the expected prior state and matched_count==0 means someone
    # else won the race -> 409, never a silent overwrite.
    if is_overwrite:
        # Pin the previously-read reviewed_at so the previous_verdict
        # snapshot built above is guaranteed consistent with what is being
        # overwritten (a concurrent verdict changes reviewed_at -> no match).
        write_filter = {"item_id": item_id, "reviewed_at": item.get("reviewed_at")}
    else:
        # First verdict: only lands while NO verdict exists. Items are seeded
        # without the field; $in [None, ""] matches both missing and null.
        write_filter = {"item_id": item_id, "verdict": {"$in": [None, ""]}}
    try:
        result = qc_coll.update_one(write_filter, {"$set": update})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[QC] verdict write failed for %s: %s", item_id, exc)
        raise HTTPException(status_code=500, detail="Failed to save verdict") from exc
    if getattr(result, "matched_count", 0) == 0:
        raise HTTPException(
            status_code=409,
            detail=(
                "This item was just verdicted by someone else (verdicts are "
                "immutable). Refresh the list."
            ),
        )
    return qc_coll.find_one({"item_id": item_id}, {"_id": 0})


# ============================================================================
# PRODUCT IMAGES — durable upload + serve (GridFS-backed file store)
# ============================================================================
# The Add Product screen (Quick Add + Guided) uploads product photos here. We
# persist the bytes durably via the shared GridFS-backed file store (same store
# admin_catalog bulk-import + GRN attachments use), so an image survives a
# Railway redeploy, and return a stable URL the create payload sends in the
# product `images` array. Mirrors the expenses upload-bill / download-bill
# pattern (size + mime validation, then store.put / store.get + StreamingResponse).

# Images only -- the shared ALLOWED_MIME_TYPES also permits application/pdf,
# which is not a product image, so gate on this narrower image-only subset.
_IMAGE_MIME_TYPES = frozenset(m for m in ALLOWED_MIME_TYPES if m.startswith("image/"))


@router.post("/image", status_code=201)
async def upload_product_image(
    file: UploadFile = File(...),
    current_user: dict = Depends(require_roles(*_CATALOG_ROLES)),
):
    """Upload one product image; persist it durably and return {file_id, url}.

    The returned `url` points at the sibling GET /products/image/{file_id} serve
    endpoint, so the create payload can carry a stable, self-hosted image URL in
    its `images` array (no external CDN needed). Validates the mime is an image
    and the size is within the shared cap before persisting anything.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB cap",
        )
    mime = (file.content_type or "").lower()
    if mime not in _IMAGE_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{mime}' not allowed. Accepted image types: {sorted(_IMAGE_MIME_TYPES)}",
        )

    store = get_file_store()
    if store is None:
        raise HTTPException(status_code=503, detail="File storage unavailable")

    file_id = store.put(
        content=content,
        filename=file.filename,
        mime_type=mime,
        metadata={"kind": "product_image", "uploaded_by": current_user.get("user_id")},
    )
    if not file_id:
        raise HTTPException(status_code=500, detail="File store write failed")

    return {
        "file_id": file_id,
        "url": f"/api/v1/products/image/{file_id}",
        "filename": file.filename,
        "content_type": mime,
        "size": len(content),
    }


class ImageFromUrlBody(BaseModel):
    """Body for the Autopilot image RE-HOST endpoint."""

    url: str


@router.post("/image/from-url", status_code=201)
def rehost_product_image_from_url(
    body: ImageFromUrlBody,
    current_user: dict = Depends(require_roles(*_CATALOG_ROLES)),
):
    """RE-HOST an external product image into OUR file store (Autopilot v2).

    Catalog Autopilot candidates carry brand-site image URLs; hotlinking them
    means the product photo dies whenever the brand site moves the file. This
    endpoint server-side fetches the external image ONCE and persists the
    bytes durably in the shared GridFS store (kind="product_image", exactly
    like the multipart upload above), returning the SAME response shape with a
    stable self-hosted url.

    SECURITY: the fetch goes through services.image_rehost.fetch_external_image
    - http/https only, every host (and every redirect hop, max 3) resolved and
    blocked for private/loopback/link-local/metadata ranges, response mime
    gated to the image allowlist, and the body streamed under the shared size
    cap. Role-gated to the catalog set like the upload. Sync `def` on purpose:
    FastAPI runs it on the threadpool so the blocking fetch never stalls the
    event loop.
    """
    from ..services.image_rehost import (
        ImageFetchError,
        fetch_external_image,
        filename_from_url,
    )

    url = (body.url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    store = get_file_store()
    if store is None:
        raise HTTPException(status_code=503, detail="File storage unavailable")

    try:
        content, mime, final_url = fetch_external_image(
            url,
            allowed_mimes=_IMAGE_MIME_TYPES,
            max_bytes=MAX_FILE_SIZE_BYTES,
        )
    except ImageFetchError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e

    filename = filename_from_url(final_url, mime)
    file_id = store.put(
        content=content,
        filename=filename,
        mime_type=mime,
        metadata={
            "kind": "product_image",
            "uploaded_by": current_user.get("user_id"),
            # Audit: where the bytes came from (never re-fetched from here).
            "source_url": url,
        },
    )
    if not file_id:
        raise HTTPException(status_code=500, detail="File store write failed")

    return {
        "file_id": file_id,
        "url": f"/api/v1/products/image/{file_id}",
        "filename": filename,
        "content_type": mime,
        "size": len(content),
    }


@router.post("/image/{file_id}/edit", status_code=201)
async def edit_product_image(
    file_id: str,
    current_user: dict = Depends(require_roles(*_CATALOG_ROLES)),
):
    """Clean up a previously-uploaded product image: remove its background and
    resize to the catalog standard, then persist the result as a NEW image.

    This runs the DETERMINISTIC cut-out pipeline (services/image_editor.py --
    Photoroom v2/edit pinned to a static backdrop + soft synthetic shadow, so
    the product pixels are preserved; it never repaints the subject). The
    EditSpec.from_env() recipe is the catalog STANDARD (white backdrop, square
    1000x1000 tile, padding) applied identically to every image, so the output
    is consistent across the catalog.

    Additive + backward-compatible: the ORIGINAL image is left untouched; a new
    file_id/url is returned so the caller replaces the entry it just edited.
    Same catalog-write role gate as the multipart upload. Fail-soft: when no
    editor is configured (no Photoroom key), returns 400 with an operator-facing
    hint -- provider internals are never leaked.
    """
    store = get_file_store()
    if store is None:
        raise HTTPException(status_code=503, detail="File storage unavailable")

    # Scope the read to product images (the shared store also holds GRN
    # attachments / expense bills). A wrong-kind or missing id -> 404.
    got = store.get(file_id, require_kind="product_image")
    if got is None:
        raise HTTPException(status_code=404, detail="Image not found")
    raw, _filename, _mime = got

    editor = get_image_editor()
    if not editor.available():
        raise HTTPException(
            status_code=400,
            detail=(
                "Background removal isn't set up. Add a Photoroom API key in "
                "Settings -> Integrations to enable it."
            ),
        )

    try:
        edited = await editor.edit(raw, EditSpec.from_env())
    except RuntimeError as e:
        # Fail-soft: surface a short, provider-agnostic reason (no secrets).
        err = str(e)[:200]
        raise HTTPException(
            status_code=502, detail=f"Background removal failed. {err}"
        ) from e

    new_id = store.put(
        content=edited,
        filename=f"bg-{file_id}.png",
        mime_type="image/png",
        metadata={
            "kind": "product_image",
            "edited_from": file_id,
            "edited_by": current_user.get("user_id"),
        },
    )
    if not new_id:
        raise HTTPException(status_code=503, detail="File store write failed")

    return {"file_id": new_id, "url": f"/api/v1/products/image/{new_id}"}


@router.get("/image/{file_id}")
async def get_product_image(file_id: str):
    """Stream a previously-uploaded product image by its stored file_id.

    Public (no auth): product images are non-sensitive catalog media and the
    returned URL is embedded in <img> tags that don't carry the auth header.
    Catalogued PUBLIC in rbac_policy for the same reason.
    """
    store = get_file_store()
    if store is None:
        raise HTTPException(status_code=503, detail="File storage unavailable")

    # SECURITY: this serve is PUBLIC and reads the SHARED file store (which also
    # holds GRN attachments + expense bills). Scope it to files stamped
    # kind="product_image" at upload so a GRN / expense-bill file_id can NOT be
    # fetched here. A wrong-kind id returns 404, same as a missing one.
    rec = store.get(file_id, require_kind="product_image")
    if rec is None:
        raise HTTPException(status_code=404, detail="Image not found")

    content, filename, mime = rec
    return StreamingResponse(
        io.BytesIO(content),
        media_type=mime or "application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            # Product images are immutable (a new upload gets a new id), so let
            # the browser cache aggressively.
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )


@router.get("/sku/{sku}")
async def get_product_by_sku(sku: str, current_user: dict = Depends(get_current_user)):
    """Get product by SKU"""
    repo = get_product_repository()

    if repo is not None:
        product = repo.find_by_sku(sku)
        if product is not None:
            return product
        raise HTTPException(status_code=404, detail="Product not found")

    return {"sku": sku}


@router.get("/{product_id}")
async def get_product(product_id: str, current_user: dict = Depends(get_current_user)):
    """Get product by ID"""
    repo = get_product_repository()

    if repo is not None:
        product = repo.find_by_id(product_id)
        if product is not None:
            # Same image_url alias as list_products (owner bug 2026-07-05):
            # consumers read the singular field; spine docs carry images[].
            if not product.get("image_url"):
                imgs = product.get("images")
                if isinstance(imgs, list) and imgs and isinstance(imgs[0], str):
                    product["image_url"] = imgs[0]
            return product
        raise HTTPException(status_code=404, detail="Product not found")

    return {"product_id": product_id}


@router.put("/{product_id}")
async def update_product(
    product_id: str,
    product: ProductUpdate,
    current_user: dict = Depends(require_roles(*_CATALOG_ROLES)),
):
    """Update product details"""
    # If the client explicitly sends `category`, it must be a valid non-blank
    # category -- you cannot blank-out / null-out a product's category via update
    # (same GST-default guard as create). Updates that omit `category` are
    # unaffected.
    if "category" in product.model_fields_set:
        product.category = _validate_category_or_422(product.category)

    # Statutory P3 (conservative): you may not CLEAR a product's HSN via update
    # -- an explicit blank hsn_code would leave the invoice / GSTR-1 line with no
    # HSN. Reject only an explicit blank value; omitting hsn_code (the common
    # case) is untouched, and a non-blank edit passes through unchanged. (A
    # null/omitted hsn_code is already stripped downstream, so this guards only
    # the explicit empty-string clear.)
    if (
        "hsn_code" in product.model_fields_set
        and product.hsn_code is not None
        and not str(product.hsn_code).strip()
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "HSN code cannot be cleared. Every taxable product line needs a "
                "valid HSN for the invoice and GSTR-1 export."
            ),
        )

    repo = get_product_repository()

    if repo is not None:
        existing = repo.find_by_id(product_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Product not found")

        update_data = product.model_dump(exclude_unset=True)

        # Hub Phase 0 hardening: drop explicit-null values before any persist.
        # ProductUpdate fields are Optional, so a client can send mrp:null /
        # offer_price:null -- which slips past Field(gt=0) (it only constrains a
        # PRESENT float) AND past _assert_mrp_ge_offer (it early-returns on None)
        # AND past the never-demote restamp, then $sets null onto a live
        # purchasable product's price. Mirrors the service-layer update_product
        # None-strip. A field is CLEARED with its empty value ("" / []), never null.
        update_data = {k: v for k, v in update_data.items() if v is not None}

        # Hub Phase 0: deep-merge a partial attributes patch onto the existing
        # attributes so a DRAFT's in-attributes gap (CL power/expiry_date,
        # OPTICAL_LENS index/coating) can be filled and auto-promoted by the
        # restamp below. An explicit value in the patch wins; existing keys are
        # preserved. Persisted as the full merged attributes dict.
        if "attributes" in update_data:
            update_data["attributes"] = {
                **(existing.get("attributes") or {}),
                **(update_data["attributes"] or {}),
            }
            # Catalog Dictionary: the update path must enforce the same
            # owner-configured value lists as the create door (create runs it
            # inside normalise_payload; PUT does not go through that path).
            try:
                from ..dependencies import get_db as _get_db_dep

                _dict_db = _get_db_dep()
            except Exception:  # noqa: BLE001
                _dict_db = None
            try:
                update_data["attributes"] = _pm.enforce_dictionary_values(
                    update_data.get("category") or existing.get("category"),
                    update_data["attributes"],
                    db=_dict_db,
                )
            except _pm.ProductMasterError as err:
                raise HTTPException(
                    status_code=err.status, detail=_pm_error_detail(err)
                ) from err

        # Validate modality enum if a CL modality is being set.
        if update_data.get("modality") and update_data["modality"] not in CL_MODALITIES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid modality. Allowed: {', '.join(CL_MODALITIES)}",
            )

        # Validate a scan-to-sell product barcode (EAN-13 format + check digit +
        # uniqueness) the moment one is set, so a malformed/duplicate barcode is
        # rejected loudly instead of silently saved (which would make the
        # product un-scannable at POS). Only runs when `barcode` is in the
        # payload; a blank value (clear) is allowed.
        if "barcode" in update_data:
            _validate_product_barcode_or_400(update_data["barcode"], repo, product_id)

        # Validate MRP >= Offer Price using the EFFECTIVE post-update values.
        # The old check only fired when BOTH fields were in the payload, so a
        # single-field edit -- lowering mrp below the existing offer_price, OR
        # raising offer_price above the existing mrp -- slipped a product into
        # an MRP < offer state. We merge the incoming change onto the existing
        # doc and validate regardless of which field(s) are present, mirroring
        # the create-path guard. Both share services/pricing_caps via the
        # _assert_mrp_ge_offer helper so the MRP-rule lives in one place.
        if "mrp" in update_data or "offer_price" in update_data:
            eff_mrp = update_data.get("mrp", existing.get("mrp"))
            eff_offer = update_data.get("offer_price", existing.get("offer_price"))
            _assert_mrp_ge_offer(eff_mrp, eff_offer)

        # Step-12: normalise tags on edit so the stored shape is identical to the
        # canonical create (lowercase/trim/dedupe). An explicit [] clears tags.
        if "tags" in update_data:
            update_data["tags"] = _pm.normalise_tags(update_data["tags"])

        update_data["updated_by"] = current_user.get("user_id")
        # Cataloguer attribution: the editor's display name rides next to the id
        # (JWT already carries the username). ALWAYS written when updated_by is
        # written -- falling back to the raw id -- so a stale previous editor's
        # name can never survive next to a new editor's id. Creation
        # attribution is immutable: ProductUpdate has no created_by* fields and
        # the None-strip above cannot introduce them, so created_by /
        # created_by_name are never rewritten here.
        update_data["updated_by_name"] = current_user.get("username") or current_user.get(
            "user_id"
        )

        if repo.update(product_id, update_data):
            # Compact field-classified audit row (scorecard corrections): the
            # spine PUT is the primary FE edit door, and without this row an
            # other-user pricing-only edit would be (over-)counted as an
            # approximate correction. before/after carry the patch's changed
            # top-level KEYS with light values only (heavy payloads elided) --
            # the corrections classifier reads keys, not values. Fail-soft:
            # an audit hiccup never blocks the product save.
            try:
                _changed_keys = [
                    k
                    for k in update_data.keys()
                    if k not in ("updated_by", "updated_by_name")
                ]
                if _changed_keys:
                    from ..dependencies import (
                        get_audit_repository as _get_audit_repo,
                    )

                    _audit_repo = _get_audit_repo()
                    if _audit_repo is not None:
                        _HEAVY = ("attributes", "images", "description")
                        _before: Dict[str, Any] = {}
                        _after: Dict[str, Any] = {}
                        for _k in _changed_keys:
                            if _k in _HEAVY:
                                _before[_k] = "[changed]"
                                _after[_k] = "[changed]"
                            else:
                                _before[_k] = existing.get(_k)
                                _after[_k] = update_data.get(_k)
                        _audit_repo.create(
                            {
                                "action": "product.updated",
                                "actor": current_user.get("user_id"),
                                "user_id": current_user.get("user_id"),
                                "entity_type": "product",
                                "entity_id": product_id,
                                "timestamp": datetime.now(),
                                "ts": datetime.now().isoformat(),
                                "source": "spine_put",
                                "before": _before,
                                "after": _after,
                            }
                        )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "[PRODUCTS] update audit row skipped for %s",
                    product_id,
                    exc_info=True,
                )
            # Hub Phase 0: apply the catalog-done restamp as a GUARDED single-doc
            # write -- NOT folded into the blind $set above. Auto-promotes a DRAFT
            # the edit just completed (e.g. cost_price / CL power filled in ->
            # catalog_status ACTIVE). The guard (find_one_and_update keyed on
            # catalog_status=DRAFT) means a concurrent edit that already promoted
            # the row is not clobbered, and a row that reads ACTIVE (incl. a
            # legacy row with no catalog_status) is left untouched -- never
            # demoted, never re-judged (forward-only, DECISION A).
            status_fields = _pm.apply_restamp_atomic(
                product_id, existing, update_data, product_repo=repo
            )
            # Products-convergence (inverse of catalog.py update->spine): mirror a
            # price / tier / gst / active edit onto the catalog_products twin
            # (shared id) so the storefront/PIM does not silently diverge from the
            # billing spine after a spine-side edit. Only the fields actually
            # changed are pushed (dot-notation onto the nested catalog pricing).
            # Fail-soft: a catalog-sync error never breaks the product save.
            try:
                _cat_patch: dict = {}
                if "mrp" in update_data:
                    _cat_patch["pricing.mrp"] = update_data["mrp"]
                if "offer_price" in update_data:
                    _cat_patch["pricing.offer_price"] = update_data["offer_price"]
                if "cost_price" in update_data:
                    _cat_patch["pricing.cost_price"] = update_data["cost_price"]
                if "discount_category" in update_data:
                    _dc = update_data["discount_category"]
                    _cat_patch["pricing.discount_category"] = (
                        _dc.upper() if isinstance(_dc, str) else _dc
                    )
                if "hsn_code" in update_data:
                    _cat_patch["hsn_code"] = update_data["hsn_code"]
                if "gst_rate" in update_data:
                    _cat_patch["gst_rate"] = update_data["gst_rate"]
                if "is_active" in update_data:
                    _cat_patch["is_active"] = update_data["is_active"]
                if "description" in update_data:
                    _cat_patch["description"] = update_data["description"]
                if _cat_patch:
                    from ..dependencies import get_db as _gdb

                    _conn = _gdb()
                    if _conn is not None and getattr(_conn, "is_connected", False):
                        _cat = _conn.get_collection("catalog_products")
                        if _cat is not None:
                            _cat.update_one({"id": product_id}, {"$set": _cat_patch})
            except Exception:  # noqa: BLE001
                logger.warning(
                    "[PRODUCTS] catalog mirror on update skipped for %s",
                    product_id,
                    exc_info=True,
                )
            # Step-13: recompute SMART collections (fail-soft). Use the merged doc
            # so the resolver sees the post-update tags/category/brand + status.
            merged = {**existing, **update_data, **status_fields}
            _refresh_collections_after_product(merged)
            return {"message": "Product updated", "product_id": product_id}

        raise HTTPException(status_code=500, detail="Failed to update product")

    return {"message": "Product updated"}
