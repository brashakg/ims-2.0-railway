"""
IMS 2.0 - Lens Enum Config Router (Branch B' sub-PR 1)
======================================================
Owner-editable enum lists for the lens catalog (Q5): coatings, brands,
series-per-brand, indexes, materials, lens_types. Stored in the
`lens_enum_config` collection, one doc per enum_type.

Endpoints (prefix /api/v1/lens-enums):
  GET    /                                       all enums
  GET    /{enum_type}                            single
  PATCH  /{enum_type}                            replace the items list
  POST   /{enum_type}/items                      append one item
  DELETE /{enum_type}/items/{item}               remove one item (refused if in use)

Role gates:
  SUPERADMIN / ADMIN only -- enum edits affect every lens line, so the
  blast radius is high. CATALOG_MANAGER reads but cannot mutate (the user
  can still propose values to the owner; the owner pushes them live).

Audit logging: every write writes to audit_logs via get_audit_repository().
Fail-soft.
"""
from __future__ import annotations

import logging
import urllib.parse
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path

from .auth import get_current_user, require_roles
from ..dependencies import get_audit_repository
from ..services.lens_catalog_validation import (
    DEFAULT_ENUM_ITEMS,
    ENUM_TYPES,
    validate_enum_config_payload,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Only SUPERADMIN/ADMIN can edit enums. SUPERADMIN auto-passes inside
# require_roles, so we only need to name ADMIN here.
_WRITE_ROLES = ("ADMIN",)


# ============================================================================
# DB HELPERS
# ============================================================================


def _get_db():
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def _enum_coll():
    db = _get_db()
    if db is None:
        return None
    try:
        return db.get_collection("lens_enum_config")
    except Exception:  # noqa: BLE001
        return None


def _catalog_coll():
    """Loaded on DELETE so we can refuse delete when a lens line still
    uses the value."""
    db = _get_db()
    if db is None:
        return None
    try:
        return db.get_collection("lens_catalog")
    except Exception:  # noqa: BLE001
        return None


def _now() -> datetime:
    return datetime.utcnow()


def _clean(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if doc is None:
        return None
    out: Dict[str, Any] = {}
    for k, v in doc.items():
        if k == "_id":
            continue
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _load_items(enum_type: str) -> List[Any]:
    """Items for an enum_type. Falls back to DEFAULT_ENUM_ITEMS when the
    row is absent (first deploy)."""
    coll = _enum_coll()
    if coll is not None:
        try:
            doc = coll.find_one({"enum_id": enum_type})
            if doc and isinstance(doc.get("items"), list):
                return list(doc["items"])
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[LENS_ENUMS] load %s failed: %s", enum_type, exc
            )
    return list(DEFAULT_ENUM_ITEMS.get(enum_type) or [])


def _persist_items(
    enum_type: str, items: List[Any], user: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Upsert the enum_type row with the new items list."""
    coll = _enum_coll()
    if coll is None:
        return None
    now = _now()
    set_doc = {
        "enum_id": enum_type,
        "items": items,
        "updated_at": now,
        "updated_by": str(user.get("user_id") or ""),
    }
    try:
        coll.update_one(
            {"enum_id": enum_type},
            {"$set": set_doc},
            upsert=True,
        )
        return coll.find_one({"enum_id": enum_type})
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[LENS_ENUMS] persist %s failed: %s", enum_type, exc
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to persist enum_type {enum_type}".format(
                enum_type=enum_type
            ),
        )


def _audit(
    action: str,
    enum_type: str,
    user: Dict[str, Any],
    before: Optional[List[Any]] = None,
    after: Optional[List[Any]] = None,
    notes: Optional[str] = None,
) -> None:
    """Audit-log an enum mutation."""
    try:
        repo = get_audit_repository()
        if repo is None:
            return
        repo.create(
            {
                "action": "lens_enum.{}".format(action),
                "module": "inventory",
                "entity_type": "lens_enum_config",
                "entity_id": enum_type,
                "user_id": user.get("user_id"),
                "user_name": user.get("username"),
                "severity": "INFO",
                "before": {"items": before} if before is not None else None,
                "after": {"items": after} if after is not None else None,
                "metadata": {"notes": notes} if notes else None,
                "timestamp": _now(),
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[LENS_ENUMS] audit insert failed for %s: %s",
            enum_type,
            exc,
        )


def _coerce_item(enum_type: str, raw: Any) -> Any:
    """Coerce a single item for the 'append one' endpoint. Index strings
    like '1.6' arrive as strings via path/query; coerce to float so the
    list keeps a consistent shape. Series append takes a {brand: [series]}
    dict shape."""
    if enum_type == "indexes":
        try:
            return float(raw)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail="indexes items must be numbers",
            )
    return raw


def _validate(enum_type: str, items: Any) -> List[Any]:
    """Wrap the service-level validator to convert ValueError to 400."""
    try:
        return validate_enum_config_payload(enum_type, items)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _check_enum_type(enum_type: str) -> str:
    if enum_type not in ENUM_TYPES:
        raise HTTPException(
            status_code=404,
            detail=(
                "Unknown enum_type {enum_type!r}; one of {allowed}".format(
                    enum_type=enum_type, allowed=list(ENUM_TYPES)
                )
            ),
        )
    return enum_type


def _count_lens_lines_using(enum_type: str, item: Any) -> int:
    """How many lens_catalog rows reference `item` for this enum_type?
    series/brand have special-case mapping; everything else is a 1:1
    column. Returns 0 when the catalog collection is absent."""
    coll = _catalog_coll()
    if coll is None:
        return 0
    field_map = {
        "coatings": "coating",
        "brands": "brand",
        "indexes": "index",
        "materials": "material",
        "lens_types": "lens_type",
    }
    if enum_type == "series":
        # `item` is a {brand: [series]} dict for series deletion; we look
        # up by series field across all the listed series codes.
        if not isinstance(item, dict):
            return 0
        total = 0
        for brand, series_list in item.items():
            for series in series_list or []:
                try:
                    total += coll.count_documents(
                        {"brand": brand, "series": series, "is_active": True}
                    )
                except Exception:  # noqa: BLE001
                    pass
        return total
    field = field_map.get(enum_type)
    if not field:
        return 0
    try:
        return coll.count_documents({field: item, "is_active": True})
    except Exception:  # noqa: BLE001
        return 0


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("")
@router.get("/")
async def list_enums(current_user: dict = Depends(get_current_user)):
    """All enum configs. Auth-only (any logged-in user can READ)."""
    _ = current_user
    out: Dict[str, Any] = {}
    for enum_type in ENUM_TYPES:
        out[enum_type] = _load_items(enum_type)
    return {"enums": out}


@router.get("/{enum_type}")
async def get_enum(
    enum_type: str = Path(...),
    current_user: dict = Depends(get_current_user),
):
    """Items for a single enum_type."""
    _ = current_user
    enum_type = _check_enum_type(enum_type)
    return {"enum_type": enum_type, "items": _load_items(enum_type)}


@router.patch("/{enum_type}")
async def replace_enum(
    enum_type: str = Path(...),
    body: Dict[str, Any] = Body(...),
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Replace the items list wholesale. Body shape: {items: [...]}.

    The validator rejects malformed shapes (e.g. non-string entries in
    coatings, non-float in indexes, malformed series dicts).
    """
    enum_type = _check_enum_type(enum_type)
    if "items" not in body:
        raise HTTPException(status_code=400, detail="`items` is required")
    items = _validate(enum_type, body["items"])
    before = _load_items(enum_type)
    persisted = _persist_items(enum_type, items, current_user)
    _audit("replace", enum_type, current_user, before=before, after=items)
    return {"status": "success", "enum": _clean(persisted)}


@router.post("/{enum_type}/items")
async def append_item(
    enum_type: str = Path(...),
    body: Dict[str, Any] = Body(...),
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Append one item to the enum list (idempotent: already-present items
    are silently de-duped by the validator).

    Body shape: {item: <value>}. For series, value is {brand: [series]}."""
    enum_type = _check_enum_type(enum_type)
    if "item" not in body:
        raise HTTPException(status_code=400, detail="`item` is required")
    new_item = _coerce_item(enum_type, body["item"])
    before = _load_items(enum_type)
    candidate = list(before) + [new_item]
    items = _validate(enum_type, candidate)
    persisted = _persist_items(enum_type, items, current_user)
    _audit(
        "append",
        enum_type,
        current_user,
        before=before,
        after=items,
        notes="item={item!r}".format(item=new_item),
    )
    return {"status": "success", "enum": _clean(persisted)}


@router.delete("/{enum_type}/items/{item}")
async def remove_item(
    enum_type: str = Path(...),
    item: str = Path(..., description="Item to remove (URL-encoded)"),
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Remove one item from an enum list. Refused (409) if any active
    lens_catalog row still uses the value -- the owner must reassign /
    soft-delete those lines first.

    Indexes arrive as URL-encoded strings ('1.60'); we coerce. Series
    deletion is by brand-only via the `?brand=` query, NOT by this
    endpoint -- to remove a brand's series, PATCH the whole list."""
    enum_type = _check_enum_type(enum_type)
    decoded = urllib.parse.unquote(item)
    coerced = _coerce_item(enum_type, decoded)

    before = _load_items(enum_type)
    if enum_type == "series":
        # `coerced` is a string; can't reliably delete a series by string
        # alone. Force the FE to use PATCH.
        raise HTTPException(
            status_code=400,
            detail=(
                "Series cannot be deleted via this endpoint; "
                "PATCH /lens-enums/series with the new full list."
            ),
        )

    # In-use check: refuse if any active lens line references the value.
    n = _count_lens_lines_using(enum_type, coerced)
    if n:
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot remove {item!r} from {enum_type}: {n} active lens "
                "line(s) still use it. Reassign or soft-delete those "
                "first.".format(item=coerced, enum_type=enum_type, n=n)
            ),
        )

    # Filter the item out. For indexes use a tolerant compare.
    if enum_type == "indexes":
        candidate = [
            x for x in before
            if not _index_equal(x, coerced)
        ]
    else:
        candidate = [x for x in before if x != coerced]
    if len(candidate) == len(before):
        raise HTTPException(
            status_code=404,
            detail="Item {item!r} not found in {enum_type}".format(
                item=coerced, enum_type=enum_type
            ),
        )
    items = _validate(enum_type, candidate)
    persisted = _persist_items(enum_type, items, current_user)
    _audit(
        "remove",
        enum_type,
        current_user,
        before=before,
        after=items,
        notes="item={item!r}".format(item=coerced),
    )
    return {"status": "success", "enum": _clean(persisted)}


def _index_equal(a: Any, b: Any) -> bool:
    """Tolerant float compare for index removals."""
    try:
        return round(float(a), 4) == round(float(b), 4)
    except (TypeError, ValueError):
        return False
