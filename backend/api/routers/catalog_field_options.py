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


def _audit(
    actor: Any,
    field: str,
    before: List[str],
    after: List[str],
    category: Any = None,
) -> None:
    """Fail-soft audit trail for dictionary edits."""
    try:
        repo = get_audit_repository()
        if repo is None:
            return
        repo.create(
            {
                "kind": "catalog_dictionary_update",
                "entity_type": "catalog_field_options",
                "entity_id": f"{category}:{field}" if category else field,
                "action": "UPDATE",
                "performed_by": actor,
                "details": {
                    "field": field,
                    "category": category or "ALL",
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
    """Every configured list, split by scope:
    {"fields": {field: [values]},                    -- "All categories" lists
     "by_category": {CATEGORY: {field: [values]}},   -- per-category overrides
     "brand_managed_fields": [...]}.
    Empty lists are included here (unlike the enforcement loader) so the
    Settings editor can show a saved-but-empty state. At enforcement/render
    time a category's own list REPLACES the All-categories one per field."""
    coll = _coll()
    fields: Dict[str, List[str]] = {}
    by_category: Dict[str, Dict[str, List[str]]] = {}
    if coll is not None:
        try:
            for doc in coll.find({}):
                field = str(doc.get("field_id") or "").strip()
                items = doc.get("items")
                if not field or not isinstance(items, list):
                    continue
                values = [str(i) for i in items]
                category = str(doc.get("category") or "").strip().upper()
                if category:
                    by_category.setdefault(category, {})[field] = values
                else:
                    fields[field] = values
        except Exception as e:  # noqa: BLE001
            logger.warning("[CATALOG-DICT] list read failed: %s", e)
    return {
        "fields": fields,
        "by_category": by_category,
        "brand_managed_fields": sorted(BRAND_MANAGED_FIELDS),
    }


@router.patch("/{field_name}")
async def replace_field_options(
    field_name: str = Path(..., min_length=1, max_length=64),
    body: Dict[str, Any] = Body(...),
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Replace the allowed-value list for one attribute field, in one SCOPE.

    Body: {"items": ["Acetate", ...], "category": "SUNGLASS"?}. Without
    `category` the list applies to ALL categories; with it, the list applies
    to that category ONLY and overrides the All-categories list there (so
    same-named fields never bleed across categories). An empty list
    UN-configures that scope. Values are trimmed + de-duped case-
    insensitively; the saved casing is what products canonicalise to.
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

    category = None
    raw_category = str(body.get("category") or "").strip()
    if raw_category:
        # Lazy import (products router already couples these modules; keep the
        # category vocabulary in ONE place -- product_master's registry).
        from ..services.product_master import resolve_category

        category = resolve_category(raw_category)
        if category is None:
            raise HTTPException(
                status_code=400, detail=f"Unknown category '{raw_category}'."
            )

    coll = _coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    # One doc per (field, scope): category None matches docs where the field
    # is null OR absent, i.e. the legacy pre-split "All categories" rows.
    scope_filter = {"field_id": field, "category": category}
    before_doc = coll.find_one(scope_filter) or {}
    before = [str(i) for i in (before_doc.get("items") or [])]

    coll.update_one(
        scope_filter,
        {
            "$set": {
                "field_id": field,
                "category": category,
                "items": items,
                "updated_at": datetime.utcnow(),
                "updated_by": current_user.get("user_id"),
            }
        },
        upsert=True,
    )
    _audit(current_user.get("user_id"), field, before, items, category)
    return {
        "field": field,
        "category": category,
        "items": items,
        "count": len(items),
    }
