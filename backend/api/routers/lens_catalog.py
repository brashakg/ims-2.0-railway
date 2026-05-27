"""
IMS 2.0 - Lens Catalog Router (Branch B' sub-PR 1)
==================================================
CRUD for the `lens_catalog` collection -- the owner-typed master list of
lens lines (brand x series x index x material x lens_type x coating combos).
Each row is the *line*; the per-cell power matrix lives in lens_stock_lines
(see routers/lens_stock.py).

Endpoints (prefix /api/v1/lens-catalog):
  GET    /                                       list (filters)
  GET    /{lens_line_id}                         single
  POST   /                                       create
  PATCH  /{lens_line_id}                         update
  DELETE /{lens_line_id}                         soft-delete (refused if stock)
  GET    /meta/options                           live enum config (Q5)

Role gates (per locked spec):
  READ  -- any authenticated user (no store gate; the lens catalog is
           cross-store identity, not stock).
  WRITE -- SUPERADMIN / ADMIN / CATALOG_MANAGER (lens lines are master data;
           store managers do NOT edit identity, only stock).

Audit logging: every write emits a row into `audit_logs` via the existing
get_audit_repository(). Fail-soft -- audit failure never undoes the write.

NO mock business data: empty state on first deploy except for the Q6 seed
of the enum config (technical-dimension defaults, populated by migration).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import get_current_user, require_roles
from ..dependencies import get_audit_repository
from ..services.lens_catalog_validation import (
    DEFAULT_ENUM_ITEMS,
    ENUM_TYPES,
    slugify_lens_line,
    validate_lens_catalog_payload,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Roles allowed to MUTATE catalog rows. SUPERADMIN auto-passes inside
# require_roles. CATALOG_MANAGER is the canonical role for catalog work
# (mirrors the products / catalog router).
_WRITE_ROLES = ("ADMIN", "CATALOG_MANAGER")


# ============================================================================
# REQUEST / RESPONSE MODELS
# ============================================================================


class _RangeIn(BaseModel):
    """sph_range / cyl_range / add_range shape. step defaults to 0.25 in
    the validator if omitted."""

    min: float
    max: float
    step: Optional[float] = 0.25


class LensLineCreate(BaseModel):
    """Payload for POST /lens-catalog.

    coating is a SINGLE string per Q1 -- combos like 'DUAL_COAT' are their
    own coating codes. Owner edits the coating list in Settings.
    """

    brand: str
    series: str
    index: float
    material: str
    lens_type: str
    coating: str
    sph_range: Optional[_RangeIn] = None
    cyl_range: Optional[_RangeIn] = None
    has_add: Optional[bool] = None
    add_range: Optional[_RangeIn] = None
    mrp: float = Field(..., ge=0)
    cost_price: Optional[float] = Field(None, ge=0)
    mrp_table: Optional[List[Dict[str, Any]]] = None
    gst_rate: Optional[float] = Field(None, ge=0, le=28)
    hsn_code: Optional[str] = None
    notes: Optional[str] = None


class LensLineUpdate(BaseModel):
    """PATCH /lens-catalog/{lens_line_id}. All fields optional. Identity
    fields (brand/series/index/material/lens_type/coating) cannot be
    changed -- changing them would force a new lens_line_id slug, which
    breaks every stock row that references the old slug. Callers should
    create a new line + transfer stock instead."""

    sph_range: Optional[_RangeIn] = None
    cyl_range: Optional[_RangeIn] = None
    has_add: Optional[bool] = None
    add_range: Optional[_RangeIn] = None
    mrp: Optional[float] = Field(None, ge=0)
    cost_price: Optional[float] = Field(None, ge=0)
    mrp_table: Optional[List[Dict[str, Any]]] = None
    gst_rate: Optional[float] = Field(None, ge=0, le=28)
    hsn_code: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


# ============================================================================
# DB HELPERS
# ============================================================================


def _get_db():
    """Raw MongoDB handle, or None when unavailable (mock / no-DB mode)."""
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def _catalog_coll():
    db = _get_db()
    if db is None:
        return None
    try:
        return db.get_collection("lens_catalog")
    except Exception:  # noqa: BLE001
        return None


def _enum_coll():
    db = _get_db()
    if db is None:
        return None
    try:
        return db.get_collection("lens_enum_config")
    except Exception:  # noqa: BLE001
        return None


def _stock_coll():
    """Loaded on DELETE so we can refuse delete when stock exists."""
    db = _get_db()
    if db is None:
        return None
    try:
        return db.get_collection("lens_stock_lines")
    except Exception:  # noqa: BLE001
        return None


def load_enum_config() -> Dict[str, Any]:
    """Load the lens_enum_config collection into a dict keyed by enum_type.

    Used by the catalog validator to check enum membership. When the
    collection is empty or absent, falls back to DEFAULT_ENUM_ITEMS so
    the FIRST lens-line create after a fresh deploy still works (the
    migration runner seeds the technical-dimension lists -- coatings,
    indexes, materials, lens_types -- but brands + series start empty
    so creates that need a brand will 400 until the owner populates it).
    """
    coll = _enum_coll()
    out: Dict[str, Any] = {}
    if coll is not None:
        try:
            for doc in coll.find({}):
                enum_id = doc.get("enum_id")
                items = doc.get("items")
                if isinstance(enum_id, str) and isinstance(items, list):
                    out[enum_id] = list(items)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[LENS_CATALOG] enum load failed: %s", exc)
    # Fill in any missing enum_type with the Q6 defaults. Owner-edited
    # values always win because they were loaded above.
    for enum_id, default in DEFAULT_ENUM_ITEMS.items():
        if enum_id not in out:
            out[enum_id] = list(default)
    return out


def _now() -> datetime:
    """UTC -- pymongo wraps to BSON date on insert."""
    return datetime.utcnow()


def _clean(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Strip Mongo _id + coerce datetime to ISO so the response JSON-
    encodes cleanly on every driver version."""
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


