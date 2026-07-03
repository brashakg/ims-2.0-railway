"""
IMS 2.0 - Catalog Dictionary router (/api/v1/catalog-field-options)
===================================================================
Settings -> Catalog Dictionary: the owner-editable allowed-value lists for
Add-Product attribute fields (frame_material, shape, tint, ...). Mirrors the
lens-enums pattern: one Mongo doc per field in `catalog_field_options`
({field_id, items, updated_at, updated_by}).

- READ  (GET)   : any authenticated user (the Add-Product form needs it).
- WRITE (PATCH) : ADMIN / CATALOG_MANAGER (SUPERADMIN auto-passes).
- brand_name / subbrand are REFUSED here: their source of truth is the Brand
  Master (Settings -> Brand Master), never this collection.

Enforcement of these lists happens server-side in
product_master.enforce_dictionary_values (create door + product update).
NO emojis (cp1252).
"""

import logging
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, HTTPException, Path

from .auth import get_current_user, require_roles
from ..dependencies import get_audit_repository
from ..services.catalog_dictionary import (
    BRAND_MANAGED_FIELDS,
    FIELD_OPTIONS_COLLECTION,
    normalize_items,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Same write gate as lens-enums / lens_catalog: the catalog manager who
# catalogues products can also edit the value lists those products draw from.
_WRITE_ROLES = ("ADMIN", "CATALOG_MANAGER")


def _get_db():
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def _coll():
    db = _get_db()
    if db is None:
        return None
    try:
        return db.get_collection(FIELD_OPTIONS_COLLECTION)
    except Exception:  # noqa: BLE001
        return None


def _audit(actor: Any, field: str, before: List[str], after: List[str]) -> None:
    """Fail-soft audit trail for dictionary edits."""
    try:
        repo = get_audit_repository()
        if repo is None:
            return
        repo.create(
            {
                "kind": "catalog_dictionary_update",
                "entity_type": "catalog_field_options",
                "entity_id": field,
                "action": "UPDATE",
                "performed_by": actor,
                "details": {
                    "field": field,
                    "before_count": len(before),
                    "after_count": len(after),
                    "added": [v for v in after if v not in before][:20],
                    "removed": [v for v in before if v not in after][:20],
                },
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("[CATALOG-DICT] audit write skipped: %s", e)


@router.get("")
@router.get("/")
async def list_field_options(current_user: dict = Depends(get_current_user)):
    """Every configured field list: {"fields": {field_name: [values...]}}.
    Empty lists are included here (unlike the enforcement loader) so the
    Settings editor can show a saved-but-empty state."""
    coll = _coll()
    fields: Dict[str, List[str]] = {}
    if coll is not None:
        try:
            for doc in coll.find({}):
                field = str(doc.get("field_id") or "").strip()
                items = doc.get("items")
                if field and isinstance(items, list):
                    fields[field] = [str(i) for i in items]
        except Exception as e:  # noqa: BLE001
            logger.warning("[CATALOG-DICT] list read failed: %s", e)
    return {"fields": fields, "brand_managed_fields": sorted(BRAND_MANAGED_FIELDS)}


@router.patch("/{field_name}")
async def replace_field_options(
    field_name: str = Path(..., min_length=1, max_length=64),
    body: Dict[str, Any] = Body(...),
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Replace the allowed-value list for one attribute field.

    Body: {"items": ["Acetate", "Metal", ...]}. An empty list UN-configures
    the field (it becomes free-form again). Values are trimmed + de-duped
    case-insensitively; the saved casing is what products canonicalise to.
    """
    field = field_name.strip()
    if field in BRAND_MANAGED_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{field}' is managed by the Brand Master "
                "(Settings -> Brand Master), not the Catalog Dictionary."
            ),
        )
    try:
        items = normalize_items(body.get("items"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    coll = _coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    before_doc = coll.find_one({"field_id": field}) or {}
    before = [str(i) for i in (before_doc.get("items") or [])]

    coll.update_one(
        {"field_id": field},
        {
            "$set": {
                "field_id": field,
                "items": items,
                "updated_at": datetime.utcnow(),
                "updated_by": current_user.get("user_id"),
            }
        },
        upsert=True,
    )
    _audit(current_user.get("user_id"), field, before, items)
    return {"field": field, "items": items, "count": len(items)}
