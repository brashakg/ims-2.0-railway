"""
IMS 2.0 - E3 Item-event ledger router (prefix /api/v1/items)
============================================================

Read + write endpoints over the append-only `item_events` ledger
(`api/services/item_events.py`):

  * GET  /items/{stock_id}/events     -- one unit's full history (event_seq order)
  * GET  /items/events                -- filtered store-scoped feed
  * POST /items/{stock_id}/quarantine -- AVAILABLE -> QUARANTINED (ledger event)
  * POST /items/{stock_id}/quarantine/release -- QUARANTINED -> AVAILABLE|DAMAGED|RTV
  * POST /items/{stock_id}/serial-bind -- bind a serial; reconciles serial_numbers
  * POST /items/{stock_id}/sell        -- concurrency-safe SELL (feature-flagged)
  * GET  /items/base-bank              -- resolved Base-Bank targets (E2 hierarchy)
  * POST /items/base-bank              -- upsert a Base-Bank target
  * GET  /items/replenishment          -- [{cell_key, base_bank, in_hand, required}]

Append-only by policy: there is NO PUT/DELETE on a ledger row. Every write goes
through `item_events.record_event` (single-document CAS + one ledger insert +
one AuditRepository.create row for material events) -- never a direct mutation.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import get_current_user, require_roles
from ..dependencies import (
    can_access_store_scoped,
    get_audit_repository,  # noqa: F401  (kept for monkeypatch parity in tests)
    get_stock_repository,
    resolve_store_scope,
    validate_store_access,
)
from ..services import item_events as ie
from ..services import power_grid

logger = logging.getLogger(__name__)
router = APIRouter()

# Inventory-write roles (mirror inventory._INVENTORY_ROLES). SUPERADMIN auto-passes.
_INVENTORY_ROLES = (
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "CATALOG_MANAGER",
    "WORKSHOP_STAFF",
)
# Write-off (DAMAGE / RTV) is a manager-ladder decision.
_STOCK_MANAGER_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER")
# Base-Bank target writers (store-scoped).
_BASE_BANK_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER")

_GRIDS = {"READERS", "CL_POWER", "CL_COLOUR", "PLANOGRAM"}
_RELEASE_DISPOSITIONS = {"RESTOCK", "DAMAGE", "RTV"}


def _get_db():
    """Raw MongoDB database (collections without a dedicated repository)."""
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and conn.is_connected:
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class QuarantineInRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=80)
    notes: Optional[str] = Field(default=None, max_length=200)


class QuarantineReleaseRequest(BaseModel):
    disposition: str = Field(..., description="RESTOCK | DAMAGE | RTV")
    notes: Optional[str] = Field(default=None, max_length=200)


class SerialBindRequest(BaseModel):
    serial: str = Field(..., min_length=1, max_length=120)


class SellRequest(BaseModel):
    order_id: Optional[str] = None


class BaseBankUpsert(BaseModel):
    store_id: str = Field(..., min_length=1)
    grid: str = Field(..., description="READERS | CL_POWER | CL_COLOUR | PLANOGRAM")
    cell_key: str = Field(..., min_length=1)
    base_bank: int = Field(..., ge=0, le=100000)
    product_line_id: Optional[str] = None
    display_size: Optional[int] = Field(default=None, ge=0, le=100000)


def _require_unit(stock_id: str, current_user: dict):
    """Fetch a unit + IDOR-hide it cross-store (same 404 contract as inventory)."""
    stock_repo = get_stock_repository()
    if stock_repo is None:
        raise HTTPException(status_code=503, detail="Stock repository unavailable")
    unit = stock_repo.find_by_id(stock_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="Stock unit not found")
    if not can_access_store_scoped(unit.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="Stock unit not found")
    return stock_repo, unit


# ---------------------------------------------------------------------------
# Ledger reads
# ---------------------------------------------------------------------------


@router.get("/{stock_id}/events")
async def get_unit_events(
    stock_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Full event ledger for one unit, ascending by event_seq. Store-scoped."""
    db = _get_db()
    if db is None:
        return {"items": [], "total": 0}
    _require_unit(stock_id, current_user)
    rows = ie.unit_history(db, stock_id)
    return {"items": rows, "total": len(rows)}


