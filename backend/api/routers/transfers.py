"""
IMS 2.0 - Stock Transfers Router
================================
Complete stock transfer management between locations, stores, and warehouses.
Includes transfer requests, approvals, in-transit tracking, and receiving.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum
import logging
import uuid

from .auth import get_current_user
from ..dependencies import get_stock_repository

logger = logging.getLogger(__name__)

router = APIRouter()

# Status a source unit is parked in once it leaves a store on a transfer. It is
# deliberately NOT one of the on-hand statuses (AVAILABLE / IN_STOCK), so the
# moment a transfer ships, the source store's on-hand for that product drops.
# Mirrors the TRANSFERRED status already recognised as non-reactivatable in
# returns.py (a transferred unit must never be resurrected by a return).
STOCK_STATUS_TRANSFERRED = "TRANSFERRED"
STOCK_STATUS_AVAILABLE = "AVAILABLE"
# A defective unit pulled off the floor (F21). It is never AVAILABLE, so the
# ship-move's AVAILABLE allowlist already excludes it -- but if a caller pins an
# explicit stock_id on a transfer line, we reject it LOUDLY (400) rather than
# silently shipping a quarantined unit. A quarantined unit must never move
# between stores.
STOCK_STATUS_QUARANTINED = "QUARANTINED"


# ============================================================================
# ENUMS
# ============================================================================


class TransferStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    PICKING = "picking"
    PACKED = "packed"
    IN_TRANSIT = "in_transit"
    PARTIALLY_RECEIVED = "partially_received"
    RECEIVED = "received"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TransferType(str, Enum):
    STORE_TO_STORE = "store_to_store"
    WAREHOUSE_TO_STORE = "warehouse_to_store"
    STORE_TO_WAREHOUSE = "store_to_warehouse"
    WAREHOUSE_TO_WAREHOUSE = "warehouse_to_warehouse"
    RETURN_TO_VENDOR = "return_to_vendor"
    SHOPIFY_FULFILLMENT = "shopify_fulfillment"


class TransferPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


# ============================================================================
# SCHEMAS
# ============================================================================


class TransferItemInput(BaseModel):
    product_id: str
    sku: str
    product_name: str
    # BUG FIX: quantity_requested must be >= 1 (0 or negative lines create phantom
    # transfer entries that corrupt the ship/receive unit-move math).
    quantity_requested: int = Field(..., ge=1)
    # BUG FIX: unit_cost must be non-negative (negative cost inverts total_value sign).
    unit_cost: Optional[float] = Field(default=None, ge=0)
    notes: Optional[str] = None


class TransferItemReceive(BaseModel):
    transfer_item_id: str
    # BUG FIX: receive quantities must be non-negative; negative would
    # attempt to un-receive units and flip committed counts backward.
    quantity_received: int = Field(..., ge=0)
    # BUG FIX: damaged count must be non-negative and cannot exceed received.
    # The ge=0 floor is enforced here; the damaged<=received invariant is
    # enforced in receive_transfer() at the endpoint level.
    quantity_damaged: int = Field(default=0, ge=0)
    damage_notes: Optional[str] = None


class TransferInput(BaseModel):
    transfer_type: TransferType
    from_location_id: str
    from_location_name: str
    to_location_id: str
    to_location_name: str
    items: List[TransferItemInput]
    priority: TransferPriority = TransferPriority.NORMAL
    expected_date: Optional[str] = None
    notes: Optional[str] = None
    shipping_method: Optional[str] = None
    # BUG FIX: shipping_cost must be non-negative.
    shipping_cost: Optional[float] = Field(default=None, ge=0)
    # Shiprocket integration
    create_shiprocket_shipment: bool = False
    shiprocket_courier: Optional[str] = None


class TransferUpdate(BaseModel):
    priority: Optional[TransferPriority] = None
    expected_date: Optional[str] = None
    notes: Optional[str] = None
    shipping_method: Optional[str] = None
    # BUG FIX: shipping_cost must be non-negative.
    shipping_cost: Optional[float] = Field(default=None, ge=0)
    tracking_number: Optional[str] = None
    tracking_url: Optional[str] = None


class TransferApproval(BaseModel):
    approved: bool
    rejection_reason: Optional[str] = None


# ============================================================================
# PERSISTENCE  (MongoDB `stock_transfers`, with in-memory fallback)
# ============================================================================
# Stock transfers used to live in a module-level dict, so they vanished on
# every redeploy and were invisible across Railway workers — even though the
# transfer feature is live in the UI. They now persist to the
# `stock_transfers` collection. The in-memory dict is kept only as a
# fail-soft fallback when the DB is unavailable (local dev / tests), which
# preserves the previous behavior there.

STOCK_TRANSFERS: Dict[str, Dict] = {}
TRANSFER_COUNTER = {"count": 1000}


def _get_db():
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and conn.is_connected:
            return conn.db
    except Exception:
        pass
    return None


def _transfers_coll():
    db = _get_db()
    return db.get_collection("stock_transfers") if db is not None else None


def _coerce(value):
    """Recursively convert Enum members to their string values so the doc is
    cleanly BSON-serialisable (statuses/types/priorities are str-Enums)."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _coerce(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_coerce(v) for v in value]
    return value


def _append_status_history(transfer: Dict, entry: Dict) -> None:
    """Append a status-history entry, tolerating a missing/null field.

    BUG FIX: transfer docs loaded from Mongo (e.g. migrated from the old
    in-memory dict or inserted via a back-fill script) may not carry the
    `status_history` list. A bare list.append() on None raises AttributeError
    which 500s the endpoint. Use setdefault so the list is always present.
    """
    history = transfer.setdefault("status_history", [])
    if not isinstance(history, list):
        transfer["status_history"] = []
    transfer["status_history"].append(entry)


def _save_transfer(transfer: Dict) -> None:
    """Upsert a transfer by its `id`. Falls back to the in-memory dict when
    the DB is unavailable."""
    doc = _coerce(transfer)
    coll = _transfers_coll()
    if coll is not None:
        coll.update_one({"id": doc["id"]}, {"$set": doc}, upsert=True)
    else:
        STOCK_TRANSFERS[doc["id"]] = doc


def _get_transfer(transfer_id: str) -> Optional[Dict]:
    coll = _transfers_coll()
    if coll is not None:
        return coll.find_one({"id": transfer_id}, {"_id": 0})
    return STOCK_TRANSFERS.get(transfer_id)


def _all_transfers() -> List[Dict]:
    coll = _transfers_coll()
    if coll is not None:
        return list(coll.find({}, {"_id": 0}))
    return list(STOCK_TRANSFERS.values())


def generate_transfer_number() -> str:
    """Generate a unique transfer number — DB-count-based when persistent,
    else the in-memory counter."""
    coll = _transfers_coll()
    if coll is not None:
        seq = coll.count_documents({}) + 1001
        return f"TRF-{datetime.now().strftime('%Y%m')}-{seq}"
    TRANSFER_COUNTER["count"] += 1
    return f"TRF-{datetime.now().strftime('%Y%m')}-{TRANSFER_COUNTER['count']}"


