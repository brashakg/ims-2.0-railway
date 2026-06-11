"""
IMS 2.0 - Products Router
==========================
Product catalog management endpoints
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from datetime import datetime
import logging
import uuid
from .auth import get_current_user, require_roles
from ..dependencies import get_product_repository

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
            # Get categories collection or distinct from products
            categories_collection = db.db.get_collection("product_categories")
            if categories_collection:
                cats = list(categories_collection.find({}, {"name": 1}))
                if cats:
                    return [c.get("name") for c in cats if c.get("name")]

            # Fallback: get distinct categories from products collection
            products_collection = db.db.get_collection("products")
            if products_collection:
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
)


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


def _canonical_door_payload(product: "ProductCreate") -> dict:
    """Map a validated flat ProductCreate into the canonical create payload the
    product_master door expects (category + attributes + pricing + identity)."""
    return {
        "category": product.category,
        "attributes": dict(product.attributes or {}),
        "mrp": product.mrp,
        "offer_price": product.offer_price,
        "sku": product.sku,
        "discount_category": product.discount_category,
        "hsn_code": product.hsn_code,
        "gst_rate": product.gst_rate,
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


def _create_via_canonical_door(
    product: "ProductCreate", current_user: dict, *, source: str
) -> dict:
    """Delegate a FORM/BULK create to the ONE canonical product_master door.

    Maps ProductMasterError -> the same HTTP codes this router already used
    (422 validation / 400 MRP-or-SKU). Returns the created canonical spine doc
    (with product_id + sku). Assumes the repo is available (caller checked)."""
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

            variant_repo = CatalogVariantRepository(db.get_collection("catalog_variants"))
    except Exception:  # noqa: BLE001 - mirror is fail-soft; never block a create
        variant_repo = None

    try:
        return _pm.create_via_door(
            _canonical_door_payload(product),
            source=source,
            actor=current_user.get("user_id"),
            extra_fields=_form_extra_fields(product),
            product_repo=get_product_repository(),
            variant_repo=variant_repo,
            audit_repo=_get_audit_repository(),
            db=db,
        )
    except _pm.ProductMasterError as err:
        raise HTTPException(status_code=err.status, detail=err.message) from err


def _build_product_data(product: "ProductCreate", created_by) -> dict:
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

    # Persist optional identity fields top-level only when provided (additive).
    for _f in _OPTIONAL_PRODUCT_FIELDS:
        _v = getattr(product, _f, None)
        if _v is not None:
            product_data[_f] = _v

    return product_data


class ProductCreate(BaseModel):
    sku: str
    category: str
    brand: str
    model: str
    variant: Optional[str] = None
    color: Optional[str] = None
    size: Optional[str] = None
    mrp: float = Field(..., gt=0)
    offer_price: float = Field(..., gt=0)
    hsn_code: Optional[str] = None
    # Persisted as `gst_rate` — the key seed data, reports and billing all
    # read. Was previously `tax_rate`, which no reader looked at.
    gst_rate: Optional[float] = None
    attributes: Optional[dict] = None
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


class ProductUpdate(BaseModel):
    category: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    color: Optional[str] = None
    mrp: Optional[float] = Field(None, gt=0)
    offer_price: Optional[float] = Field(None, gt=0)
    hsn_code: Optional[str] = None
    gst_rate: Optional[float] = None
    is_active: Optional[bool] = None
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
    # ---- Per-product reorder configuration. Moved here from the retired
    # /admin/products PUT (the Reorder dashboard's only writer) so reorder
    # settings persist through the validated path. All optional + additive. ----
    reorder_point: Optional[int] = Field(None, ge=0)
    reorder_quantity: Optional[int] = Field(None, ge=0)
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
    store_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
):
    """List active catalog products with optional search / brand / category
    filters. `store_id` is accepted (kept for backwards-compat) but does NOT
    filter the result set - the canonical Stock Ledger at /inventory/stock
    is the per-store on-hand view. POS search uses this endpoint so a SKU
    that's in the catalog can always be looked up at any store the user has
    access to."""
    from ..services.cache import cache

    # Build cache key from query params. NOTE: store_id is cache-key/back-compat
    # only -- list_products is a GLOBAL catalog lookup (POS must find any SKU at
    # any store the user can access), so it is intentionally NOT store-scoped and
    # must not call validate_store_access here.
    active_store = store_id or current_user.get("active_store_id", "")
    cache_key = f"products:{active_store}:{category}:{brand}:{search}:{skip}:{limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    repo = get_product_repository()

    if repo is not None:
        if search:
            products = repo.search_products(search, category)
        elif brand:
            products = repo.find_by_brand(brand, category)
        elif category:
            products = repo.find_by_category(category)
        else:
            products = repo.find_many({}, skip=skip, limit=limit)

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

        total = len(products)
        result = {"products": products, "total": total}
        cache.set(cache_key, result, ttl=cache.TTL_MEDIUM)
        return result

    return {"products": [], "total": 0}


@router.post("", status_code=201)
async def create_product(
    product: ProductCreate,
    current_user: dict = Depends(require_roles(*_CATALOG_ROLES)),
):
    """Create a new product.

    Unification step-9: delegates the validate + create CORE to the ONE
    canonical product-create door (product_master.create_via_door, source=FORM)
    so this door enforces the SAME registry rulebook as /products/bulk-create
    and /catalog/products. Category, MRP>=offer, and category->GST/HSN are still
    enforced; STRICT now adds the registry's category-conditional required-field
    gate (e.g. a FRAME without colour_code is rejected at entry). Auth/RBAC and
    the {product_id, sku} response shape are unchanged.
    """
    # Block save when category is blank/null/missing (server-side guard for the
    # GST-default bug: an uncategorized product would otherwise fall back to a
    # default GST rate). Normalizes the category to the validated value.
    product.category = _validate_category_or_422(product.category)

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
    try:
        _pm.build_canonical_product(
            _canonical_door_payload(product), source="FORM"
        )
    except _pm.ProductMasterError as err:
        raise HTTPException(status_code=err.status, detail=err.message) from err

    repo = get_product_repository()

    if repo is not None:
        created = _create_via_canonical_door(product, current_user, source="FORM")
        if created:
            # Invalidate product cache for this store
            from ..services.cache import cache

            cache.delete_pattern(
                f"products:{current_user.get('active_store_id', '')}:*"
            )
            return {"product_id": created["product_id"], "sku": created["sku"]}

        raise HTTPException(status_code=500, detail="Failed to create product")

    return {"product_id": str(uuid.uuid4()), "sku": product.sku}


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


def _validate_bulk_row(product: ProductCreate, seen_skus: set) -> List[str]:
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
    """
    errors: List[str] = []

    # Category (reuse the single-create validator; capture its 422 message).
    try:
        product.category = _validate_category_or_422(product.category)
    except HTTPException as exc:
        errors.append(str(exc.detail))

    # Registry strict required-field gate (step-9). Build the canonical payload
    # the same way the FORM door does so a missing colour_code / power / expiry
    # is reported here exactly as the FORM door would 422 on it. Captures the
    # ProductMasterError message instead of raising (bulk reports per row).
    try:
        _pm.build_canonical_product(
            _canonical_door_payload(product), source="BULK"
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
        errors = _validate_bulk_row(product, seen_skus)

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
                product, current_user, source="BULK"
            )
        except HTTPException as exc:
            logger.warning("[BULK-CREATE] create rejected for %s: %s", sku_norm, exc.detail)
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
    repo = get_product_repository()

    if repo is not None:
        brands = repo.get_brands(category)
        return {"brands": brands}

    return {"brands": []}


@router.get("/categories/list")
async def list_categories(current_user: dict = Depends(get_current_user)):
    """List all product categories"""
    categories = _get_categories_from_db()
    return {"categories": categories}


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

    repo = get_product_repository()

    if repo is not None:
        existing = repo.find_by_id(product_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Product not found")

        update_data = product.model_dump(exclude_unset=True)

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

        update_data["updated_by"] = current_user.get("user_id")

        if repo.update(product_id, update_data):
            return {"message": "Product updated", "product_id": product_id}

        raise HTTPException(status_code=500, detail="Failed to update product")

    return {"message": "Product updated"}
