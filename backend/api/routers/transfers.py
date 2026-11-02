"""
IMS 2.0 - Stock Transfers Router
================================
Complete stock transfer management between locations, stores, and warehouses.
Includes transfer requests, approvals, in-transit tracking, and receiving.
"""

from api.utils.event_time import utc_now_iso

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime
from ..utils.ist import now_ist
from enum import Enum
import logging
import uuid

from .auth import get_current_user
from ..dependencies import (
    get_stock_repository,
    can_access_store_scoped,
    no_shop_scope_for,
    user_store_scope,
    validate_store_access,
)

# W1.4 / OS-032: shared ONLINE store-type detector -- a transfer must never
# land stock on a pooled, stockless ONLINE store.
from ..services import org_validation as ov
from ..services import stock_value
from ..services.barcode import find_unit_by_code
from ..services.cost_mask import mask_cost
from ..services.purchase_invoice_engine import classify_supply
from ..services.stores_util import is_online_store
from database.repositories.order_repository import (
    InvoiceNumberError,
    next_document_number,
)

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
STOCK_STATUS_VOID = "VOID"

# S3: the note a short delivery between two GSTINs is booked on (CA answers
# 2026-10-23: value received + a CREDIT NOTE for the difference). It sits in
# vendor_bills next to the transfer's tax invoice (same source_transfer_id),
# negative, so every sum over those bills nets it.
TRANSFER_CREDIT_NOTE = "TRANSFER_CREDIT_NOTE"

# A piece scanned OUT and never IN ("Missing on the way") stays open until it
# is found or written off by an admin.
MISSING_OPEN = ("missing", "write_off_requested")

# Who does what (the screen's gates mirror these, transferPermissions.ts).
SHIP_ROLES = ["SUPERADMIN", "ADMIN", "STORE_MANAGER", "WORKSHOP_STAFF"]
RECEIVE_ROLES = ["SUPERADMIN", "ADMIN", "STORE_MANAGER", "WORKSHOP_STAFF"]
MISSING_ROLES = ["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"]
CANCEL_ROLES = ["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"]


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


# Statuses a transfer may be cancelled from -- an ALLOWLIST of the PRE-SHIP
# states only (PR #959 round 2). The old guard was a denylist (completed /
# cancelled / in_transit) which still ACCEPTED a cancel at RECEIVED /
# PARTIALLY_RECEIVED -- i.e. after ship moved real units out of the source
# store and receive re-homed them at the destination. A cancel at that point
# does NO stock reversal (the units stay re-homed while the doc reads
# CANCELLED) and permanently skips the inter-entity/inter-state GST
# deemed-supply mirror invoice, which books only at /complete. Pre-ship,
# nothing has moved, so cancel is harmless. REJECTED is included (pre-ship,
# zero stock moved) so a dead request can still be tidied to cancelled.
# Legacy "pending" is included because the endless-aisle writer stamps
# `status: "pending"` on the stock_transfers docs it creates
# (services/endless_aisle.py create_transfer step) -- also strictly pre-ship.
# Matched case-insensitively: the agent-proposals writer stamps uppercase
# "DRAFT" on its draft transfer docs.
CANCELLABLE_TRANSFER_STATUSES = frozenset(
    {
        TransferStatus.DRAFT.value,
        TransferStatus.PENDING_APPROVAL.value,
        TransferStatus.APPROVED.value,
        TransferStatus.PICKING.value,
        TransferStatus.PACKED.value,
        TransferStatus.REJECTED.value,
        "pending",  # legacy endless-aisle transfer docs (pre-approval)
    }
)


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


class TransferPack(BaseModel):
    """S3: the pieces scanned OUT into the box, the whole list each time (a
    scan re-sends it), so a retry or a second tablet never double-packs."""

    barcodes: List[str] = Field(default_factory=list)
    # The box revision the screen built this list from: a list built from an
    # older box (another tablet packed meanwhile) is refused, never saved over
    # it. Optional for older screens.
    rev: Optional[int] = None


class TransferPieceReceive(BaseModel):
    """S3: ONE piece scanned IN at the receiving shop."""

    barcode: str = Field(..., min_length=1)
    # Damaged in the box: the piece goes to QUARANTINE at the receiving shop,
    # never to the shelf, and the reason is required.
    damaged: bool = False
    damage_reason: Optional[str] = None


class MissingFound(BaseModel):
    """Where a piece that was missing on the way turned up."""

    at: str = Field(..., pattern="^(source|destination)$")


class TransferInput(BaseModel):
    transfer_type: TransferType
    from_location_id: str = Field(..., min_length=1)
    from_location_name: str
    to_location_id: str = Field(..., min_length=1)
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
    # Part B of the e-way bill data, typed on the box page (board TransferBox).
    eway_bill_number: Optional[str] = Field(default=None, max_length=20)
    vehicle_number: Optional[str] = Field(default=None, max_length=20)


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


def _for(user: Dict, body: Dict) -> Dict:
    """A transfer reply as `user` may read it: a line's unit_cost and the
    transfer's total_value (and a location's outgoing / incoming value) are at
    cost, so they go to the roles services/cost_mask "product" admits only
    (owner ruling D7) -- as do the frozen tax invoice lines (priced at cost)
    and each shipped piece's frozen cost (shipped_unit_costs)."""
    return mask_cost(
        body, user, "product",
        extra=("total_value", "value", "tax_invoice_lines", "shipped_unit_costs"),
    )


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


STALE_TRANSFER = "This box changed while you were working on it. Reload it and try again."


class StaleTransfer(HTTPException):
    """Another press saved the transfer after this one read it (409)."""

    def __init__(self):
        super().__init__(status_code=409, detail=STALE_TRANSFER)


def _save_transfer(transfer: Dict) -> None:
    """Save a transfer by its `id`, compare-and-set on its `rev`: the write
    lands only if the stored doc is still the revision this press read, so
    two presses on two workers never overwrite each other (a lost scan, a
    ship undone by a stale pack or cancel). Raises StaleTransfer when another
    press saved first; a new transfer (no rev, not stored yet) is inserted.
    Falls back to the in-memory dict when the DB is unavailable."""
    doc = _coerce(transfer)
    rev = doc.pop("rev", None)
    coll = _transfers_coll()
    if coll is not None:
        res = coll.update_one({"id": doc["id"], "rev": rev}, {"$set": doc, "$inc": {"rev": 1}})
        if getattr(res, "matched_count", None) == 0:
            if rev is not None or coll.find_one({"id": doc["id"]}, {"_id": 1}):
                raise StaleTransfer()
            coll.insert_one({**doc, "rev": 1})
    else:
        stored = STOCK_TRANSFERS.get(doc["id"])
        if stored is not None and stored.get("rev") != rev:
            raise StaleTransfer()
        STOCK_TRANSFERS[doc["id"]] = {**doc, "rev": (rev or 0) + 1}
    transfer["rev"] = (rev or 0) + 1


def _update_transfer(transfer_id: str, apply) -> Dict:
    """Read the transfer, `apply(transfer)` the change, save it -- and when
    another press saved first, read it again and re-apply, so a door whose
    change is safe to repeat (a scan, a piece found or written off, an e-way
    bill no.) never loses a concurrent one. `apply` refuses by raising.
    Returns the saved transfer."""
    for _ in range(5):
        transfer = _get_transfer(transfer_id)
        if not transfer:
            raise HTTPException(status_code=404, detail="Transfer not found")
        apply(transfer)
        try:
            _save_transfer(transfer)
            return transfer
        except StaleTransfer:
            continue
    raise StaleTransfer()


def _take_box(transfer: Dict) -> None:
    """Ship takes the box BEFORE it touches a piece: a compare-and-set bump of
    `rev`. A press that read the box before another press took or saved it
    is refused here (StaleTransfer) with nothing claimed, and a press that is
    overtaken mid-ship can never save -- so only the last press to take the
    box ships it. A press that dies leaves nothing locked: the next press
    reads the new rev and takes the box again."""
    rev = transfer.get("rev")
    res = _transfers_coll().update_one(
        {"id": transfer["id"], "rev": rev},
        {"$set": {"ship_taken_at": utc_now_iso()}, "$inc": {"rev": 1}},
    )
    if getattr(res, "matched_count", None) == 0:
        raise StaleTransfer()
    transfer["rev"] = (rev or 0) + 1


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


# ============================================================================
# OBJECT-LEVEL STORE AUTHORIZATION  (IDOR guard)
# ============================================================================
# The per-endpoint role gates say WHO may run a lifecycle action; they say
# nothing about WHICH transfer. Before this guard, any STORE_MANAGER /
# WORKSHOP_STAFF / AREA_MANAGER could ship / receive / cancel / complete ANY
# transfer by id -- including one between two stores they don't belong to --
# and ship/receive move REAL stock_units. Every object-level endpoint now
# asserts the caller's store membership against the relevant SIDE of the
# transfer:
#
#   side="source"  -> caller must reach from_location_id (update / approve /
#                     picking / ship / cancel / bulk-approve / shiprocket)
#   side="dest"    -> caller must reach to_location_id (receive / complete)
#   side="either"  -> either side suffices (single GET / tracking reads)
#
# SUPERADMIN / ADMIN are cross-store by design (user_store_scope) and always
# pass. Purely additive: in-scope callers see zero behavior change. A transfer
# whose relevant side is unstamped (legacy doc) is admin-only, mirroring the
# can_access_store_scoped contract for unattributed records.


def _assert_transfer_access(
    transfer: Dict, current_user: dict, side: str = "either"
) -> None:
    """Raise 403 unless the caller's store scope reaches the transfer's `side`."""
    from_id = transfer.get("from_location_id")
    to_id = transfer.get("to_location_id")
    if side == "source":
        allowed = can_access_store_scoped(from_id, current_user)
    elif side == "dest":
        allowed = can_access_store_scoped(to_id, current_user)
    else:  # "either" -- reads where both parties legitimately need visibility
        allowed = can_access_store_scoped(
            from_id, current_user
        ) or can_access_store_scoped(to_id, current_user)
    if not allowed:
        raise HTTPException(
            status_code=403, detail="No access to this transfer's store"
        )


def _in_callers_stores(transfers: List[Dict], current_user: dict) -> List[Dict]:
    """The transfers touching the caller's stores, either side --
    user_store_scope, the one store reach: SUPERADMIN / ADMIN see every
    transfer, everyone else (AREA_MANAGER included) their own stores'. Every
    transfer list and figure (list, /pending, both analytics, and finance's
    /reconciliation) reads through here, so none shows another region's
    transfers or their cost."""
    is_cross_store, allowed_stores = user_store_scope(current_user)
    if is_cross_store:
        return transfers
    return [
        t
        for t in transfers
        if t.get("from_location_id") in allowed_stores
        or t.get("to_location_id") in allowed_stores
    ]


def _can_print_challan(transfer: Dict, current_user: dict) -> bool:
    """Whether the challan route prints this transfer's delivery challan for
    the caller: its own gate (print_documents.challan_gate), asked, not
    copied -- a move IMS cannot place prints for no one, a valued one for
    managers and accounts once shipped."""
    from .print_documents import challan_gate

    # ponytail: four shop reads per UNSHIPPED transfer in a list (a shipped
    # one reads ship's stamp); batch the shops (org_validation.shop_gstins)
    # if that list ever gets slow.
    try:
        challan_gate(transfer, current_user)
    except HTTPException:
        return False
    return True


def _caller_view(transfer: Dict, current_user: dict) -> Dict:
    """The transfer as the caller may see it -- every transfer reply goes
    through here. D7: counter roles never see what the units cost (each line's
    unit_cost, the total_value -- F51). And `can_print_challan`, so the screen
    shows the Delivery Challan button only to whom the server prints it (owner
    2026-10-08: the valued challan is managers' and accounts' only). A copy:
    never mutates the stored doc (the in-memory fallback hands out the
    original)."""
    if not isinstance(transfer, dict):
        return transfer
    return _for(
        current_user,
        dict(transfer, can_print_challan=_can_print_challan(transfer, current_user)),
    )


def generate_transfer_number() -> str:
    """Generate a unique transfer number — DB-count-based when persistent,
    else the in-memory counter."""
    coll = _transfers_coll()
    if coll is not None:
        seq = coll.count_documents({}) + 1001
        return f"TRF-{now_ist().strftime('%Y%m')}-{seq}"
    TRANSFER_COUNTER["count"] += 1
    return f"TRF-{now_ist().strftime('%Y%m')}-{TRANSFER_COUNTER['count']}"


def _transfer_tax_invoice(transfer: Dict) -> str:
    """THE tax invoice number of a move between two GST registrations (owner /
    CA 2026-10-18: a stock move between our shops with DIFFERENT GSTINs is a
    supply on a TAX INVOICE + e-way bill; one inside one GSTIN stays on a
    delivery challan and its TRF- number). From the SENDING shop's "T" series
    of the one mint (order_repository.next_document_number), e.g.
    BOK02/T2627/0001. Minted ONCE: a number already on the transfer is
    returned; else one is minted and written only if none landed meanwhile
    (compare-and-set), so two presses share one number -- the loser's serial
    is an unused gap, never a second invoice. Stamps tax_invoice_number /
    tax_invoice_date on `transfer`. Raises InvoiceNumberError (no database, no
    shop code, ...): the caller refuses, never guesses."""
    if transfer.get("tax_invoice_number"):
        return transfer["tax_invoice_number"]
    db = _get_db()
    number = next_document_number(db, transfer.get("from_location_id"), "T")
    issued = utc_now_iso()
    coll = db.get_collection("stock_transfers")
    claimed = coll.update_one(
        {"id": transfer.get("id"), "tax_invoice_number": None},
        {"$set": {"tax_invoice_number": number, "tax_invoice_date": issued}},
    )
    if not claimed.modified_count:
        stored = coll.find_one({"id": transfer.get("id")}, {"_id": 0}) or {}
        if stored.get("tax_invoice_number"):
            number = stored["tax_invoice_number"]
            issued = stored.get("tax_invoice_date") or issued
    transfer["tax_invoice_number"] = number
    transfer["tax_invoice_date"] = issued
    return number


def _freeze_tax_invoice_lines(transfer: Dict) -> List[Dict]:
    """The transfer tax invoice's lines, FROZEN once: what left the shop, at
    the HSN, GST rate, taxable value and tax heads of the moment it was
    invoiced (_mirror_bill_lines on the units shipped). The print reads them
    as stored, and the mirror bill (GSTR-1, the receiver's inward copy) taxes
    each line at its frozen HSN / rate -- so an HSN or rate edited after ship
    never changes the invoice behind a number already issued. Kept on the
    transfer as `tax_invoice_lines` (written only if none landed meanwhile);
    returns them."""
    if transfer.get("tax_invoice_lines") is not None:
        return transfer["tax_invoice_lines"]
    db = _get_db()
    (_, src_gstin, _), (_, dst_gstin, _), _ = _transfer_registrations(db, transfer)
    interstate = bool(classify_supply(src_gstin, dst_gstin)["interstate"])
    lines = _mirror_bill_lines(db, transfer, interstate, shipped=True)
    transfer["tax_invoice_lines"] = lines
    if db is not None:
        db.get_collection("stock_transfers").update_one(
            {"id": transfer.get("id"), "tax_invoice_lines": None},
            {"$set": {"tax_invoice_lines": lines}},
        )
    return lines


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


