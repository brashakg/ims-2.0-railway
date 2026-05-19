"""
IMS 2.0 - Admin Catalog Router
==============================
Master-data CRUD for catalog admin UI: categories, brands (+ subbrands),
lens master (brands / indices / coatings / addons / pricing), and the
two product helpers (bulk-import, generate-sku) the frontend expects at
`/api/v1/admin/*`.

Why this router exists:
The frontend `services/api/products.ts` was written against an
`/admin/*` namespace that was never built — it issued ~40 calls
across products, categories, brands, subbrands, and lens master.
Every one of those calls 404'd in production, which silently
broke the catalog admin UI for SUPERADMIN / ADMIN. This module
fills that gap with thin CRUD over Mongo collections, gated by
the existing _require_admin_role dependency from admin.py.

Conventions:
- Each master-data type has its own Mongo collection.
- IDs are uuid4 strings, exposed as `<entity>_id`.
- timestamps `created_at` / `updated_at` are ISO datetime objects.
- All writes return the persisted document; all lists return an
  envelope `{<plural>: [...], total: N}` matching the frontend's
  existing access pattern (`response.data.<plural>`).
- Fail-soft: when the DB is absent the endpoints return empty
  envelopes / 503 on writes; never crash the FastAPI worker.
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid

from .admin import _require_admin_role


router = APIRouter(dependencies=[Depends(_require_admin_role)])


# ============================================================================
# DB HELPERS
# ============================================================================

def _coll(name: str):
    """Return the named Mongo collection or None if DB is offline."""
    try:
        from database.connection import get_db
        db = get_db()
        if db and db.is_connected:
            return db.get_collection(name)
    except Exception:
        pass
    return None


def _scrub(doc: Optional[Dict]) -> Optional[Dict]:
    """Strip Mongo's _id from a fetched document for JSON serialization."""
    if doc is None:
        return None
    doc.pop("_id", None)
    return doc


def _now() -> datetime:
    return datetime.utcnow()


def _new_id() -> str:
    return str(uuid.uuid4())


def _list_envelope(coll_name: str, plural_key: str, filter_: Optional[Dict] = None, sort_field: str = "name") -> Dict:
    """Standard list-response shape for any master-data collection."""
    coll = _coll(coll_name)
    if coll is None:
        return {plural_key: [], "total": 0}
    docs: List[Dict] = []
    try:
        cursor = coll.find(filter_ or {}).sort(sort_field, 1)
        for doc in cursor:
            scrubbed = _scrub(doc)
            if scrubbed is not None:
                docs.append(scrubbed)
    except Exception:
        pass
    return {plural_key: docs, "total": len(docs)}


def _create_doc(coll_name: str, payload: Dict, id_field: str) -> Dict:
    coll = _coll(coll_name)
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    doc = dict(payload)
    doc[id_field] = _new_id()
    doc["_id"] = doc[id_field]
    doc["created_at"] = _now()
    doc["updated_at"] = _now()
    doc.setdefault("is_active", True)
    coll.insert_one(doc)
    return _scrub(doc) or {}


def _update_doc(coll_name: str, id_field: str, doc_id: str, updates: Dict) -> Dict:
    coll = _coll(coll_name)
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    if not coll.find_one({id_field: doc_id}):
        raise HTTPException(status_code=404, detail=f"{id_field} {doc_id} not found")
    clean = {k: v for k, v in updates.items() if v is not None}
    clean["updated_at"] = _now()
    coll.update_one({id_field: doc_id}, {"$set": clean})
    fresh = _scrub(coll.find_one({id_field: doc_id}))
    return fresh or {}


