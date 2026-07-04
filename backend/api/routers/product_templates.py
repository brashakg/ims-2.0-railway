"""
IMS 2.0 - Product Templates (Phase C of the product-add redesign, #143)
========================================================================
Named, reusable product templates for the catalog "Quick Add" screen.

A template is a saved snapshot of the Quick Add form field values (category,
attributes, pricing band, HSN/GST, Shopify flags). Catalog staff save the
current form as a template, then later load it to prefill a fresh create (the
"New from template" flow). This is purely a productivity layer ON TOP of the
existing single-create path -- a loaded template is just prefilled form state;
the user still edits and saves a real product (POST /products) which mints the
SKU. Templates never create products themselves.

Backed by the `product_templates` Mongo collection. The stored `payload` is the
opaque Quick Add ProductFormValues blob so the frontend can round-trip it
without the backend having to model every form field.

Auth: catalog-write roles (ADMIN, CATALOG_MANAGER; SUPERADMIN auto-passes),
mirroring products.py `_CATALOG_ROLES` -- templates are a catalog-management
artifact, so the same people who can add products manage templates. Delete is
further restricted to the template owner (or ADMIN / SUPERADMIN).

Fail-soft: when MongoDB is unreachable (local dev / stub mode) the list returns
an empty envelope and writes return HTTP 503, matching the notifications router.
"""

from datetime import datetime
from typing import Optional
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import require_roles
from ..dependencies import get_db

router = APIRouter()

logger = logging.getLogger("ims.product_templates")

# Roles permitted to manage templates. Mirrors products.py `_CATALOG_ROLES`
# (ADMIN, CATALOG_MANAGER); SUPERADMIN auto-passes via require_roles.
_CATALOG_ROLES = ("ADMIN", "CATALOG_MANAGER")

# Roles that may delete ANY template regardless of owner. The owner can always
# delete their own; these can delete anyone's.
_DELETE_ANY_ROLES = ("ADMIN", "SUPERADMIN")

# Hard ceiling on the form-values blob so a template can't be used to stuff
# arbitrarily large documents into the collection.
_MAX_PAYLOAD_KEYS = 100


def _coll():
    """Return the product_templates collection, or None when the DB is
    unreachable (local dev / stub mode). Fail-soft like notifications._coll."""
    db = get_db()
    if db is None or not getattr(db, "is_connected", True):
        return None
    try:
        return db.get_collection("product_templates")
    except Exception:  # noqa: BLE001
        return None


# ============================================================================
# SCHEMAS
# ============================================================================


class TemplateCreate(BaseModel):
    """Save the current Quick Add form as a named template.

    `payload` is the opaque Quick Add ProductFormValues object (category,
    attributes, pricing band, HSN/GST, Shopify flags). It is stored verbatim
    and handed straight back on load so the frontend round-trips its own form
    shape without the backend modelling every field.
    """

    name: str = Field(..., min_length=1, max_length=120)
    payload: dict = Field(..., description="Quick Add ProductFormValues blob")
    # Convenience metadata surfaced in the picker without parsing the payload.
    category: Optional[str] = Field(default=None, max_length=40)


def _serialize(doc: dict) -> dict:
    """Shape a stored template doc for the API (drop Mongo _id)."""
    return {
        "template_id": doc.get("template_id"),
        "name": doc.get("name"),
        "category": doc.get("category"),
        "payload": doc.get("payload") or {},
        "created_by": doc.get("created_by"),
        "created_by_name": doc.get("created_by_name"),
        "created_at": doc.get("created_at"),
    }


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("")
@router.get("/")
async def list_templates(
    category: Optional[str] = Query(None, description="Filter by category code"),
    limit: int = Query(100, ge=1, le=500),
    current_user: dict = Depends(require_roles(*_CATALOG_ROLES)),
):
    """List saved product templates, newest first.

    Templates are shared across the catalog team (any catalog-write role sees
    all of them) -- they capture reusable product shapes, not personal drafts.
    Fail-soft: empty envelope when the DB is unavailable.
    """
    col = _coll()
    if col is None:
        return {"templates": [], "total": 0}

    query: dict = {}
    if category:
        # Category-filter fix: normalise short codes / plurals to the canonical
        # category templates stamp; fail-open pass-through when unresolvable.
        from ..services.product_master import resolve_category

        raw = str(category).strip()
        query["category"] = resolve_category(raw) or raw

    try:
        items = list(col.find(query, {"_id": 0}).sort("created_at", -1).limit(limit))
        total = col.count_documents(query)
    except Exception:  # noqa: BLE001
        return {"templates": [], "total": 0}

    return {"templates": [_serialize(d) for d in items], "total": total}


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_template(
    body: TemplateCreate,
    current_user: dict = Depends(require_roles(*_CATALOG_ROLES)),
):
    """Save the current Quick Add field values as a named template."""
    col = _coll()
    if col is None:
        raise HTTPException(status_code=503, detail="DB unavailable")

    if len(body.payload) > _MAX_PAYLOAD_KEYS:
        raise HTTPException(
            status_code=422,
            detail=f"Template payload too large (max {_MAX_PAYLOAD_KEYS} fields)",
        )

    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Template name is required")

    # Derive the category for the picker badge: explicit field wins, else read
    # it out of the form-values blob (Quick Add stores it under `category`).
    category = (body.category or body.payload.get("category") or "").strip() or None

    doc = {
        "template_id": str(uuid.uuid4()),
        "name": name,
        "category": category,
        "payload": body.payload,
        "created_by": current_user.get("user_id"),
        "created_by_name": current_user.get("username"),
        "created_at": datetime.now(),
    }

    try:
        col.insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TEMPLATES] create failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to save template")

    return _serialize(doc)


@router.delete("/{template_id}")
async def delete_template(
    template_id: str,
    current_user: dict = Depends(require_roles(*_CATALOG_ROLES)),
):
    """Delete a template.

    The owner can delete their own; ADMIN / SUPERADMIN can delete anyone's.
    A catalog manager cannot delete a template another user created.
    """
    col = _coll()
    if col is None:
        raise HTTPException(status_code=503, detail="DB unavailable")

    # No projection arg: the MockCollection used in DB-less mode only accepts a
    # filter, and _serialize() drops _id anyway, so the projection is redundant.
    doc = col.find_one({"template_id": template_id})
    if doc is None:
        raise HTTPException(status_code=404, detail="Template not found")

    roles = set(current_user.get("roles", []) or [])
    is_owner = doc.get("created_by") == current_user.get("user_id")
    can_delete_any = bool(roles & set(_DELETE_ANY_ROLES))
    if not (is_owner or can_delete_any):
        raise HTTPException(
            status_code=403,
            detail="You can only delete templates you created",
        )

    col.delete_one({"template_id": template_id})
    return {"template_id": template_id, "deleted": True}