def _qty(raw) -> int:
    """Coerce a stored quantity to a whole, non-negative int (serialized stock is
    one row per unit). Missing / junk -> 0. Pure."""
    try:
        return max(0, int(float(raw or 0)))
    except (TypeError, ValueError):
        return 0


def _line_expected_qty(line: Dict) -> int:
    """How many units RECEIVE should expect on this line.

    The SINGLE definition shared by the over-receive cap and the completeness /
    short-surplus math, so the two can never disagree about what "shipped"
    means. They used to: the cap fell back to quantity_requested for a legacy
    doc, while the totals read `quantity_shipped` with no fallback -- so such a
    line contributed 0 to expected and its full receipt to received, masking
    another line's shortfall and reporting a shortage as a surplus.

    `quantity_shipped` is authoritative once SHIP stamps it -- a stamped 0 means
    the source held nothing to send and the line expects nothing. Only a line
    that never stamped it at all (back-filled / imported doc, the same shape
    INV-11's _resolve_item covers) falls back to quantity_requested. Pure.
    """
    raw = line.get("quantity_shipped")
    return _qty(raw if raw is not None else line.get("quantity_requested"))


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
            "at": utc_now_iso(),
        }
        if extra:
            row.update(extra)
        db.get_collection("stock_audit").insert_one(row)
    except Exception as exc:  # noqa: BLE001 - audit is fail-soft
        logger.warning("[TRANSFER] stock audit skipped: %s", exc)


def _ledger_transfer_event(event_type_name: str, stock_id, from_state, to_state,
                           product_id, store_id, to_store_id, transfer):
    """E3w: additively ledger one transfer ship/receive unit move into
    item_events AFTER the existing status write + stock_audit row succeed.

    No CAS, no projection -- the transfer path already owns the stock_units
    write. Fail-soft: any error is logged and swallowed so a ledger hiccup can
    never undo the move that already happened or fail the transfer."""
    try:
        from ..services import item_events as ie

        db = _get_db()
        if db is None:
            return
        ie.record_post_write_event(
            db,
            event_type=getattr(ie.ItemEventType, event_type_name),
            actor_id="",
            stock_id=str(stock_id),
            from_state=from_state,
            to_state=to_state,
            product_id=product_id,
            store_id=store_id,
            to_store_id=to_store_id,
            source_type="TRANSFER",
            source_id=transfer.get("id"),
            payload={"transfer_number": transfer.get("transfer_number")},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TRANSFER] item-event ledger emit skipped: %s", exc)


def _writeback_units_left(product_ids: List[str], store_id, source: str) -> None:
    """Online stock write-back for units that left a shop's shelf. NEVER
    raises into the transfer path."""
    try:
        from ..services.online_stock_writeback import writeback_after_units_left

        writeback_after_units_left(_get_db(), product_ids, store_id, source=source)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[TRANSFER] online stock write-back skipped: %s", exc)


def _create_receive_mismatch_task(transfer: Dict, discrepancy: Dict) -> Optional[str]:
    """BUG-019: raise a follow-up task when a receive does not match the shipment.

    A mismatch (damaged units, OR a short receive where received < shipped, OR a
    surplus delta) used to advance the transfer to RECEIVED/PARTIALLY_RECEIVED
    silently, so a real discrepancy never surfaced for follow-up. We now insert a
    store-scoped task into the canonical `tasks` collection (the same collection
    the Tasks module + escalation engine read), scoped to the DESTINATION store
    so the receiving manager owns the reconciliation.

    Reuses the existing `tasks`-insert pattern (mirrors reminder_rail's
    _create_followup_task). Fail-soft: no DB / any error -> None, never blocks the
    receive that already happened."""
    db = _get_db()
    if db is None:
        return None
    short = int(discrepancy.get("short", 0) or 0)
    surplus = int(discrepancy.get("surplus", 0) or 0)
    damaged = int(discrepancy.get("damaged", 0) or 0)
    # A short or damaged shipment is more urgent (stock loss / vendor claim) than
    # a pure surplus reconciliation.
    priority = "P2" if (short > 0 or damaged > 0) else "P3"
    bits = []
    if short > 0:
        bits.append(f"{short} short")
    if surplus > 0:
        bits.append(f"{surplus} surplus")
    if damaged > 0:
        bits.append(f"{damaged} damaged (quarantined)")
    detail = ", ".join(bits) if bits else "quantity mismatch"
    task_id = f"TSK-{uuid.uuid4().hex[:10].upper()}"
    try:
        db.get_collection("tasks").insert_one(
            {
                "task_id": task_id,
                "task_type": "transfer_discrepancy",
                "title": (
                    f"Transfer receive discrepancy: "
                    f"{transfer.get('transfer_number', transfer.get('id'))}"
                ),
                "description": (
                    f"Transfer {transfer.get('transfer_number', transfer.get('id'))} "
                    f"from {transfer.get('from_location_name', transfer.get('from_location_id'))} "
                    f"was received with a discrepancy: {detail}. "
                    f"Reconcile against the shipment and raise a vendor/transit "
                    f"claim if needed."
                ),
                "status": "PENDING",
                "priority": priority,
                "store_id": transfer.get("to_location_id"),
                "source": "TRANSFER_RECEIVE",
                "transfer_id": transfer.get("id"),
                "transfer_number": transfer.get("transfer_number"),
                "discrepancy": {
                    "short": short,
                    "surplus": surplus,
                    "damaged": damaged,
                },
                "created_at": utc_now_iso(),
                "created_by": "system:transfer_receive",
            }
        )
        return task_id
    except Exception as exc:  # noqa: BLE001 - task is a side-channel, fail-soft
        logger.warning("[TRANSFER] receive-mismatch task create skipped: %s", exc)
        return None


def _require_roles(current_user: dict, roles: List[str]) -> None:
    if not any(role in (current_user or {}).get("roles", []) for role in roles):
        raise HTTPException(status_code=403, detail="Insufficient permissions")


def _units_coll():
    """stock_units, or a 503: a per-piece move never runs blind."""
    db = _get_db()
    if db is None:
        raise HTTPException(
            status_code=503,
            detail="IMS cannot reach the stock records just now. Nothing has "
            "moved; try again.",
        )
    return db.get_collection("stock_units")


def _free_at(db, product_id: str, store_id: str) -> tuple:
    """(free, reserved) pieces of a product at a shop. FREE is what a box can
    take: status exactly AVAILABLE (the allocation question every claim door
    asks). RESERVED pieces are promised to a customer and can never be asked
    for (owner, board Transfer)."""
    units = db.get_collection("stock_units")
    where = {"product_id": product_id, "store_id": store_id}
    free = units.count_documents({**where, "status": STOCK_STATUS_AVAILABLE})
    reserved = units.count_documents({**where, "status": {"$in": ["RESERVED", "reserved"]}})
    return free, reserved


def _packed_elsewhere(transfer_id: str, stock_ids) -> Dict[str, str]:
    """{stock_id: transfer number} of the pieces already in ANOTHER box that
    has not shipped -- one piece, one box."""
    ids = [str(s) for s in stock_ids]
    if not ids:
        return {}
    open_ = [TransferStatus.APPROVED.value, TransferStatus.PICKING.value, TransferStatus.PACKED.value]
    coll = _transfers_coll()
    if coll is not None:
        docs = coll.find(
            {"id": {"$ne": transfer_id}, "status": {"$in": open_},
             "items.packed_stock_ids": {"$in": ids}},
            {"_id": 0, "id": 1, "status": 1, "transfer_number": 1, "items": 1},
        )
    else:
        docs = [t for t in STOCK_TRANSFERS.values()
                if t.get("id") != transfer_id and t.get("status") in open_]
    out: Dict[str, str] = {}
    for doc in docs:
        if doc.get("id") == transfer_id or doc.get("status") not in open_:
            continue
        for line in doc.get("items") or []:
            for sid in line.get("packed_stock_ids") or []:
                if str(sid) in ids:
                    out[str(sid)] = doc.get("transfer_number") or ""
    return out


def _packed_units(transfer: Dict) -> Dict[str, Dict]:
    """{stock_id: unit} of the pieces packed in the box, each checked BEFORE
    anything moves: still free at the sending shop, or already claimed by
    THIS transfer (a ship that died part-way, pressed again). Anything else
    -- sold, reserved, moved since packing -- refuses the ship by its
    barcode, so nothing leaves on a box that is not what was scanned."""
    units = _units_coll()
    tid = transfer.get("id")
    from_store = transfer.get("from_location_id")
    ids = [str(s) for ln in transfer.get("items") or [] for s in ln.get("packed_stock_ids") or []]
    found = {
        str(u.get("stock_id")): u
        for u in units.find({"stock_id": {"$in": ids}}, {"_id": 0})
    }
    gone = []
    for line in transfer.get("items") or []:
        codes = list(line.get("packed_barcodes") or [])
        for n, sid in enumerate(line.get("packed_stock_ids") or []):
            u = found.get(str(sid)) or {}
            free = u.get("status") == STOCK_STATUS_AVAILABLE and u.get("store_id") == from_store
            mine = u.get("status") == STOCK_STATUS_TRANSFERRED and u.get("transfer_id") == tid
            if not (free or mine):
                gone.append(codes[n] if n < len(codes) else str(sid))
    if gone:
        raise HTTPException(
            status_code=409,
            detail="Sold or moved since it was packed: " + ", ".join(gone)
            + ". Nothing has shipped; scan the box again.",
        )
    return found


def _apply_ship_stock_move(
    transfer: Dict,
    products: Optional[Dict[str, Dict]] = None,
    units: Optional[Dict[str, Dict]] = None,
) -> Dict:
    """Move EXACTLY the packed pieces out of the sending shop (S3).

    Each piece scanned into the box is claimed AVAILABLE -> TRANSFERRED with
    one conditional write (still this shop's, still AVAILABLE), tagged with
    the transfer. It used to claim whichever AVAILABLE units of the product
    the database returned first -- a random frame left the shop while the
    packed one stayed on the shelf (Fable review 2026-10-23).

    All or nothing: a piece lost to a sale between the check and the claim
    puts back the pieces this call claimed and refuses (409). Resumable: a
    piece already TRANSFERRED under this transfer (a ship that died after
    claiming it, before saving) counts as moved. Audit rows and ledger events
    are written only once every claim has landed.

    Idempotent on `stock_shipped`. `products`: the master rows ship read
    ONCE for its guard (_ship_products); `units`: the packed units it checked
    (_packed_units). The ship door runs the two halves itself
    (_claim_packed, then _record_ship once the transfer is saved).
    """
    if transfer.get("stock_shipped"):
        return transfer
    _record_ship(transfer, _claim_packed(transfer, products, units))
    return transfer


def _release_claims(tid: str, stock_ids: List[str], token: Optional[str] = None) -> None:
    """Put back on the sending shelf the pieces a ship claimed under `tid`
    (still TRANSFERRED under it) when that ship cannot finish -- only those
    still held by THIS press (`token`): a piece a later press took over for
    the same box is that press's to ship, never put back under it."""
    if stock_ids:
        where = {"stock_id": {"$in": list(stock_ids)}, "transfer_id": tid,
                 "status": STOCK_STATUS_TRANSFERRED}
        if token:
            where["ship_token"] = token
        _units_coll().update_many(
            where,
            {"$set": {"status": STOCK_STATUS_AVAILABLE, "transfer_id": None,
                      "transfer_to_store_id": None}},
        )


def _claim_packed(
    transfer: Dict,
    products: Optional[Dict[str, Dict]] = None,
    units: Optional[Dict[str, Dict]] = None,
    token: Optional[str] = None,
) -> List[str]:
    """The claim half of _apply_ship_stock_move: claim every packed piece
    (all or nothing) and stamp the lines with what left. Writes no audit,
    ledger or online stock; returns the stock_ids THIS call now holds, so a
    ship that cannot finish puts back exactly those (_release_claims).

    Each claim carries this press's `token` (stock_units.ship_token). A piece
    already TRANSFERRED under this transfer (an earlier press that died or is
    still running) is taken over by re-tagging it, so only ONE press holds
    each piece: the earlier press can no longer put it back, and this press
    writes its ship audit and ledger rows. The ship door takes the box first
    (_take_box), so an earlier press can never save over this one."""
    token = token or uuid.uuid4().hex
    products = products or {}
    units = units if units is not None else _packed_units(transfer)
    coll = _units_coll()
    tid = transfer.get("id")
    from_store = transfer.get("from_location_id")
    to_store = transfer.get("to_location_id")

    claimed_now: List[str] = []
    plan = []
    try:
        for line in transfer.get("items", []):
            ids = [str(s) for s in line.get("packed_stock_ids") or []]
            codes = list(line.get("packed_barcodes") or [])
            moved_units: List[Dict] = []
            for n, sid in enumerate(ids):
                unit = units.get(sid) or {}
                if unit.get("status") == STOCK_STATUS_TRANSFERRED and unit.get("transfer_id") == tid:
                    won = coll.find_one_and_update(
                        {"stock_id": sid, "status": STOCK_STATUS_TRANSFERRED, "transfer_id": tid},
                        {"$set": {"ship_token": token}},
                    )
                else:
                    won = coll.find_one_and_update(
                        {"stock_id": sid, "store_id": from_store, "status": STOCK_STATUS_AVAILABLE},
                        {"$set": {
                            "status": STOCK_STATUS_TRANSFERRED,
                            "transfer_id": tid,
                            "transferred_at": utc_now_iso(),
                            "transfer_to_store_id": to_store,
                            "ship_token": token,
                        }},
                    )
                if not won:
                    raise HTTPException(
                        status_code=409,
                        detail=f"{codes[n] if n < len(codes) else sid} was sold or moved "
                        "while the box was shipping. Nothing has shipped; scan the box again.",
                    )
                claimed_now.append(sid)
                moved_units.append(unit)
            plan.append((line, ids, moved_units))
    except Exception:
        # All or nothing: a lost claim (a sale landed between the check and
        # the claim) or a database error puts back what this call claimed.
        _release_claims(tid, claimed_now, token)
        raise

    moved_total = 0
    for line, ids, moved_units in plan:
        line["shipped_stock_ids"] = ids
        line["quantity_shipped"] = len(ids)
        _stamp_shipped_value(line, moved_units, products.get(line.get("product_id")) or {})
        moved_total += len(ids)

    transfer["stock_shipped"] = True
    transfer["stock_units_moved_out"] = moved_total
    # F51: the transfer is worth what left the shop, at the units' own cost.
    transfer["total_value"] = round(
        sum(_shipped_line_value(ln) for ln in transfer.get("items", [])), 2
    )
    return claimed_now