@router.get("/events")
async def list_events(
    store_id: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    current_user: dict = Depends(get_current_user),
):
    """Filtered store-scoped ledger feed (most recent first)."""
    db = _get_db()
    if db is None:
        return {"items": [], "total": 0}
    scoped_store = resolve_store_scope(store_id, current_user)
    match: dict = {}
    if scoped_store:
        match["store_id"] = scoped_store
    if type:
        match["event_type"] = type
    try:
        rows = list(
            db.get_collection("item_events").find(match).sort("event_seq", -1).limit(limit)
        )
    except Exception:  # noqa: BLE001
        return {"items": [], "total": 0}
    for r in rows:
        r.pop("_id", None)
    return {"items": rows, "total": len(rows)}


# ---------------------------------------------------------------------------
# Quarantine (ledger-recorded)
# ---------------------------------------------------------------------------


@router.post("/{stock_id}/quarantine")
async def quarantine_in(
    stock_id: str,
    req: QuarantineInRequest,
    current_user: dict = Depends(require_roles(*_STOCK_MANAGER_ROLES)),
):
    """AVAILABLE -> QUARANTINED, recorded as a ledger event (+ audit row). The
    quarantined unit drops out of every on-hand / sellable rollup immediately."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    _stock_repo, unit = _require_unit(stock_id, current_user)
    frm = ie.canonical_state(unit.get("status"))
    if frm is None:
        raise HTTPException(status_code=409, detail={"code": "unknown_state"})
    ok, ev = ie.record_event_atomic(
        db,
        event_type=ie.ItemEventType.QUARANTINE_IN,
        actor_id=current_user.get("user_id"),
        stock_id=stock_id,
        from_state=frm,
        to_state=ie.StockState.QUARANTINED,
        store_id=unit.get("store_id"),
        product_id=unit.get("product_id"),
        source_type="MANUAL",
        payload={"quarantine_reason": (req.reason or "").strip().upper(),
                 "notes": (req.notes or "")[:200]},
    )
    if not ok:
        raise HTTPException(
            status_code=409,
            detail={"code": "illegal_transition",
                    "message": f"A unit in {frm.value} cannot be quarantined."},
        )
    return {"event": ev, "message": "Stock unit quarantined"}


@router.post("/{stock_id}/quarantine/release")
async def quarantine_release(
    stock_id: str,
    req: QuarantineReleaseRequest,
    current_user: dict = Depends(require_roles(*_STOCK_MANAGER_ROLES)),
):
    """QUARANTINED -> AVAILABLE (RESTOCK) | DAMAGED (DAMAGE) | RTV (RTV).

    DAMAGE / RTV are write-offs (manager ladder). RESTOCK returns the unit to
    sellable on-hand; the other two do NOT."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    _stock_repo, unit = _require_unit(stock_id, current_user)
    disp = (req.disposition or "").strip().upper()
    if disp not in _RELEASE_DISPOSITIONS:
        raise HTTPException(status_code=422,
                            detail=f"disposition must be one of {sorted(_RELEASE_DISPOSITIONS)}")
    target = {
        "RESTOCK": ie.StockState.AVAILABLE,
        "DAMAGE": ie.StockState.DAMAGED,
        "RTV": ie.StockState.RTV,
    }[disp]
    ok, ev = ie.record_event_atomic(
        db,
        event_type=ie.ItemEventType.QUARANTINE_OUT,
        actor_id=current_user.get("user_id"),
        stock_id=stock_id,
        from_state=ie.StockState.QUARANTINED,
        to_state=target,
        store_id=unit.get("store_id"),
        product_id=unit.get("product_id"),
        source_type="MANUAL",
        payload={"disposition": disp, "notes": (req.notes or "")[:200]},
    )
    if not ok:
        raise HTTPException(
            status_code=409,
            detail={"code": "not_quarantined",
                    "message": "Unit is not quarantined (or the disposition is illegal)."},
        )
    return {"event": ev, "message": f"Quarantine released ({disp})"}


