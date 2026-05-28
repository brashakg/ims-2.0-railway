"""
IMS 2.0 - Lens Stock Router (Branch B' sub-PR 1)
================================================
Per-cell power-matrix CRUD for the `lens_stock_lines` collection + the
atomic reserve/commit/release endpoints (Q4) that POS Step 6 and Workshop
dispatch will call from B'4.

Endpoints (prefix /api/v1/lens-stock):
  GET    /{lens_line_id}?store_id=                full power matrix
  GET    /cell/{line_stock_id}                    single cell detail
  POST   /                                        create a cell
  PATCH  /{line_stock_id}                         set on_hand / reorder_point
  POST   /{lens_line_id}/bulk-import              paste a 2D matrix (Q3)
  POST   /{lens_line_id}/reserve                  POS Step 6 (Q4)
  POST   /{lens_line_id}/commit                   Workshop dispatch (Q4)
  POST   /{lens_line_id}/release                  Order cancel (Q4)
  GET    /audit/{line_stock_id}                   adjustment history
  GET    /gap-planner?store_id=                   cells where on_hand < reorder

Q4 atomicity: reserve / commit / release use Mongo's find_one_and_update
with a $expr predicate so concurrent POS terminals can't oversell. The
condition is checked atomically by the DB; on a failed condition the
endpoint returns 409 and the caller (POS) re-renders availability.

Role gates:
  READ  -- any authenticated user with store access (validate_store_access)
  WRITE -- SUPERADMIN / ADMIN / CATALOG_MANAGER / STORE_MANAGER (the last
           is store-scoped through validate_store_access)
  reserve/commit/release: same as WRITE -- POS and Workshop services call
           these via the user's JWT (no system token in this PR).

Audit logging: every stock movement (reserve, commit, release, set_on_hand,
bulk_import, create) writes a row into the `lens_stock_audit` collection.
Independent of the broader audit_logs collection so the lens-stock history
is one focused, fast query.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import get_current_user, require_roles
from ..dependencies import validate_store_access
from ..services.lens_catalog_validation import (
    compute_available,
    validate_bulk_import_payload,
    validate_lens_stock_line_payload,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Roles allowed to MUTATE stock. STORE_MANAGER is store-scoped through
# validate_store_access. SUPERADMIN auto-passes inside require_roles.
_WRITE_ROLES = ("ADMIN", "CATALOG_MANAGER", "STORE_MANAGER")


# ============================================================================
# REQUEST MODELS
# ============================================================================


class StockCellCreate(BaseModel):
    """POST /lens-stock: create a single cell."""

    lens_line_id: str
    store_id: str
    sph: float
    cyl: float = 0.0
    add: Optional[float] = None
    on_hand: int = Field(0, ge=0)
    reserved: int = Field(0, ge=0)
    reorder_point: int = Field(0, ge=0)
    safety_stock: int = Field(0, ge=0)


class StockCellUpdate(BaseModel):
    """PATCH /lens-stock/{line_stock_id}: absolute on_hand / reorder updates.

    Note: do NOT update reserved here. Reserved is only mutated by the
    reserve/commit/release endpoints with atomic CAS. The router refuses
    a reserved-in-PATCH at runtime."""

    on_hand: Optional[int] = Field(None, ge=0)
    reorder_point: Optional[int] = Field(None, ge=0)
    safety_stock: Optional[int] = Field(None, ge=0)
    notes: Optional[str] = None


class BulkImportRow(BaseModel):
    """One row in the bulk-import body (JSON shape)."""

    sph: float
    cyl: float = 0.0
    add: Optional[float] = None
    qty: int = Field(..., ge=0)


class BulkImportPayload(BaseModel):
    """POST /lens-stock/{lens_line_id}/bulk-import.

    Either pass `matrix` (a JSON list of cells) or `csv` (a CSV string with
    a header row -- "sph,cyl,add,qty" or "sph,cyl,qty"). store_id is
    required at the top level so a single import targets one store."""

    store_id: str
    matrix: Optional[List[BulkImportRow]] = None
    csv: Optional[str] = None
    source_id: Optional[str] = None  # e.g. uploaded file name


class ReserveCommitReleasePayload(BaseModel):
    """POST /lens-stock/{lens_line_id}/{reserve|commit|release}.

    The cell key is (store_id, sph, cyl, add). source_type / source_id
    let the caller record what triggered the movement (POS order id,
    workshop job id, etc.) so the audit history is traceable."""

    store_id: str
    sph: float
    cyl: float = 0.0
    add: Optional[float] = None
    qty: int = Field(..., gt=0)
    source_type: Optional[str] = None  # POS / WORKSHOP / ORDER_CANCEL / MANUAL
    source_id: Optional[str] = None
    notes: Optional[str] = None


# ============================================================================
# DB HELPERS
# ============================================================================


def _get_db():
    """Raw MongoDB handle, or None when unavailable."""
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def _stock_coll():
    db = _get_db()
    if db is None:
        return None
    try:
        return db.get_collection("lens_stock_lines")
    except Exception:  # noqa: BLE001
        return None


def _catalog_coll():
    db = _get_db()
    if db is None:
        return None
    try:
        return db.get_collection("lens_catalog")
    except Exception:  # noqa: BLE001
        return None


def _audit_coll():
    db = _get_db()
    if db is None:
        return None
    try:
        return db.get_collection("lens_stock_audit")
    except Exception:  # noqa: BLE001
        return None


def _now() -> datetime:
    return datetime.utcnow()


def _clean(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Strip _id, ISO-format datetimes."""
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