def _record_ship(transfer: Dict, claimed_now: List[str]) -> None:
    """The record half of a ship, written once it stands: one audit row and
    one ledger event per piece this ship claimed, and the sending shop's
    online stock goes down (owner ruling 2026-09-06)."""
    from_store = transfer.get("from_location_id")
    to_store = transfer.get("to_location_id")
    moved_pids: List[str] = []
    for line in transfer.get("items", []):
        product_id = line.get("product_id")
        for sid in line.get("shipped_stock_ids") or []:
            if sid in claimed_now:
                _audit_stock_move(
                    STOCK_STATUS_AVAILABLE, STOCK_STATUS_TRANSFERRED, sid, transfer,
                    {"product_id": product_id},
                )
                _ledger_transfer_event(
                    "TRANSFER_SHIP", sid, STOCK_STATUS_AVAILABLE, STOCK_STATUS_TRANSFERRED,
                    product_id, from_store, to_store, transfer,
                )
        if line.get("shipped_stock_ids") and product_id not in moved_pids:
            moved_pids.append(product_id)
    if moved_pids:
        _writeback_units_left(moved_pids, from_store, "transfer_ship")


def _first_cost(*candidates) -> float:
    """The first positive money value among `candidates`; 0.0 when none. Pure."""
    for raw in candidates:
        try:
            value = float(raw or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def _unit_cost(unit: Dict, product: Dict) -> float:
    """THE cost of one stock unit, read through THE stock cost rule
    (services/stock_value -- never computed here): the unit's own recorded
    cost, else its product's (a catalogue-only price is no cost). When S4
    (#1303) merges, that reader becomes the product's one average cost and
    this line follows it -- no second rule. One reader for the ship guard
    (which refuses a crossing move with a unit at 0), the ship stamp (which
    values the line by it) and a missing piece's write-off. Pure."""
    return stock_value.resolved_cost(unit, stock_value.master_fallback_cost(product))


def _ship_products(transfer: Dict) -> Dict[str, Dict]:
    """{product_id: master row} for a transfer's lines, read ONCE per ship,
    before any unit moves: the ship guard (_assert_valued_paper) and the ship
    stamp (_stamp_shipped_value) answer from these same rows, so a unit the
    guard lets leave is never stamped at 0 by a second read that failed. A
    read error refuses the ship (503) -- nothing has moved yet."""
    db = _get_db()
    pids = [ln.get("product_id") for ln in transfer.get("items") or [] if ln.get("product_id")]
    if db is None or not pids:
        return {}
    try:
        return {
            row["product_id"]: row
            for row in db.get_collection("products").find(
                {"product_id": {"$in": pids}},
                {"_id": 0, "product_id": 1, "hsn_code": 1, "category": 1, "cost_price": 1,
                 "cost_price_is_catalogue_only": 1},
            )
        }
    except Exception as exc:  # noqa: BLE001 - refuse, never guess
        logger.warning("[TRANSFER] product read for the ship failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="IMS could not read the products just now. Nothing has moved; try again.",
        ) from exc


def _stamp_shipped_value(line: Dict, units: List[Dict], product: Dict) -> None:
    """F51 / D13: stamp on a line what LEFT the shop -- the units' barcodes,
    the product's HSN, and the units' OWN cost (unit_cost / cost_price, stamped
    on each unit at its goods receipt or opening stock) averaged into the
    line's unit_cost. The product master's cost_price (`product`, the row
    _ship_products read before the guard) stands in only for a unit that
    carries no cost of its own (owner 2026-09-28: shelf units carry the
    product's cost); a unit with neither leaves the line at 0, so a valued
    challan refuses instead of printing a short value (a move between two
    registrations never gets here: _assert_valued_paper stops it). A line that
    shipped nothing keeps no client-typed figure (a BOPIS line carries the SALE
    price): its rate is the product's cost, else 0. The challan, the transfer's
    total_value and the FIN-3 mirror bill all price at this one rate through
    _line_value -- the challan on the units SHIPPED, the mirror bill on the
    units RECEIVED, so a short receipt books less than the challan shows (the
    bill records both: qty_basis + challan_value; the CA decides). No read
    of its own, so nothing can fail here after the units moved."""
    line["hsn_code"] = _line_hsn(line, product)
    line["shipped_barcodes"] = [str(u["barcode"]) for u in units if u.get("barcode")]
    costs = [_unit_cost(u, product) for u in units]
    # Each piece's own cost, frozen beside shipped_stock_ids (same order): a
    # credit note and a write-off value a lost piece at exactly this figure
    # (_piece_cost), never the line's average.
    line["shipped_unit_costs"] = costs
    # ponytail: one average rate per line for the challan / invoice line
    # (n x average == the sum of the pieces' costs, to the paisa).
    if costs:
        line["unit_cost"] = sum(costs) / len(costs) if all(costs) else 0.0
    else:
        line["unit_cost"] = _unit_cost({}, product)


def _line_hsn(line: Dict, product: Optional[Dict] = None) -> str:
    """THE HSN of a transfer line -- one rule for the ship stamp, the valued
    challan and the FIN-3 mirror bill (D13): the line's own (stamped at ship),
    else the product master's, else its category's. `product` is the master
    row when the caller already read it; None looks it up here, fail-soft.
    '' when nothing answers (a valued challan refuses that line)."""
    from ..services.gst_rates import hsn_for_category

    if product is None and not line.get("hsn_code"):
        db = _get_db()
        try:
            product = (
                db.get_collection("products").find_one(
                    {"product_id": line.get("product_id")},
                    {"_id": 0, "hsn_code": 1, "category": 1},
                )
                if db is not None and line.get("product_id")
                else None
            )
        except Exception as exc:  # noqa: BLE001 - fail-soft
            logger.warning("[TRANSFER] product lookup for the HSN failed: %s", exc)
    product = product if isinstance(product, dict) else {}
    return str(
        line.get("hsn_code")
        or product.get("hsn_code")
        or hsn_for_category(line.get("category") or product.get("category"))
        or ""
    ).strip()


def _line_value(qty, cost) -> float:
    """qty x the line's stamped cost, rounded to the paisa: THE line value the
    challan / total_value (qty shipped) and the FIN-3 mirror bill (qty
    received) share. Pure."""
    return round(_qty(qty) * _first_cost(cost), 2)


def _piece_cost(line: Dict, stock_id: str) -> float:
    """THE cost of one shipped piece: its own cost frozen at ship
    (shipped_unit_costs, beside shipped_stock_ids); a line shipped before
    that was frozen falls back to the line's average. One reader for the
    credit note and the write-off. Pure."""
    ids = [str(s) for s in line.get("shipped_stock_ids") or []]
    costs = line.get("shipped_unit_costs") or []
    if str(stock_id) in ids and len(costs) == len(ids):
        return _first_cost(costs[ids.index(str(stock_id))])
    return _first_cost(line.get("unit_cost"))


def _shipped_line_value(line: Dict) -> float:
    """What a shipped line is worth: units shipped x their own cost. Pure."""
    return _line_value(line.get("quantity_shipped"), line.get("unit_cost"))


def _rehome_patch(transfer: Dict, new_status: str, reason: str = "") -> Dict:
    """The write that lands a piece at the RECEIVING shop (scan in, or found
    there later). source_* becomes the transfer, but the unit's ORIGIN
    (grn_id, grn_line_index, line_unit_seq, po_id) is never touched: every
    "already received from this receipt" count reads it
    (vendors.grn_accept_lock._receipt_units). A damaged piece goes to
    QUARANTINE (BUG-009), never to the shelf."""
    patch = {
        "status": new_status,
        "store_id": transfer.get("to_location_id"),
        "received_at": utc_now_iso(),
        "source_type": "TRANSFER",
        "source_id": transfer.get("id"),
        "transfer_number": transfer.get("transfer_number"),
        "from_store_id": transfer.get("from_location_id"),
        "transfer_id": None,
        "transfer_to_store_id": None,
    }
    if new_status == STOCK_STATUS_QUARANTINED:
        patch["quarantine_reason"] = "TRANSFER_DAMAGED"
        patch["quarantined_at"] = utc_now_iso()
        if reason:
            patch["damage_reason"] = reason
    return patch


def _line_of_piece(transfer: Dict, stock_id: str) -> Dict:
    """The transfer line a piece left the shop on (its shipped_stock_ids);
    {} when it left on none."""
    for line in transfer.get("items") or []:
        if stock_id in [str(s) for s in line.get("shipped_stock_ids") or []]:
            return line
    return {}


def _landed(unit: Dict, transfer: Dict) -> bool:
    """The piece already landed at the RECEIVING shop from this transfer: the
    write a scan in or a found at the receiver makes (_rehome_patch, the only
    write that stamps source_id = this transfer). Whatever it did since --
    sold, reserved, out of quarantine, shipped on by another transfer -- it
    landed, so it is never stuck on "Missing on the way"."""
    return (
        unit.get("source_type") == "TRANSFER"
        and unit.get("source_id") == transfer.get("id")
        and unit.get("transfer_id") != transfer.get("id")
    )


def _landed_as(unit: Dict) -> str:
    """The state a landed piece arrived in: quarantine when it still sits
    there, else the shelf (it may have been sold or reserved since)."""
    return STOCK_STATUS_QUARANTINED if unit.get("status") == STOCK_STATUS_QUARANTINED else STOCK_STATUS_AVAILABLE


def _land_on_line(line: Dict, sid: str, status: str, reason: str = "") -> None:
    """Record a landed piece on its line: received, or damaged (quarantined)
    with its reason. Recording it twice counts once."""
    if sid not in [str(s) for s in (line.get("received_stock_ids") or []) + (line.get("damaged_stock_ids") or [])]:
        if status == STOCK_STATUS_QUARANTINED:
            line.setdefault("damaged_stock_ids", []).append(sid)
            line.setdefault("damage_reasons", {})[sid] = reason or "damaged"
            line["damage_notes"] = "; ".join(f"{v}" for v in line["damage_reasons"].values())
        else:
            line.setdefault("received_stock_ids", []).append(sid)
    _settle_line_counts(line)


def _settle_line_counts(line: Dict) -> None:
    """A line's received / damaged figures, derived from the pieces it
    recorded -- never a typed number."""
    received = list(dict.fromkeys(str(s) for s in line.get("received_stock_ids") or []))
    damaged = list(dict.fromkeys(str(s) for s in line.get("damaged_stock_ids") or []))
    line["received_stock_ids"] = received
    line["damaged_stock_ids"] = damaged
    line["quantity_received"] = len(received) + len(damaged)
    line["quantity_damaged"] = len(damaged)
    line["received_qty_committed"] = len(received) + len(damaged)


def _short_credit_note(transfer: Dict, stock_ids: List[str], reason: str) -> Optional[str]:
    """CA answers 2026-10-23: a SHORT delivery between two GSTINs is booked as
    the value actually received + a CREDIT NOTE for the difference. Books that
    note for `stock_ids` (pieces that never reached the receiving shop: lost
    and written off, or found back at the sender) and returns its number;
    None for a move inside one GSTIN (no GST paper).

    Exact to the paisa: each line's note is the invoice line's value at the
    pieces still counted as delivered BEFORE, minus its value AFTER -- each
    piece at its OWN cost frozen at ship (_piece_cost, the figure its
    write-off books), then split_line_gst at the line's FROZEN rate -- so the
    invoice plus all its notes equals the tax on the value received, head by
    head, and the pieces received plus the pieces written off equal what
    shipped.

    From the SENDING shop's "C" series of the one mint, dated today (IST).
    Booked in vendor_bills next to the invoice, negative, so the sender's
    GSTR-3B outward, the receiver's input credit and every other sum nets it
    (GSTR-1 files it under CDNR, gst_itc._transfer_cdnr_rows). Called BEFORE
    the piece is written off / put back; the doors check the piece is still
    on the way FIRST, so a refused door books no note. One note per piece
    (note_key, unique): pressing twice books one."""
    from ..services.purchase_invoice_engine import split_line_gst
    from ..services.writeoff_itc import after_bill_booked

    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="IMS cannot reach the books just now; try again.")
    _, _, crosses = _transfer_registrations(db, transfer)
    if not crosses:
        return None
    tid = transfer.get("id")
    bills = db.get_collection("vendor_bills")
    notes = list(bills.find({"source_transfer_id": tid, "doc_type": TRANSFER_CREDIT_NOTE}, {"_id": 0}))
    credited = {str(s) for n in notes for s in n.get("credited_stock_ids") or []}
    todo = [str(s) for s in stock_ids if str(s) not in credited]
    if not todo:
        hit = next((n for n in notes if str(stock_ids[0]) in (n.get("credited_stock_ids") or [])), None)
        if hit:
            after_bill_booked(db, hit)  # a press again finishes a true-up that failed
        return hit.get("bill_number") if hit else None
    _book_mirror_purchase(transfer)
    invoice = bills.find_one(
        {"source_transfer_id": tid, "doc_type": {"$ne": TRANSFER_CREDIT_NOTE}}, {"_id": 0}
    )
    if not invoice:
        raise HTTPException(
            status_code=503,
            detail="This transfer's tax invoice is not in the books, so IMS cannot "
            "raise its credit note. Nothing has changed; try again or ask accounts.",
        )
    interstate = bool(invoice.get("interstate"))
    frozen = {ln.get("item_id"): ln for ln in transfer.get("tax_invoice_lines") or [] if isinstance(ln, dict)}
    heads = ("taxable", "cgst", "sgst", "igst")
    lines: List[Dict] = []
    for item in transfer.get("items") or []:
        shipped = [str(s) for s in item.get("shipped_stock_ids") or []]
        lost = [s for s in todo if s in shipped]
        if not lost:
            continue
        inv = frozen.get(item.get("id"))
        if not inv:
            raise HTTPException(
                status_code=409,
                detail=f"{item.get('product_name') or item.get('product_id')} has no line on "
                "the transfer's tax invoice, so IMS cannot price its credit note. Ask accounts.",
            )
        rate = float(inv.get("gst_rate") or 0)
        gone_before = {s for s in shipped if s in credited}

        def _value(gone):
            # The invoice line at the pieces still counted delivered, each at
            # its own frozen cost; nothing credited yet = the frozen invoice.
            if not gone:
                return {h: float(inv.get(h) or 0) for h in heads}
            kept = round(sum(_piece_cost(item, s) for s in shipped if s not in gone), 2)
            return split_line_gst(kept, rate, interstate)

        was, now = _value(gone_before), _value(gone_before | set(lost))
        cost = sum(_piece_cost(item, s) for s in lost) / len(lost)
        part = {h: round(float(now[h]) - float(was[h]), 2) for h in heads}
        codes = dict(zip([str(s) for s in item.get("shipped_stock_ids") or []], item.get("shipped_barcodes") or []))
        lines.append({
            "product_id": item.get("product_id"),
            "description": item.get("product_name") or item.get("product_id"),
            "hsn": inv.get("hsn"),
            "qty": float(len(lost)),
            "unit_price": round(cost, 2),
            "gst_rate": rate,
            **part,
            "line_total": round(sum(part.values()), 2),
            "item_id": item.get("id"),
            "serials": [codes.get(s, s) for s in lost],
        })
    if not lines:
        return None
    total = {h: round(sum(ln[h] for ln in lines), 2) for h in heads}
    tax = round(total["cgst"] + total["sgst"] + total["igst"], 2)
    note_key = f"{tid}:{','.join(sorted(todo))}"
    try:
        number = next_document_number(db, transfer.get("from_location_id"), "C")
    except InvoiceNumberError as exc:
        raise HTTPException(
            status_code=400, detail=f"Cannot issue this transfer's credit note: {exc}"
        ) from exc
    today = _bill_date_ist(utc_now_iso())
    copied = (
        "from_store_id", "to_store_id", "store_id", "vendor_id", "vendor_name", "vendor_gstin",
        "entity_id", "recipient_entity_id", "recipient_name", "recipient_gstin",
        "place_of_supply", "supply_place_recipient", "interstate", "itc_eligible",
        "source_transfer_number",
    )
    doc = {
        **{k: invoice.get(k) for k in copied},
        "bill_id": f"mbcn_{uuid.uuid4().hex[:12]}",
        "doc_type": TRANSFER_CREDIT_NOTE,
        "note_key": note_key,
        "bill_number": number,
        "invoice_number": number,
        "bill_date": today,
        "invoice_date": today,
        "source_transfer_id": tid,
        "original_invoice_number": invoice.get("invoice_number"),
        "original_invoice_date": invoice.get("invoice_date"),
        "credited_stock_ids": sorted(todo),
        "reason": reason,
        "lines": lines,
        "taxable_amount": total["taxable"],
        "tax_amount": tax,
        "taxable_total": total["taxable"],
        "cgst_total": total["cgst"],
        "sgst_total": total["sgst"],
        "igst_total": total["igst"],
        "total_amount": round(total["taxable"] + tax, 2),
        "total": round(total["taxable"] + tax, 2),
        "qty_basis": "short_delivery",
        "itc_blocked": False,
        "status": "OUTSTANDING",
        "auto_generated": True,
        "notes": (
            f"Credit note on transfer tax invoice {invoice.get('invoice_number')} "
            f"({transfer.get('transfer_number', tid)}): {reason}."
        ),
        "created_by": "SYSTEM",
        "created_at": utc_now_iso(),
    }
    try:
        # ponytail: index made on first use; one note per piece set.
        bills.create_index("note_key", unique=True, sparse=True)
        bills.insert_one(doc)
    except Exception:  # noqa: BLE001 - a racing press booked it: answer that note
        landed = bills.find_one({"note_key": note_key}, {"_id": 0, "bill_number": 1})
        if not landed:
            raise
        return landed.get("bill_number")
    # Kept pieces written off BEFORE this note reversed slices of the gross
    # invoice line: true them up to the line net of the note (writeoff_itc).
    after_bill_booked(db, doc)
    return number


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

    # R4 (owner ruling 2026-10-12): a non-admin login with NO shop that names
    # a shop is refused (403 NO_SHOP_DETAIL); unnamed, _in_callers_stores
    # below already gives it no transfers.
    no_shop_scope_for(store_id or from_location_id or to_location_id, current_user)
    transfers = _in_callers_stores(transfers, current_user)

    # Sort by created date (newest first)
    transfers.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    total = len(transfers)
    start = (page - 1) * limit
    end = start + limit

    return {
        "transfers": [_caller_view(t, current_user) for t in transfers[start:end]],
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

    # IDOR guard: applied before the optional location_id narrowing so a
    # foreign location_id can never widen a store user's view.
    transfers = _in_callers_stores(transfers, current_user)

    if location_id:
        transfers = [
            t
            for t in transfers
            if t.get("from_location_id") == location_id
            or t.get("to_location_id") == location_id
        ]

    transfers = [_caller_view(t, current_user) for t in transfers]
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


@router.get("/free")
async def free_stock(
    product_id: str, store_id: str, current_user: dict = Depends(get_current_user)
):
    """How many pieces of a product another shop can be asked for (board
    Transfer: "Bokaro has 2 free, 1 more is reserved"). Counts only -- never
    a cost, never which pieces."""
    _require_roles(current_user, MISSING_ROLES)
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="IMS cannot reach the stock records just now.")
    free, reserved = _free_at(db, product_id, store_id)
    return {"product_id": product_id, "store_id": store_id, "free": free, "reserved": reserved}


