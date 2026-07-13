"""
IMS 2.0 - Unified Product Master router (PM / N5)
=================================================
Thin router over services/product_master.py. Mounted at /api/v1/products
(alongside the legacy products router). It adds the unified-master surface:

  GET  /api/v1/products/categories                       -> canonical category specs
  GET  /api/v1/products/categories/{category}/fields     -> one category's fields
  POST /api/v1/products/sku-preview                       -> deterministic SKU preview
  POST /api/v1/products/master                            -> create via triple-write
  PUT  /api/v1/products/master/{product_id}               -> update via engine

The legacy POST /api/v1/products (products.py) is left in place for back-compat;
this router introduces the engine-backed path under explicit sub-paths so the
two never collide on the FastAPI router-precedence (first-registered-wins) rule.

No emoji (Windows cp1252). Light/restrained -- this is backend only.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import get_current_user, require_roles
from ..dependencies import (
    get_product_repository,
    get_audit_repository,
    get_db,
)
from ..services import product_master as pm

router = APIRouter()

# Catalog-mutation roles. Mirrors products.py _CATALOG_ROLES (ADMIN,
# CATALOG_MANAGER); SUPERADMIN auto-passes via require_roles.
_CATALOG_ROLES = ("ADMIN", "CATALOG_MANAGER")


# ---------------------------------------------------------------------------
# Repo helpers (fail-soft, mirror the dependency pattern)
# ---------------------------------------------------------------------------


def _catalog_variant_repo():
    db = get_db()
    if db is not None and getattr(db, "is_connected", False):
        from database.repositories.catalog_variant_repository import (
            CatalogVariantRepository,
        )

        return CatalogVariantRepository(db.get_collection("catalog_variants"))
    return None


def _actor(current_user: dict) -> str:
    return current_user.get("user_id") or current_user.get("username") or "unknown"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SkuPreviewRequest(BaseModel):
    category: str
    attributes: Dict[str, Any] = Field(default_factory=dict)


class ProductMasterCreate(BaseModel):
    category: str
    attributes: Dict[str, Any] = Field(default_factory=dict)
    mrp: float = Field(..., gt=0)
    offer_price: float = Field(..., gt=0)
    # Optional: engine mints the SKU when omitted; a supplied (legacy) SKU is
    # accepted as-is when it passes the permissive guard.
    sku: Optional[str] = None
    discount_category: Optional[str] = None
    hsn_code: Optional[str] = None
    gst_rate: Optional[float] = None
    country_of_origin: Optional[str] = None
    warranty_months: Optional[int] = Field(default=None, ge=0)
    weight_grams: Optional[float] = None


class ProductMasterUpdate(BaseModel):
    mrp: Optional[float] = Field(default=None, gt=0)
    offer_price: Optional[float] = Field(default=None, gt=0)
    hsn_code: Optional[str] = None
    gst_rate: Optional[float] = None
    discount_category: Optional[str] = None
    is_active: Optional[bool] = None
    country_of_origin: Optional[str] = None
    warranty_months: Optional[int] = Field(default=None, ge=0)
    weight_grams: Optional[float] = None


def _raise(err: "pm.ProductMasterError"):
    # A duplicate 409 carries the existing row in `conflict`; surface it as a
    # structured detail (mirrors products._pm_error_detail) so the FE can render
    # the "add stock / a variant" link. Plain string for every other error.
    conflict = getattr(err, "conflict", None)
    detail = err.message
    if conflict:
        detail = {
            "message": err.message,
            "code": getattr(err, "code", None) or "DUPLICATE_PRODUCT",
            "existing": conflict,
        }
    raise HTTPException(status_code=err.status, detail=detail)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get("/master/categories")
async def list_categories(current_user: dict = Depends(get_current_user)):
    """All canonical product-master category specs (long-form value + SKU prefix
    + required/optional fields). Mounted under /master/ -- a bare /products/categories
    is shadowed by the legacy GET /products/{product_id} (first-registered-wins)."""
    return {"categories": pm.all_category_specs()}


@router.get("/master/categories/{category}/fields")
async def category_fields(
    category: str, current_user: dict = Depends(get_current_user)
):
    """The required/optional fields for one category (any input form)."""
    spec = pm.category_spec(category)
    if spec is None:
        raise HTTPException(status_code=404, detail="Category not found")
    return {
        "category": spec.canonical,
        "category_name": spec.display,
        "sku_prefix": spec.prefix,
        "required_fields": list(spec.required),
        "optional_fields": list(spec.optional),
    }


# ---------------------------------------------------------------------------
# SKU preview
# ---------------------------------------------------------------------------


@router.post("/sku-preview")
async def sku_preview(
    body: SkuPreviewRequest,
    current_user: dict = Depends(require_roles(*_CATALOG_ROLES)),
):
    """Deterministic SKU preview per the Excel rule (PREFIX+BRAND+MODEL+COLOR+
    SIZE). Does NOT allocate a collision counter (preview is read-only)."""
    try:
        sku = pm.build_sku(body.category, body.attributes)
    except pm.ProductMasterError as err:
        _raise(err)
    return {"category": pm.resolve_category(body.category), "sku": sku}


# ---------------------------------------------------------------------------
# Create (triple-write) + update
# ---------------------------------------------------------------------------


@router.post("/master", status_code=201)
async def create_master_product(
    body: ProductMasterCreate,
    current_user: dict = Depends(require_roles(*_CATALOG_ROLES)),
):
    """Create a product via the SPINE-FIRST + COMPENSATION triple-write.

    The Mongo `products` spine is written first + alone (single-doc, atomic);
    the catalog/external mirror is best-effort + GATED off-by-default. A mirror
    failure never corrupts the spine.
    """
    try:
        created = pm.create_product(
            category=body.category,
            attributes=body.attributes,
            mrp=body.mrp,
            offer_price=body.offer_price,
            actor=_actor(current_user),
            actor_name=current_user.get("username"),
            sku=body.sku,
            discount_category=body.discount_category,
            hsn_code=body.hsn_code,
            gst_rate=body.gst_rate,
            country_of_origin=body.country_of_origin,
            warranty_months=body.warranty_months,
            weight_grams=body.weight_grams,
            product_repo=get_product_repository(),
            # catalog_products PIM doc is written via the db path inside the
            # engine (no dedicated repo); the variant tier uses its repo.
            catalog_repo=None,
            variant_repo=_catalog_variant_repo(),
            audit_repo=get_audit_repository(),
            db=get_db(),
        )
    except pm.ProductMasterError as err:
        _raise(err)
    return {
        "product_id": created.get("product_id"),
        "sku": created.get("sku"),
        "pim_product_id": created.get("pim_product_id"),
        "sync_status": created.get("sync_status"),
    }


@router.put("/master/{product_id}")
async def update_master_product(
    product_id: str,
    body: ProductMasterUpdate,
    current_user: dict = Depends(require_roles(*_CATALOG_ROLES)),
):
    """Update mutable product fields. Enforces offer<=MRP in both directions."""
    try:
        updated = pm.update_product(
            product_id=product_id,
            patch=body.model_dump(exclude_none=True),
            actor=_actor(current_user),
            actor_name=current_user.get("username"),
            product_repo=get_product_repository(),
            audit_repo=get_audit_repository(),
            db=get_db(),
        )
    except pm.ProductMasterError as err:
        _raise(err)
    return {
        "product_id": product_id,
        "mrp": updated.get("mrp"),
        "offer_price": updated.get("offer_price"),
        "discount_category": updated.get("discount_category"),
    }