# ============================================================================
# REAL STOCK MOVEMENT  (SYSTEM_INTENT 5)
# ============================================================================
# The transfer lifecycle (ship / receive) must actually MOVE serialized
# `stock_units`, not just flip a status string on the `stock_transfers` doc.
# Otherwise a "completed" transfer leaves BOTH stores' on-hand wrong.
#
#   SHIP    -> reduce on-hand at the SOURCE store: take that many AVAILABLE
#              source units and mark them TRANSFERRED (records transfer_id so
#              they can be matched on receive + so re-ship is a no-op).
#   RECEIVE -> raise on-hand at the DESTINATION store by RE-HOMING the same
#              shipped units: flip them TRANSFERRED -> AVAILABLE and set their
#              store to the destination, keeping each unit's ORIGINAL barcode
#              for life. A transfer is not a purchase, so no new barcode is
#              minted (standard serialized-stock POS behavior) and no phantom
#              stock is ever created - the unit simply changes location.
#
# Idempotency: a doc-level `stock_shipped` flag guards SHIP; each line tracks
# `received_qty_committed` (units already re-homed to the destination) so a re-/
# partial receive only ever moves the DELTA. Fail-soft: no stock repo (DB
# down / tests without Mongo) -> the lifecycle still advances the transfer doc
# exactly as before, just without moving units.


def _line_ship_qty(line: Dict) -> int:
    """How many units a transfer line should move on SHIP.

    Prefer an explicit picked/shipped quantity when the picking flow set one;
    otherwise fall back to the originally requested quantity. Coerced to a
    whole, non-negative int (serialized stock is one row per unit). Pure.
    """
    for key in ("quantity_shipped", "quantity_requested", "quantity"):
        raw = line.get(key)
        if raw in (None, 0):
            continue
        try:
            n = int(float(raw))
        except (TypeError, ValueError):
            continue
        if n > 0:
            return n
    # quantity_shipped may legitimately be 0 if nothing was picked; respect it
    # only when it was explicitly set, else default to requested.
    try:
        return max(0, int(float(line.get("quantity_requested", 0) or 0)))
    except (TypeError, ValueError):
        return 0


def _audit_stock_move(prior_status, new_status, stock_id, transfer, extra=None):
    """Write a best-effort stock_audit row for a transfer-driven status change.

    Mirrors the per-unit audit trail the returns restock flow emits. Fail-soft:
    no DB / any error -> skipped silently (audit is a side-channel, never the
    reason a transfer fails)."""
    db = _get_db()
    if db is None:
        return
    try:
        row = {
            "stock_id": stock_id,
            "prior_status": prior_status,
            "new_status": new_status,
            "source": "STOCK_TRANSFER",
            "transfer_id": transfer.get("id"),
            "transfer_number": transfer.get("transfer_number"),
            "from_store_id": transfer.get("from_location_id"),
            "to_store_id": transfer.get("to_location_id"),
            "at": datetime.now().isoformat(),
        }
        if extra:
            row.update(extra)
        db.get_collection("stock_audit").insert_one(row)
    except Exception as exc:  # noqa: BLE001 - audit is fail-soft
        logger.warning("[TRANSFER] stock audit skipped: %s", exc)


def _apply_ship_stock_move(transfer: Dict) -> Dict:
    """Move source on-hand OUT when a transfer ships.

    For each line, claim up to `_line_ship_qty` AVAILABLE units of that product
    at the SOURCE store and flip them to TRANSFERRED, tagging each with the
    transfer id. The moved unit ids are recorded on the line (`shipped_stock_ids`)
    and the line's `quantity_shipped` is set to what was actually moved.

    Idempotent: if the transfer is already flagged `stock_shipped`, this is a
    no-op (returns the transfer untouched) so a double ship/POST cannot
    double-decrement. Fail-soft: no stock repo -> transfer returned unchanged.
    """
    if transfer.get("stock_shipped"):
        return transfer

    stock_repo = get_stock_repository()
    if stock_repo is None:
        # DB down: advance the lifecycle without moving units (pre-fix behavior).
        return transfer

    from_store = transfer.get("from_location_id")
    moved_total = 0
    for line in transfer.get("items", []):
        product_id = line.get("product_id")
        want = _line_ship_qty(line)
        if not product_id or want <= 0 or not from_store:
            line.setdefault("shipped_stock_ids", [])
            continue

        # F21: if the caller pins explicit stock_ids on this line, reject LOUDLY
        # (400) any that are QUARANTINED -- a defective unit must never move
        # between stores. (When no explicit ids are given, the AVAILABLE
        # allowlist below already excludes quarantined units from being claimed.)
        explicit_ids = line.get("stock_ids") or []
        if explicit_ids:
            for sid in explicit_ids:
                try:
                    pinned = stock_repo.find_by_id(sid)
                except Exception:  # noqa: BLE001
                    pinned = None
                if pinned and (pinned.get("status") or "").strip().upper() == STOCK_STATUS_QUARANTINED:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "code": "quarantined_unit",
                            "message": (
                                f"Stock unit {sid} is QUARANTINED and cannot be transferred."
                            ),
                        },
                    )

        # Claim AVAILABLE source units for this product (one row per unit).
        # status=AVAILABLE is an explicit allowlist, so a QUARANTINED unit is
        # never a candidate here -- F21 safety is structural, not incidental.
        try:
            candidates = stock_repo.find_many(
                {
                    "product_id": product_id,
                    "store_id": from_store,
                    "status": STOCK_STATUS_AVAILABLE,
                },
                limit=want,
            )
        except TypeError:
            # Some repo/mocks don't accept limit= -> fall back + slice.
            candidates = stock_repo.find_many(
                {
                    "product_id": product_id,
                    "store_id": from_store,
                    "status": STOCK_STATUS_AVAILABLE,
                }
            )[:want]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[TRANSFER] ship lookup failed: %s", exc)
            candidates = []

        moved_ids: List[str] = []
        for unit in candidates[:want]:
            sid = unit.get("stock_id") or unit.get("stock_unit_id") or unit.get("_id")
            if not sid:
                continue
            ok = stock_repo.update(
                sid,
                {
                    "status": STOCK_STATUS_TRANSFERRED,
                    "transfer_id": transfer.get("id"),
                    "transferred_at": datetime.now().isoformat(),
                    "transfer_to_store_id": transfer.get("to_location_id"),
                },
            )
            if ok:
                moved_ids.append(str(sid))
                _audit_stock_move(
                    STOCK_STATUS_AVAILABLE,
                    STOCK_STATUS_TRANSFERRED,
                    str(sid),
                    transfer,
                    {"product_id": product_id},
                )

        line["shipped_stock_ids"] = moved_ids
        # Reflect what actually left the floor (may be < requested if the source
        # didn't hold enough AVAILABLE units - we never move phantom stock).
        line["quantity_shipped"] = len(moved_ids)
        moved_total += len(moved_ids)

    transfer["stock_shipped"] = True
    transfer["stock_units_moved_out"] = moved_total
    return transfer