@router.get("/missing")
async def list_missing(current_user: dict = Depends(get_current_user)):
    """"Missing on the way": every piece scanned OUT and never IN, until it is
    found or written off. Both shops' managers see their own transfers' (the
    one transfer store reach, _in_callers_stores); the admin sees all."""
    _require_roles(current_user, MISSING_ROLES)
    pieces = []
    for t in _in_callers_stores(_all_transfers(), current_user):
        for p in t.get("missing_pieces") or []:
            if isinstance(p, dict) and p.get("status") in MISSING_OPEN:
                pieces.append({
                    **p,
                    "transfer_id": t.get("id"),
                    "transfer_number": t.get("transfer_number"),
                    "from_location_id": t.get("from_location_id"),
                    "from_location_name": t.get("from_location_name"),
                    "to_location_id": t.get("to_location_id"),
                    "to_location_name": t.get("to_location_name"),
                })
    pieces.sort(key=lambda p: str(p.get("reported_at") or ""))
    return {"pieces": pieces}


@router.get("/{transfer_id}")
async def get_transfer(
    transfer_id: str, current_user: dict = Depends(get_current_user)
):
    """Get a single transfer with full details"""
    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")

    # IDOR guard: both parties (source + destination stores) may read.
    _assert_transfer_access(transfer, current_user, side="either")

    return {"transfer": _caller_view(transfer, current_user)}


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

    # IDOR guard (S3): a shop asks ANOTHER shop for stock (board Transfer), or
    # sends its own -- so the caller must reach one END of the move. Nothing
    # moves at create: an area manager of the sending side approves, and only
    # the sending shop packs and ships (side="source" on those doors).
    if not (
        can_access_store_scoped(transfer.from_location_id, current_user)
        or can_access_store_scoped(transfer.to_location_id, current_user)
    ):
        raise HTTPException(
            status_code=403,
            detail="You can ask for stock for your own shop, or send your own shop's stock.",
        )

    # Both ends are named: a blank end (an admin with no active shop asking
    # for stock) would make a transfer to nowhere only an admin could ever
    # receive.
    if not transfer.from_location_id.strip() or not transfer.to_location_id.strip():
        raise HTTPException(
            status_code=400,
            detail="Choose the shop the stock comes from and the shop it goes to.",
        )

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

    # W1.4 / OS-032: an ONLINE store sells the pooled stock of every physical
    # shop and owns none of its own -- stock must never be transferred INTO it
    # (receive would re-home real units onto the pooled store).
    if transfer.to_location_id and is_online_store(
        _get_db(), transfer.to_location_id
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "An online store sells pooled stock from every shop and "
                "cannot hold its own - choose a physical destination store."
            ),
        )

    # BUG FIX: at least one item must be in the transfer.
    if not transfer.items:
        raise HTTPException(
            status_code=400,
            detail="Transfer must contain at least one item",
        )

    # S3 (board Transfer): a request is capped at what is FREE at the sending
    # shop -- reserved pieces are promised to a customer and cannot be asked
    # for. Counted per product across lines.
    db = _get_db()
    if db is not None:
        want: Dict[str, int] = {}
        names: Dict[str, str] = {}
        for item in transfer.items:
            want[item.product_id] = want.get(item.product_id, 0) + item.quantity_requested
            names[item.product_id] = item.product_name or item.sku or item.product_id
        for pid, qty in want.items():
            free, reserved = _free_at(db, pid, transfer.from_location_id)
            if qty > free:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"{transfer.from_location_name or 'That shop'} has only {free} of "
                        f"{names[pid]} free"
                        + (
                            f" ({reserved} more reserved for customers - reserved pieces "
                            "cannot be asked for)"
                            if reserved
                            else ""
                        )
                        + "."
                    ),
                )

    # Owner ruling 2026-10-13: never to another company from a shop with no
    # GSTIN on file (asked again at ship, _gstin_gap).
    gap = _gstinless_sender(
        transfer.from_location_name,
        _shop_gst(db, transfer.from_location_id),
        _shop_gst(db, transfer.to_location_id),
    )
    if gap:
        raise HTTPException(status_code=400, detail=gap)

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
                "timestamp": utc_now_iso(),
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
            utc_now_iso()
            if initial_status == TransferStatus.APPROVED
            else None
        ),
        "shipped_at": None,
        "received_at": None,
        "completed_at": None,
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }

    # Create Shiprocket shipment if requested
    if transfer.create_shiprocket_shipment:
        # In production, would call Shiprocket API
        transfer_data["shiprocket_order_id"] = f"SR_{uuid.uuid4().hex[:8].upper()}"

    _save_transfer(transfer_data)

    return {
        "transfer": _caller_view(transfer_data, current_user),
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

    # IDOR guard: only someone scoped to the SOURCE store may edit the transfer.
    _assert_transfer_access(transfer, current_user, side="source")

    # Can only update certain statuses
    if transfer["status"] in [TransferStatus.COMPLETED, TransferStatus.CANCELLED]:
        raise HTTPException(
            status_code=400, detail="Cannot update completed or cancelled transfer"
        )

    update_data = update.model_dump(exclude_none=True)

    def _edit(t: Dict) -> None:
        # Only the typed fields, on a fresh read (_update_transfer): an e-way
        # bill no. saved while the box is being scanned never undoes a scan.
        if t.get("status") in (TransferStatus.COMPLETED.value, TransferStatus.CANCELLED.value):
            raise HTTPException(status_code=400, detail="Cannot update completed or cancelled transfer")
        t.update(update_data)
        t["updated_at"] = utc_now_iso()

    transfer = _update_transfer(transfer_id, _edit)
    return {"transfer": _caller_view(transfer, current_user), "message": "Transfer updated successfully"}


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

    # IDOR guard: approval authority follows the SOURCE store's chain.
    _assert_transfer_access(transfer, current_user, side="source")

    if transfer["status"] != TransferStatus.PENDING_APPROVAL:
        raise HTTPException(status_code=400, detail="Transfer is not pending approval")
    if not approval.approved and not (approval.rejection_reason or "").strip():
        raise HTTPException(status_code=400, detail="Give a reason to reject the request.")

    if approval.approved:
        new_status = TransferStatus.APPROVED
        message = "Transfer approved"
    else:
        new_status = TransferStatus.REJECTED
        message = "Transfer rejected"

    transfer["status"] = new_status
    transfer["approved_by"] = current_user.get("user_id")
    transfer["approved_at"] = utc_now_iso()
    transfer["rejection_reason"] = (
        approval.rejection_reason.strip() if not approval.approved else None
    )
    transfer["updated_at"] = utc_now_iso()

    _append_status_history(
        transfer,
        {
            "status": new_status,
            "timestamp": utc_now_iso(),
            "user_id": current_user.get("user_id"),
            "user_name": current_user.get("username"),
            "notes": approval.rejection_reason if not approval.approved else "Approved",
        },
    )

    _save_transfer(transfer)
    return {"transfer": _caller_view(transfer, current_user), "message": message}


@router.post("/{transfer_id}/pack")
async def pack_transfer(
    transfer_id: str,
    body: TransferPack,
    current_user: dict = Depends(get_current_user),
):
    """Pack the box: the pieces scanned OUT at the sending shop (S3, board
    TransferBox). The whole list each time -- the screen re-sends it on every
    scan, so the box on the server is always exactly what was scanned and a
    retry never double-packs; an empty list unpacks the box. Each piece must
    be this shop's, FREE (status AVAILABLE: never reserved, sold or in
    quarantine), a product this transfer asked for, within the quantity asked,
    and in no other open box. Nothing moves here: ship moves exactly these
    pieces (_apply_ship_stock_move). A piece a ship that died part-way
    already claimed for THIS box may be packed again (the next ship finishes
    it); one left out of the new box goes back on the shelf, so it is never
    stranded off both shelves."""
    _require_roles(current_user, SHIP_ROLES)
    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")
    _assert_transfer_access(transfer, current_user, side="source")
    status = str(transfer.get("status") or "").lower()
    if status == TransferStatus.PENDING_APPROVAL.value:
        raise HTTPException(
            status_code=400,
            detail="An area manager approves this request before it is packed.",
        )
    if status not in (
        TransferStatus.APPROVED.value, TransferStatus.PICKING.value, TransferStatus.PACKED.value
    ):
        raise HTTPException(
            status_code=400, detail="Only an approved box that has not shipped can be packed."
        )
    if body.rev is not None and body.rev != transfer.get("rev"):
        raise StaleTransfer()
    # Every line needs its own key (a BOPIS transfer's lines carry only a
    # transfer_item_id): the box, the invoice lines, the credit note and the
    # missing list all key pieces by line id. Stamped once, saved with the box.
    for line in transfer.get("items") or []:
        if not line.get("id"):
            line["id"] = line.get("transfer_item_id") or f"trfi_{uuid.uuid4().hex[:8]}"

    units = _units_coll()
    from_store = transfer.get("from_location_id")
    lines_by_pid: Dict[str, List[Dict]] = {}
    for line in transfer.get("items") or []:
        lines_by_pid.setdefault(line.get("product_id"), []).append(line)
    seen: List[str] = []
    picked: Dict[str, List[tuple]] = {}
    problems: List[str] = []
    for raw in body.barcodes:
        code = str(raw or "").strip()
        if not code:
            continue
        unit = find_unit_by_code(units, code)
        if not unit:
            problems.append(f"{code}: not found")
            continue
        sid = str(unit.get("stock_id") or unit.get("_id"))
        label = str(unit.get("barcode") or code)
        if sid in seen:
            problems.append(f"{label}: scanned twice")
            continue
        seen.append(sid)
        if unit.get("store_id") != from_store:
            problems.append(f"{label}: belongs to another shop")
            continue
        claimed_here = (
            unit.get("status") == STOCK_STATUS_TRANSFERRED and unit.get("transfer_id") == transfer_id
        )
        if unit.get("status") != STOCK_STATUS_AVAILABLE and not claimed_here:
            problems.append(
                f"{label}: not free to send (it is {str(unit.get('status') or 'unknown').lower()})"
            )
            continue
        line = next(
            (
                ln for ln in lines_by_pid.get(unit.get("product_id")) or []
                if len(picked.get(ln.get("id"), [])) < _qty(ln.get("quantity_requested"))
            ),
            None,
        )
        if line is None:
            problems.append(
                f"{label}: more pieces than were asked for"
                if unit.get("product_id") in lines_by_pid
                else f"{label}: this product was not asked for"
            )
            continue
        picked.setdefault(line.get("id"), []).append((sid, label))
    elsewhere = _packed_elsewhere(transfer_id, [s for got in picked.values() for s, _ in got])
    for got in picked.values():
        for sid, label in got:
            if sid in elsewhere:
                problems.append(f"{label}: already in another box ({elsewhere[sid]})")
    if problems:
        raise HTTPException(
            status_code=400,
            detail="These pieces cannot go in this box: " + "; ".join(problems),
        )

    packed = 0
    for line in transfer.get("items") or []:
        got = picked.get(line.get("id"), [])
        line["packed_stock_ids"] = [s for s, _ in got]
        line["packed_barcodes"] = [c for _, c in got]
        line["quantity_packed"] = len(got)
        packed += len(got)
    new_status = TransferStatus.PACKED if packed else TransferStatus.APPROVED
    if new_status.value != status:
        _append_status_history(
            transfer,
            {
                "status": new_status,
                "timestamp": utc_now_iso(),
                "user_id": current_user.get("user_id"),
                "user_name": current_user.get("username"),
                "notes": f"Packed {packed} pieces" if packed else "Box emptied",
            },
        )
    transfer["status"] = new_status
    transfer["packed_at"] = utc_now_iso() if packed else None
    transfer["packed_by"] = current_user.get("user_id") if packed else None
    transfer["updated_at"] = utc_now_iso()
    _save_transfer(transfer)
    # Only once this box is saved (still unshipped): a piece a ship that died
    # part-way claimed for it, and that is no longer in it, goes back.
    units.update_many(
        {"transfer_id": transfer_id, "status": STOCK_STATUS_TRANSFERRED,
         "stock_id": {"$nin": [s for got in picked.values() for s, _ in got]}},
        {"$set": {"status": STOCK_STATUS_AVAILABLE, "transfer_id": None,
                  "transfer_to_store_id": None}},
    )
    return {"transfer": _caller_view(transfer, current_user), "message": f"{packed} pieces in the box"}


@router.post("/{transfer_id}/ship")
async def ship_transfer(
    transfer_id: str,
    tracking_number: Optional[str] = None,
    tracking_url: Optional[str] = None,
    courier_name: Optional[str] = None,
    create_shiprocket: bool = False,
    current_user: dict = Depends(get_current_user),
):
    """Ship the packed box: the courier + tracking no. go on, and EXACTLY the
    pieces scanned out leave the sending shop (S3). Between two GSTINs the
    box travels on the tax invoice at cost (owner / CA 2026-10-18, numbered
    here once every piece is claimed, so a box that cannot ship takes no
    number) and that invoice is booked NOW, in the dispatch month (CA answers
    2026-10-23, _book_mirror_purchase)."""
    _require_roles(current_user, SHIP_ROLES)
    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")

    # IDOR guard: shipping moves REAL stock_units OUT of the SOURCE store --
    # only someone scoped to that store may trigger it.
    _assert_transfer_access(transfer, current_user, side="source")

    if transfer["status"] not in [
        TransferStatus.APPROVED, TransferStatus.PICKING, TransferStatus.PACKED
    ]:
        raise HTTPException(
            status_code=400,
            detail="Transfer must be approved or packed before shipping",
        )
    if not any(ln.get("packed_stock_ids") for ln in transfer.get("items") or []):
        raise HTTPException(
            status_code=409,
            detail="Scan the pieces into the box first: a box ships only the pieces "
            "scanned out.",
        )
    if not create_shiprocket and not (
        (courier_name or "").strip() and (tracking_number or "").strip()
    ):
        raise HTTPException(
            status_code=400, detail="Add the courier and the tracking no. before the box ships."
        )
    units = _packed_units(transfer)
    products = _ship_products(transfer)
    src, dst, crosses = _assert_valued_paper(transfer, products, units)
    # D13: the registrations the goods leave on, so the paper and the books
    # keep ship's answer (_transfer_registrations).
    transfer["gst_registrations"] = {
        "source": list(src), "destination": list(dst), "crosses": crosses,
    }

    if create_shiprocket:
        # In production, would call Shiprocket API
        shipment_id = f"SHP_{uuid.uuid4().hex[:8].upper()}"
        awb = f"AWB{uuid.uuid4().hex[:10].upper()}"
        transfer["shiprocket_shipment_id"] = shipment_id
        transfer["tracking_number"] = awb
        transfer["tracking_url"] = f"https://shiprocket.co/tracking/{awb}"
        transfer["courier_name"] = courier_name or "Delhivery"
    else:
        transfer["tracking_number"] = tracking_number.strip()
        transfer["tracking_url"] = tracking_url
        transfer["courier_name"] = courier_name.strip()

    # The box is taken, then the pieces are claimed FIRST, so a box that
    # cannot ship (a piece sold meanwhile) never takes a tax invoice number.
    # Anything that stops the ship after the claims puts back the pieces this
    # press still holds -- never one another press shipped.
    tid = transfer.get("id")
    token = uuid.uuid4().hex
    try:
        _take_box(transfer)
    except StaleTransfer:
        done = _shipped_already(tid)
        if done:
            return _shipped_reply(done, current_user)
        raise
    claimed = _claim_packed(transfer, products, units, token)
    try:
        if crosses:
            # The tax invoice travels with the goods (owner / CA 2026-10-18),
            # dated at this dispatch -- a number a failed ship already took
            # is re-dated here, never left on that earlier press's day.
            try:
                _transfer_tax_invoice(transfer)
            except InvoiceNumberError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot issue this transfer's tax invoice: {exc}",
                ) from exc
            transfer["tax_invoice_date"] = utc_now_iso()
        transfer["status"] = TransferStatus.IN_TRANSIT
        transfer["shipped_at"] = utc_now_iso()
        transfer["shipped_by"] = current_user.get("user_id")
        transfer["updated_at"] = utc_now_iso()
        for item in transfer["items"]:
            item["status"] = "in_transit"
        if transfer.get("tax_invoice_number"):
            # The units that left, at their stamped cost and today's HSN /
            # rate: the invoice the number names, frozen at THIS ship.
            transfer["tax_invoice_lines"] = None
            _freeze_tax_invoice_lines(transfer)

        _append_status_history(
            transfer,
            {
                "status": TransferStatus.IN_TRANSIT,
                "timestamp": utc_now_iso(),
                "user_id": current_user.get("user_id"),
                "user_name": current_user.get("username"),
                "notes": "Shipped"
                + (f" via {transfer['courier_name']}" if transfer.get("courier_name") else ""),
            },
        )

        _save_transfer(transfer)
    except Exception as exc:
        done = _shipped_already(tid) if isinstance(exc, StaleTransfer) else None
        shipped = {str(s) for ln in (done or {}).get("items") or [] for s in ln.get("shipped_stock_ids") or []}
        _release_claims(tid, [s for s in claimed if s not in shipped], token)
        if done:
            return _shipped_reply(done, current_user)  # another press shipped it
        raise
    _record_ship(transfer, claimed)
    # CA 2026-10-23: the supply is filed in the DISPATCH month. Idempotent and
    # fail-soft; Complete books it again if this write did not land.
    _book_mirror_purchase(transfer)
    return _shipped_reply(transfer, current_user)


