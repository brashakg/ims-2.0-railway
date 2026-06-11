"""
IMS 2.0 - Collection Browse (unification step-13)
=================================================
Read-only, FAST browse over the materialised `collection_products` view.

  GET  /api/v1/collections                      list browsable collections
  GET  /api/v1/collections/{handle}/products    page a collection's members
  POST /api/v1/collections/{handle}/refresh      recompute membership (catalog roles)

This is the storefront/catalogue-facing read surface; the role-gated ADMIN editor
(create/edit/manual-membership/rule-edit) stays under
`/api/v1/online-store/collections`. Browse resolves a handle -> collection ->
materialised membership, joining product detail for rich rows. Fail-soft: no DB ->
empty page (never a 500). No comms, no Shopify push.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .auth import get_current_user, require_roles
from ..services import collection_materializer as _mat

router = APIRouter()

# Catalogue roles allowed to force a refresh (SUPERADMIN auto-passes).
_REFRESH_ROLES = ("ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER")


def _get_db():
    """Live DB object (real pymongo Database or seeded MockDatabase) when
    connected, else None. Mirrors online_store_collections._get_db."""
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def _repo(db):
    try:
        from database.repositories import EcomCollectionRepository

        return EcomCollectionRepository(db["ecom_collections"])
    except Exception:  # noqa: BLE001
        return None


@router.get("")
async def list_browsable_collections(
    published: Optional[bool] = Query(True, description="Only published collections"),
    collection_type: Optional[str] = Query(None, description="CUSTOM | SMART"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
):
    """List collections available to browse (published by default), with their
    materialised member count. Fail-soft -> empty list."""
    db = _get_db()
    if db is None:
        return {"collections": [], "total": 0}
    repo = _repo(db)
    if repo is None:
        return {"collections": [], "total": 0}
    ctype = None
    if collection_type:
        ctype = str(collection_type).strip().upper()
        if ctype not in ("CUSTOM", "SMART"):
            raise HTTPException(
                status_code=400, detail="collection_type must be CUSTOM or SMART"
            )
    try:
        docs = repo.list(
            published=published, collection_type=ctype, skip=skip, limit=limit
        )
    except Exception:  # noqa: BLE001
        return {"collections": [], "total": 0}
    rows = []
    for d in docs or []:
        if not isinstance(d, dict):
            continue
        rows.append(
            {
                "id": d.get("collection_id") or d.get("id"),
                "handle": d.get("handle"),
                "title": d.get("title") or d.get("name") or d.get("handle"),
                "collection_type": (d.get("collection_type") or "CUSTOM").upper(),
                "products_count": int(d.get("products_count") or 0),
                "published": bool(d.get("published", True)),
                "sort_priority": int(d.get("sort_priority") or 100),
            }
        )
    return {"collections": rows, "total": len(rows)}


@router.get("/{handle}/products")
async def browse_collection_products(
    handle: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(24, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    """Page a collection's MATERIALISED membership (rich rows). 404 if the handle
    is unknown; an empty collection returns an empty page (not an error)."""
    db = _get_db()
    if db is None:
        return {
            "handle": handle,
            "collection_type": None,
            "products": [],
            "total": 0,
            "skip": skip,
            "limit": limit,
        }
    repo = _repo(db)
    doc = repo.get_by_handle(handle) if repo is not None else None
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Collection not found: {handle}")
    page = _mat.browse(db, doc, skip=skip, limit=limit)
    page["handle"] = handle
    page["collection_id"] = doc.get("collection_id") or doc.get("id")
    page["title"] = doc.get("title") or doc.get("name") or handle
    page["collection_type"] = (doc.get("collection_type") or "CUSTOM").upper()
    return page


@router.post("/{handle}/refresh")
async def refresh_collection_membership(
    handle: str,
    current_user: dict = Depends(require_roles(*_REFRESH_ROLES)),
):
    """Force-recompute a collection's materialised membership (catalogue roles).
    404 if the handle is unknown. Fail-soft on the materialise itself."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Collections store unavailable")
    repo = _repo(db)
    doc = repo.get_by_handle(handle) if repo is not None else None
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Collection not found: {handle}")
    count = _mat.materialize_collection(db, doc)
    return {"handle": handle, "materialized": True, "products_count": count}