def _transferred_pool(stock_repo, transfer, product_id, prefer):
    """Ordered pool of unit ids shipped under this transfer for a product.

    Starts from the ids SHIP recorded on the line (`prefer`); only when that is
    empty does it fall back to querying the units still marked TRANSFERRED for
    this transfer+product (covers legacy docs whose `shipped_stock_ids` wasn't
    recorded). Fail-soft to `prefer` on any repo error. Pure read."""
    pool = [str(s) for s in (prefer or []) if s]
    if pool:
        return pool
    try:
        rows = stock_repo.find_many(
            {
                "transfer_id": transfer.get("id"),
                "product_id": product_id,
                "status": STOCK_STATUS_TRANSFERRED,
            }
        )
    except Exception as exc:  # noqa: BLE001 - fail-soft
        logger.warning("[TRANSFER] receive pool lookup failed: %s", exc)
        return pool
    for unit in rows or []:
        sid = unit.get("stock_id") or unit.get("stock_unit_id") or unit.get("_id")
        if sid:
            pool.append(str(sid))
    return pool


def _apply_receive_stock_move(transfer: Dict) -> Dict:
    """Raise destination on-hand by RE-HOMING the shipped units.

    A transfer never creates stock. For each line, the SAME physical units that
    SHIP marked TRANSFERRED (recorded per line in `shipped_stock_ids`) are
    flipped back to AVAILABLE and re-homed to the destination store, keeping
    their ORIGINAL barcode for life (a transfer is not a purchase -> no new
    barcode is minted). The destination's on-hand rises by exactly the number of
    units that physically arrived.

    Per line we re-home at most `quantity_received` units, bounded by the pool of
    units actually shipped (so a receive can never exceed what left the source).
    `received_qty_committed` tracks how many of the line's shipped units have
    already been re-homed, so a repeated or partial receive only ever moves the
    DELTA - never double-counts and never fabricates stock the source never sent.

    Fail-soft: no stock repo -> transfer returned unchanged (lifecycle still
    advances, as before).
    """
    stock_repo = get_stock_repository()
    if stock_repo is None:
        return transfer

    to_store = transfer.get("to_location_id")
    if not to_store:
        return transfer

    moved_total = 0
    for line in transfer.get("items", []):
        product_id = line.get("product_id")
        try:
            received = int(float(line.get("quantity_received", 0) or 0))
        except (TypeError, ValueError):
            received = 0
        already = int(line.get("received_qty_committed", 0) or 0)
        want = received - already
        if not product_id or want <= 0:
            continue

        # The units to re-home are exactly those SHIP marked TRANSFERRED for this
        # transfer (stable, ordered pool); never mint new ones.
        pool = _transferred_pool(
            stock_repo, transfer, product_id, line.get("shipped_stock_ids")
        )
        movable = pool[already : already + want]

        received_ids: List[str] = list(line.get("received_stock_ids", []))
        moved_here = 0
        for sid in movable:
            ok = stock_repo.update(
                sid,
                {
                    "status": STOCK_STATUS_AVAILABLE,
                    "store_id": to_store,
                    "received_at": datetime.now().isoformat(),
                    "source_type": "TRANSFER",
                    "source_id": transfer.get("id"),
                    "transfer_number": transfer.get("transfer_number"),
                    "from_store_id": transfer.get("from_location_id"),
                    # No longer held against the (now-completed) transfer.
                    "transfer_id": None,
                    "transfer_to_store_id": None,
                },
            )
            if ok:
                received_ids.append(str(sid))
                moved_here += 1
                moved_total += 1
                _audit_stock_move(
                    STOCK_STATUS_TRANSFERRED,
                    STOCK_STATUS_AVAILABLE,
                    str(sid),
                    transfer,
                    {"product_id": product_id, "moved_to": to_store},
                )

        line["received_stock_ids"] = received_ids
        line["received_qty_committed"] = already + moved_here

    transfer["stock_units_moved_in"] = (
        int(transfer.get("stock_units_moved_in", 0) or 0) + moved_total
    )
    return transfer


# ============================================================================
# TRANSFER ENDPOINTS
# ============================================================================


@router.get("")
@router.get("/")
async def list_transfers(
    status: Optional[TransferStatus] = None,
    transfer_type: Optional[TransferType] = None,
    from_location_id: Optional[str] = None,
    to_location_id: Optional[str] = None,
    store_id: Optional[str] = Query(
        None,
        description=(
            "Convenience filter — returns transfers where store_id is "
            "EITHER the source OR the destination. Used by the topbar "
            "store-switcher; mutually exclusive with explicit from/to "
            "location filters (those take precedence when both are set)."
        ),
    ),
    priority: Optional[TransferPriority] = None,
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
    limit: int = Query(default=50, le=250),
    page: int = 1,
    current_user: dict = Depends(get_current_user),
):
    """List all stock transfers with filtering"""
    transfers = _all_transfers()

    # Apply filters
    if status:
        transfers = [t for t in transfers if t.get("status") == status]
    if transfer_type:
        transfers = [t for t in transfers if t.get("transfer_type") == transfer_type]
    if from_location_id:
        transfers = [
            t for t in transfers if t.get("from_location_id") == from_location_id
        ]
    if to_location_id:
        transfers = [t for t in transfers if t.get("to_location_id") == to_location_id]
    # `store_id` convenience: include transfers where the store is on
    # either side. Explicit from/to filters already applied above; this
    # narrows further only if neither was set.
    if store_id and not (from_location_id or to_location_id):
        transfers = [
            t
            for t in transfers
            if t.get("from_location_id") == store_id
            or t.get("to_location_id") == store_id
        ]
    if priority:
        transfers = [t for t in transfers if t.get("priority") == priority]

    # Filter by store access for non-superadmin users
    user_roles = current_user.get("roles", [])
    if not any(role in user_roles for role in ["SUPERADMIN", "ADMIN", "AREA_MANAGER"]):
        user_stores = current_user.get("store_ids", [])
        transfers = [
            t
            for t in transfers
            if t.get("from_location_id") in user_stores
            or t.get("to_location_id") in user_stores
        ]

    # Sort by created date (newest first)
    transfers.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    total = len(transfers)
    start = (page - 1) * limit
    end = start + limit

    return {
        "transfers": transfers[start:end],
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": (total + limit - 1) // limit,
    }