def _shipped_already(transfer_id: str) -> Optional[Dict]:
    """The stored transfer when another press has already shipped it."""
    stored = _get_transfer(transfer_id)
    return stored if stored and stored.get("stock_shipped") else None


def _shipped_reply(transfer: Dict, current_user: dict) -> Dict:
    return {
        "transfer": _caller_view(transfer, current_user),
        "message": "Transfer shipped",
        "tracking": {
            "number": transfer.get("tracking_number"),
            "url": transfer.get("tracking_url"),
        },
    }


@router.post("/{transfer_id}/receive")
async def receive_transfer(
    transfer_id: str,
    body: TransferPieceReceive,
    current_user: dict = Depends(get_current_user),
):
    """Scan ONE piece IN at the receiving shop (S3, board TransferBox). Only a
    piece that left in this box lands: it is re-homed to this shop, on the
    shelf -- or, damaged in the box, in QUARANTINE (with the reason), never
    on the shelf. The same piece scanned twice counts once. A piece that is
    never scanned in goes on "Missing on the way" at Complete."""
    _require_roles(current_user, RECEIVE_ROLES)
    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")

    # IDOR guard: receiving re-homes REAL stock_units INTO the DESTINATION.
    _assert_transfer_access(transfer, current_user, side="dest")

    # W1.4 / OS-032 (belt and braces): never land owned stock on the pooled,
    # stockless ONLINE store, even for a legacy transfer created before the
    # create-time guard existed.
    if transfer.get("to_location_id") and is_online_store(
        _get_db(), transfer.get("to_location_id")
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "The destination is an online store, which sells pooled stock "
                "and holds none of its own - it cannot receive a transfer. "
                "Re-route these units to a physical shop."
            ),
        )
    if transfer["status"] not in [TransferStatus.IN_TRANSIT, TransferStatus.PARTIALLY_RECEIVED]:
        raise HTTPException(status_code=400, detail="Transfer must be in transit to receive")
    reason = (body.damage_reason or "").strip()
    if body.damaged and not reason:
        raise HTTPException(status_code=400, detail="Say what is wrong with the damaged piece.")

    units = _units_coll()
    code = body.barcode.strip()
    unit = find_unit_by_code(units, code)
    sid = str(unit.get("stock_id")) if unit else ""
    line = _line_of_piece(transfer, sid) if sid else {}
    if not line:
        raise HTTPException(status_code=400, detail=f"{code} is not in this box.")
    label = str(unit.get("barcode") or code)
    settled = [str(s) for s in (line.get("received_stock_ids") or []) + (line.get("damaged_stock_ids") or [])]
    if sid in settled:
        return {
            "transfer": _caller_view(transfer, current_user),
            "message": f"{label} is already scanned in",
            "piece": {"barcode": label, "status": "already"},
        }

    tid = transfer.get("id")
    new_status = STOCK_STATUS_QUARANTINED if body.damaged else STOCK_STATUS_AVAILABLE
    res = units.update_one(
        {"stock_id": sid, "status": STOCK_STATUS_TRANSFERRED, "transfer_id": tid},
        {"$set": _rehome_patch(transfer, new_status, reason)},
    )
    if not int(getattr(res, "modified_count", 0) or 0):
        now = units.find_one({"stock_id": sid}) or {}
        if not _landed(now, transfer):
            raise HTTPException(
                status_code=409, detail=f"{label} is no longer on the way to this shop."
            )
        new_status = _landed_as(now)  # a scan that died after the write, pressed again
    _audit_stock_move(
        STOCK_STATUS_TRANSFERRED, new_status, sid, transfer,
        {"product_id": line.get("product_id"), "moved_to": transfer.get("to_location_id"),
         **({"reason": "TRANSFER_DAMAGED", "damage_reason": reason} if body.damaged else {})},
    )
    _ledger_transfer_event(
        "TRANSFER_RECEIVE", sid, STOCK_STATUS_TRANSFERRED, new_status,
        line.get("product_id"), transfer.get("to_location_id"), None, transfer,
    )

    def _record(t: Dict) -> None:
        # Re-read on every try (_update_transfer): two tablets scanning two
        # pieces of one box both land.
        ln = _line_of_piece(t, sid)
        if t.get("status") not in (
            TransferStatus.IN_TRANSIT.value, TransferStatus.PARTIALLY_RECEIVED.value,
            TransferStatus.RECEIVED.value,
        ):
            if sid in [str(s) for s in (ln.get("received_stock_ids") or []) + (ln.get("damaged_stock_ids") or [])]:
                return  # Accept already recorded it from the unit (complete_transfer)
            where = "in this shop's quarantine" if new_status == STOCK_STATUS_QUARANTINED else "on this shop's shelf"
            raise HTTPException(
                status_code=409,
                detail=f"{label} is {where}, but the box was accepted meanwhile: on "
                "'Missing on the way', mark it Found at this shop.",
            )
        _land_on_line(ln, sid, new_status, reason)
        ln["status"] = "received"
        ln["received_at"] = utc_now_iso()
        complete = all(
            _qty(x.get("quantity_received")) >= len(x.get("shipped_stock_ids") or [])
            for x in t.get("items") or []
        )
        new_tstatus = TransferStatus.RECEIVED if complete else TransferStatus.PARTIALLY_RECEIVED
        if t.get("status") != new_tstatus.value:
            _append_status_history(
                t,
                {
                    "status": new_tstatus,
                    "timestamp": utc_now_iso(),
                    "user_id": current_user.get("user_id"),
                    "user_name": current_user.get("username"),
                    "notes": "Every piece scanned in" if complete else "Scanning in",
                },
            )
        t["status"] = new_tstatus
        t["received_at"] = utc_now_iso()
        t["received_by"] = current_user.get("user_id")
        t["total_received"] = sum(_qty(x.get("quantity_received")) for x in t["items"])
        t["total_damaged"] = sum(_qty(x.get("quantity_damaged")) for x in t["items"])
        t["updated_at"] = utc_now_iso()

    transfer = _update_transfer(transfer_id, _record)
    return {
        "transfer": _caller_view(transfer, current_user),
        "message": f"{label} received" + (" damaged, to quarantine" if body.damaged else ""),
        "piece": {"barcode": label, "status": "damaged" if body.damaged else "received"},
    }