def _enrich(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Add computed `available` to the response so the FE doesn't have to."""
    cleaned = _clean(doc)
    if cleaned is None:
        return None
    cleaned["available"] = compute_available(
        cleaned.get("on_hand", 0), cleaned.get("reserved", 0)
    )
    return cleaned


def _load_lens_line(lens_line_id: str) -> Optional[Dict[str, Any]]:
    coll = _catalog_coll()
    if coll is None:
        return None
    try:
        return coll.find_one({"lens_line_id": lens_line_id})
    except Exception:  # noqa: BLE001
        return None


def _write_audit(
    *,
    action: str,
    line_stock_id: str,
    lens_line_id: str,
    store_id: str,
    delta_on_hand: int,
    delta_reserved: int,
    prior: Dict[str, int],
    after: Dict[str, int],
    user: Dict[str, Any],
    source_type: Optional[str] = None,
    source_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> None:
    """Append a row to lens_stock_audit. Fail-soft."""
    coll = _audit_coll()
    if coll is None:
        return
    doc: Dict[str, Any] = {
        "audit_id": uuid.uuid4().hex,
        "line_stock_id": line_stock_id,
        "lens_line_id": lens_line_id,
        "store_id": store_id,
        "action": action,
        "delta_on_hand": int(delta_on_hand),
        "delta_reserved": int(delta_reserved),
        "prior": {
            "on_hand": int(prior.get("on_hand", 0)),
            "reserved": int(prior.get("reserved", 0)),
        },
        "after": {
            "on_hand": int(after.get("on_hand", 0)),
            "reserved": int(after.get("reserved", 0)),
        },
        "source_type": source_type,
        "source_id": source_id,
        "by_user_id": str(user.get("user_id") or ""),
        "by_user_name": str(user.get("username") or ""),
        "notes": notes,
        "at": _now(),
    }
    try:
        coll.insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[LENS_STOCK] audit insert failed for %s: %s",
            line_stock_id,
            exc,
        )


def _return_document_after():
    """Resolve ReturnDocument.AFTER without hard-importing pymongo at
    module load. Fail-soft to None (the driver default) when pymongo is
    not installed -- the tests use an in-memory FakeColl that returns the
    updated doc unconditionally."""
    try:
        from pymongo import ReturnDocument

        return ReturnDocument.AFTER
    except Exception:  # noqa: BLE001
        return None


# ============================================================================
# READ ENDPOINTS
# ============================================================================


@router.get("/gap-planner")
async def gap_planner(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Cells where (on_hand - reserved) < reorder_point. The FE shows this
    as a 'reorder these' tab. Mounted ABOVE /{lens_line_id} so a literal
    slug 'gap-planner' could not shadow it."""
    active_store = validate_store_access(store_id, current_user)
    coll = _stock_coll()
    if coll is None:
        return {"cells": [], "total": 0}

    query: Dict[str, Any] = {}
    if active_store:
        query["store_id"] = active_store
    # We can't express (on_hand - reserved) < reorder_point in a Mongo
    # find without aggregate, so pull everything store-scoped and filter
    # in Python. Volume is small (a few thousand cells max).
    try:
        docs = list(coll.find(query))
    except Exception as exc:  # noqa: BLE001
        logger.error("[LENS_STOCK] gap_planner read failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to read stock")

    out: List[Dict[str, Any]] = []
    for d in docs:
        avail = compute_available(d.get("on_hand", 0), d.get("reserved", 0))
        rp = int(d.get("reorder_point") or 0)
        if rp > 0 and avail < rp:
            ed = _enrich(d) or {}
            ed["gap"] = rp - avail
            out.append(ed)
    out.sort(key=lambda c: (c.get("gap") or 0), reverse=True)
    return {"cells": out, "total": len(out)}


@router.get("/audit/{line_stock_id}")
async def stock_audit(
    line_stock_id: str,
    limit: int = Query(100, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
):
    """History of movements for a single cell. Ordered desc by `at`."""
    _ = current_user
    coll = _audit_coll()
    if coll is None:
        return {"audit": [], "total": 0}
    try:
        cursor = coll.find({"line_stock_id": line_stock_id})
    except Exception as exc:  # noqa: BLE001
        logger.error("[LENS_STOCK] audit read failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to read audit")
    docs = [_clean(d) for d in cursor]
    # Sort in Python: desc by `at`.
    docs.sort(
        key=lambda d: (d.get("at") if isinstance(d, dict) else "") or "",
        reverse=True,
    )
    return {"audit": docs[:limit], "total": len(docs)}


@router.get("/cell/{line_stock_id}")
async def get_cell(
    line_stock_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Single-cell detail. Store-scoped via validate_store_access."""
    coll = _stock_coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        doc = coll.find_one({"line_stock_id": line_stock_id})
    except Exception as exc:  # noqa: BLE001
        logger.error("[LENS_STOCK] cell read failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to read cell")
    if not doc:
        raise HTTPException(status_code=404, detail="Stock cell not found")
    validate_store_access(doc.get("store_id"), current_user)
    return {"cell": _enrich(doc)}


@router.get("/{lens_line_id}")
async def list_cells(
    lens_line_id: str,
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Full power matrix for a lens line at one store. Returns the raw
    cells -- the FE builds the (sph, cyl, add) grid."""
    active_store = validate_store_access(store_id, current_user)
    coll = _stock_coll()
    if coll is None:
        return {"cells": [], "total": 0, "lens_line_id": lens_line_id}

    query: Dict[str, Any] = {"lens_line_id": lens_line_id}
    if active_store:
        query["store_id"] = active_store
    try:
        docs = list(coll.find(query))
    except Exception as exc:  # noqa: BLE001
        logger.error("[LENS_STOCK] matrix read failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to read matrix")
    cleaned = [_enrich(d) for d in docs]
    cleaned.sort(
        key=lambda c: (
            (c or {}).get("sph") or 0,
            (c or {}).get("cyl") or 0,
            ((c or {}).get("add") if (c or {}).get("add") is not None else -999),
        )
    )
    return {
        "cells": cleaned,
        "total": len(cleaned),
        "lens_line_id": lens_line_id,
    }


# ============================================================================
# WRITE ENDPOINTS
# ============================================================================


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_cell(
    payload: StockCellCreate,
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Create a new stock cell. (sph, cyl, add) is validated against the
    parent lens line's ranges, and add-nullness is enforced. Duplicate
    (lens_line_id, store_id, sph, cyl, add) raises 409."""
    coll = _stock_coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")

    # Store-scope: a STORE_MANAGER can only write into their own store.
    active_store = validate_store_access(payload.store_id, current_user)

    lens_line = _load_lens_line(payload.lens_line_id)
    if not lens_line:
        raise HTTPException(
            status_code=400,
            detail="Lens line {id!r} does not exist".format(id=payload.lens_line_id),
        )

    raw = payload.model_dump()
    raw["store_id"] = active_store
    try:
        normalized = validate_lens_stock_line_payload(raw, lens_line=lens_line)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    line_stock_id = uuid.uuid4().hex
    doc = dict(normalized)
    doc["line_stock_id"] = line_stock_id
    doc["last_movement_at"] = _now()

    try:
        coll.insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "duplicate key" in msg.lower() or "E11000" in msg:
            raise HTTPException(
                status_code=409,
                detail=(
                    "A stock cell already exists at this (sph, cyl, add) "
                    "for this lens line + store."
                ),
            )
        logger.error("[LENS_STOCK] create failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create cell")

    _write_audit(
        action="create",
        line_stock_id=line_stock_id,
        lens_line_id=doc["lens_line_id"],
        store_id=doc["store_id"],
        delta_on_hand=int(doc.get("on_hand") or 0),
        delta_reserved=int(doc.get("reserved") or 0),
        prior={"on_hand": 0, "reserved": 0},
        after={
            "on_hand": int(doc.get("on_hand") or 0),
            "reserved": int(doc.get("reserved") or 0),
        },
        user=current_user,
        source_type="MANUAL",
    )
    return {"status": "success", "cell": _enrich(doc)}


@router.patch("/{line_stock_id}")
async def update_cell(
    line_stock_id: str,
    payload: StockCellUpdate,
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Absolute updates to on_hand / reorder_point / safety_stock. Does NOT
    touch reserved -- reserved is only mutated by reserve/commit/release."""
    coll = _stock_coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")

    existing = coll.find_one({"line_stock_id": line_stock_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Stock cell not found")
    validate_store_access(existing.get("store_id"), current_user)

    raw = payload.model_dump(exclude_none=True)
    notes = raw.pop("notes", None)
    if not raw:
        return {"status": "no_changes", "cell": _enrich(existing)}

    prior_on_hand = int(existing.get("on_hand") or 0)
    prior_reserved = int(existing.get("reserved") or 0)

    changed: Dict[str, Any] = {}
    for k in ("on_hand", "reorder_point", "safety_stock"):
        if k in raw:
            v = raw[k]
            if not isinstance(v, int) or v < 0:
                raise HTTPException(
                    status_code=400,
                    detail="{k} must be a non-negative integer".format(k=k),
                )
            changed[k] = v
    if not changed:
        return {"status": "no_changes", "cell": _enrich(existing)}
    changed["last_movement_at"] = _now()

    try:
        coll.update_one({"line_stock_id": line_stock_id}, {"$set": changed})
    except Exception as exc:  # noqa: BLE001
        logger.error("[LENS_STOCK] update failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to update cell")

    updated = coll.find_one({"line_stock_id": line_stock_id}) or {}
    new_on_hand = int(updated.get("on_hand") or 0)
    if "on_hand" in changed:
        _write_audit(
            action="set_on_hand",
            line_stock_id=line_stock_id,
            lens_line_id=str(updated.get("lens_line_id") or ""),
            store_id=str(updated.get("store_id") or ""),
            delta_on_hand=new_on_hand - prior_on_hand,
            delta_reserved=0,
            prior={"on_hand": prior_on_hand, "reserved": prior_reserved},
            after={
                "on_hand": new_on_hand,
                "reserved": int(updated.get("reserved") or 0),
            },
            user=current_user,
            source_type="MANUAL",
            notes=notes,
        )
    return {"status": "success", "cell": _enrich(updated)}


@router.post("/{lens_line_id}/bulk-import")
async def bulk_import(
    lens_line_id: str,
    payload: BulkImportPayload,
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Paste-matrix bulk import (Q3). Accepts a JSON list under `matrix`
    or a CSV string under `csv`. Upserts one cell per row, setting
    on_hand to the supplied qty (absolute -- not additive). Emits one
    audit row per cell with action='bulk_import'.

    Partial failures: the validator pre-checks every row before any DB
    write, so a malformed row 400s the whole import. Individual cell
    writes are best-effort (a single failure logs + continues; the
    response reports per-cell status)."""
    coll = _stock_coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")

    active_store = validate_store_access(payload.store_id, current_user)

    lens_line = _load_lens_line(lens_line_id)
    if not lens_line:
        raise HTTPException(
            status_code=400,
            detail="Lens line {id!r} does not exist".format(id=lens_line_id),
        )

    # Reduce to the cell payloads the validator expects.
    matrix_in: Any
    if payload.matrix is not None:
        matrix_in = [
            {
                "sph": r.sph,
                "cyl": r.cyl,
                "add": r.add,
                "qty": r.qty,
                "store_id": active_store,
            }
            for r in payload.matrix
        ]
    elif payload.csv is not None:
        matrix_in = payload.csv
    else:
        raise HTTPException(
            status_code=400,
            detail="Either `matrix` or `csv` is required",
        )

    try:
        normalized_cells = validate_bulk_import_payload(matrix_in, lens_line=lens_line)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    written: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for cell in normalized_cells:
        cell["store_id"] = active_store  # CSV may have carried a different one
        try:
            existing = coll.find_one(
                {
                    "lens_line_id": lens_line_id,
                    "store_id": active_store,
                    "sph": cell["sph"],
                    "cyl": cell["cyl"],
                    "add": cell["add"],
                }
            )
        except Exception as exc:  # noqa: BLE001
            failed.append({"cell": cell, "error": str(exc)})
            continue
        new_on_hand = int(cell.get("on_hand") or 0)
        if existing:
            prior = {
                "on_hand": int(existing.get("on_hand") or 0),
                "reserved": int(existing.get("reserved") or 0),
            }
            after = {"on_hand": new_on_hand, "reserved": prior["reserved"]}
            try:
                coll.update_one(
                    {"line_stock_id": existing["line_stock_id"]},
                    {
                        "$set": {
                            "on_hand": new_on_hand,
                            "last_movement_at": _now(),
                        }
                    },
                )
            except Exception as exc:  # noqa: BLE001
                failed.append({"cell": cell, "error": str(exc)})
                continue
            _write_audit(
                action="bulk_import",
                line_stock_id=str(existing["line_stock_id"]),
                lens_line_id=lens_line_id,
                store_id=active_store,
                delta_on_hand=new_on_hand - prior["on_hand"],
                delta_reserved=0,
                prior=prior,
                after=after,
                user=current_user,
                source_type="IMPORT",
                source_id=payload.source_id,
            )
            written.append({"line_stock_id": existing["line_stock_id"], "after": after})
        else:
            line_stock_id = uuid.uuid4().hex
            doc = dict(cell)
            doc["lens_line_id"] = lens_line_id
            doc["store_id"] = active_store
            doc["line_stock_id"] = line_stock_id
            doc["last_movement_at"] = _now()
            try:
                coll.insert_one(doc)
            except Exception as exc:  # noqa: BLE001
                failed.append({"cell": cell, "error": str(exc)})
                continue
            _write_audit(
                action="bulk_import",
                line_stock_id=line_stock_id,
                lens_line_id=lens_line_id,
                store_id=active_store,
                delta_on_hand=new_on_hand,
                delta_reserved=0,
                prior={"on_hand": 0, "reserved": 0},
                after={"on_hand": new_on_hand, "reserved": 0},
                user=current_user,
                source_type="IMPORT",
                source_id=payload.source_id,
            )
            written.append(
                {
                    "line_stock_id": line_stock_id,
                    "after": {"on_hand": new_on_hand, "reserved": 0},
                }
            )

    return {
        "status": "success",
        "written": written,
        "failed": failed,
        "total_written": len(written),
        "total_failed": len(failed),
    }


# ----------------------------------------------------------------------------
# Atomic reserve / commit / release (Q4)
# ----------------------------------------------------------------------------


def _cell_filter(
    lens_line_id: str,
    store_id: str,
    sph: float,
    cyl: float,
    add: Optional[float],
) -> Dict[str, Any]:
    """The (lens_line, store, sph, cyl, add) lookup filter. add=None means
    the SV branch -- Mongo matches None to None."""
    return {
        "lens_line_id": lens_line_id,
        "store_id": store_id,
        "sph": sph,
        "cyl": cyl,
        "add": add,
    }


def _atomic_update(
    coll: Any,
    filter_q: Dict[str, Any],
    update: Dict[str, Any],
    extra_predicate: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Wrapper around find_one_and_update so the routers stay DRY.

    `extra_predicate` is merged into the filter -- this is where the
    $expr CAS condition goes. Returns the AFTER document, or None when
    no doc matched (the CAS failed -> caller raises 409)."""
    full_filter = dict(filter_q)
    if extra_predicate:
        full_filter.update(extra_predicate)
    return coll.find_one_and_update(
        full_filter,
        update,
        return_document=_return_document_after(),
    )


@router.post("/{lens_line_id}/reserve")
async def reserve_cell(
    lens_line_id: str,
    payload: ReserveCommitReleasePayload,
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Atomically reserve `qty` units of a cell.

    Succeeds only if (on_hand - reserved) >= qty. Increments .reserved by
    qty (on_hand unchanged). Used by POS Step 6 Confirm -- if the customer
    abandons or the order is voided pre-dispatch, the caller MUST follow
    up with /release."""
    coll = _stock_coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    active_store = validate_store_access(payload.store_id, current_user)

    filt = _cell_filter(
        lens_line_id, active_store, payload.sph, payload.cyl, payload.add
    )
    # CAS: only update when on_hand - reserved >= qty.
    cas = {"$expr": {"$gte": [{"$subtract": ["$on_hand", "$reserved"]}, payload.qty]}}
    update = {
        "$inc": {"reserved": payload.qty},
        "$set": {"last_movement_at": _now()},
    }
    try:
        result = _atomic_update(coll, filt, update, extra_predicate=cas)
    except Exception as exc:  # noqa: BLE001
        logger.error("[LENS_STOCK] reserve failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to reserve stock")
    if result is None:
        # Either the cell doesn't exist, or available < qty. Distinguish
        # for the FE: pull the cell to give a useful error.
        cell = coll.find_one(filt)
        if cell is None:
            raise HTTPException(status_code=404, detail="Stock cell not found")
        avail = compute_available(cell.get("on_hand", 0), cell.get("reserved", 0))
        raise HTTPException(
            status_code=409,
            detail=(
                "Insufficient available stock to reserve {q} unit(s); "
                "available={a}.".format(q=payload.qty, a=avail)
            ),
        )

    prior = {
        "on_hand": int(result.get("on_hand") or 0),
        "reserved": int((result.get("reserved") or 0) - payload.qty),
    }
    after = {
        "on_hand": int(result.get("on_hand") or 0),
        "reserved": int(result.get("reserved") or 0),
    }
    _write_audit(
        action="reserve",
        line_stock_id=str(result.get("line_stock_id") or ""),
        lens_line_id=lens_line_id,
        store_id=active_store,
        delta_on_hand=0,
        delta_reserved=payload.qty,
        prior=prior,
        after=after,
        user=current_user,
        source_type=payload.source_type or "POS",
        source_id=payload.source_id,
        notes=payload.notes,
    )
    return {"status": "success", "cell": _enrich(result)}


@router.post("/{lens_line_id}/commit")
async def commit_cell(
    lens_line_id: str,
    payload: ReserveCommitReleasePayload,
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Atomically commit `qty` units of a cell.

    Succeeds only if on_hand >= qty AND reserved >= qty. Decrements BOTH
    on_hand and reserved by qty (the units leave the bin AND lose their
    reservation in the same atomic write). Used by Workshop dispatch --
    the workshop-side caller is wired in B'4."""
    coll = _stock_coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    active_store = validate_store_access(payload.store_id, current_user)

    filt = _cell_filter(
        lens_line_id, active_store, payload.sph, payload.cyl, payload.add
    )
    cas = {
        "$expr": {
            "$and": [
                {"$gte": ["$on_hand", payload.qty]},
                {"$gte": ["$reserved", payload.qty]},
            ]
        }
    }
    update = {
        "$inc": {"on_hand": -payload.qty, "reserved": -payload.qty},
        "$set": {"last_movement_at": _now()},
    }
    try:
        result = _atomic_update(coll, filt, update, extra_predicate=cas)
    except Exception as exc:  # noqa: BLE001
        logger.error("[LENS_STOCK] commit failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to commit stock")
    if result is None:
        cell = coll.find_one(filt)
        if cell is None:
            raise HTTPException(status_code=404, detail="Stock cell not found")
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot commit {q} unit(s): need on_hand>={q} and "
                "reserved>={q} (have on_hand={oh}, reserved={r}).".format(
                    q=payload.qty,
                    oh=cell.get("on_hand"),
                    r=cell.get("reserved"),
                )
            ),
        )

    prior = {
        "on_hand": int((result.get("on_hand") or 0) + payload.qty),
        "reserved": int((result.get("reserved") or 0) + payload.qty),
    }
    after = {
        "on_hand": int(result.get("on_hand") or 0),
        "reserved": int(result.get("reserved") or 0),
    }
    _write_audit(
        action="commit",
        line_stock_id=str(result.get("line_stock_id") or ""),
        lens_line_id=lens_line_id,
        store_id=active_store,
        delta_on_hand=-payload.qty,
        delta_reserved=-payload.qty,
        prior=prior,
        after=after,
        user=current_user,
        source_type=payload.source_type or "WORKSHOP",
        source_id=payload.source_id,
        notes=payload.notes,
    )
    return {"status": "success", "cell": _enrich(result)}


@router.post("/{lens_line_id}/release")
async def release_cell(
    lens_line_id: str,
    payload: ReserveCommitReleasePayload,
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Atomically release `qty` reserved units back to available.

    Succeeds only if reserved >= qty. Decrements .reserved by qty (on_hand
    unchanged). Used on order-cancel before dispatch -- the workshop hasn't
    consumed the units yet, so they go back to the shared available pool."""
    coll = _stock_coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    active_store = validate_store_access(payload.store_id, current_user)

    filt = _cell_filter(
        lens_line_id, active_store, payload.sph, payload.cyl, payload.add
    )
    cas = {"$expr": {"$gte": ["$reserved", payload.qty]}}
    update = {
        "$inc": {"reserved": -payload.qty},
        "$set": {"last_movement_at": _now()},
    }
    try:
        result = _atomic_update(coll, filt, update, extra_predicate=cas)
    except Exception as exc:  # noqa: BLE001
        logger.error("[LENS_STOCK] release failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to release stock")
    if result is None:
        cell = coll.find_one(filt)
        if cell is None:
            raise HTTPException(status_code=404, detail="Stock cell not found")
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot release {q} unit(s): reserved={r} only.".format(
                    q=payload.qty, r=cell.get("reserved")
                )
            ),
        )

    prior = {
        "on_hand": int(result.get("on_hand") or 0),
        "reserved": int((result.get("reserved") or 0) + payload.qty),
    }
    after = {
        "on_hand": int(result.get("on_hand") or 0),
        "reserved": int(result.get("reserved") or 0),
    }
    _write_audit(
        action="release",
        line_stock_id=str(result.get("line_stock_id") or ""),
        lens_line_id=lens_line_id,
        store_id=active_store,
        delta_on_hand=0,
        delta_reserved=-payload.qty,
        prior=prior,
        after=after,
        user=current_user,
        source_type=payload.source_type or "ORDER_CANCEL",
        source_id=payload.source_id,
        notes=payload.notes,
    )
    return {"status": "success", "cell": _enrich(result)}