@router.get("/pending")
async def get_pending_transfers(
    location_id: Optional[str] = None, current_user: dict = Depends(get_current_user)
):
    """Get transfers pending approval or action"""
    transfers = _all_transfers()

    pending_statuses = [
        TransferStatus.PENDING_APPROVAL,
        TransferStatus.APPROVED,
        TransferStatus.IN_TRANSIT,
        TransferStatus.PARTIALLY_RECEIVED,
    ]

    transfers = [t for t in transfers if t.get("status") in pending_statuses]

    if location_id:
        transfers = [
            t
            for t in transfers
            if t.get("from_location_id") == location_id
            or t.get("to_location_id") == location_id
        ]

    return {
        "pending_approval": [
            t for t in transfers if t.get("status") == TransferStatus.PENDING_APPROVAL
        ],
        "ready_to_ship": [
            t for t in transfers if t.get("status") == TransferStatus.APPROVED
        ],
        "in_transit": [
            t for t in transfers if t.get("status") == TransferStatus.IN_TRANSIT
        ],
        "pending_receipt": [
            t for t in transfers if t.get("status") == TransferStatus.PARTIALLY_RECEIVED
        ],
    }


@router.get("/{transfer_id}")
async def get_transfer(
    transfer_id: str, current_user: dict = Depends(get_current_user)
):
    """Get a single transfer with full details"""
    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")

    return {"transfer": transfer}


@router.post("")
@router.post("/")
async def create_transfer(
    transfer: TransferInput, current_user: dict = Depends(get_current_user)
):
    """Create a new stock transfer request"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # BUG FIX: a self-transfer (source == destination) would mark source units
    # TRANSFERRED then re-home them back to the same store on receive — the
    # on-hand count looks correct after completion but the TRANSFERRED phase
    # temporarily drops the source on-hand to zero, confusing POS / alerts,
    # and writes a spurious audit trail of units leaving/arriving a single store.
    if transfer.from_location_id == transfer.to_location_id:
        raise HTTPException(
            status_code=400,
            detail="Source and destination store must be different",
        )

    # BUG FIX: at least one item must be in the transfer.
    if not transfer.items:
        raise HTTPException(
            status_code=400,
            detail="Transfer must contain at least one item",
        )

    transfer_id = f"trf_{uuid.uuid4().hex[:12]}"
    transfer_number = generate_transfer_number()

    # Calculate totals
    total_items = sum(item.quantity_requested for item in transfer.items)
    total_value = sum(
        (item.unit_cost or 0) * item.quantity_requested for item in transfer.items
    )

    # Process items
    items = []
    for item in transfer.items:
        item_id = f"trfi_{uuid.uuid4().hex[:8]}"
        items.append(
            {
                "id": item_id,
                "transfer_id": transfer_id,
                **item.model_dump(),
                "quantity_shipped": 0,
                "quantity_received": 0,
                "quantity_damaged": 0,
                "status": "pending",
            }
        )

    # Determine initial status based on user role
    user_roles = current_user.get("roles", [])
    if any(role in user_roles for role in ["SUPERADMIN", "ADMIN"]):
        initial_status = TransferStatus.APPROVED
    else:
        initial_status = TransferStatus.PENDING_APPROVAL

    transfer_data = {
        "id": transfer_id,
        "transfer_number": transfer_number,
        "transfer_type": transfer.transfer_type,
        "from_location_id": transfer.from_location_id,
        "from_location_name": transfer.from_location_name,
        "to_location_id": transfer.to_location_id,
        "to_location_name": transfer.to_location_name,
        "items": items,
        "total_items": total_items,
        "total_value": total_value,
        "priority": transfer.priority,
        "expected_date": transfer.expected_date,
        "notes": transfer.notes,
        "shipping_method": transfer.shipping_method,
        "shipping_cost": transfer.shipping_cost,
        "tracking_number": None,
        "tracking_url": None,
        "shiprocket_order_id": None,
        "shiprocket_shipment_id": None,
        "status": initial_status,
        "status_history": [
            {
                "status": initial_status,
                "timestamp": datetime.now().isoformat(),
                "user_id": current_user.get("user_id"),
                "user_name": current_user.get("username"),
                "notes": "Transfer created",
            }
        ],
        "created_by": current_user.get("user_id"),
        "created_by_name": current_user.get("username"),
        "approved_by": (
            current_user.get("user_id")
            if initial_status == TransferStatus.APPROVED
            else None
        ),
        "approved_at": (
            datetime.now().isoformat()
            if initial_status == TransferStatus.APPROVED
            else None
        ),
        "shipped_at": None,
        "received_at": None,
        "completed_at": None,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }

    # Create Shiprocket shipment if requested
    if transfer.create_shiprocket_shipment:
        # In production, would call Shiprocket API
        transfer_data["shiprocket_order_id"] = f"SR_{uuid.uuid4().hex[:8].upper()}"

    _save_transfer(transfer_data)

    return {
        "transfer": transfer_data,
        "message": f"Transfer {transfer_number} created successfully",
    }


@router.put("/{transfer_id}")
async def update_transfer(
    transfer_id: str,
    update: TransferUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Update transfer details"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")

    # Can only update certain statuses
    if transfer["status"] in [TransferStatus.COMPLETED, TransferStatus.CANCELLED]:
        raise HTTPException(
            status_code=400, detail="Cannot update completed or cancelled transfer"
        )

    update_data = update.model_dump(exclude_none=True)
    transfer.update(update_data)
    transfer["updated_at"] = datetime.now().isoformat()

    _save_transfer(transfer)
    return {"transfer": transfer, "message": "Transfer updated successfully"}


@router.post("/{transfer_id}/approve")
async def approve_transfer(
    transfer_id: str,
    approval: TransferApproval,
    current_user: dict = Depends(get_current_user),
):
    """Approve or reject a transfer request"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "AREA_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")

    if transfer["status"] != TransferStatus.PENDING_APPROVAL:
        raise HTTPException(status_code=400, detail="Transfer is not pending approval")

    if approval.approved:
        new_status = TransferStatus.APPROVED
        message = "Transfer approved"
    else:
        new_status = TransferStatus.REJECTED
        message = "Transfer rejected"

    transfer["status"] = new_status
    transfer["approved_by"] = current_user.get("user_id")
    transfer["approved_at"] = datetime.now().isoformat()
    transfer["rejection_reason"] = (
        approval.rejection_reason if not approval.approved else None
    )
    transfer["updated_at"] = datetime.now().isoformat()

    _append_status_history(
        transfer,
        {
            "status": new_status,
            "timestamp": datetime.now().isoformat(),
            "user_id": current_user.get("user_id"),
            "user_name": current_user.get("username"),
            "notes": approval.rejection_reason if not approval.approved else "Approved",
        },
    )

    _save_transfer(transfer)
    return {"transfer": transfer, "message": message}