def _audit(
    action: str,
    lens_line_id: str,
    user: Dict[str, Any],
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    notes: Optional[str] = None,
) -> None:
    """Audit-log a catalog mutation. Fail-soft."""
    try:
        repo = get_audit_repository()
        if repo is None:
            return
        repo.create(
            {
                "action": "lens_catalog.{}".format(action),
                "module": "inventory",
                "entity_type": "lens_catalog",
                "entity_id": lens_line_id,
                "user_id": user.get("user_id"),
                "user_name": user.get("username"),
                "severity": "INFO",
                "before": before,
                "after": after,
                "metadata": {"notes": notes} if notes else None,
                "timestamp": _now(),
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[LENS_CATALOG] audit insert failed for %s: %s",
            lens_line_id,
            exc,
        )


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("/meta/options")
async def lens_catalog_meta_options(
    current_user: dict = Depends(get_current_user),
):
    """Live enum config -- powers the FE dropdowns (brand, series, etc.).
    Mounted ABOVE /{lens_line_id} so a slug literally called 'meta' could
    never shadow it."""
    _ = current_user
    cfg = load_enum_config()
    return {
        "enums": cfg,
        "enum_types": list(ENUM_TYPES),
    }


@router.get("")
@router.get("/")
async def list_lens_lines(
    brand: Optional[str] = Query(None),
    series: Optional[str] = Query(None),
    index: Optional[float] = Query(None),  # noqa: A002 - matches the request
    material: Optional[str] = Query(None),
    lens_type: Optional[str] = Query(None),
    coating: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Free-text on brand/series"),
    active: bool = Query(True),
    limit: int = Query(50, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
):
    """List lens lines, filterable. active=True (default) hides soft-deleted.
    The lens catalog is cross-store identity (one row per combo regardless
    of where stock sits), so we do NOT store-scope here -- a STORE_MANAGER
    in Bokaro and a CATALOG_MANAGER in Pune both see the same catalog."""
    _ = current_user
    coll = _catalog_coll()
    if coll is None:
        return {"lens_lines": [], "total": 0}

    query: Dict[str, Any] = {}
    if brand:
        query["brand"] = brand
    if series:
        query["series"] = series
    if index is not None:
        query["index"] = float(index)
    if material:
        query["material"] = material
    if lens_type:
        query["lens_type"] = lens_type
    if coating:
        query["coating"] = coating
    if active:
        query["is_active"] = {"$ne": False}
    if q:
        # Case-insensitive substring on brand+series. Simple regex, not
        # an Atlas text-index search -- a few hundred lens lines max.
        import re as _re

        try:
            rx = _re.compile(_re.escape(q.strip()), _re.IGNORECASE)
            query["$or"] = [{"brand": rx}, {"series": rx}]
        except Exception:  # noqa: BLE001
            pass

    try:
        docs = list(coll.find(query).limit(limit))
    except Exception as exc:  # noqa: BLE001
        logger.error("[LENS_CATALOG] list failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to list lens lines")

    cleaned = [_clean(d) for d in docs]
    # Stable sort: brand, series, index, material, lens_type, coating.
    cleaned.sort(
        key=lambda d: (
            (d or {}).get("brand") or "",
            (d or {}).get("series") or "",
            (d or {}).get("index") or 0,
            (d or {}).get("material") or "",
            (d or {}).get("lens_type") or "",
            (d or {}).get("coating") or "",
        )
    )
    return {"lens_lines": cleaned, "total": len(cleaned)}


@router.get("/{lens_line_id}")
async def get_lens_line(
    lens_line_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Single lens line by slug. 404 when missing."""
    _ = current_user
    coll = _catalog_coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        doc = coll.find_one({"lens_line_id": lens_line_id})
    except Exception as exc:  # noqa: BLE001
        logger.error("[LENS_CATALOG] get failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to get lens line")
    if not doc:
        raise HTTPException(status_code=404, detail="Lens line not found")
    return {"lens_line": _clean(doc)}


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_lens_line(
    payload: LensLineCreate,
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Create a new lens line. The slug (lens_line_id) is derived from
    the identity tuple -- two creates with the same (brand, series, index,
    material, lens_type, coating) will conflict on the unique index, which
    we lift to 409 with a useful message."""
    coll = _catalog_coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")

    enum_config = load_enum_config()
    raw = payload.model_dump(exclude_none=True)
    try:
        normalized = validate_lens_catalog_payload(raw, enum_config=enum_config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        lens_line_id = slugify_lens_line(
            normalized["brand"],
            normalized["series"],
            normalized["index"],
            normalized["material"],
            normalized["lens_type"],
            normalized["coating"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    doc = dict(normalized)
    doc["lens_line_id"] = lens_line_id
    now = _now()
    doc["created_at"] = now
    doc["updated_at"] = now
    doc["created_by"] = current_user.get("user_id")

    try:
        coll.insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "duplicate key" in msg.lower() or "E11000" in msg:
            raise HTTPException(
                status_code=409,
                detail=(
                    "A lens line with the same brand/series/index/material/"
                    "lens_type/coating already exists "
                    "(lens_line_id={slug!r}).".format(slug=lens_line_id)
                ),
            )
        logger.error("[LENS_CATALOG] create failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create lens line")

    cleaned = _clean(doc) or {}
    _audit("create", lens_line_id, current_user, before=None, after=cleaned)
    return {"status": "success", "lens_line": cleaned}


@router.patch("/{lens_line_id}")
async def update_lens_line(
    lens_line_id: str,
    payload: LensLineUpdate,
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """PATCH a lens line. Identity fields are NOT patchable -- the LensLine
    Update model doesn't include them, so this enforces it at the API
    surface. Returns the updated doc."""
    coll = _catalog_coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")

    existing = coll.find_one({"lens_line_id": lens_line_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Lens line not found")

    raw = payload.model_dump(exclude_none=True)
    if not raw:
        return {"status": "no_changes", "lens_line": _clean(existing)}

    enum_config = load_enum_config()
    try:
        normalized = validate_lens_catalog_payload(
            raw, enum_config=enum_config, existing=existing
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Only push fields that actually changed -- keeps the audit diff honest.
    changed: Dict[str, Any] = {}
    for k, v in normalized.items():
        if k in raw:
            changed[k] = v
    if not changed:
        return {"status": "no_changes", "lens_line": _clean(existing)}
    changed["updated_at"] = _now()

    try:
        coll.update_one({"lens_line_id": lens_line_id}, {"$set": changed})
    except Exception as exc:  # noqa: BLE001
        logger.error("[LENS_CATALOG] update failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to update lens line")

    updated = coll.find_one({"lens_line_id": lens_line_id})
    cleaned_before = _clean(existing) or {}
    cleaned_after = _clean(updated) or {}
    _audit(
        "update",
        lens_line_id,
        current_user,
        before=cleaned_before,
        after=cleaned_after,
    )
    return {"status": "success", "lens_line": cleaned_after}


@router.delete("/{lens_line_id}")
async def delete_lens_line(
    lens_line_id: str,
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Soft-delete a lens line (set is_active=False). Refuses if any
    stock_lines row for this line has on_hand > 0 OR reserved > 0 --
    the owner must zero out stock first. Returns 409 with the dependent
    row count."""
    coll = _catalog_coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    existing = coll.find_one({"lens_line_id": lens_line_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Lens line not found")

    # Block soft-delete if ANY stock cell still carries on_hand or reserved.
    stock = _stock_coll()
    if stock is not None:
        try:
            n = stock.count_documents(
                {
                    "lens_line_id": lens_line_id,
                    "$or": [
                        {"on_hand": {"$gt": 0}},
                        {"reserved": {"$gt": 0}},
                    ],
                }
            )
        except Exception:  # noqa: BLE001
            n = 0
        if n:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot delete lens line: {n} stock cell(s) still carry "
                    "on_hand or reserved units. Zero them out first.".format(
                        n=n
                    )
                ),
            )

    try:
        coll.update_one(
            {"lens_line_id": lens_line_id},
            {"$set": {"is_active": False, "updated_at": _now()}},
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[LENS_CATALOG] delete failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to delete lens line")

    cleaned_before = _clean(existing) or {}
    _audit(
        "delete",
        lens_line_id,
        current_user,
        before=cleaned_before,
        after=None,
        notes="soft-delete (is_active=False)",
    )
    return {"status": "success", "lens_line_id": lens_line_id}