@router.post("/{transfer_id}/complete")
async def complete_transfer(
    transfer_id: str,
    notes: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Accept the box at the receiving shop. Every piece that left in it and
    was not scanned in goes on "Missing on the way" -- still tracked against
    this transfer (TRANSFERRED, at neither shelf) until it is found or an
    admin writes it off -- so a partial receipt never strands a piece (Fable
    review 2026-10-23). The tax invoice was booked at ship; booking it again
    here is a no-op unless that write did not land."""
    _require_roles(current_user, ["SUPERADMIN", "ADMIN", "STORE_MANAGER"])
    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")

    # IDOR guard: completion is the DESTINATION's sign-off that goods arrived.
    _assert_transfer_access(transfer, current_user, side="dest")

    if transfer["status"] not in [
        TransferStatus.IN_TRANSIT,
        TransferStatus.RECEIVED,
        TransferStatus.PARTIALLY_RECEIVED,
    ]:
        raise HTTPException(
            status_code=400, detail="Transfer must be received before completion"
        )

    gone: List[tuple] = []

    def _accept(t: Dict) -> None:
        # Re-read on every try (_update_transfer): a scan saved meanwhile is
        # received, never listed missing.
        if t.get("status") not in (
            TransferStatus.IN_TRANSIT.value, TransferStatus.RECEIVED.value,
            TransferStatus.PARTIALLY_RECEIVED.value,
        ):
            raise HTTPException(status_code=400, detail="Transfer must be received before completion")
        known = {str(p.get("stock_id")) for p in t.get("missing_pieces") or []}
        gone.clear()
        for line in t.get("items") or []:
            settled = {str(s) for s in (line.get("received_stock_ids") or []) + (line.get("damaged_stock_ids") or [])}
            for sid in line.get("shipped_stock_ids") or []:
                if str(sid) not in settled and str(sid) not in known:
                    gone.append((str(sid), line))
        found_units: Dict[str, Dict] = {}
        if gone:
            found_units = {
                str(u.get("stock_id")): u
                for u in _units_coll().find({"stock_id": {"$in": [s for s, _ in gone]}}, {"_id": 0})
            }
        # A piece whose scan in landed on the unit but not on the box (a scan
        # racing this Accept, or a process that died between the two writes)
        # is received / damaged as its unit says, never "missing".
        for sid, line in list(gone):
            u = found_units.get(sid) or {}
            if _landed(u, t):
                _land_on_line(line, sid, u.get("status"), u.get("damage_reason") or "")
                gone.remove((sid, line))
        codes = {sid: str(u.get("barcode") or "") for sid, u in found_units.items()}
        now = utc_now_iso()
        missing = list(t.get("missing_pieces") or [])
        for sid, line in gone:
            missing.append({
                "stock_id": sid,
                "barcode": codes.get(sid) or sid,
                "item_id": line.get("id"),
                "product_id": line.get("product_id"),
                "product_name": line.get("product_name"),
                "status": "missing",
                "reported_at": now,
                "reported_by": current_user.get("user_id"),
            })
        t["missing_pieces"] = missing
        t["status"] = TransferStatus.COMPLETED
        t["completed_at"] = now
        t["completed_by"] = current_user.get("user_id")
        t["completion_notes"] = notes
        t["updated_at"] = now
        _append_status_history(
            t,
            {
                "status": TransferStatus.COMPLETED,
                "timestamp": now,
                "user_id": current_user.get("user_id"),
                "user_name": current_user.get("username"),
                "notes": notes
                or (f"Accepted; {len(gone)} missing on the way" if gone else "Transfer completed"),
            },
        )

    transfer = _update_transfer(transfer_id, _accept)
    damaged = sum(_qty(ln.get("quantity_damaged")) for ln in transfer.get("items") or [])
    if gone or damaged:
        task_id = _create_receive_mismatch_task(
            transfer, {"short": len(gone), "surplus": 0, "damaged": damaged}
        )
        if task_id:
            def _link(t: Dict) -> None:
                t["discrepancy_task_id"] = task_id

            transfer = _update_transfer(transfer_id, _link)
    _book_mirror_purchase(transfer)
    return {"transfer": _caller_view(transfer, current_user), "message": "Transfer completed"}


def _transfer_has_moved_stock(transfer: Dict) -> bool:
    """Ground-truth check: has this transfer ALREADY moved real units OUT?

    Returns True iff the stock_units collection holds at least one unit stamped
    with this transfer's id and parked in TRANSFERRED status -- the exact stamp
    `claim_for_transfer` writes per unit at ship time (see _apply_ship_stock_move
    -> claim_for_transfer, which sets {status: TRANSFERRED, transfer_id: <id>}).

    Why this and not the transfer doc's own fields: ship flips units to
    TRANSFERRED and only THEN sets `stock_shipped` / `shipped_stock_ids` in memory
    and persists the doc. If the process dies between the per-unit claim writes
    (already committed to stock_units) and _save_transfer, the persisted doc is
    left at a PRE-SHIP status with NO stock_shipped flag and NO shipped_stock_ids
    -- so those doc fields cannot detect the orphan. The stock_units rows are the
    only durable evidence, so we query them directly.

    Fail-soft: no stock repo (DB down / no stock backend) or any query error ->
    False, so a normal cancel is never blocked when we can't positively SEE moved
    units (preserves the pre-existing no-stock-backend behavior). The guard only
    bites when it can prove units already left the floor. Pure read -- writes
    nothing.
    """
    stock_repo = get_stock_repository()
    if stock_repo is None:
        return False
    tid = transfer.get("id")
    if not tid:
        return False
    query = {"transfer_id": tid, "status": STOCK_STATUS_TRANSFERRED}
    try:
        try:
            rows = stock_repo.find_many(query, limit=1)
        except TypeError:
            # Some repo/mocks don't accept limit= -> call without it.
            rows = stock_repo.find_many(query)
    except Exception as exc:  # noqa: BLE001 - fail-soft, never block on a read error
        logger.warning("[TRANSFER] cancel stock-move guard lookup failed: %s", exc)
        return False
    return bool(rows)


@router.post("/{transfer_id}/cancel")
async def cancel_transfer(
    transfer_id: str, reason: str, current_user: dict = Depends(get_current_user)
):
    """Cancel a transfer"""
    _require_roles(current_user, CANCEL_ROLES)
    if not (reason or "").strip():
        raise HTTPException(status_code=400, detail="Give a reason to cancel the transfer.")

    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")

    # IDOR guard (S3): either end may call off a box that has not shipped --
    # the asking shop withdraws its request, the sending shop declines to
    # send. Nothing has moved before ship (the allowlist below).
    _assert_transfer_access(transfer, current_user, side="either")

    # ALLOWLIST guard (PR #959 round 2): cancellable ONLY from a pre-ship
    # status. The previous denylist (completed/cancelled/in_transit) still let
    # a transfer be cancelled at RECEIVED / PARTIALLY_RECEIVED -- after the
    # units had already left the source and been re-homed at the destination.
    # That cancel performed no stock reversal and permanently skipped the
    # deemed-supply GST mirror invoice (booked only at /complete). Status is
    # normalized case-insensitively (legacy writers stamp "DRAFT"/"pending").
    status_norm = str(transfer.get("status") or "").strip().lower()
    if status_norm not in CANCELLABLE_TRANSFER_STATUSES:
        if status_norm in (
            TransferStatus.COMPLETED.value,
            TransferStatus.CANCELLED.value,
        ):
            raise HTTPException(
                status_code=400,
                detail="Cannot cancel completed or already cancelled transfer",
            )
        if status_norm == TransferStatus.IN_TRANSIT.value:
            raise HTTPException(
                status_code=400, detail="Cannot cancel transfer that is in transit"
            )
        raise HTTPException(
            status_code=400,
            detail=(
                "Cannot cancel a transfer after its stock has shipped - the "
                "units have already moved. Receive and complete it, then move "
                "the goods back with a reverse transfer if needed."
            ),
        )

    # DEFENSE-IN-DEPTH (crash-window stock integrity): the allowlist above trusts
    # the persisted transfer STATUS, but ship moves real stock_units OUT *before*
    # it persists status=IN_TRANSIT / stock_shipped (ship_transfer ->
    # _apply_ship_stock_move claims units, THEN _save_transfer). If the process
    # dies in that window, the doc is left at a pre-ship status (packed/approved)
    # with NO stock_shipped flag -- yet those units are already TRANSFERRED in the
    # stock_units collection. The allowlist would then WRONGLY accept a cancel,
    # which does NO stock reversal and skips the GST deemed-supply mirror,
    # stranding the units as TRANSFERRED forever. The doc fields we'd normally
    # trust are the exact ones lost in the crash, so we consult the stock_units
    # GROUND TRUTH instead. If any unit already moved, refuse LOUDLY -- do not
    # self-heal by reversing stock in the cancel path (higher blast radius; "fail
    # loudly" and surface for reconciliation). Raised BEFORE any mutation/save, so
    # a blocked cancel writes nothing. Fail-soft when no stock backend is visible.
    if _transfer_has_moved_stock(transfer):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "transfer_stock_already_moved",
                "message": (
                    "Stock has already left the source store for this transfer "
                    "(units are marked TRANSFERRED). It cannot be cancelled -- "
                    "receive and complete it, or reconcile the moved units, then "
                    "reverse with a new transfer if needed."
                ),
            },
        )

    transfer["status"] = TransferStatus.CANCELLED
    transfer["cancelled_at"] = utc_now_iso()
    transfer["cancelled_by"] = current_user.get("user_id")
    transfer["cancellation_reason"] = reason
    transfer["updated_at"] = utc_now_iso()

    _append_status_history(
        transfer,
        {
            "status": TransferStatus.CANCELLED,
            "timestamp": utc_now_iso(),
            "user_id": current_user.get("user_id"),
            "user_name": current_user.get("username"),
            "notes": reason,
        },
    )

    _save_transfer(transfer)

    # Back-sync a linked endless-aisle request (feature #38). Cancelling the
    # transfer must not strand its EA request at TRANSFER_CREATED, from which the
    # decorative EA ship/deliver endpoints could still march it to DELIVERED. The
    # EA service flips the request ONLY when CANCELLED is a legal transition
    # (pre-ship states) and NEVER raises, so this is a pure, fail-soft status
    # back-sync -- it moves no stock and books no GST. Lazy import avoids an
    # endless_aisle <-> transfers import cycle. Best-effort: a failure here must
    # NOT turn a committed cancel into an error.
    ea_request_id = transfer.get("endless_aisle_request_id")
    if ea_request_id:
        try:
            from ..services import endless_aisle as _ea

            _ea.cancel_linked_request(_get_db(), ea_request_id, actor=current_user)
        except Exception:  # noqa: BLE001 - back-sync is non-blocking
            logger.warning(
                "[TRANSFER] endless-aisle back-sync failed for request %s",
                ea_request_id,
            )

    return {"transfer": _caller_view(transfer, current_user), "message": "Transfer cancelled"}


# ============================================================================
# MISSING ON THE WAY  (S3)
# ============================================================================
# A piece scanned OUT and never IN stays TRANSFERRED against its transfer --
# at neither shelf, on nobody's sellable stock -- and on the "Missing on the
# way" list both managers and the admin see, until it is FOUND (it lands on
# the shelf where it turned up) or an ADMIN writes it off (owner ruling
# 2026-08-25: every write-off is admin-only). Between two GSTINs, a piece that
# never reached the receiving shop (written off, or found back at the
# sender) gets its CREDIT NOTE first (_short_credit_note); a piece found at
# the receiving shop was delivered, so the invoice already covers it.


def _open_missing(transfer: Dict, stock_id: str) -> Dict:
    for piece in transfer.get("missing_pieces") or []:
        if isinstance(piece, dict) and str(piece.get("stock_id")) == stock_id:
            if piece.get("status") not in MISSING_OPEN:
                raise HTTPException(
                    status_code=409,
                    detail=f"{piece.get('barcode') or stock_id} is no longer missing "
                    f"({str(piece.get('status') or '').replace('_', ' ')}).",
                )
            return piece
    raise HTTPException(status_code=404, detail="That piece is not missing on this transfer.")


@router.post("/{transfer_id}/missing/{stock_id}/found")
async def found_missing_piece(
    transfer_id: str,
    stock_id: str,
    body: MissingFound,
    current_user: dict = Depends(get_current_user),
):
    """A missing piece turned up -- at the receiving shop (it lands on that
    shelf, delivered late) or back at the sending shop (it goes back on that
    shelf; between two GSTINs its credit note is booked first, since it was
    never delivered). Only a manager of the shop where it turned up (or an
    admin) can say so."""
    _require_roles(current_user, MISSING_ROLES)
    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")
    at_source = body.at == "source"
    _assert_transfer_access(transfer, current_user, side="source" if at_source else "dest")
    piece = _open_missing(transfer, stock_id)
    store = transfer.get("from_location_id") if at_source else transfer.get("to_location_id")
    units = _units_coll()
    tid = transfer.get("id")

    def _found_here(u: Dict) -> bool:  # already where this door puts it
        if not at_source:
            return _landed(u, transfer)  # on the shelf, or damaged in quarantine
        # The lasting stamp of the move back: whatever the piece did since
        # (sold, reserved, packed again) a retry still books its note.
        return u.get("found_back_from_transfer_id") == tid

    patch = (
        {"status": STOCK_STATUS_AVAILABLE, "transfer_id": None, "transfer_to_store_id": None,
         "found_back_from_transfer_id": tid}
        if at_source
        else _rehome_patch(transfer, STOCK_STATUS_AVAILABLE)
    )
    res = units.update_one(
        {"stock_id": stock_id, "status": STOCK_STATUS_TRANSFERRED, "transfer_id": tid},
        {"$set": patch},
    )
    # The unit moves FIRST (guarded: still on the way under this transfer),
    # THEN the note: a piece that landed elsewhere meanwhile (found at the
    # receiver on another worker) is refused with no note booked. A note that
    # fails after the move is booked by pressing again (_found_here; one note
    # per piece, note_key).
    unit = units.find_one({"stock_id": stock_id}) or {}
    if not int(getattr(res, "modified_count", 0) or 0):
        if not _found_here(unit):
            raise HTTPException(
                status_code=409, detail=f"{piece.get('barcode') or stock_id} is no longer on the way."
            )
    note = (
        _short_credit_note(transfer, [stock_id], "piece found back at the sending shop, never delivered")
        if at_source
        else None
    )

    def _resolve(t: Dict) -> None:
        p = _open_missing(t, stock_id)
        if not at_source:
            line = _line_of_piece(t, stock_id)
            if line:
                _land_on_line(line, stock_id, unit.get("status"), unit.get("damage_reason") or "")
        p.update({
            "status": "found",
            "found_side": body.at,
            "found_at_store_id": store,
            "resolved_at": utc_now_iso(),
            "resolved_by": current_user.get("user_id"),
            **({"credit_note_number": note} if note else {}),
        })
        t["updated_at"] = utc_now_iso()

    transfer = _update_transfer(transfer_id, _resolve)
    landed_as = _landed_as(unit)  # QUARANTINED: found damaged
    _audit_stock_move(
        STOCK_STATUS_TRANSFERRED, landed_as, stock_id, transfer,
        {"product_id": piece.get("product_id"), "moved_to": store, "reason": "MISSING_FOUND"},
    )
    _ledger_transfer_event(
        "TRANSFER_RECEIVE", stock_id, STOCK_STATUS_TRANSFERRED, landed_as,
        piece.get("product_id"), store, None, transfer,
    )
    return {"transfer": _caller_view(transfer, current_user), "message": "Found", "credit_note_number": note}


@router.post("/{transfer_id}/missing/{stock_id}/write-off-request")
async def request_missing_write_off(
    transfer_id: str, stock_id: str, current_user: dict = Depends(get_current_user)
):
    """A manager of either shop sends a missing piece to the admin for
    write-off (only an admin writes stock off, owner ruling 2026-08-25)."""
    _require_roles(current_user, MISSING_ROLES)
    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")
    _assert_transfer_access(transfer, current_user, side="either")
    if _open_missing(transfer, stock_id).get("status") != "write_off_requested":
        def _ask(t: Dict) -> None:
            p = _open_missing(t, stock_id)
            if p.get("status") != "write_off_requested":
                p.update({
                    "status": "write_off_requested",
                    "write_off_requested_by": current_user.get("user_id"),
                    "write_off_requested_by_name": current_user.get("username"),
                    "write_off_requested_at": utc_now_iso(),
                })
                t["updated_at"] = utc_now_iso()

        transfer = _update_transfer(transfer_id, _ask)
    return {"transfer": _caller_view(transfer, current_user), "message": "Sent to the admin for write-off"}


@router.post("/{transfer_id}/missing/{stock_id}/write-off")
async def write_off_missing_piece(
    transfer_id: str, stock_id: str, current_user: dict = Depends(get_current_user)
):
    """ADMIN / SUPERADMIN write a missing piece off (owner ruling 2026-08-25:
    every write-off is admin-only). The loss is the SENDING shop's: the piece
    is VOIDed there (it never left its books), with a stock_shrinkage row at
    the piece's own cost. Between two GSTINs its credit note is booked first
    (CA 2026-10-23: short delivery = value received + credit note)."""
    from .inventory._shared import is_write_off_admin

    if not is_write_off_admin(current_user):
        raise HTTPException(
            status_code=403,
            detail="Only an admin writes stock off. Send it to the admin for write-off.",
        )
    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")
    piece = _open_missing(transfer, stock_id)
    units = _units_coll()
    tid = transfer.get("id")
    reason_tag = f"transfer-missing:{tid}"
    now = utc_now_iso()
    # The unit is written off FIRST (guarded: still on the way under this
    # transfer), THEN the note: a piece that landed meanwhile (found at the
    # receiver on another worker) is refused with no note booked. A note that
    # fails after the write-off is booked by pressing again (the VOID with
    # this reason_tag; one note per piece, note_key).
    res = units.update_one(
        {"stock_id": stock_id, "status": STOCK_STATUS_TRANSFERRED, "transfer_id": tid},
        {"$set": {"status": STOCK_STATUS_VOID, "voided_at": now, "void_reason": reason_tag,
                  "transfer_id": None, "transfer_to_store_id": None, "void_transfer_id": tid}},
    )
    if not int(getattr(res, "modified_count", 0) or 0):
        unit = units.find_one({"stock_id": stock_id}) or {}
        if not (unit.get("status") == STOCK_STATUS_VOID and unit.get("void_reason") == reason_tag):
            raise HTTPException(
                status_code=409, detail=f"{piece.get('barcode') or stock_id} is no longer on the way."
            )
    db = _get_db()
    # Section 17(5)(h): the SENDER's input credit on the lost piece is taken
    # back (#1302's one write-off rule, once per piece, so pressing again is
    # safe). Read AFTER the VOID: transfer_id is cleared, so the rule picks
    # the bill that put the piece on the sender's GSTIN -- never this box's
    # own tax invoice, which the credit note nets for the receiver.
    from ..services.writeoff_itc import record_writeoff

    itc = record_writeoff(
        db, [units.find_one({"stock_id": stock_id}) or {"stock_id": stock_id}],
        source_type="TRANSFER_MISSING", source_id=tid, when=now,
        actor_id=current_user.get("user_id", ""),
    )
    note = _short_credit_note(transfer, [stock_id], "piece lost on the way, written off")
    shrink = db.get_collection("stock_shrinkage")
    if not shrink.find_one({"transfer_id": tid, "voided_stock_ids": stock_id}):
        line = _line_of_piece(transfer, stock_id)
        # The same frozen figure the credit note credited (_piece_cost).
        piece_cost = round(_piece_cost(line, stock_id), 2)
        shrink.insert_one({
            "shrinkage_id": str(uuid.uuid4()),
            "source": "TRANSFER_MISSING",
            "transfer_id": tid,
            "transfer_number": transfer.get("transfer_number"),
            "store_id": transfer.get("from_location_id"),
            "product_id": piece.get("product_id"),
            "product_name": piece.get("product_name"),
            "sku": line.get("sku"),
            "shrinkage_quantity": 1,
            "units_voided": 1,
            "units_not_voided": 0,
            "voided_stock_ids": [stock_id],
            "missing_barcodes": [piece.get("barcode")],
            "unit_cost": piece_cost,
            "shrinkage_value": piece_cost,
            "credit_note_number": note,
            "itc_reversed": itc,
            "write_off_requested_by": piece.get("write_off_requested_by"),
            "recorded_at": now,
            "recorded_by": current_user.get("user_id"),
        })
    try:
        from ..services import item_events as ie

        ie.record_post_write_event(
            db, event_type=ie.ItemEventType.VOID, actor_id=current_user.get("user_id", ""),
            stock_id=stock_id, from_state=ie.StockState.TRANSFERRED, to_state=ie.StockState.VOID,
            store_id=transfer.get("from_location_id"), product_id=piece.get("product_id"),
            source_type="TRANSFER", source_id=tid, payload={"void_reason": reason_tag},
        )
    except Exception as exc:  # noqa: BLE001 - the ledger is additive
        logger.warning("[TRANSFER] write-off ledger row failed: %s", exc)
    def _resolve(t: Dict) -> None:
        _open_missing(t, stock_id).update({
            "status": "written_off",
            "resolved_at": now,
            "resolved_by": current_user.get("user_id"),
            **({"credit_note_number": note} if note else {}),
        })
        t["updated_at"] = now

    transfer = _update_transfer(transfer_id, _resolve)
    return {"transfer": _caller_view(transfer, current_user), "message": "Written off", "credit_note_number": note}


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
    transfers = _in_callers_stores(_all_transfers(), current_user)

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

    # D7: the value is the units' own cost (F51) -- _for drops it for counter roles.
    total_value = sum(t.get("total_value", 0) for t in transfers)
    total_items = sum(t.get("total_items", 0) for t in transfers)

    return _for(current_user, {
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
    })


@router.get("/analytics/location/{location_id}")
async def get_location_transfer_analytics(
    location_id: str, current_user: dict = Depends(get_current_user)
):
    """Get transfer analytics for a specific location"""
    transfers = _in_callers_stores(_all_transfers(), current_user)

    outgoing = [t for t in transfers if t.get("from_location_id") == location_id]
    incoming = [t for t in transfers if t.get("to_location_id") == location_id]
    # D7: each value is the units' own cost (F51) -- _for drops it for counter roles.

    return _for(current_user, {
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
    })


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


def _shop_gst(db, store_id: str) -> tuple:
    """(entity_id, gstin, state) of one side of a transfer: THE shop's GSTIN
    (org_validation.shop_gstin -- the one every bill door books on) and the
    state that registration decides, its first two digits (owner,
    2026-09-30). A shop with no GSTIN of its company has an EMPTY GSTIN and an
    EMPTY state -- never its declared state, the company's primary, or another
    state's number -- so the miss stays loud: reports._compute_gstr1 flags the bill
    and the portal export drops the row instead of a wrong counterparty.
    ('', '', '') on a miss / DB absent. A READ ERROR raises 503, never reads
    as 'no company': ship and the challan are refusal doors, so they refuse
    when they cannot read the shops (a blank read would place a two-company
    move on no registration and let it ship unvalued)."""
    if db is None or not store_id:
        return "", "", ""
    try:
        store = db.get_collection("stores").find_one(
            {"store_id": store_id},
            {"_id": 0, "entity_id": 1, "gstin": 1, "state_code": 1, "state": 1},
        ) or {}
        entity_id = str(store.get("entity_id") or "")
        entity = (
            db.get_collection("entities").find_one(
                {"entity_id": entity_id}, {"_id": 0, "gstins": 1}
            )
            if entity_id
            else None
        )
    except Exception as exc:  # noqa: BLE001 - refuse, never guess
        logger.warning("[TRANSFER] shop GST lookup failed for %s: %s", store_id, exc)
        raise HTTPException(
            status_code=503,
            detail="IMS could not read the shops' GST registrations just now. "
            "Nothing has moved; try again.",
        ) from exc
    gstin = ov.shop_gstin(entity, store) or ""
    return entity_id, gstin, gstin[:2]


def _transfer_registrations(db, transfer: Dict) -> tuple:
    """(source, destination, crosses): each side's (entity_id, gstin, state)
    and whether the move crosses a GST registration -- a different company OR
    a different GSTIN of one company (Sch I deemed supply). ONE rule for ship,
    the D13 valued delivery challan and the FIN-3 mirror bill, so the paper
    and the books can never disagree about whether a move is between two
    registrations. `crosses` is None when IMS cannot tell (r2: refuse, never
    guess): a company is involved but EITHER shop's GSTIN is not on file (one
    blank or both, one company or two, or a shop with no company). Ship and
    the challan refuse that as a data gap. The mirror bill, booked after the
    goods moved, cannot refuse: it books such a move with the blank GSTIN,
    flagged on GSTR-1's validation and the Cross-Check (never a supply left
    off unseen), when both shops have a company to name on it. No company on
    either side -> False.

    Once shipped, the answer is the one ship placed the move on
    (`gst_registrations`, stamped by ship_transfer): the goods left on that
    paper, so a GSTIN entered or lost afterwards changes neither the challan
    nor the mirror bill booked at complete."""
    stamp = transfer.get("gst_registrations")
    if isinstance(stamp, dict):
        return tuple(stamp["source"]), tuple(stamp["destination"]), stamp["crosses"]
    src = _shop_gst(db, transfer.get("from_location_id") or "")
    dst = _shop_gst(db, transfer.get("to_location_id") or "")
    if src[1] and dst[1]:
        return src, dst, src != dst
    # ponytail: two company-less shops stay False (an org with no company on
    # file books nothing anywhere); refuse them too if one ever runs that way.
    return src, dst, None if (src[0] or dst[0]) else False


def _gstinless_sender(from_name: str, src: tuple, dst: tuple) -> str:
    """Owner ruling 2026-10-13: stock goes to ANOTHER of our companies only
    from a shop with its GSTIN on file. The refusal when it does not, else ''.
    Asked at create, and at ship and the challan through _gstin_gap (a GSTIN
    removed after create); a same-company move is not this rule's."""
    if src[0] and dst[0] and src[0] != dst[0] and not src[1]:
        return (
            f"{from_name or 'The sending shop'} has no GSTIN on file - add it in "
            "Organization before sending stock to another company."
        )
    return ""


