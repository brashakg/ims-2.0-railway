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
  POST   /{enum_type}/rename                     rename + CASCADE (B'3)
  DELETE /{enum_type}/items/{item}               remove one item (refused if in use)

Role gates:
  SUPERADMIN / ADMIN / CATALOG_MANAGER may mutate -- the catalog manager
  owns the lens catalog (mirrors lens_catalog.py's own write gate). Enum
  edits affect every lens line, so the blast radius is high; reads stay
  open to any authenticated user.

Cascade rename (B'3): renaming an enum value (e.g. "Essilor" -> "Essilor
India") rewrites the enum master, every lens_catalog row that uses the old
value (brand/coating/index/material/lens_type), and stamps every dependent
lens_stock_lines row -- atomically from the caller's view, audit-logged with
kind="lens_enum_rename". The lens_line_id SLUG is intentionally NOT re-built:
the slug is a stable identifier referenced by stock rows + historical orders
(see LensLineUpdate in lens_catalog.py, which forbids identity edits for the
same reason). Stock lines reference a line by its stable lens_line_id, so a
rename keeps every FK link intact while the displayed brand/coating updates.

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

# SUPERADMIN / ADMIN / CATALOG_MANAGER can edit enums. SUPERADMIN auto-passes
# inside require_roles, so we only need to name ADMIN + CATALOG_MANAGER here.
# This mirrors lens_catalog.py's _WRITE_ROLES so the catalog manager who edits
# lens lines can also edit the enum lists those lines draw from.
_WRITE_ROLES = ("ADMIN", "CATALOG_MANAGER")


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


def _stock_coll():
    """Loaded on RENAME so the cascade can stamp dependent stock rows."""
    db = _get_db()
    if db is None:
        return None
    try:
        return db.get_collection("lens_stock_lines")
    except Exception:  # noqa: BLE001
        return None


# Map an enum_type to the lens_catalog field it populates. `series` is
# intentionally absent -- it is a per-brand list, not a 1:1 column, so it is
# renamed via PATCH /series with the new full list (same rule as delete).
_ENUM_FIELD_MAP: Dict[str, str] = {
    "coatings": "coating",
    "brands": "brand",
    "indexes": "index",
    "materials": "material",
    "lens_types": "lens_type",
}


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
            logger.warning("[LENS_ENUMS] load %s failed: %s", enum_type, exc)
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
        logger.error("[LENS_ENUMS] persist %s failed: %s", enum_type, exc)
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


def _audit_rename(
    enum_type: str,
    user: Dict[str, Any],
    old_value: Any,
    new_value: Any,
    catalog_modified: int,
    stock_modified: int,
) -> None:
    """Audit-log a cascade rename to audit_logs (kind=lens_enum_rename).

    Distinct from the generic enum mutations (which go through _audit) so
    the auditor can grep one query for every cascade rename + see how far
    the blast radius reached (catalog rows + stock rows touched)."""
    try:
        repo = get_audit_repository()
        if repo is None:
            return
        repo.create(
            {
                "action": "lens_enum.rename",
                "kind": "lens_enum_rename",
                "module": "inventory",
                "entity_type": "lens_enum_config",
                "entity_id": enum_type,
                "user_id": user.get("user_id"),
                "user_name": user.get("username"),
                "severity": "INFO",
                "before": {"value": old_value},
                "after": {"value": new_value},
                "metadata": {
                    "enum_type": enum_type,
                    "old_value": old_value,
                    "new_value": new_value,
                    "catalog_rows_updated": catalog_modified,
                    "stock_rows_stamped": stock_modified,
                },
                "timestamp": _now(),
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[LENS_ENUMS] rename audit insert failed for %s: %s",
            enum_type,
            exc,
        )


# The identity tuple that lens_catalog enforces UNIQUE. Renaming one of
# these fields can collide two lines onto the same tuple.
_IDENTITY_FIELDS: tuple = (
    "brand",
    "series",
    "index",
    "material",
    "lens_type",
    "coating",
)


def _identity_key(doc: Dict[str, Any], field: str, new_value: Any) -> tuple:
    """The post-rename identity tuple for `doc` (substituting new_value for
    `field`). Used to detect collisions before the cascade write."""
    return tuple(new_value if f == field else doc.get(f) for f in _IDENTITY_FIELDS)


def _cascade_rename_catalog(field: str, old_value: Any, new_value: Any) -> List[str]:
    """Rewrite `field` from old_value -> new_value on every lens_catalog row.
    Returns the affected lens_line_id list (used to stamp stock rows + report
    the blast radius). Fail-soft: returns [] when the collection is absent.

    Pre-flight collision check: if the rename would push any affected line
    onto an identity tuple already held by a DIFFERENT line, we 409 BEFORE
    writing anything (update_many is not atomic across docs, so a partial
    rename would otherwise be possible)."""
    coll = _catalog_coll()
    if coll is None:
        return []
    try:
        affected_docs = list(coll.find({field: old_value}))
    except Exception as exc:  # noqa: BLE001
        logger.error("[LENS_ENUMS] rename catalog scan failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Failed to read lens_catalog for rename cascade",
        )
    affected = [
        str(d.get("lens_line_id")) for d in affected_docs if d.get("lens_line_id")
    ]
    if not affected:
        return []

    # Pre-flight: would the rename collide onto an existing different line?
    try:
        all_docs = list(coll.find({}))
    except Exception:  # noqa: BLE001
        all_docs = affected_docs
    affected_ids = set(affected)
    existing_keys = {
        tuple(d.get(f) for f in _IDENTITY_FIELDS): str(d.get("lens_line_id"))
        for d in all_docs
        if str(d.get("lens_line_id")) not in affected_ids
    }
    post_keys: set = set()
    for d in affected_docs:
        key = _identity_key(d, field, new_value)
        if key in existing_keys or key in post_keys:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Rename to {new!r} would collide two lens lines onto the "
                    "same identity (brand/series/index/material/lens_type/"
                    "coating). Merge or retire the conflicting line first; "
                    "the enum list was left unchanged.".format(new=new_value)
                ),
            )
        post_keys.add(key)
    try:
        coll.update_many(
            {field: old_value},
            {"$set": {field: new_value, "updated_at": _now()}},
        )
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "duplicate key" in msg.lower() or "E11000" in msg:
            # Renaming would collide two lens lines onto the same identity
            # tuple (brand, series, index, material, lens_type, coating).
            # The owner must merge/retire one of them first. The enum master
            # is still untouched (catalog write came first), so this is a
            # clean refusal.
            raise HTTPException(
                status_code=409,
                detail=(
                    "Rename to {new!r} would collide two lens lines onto the "
                    "same identity (brand/series/index/material/lens_type/"
                    "coating). Merge or retire the conflicting line first; "
                    "the enum list was left unchanged.".format(new=new_value)
                ),
            )
        logger.error("[LENS_ENUMS] rename catalog update failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to cascade rename onto lens_catalog "
                "({field}); enum master left unchanged.".format(field=field)
            ),
        )
    return affected


def _cascade_stamp_stock(lens_line_ids: List[str]) -> int:
    """Stamp every lens_stock_lines row whose parent line was renamed with
    enum_rename_at=now. The stock row references the line by its stable
    lens_line_id (unchanged by the rename), so its effective brand/coating
    already resolves to the new value -- the stamp makes the cascade an
    observable, audit-able write across all three layers. Returns the row
    count. Fail-soft: returns 0 when stock collection absent."""
    if not lens_line_ids:
        return 0
    coll = _stock_coll()
    if coll is None:
        return 0
    try:
        result = coll.update_many(
            {"lens_line_id": {"$in": lens_line_ids}},
            {"$set": {"enum_rename_at": _now()}},
        )
        return int(getattr(result, "modified_count", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        # Stock stamping is best-effort -- the catalog rename already
        # succeeded and the FK links are intact. Log + report 0.
        logger.warning("[LENS_ENUMS] rename stock stamp failed: %s", exc)
        return 0


@router.post("/{enum_type}/rename")
async def rename_item(
    enum_type: str = Path(...),
    body: Dict[str, Any] = Body(...),
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Rename an enum value and CASCADE the change (B'3).

    Body shape: {old_value, new_value}. Renames the value in the enum master
    AND on every lens_catalog row + stamps every dependent lens_stock_lines
    row, then audit-logs the cascade (kind=lens_enum_rename).

    The order is deliberate so a mid-cascade failure leaves the system
    consistent: (1) cascade onto lens_catalog FIRST (the wide write), (2)
    flip the enum master only after the catalog write succeeds, (3) stamp
    stock rows (best-effort, FK links stay intact regardless). If the
    catalog write fails the enum master is untouched -> a clean retry.

    `series` cannot be renamed here (it is a per-brand list); PATCH
    /lens-enums/series with the new full list instead. `indexes` rename
    takes numeric old/new values.
    """
    enum_type = _check_enum_type(enum_type)
    if "old_value" not in body or "new_value" not in body:
        raise HTTPException(
            status_code=400,
            detail="`old_value` and `new_value` are required",
        )

    if enum_type == "series":
        raise HTTPException(
            status_code=400,
            detail=(
                "Series cannot be renamed via this endpoint; "
                "PATCH /lens-enums/series with the new full list."
            ),
        )

    old_value = _coerce_item(enum_type, body["old_value"])
    new_value = _coerce_item(enum_type, body["new_value"])

    # Build the new enum list: swap old -> new, then validate (de-dupes,
    # type-checks, rejects index<=1.0, etc.). The validator collapses a
    # rename-onto-an-existing-value into a single entry, which is the
    # correct "merge two values into one" behaviour.
    before = _load_items(enum_type)
    if enum_type == "indexes":
        present = any(_index_equal(x, old_value) for x in before)
        candidate = [(new_value if _index_equal(x, old_value) else x) for x in before]
    else:
        present = old_value in before
        candidate = [(new_value if x == old_value else x) for x in before]
    if not present:
        raise HTTPException(
            status_code=404,
            detail="{old!r} not found in {enum_type}".format(
                old=old_value, enum_type=enum_type
            ),
        )
    if old_value == new_value:
        raise HTTPException(
            status_code=400,
            detail="old_value and new_value are identical; nothing to rename",
        )

    # Validate the resulting list BEFORE touching any catalog rows, so a
    # bad new_value (e.g. empty string, index<=1.0) 400s without a partial
    # cascade.
    new_items = _validate(enum_type, candidate)

    field = _ENUM_FIELD_MAP.get(enum_type)
    if not field:
        # Should be unreachable (series handled above), defensive only.
        raise HTTPException(
            status_code=400,
            detail="enum_type {0!r} cannot be cascade-renamed".format(enum_type),
        )

    # 1. Cascade onto lens_catalog (the wide write) FIRST.
    affected_lines = _cascade_rename_catalog(field, old_value, new_value)
    # 2. Flip the enum master only after the catalog write succeeded.
    persisted = _persist_items(enum_type, new_items, current_user)
    # 3. Stamp dependent stock rows (best-effort).
    stock_modified = _cascade_stamp_stock(affected_lines)

    _audit_rename(
        enum_type,
        current_user,
        old_value,
        new_value,
        catalog_modified=len(affected_lines),
        stock_modified=stock_modified,
    )
    return {
        "status": "success",
        "enum": _clean(persisted),
        "cascade": {
            "old_value": old_value,
            "new_value": new_value,
            "catalog_rows_updated": len(affected_lines),
            "stock_rows_stamped": stock_modified,
            "affected_lens_line_ids": affected_lines,
        },
    }


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
        candidate = [x for x in before if not _index_equal(x, coerced)]
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
