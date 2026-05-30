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
    quantity_requested: int
    unit_cost: Optional[float] = None
    notes: Optional[str] = None


class TransferItemReceive(BaseModel):
    transfer_item_id: str
    quantity_received: int
    quantity_damaged: int = 0
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
    shipping_cost: Optional[float] = None
    # Shiprocket integration
    create_shiprocket_shipment: bool = False
    shiprocket_courier: Optional[str] = None


class TransferUpdate(BaseModel):
    priority: Optional[TransferPriority] = None
    expected_date: Optional[str] = None
    notes: Optional[str] = None
    shipping_method: Optional[str] = None
    shipping_cost: Optional[float] = None
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
#   RECEIVE -> raise on-hand at the DESTINATION store: create that many
#              AVAILABLE units there (new store-local barcodes are a separate
#              print step; the data layer just mints the units).
#
# Idempotency: a doc-level `stock_shipped` flag guards SHIP; each line tracks
# `received_qty_committed` (units already minted at destination) so a re-/
# partial receive only ever mints the DELTA. Fail-soft: no stock repo (DB
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

        # Claim AVAILABLE source units for this product (one row per unit).
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


def _apply_receive_stock_move(transfer: Dict) -> Dict:
    """Raise destination on-hand when transfer units are received.

    For each line, mint AVAILABLE units at the DESTINATION store up to the
    line's `quantity_received`, minus whatever was already minted on a prior
    (partial) receive. The delta is tracked per line in `received_qty_committed`
    and the minted ids appended to `received_stock_ids`, so repeated or partial
    receives only ever create the NEW units - never duplicates.

    Fail-soft: no stock repo -> transfer returned unchanged (lifecycle still
    advances, as before).
    """
    stock_repo = get_stock_repository()
    if stock_repo is None:
        return transfer

    to_store = transfer.get("to_location_id")
    if not to_store:
        return transfer

    minted_total = 0
    for line in transfer.get("items", []):
        product_id = line.get("product_id")
        try:
            received = int(float(line.get("quantity_received", 0) or 0))
        except (TypeError, ValueError):
            received = 0
        already = int(line.get("received_qty_committed", 0) or 0)
        delta = received - already
        if not product_id or delta <= 0:
            continue

        minted_ids: List[str] = list(line.get("received_stock_ids", []))
        for _ in range(delta):
            barcode = f"{str(to_store)[:3]}-{uuid.uuid4().hex[:8].upper()}"
            created = stock_repo.create(
                {
                    "product_id": product_id,
                    "store_id": to_store,
                    "barcode": barcode,
                    "location_code": "DEFAULT",
                    "status": STOCK_STATUS_AVAILABLE,
                    "is_reserved": False,
                    "barcode_printed": False,
                    "source_type": "TRANSFER",
                    "source_id": transfer.get("id"),
                    "transfer_number": transfer.get("transfer_number"),
                    "from_store_id": transfer.get("from_location_id"),
                }
            )
            if created:
                sid = (
                    created.get("stock_id")
                    or created.get("stock_unit_id")
                    or created.get("_id")
                )
                if sid:
                    minted_ids.append(str(sid))
                    minted_total += 1
                    _audit_stock_move(
                        None,
                        STOCK_STATUS_AVAILABLE,
                        str(sid),
                        transfer,
                        {"product_id": product_id, "minted": True},
                    )

        line["received_stock_ids"] = minted_ids
        line["received_qty_committed"] = already + delta

    transfer["stock_units_moved_in"] = (
        int(transfer.get("stock_units_moved_in", 0) or 0) + minted_total
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

    transfer["status_history"].append(
        {
            "status": new_status,
            "timestamp": datetime.now().isoformat(),
            "user_id": current_user.get("user_id"),
            "user_name": current_user.get("username"),
            "notes": approval.rejection_reason if not approval.approved else "Approved",
        }
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

    transfer["status_history"].append(
        {
            "status": TransferStatus.PICKING,
            "timestamp": datetime.now().isoformat(),
            "user_id": current_user.get("user_id"),
            "user_name": current_user.get("username"),
            "notes": "Picking started",
        }
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

    transfer["status_history"].append(
        {
            "status": TransferStatus.PACKED,
            "timestamp": datetime.now().isoformat(),
            "user_id": current_user.get("user_id"),
            "user_name": current_user.get("username"),
            "notes": "Picking completed, items packed",
        }
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

    transfer["status_history"].append(
        {
            "status": TransferStatus.IN_TRANSIT,
            "timestamp": datetime.now().isoformat(),
            "user_id": current_user.get("user_id"),
            "user_name": current_user.get("username"),
            "notes": f"Shipped via {transfer.get('courier_name', 'carrier')}",
        }
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

    # Update item quantities
    item_map = {item["id"]: item for item in transfer["items"]}
    total_expected = 0
    total_received = 0
    total_damaged = 0

    for received in items_received:
        if received.transfer_item_id in item_map:
            item = item_map[received.transfer_item_id]
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

    transfer["status_history"].append(
        {
            "status": new_status,
            "timestamp": datetime.now().isoformat(),
            "user_id": current_user.get("user_id"),
            "user_name": current_user.get("username"),
            "notes": f"Received {total_received} items, {total_damaged} damaged",
        }
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

    transfer["status_history"].append(
        {
            "status": TransferStatus.COMPLETED,
            "timestamp": datetime.now().isoformat(),
            "user_id": current_user.get("user_id"),
            "user_name": current_user.get("username"),
            "notes": notes or "Transfer completed",
        }
    )

    _save_transfer(transfer)
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

    transfer["status_history"].append(
        {
            "status": TransferStatus.CANCELLED,
            "timestamp": datetime.now().isoformat(),
            "user_id": current_user.get("user_id"),
            "user_name": current_user.get("username"),
            "notes": reason,
        }
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

        transfer["status_history"].append(
            {
                "status": TransferStatus.APPROVED,
                "timestamp": datetime.now().isoformat(),
                "user_id": current_user.get("user_id"),
                "user_name": current_user.get("username"),
                "notes": "Bulk approved",
            }
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