def _gstin_gap(transfer: Dict, src: tuple, dst: tuple) -> str:
    """The refusal for a move IMS cannot place (_transfer_registrations
    None), naming the store(s) with no GSTIN on file -- ship and the challan
    give this one reason (the owner's own words for a sender with no GSTIN,
    _gstinless_sender)."""
    sender = _gstinless_sender(transfer.get("from_location_name") or "", src, dst)
    if sender:
        return sender
    missing = [
        name or "A store"
        for name, side in (
            (transfer.get("from_location_name"), src),
            (transfer.get("to_location_name"), dst),
        )
        if not side[1]
    ]
    return (
        " and ".join(missing)
        + " has no GSTIN of its company on file, so IMS cannot tell which GST "
        "registration this move leaves or enters. Set the shop's company and "
        "GSTIN first."
    )


def _require_hsn(name: str, hsn: str) -> None:
    """D13: a challan between two GST registrations carries each line's HSN.
    ONE refusal for ship and the challan, so goods never leave on a move
    whose paper cannot print."""
    if not hsn:
        raise HTTPException(
            status_code=409,
            detail=f"{name} has no HSN code. Add the product's HSN first: a "
            "challan between two GST registrations carries one.",
        )


def _assert_valued_paper(
    transfer: Dict, products: Dict[str, Dict], units: Optional[Dict[str, Dict]] = None
) -> tuple:
    """D13 / CA 2026-10-18: a move between two GST registrations travels on a
    tax invoice at cost, so it ships only when that paper can be printed --
    both GSTINs on file, at least one piece in the box (never a Rs 0 paper),
    an HSN and a cost for every packed piece (_unit_cost, the rule the ship
    stamp values them by, on the same product rows: `products`, from
    _ship_products; `units`: the packed pieces, from _packed_units). A move
    IMS cannot place (crosses None) is refused as the data gap it is. Refused
    BEFORE any unit moves: once shipped, the value is fixed
    (_stamp_shipped_value). Returns the registrations, which ship stamps on
    the transfer (_transfer_registrations)."""
    db = _get_db()
    src, dst, crosses = registrations = _transfer_registrations(db, transfer)
    if crosses is False:
        return registrations
    if crosses is None:
        raise HTTPException(status_code=400, detail=_gstin_gap(transfer, src, dst))
    units = units or {}
    in_box = False
    for line in transfer.get("items") or []:
        pid = line.get("product_id")
        packed = [units[str(s)] for s in line.get("packed_stock_ids") or [] if str(s) in units]
        if not pid or not packed:
            continue
        in_box = True
        product = products.get(pid) or {}
        _require_hsn(line.get("product_name") or pid, _line_hsn(line, product))
        if not all(_unit_cost(u, product) for u in packed):
            raise HTTPException(
                status_code=409,
                detail=f"{line.get('product_name') or pid} has no cost price and "
                "some of its packed pieces carry no cost. Enter the product's cost "
                "price before shipping: a transfer between two GST registrations "
                "is valued at cost.",
            )
    if not in_box:
        raise HTTPException(
            status_code=409,
            detail="Nothing is packed in this box. A transfer between two GST "
            "registrations travels on a tax invoice valued at the pieces that leave "
            "the shop: scan them into the box first.",
        )
    return registrations


def _tax_split(tax: float, interstate: bool):
    """Return (cgst, sgst, igst) for a tax amount -- gst_rates.split_gst, the
    one splitter every bill door and the GSTR readers use (no second copy)."""
    from ..services.gst_rates import split_gst

    return split_gst(tax, interstate)


def _bill_date_ist(completed_at_raw) -> str:
    """IST wall-clock ISO string for the mirror bill's bill/invoice date.

    transfer.completed_at is stamped with naive ``datetime.now()`` == the
    server (UTC on Railway) wall clock, but the GST tax period is the IST
    calendar month and the vendor-bill month windows (_itc_from_vendor_bills /
    _transfer_outward_bills) compare these date STRINGS in calendar frame. So
    the instant must be shifted to IST before it becomes the filing date --
    otherwise a completion at 00:00-05:29 IST files one month EARLY relative to
    the orders in the same return (which use ist_day_start_utc windows).

    Naive input is treated as UTC (the codebase convention -- see utils/ist.py
    module docstring). Fail-soft: unparseable/missing -> IST "now".
    """
    from datetime import timezone as _tz

    from ..utils.ist import IST, now_ist_naive

    try:
        if completed_at_raw:
            dt = datetime.fromisoformat(str(completed_at_raw))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_tz.utc)
            return dt.astimezone(IST).replace(tzinfo=None).isoformat()
    except Exception:  # noqa: BLE001 - fail-soft
        pass
    return now_ist_naive().isoformat()


