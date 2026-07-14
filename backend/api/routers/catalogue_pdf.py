"""
IMS 2.0 - Catalogue PDF + Temporary Collections router
======================================================
Two staff-facing surfaces for sharing product options with customers:

  POST   /catalogue/pdf               Build a branded A4 catalogue PDF from a
                                      collection (collection_id) OR a hand-picked
                                      product_ids list. Two independent toggles:
                                      include_details, include_mrp. Returns the
                                      PDF as an attachment (the owner shares it via
                                      WhatsApp / email).
  POST   /catalogue/temp-collections  Save a hand-picked selection as a TEMPORARY
                                      collection (name + validity_days, HARD MAX 7).
                                      Auto-expires via a Mongo TTL index on
                                      expires_at. NEVER synced to Shopify.
  GET    /catalogue/temp-collections  List the caller's live temp collections.
  DELETE /catalogue/temp-collections/{collection_id}  Remove one early.

RBAC: catalogue sharing is a broad staff activity (anyone helping a customer),
so every route here is AUTHENTICATED -- the same posture as the internal catalogue
browse (GET /products, GET /collections). Each route is catalogued in
rbac_policy.POLICY (the coverage-lock test fails otherwise). The literal
/temp-collections path out-ranks /temp-collections/{collection_id} in the matcher.

Mounted at /api/v1/catalogue. Fail-soft: no DB -> reads empty / writes 503.
Temporary collections are structurally excluded from the Shopify push
(is_temporary + sync_to_shopify=False + published=False); a guard in
routers/online_store_push.py refuses to push one even if targeted by hand.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .auth import get_current_user
from ..services import catalogue_pdf as pdf

router = APIRouter()

# Temporary collections never live longer than a week (owner rule).
TEMP_MAX_DAYS = 7
TEMP_DEFAULT_DAYS = 7


# ---------------------------------------------------------------------------
# DB helper (fail-soft; mirrors the other online-store routers)
# ---------------------------------------------------------------------------


def _get_db():
    """Underlying DB object (real pymongo Database or seeded MockDatabase) when
    connected, else None. Subscript access (db[name]) works on both."""
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def _brand_and_contact(db, current_user: dict) -> "tuple[str, str]":
    """Resolve the cover brand/store name + a contact line from the caller's
    active store (fail-soft -> the default brand, no contact)."""
    brand = pdf.DEFAULT_BRAND
    contact = ""
    store_id = (current_user or {}).get("active_store_id")
    if db is None or not store_id:
        return brand, contact
    try:
        store = db["stores"].find_one({"store_id": store_id})
        if isinstance(store, dict):
            brand = (
                store.get("store_name")
                or store.get("name")
                or store.get("brand")
                or brand
            )
            bits = [
                str(store.get(k)).strip()
                for k in ("phone", "contact_number", "city")
                if store.get(k)
            ]
            contact = "  ".join(b for b in bits if b)
    except Exception:  # noqa: BLE001
        pass
    return brand, contact


def _clamp_validity_days(days: Optional[int]) -> int:
    """Clamp requested validity into [1, TEMP_MAX_DAYS]; None/blank -> default."""
    if days is None:
        return TEMP_DEFAULT_DAYS
    try:
        d = int(days)
    except (TypeError, ValueError):
        return TEMP_DEFAULT_DAYS
    if d < 1:
        return 1
    if d > TEMP_MAX_DAYS:
        return TEMP_MAX_DAYS
    return d


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


class CataloguePdfRequest(BaseModel):
    collection_id: Optional[str] = Field(
        None, description="Build from this collection's resolved members"
    )
    product_ids: Optional[List[str]] = Field(
        None, description="OR build from this explicit hand-picked list"
    )
    include_details: bool = Field(
        False, description="Include the key attributes + short description block"
    )
    include_mrp: bool = Field(True, description="Include MRP (+ offer price)")
    title: Optional[str] = Field(None, description="Override the cover title")


class TempCollectionCreate(BaseModel):
    name: str = Field(..., description="Display name for the temporary set")
    product_ids: List[str] = Field(..., description="Hand-picked product ids")
    validity_days: Optional[int] = Field(
        TEMP_DEFAULT_DAYS,
        description="Days until auto-expiry (default 7, HARD MAX 7 -- clamped)",
    )


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


@router.post("/pdf")
async def generate_catalogue_pdf(
    payload: CataloguePdfRequest,
    current_user: dict = Depends(get_current_user),
):
    """Build the catalogue PDF and stream it back as an attachment.

    Resolves the product set from `collection_id` (its members -- CUSTOM manual
    membership OR SMART rule matches) OR from an explicit `product_ids` list. An
    empty selection still returns a valid (single-cover) 'no products' PDF so the
    caller always gets a file. An unknown collection_id is a 404."""
    db = _get_db()

    products: List[Dict] = []
    default_title = "Product Catalogue"
    if payload.collection_id:
        resolved = pdf.resolve_collection_products(db, payload.collection_id)
        if resolved is None:
            raise HTTPException(status_code=404, detail="Collection not found")
        products = resolved
        # Best-effort nicer default title from the collection.
        try:
            from database.repositories import EcomCollectionRepository

            if db is not None:
                doc = EcomCollectionRepository(db["ecom_collections"]).get_by_id(
                    payload.collection_id
                )
                if isinstance(doc, dict) and doc.get("title"):
                    default_title = str(doc["title"])
        except Exception:  # noqa: BLE001
            pass
    elif payload.product_ids:
        products = pdf.resolve_products_by_ids(db, payload.product_ids)
    else:
        raise HTTPException(
            status_code=400, detail="Provide collection_id or product_ids"
        )

    truncated = len(products) >= pdf.MAX_PRODUCTS
    title = (payload.title or default_title).strip() or default_title

    rows = pdf.build_product_rows(
        products,
        include_details=payload.include_details,
        include_mrp=payload.include_mrp,
    )
    images = await pdf.fetch_images(rows)
    brand, contact = _brand_and_contact(db, current_user)

    content = await run_in_threadpool(
        pdf.render_catalogue_pdf,
        title=title,
        brand_name=brand,
        rows=rows,
        image_bytes=images,
        include_details=payload.include_details,
        include_mrp=payload.include_mrp,
        contact_line=contact,
        truncated=truncated,
    )

    filename = "%s.pdf" % pdf.slugify_filename(title)
    return StreamingResponse(
        iter([content]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="%s"' % filename,
            "Content-Length": str(len(content)),
        },
    )


# ---------------------------------------------------------------------------
# Temporary collections
# ---------------------------------------------------------------------------


def _temp_repo(db):
    try:
        from database.repositories import EcomCollectionRepository

        return EcomCollectionRepository(db["ecom_collections"])
    except Exception:  # noqa: BLE001
        return None


def _temp_public(doc: Dict) -> Dict:
    """Shape a temp-collection doc into a slim public row."""
    expires_at = doc.get("expires_at")
    expires_iso = expires_at.isoformat() if isinstance(expires_at, datetime) else expires_at
    return {
        "collection_id": doc.get("collection_id"),
        "id": doc.get("collection_id"),
        "name": doc.get("title"),
        "handle": doc.get("handle"),
        "is_temporary": True,
        "products_count": doc.get("products_count", len(doc.get("products") or [])),
        "expires_at": expires_iso,
        "created_by": doc.get("created_by"),
        "created_at": (
            doc.get("created_at").isoformat()
            if isinstance(doc.get("created_at"), datetime)
            else doc.get("created_at")
        ),
    }


@router.post("/temp-collections", status_code=201)
async def create_temp_collection(
    payload: TempCollectionCreate,
    current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Save a hand-picked selection as a TEMPORARY collection.

    Stored in `ecom_collections` as a CUSTOM collection with is_temporary=True,
    sync_to_shopify=False, published=False and an expires_at (now + clamped
    validity, HARD MAX 7 days). A Mongo TTL index auto-removes it at expiry.
    Never synced to Shopify (structurally excluded)."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Collections store unavailable")

    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    skus = pdf.product_ids_to_skus(db, payload.product_ids or [])
    if not skus:
        raise HTTPException(
            status_code=400,
            detail="No valid products in the selection (none resolved to a SKU)",
        )

    days = _clamp_validity_days(payload.validity_days)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=days)

    repo = _temp_repo(db)
    if repo is None:
        raise HTTPException(status_code=503, detail="Collections store unavailable")

    # A unique, non-colliding handle. tmp- prefix + slug + short uuid so two temp
    # sets with the same name never clash on the unique handle index.
    handle = "tmp-%s-%s" % (pdf.slugify_filename(name, "set")[:48], uuid.uuid4().hex[:8])
    data: Dict[str, Any] = {
        "title": name,
        "handle": handle,
        "collection_type": "CUSTOM",
        # Internal sharing set -- must never reach the storefront / Shopify.
        "published": False,
        "is_temporary": True,
        "sync_to_shopify": False,
        "expires_at": expires_at,
        "created_by": (current_user or {}).get("user_id"),
        "products": [{"sku": sku, "position": i} for i, sku in enumerate(skus)],
    }
    created = repo.create(data)
    if created is None:
        raise HTTPException(status_code=500, detail="Failed to create temp collection")
    return {"collection": _temp_public(created)}


@router.get("/temp-collections")
async def list_temp_collections(
    current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """List LIVE temporary collections (is_temporary=True, not yet expired).

    The TTL sweep is periodic, so we also filter out any already-past expires_at
    at read time (belt + braces). Fail-soft -> empty list."""
    db = _get_db()
    if db is None:
        return {"collections": [], "count": 0, "db_connected": False}
    now = datetime.now(timezone.utc)
    rows: List[Dict] = []
    try:
        cursor = db["ecom_collections"].find({"is_temporary": True})
        for doc in cursor:
            if not isinstance(doc, dict):
                continue
            exp = doc.get("expires_at")
            if isinstance(exp, datetime):
                # Compare tz-aware vs naive safely.
                exp_cmp = exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
                if exp_cmp < now:
                    continue
            rows.append(_temp_public(doc))
    except Exception:  # noqa: BLE001
        return {"collections": [], "count": 0, "db_connected": True}
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return {"collections": rows, "count": len(rows), "db_connected": True}


@router.delete("/temp-collections/{collection_id}")
async def delete_temp_collection(
    collection_id: str,
    current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Remove a temporary collection early. Refuses to touch a NON-temporary
    (real storefront) collection (404), so this can never delete a real one."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Collections store unavailable")
    repo = _temp_repo(db)
    doc = repo.get_by_id(collection_id) if repo else None
    if doc is None or not doc.get("is_temporary"):
        raise HTTPException(status_code=404, detail="Temporary collection not found")
    ok = repo.delete(collection_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to delete temp collection")
    return {"deleted": True, "collection_id": collection_id}