@router.post("/{transfer_id}/start-picking")
async def start_picking(
    transfer_id: str, current_user: dict = Depends(get_current_user)
):
    """Start picking items for transfer"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "STORE_MANAGER", "WORKSHOP_STAFF"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")

    if transfer["status"] != TransferStatus.APPROVED:
        raise HTTPException(
            status_code=400, detail="Transfer must be approved before picking"
        )

    transfer["status"] = TransferStatus.PICKING
    transfer["picking_started_at"] = datetime.now().isoformat()
    transfer["picking_by"] = current_user.get("user_id")
    transfer["updated_at"] = datetime.now().isoformat()

    _append_status_history(
        transfer,
        {
            "status": TransferStatus.PICKING,
            "timestamp": datetime.now().isoformat(),
            "user_id": current_user.get("user_id"),
            "user_name": current_user.get("username"),
            "notes": "Picking started",
        },
    )

    _save_transfer(transfer)
    return {"transfer": transfer, "message": "Picking started"}


@router.post("/{transfer_id}/complete-picking")
async def complete_picking(
    transfer_id: str,
    items_picked: List[Dict[str, Any]],  # [{"item_id": "xxx", "quantity_picked": 10}]
    current_user: dict = Depends(get_current_user),
):
    """Complete picking and mark items as packed"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "STORE_MANAGER", "WORKSHOP_STAFF"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")

    if transfer["status"] != TransferStatus.PICKING:
        raise HTTPException(
            status_code=400, detail="Transfer must be in picking status"
        )

    # Update item quantities
    item_map = {item["id"]: item for item in transfer["items"]}
    for picked in items_picked:
        if picked["item_id"] in item_map:
            item_map[picked["item_id"]]["quantity_shipped"] = picked.get(
                "quantity_picked", 0
            )
            item_map[picked["item_id"]]["status"] = "packed"

    transfer["status"] = TransferStatus.PACKED
    transfer["picking_completed_at"] = datetime.now().isoformat()
    transfer["updated_at"] = datetime.now().isoformat()

    _append_status_history(
        transfer,
        {
            "status": TransferStatus.PACKED,
            "timestamp": datetime.now().isoformat(),
            "user_id": current_user.get("user_id"),
            "user_name": current_user.get("username"),
            "notes": "Picking completed, items packed",
        },
    )

    _save_transfer(transfer)
    return {"transfer": transfer, "message": "Picking completed, ready for shipment"}


@router.post("/{transfer_id}/ship")
async def ship_transfer(
    transfer_id: str,
    tracking_number: Optional[str] = None,
    tracking_url: Optional[str] = None,
    courier_name: Optional[str] = None,
    create_shiprocket: bool = False,
    current_user: dict = Depends(get_current_user),
):
    """Mark transfer as shipped / in transit"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "STORE_MANAGER", "WORKSHOP_STAFF"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")

    if transfer["status"] not in [TransferStatus.APPROVED, TransferStatus.PACKED]:
        raise HTTPException(
            status_code=400,
            detail="Transfer must be approved or packed before shipping",
        )

    # Create Shiprocket shipment if requested
    if create_shiprocket:
        # In production, would call Shiprocket API
        shipment_id = f"SHP_{uuid.uuid4().hex[:8].upper()}"
        awb = f"AWB{uuid.uuid4().hex[:10].upper()}"
        transfer["shiprocket_shipment_id"] = shipment_id
        transfer["tracking_number"] = awb
        transfer["tracking_url"] = f"https://shiprocket.co/tracking/{awb}"
        transfer["courier_name"] = courier_name or "Delhivery"
    else:
        transfer["tracking_number"] = tracking_number
        transfer["tracking_url"] = tracking_url
        transfer["courier_name"] = courier_name

    transfer["status"] = TransferStatus.IN_TRANSIT
    transfer["shipped_at"] = datetime.now().isoformat()
    transfer["shipped_by"] = current_user.get("user_id")
    transfer["updated_at"] = datetime.now().isoformat()

    # Update item statuses
    for item in transfer["items"]:
        item["status"] = "in_transit"

    # SYSTEM_INTENT 5: actually reduce source-store on-hand. Idempotent via the
    # `stock_shipped` flag set inside the helper, so a re-POST won't double-move.
    transfer = _apply_ship_stock_move(transfer)

    _append_status_history(
        transfer,
        {
            "status": TransferStatus.IN_TRANSIT,
            "timestamp": datetime.now().isoformat(),
            "user_id": current_user.get("user_id"),
            "user_name": current_user.get("username"),
            "notes": f"Shipped via {transfer.get('courier_name', 'carrier')}",
        },
    )

    _save_transfer(transfer)
    return {
        "transfer": transfer,
        "message": "Transfer shipped",
        "tracking": {
            "number": transfer.get("tracking_number"),
            "url": transfer.get("tracking_url"),
        },
    }


@router.post("/{transfer_id}/receive")
async def receive_transfer(
    transfer_id: str,
    items_received: List[TransferItemReceive],
    current_user: dict = Depends(get_current_user),
):
    """Receive transfer items at destination"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "STORE_MANAGER", "WORKSHOP_STAFF"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")

    if transfer["status"] not in [
        TransferStatus.IN_TRANSIT,
        TransferStatus.PARTIALLY_RECEIVED,
    ]:
        raise HTTPException(
            status_code=400, detail="Transfer must be in transit to receive"
        )

    # BUG FIX: damaged qty cannot exceed received qty on any line.
    for received in items_received:
        if received.quantity_damaged > received.quantity_received:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"quantity_damaged ({received.quantity_damaged}) cannot exceed "
                    f"quantity_received ({received.quantity_received}) for item "
                    f"{received.transfer_item_id}"
                ),
            )

    # INV-11: build a multi-key lookup so older transfer docs whose item `id` was
    # never reliably set (or was stored under a different field name) can still be
    # received.  Priority: explicit `id` > `product_id` > zero-indexed fallback.
    # `product_id`-keyed resolution only fires when a doc has no `id` field at all
    # (e.g. imported / back-filled docs) so it doesn't accidentally merge two
    # legitimate lines for the same product in a multi-line transfer.
    item_map: Dict[str, dict] = {}
    items_without_id: List[dict] = []
    for item in transfer.get("items", []):
        item_id = item.get("id") or item.get("item_id") or ""
        if item_id:
            item_map[item_id] = item
        else:
            items_without_id.append(item)
    # For items that have no id, register by product_id as a fallback key.
    product_id_fallback: Dict[str, dict] = {}
    for item in items_without_id:
        pid = str(item.get("product_id") or "")
        if pid and pid not in product_id_fallback:
            product_id_fallback[pid] = item

    def _resolve_item(transfer_item_id: str) -> Optional[dict]:
        """Look up a transfer line by the caller's transfer_item_id.

        Falls back to product_id when the id field isn't present on the line
        (covers back-filled or pre-INV-11 docs). Returns None when no match."""
        if transfer_item_id in item_map:
            return item_map[transfer_item_id]
        # Caller may be sending product_id as transfer_item_id for legacy docs.
        if transfer_item_id in product_id_fallback:
            return product_id_fallback[transfer_item_id]
        return None

    total_expected = 0
    total_received = 0
    total_damaged = 0

    for received in items_received:
        item = _resolve_item(received.transfer_item_id)
        if item is not None:
            item["quantity_received"] = received.quantity_received
            item["quantity_damaged"] = received.quantity_damaged
            item["damage_notes"] = received.damage_notes
            item["received_at"] = datetime.now().isoformat()
            item["status"] = "received"

    # SYSTEM_INTENT 5: raise destination on-hand by creating AVAILABLE units at
    # the receiving store. Tracks per-line committed qty so a partial/repeat
    # receive only mints the delta - never double-creates. Runs after the line
    # quantities above are set so it sees the final `quantity_received`.
    transfer = _apply_receive_stock_move(transfer)

    for item in transfer["items"]:
        total_expected += item.get("quantity_shipped", 0)
        total_received += item.get("quantity_received", 0)
        total_damaged += item.get("quantity_damaged", 0)

    # Determine status
    if total_received >= total_expected:
        new_status = TransferStatus.RECEIVED
    else:
        new_status = TransferStatus.PARTIALLY_RECEIVED

    transfer["status"] = new_status
    transfer["received_at"] = datetime.now().isoformat()
    transfer["received_by"] = current_user.get("user_id")
    transfer["total_received"] = total_received
    transfer["total_damaged"] = total_damaged
    transfer["updated_at"] = datetime.now().isoformat()

    _append_status_history(
        transfer,
        {
            "status": new_status,
            "timestamp": datetime.now().isoformat(),
            "user_id": current_user.get("user_id"),
            "user_name": current_user.get("username"),
            "notes": f"Received {total_received} items, {total_damaged} damaged",
        },
    )

    _save_transfer(transfer)
    return {
        "transfer": transfer,
        "message": "Items received",
        "summary": {
            "expected": total_expected,
            "received": total_received,
            "damaged": total_damaged,
        },
    }