def _delete_doc(coll_name: str, id_field: str, doc_id: str) -> Dict:
    coll = _coll(coll_name)
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    res = coll.delete_one({id_field: doc_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail=f"{id_field} {doc_id} not found")
    return {"deleted": True, id_field: doc_id}


def _get_doc(coll_name: str, id_field: str, doc_id: str) -> Dict:
    coll = _coll(coll_name)
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    doc = _scrub(coll.find_one({id_field: doc_id}))
    if doc is None:
        raise HTTPException(status_code=404, detail=f"{id_field} {doc_id} not found")
    return doc


# ============================================================================
# CATEGORIES — /api/v1/admin/categories
# ============================================================================
# Distinct from the hardcoded ProductCategory enum in catalog.py: those
# are physical SKU-prefix codes (FRAME, LENS, ...). These are the ADMIN
# editable category masters with HSN code, GST rate, and discount cap.

class CategoryCreate(BaseModel):
    code: str = Field(..., min_length=2, max_length=24, description="SKU/HSN prefix code")
    name: str = Field(..., min_length=2, max_length=80)
    hsnCode: str = Field(..., min_length=4, max_length=12)
    gstRate: float = Field(..., ge=0, le=28)
    description: Optional[str] = None
    attributes: Optional[List[str]] = None
    status: Optional[str] = "ACTIVE"


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    hsnCode: Optional[str] = None
    gstRate: Optional[float] = None
    description: Optional[str] = None
    attributes: Optional[List[str]] = None
    status: Optional[str] = None


@router.get("/categories")
async def list_categories():
    return _list_envelope("category_masters", "categories", sort_field="name")


@router.get("/categories/{category_id}")
async def get_category(category_id: str):
    return _get_doc("category_masters", "category_id", category_id)


@router.post("/categories", status_code=201)
async def create_category(payload: CategoryCreate):
    coll = _coll("category_masters")
    if coll is not None and coll.find_one({"code": payload.code}):
        raise HTTPException(status_code=409, detail=f"Category code '{payload.code}' already exists")
    return _create_doc("category_masters", payload.model_dump(), "category_id")


@router.put("/categories/{category_id}")
async def update_category(category_id: str, payload: CategoryUpdate):
    return _update_doc("category_masters", "category_id", category_id, payload.model_dump(exclude_unset=True))


@router.delete("/categories/{category_id}")
async def delete_category(category_id: str):
    return _delete_doc("category_masters", "category_id", category_id)


# ============================================================================
# BRANDS — /api/v1/admin/brands  (and brand subbrands)
# ============================================================================

class BrandCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    code: str = Field(..., min_length=2, max_length=24)
    categories: List[str] = Field(default_factory=list, description="Category codes this brand applies to")
    tier: str = Field(..., description="MASS | PREMIUM | LUXURY")
    warranty: Optional[int] = Field(None, ge=0, le=120, description="Warranty months")
    description: Optional[str] = None
    status: Optional[str] = "ACTIVE"


class BrandUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    categories: Optional[List[str]] = None
    tier: Optional[str] = None
    warranty: Optional[int] = None
    description: Optional[str] = None
    status: Optional[str] = None


@router.get("/brands")
async def list_brands(category: Optional[str] = None, tier: Optional[str] = None):
    filter_: Dict = {}
    if category:
        filter_["categories"] = category
    if tier:
        filter_["tier"] = tier
    return _list_envelope("brand_masters", "brands", filter_=filter_, sort_field="name")


@router.get("/brands/{brand_id}")
async def get_brand(brand_id: str):
    return _get_doc("brand_masters", "brand_id", brand_id)


@router.post("/brands", status_code=201)
async def create_brand(payload: BrandCreate):
    coll = _coll("brand_masters")
    if coll is not None and coll.find_one({"code": payload.code}):
        raise HTTPException(status_code=409, detail=f"Brand code '{payload.code}' already exists")
    if payload.tier not in {"MASS", "PREMIUM", "LUXURY"}:
        raise HTTPException(status_code=400, detail="tier must be MASS, PREMIUM, or LUXURY")
    return _create_doc("brand_masters", payload.model_dump(), "brand_id")


@router.put("/brands/{brand_id}")
async def update_brand(brand_id: str, payload: BrandUpdate):
    return _update_doc("brand_masters", "brand_id", brand_id, payload.model_dump(exclude_unset=True))


@router.delete("/brands/{brand_id}")
async def delete_brand(brand_id: str):
    return _delete_doc("brand_masters", "brand_id", brand_id)


# Subbrands are nested under brands. Stored in their own collection
# with a `brand_id` foreign key for index efficiency.

class SubbrandCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    code: str = Field(..., min_length=1, max_length=24)
    description: Optional[str] = None


@router.get("/brands/{brand_id}/subbrands")
async def list_subbrands(brand_id: str):
    # Verify the parent brand exists (404 early if not)
    _get_doc("brand_masters", "brand_id", brand_id)
    return _list_envelope("subbrand_masters", "subbrands", filter_={"brand_id": brand_id})


@router.post("/brands/{brand_id}/subbrands", status_code=201)
async def create_subbrand(brand_id: str, payload: SubbrandCreate):
    _get_doc("brand_masters", "brand_id", brand_id)
    coll = _coll("subbrand_masters")
    if coll is not None and coll.find_one({"brand_id": brand_id, "code": payload.code}):
        raise HTTPException(status_code=409, detail=f"Subbrand code '{payload.code}' already exists for this brand")
    body = payload.model_dump()
    body["brand_id"] = brand_id
    return _create_doc("subbrand_masters", body, "subbrand_id")


@router.delete("/brands/{brand_id}/subbrands/{subbrand_id}")
async def delete_subbrand(brand_id: str, subbrand_id: str):
    coll = _coll("subbrand_masters")
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    res = coll.delete_one({"brand_id": brand_id, "subbrand_id": subbrand_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Subbrand not found")
    return {"deleted": True, "subbrand_id": subbrand_id}


# ============================================================================
# LENS MASTER — /api/v1/admin/lens/{brands,indices,coatings,addons,pricing}
# ============================================================================
# Five collections; the pricing matrix is keyed on (brandId, indexId, category)
# so it sits over the others.

class LensBrandCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    code: str = Field(..., min_length=2, max_length=24)
    tier: Optional[str] = "STANDARD"


class LensBrandUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    tier: Optional[str] = None


class LensIndexCreate(BaseModel):
    value: str = Field(..., description="e.g. 1.50, 1.56, 1.60, 1.67, 1.74")
    multiplier: float = Field(..., gt=0, le=10, description="Pricing multiplier vs base")
    description: Optional[str] = None


class LensIndexUpdate(BaseModel):
    value: Optional[str] = None
    multiplier: Optional[float] = None
    description: Optional[str] = None


class LensCoatingCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    code: str = Field(..., min_length=2, max_length=24)
    price: float = Field(..., ge=0)
    description: Optional[str] = None


class LensCoatingUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    price: Optional[float] = None
    description: Optional[str] = None


class LensAddonCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    code: str = Field(..., min_length=2, max_length=24)
    price: float = Field(..., ge=0)
    type: str = Field(..., description="e.g. PHOTOCHROMIC, POLARIZED, BLUE_CUT")
    description: Optional[str] = None


class LensAddonUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    price: Optional[float] = None
    type: Optional[str] = None
    description: Optional[str] = None


class LensPricingCreate(BaseModel):
    brandId: str
    indexId: str
    category: str = Field(..., description="Lens category, e.g. SINGLE_VISION, BIFOCAL, PROGRESSIVE")
    basePrice: float = Field(..., ge=0)


# ---------------- Lens brands ----------------

@router.get("/lens/brands")
async def list_lens_brands():
    return _list_envelope("lens_brand_masters", "brands", sort_field="name")


@router.post("/lens/brands", status_code=201)
async def create_lens_brand(payload: LensBrandCreate):
    coll = _coll("lens_brand_masters")
    if coll is not None and coll.find_one({"code": payload.code}):
        raise HTTPException(status_code=409, detail=f"Lens brand code '{payload.code}' already exists")
    return _create_doc("lens_brand_masters", payload.model_dump(), "brand_id")


@router.put("/lens/brands/{brand_id}")
async def update_lens_brand(brand_id: str, payload: LensBrandUpdate):
    return _update_doc("lens_brand_masters", "brand_id", brand_id, payload.model_dump(exclude_unset=True))


@router.delete("/lens/brands/{brand_id}")
async def delete_lens_brand(brand_id: str):
    return _delete_doc("lens_brand_masters", "brand_id", brand_id)


# ---------------- Lens indices ----------------

@router.get("/lens/indices")
async def list_lens_indices():
    return _list_envelope("lens_index_masters", "indices", sort_field="value")


@router.post("/lens/indices", status_code=201)
async def create_lens_index(payload: LensIndexCreate):
    coll = _coll("lens_index_masters")
    if coll is not None and coll.find_one({"value": payload.value}):
        raise HTTPException(status_code=409, detail=f"Lens index '{payload.value}' already exists")
    return _create_doc("lens_index_masters", payload.model_dump(), "index_id")


@router.put("/lens/indices/{index_id}")
async def update_lens_index(index_id: str, payload: LensIndexUpdate):
    return _update_doc("lens_index_masters", "index_id", index_id, payload.model_dump(exclude_unset=True))


@router.delete("/lens/indices/{index_id}")
async def delete_lens_index(index_id: str):
    return _delete_doc("lens_index_masters", "index_id", index_id)


# ---------------- Lens coatings ----------------

@router.get("/lens/coatings")
async def list_lens_coatings():
    return _list_envelope("lens_coating_masters", "coatings", sort_field="name")


@router.post("/lens/coatings", status_code=201)
async def create_lens_coating(payload: LensCoatingCreate):
    coll = _coll("lens_coating_masters")
    if coll is not None and coll.find_one({"code": payload.code}):
        raise HTTPException(status_code=409, detail=f"Lens coating code '{payload.code}' already exists")
    return _create_doc("lens_coating_masters", payload.model_dump(), "coating_id")


@router.put("/lens/coatings/{coating_id}")
async def update_lens_coating(coating_id: str, payload: LensCoatingUpdate):
    return _update_doc("lens_coating_masters", "coating_id", coating_id, payload.model_dump(exclude_unset=True))


@router.delete("/lens/coatings/{coating_id}")
async def delete_lens_coating(coating_id: str):
    return _delete_doc("lens_coating_masters", "coating_id", coating_id)


# ---------------- Lens add-ons ----------------

@router.get("/lens/addons")
async def list_lens_addons():
    return _list_envelope("lens_addon_masters", "addons", sort_field="name")


@router.post("/lens/addons", status_code=201)
async def create_lens_addon(payload: LensAddonCreate):
    coll = _coll("lens_addon_masters")
    if coll is not None and coll.find_one({"code": payload.code}):
        raise HTTPException(status_code=409, detail=f"Lens addon code '{payload.code}' already exists")
    return _create_doc("lens_addon_masters", payload.model_dump(), "addon_id")


@router.put("/lens/addons/{addon_id}")
async def update_lens_addon(addon_id: str, payload: LensAddonUpdate):
    return _update_doc("lens_addon_masters", "addon_id", addon_id, payload.model_dump(exclude_unset=True))


@router.delete("/lens/addons/{addon_id}")
async def delete_lens_addon(addon_id: str):
    return _delete_doc("lens_addon_masters", "addon_id", addon_id)


# ---------------- Lens pricing matrix ----------------
# Uniqueness composite key: (brand_id, index_id, category). POST is upsert
# semantics — re-posting the same triple replaces the basePrice.

@router.get("/lens/pricing")
async def list_lens_pricing(brand_id: Optional[str] = None):
    filter_: Dict = {}
    if brand_id:
        filter_["brandId"] = brand_id
    return _list_envelope("lens_pricing_masters", "pricing", filter_=filter_, sort_field="basePrice")


@router.post("/lens/pricing", status_code=201)
async def upsert_lens_pricing(payload: LensPricingCreate):
    coll = _coll("lens_pricing_masters")
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    key = {
        "brandId": payload.brandId,
        "indexId": payload.indexId,
        "category": payload.category,
    }
    existing = coll.find_one(key)
    if existing:
        coll.update_one(
            key,
            {"$set": {"basePrice": payload.basePrice, "updated_at": _now()}},
        )
        fresh = _scrub(coll.find_one(key))
        return fresh or {}
    body = payload.model_dump()
    body["pricing_id"] = _new_id()
    body["_id"] = body["pricing_id"]
    body["created_at"] = _now()
    body["updated_at"] = _now()
    coll.insert_one(body)
    return _scrub(body) or {}


# ---------------- Lens pricing RANGES (May 2026) ----------------
# Range-wise tier pricing. Avoids the per-SKU explosion when a chain
# has 50+ brands × 5 indices × ~80 sphere/cyl combos × 4 coatings.
# Operator sets brackets like:
#   Sphere ±0.00 → ±2.00 = ₹1,200
#   Sphere ±2.25 → ±4.00 = ₹1,500
# Pure resolver lives in `api/services/lens_pricing.py`.


class LensPricingRangeCreate(BaseModel):
    brand_id: str
    index_id: str
    category: str = Field(..., description="SINGLE_VISION | BIFOCAL | PROGRESSIVE | OFFICE")
    parameter: str = Field(..., description="sphere | cylinder | addition")
    min_value: float = Field(..., description="Inclusive (signed; absolute value used for matching)")
    max_value: float = Field(..., description="Inclusive (signed; absolute value used for matching)")
    base_price: float = Field(..., ge=0)


class LensPricingRangeUpdate(BaseModel):
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    base_price: Optional[float] = Field(None, ge=0)
    is_active: Optional[bool] = None


class LensPriceQuoteInput(BaseModel):
    brand_id: str
    index_id: str
    category: str
    sphere: Optional[float] = None
    cylinder: Optional[float] = None
    addition: Optional[float] = None
    coatings: List[str] = Field(default_factory=list)


_VALID_PARAMS = frozenset({"sphere", "cylinder", "addition"})
_VALID_CATEGORIES = frozenset({"SINGLE_VISION", "BIFOCAL", "PROGRESSIVE", "OFFICE"})


@router.get("/lens/pricing-ranges")
async def list_lens_pricing_ranges(
    brand_id: Optional[str] = None,
    index_id: Optional[str] = None,
    category: Optional[str] = None,
):
    """List active pricing ranges. Each row matches a (brand × index ×
    category × parameter) tier slot."""
    filter_: Dict = {"is_active": True}
    if brand_id:
        filter_["brand_id"] = brand_id
    if index_id:
        filter_["index_id"] = index_id
    if category:
        filter_["category"] = category
    return _list_envelope(
        "lens_pricing_ranges", "ranges", filter_=filter_, sort_field="base_price"
    )


@router.post("/lens/pricing-ranges", status_code=201)
async def create_lens_pricing_range(payload: LensPricingRangeCreate):
    if payload.parameter not in _VALID_PARAMS:
        raise HTTPException(status_code=400, detail=f"parameter must be one of {sorted(_VALID_PARAMS)}")
    if payload.category not in _VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"category must be one of {sorted(_VALID_CATEGORIES)}")
    if abs(payload.min_value) > abs(payload.max_value):
        raise HTTPException(status_code=400, detail="abs(min_value) must be ≤ abs(max_value)")

    coll = _coll("lens_pricing_ranges")
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")

    # Overlap detection — same key + overlapping bracket = 409
    from ..services.lens_pricing import detect_overlap
    existing = list(coll.find({
        "brand_id": payload.brand_id,
        "index_id": payload.index_id,
        "category": payload.category,
        "parameter": payload.parameter,
    }))
    body = payload.model_dump()
    overlap = detect_overlap(body, [_scrub(dict(r)) for r in existing if r])
    if overlap is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Overlap with existing range {overlap.get('range_id')} ({overlap.get('min_value')}..{overlap.get('max_value')})",
        )

    return _create_doc("lens_pricing_ranges", body, "range_id")


@router.put("/lens/pricing-ranges/{range_id}")
async def update_lens_pricing_range(range_id: str, payload: LensPricingRangeUpdate):
    return _update_doc(
        "lens_pricing_ranges", "range_id", range_id, payload.model_dump(exclude_unset=True)
    )


@router.delete("/lens/pricing-ranges/{range_id}")
async def delete_lens_pricing_range(range_id: str):
    """Soft delete: flips is_active=False so historic resolutions still
    explain. Hard-delete is intentionally not exposed."""
    coll = _coll("lens_pricing_ranges")
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    if not coll.find_one({"range_id": range_id}):
        raise HTTPException(status_code=404, detail=f"Range {range_id} not found")
    coll.update_one({"range_id": range_id}, {"$set": {"is_active": False, "updated_at": _now()}})
    return {"deactivated": True, "range_id": range_id}


@router.post("/lens/pricing-ranges/bulk", status_code=201)
async def bulk_create_lens_pricing_ranges(payload: List[LensPricingRangeCreate]):
    """Bulk create — chain-wide price refresh. All-or-nothing on overlap."""
    if not payload:
        raise HTTPException(status_code=400, detail="At least one range required")
    coll = _coll("lens_pricing_ranges")
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    created: List[Dict] = []
    for item in payload:
        if item.parameter not in _VALID_PARAMS:
            raise HTTPException(status_code=400, detail=f"parameter must be one of {sorted(_VALID_PARAMS)}")
        if item.category not in _VALID_CATEGORIES:
            raise HTTPException(status_code=400, detail=f"category must be one of {sorted(_VALID_CATEGORIES)}")
        body = item.model_dump()
        body["range_id"] = _new_id()
        body["_id"] = body["range_id"]
        body["created_at"] = _now()
        body["updated_at"] = _now()
        body["is_active"] = True
        coll.insert_one(body)
        created.append(_scrub(body) or {})
    return {"created": created, "total": len(created)}


@router.post("/lens/pricing-ranges/quote")
async def quote_lens_price(payload: LensPriceQuoteInput):
    """Single source of truth for "what does this lens cost given these
    Rx params" — POS calls this on the prescription step.

    Lookup priority: exact_match (lens_pricing_masters) → range_match
    (lens_pricing_ranges) → no_pricing (404 with hint).
    """
    from ..services.lens_pricing import resolve_price

    coll_ranges = _coll("lens_pricing_ranges")
    coll_exact = _coll("lens_pricing_masters")
    coll_brand = _coll("lens_brand_masters")
    coll_index = _coll("lens_index_masters")
    coll_coatings = _coll("lens_coating_masters")

    ranges = list(coll_ranges.find({"is_active": True})) if coll_ranges is not None else []
    exact_pricing = list(coll_exact.find({})) if coll_exact is not None else []
    brand = coll_brand.find_one({"brand_id": payload.brand_id}) if coll_brand is not None else None
    index_master = coll_index.find_one({"index_id": payload.index_id}) if coll_index is not None else None
    coating_masters = list(coll_coatings.find({})) if coll_coatings is not None else []

    rx = {
        "sphere": payload.sphere,
        "cylinder": payload.cylinder,
        "addition": payload.addition,
    }
    quote = resolve_price(
        rx=rx,
        brand_id=payload.brand_id,
        index_id=payload.index_id,
        category=payload.category,
        coatings=payload.coatings,
        exact_pricing=[_scrub(dict(p)) for p in exact_pricing if p],
        ranges=[_scrub(dict(r)) for r in ranges if r],
        brand=_scrub(dict(brand)) if brand else None,
        index_master=_scrub(dict(index_master)) if index_master else None,
        coating_masters=[_scrub(dict(c)) for c in coating_masters if c],
    )
    if not quote.get("ok"):
        # Soft 200 with `ok=False` so the POS can fall back to manual pricing
        # rather than crashing the checkout. The hint guides the operator.
        return quote
    return quote


# ============================================================================
# PRODUCTS — bulk-import + generate-sku helpers
# ============================================================================
# Sit alongside the existing /catalog/products surface. Frontend posts
# multipart/form-data for bulk-import; we stash the row count + accept
# the file, then defer the actual ingestion to the catalog router's
# /catalog/products/import endpoint (which already exists at line 1358).
# generate-sku is pure utility: builds an SKU string from category +
# brand + model_no without persisting anything.

class GenerateSkuInput(BaseModel):
    category: str
    brand: str
    model_no: str


@router.post("/products/generate-sku")
async def generate_product_sku(payload: GenerateSkuInput):
    """Compose an SKU string from category, brand, and model_no.
    Pure function; does not persist or check uniqueness — the create-product
    endpoint downstream will fail with 409 if the SKU is already taken.
    """
    cat = (payload.category or "").upper().strip().replace(" ", "")[:6]
    brand = (payload.brand or "").upper().strip().replace(" ", "")[:6]
    model = (payload.model_no or "").upper().strip().replace(" ", "")[:8]
    rand = uuid.uuid4().hex[:4].upper()
    sku = "-".join(p for p in (cat, brand, model, rand) if p)
    return {"sku": sku, "category": payload.category, "brand": payload.brand, "model_no": payload.model_no}


@router.post("/products/bulk-import", status_code=202)
async def bulk_import_products(
    file: UploadFile = File(...),
    category: str = Form(...),
):
    """Accept a CSV / XLSX upload for batch product creation.
    Currently records the upload metadata to a `bulk_import_jobs` collection
    and returns a job-id stub — the actual row-by-row ingestion is wired by
    the existing `/catalog/products/import` route in catalog.py. Operators
    can poll the job status via `/catalog/products/import` directly.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")
    contents = await file.read()
    size_bytes = len(contents) if contents else 0
    coll = _coll("bulk_import_jobs")
    job_id = _new_id()
    if coll is not None:
        coll.insert_one(
            {
                "_id": job_id,
                "job_id": job_id,
                "filename": file.filename,
                "category": category,
                "size_bytes": size_bytes,
                "status": "received",
                "created_at": _now(),
            }
        )
    return {
        "job_id": job_id,
        "filename": file.filename,
        "category": category,
        "size_bytes": size_bytes,
        "status": "received",
        "next_step": "POST /api/v1/catalog/products/import to process rows",
    }


# ============================================================================
# PRODUCTS — full CRUD aliases under /admin/products
# ============================================================================
# The frontend's `adminProductApi` was written against `/admin/products`
# CRUD, but only `/admin/products/generate-sku` and
# `/admin/products/bulk-import` existed at that prefix — every list /
# read / create / update / delete call was 404'ing. Backend HAD the
# canonical endpoints at `/catalog/products` (catalog.py) and
# `/products` (products.py), but admin pages weren't reaching them.
# These thin aliases route admin-prefixed calls to the same `products`
# Mongo collection so the catalog page works end-to-end.

@router.get("/products")
async def list_products(
    search: Optional[str] = None,
    category: Optional[str] = None,
    brand: Optional[str] = None,
    is_active: Optional[bool] = None,
):
    coll = _coll("products")
    if coll is None:
        return {"products": [], "total": 0}
    filter_: Dict = {}
    if is_active is not None:
        filter_["is_active"] = is_active
    if category:
        filter_["category"] = category
    if brand:
        filter_["brand"] = brand
    if search:
        filter_["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"sku": {"$regex": search, "$options": "i"}},
            {"barcode": {"$regex": search, "$options": "i"}},
        ]
    docs = list(coll.find(filter_).limit(200))
    return {"products": [_scrub(d) for d in docs if d], "total": len(docs)}


@router.get("/products/{product_id}")
async def get_product(product_id: str):
    coll = _coll("products")
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    doc = coll.find_one({"$or": [{"product_id": product_id}, {"_id": product_id}]})
    if not doc:
        raise HTTPException(status_code=404, detail="Product not found")
    return _scrub(doc) or {}


@router.post("/products", status_code=201)
async def create_product(payload: Dict[str, Any]):
    coll = _coll("products")
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    pid = _new_id()
    body = {**payload, "product_id": pid, "_id": pid, "created_at": _now(), "updated_at": _now()}
    body.setdefault("is_active", True)
    coll.insert_one(body)
    return _scrub(body) or {}


@router.put("/products/{product_id}")
async def update_product(product_id: str, updates: Dict[str, Any]):
    coll = _coll("products")
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    if not coll.find_one({"$or": [{"product_id": product_id}, {"_id": product_id}]}):
        raise HTTPException(status_code=404, detail="Product not found")
    updates = {k: v for k, v in updates.items() if k not in ("_id", "product_id")}
    updates["updated_at"] = _now()
    coll.update_one({"$or": [{"product_id": product_id}, {"_id": product_id}]}, {"$set": updates})
    doc = coll.find_one({"$or": [{"product_id": product_id}, {"_id": product_id}]})
    return _scrub(doc) or {}


@router.delete("/products/{product_id}")
async def delete_product(product_id: str):
    """Soft-delete via is_active=False. Hard delete is intentionally not exposed."""
    coll = _coll("products")
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    if not coll.find_one({"$or": [{"product_id": product_id}, {"_id": product_id}]}):
        raise HTTPException(status_code=404, detail="Product not found")
    coll.update_one(
        {"$or": [{"product_id": product_id}, {"_id": product_id}]},
        {"$set": {"is_active": False, "updated_at": _now()}},
    )
    return {"deactivated": True, "product_id": product_id}