def _mirror_bill_lines(
    db, transfer: Dict, interstate: bool, shipped: bool = False
) -> List[Dict]:
    """Per-line taxable + GST for a transfer mirror bill (NEW-GST-TRANSFER-RATES).

    Resolves each transferred product's GST rate the SAME way the rest of the
    app does -- gst_rates.resolve_gst_rate over the product master's hsn_code /
    category (editable hsn_gst_master first, static table fallback) -- instead
    of the old flat 18%. Frames / optical lenses / contact lenses are 5% under
    GST 2.0; sunglasses / watches / accessories are 18%. A flat 18% over-taxed
    the majority of an optical chain's stock moves AND fed the wrong number
    into both entities' GST returns.

    Line shape mirrors purchase_invoices lines (purchase_invoice_engine.
    split_line_gst): {product_id, description, hsn, qty, unit_price, taxable,
    gst_rate, cgst, sgst, igst, line_total}. Per-line values are rounded, so
    header totals (the sums of these) == sum(lines) to the paisa -- the same
    invariant compute_invoice keeps for real purchase invoices.

    Quantity: quantity_received, USED AS-IS when the item carries it (the units
    that actually arrived -- what the receiving side is billed for, mirroring
    GRN accepted-qty billing). received == 0 means the line was SHORT-SHIPPED
    and must NOT be billed (a `received or requested` fallback would bill the
    full requested qty and hand the receiver ITC on goods never received).
    quantity_requested is the fallback ONLY when the item has no
    quantity_received field at all (legacy pre-receive docs). Lines with no
    cost or no quantity are skipped (the caller falls back to the legacy
    aggregate on total_value only when NO item carried usable cost data).

    `shipped`: the units that LEFT the shop instead (_line_expected_qty) --
    the transfer tax invoice, which travels with the goods, prints these.

    Fail-soft: any product-master lookup error degrades that line to the
    app-wide resolve_gst_rate fallback (optical-dominant 5%); never raises.
    """
    from ..services.gst_rates import resolve_gst_rate
    from ..services.purchase_invoice_engine import split_line_gst

    try:
        products_coll = db.get_collection("products") if db is not None else None
    except Exception:  # noqa: BLE001 - fail-soft
        products_coll = None

    # The tax invoice's frozen lines (_freeze_tax_invoice_lines): a line taxes
    # at the HSN / rate it was invoiced at, never one edited since.
    frozen = {
        ln.get("item_id"): ln
        for ln in transfer.get("tax_invoice_lines") or []
        if isinstance(ln, dict) and ln.get("item_id")
    }
    lines: List[Dict] = []
    for item in transfer.get("items") or []:
        if not isinstance(item, dict):
            continue
        # SHORT-SHIP: an explicit quantity_received (including 0) is
        # authoritative; only a missing/None field falls back to requested.
        qty_raw = item.get("quantity_received")
        if qty_raw is None:
            qty_raw = item.get("quantity_requested")
        qty = _line_expected_qty(item) if shipped else _qty(qty_raw)
        invoiced = frozen.get(item.get("id"))
        if invoiced and not shipped and qty == _qty(invoiced.get("qty")):
            # Received as invoiced: the bill books the invoice's own line.
            lines.append({k: v for k, v in invoiced.items() if k not in ("item_id", "serials")})
            continue
        cost = _first_cost(item.get("unit_cost"))
        if qty <= 0 or cost <= 0:
            continue

        product = None
        product_id = item.get("product_id")
        if products_coll is not None and product_id:
            try:
                product = products_coll.find_one(
                    {"product_id": product_id},
                    {"_id": 0, "hsn_code": 1, "category": 1},
                )
            except Exception:  # noqa: BLE001 - fail-soft per line
                product = None
        product = product if isinstance(product, dict) else {}

        category = item.get("category") or product.get("category")
        if invoiced:
            hsn, rate = invoiced.get("hsn") or "", float(invoiced.get("gst_rate") or 0)
        else:
            hsn = _line_hsn(item, product)
            rate = resolve_gst_rate(hsn_code=hsn or None, category=category)

        taxable = _line_value(qty, cost)
        split = split_line_gst(taxable, rate, interstate)
        lines.append(
            {
                "product_id": product_id,
                "description": item.get("product_name") or product_id,
                "hsn": hsn or None,
                "qty": float(qty),
                "unit_price": round(cost, 2),
                "taxable": split["taxable"],
                "gst_rate": split["gst_rate"],
                "cgst": split["cgst"],
                "sgst": split["sgst"],
                "igst": split["igst"],
                "line_total": split["line_total"],
                # The transfer line it is, and the units' barcodes (the tax
                # invoice's serial numbers).
                **(
                    {"item_id": item.get("id"), "serials": item.get("shipped_barcodes") or []}
                    if shipped
                    else {}
                ),
            }
        )
    return lines


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

    # NEW-GST-TRANSFER-IGST: a stock move books a mirror purchase (and GST) when it
    # crosses a GSTIN boundary -- that is EITHER a different legal entity OR a
    # different registration. A same-PAN inter-STATE transfer is a deemed supply
    # between distinct GSTINs (Sch I) and attracts IGST. Each side's GSTIN and
    # state come from the one shop-GSTIN rule (_shop_gst), so two shops on ONE
    # registration never book a supply to themselves -- and the crossing test is
    # the ONE rule shared with the D13 valued delivery challan -- the answer
    # ship stamped, so the books keep the registrations the goods left on.
    try:
        (from_entity, from_gstin, from_state), (to_entity, to_gstin, to_state), crosses = (
            _transfer_registrations(db, transfer)
        )
    except HTTPException as exc:  # a shop read failed (legacy, unstamped) - fail-soft
        logger.warning("[TRANSFER] mirror bill skipped for %s: %s", transfer.get("id"), exc.detail)
        return
    # 'Cannot tell' (None) books too: the goods already moved, and the blank
    # GSTIN keeps the bill loud (see _transfer_registrations). Only a transfer
    # shipped before ship stamped its answer can reach it.
    if crosses is False or not (from_entity and to_entity):
        return

    # Idempotent: skip if we already wrote the bill for this transfer (its
    # credit notes share source_transfer_id; they are not the invoice).
    try:
        if db.get_collection("vendor_bills").find_one(
            {"source_transfer_id": transfer.get("id"), "doc_type": {"$ne": TRANSFER_CREDIT_NOTE}},
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
        # GST: the ONE tax-head rule (purchase_invoice_engine.classify_supply)
        # on the two registrations -- a deemed sale under Sch I Entry 2 between
        # two GSTINs, the "vendor" and the recipient below. A shop with no
        # registration has '' here, so its bill is flagged as unplaced by the
        # Cross-Check instead of getting a head from a declared state.
        interstate = bool(classify_supply(from_gstin, to_gstin)["interstate"])

        # The bill IS the sender's transfer tax invoice (GSTR-1 B2B) and the
        # receiver's inward copy of it: it carries that invoice's number and
        # taxes each line at the invoice's frozen HSN / rate -- both from ship,
        # or made here for a move shipped before ship issued them. A refused
        # mint books the legacy number, which GSTR-1 flags (over 16
        # characters): the goods moved, so the supply is never left off unseen.
        try:
            bill_number = _transfer_tax_invoice(transfer)
            _freeze_tax_invoice_lines(transfer)
        except InvoiceNumberError as exc:
            logger.error(
                "[TRANSFER] no tax invoice number for transfer %s: %s",
                transfer.get("id"), exc,
            )
            bill_number = f"TRF/{transfer.get('transfer_number', transfer.get('id', ''))}"

        # NEW-GST-TRANSFER-RATES (GAP B): per-line taxable + GST at each
        # product's REAL rate via gst_rates.resolve_gst_rate (was: flat 18% on
        # the whole transfer value -- wrong for 5% frames/lenses). Header totals
        # are sums of the ROUNDED per-line values, so header == sum(lines) to
        # the paisa. These lines also feed the SENDER's GSTR-1 HSN summary
        # (reports._compute_gstr1), so per-line hsn/rate precision here is what
        # makes both entities' filings correct.
        # CA answers 2026-10-23: the bill IS the tax invoice as issued -- the
        # units SHIPPED, at the frozen lines the paper printed -- and a short
        # delivery is a credit note on it (_short_credit_note), never a
        # smaller bill.
        lines = [
            {k: v for k, v in ln.items() if k not in ("item_id", "serials")}
            for ln in (transfer.get("tax_invoice_lines") or [])
            if isinstance(ln, dict)
        ] or _mirror_bill_lines(db, transfer, interstate, shipped=True)
        if lines:
            taxable = 0.0
            cgst = 0.0
            sgst = 0.0
            igst = 0.0
            for ln in lines:
                taxable = round(taxable + ln["taxable"], 2)
                cgst = round(cgst + ln["cgst"], 2)
                sgst = round(sgst + ln["sgst"], 2)
                igst = round(igst + ln["igst"], 2)
            tax = round(cgst + sgst + igst, 2)
        else:
            # Legacy aggregate fallback: no usable per-item cost data (older
            # transfer docs / cost-less lines). Value = total_value (sum of
            # unit_cost * qty at request time). The rate resolves through the
            # SAME resolve_gst_rate path with nothing known -> the app-wide
            # optical-dominant default (5%, see gst_rates.DEFAULT_GST_RATE) --
            # consistent with how POS bills an uncategorised item, replacing
            # the old arbitrary flat 18%. The CA can correct the bill if the
            # actual mix differs.
            #
            # SHORT-SHIP GUARD: the fallback fires ONLY when no item carried
            # usable cost data. If cost-bearing items exist but produced no
            # line, every one of them was received == 0 (short-shipped) -- the
            # bill must then be ZERO, not the request-time total_value, or the
            # receiver would claim ITC on goods that never arrived.
            from ..services.gst_rates import resolve_gst_rate

            has_costed_items = False
            for _it in transfer.get("items") or []:
                if not isinstance(_it, dict):
                    continue
                try:
                    if float(_it.get("unit_cost") or 0) > 0:
                        has_costed_items = True
                        break
                except (TypeError, ValueError):
                    continue

            if has_costed_items:
                taxable = 0.0
            else:
                taxable = round(float(transfer.get("total_value") or 0), 2)
            fallback_rate = resolve_gst_rate()
            tax = round(taxable * fallback_rate / 100.0, 2)
            cgst, sgst, igst = _tax_split(tax, interstate)
            if taxable > 0:
                lines = [
                    {
                        "product_id": None,
                        "description": "Stock transfer (aggregate -- no per-item cost data)",
                        "hsn": None,
                        "qty": 0.0,
                        "unit_price": 0.0,
                        "taxable": taxable,
                        "gst_rate": fallback_rate,
                        "cgst": cgst,
                        "sgst": sgst,
                        "igst": igst,
                        "line_total": round(taxable + tax, 2),
                    }
                ]

        bill_id = f"mbill_{uuid.uuid4().hex[:12]}"
        now_iso = utc_now_iso()
        # CA answers 2026-10-23: filed in the DISPATCH month -- the tax
        # invoice's own date (ship), in the IST frame (the GST tax period is
        # the IST calendar month) -- see _bill_date_ist. Both sides read this
        # one date (invoice_date == bill_date), so the sender's outward supply
        # and the receiver's credit land in the same month.
        bill_date_iso = _bill_date_ist(
            transfer.get("tax_invoice_date")
            or transfer.get("shipped_at")
            or transfer.get("completed_at")
        )

        doc = {
            "bill_id": bill_id,
            "bill_number": bill_number,
            "invoice_number": bill_number,
            "bill_date": bill_date_iso,
            "invoice_date": bill_date_iso,
            # The date printed on the tax invoice (ship); bill_date (complete,
            # the receipt) still places the bill on both returns.
            "tax_invoice_date": _bill_date_ist(
                transfer.get("tax_invoice_date") or transfer.get("completed_at")
            ),
            # Link back to the transfer for traceability.
            "source_transfer_id": transfer.get("id"),
            "source_transfer_number": transfer.get("transfer_number"),
            # NEW-GST-TRANSFER-OUTWARD: both stores of the move, so the SENDING
            # store's GSTR-1/3B can pick this bill up as its outward deemed
            # supply (reports._transfer_outward_bills keys on from_store_id) and
            # the ITC lands ONLY in the RECEIVING store's GSTR-3B (to_store_id).
            "from_store_id": from_store_id,
            "to_store_id": to_store_id,
            # The shop the goods landed in -- what every Purchase tab scopes on.
            "store_id": to_store_id,
            # The "vendor" is the sending entity.
            "vendor_id": from_entity,
            "vendor_name": transfer.get("from_location_name") or from_entity,
            "vendor_gstin": from_gstin,
            # Receiving entity's details. recipient_entity_id matches the field
            # purchase_invoices writes -- reports._itc_from_vendor_bills scopes
            # GSTR-3B Table 4 on it, so without it the receiver's ITC claim from
            # transfer bills silently dropped out of the return.
            "entity_id": to_entity,
            "recipient_entity_id": to_entity,
            "recipient_name": transfer.get("to_location_name") or to_entity,
            "recipient_gstin": to_gstin,
            # GST place-of-supply: the sending store's state (Sch I deemed supply)
            # -- what itc_reconcile compares against the recipient entity state.
            "place_of_supply": from_state,
            # The recipient-side (legal outward) place of supply, mirroring the
            # purchase_invoices field of the same name; the sender's GSTR-1 B2B
            # row reports THIS as the place of supply.
            "supply_place_recipient": to_state,
            "interstate": interstate,
            # Per-line detail (hsn / gst_rate / taxable / cgst / sgst / igst per
            # line, purchase_invoices line shape) -- feeds the sender's GSTR-1
            # HSN summary with mixed-rate precision.
            "lines": lines,
            # Amounts.
            "taxable_amount": taxable,
            "tax_amount": tax,
            "taxable_total": taxable,
            "cgst_total": cgst,
            "sgst_total": sgst,
            "igst_total": igst,
            "total_amount": round(taxable + tax, 2),
            "total": round(taxable + tax, 2),
            # CA 2026-10-23: the units SHIPPED (the invoice); a short delivery
            # is its credit note.
            "qty_basis": "shipped",
            "challan_value": round(float(transfer.get("total_value") or 0), 2),
            # ITC eligibility: inter-entity transfers are stock-in-trade, so
            # claimable -- decided by the ONE helper every booking door and
            # reader uses: a sending shop with no valid registration gives no
            # credit. The CA can flag itc_blocked if needed.
            "itc_eligible": ov.itc_claimable(from_gstin),
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
        return
    # Pieces of this transfer written off before its bill landed (damaged on
    # arrival) reverse the credit the bill just claimed (services/writeoff_itc).
    from ..services.writeoff_itc import after_bill_booked

    after_bill_booked(db, doc)


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

        # IDOR guard: same SOURCE-side rule as single /approve. Out-of-scope
        # ids become per-item errors (mirroring "Not found"), so a mixed
        # batch still approves the caller's own transfers.
        if not can_access_store_scoped(
            transfer.get("from_location_id"), current_user
        ):
            errors.append({"id": tid, "error": "No access to this transfer's store"})
            continue

        if transfer["status"] != TransferStatus.PENDING_APPROVAL:
            errors.append({"id": tid, "error": "Not pending approval"})
            continue

        transfer["status"] = TransferStatus.APPROVED
        transfer["approved_by"] = current_user.get("user_id")
        transfer["approved_at"] = utc_now_iso()
        transfer["updated_at"] = utc_now_iso()

        _append_status_history(
            transfer,
            {
                "status": TransferStatus.APPROVED,
                "timestamp": utc_now_iso(),
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

    # IDOR guard: shipment booking is a SOURCE-store action.
    _assert_transfer_access(transfer, current_user, side="source")

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
    transfer["updated_at"] = utc_now_iso()

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

    # IDOR guard: both parties (source + destination stores) may track.
    _assert_transfer_access(transfer, current_user, side="either")

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