@router.post("/{transfer_id}/complete")
async def complete_transfer(
    transfer_id: str,
    notes: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Mark transfer as completed"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "STORE_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")

    if transfer["status"] not in [
        TransferStatus.RECEIVED,
        TransferStatus.PARTIALLY_RECEIVED,
    ]:
        raise HTTPException(
            status_code=400, detail="Transfer must be received before completion"
        )

    transfer["status"] = TransferStatus.COMPLETED
    transfer["completed_at"] = datetime.now().isoformat()
    transfer["completed_by"] = current_user.get("user_id")
    transfer["completion_notes"] = notes
    transfer["updated_at"] = datetime.now().isoformat()

    _append_status_history(
        transfer,
        {
            "status": TransferStatus.COMPLETED,
            "timestamp": datetime.now().isoformat(),
            "user_id": current_user.get("user_id"),
            "user_name": current_user.get("username"),
            "notes": notes or "Transfer completed",
        },
    )

    _save_transfer(transfer)

    # FIN-3: If this transfer crosses entity boundaries, write the mirror
    # purchase (vendor_bills) in the receiving entity so it can claim ITC.
    # Fail-soft -- never blocks the status flip.
    _book_mirror_purchase(transfer)

    return {"transfer": transfer, "message": "Transfer completed"}


@router.post("/{transfer_id}/cancel")
async def cancel_transfer(
    transfer_id: str, reason: str, current_user: dict = Depends(get_current_user)
):
    """Cancel a transfer"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "AREA_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")

    if transfer["status"] in [TransferStatus.COMPLETED, TransferStatus.CANCELLED]:
        raise HTTPException(
            status_code=400,
            detail="Cannot cancel completed or already cancelled transfer",
        )

    if transfer["status"] == TransferStatus.IN_TRANSIT:
        raise HTTPException(
            status_code=400, detail="Cannot cancel transfer that is in transit"
        )

    transfer["status"] = TransferStatus.CANCELLED
    transfer["cancelled_at"] = datetime.now().isoformat()
    transfer["cancelled_by"] = current_user.get("user_id")
    transfer["cancellation_reason"] = reason
    transfer["updated_at"] = datetime.now().isoformat()

    _append_status_history(
        transfer,
        {
            "status": TransferStatus.CANCELLED,
            "timestamp": datetime.now().isoformat(),
            "user_id": current_user.get("user_id"),
            "user_name": current_user.get("username"),
            "notes": reason,
        },
    )

    _save_transfer(transfer)
    return {"transfer": transfer, "message": "Transfer cancelled"}


# ============================================================================
# ANALYTICS & REPORTS
# ============================================================================


@router.get("/analytics/summary")
async def get_transfer_analytics(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    location_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Get transfer analytics summary"""
    transfers = _all_transfers()

    if location_id:
        transfers = [
            t
            for t in transfers
            if t.get("from_location_id") == location_id
            or t.get("to_location_id") == location_id
        ]

    total_transfers = len(transfers)
    completed = len([t for t in transfers if t["status"] == TransferStatus.COMPLETED])
    in_transit = len([t for t in transfers if t["status"] == TransferStatus.IN_TRANSIT])
    pending = len(
        [
            t
            for t in transfers
            if t["status"] in [TransferStatus.PENDING_APPROVAL, TransferStatus.APPROVED]
        ]
    )
    cancelled = len([t for t in transfers if t["status"] == TransferStatus.CANCELLED])

    total_value = sum(t.get("total_value", 0) for t in transfers)
    total_items = sum(t.get("total_items", 0) for t in transfers)

    return {
        "summary": {
            "total_transfers": total_transfers,
            "completed": completed,
            "in_transit": in_transit,
            "pending": pending,
            "cancelled": cancelled,
            "total_value": total_value,
            "total_items": total_items,
        },
        "by_type": {
            t_type.value: len(
                [t for t in transfers if t.get("transfer_type") == t_type.value]
            )
            for t_type in TransferType
        },
        "by_priority": {
            p.value: len([t for t in transfers if t.get("priority") == p.value])
            for p in TransferPriority
        },
    }


@router.get("/analytics/location/{location_id}")
async def get_location_transfer_analytics(
    location_id: str, current_user: dict = Depends(get_current_user)
):
    """Get transfer analytics for a specific location"""
    transfers = _all_transfers()

    outgoing = [t for t in transfers if t.get("from_location_id") == location_id]
    incoming = [t for t in transfers if t.get("to_location_id") == location_id]

    return {
        "location_id": location_id,
        "outgoing": {
            "total": len(outgoing),
            "in_transit": len(
                [t for t in outgoing if t["status"] == TransferStatus.IN_TRANSIT]
            ),
            "pending": len(
                [
                    t
                    for t in outgoing
                    if t["status"]
                    in [TransferStatus.PENDING_APPROVAL, TransferStatus.APPROVED]
                ]
            ),
            "value": sum(t.get("total_value", 0) for t in outgoing),
        },
        "incoming": {
            "total": len(incoming),
            "in_transit": len(
                [t for t in incoming if t["status"] == TransferStatus.IN_TRANSIT]
            ),
            "pending_receipt": len(
                [t for t in incoming if t["status"] == TransferStatus.IN_TRANSIT]
            ),
            "value": sum(t.get("total_value", 0) for t in incoming),
        },
    }


# ============================================================================
# INTER-GSTIN MIRROR PURCHASE  (FIN-3)
# ============================================================================
# An inter-entity transfer is a taxable supply: the sending entity issues a
# tax invoice to the receiving entity, and the receiving entity claims ITC on
# it.  `transfers.py` moves the physical stock; this helper writes the matching
# `vendor_bills` document in the receiving entity's books so the ITC register
# picks it up automatically.
#
# Triggered at COMPLETE (goods physically arrived + verified by the receiver).
# Fail-soft: a DB error here NEVER blocks the status flip -- the transfer is
# marked COMPLETED and the error is logged. The accountant can back-fill the
# bill manually.
#
# GST rule: intra-state supply = CGST + SGST; inter-state supply = IGST.
# We detect the states from the `stores` collection.  When state data is
# missing we default to intra-state (conservative -- never misroutes IGST).


def _store_state_code(db, store_id: str) -> str:
    """Return the 2-digit GST state code for a store; '' on miss/DB absent."""
    if db is None or not store_id:
        return ""
    try:
        store = db.get_collection("stores").find_one(
            {"store_id": store_id},
            {"_id": 0, "state": 1, "state_code": 1, "gstin": 1},
        )
        if not store:
            return ""
        # Prefer explicit state_code field.
        sc = str(store.get("state_code") or "").strip()
        if sc:
            return sc[:2]
        # Fall back to first 2 chars of the store's GSTIN.
        gstin = str(store.get("gstin") or "").strip()
        if len(gstin) >= 2:
            return gstin[:2]
    except Exception as exc:  # noqa: BLE001 - fail-soft
        logger.warning("[TRANSFER] state lookup failed for %s: %s", store_id, exc)
    return ""


def _store_entity(db, store_id: str) -> str:
    """Return the entity_id the store belongs to; '' on miss/DB absent."""
    if db is None or not store_id:
        return ""
    try:
        store = db.get_collection("stores").find_one(
            {"store_id": store_id},
            {"_id": 0, "entity_id": 1},
        )
        if store:
            return str(store.get("entity_id") or "")
    except Exception as exc:  # noqa: BLE001 - fail-soft
        logger.warning("[TRANSFER] entity lookup failed for %s: %s", store_id, exc)
    return ""


def _entity_gstin_for_state(db, entity_id: str, state_code: str) -> str:
    """Return the GSTIN the entity bills under in `state_code`; '' on miss."""
    if db is None or not entity_id:
        return ""
    try:
        entity = db.get_collection("entities").find_one(
            {"entity_id": entity_id},
            {"_id": 0, "gstins": 1},
        )
        if not entity:
            return ""
        for g in entity.get("gstins") or []:
            if str(g.get("state_code") or g.get("state") or "")[:2] == state_code:
                return str(g.get("gstin") or "")
        # Fall back to any GSTIN registered under this entity.
        gstins = entity.get("gstins") or []
        if gstins:
            return str(gstins[0].get("gstin") or "")
    except Exception as exc:  # noqa: BLE001 - fail-soft
        logger.warning(
            "[TRANSFER] GSTIN lookup failed entity=%s state=%s: %s",
            entity_id,
            state_code,
            exc,
        )
    return ""


def _tax_split(tax: float, interstate: bool):
    """Return (cgst, sgst, igst) for a tax amount.

    Intra-state: CGST = SGST = half each (residual trick avoids +/-1 paisa drift).
    Inter-state: IGST = full tax, CGST = SGST = 0.
    Pure, no I/O.
    """
    tax = round(float(tax or 0), 2)
    if interstate:
        return 0.0, 0.0, tax
    half = round(tax / 2, 2)
    sgst = round(tax - half, 2)
    return half, sgst, 0.0


def _book_mirror_purchase(transfer: Dict) -> None:
    """Write a vendor_bills document for an inter-entity transfer.

    Only writes when:
      * DB is available
      * from_store and to_store belong to DIFFERENT entity_ids
      * The bill has not already been written (idempotent on transfer_id)

    Fail-soft: any exception is logged; the caller (complete_transfer) is never
    interrupted.
    """
    db = _get_db()
    if db is None:
        return

    from_store_id = transfer.get("from_location_id") or ""
    to_store_id = transfer.get("to_location_id") or ""
    if not from_store_id or not to_store_id:
        return

    from_entity = _store_entity(db, from_store_id)
    to_entity = _store_entity(db, to_store_id)

    # NEW-GST-TRANSFER-IGST: a stock move books a mirror purchase (and GST) when it
    # crosses a GSTIN boundary -- that is EITHER a different legal entity OR a
    # different STATE. A same-PAN inter-STATE transfer is a deemed supply between
    # distinct GSTINs (Sch I) and attracts IGST. Compute states here so the gate
    # isn't fooled by a same-entity cross-state move (previously it returned early
    # and booked NO IGST -> GST understated).
    from_state = _store_state_code(db, from_store_id)
    to_state = _store_state_code(db, to_store_id)
    if not from_entity or not to_entity:
        return
    if from_entity == to_entity and (from_state or "") == (to_state or ""):
        return

    # Idempotent: skip if we already wrote the bill for this transfer.
    try:
        if db.get_collection("vendor_bills").find_one(
            {"source_transfer_id": transfer.get("id")},
            {"_id": 1},
        ):
            logger.info(
                "[TRANSFER] mirror bill already exists for transfer %s -- skipping",
                transfer.get("id"),
            )
            return
    except Exception as exc:  # noqa: BLE001 - fail-soft
        logger.warning("[TRANSFER] mirror bill idempotency check failed: %s", exc)
        return

    try:
        # Value = total_value on the transfer (sum of unit_cost * qty for each line).
        # Tax is computed at a fixed 18% (optics default: sunglasses/accessories) if
        # no per-line tax rate is captured; the CA can correct the bill if needed.
        # We store taxable_amount + tax_amount separately so the ITC register picks
        # them up correctly via itc_reconcile.build_itc_register.
        transfer_value = float(transfer.get("total_value") or 0)
        # Fall back: compute from items if total_value is zero.
        if transfer_value == 0:
            for item in transfer.get("items") or []:
                qty = int(
                    float(
                        item.get("quantity_received")
                        or item.get("quantity_requested")
                        or 0
                    )
                )
                cost = float(item.get("unit_cost") or 0)
                transfer_value += qty * cost
        transfer_value = round(transfer_value, 2)

        # GST: detect intra/inter-state from the sending (supply) store's state
        # vs the receiving store's state (consistent with GST Act -- place of
        # supply = location of goods at time of supply for stock transfers within
        # the same taxpayer group treated as deemed sale under Sch I Entry 2).
        from_state = _store_state_code(db, from_store_id)
        to_state = _store_state_code(db, to_store_id)
        interstate = bool(from_state and to_state and from_state != to_state)

        # Standard 18% GST rate for accessories; the CA adjusts if different.
        # Using 18% = 9+9 CGST+SGST (intra) or 18 IGST (inter).
        # If no cost data, taxable = 0 and no tax either.
        gst_rate = 0.18
        taxable = transfer_value  # transfer_value is cost (ex-GST)
        tax = round(taxable * gst_rate, 2)
        cgst, sgst, igst = _tax_split(tax, interstate)

        # Sending entity's GSTIN (acts as the "vendor" for the receiving entity).
        from_gstin = _entity_gstin_for_state(db, from_entity, from_state) or ""

        # Receiving entity's GSTIN (determines place_of_supply for ITC).
        to_gstin = _entity_gstin_for_state(db, to_entity, to_state) or ""

        bill_id = f"mbill_{uuid.uuid4().hex[:12]}"
        # Bill number mirrors the transfer number so it is traceable.
        bill_number = f"TRF/{transfer.get('transfer_number', transfer.get('id', ''))}"
        now_iso = datetime.now().isoformat()

        doc = {
            "bill_id": bill_id,
            "bill_number": bill_number,
            "invoice_number": bill_number,
            "bill_date": transfer.get("completed_at") or now_iso,
            "invoice_date": transfer.get("completed_at") or now_iso,
            # Link back to the transfer for traceability.
            "source_transfer_id": transfer.get("id"),
            "source_transfer_number": transfer.get("transfer_number"),
            # The "vendor" is the sending entity.
            "vendor_id": from_entity,
            "vendor_name": transfer.get("from_location_name") or from_entity,
            "vendor_gstin": from_gstin,
            # Receiving entity's details.
            "entity_id": to_entity,
            "recipient_gstin": to_gstin,
            # GST place-of-supply: the sending store's state (Sch I deemed supply).
            "place_of_supply": from_state,
            "interstate": interstate,
            # Amounts.
            "taxable_amount": taxable,
            "tax_amount": tax,
            "taxable_total": taxable,
            "cgst_total": cgst,
            "sgst_total": sgst,
            "igst_total": igst,
            "total_amount": round(taxable + tax, 2),
            "total": round(taxable + tax, 2),
            # ITC eligibility: inter-entity transfers are stock-in-trade, so
            # eligible by default. The CA can flag itc_blocked if needed.
            "itc_eligible": True,
            "itc_blocked": False,
            "status": "OUTSTANDING",
            "auto_generated": True,
            "notes": (
                f"Auto-generated mirror purchase for inter-entity stock transfer "
                f"{transfer.get('transfer_number', transfer.get('id', ''))} "
                f"from {transfer.get('from_location_name', from_entity)} "
                f"to {transfer.get('to_location_name', to_entity)}."
            ),
            "created_by": "SYSTEM",
            "created_at": now_iso,
        }

        db.get_collection("vendor_bills").insert_one(doc)
        logger.info(
            "[TRANSFER] mirror purchase bill %s written for inter-entity transfer %s "
            "(entities: %s -> %s, taxable=%.2f, tax=%.2f, interstate=%s)",
            bill_id,
            transfer.get("id"),
            from_entity,
            to_entity,
            taxable,
            tax,
            interstate,
        )
    except Exception as exc:  # noqa: BLE001 - fail-soft; never block COMPLETE
        logger.error(
            "[TRANSFER] mirror purchase write failed for transfer %s: %s",
            transfer.get("id"),
            exc,
        )


# ============================================================================
# BULK OPERATIONS
# ============================================================================


@router.post("/bulk-approve")
async def bulk_approve_transfers(
    transfer_ids: List[str], current_user: dict = Depends(get_current_user)
):
    """Bulk approve multiple transfers"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "AREA_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    approved = 0
    errors = []

    for tid in transfer_ids:
        transfer = _get_transfer(tid)
        if not transfer:
            errors.append({"id": tid, "error": "Not found"})
            continue

        if transfer["status"] != TransferStatus.PENDING_APPROVAL:
            errors.append({"id": tid, "error": "Not pending approval"})
            continue

        transfer["status"] = TransferStatus.APPROVED
        transfer["approved_by"] = current_user.get("user_id")
        transfer["approved_at"] = datetime.now().isoformat()
        transfer["updated_at"] = datetime.now().isoformat()

        _append_status_history(
            transfer,
            {
                "status": TransferStatus.APPROVED,
                "timestamp": datetime.now().isoformat(),
                "user_id": current_user.get("user_id"),
                "user_name": current_user.get("username"),
                "notes": "Bulk approved",
            },
        )

        _save_transfer(transfer)
        approved += 1

    return {
        "message": f"{approved} transfers approved",
        "approved_count": approved,
        "errors": errors,
    }


# ============================================================================
# SHIPROCKET INTEGRATION
# ============================================================================


@router.post("/{transfer_id}/create-shiprocket-shipment")
async def create_shiprocket_shipment_for_transfer(
    transfer_id: str,
    courier_code: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Create Shiprocket shipment for a transfer"""
    if not any(
        role in current_user.get("roles", [])
        for role in ["SUPERADMIN", "ADMIN", "STORE_MANAGER"]
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")

    if transfer["status"] not in [TransferStatus.APPROVED, TransferStatus.PACKED]:
        raise HTTPException(
            status_code=400, detail="Transfer must be approved or packed"
        )

    # In production, would call Shiprocket API
    shipment_id = f"SHP_{uuid.uuid4().hex[:8].upper()}"
    awb = f"AWB{uuid.uuid4().hex[:10].upper()}"

    transfer["shiprocket_shipment_id"] = shipment_id
    transfer["tracking_number"] = awb
    transfer["tracking_url"] = f"https://shiprocket.co/tracking/{awb}"
    transfer["courier_name"] = courier_code or "Delhivery"
    transfer["updated_at"] = datetime.now().isoformat()

    _save_transfer(transfer)
    return {
        "transfer_id": transfer_id,
        "shiprocket_shipment_id": shipment_id,
        "awb": awb,
        "tracking_url": transfer["tracking_url"],
        "courier": transfer["courier_name"],
        "message": "Shiprocket shipment created",
    }


@router.get("/{transfer_id}/tracking")
async def get_transfer_tracking(
    transfer_id: str, current_user: dict = Depends(get_current_user)
):
    """Get tracking information for a transfer"""
    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")

    if not transfer.get("tracking_number"):
        raise HTTPException(status_code=400, detail="No tracking information available")

    # In production, would call Shiprocket tracking API
    return {
        "transfer_id": transfer_id,
        "tracking_number": transfer.get("tracking_number"),
        "tracking_url": transfer.get("tracking_url"),
        "courier": transfer.get("courier_name"),
        "current_status": transfer.get("status"),
        "tracking_history": [
            {
                "status": "PICKUP_SCHEDULED",
                "location": transfer.get("from_location_name"),
                "timestamp": transfer.get("created_at"),
            },
            (
                {
                    "status": "PICKED_UP",
                    "location": transfer.get("from_location_name"),
                    "timestamp": transfer.get("shipped_at"),
                }
                if transfer.get("shipped_at")
                else None
            ),
            (
                {
                    "status": "IN_TRANSIT",
                    "location": "Distribution Hub",
                    "timestamp": transfer.get("shipped_at"),
                }
                if transfer.get("shipped_at")
                else None
            ),
        ],
    }