# ---------------------------------------------------------------------------
# Serial bind (reconciles with the EXISTING serial_numbers collection)
# ---------------------------------------------------------------------------


@router.post("/{stock_id}/serial-bind")
async def serial_bind(
    stock_id: str,
    req: SerialBindRequest,
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """Bind a serial number to a stock unit.

    Reconciles with the EXISTING `serial_numbers` collection rather than forking
    a second store: sets `stock_units.serial` AND upserts the canonical
    `serial_numbers` row (so the legacy /inventory/serials tracker UI keeps
    working). 409 on a duplicate serial within the store. Records a serial.bind
    ledger event (no status change)."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    stock_repo, unit = _require_unit(stock_id, current_user)
    serial = (req.serial or "").strip().upper()
    if not serial:
        raise HTTPException(status_code=422, detail="serial is required")

    store_id = unit.get("store_id")
    serials_coll = db.get_collection("serial_numbers")
    # Duplicate-serial guard within the store (the serial is the physical key).
    dup = serials_coll.find_one({"serial_number": serial, "store_id": store_id})
    if dup and dup.get("stock_id") not in (None, stock_id):
        raise HTTPException(status_code=409,
                            detail={"code": "duplicate_serial",
                                    "message": "Serial already bound in this store."})

    # 1. Project onto stock_units (single-doc write) + record the ledger event.
    ev = ie.record_event(
        db,
        event_type=ie.ItemEventType.SERIAL_BIND,
        actor_id=current_user.get("user_id"),
        stock_id=stock_id,
        store_id=store_id,
        product_id=unit.get("product_id"),
        serial=serial,
        source_type="MANUAL",
        payload={"serial": serial},
    )
    # record_event with no to_state skips the CAS; set serial directly so the
    # projection always lands even when the ledger insert is fail-soft.
    stock_repo.update(stock_id, {"serial": serial})

    # 2. Bridge the EXISTING serial_numbers collection (single-doc upsert).
    now_iso = datetime.now(timezone.utc).isoformat()
    if dup:
        serials_coll.update_one(
            {"serial_id": dup.get("serial_id")},
            {"$set": {"stock_id": stock_id, "product_id": unit.get("product_id"),
                      "updated_at": now_iso}},
        )
        serial_id = dup.get("serial_id")
    else:
        serial_id = str(uuid.uuid4())
        serials_coll.insert_one(
            {
                "serial_id": serial_id,
                "serial_number": serial,
                "product_id": unit.get("product_id"),
                "store_id": store_id,
                "status": "IN_STOCK",
                "stock_id": stock_id,
                "created_at": now_iso,
                "created_by": current_user.get("user_id", ""),
                "updated_at": now_iso,
            }
        )
    return {"event": ev, "serial": serial, "serial_id": serial_id,
            "message": "Serial bound"}


# ---------------------------------------------------------------------------
# Concurrency-safe SELL (feature-flagged OFF by default -- POS path)
# ---------------------------------------------------------------------------


def _pos_sell_enabled(store_id: Optional[str]) -> bool:
    """E2 feature flag FF_E3_POS_SELL (off by default). POS/money safety: the
    SELL event stays dark until the orchestrator flips the flag per store."""
    try:
        from ..services.policy_engine import get_policy

        scope = {"store_id": store_id} if store_id else None
        return bool(get_policy("FF_E3_POS_SELL", scope, default=False))
    except Exception:  # noqa: BLE001
        return False


@router.post("/{stock_id}/sell")
async def sell_unit(
    stock_id: str,
    req: SellRequest,
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES, "SALES_CASHIER", "SALES_STAFF")),
):
    """AVAILABLE -> SOLD via the concurrency-safe CAS. Behind FF_E3_POS_SELL
    (off by default). Two racing sells: exactly one wins (ok), the loser 409s."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    _stock_repo, unit = _require_unit(stock_id, current_user)
    if not _pos_sell_enabled(unit.get("store_id")):
        raise HTTPException(status_code=403,
                            detail={"code": "feature_disabled",
                                    "message": "E3 POS sell flag is off."})
    ok, ev = ie.record_event_atomic(
        db,
        event_type=ie.ItemEventType.SELL,
        actor_id=current_user.get("user_id"),
        stock_id=stock_id,
        from_state=ie.StockState.AVAILABLE,
        to_state=ie.StockState.SOLD,
        store_id=unit.get("store_id"),
        product_id=unit.get("product_id"),
        source_type="POS",
        source_id=req.order_id,
    )
    if not ok:
        raise HTTPException(status_code=409,
                            detail={"code": "already_sold",
                                    "message": "Unit is no longer available."})
    return {"event": ev, "message": "Unit sold"}


# ---------------------------------------------------------------------------
# Base-Bank targets + replenishment
# ---------------------------------------------------------------------------


def _resolve_target(db, store_id: str, grid: str, cell_key: str) -> Optional[dict]:
    """Resolve a Base-Bank target via the E2 STORE > ENTITY > GLOBAL hierarchy.

    A store missing entity_id drops silently to GLOBAL (E2 entity fail-safe)."""
    coll = db.get_collection("base_bank_targets")
    entity_id = None
    try:
        store = db.get_collection("stores").find_one({"store_id": store_id})
        if store:
            entity_id = store.get("entity_id")
    except Exception:  # noqa: BLE001
        entity_id = None
    # STORE first, then ENTITY (if known), then GLOBAL.
    candidates = [{"scope": "STORE", "store_id": store_id, "grid": grid, "cell_key": cell_key}]
    if entity_id:
        candidates.append({"scope": "ENTITY", "entity_id": entity_id, "grid": grid, "cell_key": cell_key})
    candidates.append({"scope": "GLOBAL", "grid": grid, "cell_key": cell_key})
    for q in candidates:
        try:
            row = coll.find_one(q)
        except Exception:  # noqa: BLE001
            row = None
        if row:
            row.pop("_id", None)
            return row
    return None


@router.post("/base-bank")
async def upsert_base_bank(
    req: BaseBankUpsert,
    current_user: dict = Depends(require_roles(*_BASE_BANK_ROLES)),
):
    """Upsert a STORE-scoped Base-Bank target. Store-scoped: a store role may
    only write its own store's targets."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    store_id = validate_store_access(req.store_id, current_user)
    grid = (req.grid or "").strip().upper()
    if grid not in _GRIDS:
        raise HTTPException(status_code=422, detail=f"grid must be one of {sorted(_GRIDS)}")
    # Normalise power cell keys via the canonical grid formatter (so "+2.00" and
    # "2" snap to the same cell). Colour / planogram slot keys pass through.
    cell_key = req.cell_key.strip()
    if grid in ("READERS", "CL_POWER"):
        snapped = power_grid.format_power(cell_key)
        if snapped is not None:
            cell_key = snapped
    coll = db.get_collection("base_bank_targets")
    now = datetime.now(timezone.utc)
    key = {"scope": "STORE", "store_id": store_id, "grid": grid, "cell_key": cell_key}
    existing = coll.find_one(key)
    set_block = {
        **key,
        "entity_id": None,
        "product_line_id": req.product_line_id,
        "base_bank": int(req.base_bank),
        "display_size": req.display_size,
        "updated_by": current_user.get("user_id"),
        "updated_at": now,
    }
    if existing:
        coll.update_one({"target_id": existing.get("target_id")}, {"$set": set_block})
        target_id = existing.get("target_id")
    else:
        target_id = str(uuid.uuid4())
        coll.insert_one({"target_id": target_id, **set_block})
    return {"target_id": target_id, **set_block, "updated_at": now.isoformat()}


@router.get("/base-bank")
async def get_base_bank(
    store_id: Optional[str] = Query(None),
    grid: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """List Base-Bank targets for a store + grid (STORE rows; resolution applied
    at replenishment time)."""
    db = _get_db()
    if db is None:
        return {"items": [], "total": 0}
    scoped_store = resolve_store_scope(store_id, current_user)
    g = (grid or "").strip().upper()
    match: dict = {"grid": g}
    if scoped_store:
        match["$or"] = [{"scope": "STORE", "store_id": scoped_store}, {"scope": "GLOBAL"}]
    try:
        rows = list(db.get_collection("base_bank_targets").find(match))
    except Exception:  # noqa: BLE001
        return {"items": [], "total": 0}
    for r in rows:
        r.pop("_id", None)
    return {"items": rows, "total": len(rows)}


@router.get("/replenishment")
async def replenishment(
    store_id: Optional[str] = Query(None),
    grid: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """`[{cell_key, base_bank, in_hand, required}]` for a store + grid.

    For each STORE/GLOBAL target cell, resolve the effective base_bank via the
    E2 hierarchy and count sellable on-hand units at the cell's power. Powers are
    normalised with `format_power` so "+2.00" / "2" snap to one cell."""
    db = _get_db()
    if db is None:
        return {"items": [], "total": 0}
    resolved_store = resolve_store_scope(store_id, current_user)
    if not resolved_store:
        raise HTTPException(status_code=422, detail="store_id is required for replenishment")
    g = (grid or "").strip().upper()
    if g not in _GRIDS:
        raise HTTPException(status_code=422, detail=f"grid must be one of {sorted(_GRIDS)}")

    coll = db.get_collection("base_bank_targets")
    # All cell_keys that have a target at STORE or GLOBAL scope for this grid.
    cell_keys = set()
    try:
        for row in coll.find({"grid": g,
                              "$or": [{"scope": "STORE", "store_id": resolved_store},
                                      {"scope": "GLOBAL"}]}):
            if row.get("cell_key"):
                cell_keys.add(row["cell_key"])
    except Exception:  # noqa: BLE001
        cell_keys = set()

    slots = []
    for cell_key in sorted(cell_keys):
        target = _resolve_target(db, resolved_store, g, cell_key)
        base_bank = int((target or {}).get("base_bank", 0) or 0)
        in_hand = _count_on_hand_at_cell(db, resolved_store, g, cell_key,
                                         (target or {}).get("product_line_id"))
        slots.append({"cell_key": cell_key, "base_bank": base_bank, "in_hand": in_hand})
    return {"items": ie.build_replenishment(slots), "total": len(slots)}


def _count_on_hand_at_cell(db, store_id, grid, cell_key, product_line_id) -> int:
    """Count sellable on-hand stock_units at one Base-Bank cell.

    For power grids the cell_key is a normalised dioptre string; we match a
    unit's `power` (or `sph`) snapped through `format_power`. EXCLUDED_STATUSES
    (quarantine/under-audit/etc.) are never counted. Fail-soft 0."""
    try:
        match: dict = {
            "store_id": store_id,
            "status": {"$nin": ie.EXCLUDED_STATUSES},
        }
        if product_line_id:
            match["product_id"] = product_line_id
        n = 0
        for unit in db.get_collection("stock_units").find(match):
            if grid in ("READERS", "CL_POWER"):
                raw = unit.get("power", unit.get("sph"))
                if power_grid.format_power(raw) != cell_key:
                    continue
            elif grid == "CL_COLOUR":
                if str(unit.get("colour", unit.get("color", ""))).strip().upper() != cell_key.upper():
                    continue
            elif grid == "PLANOGRAM":
                if str(unit.get("location_code", "")).strip().upper() != cell_key.upper():
                    continue
            n += int(unit.get("quantity", 1) or 1)
        return n
    except Exception:  # noqa: BLE001
        return 0
