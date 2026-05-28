"""
IMS 2.0 - Products Router
==========================
Product catalog management endpoints
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
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

# Canonical set of accepted product categories. Pulled from the single source of
# truth -- the GST/HSN table keys -- so master, billing and this guard never
# drift. A category MUST normalize (upper/trim) to one of these keys; anything
# blank/null/missing or unrecognized is rejected at create/update.
_VALID_CATEGORY_KEYS = frozenset(GST_CATEGORY_TABLE.keys())
# Short, human-friendly subset surfaced in the 422 message (the full key set also
# carries legacy aliases + 2-letter UI codes, which would make the message
# noisy). These are the canonical product categories from schemas.py.
_VALID_CATEGORY_DISPLAY = (
    "FRAME",
    "OPTICAL_LENS",
    "READING_GLASSES",
    "CONTACT_LENS",
    "COLORED_CONTACT_LENS",
    "SUNGLASS",
    "WATCH",
    "SMARTWATCH",
    "SMARTGLASSES",
    "WALL_CLOCK",
    "ACCESSORIES",
    "SERVICES",
    "HEARING_AID",
)


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
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """List active catalog products with optional search / brand / category
    filters. `store_id` is accepted (kept for backwards-compat) but does NOT
    filter the result set - the canonical Stock Ledger at /inventory/stock
    is the per-store on-hand view. POS search uses this endpoint so a SKU
    that's in the catalog can always be looked up at any store the user has
    access to."""
    from ..services.cache import cache

    # Build cache key from query params
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
    """Create a new product"""
    # Block save when category is blank/null/missing (server-side guard for the
    # GST-default bug: an uncategorized product would otherwise fall back to a
    # default GST rate). Normalizes the category to the validated value.
    product.category = _validate_category_or_422(product.category)

    # Validate MRP >= Offer Price
    if product.offer_price > product.mrp:
        raise HTTPException(status_code=400, detail="Offer price cannot exceed MRP")

    repo = get_product_repository()

    if repo is not None:
        # Check if SKU already exists
        existing = repo.find_by_sku(product.sku)
        if existing is not None:
            raise HTTPException(
                status_code=400, detail="Product with this SKU already exists"
            )

        is_cl = product.category in CL_CATEGORIES
        if product.modality and product.modality not in CL_MODALITIES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid modality. Allowed: {', '.join(CL_MODALITIES)}",
            )

        # HSN / GST: explicit value wins; otherwise fall back to the canonical
        # category->(hsn, rate) table so the master rate a product is created
        # with equals what POS bills it (see api/services/gst_rates.py). This
        # gives OPTICAL_LENS / READING_GLASSES / COLORED_CONTACT_LENS their
        # correct 5% instead of the old blanket 18% non-CL default.
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
            "created_by": current_user.get("user_id"),
        }

        # Persist CL identity fields top-level only when provided (additive).
        for _f in (
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
        ):
            _v = getattr(product, _f, None)
            if _v is not None:
                product_data[_f] = _v

        created = repo.create(product_data)
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

        # Validate MRP >= Offer Price if both are being updated
        if "mrp" in update_data and "offer_price" in update_data:
            if update_data["offer_price"] > update_data["mrp"]:
                raise HTTPException(
                    status_code=400, detail="Offer price cannot exceed MRP"
                )

        update_data["updated_by"] = current_user.get("user_id")

        if repo.update(product_id, update_data):
            return {"message": "Product updated", "product_id": product_id}

        raise HTTPException(status_code=500, detail="Failed to update product")

    return {"message": "Product updated"}
