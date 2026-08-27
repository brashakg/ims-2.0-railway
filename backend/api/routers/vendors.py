"""
IMS 2.0 - Vendors Router
=========================
Real database queries for vendor and purchase order management
"""

import logging
import re
import io
import hashlib

from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Optional
from datetime import datetime, timedelta
import uuid
from .auth import get_current_user, require_roles
from ..dependencies import (
    get_vendor_repository,
    get_purchase_order_repository,
    get_grn_repository,
    get_stock_repository,
    get_vendor_portal_token_repository,
    get_audit_repository,
    get_product_repository,
    validate_store_access,
    can_access_store_scoped,
    resolve_store_scope,
)
from ..services import ap_engine
from ..utils.ist import fy_start_year_ist, ist_date_str, now_ist
from ..services import product_master as _pm
from ..services.reorder_policy import auto_reorder_disabled as _auto_reorder_disabled
from ..services.file_store import (
    get_file_store,
    ALLOWED_MIME_TYPES,
    MAX_FILE_SIZE_BYTES,
)


def _po_catalog_gate_on() -> bool:
    """Hub Phase 2: is the PO catalog gate enabled? DARK by default so the manual
    free-text Create-PO flow keeps working until the Buy Desk product picker ships.
    The GRN ghost-stock gate is independent and ALWAYS on. Fail-soft: OFF on error."""
    try:
        from ..services.policy_engine import get_policy

        return bool(get_policy("pm.po_catalog_gate", default=False))
    except Exception:  # noqa: BLE001
        return False


router = APIRouter()
logger = logging.getLogger(__name__)

# Roles permitted to mutate vendors, purchase orders and goods-receipt notes.
# Mirrors the frontend /purchase/* route guards. SUPERADMIN auto-passes.
_VENDOR_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT")

# The metadata.kind this router's own GRN upload stamps. Anything else in the
# shared GridFS bucket belongs to another feature and must never be bound to,
# or served from, a GRN.
_GRN_DOCUMENT_KIND = "grn_document"

# ONE message for every attachment rejection -- forged id, wrong kind, another
# store's document -- so the create endpoint cannot be used as an existence or
# ownership oracle over the shared bucket.
_ATTACHMENT_INVALID_DETAIL = {
    "code": "ATTACHMENT_INVALID",
    "message": (
        "The attached file is no longer available or invalid. Please re-upload."
    ),
}

# Tighter set for money-out / accounts-payable writes (bills, payments, debit
# notes). Recording a payable or releasing cash is an accounting action, so it
# is limited to ADMIN / ACCOUNTANT (SUPERADMIN auto-passes via require_roles).
_AP_ROLES = ("ADMIN", "ACCOUNTANT")

# A PO can have goods received against it while it is en route or partially
# delivered. "PARTIAL" is the legacy single-word status; "PARTIALLY_RECEIVED"
# is what the PO repository's find_pending/find_overdue use -- accept both so a
# part-received PO stays receivable for the remaining lines.
_RECEIVABLE_PO_STATUSES = (
    "SENT",
    "ACKNOWLEDGED",
    "PARTIAL",
    "PARTIALLY_RECEIVED",
)


def _get_db():
    """Direct DB handle for the accounts-payable collections (vendor_bills,
    vendor_payments, vendor_debit_notes). Matches finance.py's pattern -- these
    collections have no repository factory and are queried directly."""
    from database.connection import get_db

    return get_db().db


# W1.4 / OS-006: shared ONLINE store-type detector. An ONLINE store owns no
# stock, so a PO can never deliver to it and a GRN can never receive at it.
from ..services.stores_util import is_online_store  # noqa: E402


# ============================================================================
# SCHEMAS
# ============================================================================


# Indian GSTIN format: 2-digit state code + 10-char PAN + 1 entity + Z + 1 check
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")


class VendorCreate(BaseModel):
    legal_name: str
    trade_name: str
    vendor_type: str = "INDIAN"
    gstin_status: str
    # GSTIN must match the 15-character Indian format when the vendor is
    # REGISTERED. An UNREGISTERED / COMPOSITION / OVERSEAS vendor may omit it.
    gstin: Optional[str] = None
    address: str
    city: str
    state: str
    mobile: str
    email: Optional[str] = None
    # Credit terms must be non-negative. 0 = COD (immediate payment), which is
    # legitimate; negative days would produce a due date BEFORE the bill date
    # and poison the AP aging calculation.
    credit_days: int = Field(30, ge=0)

    @field_validator("gstin", mode="before")
    @classmethod
    def _validate_gstin(cls, v):
        if v is None or v == "":
            return None
        cleaned = v.strip().upper()
        if not _GSTIN_RE.match(cleaned):
            raise ValueError(
                "GSTIN must be 15 characters in the format "
                "NN-AAAAA-9999A-9Z9 (e.g. 27ABCDE1234F1Z5)"
            )
        return cleaned


class VendorUpdate(BaseModel):
    legal_name: Optional[str] = None
    trade_name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[str] = None
    # credit_days must be non-negative on update too.
    credit_days: Optional[int] = Field(default=None, ge=0)
    is_active: Optional[bool] = None


class POItemNewProduct(BaseModel):
    """The identity a buyer types for an item that is not catalogued YET.

    Owner ruling 13: you order from a vendor's list before the product exists in
    IMS, so the system must stop being the obstacle at the FRONT of the flow.
    These are exactly the fields he named -- brand, model no, colour code, size
    and MRP. The line's own `unit_price` is the cost price (provisional: the
    purchase INVOICE later corrects it to the actual one, ruling 12).

    There is deliberately NO selling price here: see product_master's provisional
    door. Without one the product can never stamp ACTIVE, so it can never be
    sold before a cataloguer finishes it.
    """

    # FRAME is the overwhelmingly common case at the buy desk; any registry
    # category is accepted and resolved server-side.
    category: str = "FRAME"
    brand: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    colour: Optional[str] = None
    size: Optional[str] = None
    mrp: float = Field(..., gt=0)


class POItemCreate(BaseModel):
    # Ruling 13: a line references EITHER an existing catalogued product OR
    # carries the identity of one that does not exist yet, which the server
    # materialises into a real (provisional, unsellable) spine row before the PO
    # is written. product_id stays the join key for everything downstream --
    # receiving, the stock mint, the invoice and the 3-way match all key on it.
    product_id: Optional[str] = None
    product_name: Optional[str] = None
    sku: Optional[str] = None
    new_product: Optional[POItemNewProduct] = None
    # A PO line must order at least one unit at a non-negative price. Without
    # these bounds a negative quantity / price would persist a corrupt PO and
    # poison the subtotal/GST math (subtotal = sum(quantity * unit_price)).
    quantity: int = Field(..., ge=1)
    unit_price: float = Field(..., ge=0)
    # Per-line GST identity. All optional: when gst_rate is None the server
    # resolves it from hsn/category (falling back to the product's own
    # hsn/category), so a PO never silently bills the old flat 18%.
    hsn: Optional[str] = None
    gst_rate: Optional[float] = Field(default=None, ge=0, le=100)
    category: Optional[str] = None

    @model_validator(mode="after")
    def _one_product_identity(self):
        """A line names an existing product OR describes a new one -- never both,
        never neither. Both would be ambiguous about which identity the receipt
        and the bill are settled against."""
        has_id = bool((self.product_id or "").strip())
        has_new = self.new_product is not None
        if has_id and has_new:
            raise ValueError(
                "a PO line cannot both reference an existing product and "
                "describe a new one"
            )
        if not has_id and not has_new:
            raise ValueError(
                "a PO line needs either a catalogued product or the new item's "
                "brand, model, colour, size and MRP"
            )
        if has_id and not (self.product_name or "").strip():
            raise ValueError("product_name is required for a catalogued line")
        return self


class POCreate(BaseModel):
    vendor_id: str
    delivery_store_id: str
    # An empty items list would store a PO with subtotal=0/tax=0/total=0 --
    # a corrupt record that passes all downstream checks but means nothing.
    # Enforce at least one line item.
    items: List[POItemCreate] = Field(..., min_length=1)
    expected_date: Optional[str] = None
    notes: Optional[str] = None


class GRNItemCreate(BaseModel):
    # F9: optional -- a no-PO Delivery Challan line has no PO item to reference.
    # A standard GRN line still carries it (the frontend always sends it).
    po_item_id: Optional[str] = None
    product_id: str
    # Receipt quantities are counts of physical units -- never negative. A
    # negative received/accepted/rejected qty would mint a negative stock
    # movement and corrupt the PO receipt-state + accepted-qty rollups.
    received_qty: int = Field(..., ge=0)
    accepted_qty: int = Field(..., ge=0)
    rejected_qty: int = Field(0, ge=0)
    rejection_reason: Optional[str] = None
    # Ruling 14 -- THE TALLY TICK. The receiver ticks each line to say "I have
    # counted this one against what was ordered". Defaults to False so the tick
    # is a real act: a clerk who touches nothing can no longer post a perfect
    # receipt off the pre-filled ordered quantity. Enforced for PO-backed
    # STANDARD receipts only (a Delivery Challan has no order to tally against).
    tallied: bool = False
    # Receiving location for the minted serialized units (optional; falls back
    # to "DEFAULT" on the stock unit). Lets the receiver bin goods at post time.
    location_code: Optional[str] = None
    # P2 (optical batch/expiry): a contact-lens line carries the supplier batch
    # + expiry so each minted unit is dated for FEFO consumption + near-expiry
    # reporting (the stock_unit model + FEFO helpers already key on these).
    # Optional + backward-compatible: a frame/spectacle line simply omits them.
    # lot_number is an accepted alias for batch_code (CL convention).
    batch_code: Optional[str] = None
    lot_number: Optional[str] = None
    expiry_date: Optional[str] = None

    @field_validator("batch_code", "lot_number", "expiry_date", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @model_validator(mode="after")
    def _validate_qty_coherence(self):
        """Cross-field quantity guard.

        Two physical invariants that Pydantic field bounds alone cannot enforce:

        1. accepted_qty <= received_qty -- you cannot accept more units than
           arrived. An accepted_qty > received_qty would produce a positive
           stock write larger than the physical receipt and corrupt inventory.

        2. accepted_qty + rejected_qty == received_qty -- every received unit
           must be either accepted (added to stock) or rejected (returned /
           debit-noted). A mismatch silently discards or double-counts units.
        """
        rec = self.received_qty
        acc = self.accepted_qty
        rej = self.rejected_qty
        if acc > rec:
            raise ValueError(f"accepted_qty ({acc}) cannot exceed received_qty ({rec})")
        if acc + rej != rec:
            raise ValueError(
                f"accepted_qty ({acc}) + rejected_qty ({rej}) must equal "
                f"received_qty ({rec})"
            )
        return self


# F9: GRN subtypes. A STANDARD GRN is received against a PO + the vendor's tax
# invoice (vendor_invoice_no is mandatory). A DELIVERY_CHALLAN (DC) is the
# physical goods-receipt doc a lens lab sends WITH external-lab lenses -- the tax
# invoice comes later (monthly/fortnightly), so vendor_invoice_no is optional and
# the DC carries its own dc_number + dc_date. A missing grn_subtype on legacy
# docs reads as STANDARD (backward-compatible).
GRN_SUBTYPE_STANDARD = "STANDARD"
GRN_SUBTYPE_DC = "DELIVERY_CHALLAN"
_GRN_SUBTYPES = (GRN_SUBTYPE_STANDARD, GRN_SUBTYPE_DC)


class GRNCreate(BaseModel):
    # F9: po_id is REQUIRED for a STANDARD GRN but OPTIONAL for a DELIVERY_CHALLAN
    # (a lens top-up DC often arrives with no pre-logged PO). Enforced in the
    # model_validator below so the field default can be None.
    po_id: Optional[str] = None
    # F9: vendor_invoice_no is REQUIRED for a STANDARD GRN but OPTIONAL for a DC
    # (the tax invoice arrives later and is reconciled via the bulk DC->invoice
    # tally). Enforced in the validator.
    vendor_invoice_no: Optional[str] = None
    vendor_invoice_date: Optional[str] = None
    # A GRN with zero items is meaningless and would mark a PO as having
    # been received without actually recording any goods.
    items: List[GRNItemCreate] = Field(..., min_length=1)
    notes: Optional[str] = None
    # F9: Delivery-Challan fields.
    grn_subtype: str = GRN_SUBTYPE_STANDARD
    dc_number: Optional[str] = None
    dc_date: Optional[str] = None
    # F9: the vendor a no-PO DC is for (a STANDARD GRN derives this from the PO).
    vendor_id: Optional[str] = None
    # F-S3: mandatory goods-receipt document. The ops user (Superadmin/Admin/
    # Store Manager) physically receiving the stock MUST attach the vendor
    # invoice/challan image or PDF BEFORE the GRN can be created -- so the
    # accountant always has the source document to reconcile against. The file
    # is uploaded first via POST /vendors/grn/upload-doc, which returns a
    # file_id that is then passed here. STANDARD GRNs require it; a
    # DELIVERY_CHALLAN is exempt at receipt time (its tax invoice arrives later
    # and is attached at reconciliation -- see P3). Gate enforced in create_grn.
    attachment_file_id: Optional[str] = None
    attachment_filename: Optional[str] = None
    attachment_mime: Optional[str] = None

    @field_validator("grn_subtype", mode="before")
    @classmethod
    def _normalize_subtype(cls, v):
        s = str(v or GRN_SUBTYPE_STANDARD).strip().upper().replace("-", "_")
        return s if s in _GRN_SUBTYPES else GRN_SUBTYPE_STANDARD

    @model_validator(mode="after")
    def _validate_subtype_fields(self):
        """F9 subtype-specific required-field guard.

        STANDARD: po_id + vendor_invoice_no are both required (the existing
                  contract -- a standard GRN is always against a PO + invoice).
        DELIVERY_CHALLAN: dc_number + dc_date are required; po_id +
                  vendor_invoice_no are optional (they come later).
        """
        if self.grn_subtype == GRN_SUBTYPE_DC:
            if not (self.dc_number and str(self.dc_number).strip()):
                raise ValueError("dc_number is required for a Delivery Challan")
            if not (self.dc_date and str(self.dc_date).strip()):
                raise ValueError("dc_date is required for a Delivery Challan")
        else:
            if not (self.po_id and str(self.po_id).strip()):
                raise ValueError("po_id is required for a standard GRN")
            if not (self.vendor_invoice_no and str(self.vendor_invoice_no).strip()):
                raise ValueError("vendor_invoice_no is required for a standard GRN")
        return self


class ExpressGRNItemCreate(BaseModel):
    """One received line for the express (clean-delivery) receiving chain.

    Mirrors GRNItemCreate's fields but WITHOUT the cross-field coherence
    validator: express enforces its own stricter CLEAN-ONLY rule in the
    handler (rejected == 0 and accepted == received > 0) and answers every
    violation with ONE stable 400 code (EXPRESS_NOT_CLEAN) the frontend keys
    on to fall back to the two-step receive -- a Pydantic 422 would leak a
    different error shape for some non-clean payloads.
    """

    po_item_id: Optional[str] = None
    product_id: str
    received_qty: int = Field(..., ge=0)
    accepted_qty: int = Field(..., ge=0)
    rejected_qty: int = Field(0, ge=0)
    location_code: Optional[str] = None
    batch_code: Optional[str] = None
    lot_number: Optional[str] = None
    expiry_date: Optional[str] = None

    @field_validator("batch_code", "lot_number", "expiry_date", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class ExpressGRNCreate(BaseModel):
    """Body for POST /vendors/grn/express -- the one-shot clean receive.

    STANDARD PO-backed receipts only: po_id + vendor_invoice_no are required
    (grn_subtype is accepted for explicitness but a DELIVERY_CHALLAN is
    rejected in the handler with EXPRESS_STANDARD_ONLY). The attachment fields
    flow into the SAME F-S3 mandatory-document gate create_grn enforces --
    attachment_file_id stays Optional here so a missing document surfaces as
    the canonical 400 ATTACHMENT_REQUIRED (not a 422), keeping the
    no-paper-no-stock control's error contract identical across both flows.
    """

    po_id: str = Field(..., min_length=1)
    vendor_invoice_no: str = Field(..., min_length=1)
    vendor_invoice_date: Optional[str] = None
    items: List[ExpressGRNItemCreate] = Field(..., min_length=1)
    attachment_file_id: Optional[str] = None
    attachment_filename: Optional[str] = None
    attachment_mime: Optional[str] = None
    notes: Optional[str] = None
    grn_subtype: str = GRN_SUBTYPE_STANDARD

    @field_validator("grn_subtype", mode="before")
    @classmethod
    def _normalize_subtype(cls, v):
        s = str(v or GRN_SUBTYPE_STANDARD).strip().upper().replace("-", "_")
        return s if s in _GRN_SUBTYPES else GRN_SUBTYPE_STANDARD


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def _counters_collection():
    """Shared ``counters`` collection for atomic purchase numbering. Fail-soft:
    returns None (DB-less / mock) so the numberers fall back to a timestamp."""
    try:
        db = _get_db()
        return db.get_collection("counters") if db is not None else None
    except Exception:  # noqa: BLE001
        return None


def generate_po_number(store_id: str, store_code: Optional[str] = None) -> str:
    """Allocate the next PO number for a store in its financial year.

    Format ``PO/{store}/{FY}/{serial}`` (e.g. ``PO/BV-BOK-01/26-27/0001``) --
    a consecutive, per-store, per-FY serial via the shared counters collection
    (S5), the same discipline as the GST invoice number. Fail-soft: with no DB
    the service returns a time-derived suffix in the same format."""
    from ..services.purchase_numbering import next_purchase_number

    return next_purchase_number(
        _counters_collection(),
        doc_type="PO",
        store_id=store_id,
        store_code=store_code or store_id,
    )


def generate_grn_number(store_id: str, store_code: Optional[str] = None) -> str:
    """Allocate the next goods-receipt (GRN) number for a store in its FY.

    Format ``RCPT/{store}/{FY}/{serial}``. Atomic per (store, FY) via the shared
    counters collection (S5); fail-soft to a time-derived suffix when DB-less."""
    from ..services.purchase_numbering import next_purchase_number

    return next_purchase_number(
        _counters_collection(),
        doc_type="GRN",
        store_id=store_id,
        store_code=store_code or store_id,
    )


def classify_grn_line_variance(received_qty, ordered_qty, tolerance: int = 0) -> str:
    """Classify a single received line against what was ordered on the PO.

    Returns one of:
      * "UNMATCHED" -- the line could not be matched to a PO line (ordered_qty
        is None), so there is nothing to compare against.
      * "SHORT"     -- received fewer units than ordered (beyond tolerance).
      * "OVER"      -- received more units than ordered (beyond tolerance).
      * "EXACT"     -- received exactly what was ordered (within tolerance).

    Pure + total: garbage/missing numbers coerce to 0 so this never raises.
    Used both to stamp a per-line `variance_status` on the GRN at create time
    and to drive the receiving UI's short/exact/over flags.
    """

    def _int(v) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    if ordered_qty is None:
        return "UNMATCHED"

    tol = abs(_int(tolerance))
    delta = _int(received_qty) - _int(ordered_qty)
    if delta < -tol:
        return "SHORT"
    if delta > tol:
        return "OVER"
    return "EXACT"


def compute_po_receipt_state(
    po_items, received_by_product: dict, tolerance: int = 0
) -> str:
    """Decide whether a PO is fully or partially received.

    Compares the cumulative received quantity per product (summed across every
    ACCEPTED GRN for the PO) against the ordered quantity on each PO line.

    Returns "RECEIVED" when every ordered line has been received in full (or
    over-received) within tolerance, otherwise "PARTIALLY_RECEIVED". A PO with
    no line items resolves to "RECEIVED" (nothing left to receive).

    Pure + total: bad fields coerce to 0; never raises. This is the core
    partial-vs-full decision and is unit-tested without a database.
    """

    def _int(v) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    tol = abs(_int(tolerance))
    items = po_items if isinstance(po_items, list) else []
    received_by_product = received_by_product or {}

    # Roll the ordered quantity up per product so multiple PO lines for the same
    # product are compared against the combined received count.
    ordered_by_product: dict = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        pid = item.get("product_id")
        if pid is None:
            continue
        ordered_by_product[pid] = ordered_by_product.get(pid, 0) + _int(
            item.get("quantity")
        )

    if not ordered_by_product:
        return "RECEIVED"

    for pid, ordered in ordered_by_product.items():
        received = _int(received_by_product.get(pid))
        if received < ordered - tol:
            return "PARTIALLY_RECEIVED"

    return "RECEIVED"


def grn_has_discrepancy(grn: dict, qty_tolerance: int = 0) -> bool:
    """True if a goods-receipt note shows a receiving variance worth a task.

    A discrepancy is any of:
      * a line with rejected_qty > 0 (goods sent back as defective/wrong), or
      * a line whose received_qty differs from its ordered_qty beyond
        qty_tolerance (short or over shipment), where ordered_qty is matched
        from the PO and stamped onto the line, or
      * a top-level total_received != total_ordered beyond qty_tolerance.

    Pure and total: missing/garbage fields coerce to 0 so a malformed GRN never
    raises here (the caller is fail-soft regardless). Only line-level signals are
    used when present; the total check is a backstop for callers that pass totals
    but not per-line ordered quantities.
    """
    if not isinstance(grn, dict):
        return False

    def _int(v) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    tol = abs(_int(qty_tolerance))
    items = grn.get("items") if isinstance(grn.get("items"), list) else []

    for item in items:
        if not isinstance(item, dict):
            continue
        if _int(item.get("rejected_qty")) > 0:
            return True
        if "ordered_qty" in item:
            if (
                abs(_int(item.get("received_qty")) - _int(item.get("ordered_qty")))
                > tol
            ):
                return True

    if grn.get("total_ordered") is not None:
        if abs(_int(grn.get("total_received")) - _int(grn.get("total_ordered"))) > tol:
            return True

    return False


def _grn_barcode(store_id: Optional[str], product_id: Optional[str]) -> str:
    """Generate a barcode for a GRN-minted serialized unit.

    Reuses inventory.generate_barcode (the canonical stock-barcode format) so a
    unit received via GRN is indistinguishable from one added via the inventory
    /stock/add screen. Fail-soft: if that helper can't be imported for any
    reason, fall back to a uuid-derived barcode so the stock write still
    succeeds (a missing barcode must never block receiving goods).
    """
    try:
        from .inventory import generate_barcode

        return generate_barcode(store_id, product_id)
    except Exception:  # noqa: BLE001
        return f"BC-{uuid.uuid4().hex[:12].upper()}"


def _grn_stock_audit(
    stock_id: str,
    new_status: str,
    grn_id: str,
    po_id: Optional[str],
    store_id: Optional[str],
    user_id: Optional[str],
) -> None:
    """Cheap, fail-soft insert into the stock_audit collection for every unit
    minted while posting a GRN. Mirrors the returns-restock audit shape so the
    audit trail answers "which unit entered stock when, and from which GRN/PO".

    Any error is swallowed -- the audit row must never break (or roll back) the
    stock write that already happened. This is the Fail-Loudly-but-not-here
    boundary: losing an audit row is acceptable; losing received stock is not.
    """
    if not stock_id:
        return
    try:
        from ..dependencies import get_db

        db = get_db()
        if db is None or not getattr(db, "is_connected", False):
            return
        coll = db.db.get_collection("stock_audit")
        if coll is None:
            return
        coll.insert_one(
            {
                "stock_id": str(stock_id),
                "prior_status": None,
                "new_status": new_status,
                "source": "GRN_RECEIPT",
                "grn_id": grn_id,
                "po_id": po_id,
                "store_id": store_id,
                "by_user": user_id,
                "at": datetime.now().isoformat(),
            }
        )
    except Exception:  # noqa: BLE001
        pass


def _cumulative_received_by_product(grn_repo, po_id: str) -> dict:
    """Sum accepted_qty per product across every ACCEPTED GRN for a PO.

    This is the running on-hand-received tally used to decide whether the PO is
    now fully or partially received. Fail-soft: any read error returns {} so the
    caller degrades to "partial" rather than crashing the accept.
    """
    totals: dict = {}
    if grn_repo is None or not po_id:
        return totals
    try:
        accepted_grns = grn_repo.find_many(
            {"po_id": po_id, "status": "ACCEPTED"}, limit=1000
        )
    except Exception:  # noqa: BLE001
        return totals
    for grn in accepted_grns or []:
        if not isinstance(grn, dict):
            continue
        for item in grn.get("items", []) or []:
            if not isinstance(item, dict):
                continue
            pid = item.get("product_id")
            if pid is None:
                continue
            try:
                totals[pid] = totals.get(pid, 0) + int(item.get("accepted_qty", 0) or 0)
            except (TypeError, ValueError):
                continue
    return totals


# ============================================================================
# VENDOR ENDPOINTS
# ============================================================================


# Both "" and "/" — the app uses redirect_slashes=False, so bare + slashed
# forms must both resolve. Audit Run #2: Purchase page was 404'ing because
# the frontend calls api.get('/vendors') without trailing slash.
@router.get("")
@router.get("/")
async def list_vendors(
    search: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """List all vendors with optional search"""
    vendor_repo = get_vendor_repository()

    if vendor_repo is None:
        return {"vendors": [], "total": 0}

    filter_dict = {}
    if is_active is not None:
        filter_dict["is_active"] = is_active

    if search:
        # Search in name, trade name, or mobile
        vendors = vendor_repo.search_vendors(search)
    else:
        vendors = vendor_repo.find_many(filter_dict, skip=skip, limit=limit)

    return {"vendors": vendors or [], "total": len(vendors) if vendors else 0}


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_vendor(
    vendor: VendorCreate,
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """Create a new vendor"""
    vendor_repo = get_vendor_repository()
    vendor_id = str(uuid.uuid4())

    if vendor_repo is not None:
        # Check for duplicate GSTIN
        if vendor.gstin:
            existing = vendor_repo.find_one({"gstin": vendor.gstin})
            if existing is not None:
                raise HTTPException(
                    status_code=400, detail="Vendor with this GSTIN already exists"
                )

        vendor_repo.create(
            {
                "vendor_id": vendor_id,
                "legal_name": vendor.legal_name,
                "trade_name": vendor.trade_name,
                "vendor_type": vendor.vendor_type,
                "gstin_status": vendor.gstin_status,
                "gstin": vendor.gstin,
                "address": vendor.address,
                "city": vendor.city,
                "state": vendor.state,
                "mobile": vendor.mobile,
                "email": vendor.email,
                "credit_days": vendor.credit_days,
                "is_active": True,
                "created_by": current_user.get("user_id"),
                "created_at": datetime.now().isoformat(),
            }
        )

    return {"vendor_id": vendor_id, "message": "Vendor created successfully"}


# IMPORTANT: GET /{vendor_id} is registered at the BOTTOM of this file via
# `router.add_api_route(...)`, NOT here. FastAPI matches routes in
# registration order; a `/{vendor_id}` decorator here shadowed every
# specific GET below it (`/purchase-orders`, `/grn`) — they'd resolve
# to this handler with `vendor_id="purchase-orders"` and return 404
# ("Vendor not found"). Same class of bug as the tasks.py route-order
# fix in PR #103.
async def get_vendor(vendor_id: str, current_user: dict = Depends(get_current_user)):
    """Get vendor details"""
    vendor_repo = get_vendor_repository()

    if vendor_repo is None:
        return {"vendor_id": vendor_id}

    vendor = vendor_repo.find_by_id(vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")

    return vendor


@router.put("/{vendor_id}")
async def update_vendor(
    vendor_id: str,
    updates: VendorUpdate,
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """Update vendor details"""
    vendor_repo = get_vendor_repository()

    if vendor_repo is not None:
        existing = vendor_repo.find_by_id(vendor_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Vendor not found")

        update_data = updates.model_dump(exclude_unset=True)
        update_data["updated_by"] = current_user.get("user_id")
        update_data["updated_at"] = datetime.now().isoformat()

        vendor_repo.update(vendor_id, update_data)

    return {"vendor_id": vendor_id, "message": "Vendor updated successfully"}


# ============================================================================
# INV-7: VENDOR SKU ALIAS — maps vendor-specific codes to IMS master products
# ============================================================================
# Problem: the same lens/frame arrives from different suppliers under different
# catalogue codes.  Without an alias map, staff must search the product master
# each time, leading to duplicate entries at goods-inward.  With this, they
# register the vendor code once and the GRN workflow can resolve it to the
# canonical IMS product_id automatically.


class VendorSkuAliasCreate(BaseModel):
    product_id: str  # IMS master product_id
    vendor_sku: str  # vendor's own catalogue / item code
    description: Optional[str] = None  # optional free-text note


@router.get("/sku-alias-lookup")
async def lookup_vendor_sku(
    vendor_id: str = Query(...),
    vendor_sku: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Resolve a vendor's SKU code to the IMS master product_id (INV-7).

    Called during GRN / goods-inward so staff never have to manually search
    the product master when receiving stock.
    """
    db = _get_db()
    if db is None:
        return {"product_id": None, "vendor_id": vendor_id, "vendor_sku": vendor_sku}

    try:
        coll = db.get_collection("vendor_sku_aliases")
        doc = coll.find_one({"vendor_id": vendor_id, "vendor_sku": vendor_sku})
        if not doc:
            return {
                "product_id": None,
                "vendor_id": vendor_id,
                "vendor_sku": vendor_sku,
                "message": "No alias found",
            }
        doc.pop("_id", None)
        return {
            "product_id": doc.get("product_id"),
            "vendor_id": vendor_id,
            "vendor_sku": vendor_sku,
            "description": doc.get("description"),
            "alias_id": doc.get("alias_id"),
        }
    except Exception as e:
        logger.warning(f"lookup_vendor_sku error: {e}")
        return {"product_id": None, "vendor_id": vendor_id, "vendor_sku": vendor_sku}


@router.get("/{vendor_id}/sku-aliases")
async def list_vendor_sku_aliases(
    vendor_id: str,
    current_user: dict = Depends(get_current_user),
):
    """List all SKU aliases registered for a vendor (INV-7)."""
    db = _get_db()
    if db is None:
        return {"aliases": [], "vendor_id": vendor_id}

    try:
        coll = db.get_collection("vendor_sku_aliases")
        docs = list(coll.find({"vendor_id": vendor_id}, {"_id": 0}))
        return {"aliases": docs, "vendor_id": vendor_id, "total": len(docs)}
    except Exception as e:
        logger.warning(f"list_vendor_sku_aliases error: {e}")
        return {"aliases": [], "vendor_id": vendor_id}


@router.post("/{vendor_id}/sku-aliases", status_code=201)
async def create_vendor_sku_alias(
    vendor_id: str,
    body: VendorSkuAliasCreate,
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """Register a vendor SKU code to an IMS master product (INV-7).

    Idempotent on (vendor_id, vendor_sku): re-posting an existing alias
    updates the product_id and description rather than creating a duplicate.
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        vendor_repo = get_vendor_repository()
        if vendor_repo is not None:
            vendor = vendor_repo.find_by_id(vendor_id)
            if vendor is None:
                raise HTTPException(status_code=404, detail="Vendor not found")

        coll = db.get_collection("vendor_sku_aliases")
        now = datetime.now()

        # Upsert on (vendor_id, vendor_sku) — idempotent
        existing = coll.find_one(
            {"vendor_id": vendor_id, "vendor_sku": body.vendor_sku}
        )
        if existing:
            coll.update_one(
                {"vendor_id": vendor_id, "vendor_sku": body.vendor_sku},
                {
                    "$set": {
                        "product_id": body.product_id,
                        "description": body.description,
                        "updated_at": now.isoformat(),
                        "updated_by": current_user.get("user_id", ""),
                    }
                },
            )
            alias_id = existing.get("alias_id", "")
            action = "updated"
        else:
            alias_id = str(uuid.uuid4())
            coll.insert_one(
                {
                    "alias_id": alias_id,
                    "vendor_id": vendor_id,
                    "vendor_sku": body.vendor_sku,
                    "product_id": body.product_id,
                    "description": body.description,
                    "created_at": now.isoformat(),
                    "created_by": current_user.get("user_id", ""),
                }
            )
            action = "created"

        return {
            "alias_id": alias_id,
            "vendor_id": vendor_id,
            "vendor_sku": body.vendor_sku,
            "product_id": body.product_id,
            "action": action,
            "message": f"Vendor SKU alias {action}",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"create_vendor_sku_alias error: {e}")
        raise HTTPException(status_code=500, detail="Failed to save vendor SKU alias")


@router.delete("/{vendor_id}/sku-aliases/{alias_id}", status_code=200)
async def delete_vendor_sku_alias(
    vendor_id: str,
    alias_id: str,
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """Remove a vendor SKU alias (INV-7)."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        coll = db.get_collection("vendor_sku_aliases")
        result = coll.delete_one({"alias_id": alias_id, "vendor_id": vendor_id})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Alias not found")
        return {
            "alias_id": alias_id,
            "vendor_id": vendor_id,
            "message": "Alias deleted",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"delete_vendor_sku_alias error: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete alias")


# ============================================================================
# PURCHASE ORDER ENDPOINTS
# ============================================================================


@router.get("/purchase-orders")
async def list_pos(
    vendor_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """List purchase orders with filters"""
    po_repo = get_purchase_order_repository()
    active_store = validate_store_access(store_id, current_user) or current_user.get(
        "active_store_id"
    )

    if po_repo is None:
        return {"purchase_orders": [], "total": 0}

    filter_dict = {}
    if vendor_id:
        filter_dict["vendor_id"] = vendor_id
    if status:
        filter_dict["status"] = status
    if active_store:
        filter_dict["delivery_store_id"] = active_store

    pos = po_repo.find_many(filter_dict, skip=skip, limit=limit)

    return {"purchase_orders": pos or [], "total": len(pos) if pos else 0}


# INV-9: Demand forecast -> nightly draft-PO suggestions
# Reads the analytics-v2 demand-forecast data for the caller's store and
# creates a DRAFT purchase order for each product that needs reorder,
# grouped by the product's preferred_vendor_id.  If no vendor is attached to
# a product, the item is placed on a catch-all "unassigned" suggestions list.
# Only SUPERADMIN / ADMIN / AREA_MANAGER / STORE_MANAGER may trigger this
# (mirrors the PO create gate).  Fail-soft: if the demand data can't be read
# the endpoint returns an empty result rather than 500.


class ForecastPoRequest(BaseModel):
    store_id: Optional[str] = None  # defaults to caller's active store
    horizon_days: int = Field(30, ge=7, le=90)  # forecast window
    safety_stock_days: int = Field(7, ge=0, le=30)  # extra buffer days
    # If True a real DRAFT PO doc is persisted per vendor; otherwise returns
    # suggestions only (dry_run=True is safe for the nightly ORACLE cron).
    dry_run: bool = False


@router.post("/purchase-orders/from-forecast", status_code=201)
async def create_pos_from_forecast(
    body: ForecastPoRequest,
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """Generate DRAFT purchase orders from the demand forecast (INV-9).

    Algorithm:
    1. Pull 90-day sales velocity per product for the store.
    2. For each product where predicted demand > current_stock + safety_stock,
       compute the recommended order quantity.
    3. Group by preferred_vendor_id (stored on the product doc).
    4. Create one DRAFT PO per vendor group (unless dry_run=True).

    Returns a summary and the list of created (or would-be-created) POs.
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    active_store = body.store_id or current_user.get("active_store_id") or ""
    if not active_store:
        raise HTTPException(status_code=400, detail="store_id is required")

    # W1.4 / OS-006: forecast POs deliver to the requesting store -- never an
    # ONLINE (stockless, pooled) store.
    if is_online_store(db, active_store):
        raise HTTPException(
            status_code=400,
            detail=(
                "Online stores hold no stock - switch to a physical shop "
                "before generating purchase orders."
            ),
        )

    horizon = body.horizon_days
    safety = body.safety_stock_days

    try:
        from datetime import timedelta

        now = datetime.now()
        ninety_days_ago = now - timedelta(days=90)

        # --- Step 1: compute sales velocity per product (last 90 days) ---
        orders = list(
            db.get_collection("orders")
            .find(
                {
                    "store_id": active_store,
                    "status": {"$nin": ["CANCELLED", "DRAFT"]},
                    "created_at": {"$gte": ninety_days_ago},
                },
                {"items": 1, "order_items": 1},
            )
            .limit(10000)
        )

        product_sales: dict = {}
        for o in orders:
            for item in o.get("items") or o.get("order_items") or []:
                pid = item.get("product_id", "")
                if not pid:
                    continue
                qty = int(item.get("quantity", 1) or 1)
                if pid not in product_sales:
                    product_sales[pid] = {
                        "product_name": item.get("product_name")
                        or item.get("name", ""),
                        "sku": item.get("sku", ""),
                        "qty_90d": 0,
                    }
                product_sales[pid]["qty_90d"] += qty

        if not product_sales:
            return {
                "store_id": active_store,
                "dry_run": body.dry_run,
                "pos_created": 0,
                "suggestions": [],
                "message": "No sales data found for the last 90 days",
            }

        # --- Step 2: join with products for stock + vendor ---
        products_coll = db.get_collection("products")
        product_ids = list(product_sales.keys())
        prod_docs = {
            p.get("product_id"): p
            for p in products_coll.find({"product_id": {"$in": product_ids}})
            if p.get("product_id")
        }

        reorder_items: dict = {}  # vendor_id -> list of line items
        suggestions = []

        for pid, sales in product_sales.items():
            avg_daily = sales["qty_90d"] / 90.0
            predicted = avg_daily * horizon
            buffer = avg_daily * safety
            need = predicted + buffer

            prod = prod_docs.get(pid, {})
            # Owner decision (2026-07-04): reorder_quantity <= 0 (the new -1
            # default) disables auto-reorder for the product -- no forecast
            # suggestion, no draft PO line (see api/services/reorder_policy.py).
            if _auto_reorder_disabled(prod):
                continue
            current_stock = int(prod.get("quantity", 0) or prod.get("stock", 0) or 0)
            reorder_qty = max(0, round(need - current_stock))
            if reorder_qty == 0:
                continue

            vendor_id = prod.get("preferred_vendor_id") or "UNASSIGNED"
            unit_price = float(
                prod.get("cost_price", 0) or prod.get("purchase_price", 0) or 0
            )
            sku = sales.get("sku") or prod.get("sku", "")
            product_name = sales.get("product_name") or prod.get("name", "")

            suggestion = {
                "product_id": pid,
                "product_name": product_name,
                "sku": sku,
                "vendor_id": vendor_id,
                "current_stock": current_stock,
                "avg_daily_sales": round(avg_daily, 2),
                "predicted_demand": round(predicted, 1),
                "safety_buffer": round(buffer, 1),
                "reorder_quantity": reorder_qty,
                "estimated_unit_price": unit_price,
            }
            suggestions.append(suggestion)

            if vendor_id != "UNASSIGNED":
                reorder_items.setdefault(vendor_id, []).append(
                    {
                        "product_id": pid,
                        "product_name": product_name,
                        "sku": sku,
                        "quantity": reorder_qty,
                        "unit_price": unit_price,
                    }
                )

        # --- Step 3: create DRAFT POs per vendor group ---
        created_pos = []
        if not body.dry_run and reorder_items:
            po_repo = get_purchase_order_repository()
            vendor_repo = get_vendor_repository()

            for v_id, lines in reorder_items.items():
                vendor = None
                if vendor_repo is not None:
                    vendor = vendor_repo.find_by_id(v_id)
                if vendor is None:
                    # Skip if vendor not found; include in suggestions only
                    continue

                po_id = str(uuid.uuid4())
                po_number = generate_po_number(active_store)
                subtotal = sum(ln["quantity"] * ln["unit_price"] for ln in lines)
                tax = subtotal * 0.18
                total = subtotal + tax

                po_doc = {
                    "po_id": po_id,
                    "po_number": po_number,
                    "vendor_id": v_id,
                    "vendor_name": vendor.get("trade_name") or vendor.get("legal_name"),
                    "delivery_store_id": active_store,
                    "items": lines,
                    "subtotal": round(subtotal, 2),
                    "tax_amount": round(tax, 2),
                    "total_amount": round(total, 2),
                    "status": "DRAFT",
                    "source": "demand_forecast",
                    "forecast_horizon_days": horizon,
                    "created_by": current_user.get("user_id"),
                    "created_at": now.isoformat(),
                    "notes": (
                        f"Auto-generated from {horizon}-day demand forecast "
                        f"(safety stock {safety} days)"
                    ),
                }

                if po_repo is not None:
                    try:
                        po_repo.create(po_doc)
                        created_pos.append(
                            {
                                "po_id": po_id,
                                "po_number": po_number,
                                "vendor_id": v_id,
                                "vendor_name": po_doc["vendor_name"],
                                "lines": len(lines),
                                "total_amount": round(total, 2),
                            }
                        )
                    except Exception as _e:
                        logger.warning(
                            f"[INV-9] PO create failed for vendor {v_id}: {_e}"
                        )

        return {
            "store_id": active_store,
            "dry_run": body.dry_run,
            "horizon_days": horizon,
            "safety_stock_days": safety,
            "products_needing_reorder": len(suggestions),
            "pos_created": len(created_pos),
            "created_pos": created_pos,
            "suggestions": suggestions,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"create_pos_from_forecast error: {e}")
        return {
            "store_id": active_store,
            "dry_run": body.dry_run,
            "pos_created": 0,
            "suggestions": [],
            "message": "Forecast data unavailable",
        }


@router.post("/purchase-orders", status_code=201)
async def create_po(
    po: POCreate, current_user: dict = Depends(require_roles(*_VENDOR_ROLES))
):
    """Create a new purchase order"""
    po_repo = get_purchase_order_repository()
    vendor_repo = get_vendor_repository()

    # F2 store boundary: a store-scoped role may only raise a PO for a store it
    # can access. validate_store_access 403s another store; ADMIN / AREA_MANAGER /
    # SUPERADMIN pass. Without this, delivery_store_id came straight off the
    # request body, so a store-scoped user could craft a PO against another
    # store's inventory.
    validate_store_access(po.delivery_store_id, current_user)

    # W1.4 / OS-006: an ONLINE store (pooled, stockless) can never be the
    # delivery store -- receiving there would mint phantom owned stock that
    # corrupts the pooled-inventory model feeding the live storefront.
    if is_online_store(None, po.delivery_store_id):
        raise HTTPException(
            status_code=400,
            detail=(
                "Online stores hold no stock - choose a physical shop as the "
                "delivery store for this purchase order."
            ),
        )

    po_id = str(uuid.uuid4())
    po_number = generate_po_number(po.delivery_store_id)

    # Validate vendor exists
    if vendor_repo is not None:
        vendor = vendor_repo.find_by_id(po.vendor_id)
        if vendor is None:
            raise HTTPException(status_code=404, detail="Vendor not found")

    # Ruling 13 -- BUY FIRST, CATALOGUE LATER. Any line that carried typed-in
    # identity instead of a product_id becomes a REAL row on the products spine
    # here, through the ONE product door, born provisional: inactive, no selling
    # price, catalog_status DRAFT. That keeps product_id the single join key for
    # receiving, the stock mint, the invoice and the 3-way match, instead of
    # forking identity into a second placeholder system.
    product_repo = get_product_repository()
    for it in po.items:
        if it.new_product is None:
            continue
        np = it.new_product
        try:
            created = _pm.create_via_door(
                {
                    "category": np.category,
                    "brand": np.brand,
                    "model": np.model,
                    "colour": np.colour,
                    "size": np.size,
                    "mrp": np.mrp,
                    # The PO rate is the PROVISIONAL cost (ruling 10); the
                    # purchase invoice corrects it to the actual one (ruling 12).
                    "cost_price": it.unit_price or None,
                    "as_draft": True,
                    "provisional": True,
                },
                source="FORM",
                actor=current_user.get("user_id"),
                actor_name=current_user.get("username"),
                product_repo=product_repo,
                audit_repo=get_audit_repository(),
                db=_get_db(),
            )
        except _pm.ProductMasterError as err:
            # An identical brand+model+colour+size already exists: reuse it
            # rather than refusing the order or minting a twin. The buyer has
            # just typed a description of a product we already know.
            if err.status == 409 and (err.conflict or {}).get("product_id"):
                it.product_id = err.conflict["product_id"]
                it.product_name = it.product_name or err.conflict.get("name")
                it.sku = it.sku or err.conflict.get("sku")
                it.new_product = None
                continue
            raise HTTPException(
                status_code=err.status,
                detail={
                    "code": "NEW_PRODUCT_INVALID",
                    "message": err.message,
                    "field": err.field,
                },
            ) from err
        it.product_id = created.get("product_id")
        it.product_name = (
            it.product_name
            or created.get("name")
            or f"{np.brand} {np.model}".strip()
        )
        it.sku = created.get("sku")
        it.new_product = None

    # Hub Phase 2: every PO line must reference a REAL catalogued product on the
    # `products` spine. This rejects a fabricated / placeholder id (e.g. the UI's
    # old `new-<timestamp>` id) at PO creation, so a PO can never carry a line
    # that GRN would later mint as ghost stock. Gated behind pm.po_catalog_gate
    # (DARK by default) so the existing free-text Create-PO form keeps working
    # until the Buy Desk picker ships. Fail-soft when no product repo. A line
    # that arrived as a typed-in new product has just been given a real id
    # above, so it passes this gate like any other.
    if product_repo is not None and _po_catalog_gate_on():
        unknown = [
            it.product_id
            for it in po.items
            if product_repo.find_by_id(it.product_id) is None
        ]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": (
                        "One or more PO lines reference an unknown product. "
                        "Catalog the product first, then add it to the PO."
                    ),
                    "code": "UNKNOWN_PRODUCT",
                    "product_ids": unknown,
                },
            )

    # Calculate totals with PER-LINE, server-resolved GST (was a flat 18% that
    # both over-taxed the PO and -- because lines stored no tax_rate -- made the
    # downstream invoice draft compute 0% tax). Each stored line carries its
    # resolved tax_rate + hsn + ordered/received residual fields the receiving
    # cockpit and reconciliation console read.
    from ..services.gst_rates import resolve_gst_rate

    subtotal = 0.0
    tax = 0.0
    stored_items = []
    for item in po.items:
        line_total = item.quantity * item.unit_price
        prod = (
            product_repo.find_by_id(item.product_id)
            if (product_repo is not None and item.gst_rate is None)
            else None
        ) or {}
        rate = (
            item.gst_rate
            if item.gst_rate is not None
            else resolve_gst_rate(
                hsn_code=item.hsn or prod.get("hsn_code"),
                category=item.category or prod.get("category"),
            )
        )
        line_tax = round(line_total * (rate / 100.0), 2)
        subtotal += line_total
        tax += line_tax
        stored_items.append(
            {
                **item.model_dump(exclude={"new_product"}),
                "tax_rate": rate,
                "hsn": item.hsn or prod.get("hsn_code"),
                "line_tax": line_tax,
                "ordered_qty": item.quantity,
                "received_qty": 0,
                "line_status": "OPEN",
            }
        )
    subtotal = round(subtotal, 2)
    tax = round(tax, 2)
    total = round(subtotal + tax, 2)

    if po_repo is not None:
        po_repo.create(
            {
                "po_id": po_id,
                "po_number": po_number,
                "vendor_id": po.vendor_id,
                "vendor_name": (
                    vendor.get("trade_name")
                    if vendor_repo is not None and vendor
                    else None
                ),
                "delivery_store_id": po.delivery_store_id,
                "items": stored_items,
                "subtotal": subtotal,
                "tax_amount": tax,
                "total_amount": total,
                "expected_date": po.expected_date,
                "notes": po.notes,
                "status": "DRAFT",
                "created_by": current_user.get("user_id"),
                "created_at": datetime.now().isoformat(),
            }
        )

    return {
        "po_id": po_id,
        "po_number": po_number,
        "total_amount": total,
        "message": "Purchase order created",
    }


@router.get("/goods-receipt/cockpit")
async def goods_receipt_cockpit(
    vendor_id: str = Query(..., description="Vendor to receive against"),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """Vendor-first goods-receipt cockpit (Purchase P1 / S2).

    One read-only payload with the three worklists the receiving screen needs:
      * open_pos -- this vendor's receivable POs that still have unreceived lines
      * pending_not_received -- per-product residual (ordered - received) summed
        across those open POs
      * pending_cataloged -- ACTIVE cataloged products not already on an open PO
        (products carry no vendor link today, so this list is vendor-agnostic --
        cataloged items ready to be put on a PO / received; capped at 200).

    Residuals read the per-line ordered_qty/received_qty (S1) and fall back to
    the PO header received_qty_by_product for POs created before S1.
    """
    po_repo = get_purchase_order_repository()
    product_repo = get_product_repository()

    open_pos: list = []
    pending: dict = {}
    ordered_product_ids: set = set()

    if po_repo is not None:
        flt: dict = {
            "vendor_id": vendor_id,
            "status": {"$in": list(_RECEIVABLE_PO_STATUSES)},
        }
        # F2 store boundary: resolve the effective store filter for the caller.
        # An explicit store_id is validated (403 if a store-scoped role asks for
        # another store); when omitted, SUPERADMIN/ADMIN see all stores while a
        # store-scoped role is pinned to its OWN active store -- so a store role
        # can no longer see every store's open POs by leaving store_id blank.
        scoped_store = resolve_store_scope(store_id, current_user)
        if scoped_store:
            flt["delivery_store_id"] = scoped_store
        for po in po_repo.find_many(flt, limit=500) or []:
            header_recv = po.get("received_qty_by_product") or {}
            open_lines: list = []
            for it in po.get("items") or []:
                pid = it.get("product_id")
                ordered = it.get("ordered_qty", it.get("quantity", 0)) or 0
                recv = it.get("received_qty")
                if recv is None:
                    recv = header_recv.get(pid, 0)
                recv = recv or 0
                if pid:
                    ordered_product_ids.add(pid)
                if ordered and recv < ordered:
                    residual = ordered - recv
                    open_lines.append(
                        {
                            "product_id": pid,
                            "product_name": it.get("product_name"),
                            "sku": it.get("sku"),
                            "ordered_qty": ordered,
                            "received_qty": recv,
                            "pending_qty": residual,
                            "unit_price": it.get("unit_price"),
                            "tax_rate": it.get("tax_rate"),
                        }
                    )
                    roll = pending.setdefault(
                        pid,
                        {
                            "product_id": pid,
                            "product_name": it.get("product_name"),
                            "sku": it.get("sku"),
                            "ordered_qty": 0,
                            "received_qty": 0,
                            "pending_qty": 0,
                        },
                    )
                    roll["ordered_qty"] += ordered
                    roll["received_qty"] += recv
                    roll["pending_qty"] += residual
            if open_lines:
                open_pos.append(
                    {
                        "po_id": po.get("po_id"),
                        "po_number": po.get("po_number"),
                        "status": po.get("status"),
                        "expected_date": po.get("expected_date"),
                        "lines": open_lines,
                    }
                )

    pending_cataloged: list = []
    if product_repo is not None:
        try:
            actives = product_repo.find_many({"is_active": True}, limit=500) or []
        except Exception:  # noqa: BLE001
            actives = []
        for p in actives:
            pid = p.get("product_id")
            if pid in ordered_product_ids:
                continue
            try:
                status, _gaps = _pm.compute_catalog_status(p)
            except Exception:  # noqa: BLE001
                continue
            if status == "ACTIVE":
                pending_cataloged.append(
                    {
                        "product_id": pid,
                        "product_name": p.get("product_name") or p.get("name"),
                        "sku": p.get("sku"),
                        "category": p.get("category"),
                    }
                )
            if len(pending_cataloged) >= 200:
                break

    return {
        "vendor_id": vendor_id,
        "open_pos": open_pos,
        "pending_not_received": list(pending.values()),
        "pending_cataloged": pending_cataloged,
    }


@router.get("/last-cost")
async def get_last_purchase_cost(
    vendor_id: str = Query(..., description="Vendor to look up prior prices for"),
    product_ids: str = Query(..., description="Comma-separated product_ids to price"),
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """Most-recent agreed purchase price per product for this vendor, from PO
    history -- so the PO / Buy-Desk form can pre-fill "last paid Rs X on <date>"
    instead of the operator guessing the cost (procurement Phase 2C).

    Reads the vendor's POs newest-first (capped) and takes the first line hit
    per requested product_id. Read-only, fail-soft: DB trouble or no history
    yields an empty map (the form then just shows a blank cost). Registered
    ABOVE /purchase-orders/{po_id} so the literal path wins.

    Shape: {"costs": {product_id: {unit_price, po_number, po_id, date}, ...}}.
    """
    wanted = {p.strip() for p in (product_ids or "").split(",") if p.strip()}
    if not vendor_id or not wanted:
        return {"costs": {}}

    po_repo = get_purchase_order_repository()
    if po_repo is None:
        return {"costs": {}}

    costs: dict = {}
    try:
        # Newest POs for this vendor first; walk lines until every requested
        # product has a price (or the cap is hit).
        pos = po_repo.find_many(
            {"vendor_id": vendor_id},
            sort=[("created_at", -1)],
            limit=100,
        )
        for po in pos or []:
            # Only surface prices from stores the caller may see (cross-store
            # roles pass); never leak another store's negotiated cost.
            if not can_access_store_scoped(po.get("delivery_store_id"), current_user):
                continue
            for it in po.get("items", []) or []:
                if not isinstance(it, dict):
                    continue
                pid = it.get("product_id")
                if pid not in wanted or pid in costs:
                    continue
                try:
                    price = round(float(it.get("unit_price") or 0), 2)
                except (TypeError, ValueError):
                    continue
                if price <= 0:
                    continue
                costs[pid] = {
                    "unit_price": price,
                    "po_number": po.get("po_number"),
                    "po_id": po.get("po_id"),
                    "date": po.get("created_at"),
                }
            if len(costs) >= len(wanted):
                break
    except Exception as e:  # noqa: BLE001 - read-only helper, never a blocker
        logger.warning("[VENDOR] last-cost lookup failed: %s", e)
        return {"costs": {}}

    return {"costs": costs}


def _stamp_event_actors(events: list) -> None:
    """Replace each event's raw ``actor`` user id with a display name in ``by``.

    In place, batched (one users read for the whole timeline), fail-soft: on
    any lookup problem the ids still surface as ``by`` rather than vanishing.
    """
    names: dict = {}
    try:
        from ..services.name_resolver import user_name_map

        names = user_name_map(_get_db(), [e.get("actor") for e in events])
    except Exception:  # noqa: BLE001
        names = {}
    for e in events:
        actor = e.pop("actor", None)
        if actor:
            e["by"] = names.get(str(actor)) or str(actor)


@router.get("/purchase-orders/{po_id}/timeline")
async def get_po_timeline(po_id: str, current_user: dict = Depends(get_current_user)):
    """The full life of a PO on one read (procurement Phase 3): ordered ->
    sent -> box received (GRNs) -> on shelf (accepted) -> bill (purchase
    invoices). One click from any PO number opens the drawer that renders this.

    Read-only; store-scoped exactly like get_po (a store role only sees its own
    PO -> 404 otherwise). Fail-soft: a GRN/invoice lookup problem degrades to a
    shorter timeline, never a 5xx. Returns chronological events with the owner-
    facing five-word status vocabulary the FE chip maps.

    Shape: {"po_id", "po_number", "status", "events": [{kind, label, at, ref,
    detail}], "grns": [...], "invoices": [...]}.
    """
    po_repo = get_purchase_order_repository()
    if po_repo is None:
        return {"po_id": po_id, "events": []}

    po = po_repo.find_by_id(po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    # Same object-level store boundary as get_po (hide other stores' POs).
    if not can_access_store_scoped(po.get("delivery_store_id"), current_user):
        raise HTTPException(status_code=404, detail="Purchase order not found")

    events: list = []
    events.append(
        {
            "kind": "ordered",
            "label": "Ordered",
            "at": po.get("created_at"),
            "ref": po.get("po_number"),
            "actor": po.get("created_by"),
        }
    )
    if po.get("sent_at"):
        events.append(
            {
                "kind": "sent",
                "label": "Sent",
                "at": po.get("sent_at"),
                "ref": po.get("po_number"),
                "actor": po.get("sent_by"),
                "detail": "PO sent to the vendor",
            }
        )
    if po.get("cancelled_at"):
        events.append(
            {
                "kind": "cancelled",
                "label": "Cancelled",
                "at": po.get("cancelled_at"),
                "ref": po.get("po_number"),
                "actor": po.get("cancelled_by"),
                "detail": po.get("cancellation_reason") or "PO cancelled",
            }
        )

    # GRNs against this PO -> "Box received" (PENDING) + "On shelf" (ACCEPTED).
    grns_out: list = []
    grn_ids: list = []
    try:
        grn_repo = get_grn_repository()
        if grn_repo is not None:
            grns = grn_repo.find_many({"po_id": po_id}, limit=200) or []
            for g in grns:
                gid = g.get("grn_id")
                if gid:
                    grn_ids.append(gid)
                grns_out.append(
                    {
                        "grn_id": gid,
                        "grn_number": g.get("grn_number"),
                        "status": g.get("status"),
                        "created_at": g.get("created_at"),
                        "accepted_at": g.get("accepted_at"),
                        "total_accepted": g.get("total_accepted"),
                    }
                )
                if g.get("status") == "VOID":
                    continue
                events.append(
                    {
                        "kind": "box_received",
                        "label": "Box received",
                        "at": g.get("created_at"),
                        "ref": g.get("grn_number"),
                        "actor": g.get("created_by"),
                        "detail": f"Goods receipt logged ({g.get('total_received') or 0} units)",
                    }
                )
                if g.get("accepted_at"):
                    events.append(
                        {
                            "kind": "on_shelf",
                            "label": "On shelf",
                            "at": g.get("accepted_at"),
                            "ref": g.get("grn_number"),
                            "actor": g.get("accepted_by"),
                            "detail": f"{g.get('total_accepted') or 0} units accepted into stock",
                        }
                    )
    except Exception as e:  # noqa: BLE001
        logger.warning("[VENDOR] po-timeline grn lookup failed: %s", e)

    # Purchase invoices linked to this PO or any of its GRNs -> "Bill settled".
    invoices_out: list = []
    try:
        db = _get_db()
        if db is not None:
            or_terms: list = [{"po_id": po_id}]
            if grn_ids:
                or_terms.append({"grn_id": {"$in": grn_ids}})
            rows = list(
                db.get_collection("vendor_bills").find(
                    {"doc_type": "PURCHASE_INVOICE", "$or": or_terms},
                    {"_id": 0},
                )
            )
            for r in rows:
                invoices_out.append(
                    {
                        "bill_id": r.get("bill_id"),
                        "invoice_number": r.get("invoice_number")
                        or r.get("bill_number"),
                        "status": r.get("status"),
                        "total": r.get("total"),
                        "created_at": r.get("created_at"),
                    }
                )
                events.append(
                    {
                        "kind": "bill_settled",
                        "label": "Bill settled",
                        "at": r.get("created_at"),
                        "ref": r.get("invoice_number") or r.get("bill_number"),
                        "actor": r.get("created_by"),
                        "detail": f"Purchase invoice booked ({r.get('status') or 'OUTSTANDING'})",
                    }
                )
    except Exception as e:  # noqa: BLE001
        logger.warning("[VENDOR] po-timeline invoice lookup failed: %s", e)

    # Chronological (blank timestamps sort last, stable).
    events.sort(key=lambda e: (e.get("at") is None, e.get("at") or ""))

    # WHO did it. Every writer stamps a user_id ("user-superadmin"), never a
    # name, so the drawer used to print that id straight into the prose -- an
    # audit trail that cannot name the person is not an audit trail. Resolve
    # every stamped actor in ONE query (same helper + fail-soft shape as
    # _enrich_grn_names). Unresolvable id -> keep the id verbatim (traceable,
    # and never an invented name); nothing stamped -> no "by" at all.
    _stamp_event_actors(events)

    return {
        "po_id": po_id,
        "po_number": po.get("po_number"),
        "status": po.get("status"),
        "vendor_id": po.get("vendor_id"),
        "vendor_name": po.get("vendor_name"),
        "delivery_store_id": po.get("delivery_store_id"),
        "events": events,
        "grns": grns_out,
        "invoices": invoices_out,
    }


@router.get("/purchase-orders/{po_id}")
async def get_po(po_id: str, current_user: dict = Depends(get_current_user)):
    """Get purchase order details"""
    po_repo = get_purchase_order_repository()

    if po_repo is None:
        return {"po_id": po_id}

    po = po_repo.find_by_id(po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    # F2 object-level store boundary: a store-scoped role may only read a PO for
    # its own store. Cross-store roles pass; otherwise 404 (hide existence of
    # other stores' POs, mirroring the GRN read guards).
    if not can_access_store_scoped(po.get("delivery_store_id"), current_user):
        raise HTTPException(status_code=404, detail="Purchase order not found")

    return po


@router.post("/purchase-orders/{po_id}/send")
async def send_po(
    po_id: str, current_user: dict = Depends(require_roles(*_VENDOR_ROLES))
):
    """Send PO to vendor (mark as sent)"""
    po_repo = get_purchase_order_repository()

    if po_repo is not None:
        po = po_repo.find_by_id(po_id)
        if not po:
            raise HTTPException(status_code=404, detail="Purchase order not found")

        # F2 object-level store boundary: a store-scoped role may only send a PO
        # for its own store (cross-store roles pass; else 404).
        if not can_access_store_scoped(po.get("delivery_store_id"), current_user):
            raise HTTPException(status_code=404, detail="Purchase order not found")

        if po.get("status") != "DRAFT":
            raise HTTPException(status_code=400, detail="Only draft POs can be sent")

        # Hub Phase 2 SENT gate: a PO may be DRAFTED against an incomplete product,
        # but cannot be SENT to the vendor until every line is catalog-complete.
        # cost_price is the ONE allowed gap -- it legitimately arrives at GRN (the
        # receiving flow backfills it from this PO), so a product that is DRAFT
        # ONLY because cost is unknown is still sendable. Any OTHER gap (missing
        # category attribute, mrp/offer, hsn/gst) blocks the send. Fail-soft when
        # no product repo.
        #
        # This gate governs ONLY manually-entered PO lines (the Create-PO form's
        # spine-product picker). Auto-generated POs carry a `source`:
        # cl_po lens replenishment ("cl_po_generator") and demand-forecast
        # ("demand_forecast") source their lines from system data (lens_catalog
        # needs / sales history) whose ids are NOT on the products spine, and were
        # never gated before pm.po_catalog_gate defaulted ON. We therefore skip
        # the gate for any PO bearing a `source`, mirroring the create-side gate
        # which only fires inside the manual create_po endpoint those flows bypass.
        # Without this, every cl_po/forecast DRAFT would 400 PO_LINES_INCOMPLETE.
        product_repo = get_product_repository()
        if product_repo is not None and _po_catalog_gate_on() and not po.get("source"):
            blocked = []
            for it in po.get("items", []) or []:
                pid = it.get("product_id")
                prod = product_repo.find_by_id(pid) if pid else None
                if prod is None:
                    blocked.append(
                        {"product_id": pid, "missing": ["product_not_found"]}
                    )
                    continue
                # Ruling 13: a PROVISIONAL row exists precisely because the buyer
                # is ordering something nobody has catalogued yet. Blocking the
                # send on its (inevitable) gaps would put the obstacle back at
                # the front of the flow. The strictness now lives at the INVOICE
                # (ruling 15), which refuses to settle an incomplete product.
                if prod.get("provisional"):
                    continue
                gaps = set(_pm.compute_catalog_status(prod)[1]) - {"cost_price"}
                if gaps:
                    blocked.append({"product_id": pid, "missing": sorted(gaps)})
            if blocked:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "message": (
                            "Cannot send this PO: some lines are not catalog-"
                            "complete. Finish cataloguing them, then send."
                        ),
                        "code": "PO_LINES_INCOMPLETE",
                        "lines": blocked,
                    },
                )

        po_repo.update(
            po_id,
            {
                "status": "SENT",
                "sent_at": datetime.now().isoformat(),
                "sent_by": current_user.get("user_id"),
            },
        )

    return {"message": "PO sent to vendor", "po_id": po_id}


@router.post("/purchase-orders/{po_id}/cancel")
async def cancel_po(
    po_id: str,
    reason: str = Query(...),
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """Cancel a purchase order"""
    po_repo = get_purchase_order_repository()

    if po_repo is not None:
        po = po_repo.find_by_id(po_id)
        if not po:
            raise HTTPException(status_code=404, detail="Purchase order not found")

        # F2 object-level store boundary: a store-scoped role may only cancel a
        # PO for its own store (cross-store roles pass; else 404).
        if not can_access_store_scoped(po.get("delivery_store_id"), current_user):
            raise HTTPException(status_code=404, detail="Purchase order not found")

        # A PARTIALLY_RECEIVED PO has stock already in the warehouse. Cancelling
        # it would orphan those stock units (no live PO to trace back to) and
        # leave the GRN with a reference to a cancelled order. Block it -- the
        # operator must raise a debit note for the unreceived portion instead.
        if po.get("status") in [
            "RECEIVED",
            "CANCELLED",
            "PARTIALLY_RECEIVED",
            "PARTIAL",
        ]:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Cannot cancel this PO. A fully or partially received PO "
                    "cannot be cancelled because stock has already been posted "
                    "against it. Raise a debit note for any unreceived portion."
                ),
            )

        po_repo.update(
            po_id,
            {
                "status": "CANCELLED",
                "cancelled_at": datetime.now().isoformat(),
                "cancelled_by": current_user.get("user_id"),
                "cancellation_reason": reason,
            },
        )

    return {"message": "PO cancelled", "po_id": po_id}


# ============================================================================
# GRN (GOODS RECEIVED NOTE) ENDPOINTS
# ============================================================================


@router.get("/grn")
async def list_grns(
    store_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    po_id: Optional[str] = Query(None),
    # F9: Delivery-Challan filters. The accountant's open-DC panel queries
    # grn_subtype=DELIVERY_CHALLAN & dc_matched=false & vendor_id=X & status=ACCEPTED
    # to pick the DCs to reconcile into one bulk invoice.
    grn_subtype: Optional[str] = Query(None),
    dc_matched: Optional[bool] = Query(None),
    vendor_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="DC date >= (ISO)"),
    date_to: Optional[str] = Query(None, description="DC date <= (ISO)"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """List GRNs with filters (incl. F9 Delivery-Challan filters)."""
    grn_repo = get_grn_repository()
    active_store = validate_store_access(store_id, current_user) or current_user.get(
        "active_store_id"
    )

    if grn_repo is None:
        return {"grns": [], "total": 0}

    filter_dict: dict = {}
    if active_store:
        filter_dict["store_id"] = active_store
    if status:
        filter_dict["status"] = status
    if po_id:
        filter_dict["po_id"] = po_id
    if grn_subtype:
        # Normalise to the canonical subtype string.
        sub = str(grn_subtype).strip().upper().replace("-", "_")
        if sub in _GRN_SUBTYPES:
            filter_dict["grn_subtype"] = sub
    if dc_matched is not None:
        filter_dict["dc_matched"] = bool(dc_matched)
    if vendor_id:
        filter_dict["vendor_id"] = vendor_id
    # dc_date range filter (string ISO compares lexicographically for YYYY-MM-DD).
    if date_from or date_to:
        rng: dict = {}
        if date_from:
            rng["$gte"] = date_from
        if date_to:
            rng["$lte"] = date_to
        filter_dict["dc_date"] = rng

    grns = grn_repo.find_many(filter_dict, skip=skip, limit=limit)

    _enrich_grn_names(grns or [])

    return {"grns": grns or [], "total": len(grns) if grns else 0}


def _enrich_grn_names(grns: list) -> None:
    """backlog #4: add created_by_name / vendor_name beside the raw ids on GRN
    rows so the UI shows who received the goods (a name, not a UUID). In-place,
    batched, fail-soft."""
    if not grns:
        return
    try:
        from ..services.name_resolver import user_name_map, vendor_name_map

        db = _get_db()
        umap = user_name_map(db, [g.get("created_by") for g in grns])
        vmap = vendor_name_map(db, [g.get("vendor_id") for g in grns])
        for g in grns:
            cb = g.get("created_by")
            if cb and not g.get("created_by_name") and str(cb) in umap:
                g["created_by_name"] = umap[str(cb)]
            vid = g.get("vendor_id")
            if vid and not g.get("vendor_name") and str(vid) in vmap:
                g["vendor_name"] = vmap[str(vid)]
    except Exception:  # noqa: BLE001
        pass


@router.post("/grn/upload-doc")
async def upload_grn_doc(
    file: UploadFile = File(...),
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """F-S3: upload the goods-receipt document (vendor invoice/challan image or
    PDF) and get back a file_id to attach to the GRN.

    The ops user (Superadmin/Admin/Store Manager) uploads the receipt FIRST,
    then submits the GRN with the returned file_id. create_grn rejects a
    STANDARD GRN that has no attachment_file_id (ATTACHMENT_REQUIRED), so this
    is the only way to clear the gate. Persists the bytes durably in the
    GridFS-backed file store (Railway disk is ephemeral) -- mirrors the
    expenses upload-bill pattern: size + MIME validation, then store.put(...).
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    # Read + validate before persisting anything.
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB cap",
        )
    mime = (file.content_type or "").lower()
    if mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"File type '{mime}' not allowed. Accepted: "
                f"{sorted(ALLOWED_MIME_TYPES)}"
            ),
        )

    store = get_file_store()
    if store is None:
        # Storage unavailable: fail LOUD with 503 so the UI keeps the user on
        # the upload step rather than letting them proceed paperwork-less.
        raise HTTPException(status_code=503, detail="File storage unavailable")

    sha256 = hashlib.sha256(content).hexdigest()
    # The stamped store is what authorises this blob at GRN-create time, so a
    # blob we cannot stamp is a blob that will be refused later with a message
    # that reads like a forged id. Fail LOUD here instead of minting it: the
    # caller can pick a store and retry, rather than looping on an upload that
    # can never be attached.
    upload_store = current_user.get("active_store_id")
    if not upload_store:
        raise HTTPException(
            status_code=400,
            detail=(
                "Select a store before uploading a goods-receipt document -- "
                "the document is filed against the receiving store."
            ),
        )

    file_id = store.put(
        content=content,
        filename=file.filename,
        mime_type=mime,
        metadata={
            "kind": "grn_document",
            "store_id": upload_store,
            "uploaded_by": current_user.get("user_id"),
            "sha256": sha256,
        },
    )
    if not file_id:
        raise HTTPException(status_code=500, detail="File store write failed")

    return {
        "file_id": file_id,
        "filename": file.filename,
        "mime": mime,
        "size": len(content),
        "sha256": sha256,
        "persisted": True,
    }


@router.get("/grn/{grn_id}/document")
async def download_grn_doc(
    grn_id: str,
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """F-S3: stream the goods-receipt document attached to a GRN.

    The accountant reconciliation console links here to view the source invoice/
    challan the ops user uploaded at receipt. Store-scoped: a GRN outside the
    caller's store scope reads as 404 (no cross-store document leak)."""
    grn_repo = get_grn_repository()
    if grn_repo is None:
        raise HTTPException(status_code=503, detail="Database not available")

    grn = grn_repo.find_one({"grn_id": grn_id})
    if grn is None:
        raise HTTPException(status_code=404, detail="GRN not found")

    # Store-scope (SEC #2 object-level pattern): cross-store roles
    # (SUPERADMIN/ADMIN) may read any GRN's document; a store-level caller can
    # only read GRNs stamped with one of their stores. A mismatch reads as 404
    # (not 403) so a document's existence in another store isn't disclosed.
    if not can_access_store_scoped(grn.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="GRN not found")

    file_id = grn.get("attachment_file_id")
    if not file_id:
        raise HTTPException(status_code=404, detail="No document attached to this GRN")

    store = get_file_store()
    if store is None:
        raise HTTPException(status_code=503, detail="File storage unavailable")

    # Defence in depth behind the create-time kind check: this route only ever
    # streams THIS router's own kind of file, so a foreign id persisted on a GRN
    # (legacy row, future write path) reads as "no longer available", not bytes.
    rec = store.get(file_id, require_kind=_GRN_DOCUMENT_KIND)
    if rec is None:
        raise HTTPException(status_code=404, detail="Document file no longer available")

    file_content, filename, file_mime = rec
    return StreamingResponse(
        io.BytesIO(file_content),
        media_type=file_mime,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.post("/grn", status_code=201)
async def create_grn(
    grn: GRNCreate, current_user: dict = Depends(require_roles(*_VENDOR_ROLES))
):
    """Create a new GRN (STANDARD) or log a Delivery Challan (F9 DC subtype)."""
    return await _create_grn_impl(grn, current_user)


async def _create_grn_impl(grn: GRNCreate, current_user: dict) -> dict:
    """Shared GRN-create engine behind POST /grn (and POST /grn/express).

    Behavior-preserving extraction of the original create_grn body so the
    express receiving chain can run the SAME validation + persistence path --
    attachment gate (F-S3 + BUG-010 file-exists), PO receivable check, F2
    store re-point to the PO's delivery store, per-store numbering and the DC
    guards -- without duplicating any control. Callers pass the authenticated
    ``current_user`` their own ``require_roles(*_VENDOR_ROLES)`` gate produced.
    """
    grn_repo = get_grn_repository()
    po_repo = get_purchase_order_repository()

    grn_id = str(uuid.uuid4())
    store_id = current_user.get("active_store_id")
    is_dc = grn.grn_subtype == GRN_SUBTYPE_DC
    # grn_number is generated AFTER the receiving store is finalised (a standard
    # PO-backed GRN is re-pointed to the PO's delivery store below), so the
    # per-store serial reflects the store the goods are actually booked to.

    # F-S3: mandatory goods-receipt document. The ops user physically receiving a
    # STANDARD shipment MUST attach the vendor invoice/challan (image or PDF)
    # before the GRN is created -- so the accountant always has the source doc to
    # reconcile against and there is no "received with no paperwork" hole. The
    # file is uploaded first via POST /vendors/grn/upload-doc (returns file_id),
    # which already validated size + MIME, so here we only assert it is present.
    # A DELIVERY_CHALLAN is exempt at receipt (its tax invoice arrives later and
    # is attached at reconciliation). Fail LOUD: a 400 with a stable code the UI
    # keys on to keep the user on the upload step.
    if not is_dc and not (
        grn.attachment_file_id and str(grn.attachment_file_id).strip()
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "ATTACHMENT_REQUIRED",
                "message": (
                    "Attach the vendor invoice or challan (image or PDF) before "
                    "creating the goods receipt."
                ),
            },
        )

    # BUG-010: the presence check above only proves a NON-EMPTY id was sent -- a
    # forged / stale id would pass the gate and persist, only 404'ing later at
    # download time, defeating the mandatory-attachment guarantee. Verify the
    # file actually exists in the store BEFORE persisting. Only STANDARD GRNs are
    # gated (a DC has no receipt-time attachment to verify).
    # Storage-down vs forged-id: get_file_store() returning None means storage
    # itself is unavailable -> 503 (do NOT mask the existing fail-loud behavior
    # by 400'ing). A live store whose get() finds nothing == a forged/stale id
    # -> 400 ATTACHMENT_INVALID.
    #
    # P0 (security panel round 6): this gate used to run under `if not is_dc`,
    # while the persistence below wrote attachment_file_id UNCONDITIONALLY -- so
    # adding one JSON field, grn_subtype="DELIVERY_CHALLAN", skipped BOTH the
    # kind check and the store bind and re-opened the round-5 theft verbatim.
    # The gate now runs whenever an attachment id is PRESENT, whatever the
    # subtype; the DC exemption is only from the REQUIREMENT to attach (above),
    # never from authorising an attachment that was sent anyway.
    _attachment_meta = None
    if grn.attachment_file_id and str(grn.attachment_file_id).strip():
        store = get_file_store()
        if store is None:
            raise HTTPException(status_code=503, detail="File storage unavailable")
        # P0 (security panel): existence is NOT authorisation. The id is
        # supplied by the CALLER (GRNCreate.attachment_file_id) and ONE bucket
        # holds every binary in the app, so an existence check passes just as
        # happily for a task attachment or an employee Aadhaar scan.
        # Kind is checked here; the STORE bind runs below, once the receiving
        # store is final (a PO-backed GRN is re-pointed to the PO's store).
        _attachment_meta = store.get_metadata(str(grn.attachment_file_id).strip())
        if (
            _attachment_meta is None
            or _attachment_meta.get("kind") != _GRN_DOCUMENT_KIND
        ):
            raise HTTPException(
                status_code=400,
                detail=_ATTACHMENT_INVALID_DETAIL,
            )

    # Validate PO exists. For a DC, the PO is optional (lens top-ups arrive with
    # no pre-logged PO) -- only validate when one was supplied.
    po = None
    if po_repo is not None and grn.po_id:
        po = po_repo.find_by_id(grn.po_id)
        if not po:
            raise HTTPException(status_code=404, detail="Purchase order not found")

        if po.get("status") not in _RECEIVABLE_PO_STATUSES:
            raise HTTPException(
                status_code=400, detail="PO is not in receivable status"
            )

        # F2 store boundary: a STANDARD PO-backed GRN is received at the PO's
        # delivery store -- not blindly the caller's active store, else stock from
        # another store's PO could be mis-credited here (the GRN was stamped to
        # active_store_id with no PO cross-check). Re-point store_id to the PO's
        # store and verify the caller may act on it: cross-store roles (ADMIN /
        # AREA_MANAGER / SUPERADMIN) pass; a store-scoped role only for its own
        # store, else 404 (hiding other stores' POs). DC / PO-less receipts stay
        # on the caller's active store.
        if not is_dc:
            po_store = po.get("delivery_store_id")
            if po_store:
                if not can_access_store_scoped(po_store, current_user):
                    raise HTTPException(
                        status_code=404, detail="Purchase order not found"
                    )
                store_id = po_store

    # P0 STORE BIND (security panel round 5, reproduced end to end). Checking
    # only the KIND still let a caller LAUNDER another store's document: the
    # download route is scoped by the GRN RECORD
    # (can_access_store_scoped(grn["store_id"])), so binding a victim's
    # grn_document file_id to a GRN in YOUR OWN store walks it straight past
    # that scope. A STORE_MANAGER at WO-JSR-01 bound an ACCOUNTANT's
    # BV-RANCHI-01 supplier invoice -- a different store AND a different legal
    # entity -- and streamed it back, cost prices, vendor terms and the real
    # filename included, while the front door correctly 404'd.
    #
    # POST /vendors/grn/upload-doc already stamps store_id, so binding the store
    # costs the receiving flow nothing: the live UI uploads and creates from the
    # same component. The UPLOADER is deliberately NOT bound -- that would
    # foreclose an ops-uploads / accountant-creates split -- but "don't bind the
    # uploader" never implied "bind nothing".
    #
    # The test is the caller's REACHABLE stores, not the receiving store.
    # Comparing the blob's stamp to the post-re-point store_id (my round-6
    # attempt) BROKE THE FORWARD RECEIVING FLOW: upload-doc stamps the uploader's
    # ACTIVE store, while a PO-backed GRN is re-pointed to the PO's delivery
    # store, so an ADMIN active at A receiving store B's PO was 400'd -- and
    # re-uploading produced the same stamp, an unescapable loop, with the
    # deliberately indistinguishable message making an outage look like a forged
    # id. The repo's own test_po_store_boundary states that intent.
    #
    # can_access_store_scoped is the canonical helper and gets both directions
    # right: a store-level thief cannot reach the victim's store, so the round-5
    # theft is still refused; a cross-store ADMIN/AREA_MANAGER is granted nothing
    # they could not already read through the download route. A blob with no
    # store_id resolves the same way -- unreachable for a store-level caller,
    # readable by a cross-store one -- so there is no fail-open either.
    if _attachment_meta is not None:
        _blob_store = _attachment_meta.get("store_id")
        if not can_access_store_scoped(_blob_store, current_user):
            raise HTTPException(
                status_code=400,
                detail=_ATTACHMENT_INVALID_DETAIL,
            )

    # W1.4 / OS-006: the receiving store is now FINAL (PO re-point applied).
    # An ONLINE store owns no stock -- accepting goods there would mint real
    # stock_units on the pooled store, corrupting the no-own-stock model and
    # masking the oversell warning. Reject BEFORE the GRN doc is minted.
    if is_online_store(None, store_id):
        raise HTTPException(
            status_code=400,
            detail=(
                "Online stores hold no stock - receive these goods into a "
                "physical shop instead."
            ),
        )

    # F9: the vendor a DC is for -- from the PO when linked, else the body field.
    vendor_id = (po.get("vendor_id") if po else None) or grn.vendor_id

    # Now that the receiving store is final (re-pointed to the PO's delivery
    # store for a standard PO-backed GRN), mint the per-store GRN serial.
    grn_number = generate_grn_number(store_id)

    # F9: DC-specific guards (uniqueness + period lock). Both are best-effort on
    # a DB error (fail-soft) but a found duplicate is a hard 409.
    if is_dc:
        db = None
        try:
            db = _get_db()
        except Exception:
            db = None
        # Application-level DC-number uniqueness per (vendor_id, dc_number,
        # store_id) -- vendors reuse the same DC number across branches, so the
        # key is per store, not just per vendor. (The unique partial index is
        # added post-dedup per the prod-data-blockers convention.)
        if db is not None and grn.dc_number:
            try:
                dup = db.get_collection("grns").find_one(
                    {
                        "grn_subtype": GRN_SUBTYPE_DC,
                        "vendor_id": vendor_id,
                        "dc_number": grn.dc_number,
                        "store_id": store_id,
                    },
                    {"_id": 0, "grn_id": 1},
                )
                if dup:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Delivery Challan '{grn.dc_number}' is already "
                            f"logged for this vendor at this store. Duplicate "
                            f"DC numbers are not allowed."
                        ),
                    )
            except HTTPException:
                raise
            except Exception:
                pass  # fail-soft: skip dup check on DB error, proceed
        # Period lock on the DC date (goods movement into a closed month).
        if db is not None and grn.dc_date:
            try:
                from .finance import check_period_locked

                check_period_locked(db, grn.dc_date)
            except HTTPException:
                raise
            except Exception:
                pass

    # Ruling 14 -- THE TALLY. Every line of a PO-backed receipt must be ticked
    # before the receipt is written: the quantity that arrived has been counted
    # against the quantity that was ordered, line by line, by a person. Without
    # this the received quantity arrives pre-filled with the ordered quantity
    # and a receipt posts itself.
    if po is not None and not is_dc:
        untallied = [
            {"product_id": it.product_id, "received_qty": it.received_qty}
            for it in grn.items
            if not it.tallied
        ]
        if untallied:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "LINES_NOT_TALLIED",
                    "message": (
                        "Tick every line to confirm you have counted what "
                        "arrived against what was ordered, then post the "
                        "receipt."
                    ),
                    "lines": untallied,
                },
            )

    # Calculate totals
    total_received = sum(item.received_qty for item in grn.items)
    total_accepted = sum(item.accepted_qty for item in grn.items)
    total_rejected = sum(item.rejected_qty for item in grn.items)

    # Stamp the ordered quantity (from the PO, matched by product_id) onto each
    # received line so the GRN doc is self-describing for discrepancy detection
    # and downstream reporting. Fail-soft: a PO line we can't match leaves the
    # GRN line without an ordered_qty (no false discrepancy).
    ordered_by_product: dict = {}
    total_ordered = None
    if po:
        po_items = po.get("items") if isinstance(po.get("items"), list) else []
        for po_item in po_items:
            if not isinstance(po_item, dict):
                continue
            pid = po_item.get("product_id")
            if pid is None:
                continue
            try:
                ordered_by_product[pid] = ordered_by_product.get(pid, 0) + int(
                    po_item.get("quantity", 0) or 0
                )
            except (TypeError, ValueError):
                continue
        if ordered_by_product:
            total_ordered = sum(ordered_by_product.values())

    item_docs = []
    for item in grn.items:
        doc = item.model_dump()
        ordered = ordered_by_product.get(item.product_id)
        if ordered is not None:
            doc["ordered_qty"] = ordered
        # Stamp the per-line short/exact/over flag so the GRN doc is
        # self-describing and the receiving UI / discrepancy report don't have
        # to recompute it. UNMATCHED when the line isn't on the PO.
        doc["variance_status"] = classify_grn_line_variance(item.received_qty, ordered)
        item_docs.append(doc)

    grn_doc = {
        "grn_id": grn_id,
        "grn_number": grn_number,
        "po_id": grn.po_id,
        "po_number": po.get("po_number") if po else None,
        "vendor_id": vendor_id,
        "vendor_name": po.get("vendor_name") if po else None,
        "store_id": store_id,
        "vendor_invoice_no": grn.vendor_invoice_no,
        "vendor_invoice_date": grn.vendor_invoice_date,
        # F-S3: the receipt document the ops user attached (file_store id +
        # metadata). The accountant reconciliation console reads these to render
        # the "view document" link. None for a DC (attached later) -- and that
        # is now ENFORCED, not merely documented: this dict used to persist the
        # caller's id unconditionally, which is what made the DC subtype a
        # bypass of the authorisation gate above.
        "attachment_file_id": None if is_dc else grn.attachment_file_id,
        "attachment_filename": None if is_dc else grn.attachment_filename,
        "attachment_mime": None if is_dc else grn.attachment_mime,
        "items": item_docs,
        "total_received": total_received,
        "total_accepted": total_accepted,
        "total_rejected": total_rejected,
        "total_ordered": total_ordered,
        "notes": grn.notes,
        "status": "PENDING",
        # F9: subtype + DC fields. dc_matched/linked_bulk_invoice_id are flipped
        # when the DC is reconciled into a bulk invoice (see purchase_invoices).
        "grn_subtype": grn.grn_subtype,
        "dc_number": grn.dc_number if is_dc else None,
        "dc_date": grn.dc_date if is_dc else None,
        "dc_matched": False if is_dc else None,
        "linked_bulk_invoice_id": None,
        "created_by": current_user.get("user_id"),
        "created_at": datetime.now().isoformat(),
    }

    if grn_repo is not None:
        created = grn_repo.create(grn_doc)
        # F9 P3: the repository swallows insert errors (returns None). With the
        # partial UNIQUE (vendor_id, dc_number, store_id) index on DC rows
        # (schemas.py uniq_dc_vendor_number_store), a concurrent duplicate that
        # raced past the app-level check above surfaces as a DuplicateKeyError
        # inside create() -> None. Re-probe the dup key: if a rival row now
        # holds it, map to the SAME 409 as the app-level guard; any other save
        # failure on a DC is a loud 500 (never a false 201).
        if created is None and is_dc:
            dup = None
            try:
                dup = grn_repo.find_one(
                    {
                        "grn_subtype": GRN_SUBTYPE_DC,
                        "vendor_id": vendor_id,
                        "dc_number": grn.dc_number,
                        "store_id": store_id,
                    }
                )
            except Exception:  # noqa: BLE001
                dup = None
            if dup is not None and dup.get("grn_id") != grn_id:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Delivery Challan '{grn.dc_number}' is already "
                        f"logged for this vendor at this store. Duplicate "
                        f"DC numbers are not allowed."
                    ),
                )
            raise HTTPException(
                status_code=500, detail="Failed to save Delivery Challan"
            )

    # F9: audit the DC log (immutable; a DC is the accountable checkpoint between
    # physical lens arrival and workshop work). Fail-soft -- never blocks save.
    if is_dc:
        try:
            audit = get_audit_repository()
            if audit is not None:
                audit.create(
                    {
                        "action": "vendor.dc_log",
                        "entity_type": "grn",
                        "entity_id": grn_id,
                        "user_id": current_user.get("user_id"),
                        "detail": {
                            "grn_number": grn_number,
                            "dc_number": grn.dc_number,
                            "dc_date": grn.dc_date,
                            "vendor_id": vendor_id,
                            "store_id": store_id,
                            "total_received": total_received,
                            "total_accepted": total_accepted,
                        },
                    }
                )
        except Exception:
            pass

    # Anti-fraud / variance: a receiving discrepancy (rejected goods or a
    # short/over shipment vs the PO) raises an accountable SYSTEM task so it is
    # investigated rather than silently absorbed. A DC with no PO has nothing to
    # compare ordered-against, so the PO-discrepancy task only fires for a GRN
    # that has an ordered baseline. Fail-soft -- a task failure must never break
    # the GRN save.
    if total_ordered is not None and grn_has_discrepancy(grn_doc):
        try:
            from ..services.task_triggers import create_system_task
            from ..dependencies import get_task_repository

            po_label = grn_doc.get("po_number") or grn.po_id
            create_system_task(
                get_task_repository(),
                title=f"GRN discrepancy on PO {po_label}",
                description=(
                    f"Goods receipt {grn_number} against PO {po_label} shows a "
                    f"discrepancy: received {total_received}, accepted "
                    f"{total_accepted}, rejected {total_rejected}"
                    + (
                        f" vs ordered {total_ordered}"
                        if total_ordered is not None
                        else ""
                    )
                    + ". Reconcile receipt vs order and vendor invoice "
                    f"{grn.vendor_invoice_no}."
                ),
                priority="P2",
                category="Purchase",
                store_id=grn_doc.get("store_id"),
                dedupe_ref=f"grn:{grn_id}",
            )
        except Exception:
            pass

    return {
        "grn_id": grn_id,
        "grn_number": grn_number,
        "grn_subtype": grn.grn_subtype,
        "dc_number": grn.dc_number if is_dc else None,
        "total_received": total_received,
        "has_discrepancy": grn_has_discrepancy(grn_doc),
        "message": "Delivery Challan logged" if is_dc else "GRN created",
    }


@router.get("/grn/{grn_id}")
async def get_grn(grn_id: str, current_user: dict = Depends(get_current_user)):
    """Get GRN details"""
    grn_repo = get_grn_repository()

    if grn_repo is None:
        return {"grn_id": grn_id}

    grn = grn_repo.find_by_id(grn_id)
    if not grn:
        raise HTTPException(status_code=404, detail="GRN not found")

    _enrich_grn_names([grn])

    return grn


# ---------------------------------------------------------------------------
# F8 -- GRN acceptance is CLAIMED atomically before any stock is minted.
# ---------------------------------------------------------------------------
# Accepting a GRN used to be a classic check-then-act: read the doc, test
# status == PENDING, mint serialized stock_units, and only THEN flip the status.
# Two concurrent POSTs (an impatient double-click on "Accept", a retry, two
# terminals) both passed the status test and BOTH ran the minting loop -- the
# per-line `stock_repo.count` idempotency guard could not save it either,
# because both requests read `already = 0` before either had written. Result:
# real received inventory doubled.
#
# The fix is the same guarded single-document claim the rest of this codebase
# uses for "only one caller may proceed" (purchase_invoices._stamp_dcs_matched,
# marketing.credit_referral_reward, lens_stock reserve/commit): ONE
# find_one_and_update whose FILTER carries both the acceptable statuses and the
# lock state. Exactly one racing caller matches; the loser gets 409.
#
# The claim writes a LOCK FIELD and deliberately does NOT touch `status`: the
# status vocabulary is read by the receiving cockpit, the PO math, the DC/bulk
# invoice screens and the frontend, so a transient in-flight status would leak
# into all of them. (The accept_lock_* keys DO ride along in the raw GRN JSON;
# no reader interprets them, but they are not literally invisible.)
_GRN_ACCEPTABLE_STATUSES = ("PENDING", "PARTIALLY_ACCEPTED")

# Statuses that mean "this receipt HAS been accepted". Anything else the flip
# lands on (VOID, ESCALATED, missing) is a failure, not a benign no-op.
_GRN_TERMINAL_ACCEPT_STATUSES = ("ACCEPTED", "PARTIALLY_ACCEPTED")

# A crashed / KILLED worker (SIGKILL, OOM, Railway redeploy -- nothing that any
# except/finally can catch) must not freeze a receipt forever, so a lock older
# than this may be taken over. That wait is pure SHOP-FLOOR TIME: a carton on the
# counter that nobody can receive, with no admin unlock anywhere in the app.
#
# SIZING (round 4). Round 2 set this to 1800s to buy margin against a worker
# wedged inside one pymongo call (prod sets no socketTimeoutMS, so a blackholed
# socket parks a request indefinitely). That margin is no longer bought here:
#   * the heartbeat re-stamps every 25 units / 10s, so a LIVE accept is equally
#     protected at 300s as at 1800s;
#   * the heartbeat now FAILS CLOSED -- a worker that cannot prove it still holds
#     the claim stops minting after 3 consecutive errors or half this window,
#     i.e. it gives up BEFORE the window even permits a takeover;
#   * a takeover's only remaining overlap (one in-flight insert) is rejected by
#     the unique (source_id, grn_line_index, line_unit_seq) index.
# So the window's only job is recovering from a genuinely dead worker, and 300s
# is 30x the heartbeat cadence -- far too wide to steal a live claim, and a
# 5-minute worst case on the shop floor instead of 30.
_GRN_ACCEPT_LOCK_STALE_SECONDS = 300

# Heartbeat cadence for a long accept: re-stamp the lock at least this often, in
# units minted or in wall-clock seconds, whichever comes first. The seconds arm
# is the one that matters after a stall: the very next unit past a multi-minute
# block is overdue, so the abort check fires before another unit is minted.
_GRN_ACCEPT_HEARTBEAT_UNITS = 25
_GRN_ACCEPT_HEARTBEAT_SECONDS = 10

# How many heartbeat writes in a row may fail before the mint gives up. An
# UNVERIFIABLE claim must never be read as "still ours" -- see the tick below.
_GRN_ACCEPT_HEARTBEAT_MAX_ERRORS = 3

# The DB-level backstop this router leans on. Named so its presence can be
# verified at runtime and so a DuplicateKeyError can be attributed to it.
_GRN_UNIT_INDEX_NAME = "uniq_grn_line_unit_seq"

# Distinct outcome for "an atomic primitive EXISTS but the write raised". It is
# NOT the same as "this repo exposes no atomic primitive" (a minimal mock): a
# raising primitive means the state is UNKNOWN and Mongo is unhappy, so callers
# must fail CLOSED. Collapsing the two into None let a replica-set stepdown hand
# a claim token to BOTH racing double-click POSTs -- the exact double-mint F8
# exists to prevent, with no stale window involved.
_GRN_WRITE_ERROR = object()


def _grn_atomic_primitive(grn_repo):
    """The ONE guarded single-document primitive to use, or None when this repo
    exposes none at all.

    Chosen by ATTRIBUTE PRESENCE, never by catching an exception, and exactly
    one is used -- falling through from a raised find_one_and_update to
    update_one would re-test the same filter that our own partially-applied
    write may already have invalidated (and self-409 the only caller).

    The real GRNRepository wraps the pymongo `grns` collection (`.collection`);
    some minimal test/mock repos expose the primitive on themselves instead."""
    for target in (getattr(grn_repo, "collection", None), grn_repo):
        if target is None:
            continue
        for name in ("find_one_and_update", "update_one"):
            fn = getattr(target, name, None)
            if callable(fn):
                return name, fn
    return None


def _guarded_grn_write(grn_repo, flt: dict, patch: dict, pre_image: dict = None):
    """Run ONE guarded single-document write and report the outcome.

    Returns True (won), False (lost -- the filter matched nothing), None (this
    repo exposes NO atomic primitive: a minimal mock, callers may fail open) or
    ``_GRN_WRITE_ERROR`` (a primitive existed but the write raised: UNKNOWN,
    callers must fail closed).

    ``pre_image``, when given, receives the pre-update document under key "doc"
    (find_one_and_update only) so a caller can tell a fresh claim from a
    stale-lock takeover."""
    primitive = _grn_atomic_primitive(grn_repo)
    if primitive is None:
        return None
    name, fn = primitive
    try:
        res = fn(flt, patch)
    except Exception as exc:  # noqa: BLE001
        logger.error("[VENDOR] GRN guarded %s failed (failing CLOSED): %s", name, exc)
        return _GRN_WRITE_ERROR
    if name == "find_one_and_update":
        if pre_image is not None and isinstance(res, dict):
            pre_image["doc"] = res
        return res is not None
    return bool(getattr(res, "modified_count", 0) or getattr(res, "matched_count", 0))


def _claim_grn_for_accept(grn_repo, grn_id: str, user_id) -> Optional[str]:
    """Atomically claim a GRN for acceptance. Returns the claim token, or None
    when another caller holds the claim / the GRN is no longer acceptable.

    Raises 503 when the claim write itself errored: if Mongo cannot answer
    "did I get the lock?", minting stock next is the wrong move. Nothing has
    been minted and no lock was written, so a 503 strands nothing.

    Fail-open ONLY for a repo with no atomic primitive at all (minimal mock):
    receiving must never be blocked by an infrastructure gap -- same convention
    as is_online_store and the marketing.py claim fallback."""
    token = "GACC-" + uuid.uuid4().hex[:16]
    stale_cutoff = (
        datetime.now() - timedelta(seconds=_GRN_ACCEPT_LOCK_STALE_SECONDS)
    ).isoformat()
    flt = {
        "grn_id": grn_id,
        "status": {"$in": list(_GRN_ACCEPTABLE_STATUSES)},
        "$or": [
            {"accept_lock_at": {"$exists": False}},
            {"accept_lock_at": None},
            {"accept_lock_at": {"$lt": stale_cutoff}},
        ],
    }
    patch = {
        "$set": {
            "accept_lock_at": datetime.now().isoformat(),
            "accept_lock_token": token,
            "accept_lock_by": user_id or "",
        }
    }
    pre_image: dict = {}
    won = _guarded_grn_write(grn_repo, flt, patch, pre_image=pre_image)
    if won is _GRN_WRITE_ERROR:
        # The write may have APPLIED server-side and only then lost its reply --
        # in which case OUR token is now on the doc with nobody heartbeating it,
        # and the receipt would be frozen for the whole stale window on a claim
        # nobody is using. Clear it, guarded on our own token: that token is a
        # fresh uuid4, so this can only ever remove a lock THIS call wrote and
        # can never touch another holder's. Only now is it true that a 503
        # strands nothing.
        _release_grn_accept_claim(grn_repo, grn_id, token)
        raise HTTPException(
            status_code=503,
            detail=(
                "Could not reserve this goods receipt -- try again. Nothing was "
                "added to stock."
            ),
        )
    if won is None:
        return token
    if not won:
        return None
    prior = (pre_image.get("doc") or {}).get("accept_lock_at")
    if prior:
        # A takeover should be RARE (only a genuinely dead worker gets this far).
        # Log loudly: it is the one path where two workers could ever overlap.
        logger.error(
            "[VENDOR] GRN %s accept claim TAKEN OVER from a stale lock "
            "(locked at %s by %s) -- previous worker presumed dead",
            grn_id,
            prior,
            (pre_image.get("doc") or {}).get("accept_lock_by"),
        )
    return token


def _guarded_grn_write_retried(grn_repo, flt: dict, patch: dict, *, what, grn_id):
    """_guarded_grn_write, retried once on _GRN_WRITE_ERROR and LOUD if it still
    cannot be written.

    _guarded_grn_write swallows the driver exception into a sentinel, so a
    caller that ignores the return value fails completely silently -- which is
    how a single failed lock release could park a receipt behind the stale
    window with nobody able to see why."""
    res = None
    for attempt in range(2):
        if attempt:
            # A brief pause: back-to-back attempts against a broken socket both
            # fail in milliseconds and the "retry" buys nothing. This is short
            # enough not to matter to a request and long enough to clear a
            # primary stepdown that is already completing.
            import time

            time.sleep(0.25)
        res = _guarded_grn_write(grn_repo, flt, patch)
        if res is not _GRN_WRITE_ERROR:
            return res
    logger.error(
        "[VENDOR] GRN %s: %s could NOT be written (2 attempts). The receipt may "
        "stay locked until the %ss stale window expires.",
        grn_id,
        what,
        _GRN_ACCEPT_LOCK_STALE_SECONDS,
    )
    return res


def _release_grn_accept_claim(grn_repo, grn_id: str, token: Optional[str]) -> None:
    """Best-effort, TOKEN-GUARDED release so a failed accept does not park the
    receipt behind a lock. Guarded on our own token so we can never release a
    lock a stale-takeover handed to somebody else. Never raises."""
    if not token:
        return
    try:
        _guarded_grn_write_retried(
            grn_repo,
            {"grn_id": grn_id, "accept_lock_token": token},
            {
                "$set": {
                    "accept_lock_at": None,
                    "accept_lock_token": None,
                    "accept_lock_by": None,
                }
            },
            what="accept-claim release",
            grn_id=grn_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[VENDOR] GRN accept-claim release skipped for %s: %s", grn_id, exc
        )


def _advance_grn_terminal_status(grn_repo, grn_id: str, grn_status: str) -> bool:
    """Flip the receipt to its terminal status. Returns False when the write
    could not be confirmed (so the caller can be honest about it).

    NOT token-guarded on purpose: a worker whose claim was stolen has still put
    real units on the shelf, and leaving them behind a PENDING receipt is worse
    than a stale flip. But it IS guarded to only ever ADVANCE -- the filter
    accepts only the pre-terminal statuses -- so a stale worker cannot demote an
    already-ACCEPTED receipt back to PARTIALLY_ACCEPTED.

    Matching nothing is NOT automatically success. It only means the doc is no
    longer in a pre-terminal status, which is benign if somebody already
    advanced it -- and a silent catastrophe if the receipt went VOID underneath
    us, because the caller would then answer a green "GRN accepted, stock added"
    over a VOID doc while N real sellable units sit behind it. So on a no-match
    we RE-READ and only report success for a genuinely terminal status."""
    res = _guarded_grn_write(
        grn_repo,
        {"grn_id": grn_id, "status": {"$in": list(_GRN_ACCEPTABLE_STATUSES)}},
        {"$set": {"status": grn_status}},
    )
    if res is None:
        # Minimal mock with no atomic primitive: never lose the flip.
        return bool(grn_repo.update(grn_id, {"status": grn_status}))
    if res is _GRN_WRITE_ERROR:
        logger.error(
            "[VENDOR] GRN %s: terminal status flip to %s could NOT be written. "
            "Units are already minted; the receipt stays in its previous status "
            "and a re-accept is idempotent.",
            grn_id,
            grn_status,
        )
        return False
    if res is False:
        try:
            current = grn_repo.find_by_id(grn_id)
        except Exception:  # noqa: BLE001
            current = None
        status_now = (current or {}).get("status")
        if status_now in _GRN_TERMINAL_ACCEPT_STATUSES:
            return True  # somebody already advanced it; nothing is stranded
        logger.error(
            "[VENDOR] GRN %s: terminal status flip to %s did NOT apply -- the "
            "receipt is now %s. Units are already minted and are NOT attached "
            "to an accepted receipt; this needs manual reconciliation.",
            grn_id,
            grn_status,
            status_now,
        )
        return False
    return True


def _finalise_grn_accept_metadata(
    grn_repo, grn_id: str, token: Optional[str], fields: dict
) -> None:
    """Record who accepted / how much AND release the lock, in ONE token-guarded
    write. A worker whose claim was taken over matches nothing and leaves the
    real holder's numbers -- and the real holder's lock -- untouched."""
    patch = dict(fields)
    patch.update(
        {
            "accept_lock_at": None,
            "accept_lock_token": None,
            "accept_lock_by": None,
        }
    )
    if not token:
        grn_repo.update(grn_id, patch)
        return
    res = _guarded_grn_write_retried(
        grn_repo,
        {"grn_id": grn_id, "accept_lock_token": token},
        {"$set": patch},
        what="accept metadata + lock release",
        grn_id=grn_id,
    )
    if res is None:
        # No atomic primitive (minimal mock): plain write, nothing to guard.
        grn_repo.update(grn_id, patch)
        return
    if res is False:
        logger.error(
            "[VENDOR] GRN %s: this worker's accept claim had been taken over -- "
            "leaving the holder's accepted_by / units_added and its live lock "
            "intact",
            grn_id,
        )


def _grn_accept_heartbeat_tick(grn_repo, grn_id: str, token, state: dict) -> None:
    """Called once per unit ABOUT to be minted: keep this claim fresh, and ABORT
    the moment it is no longer ours.

    Two jobs, both needed to make the stale-lock valve safe:
      * re-stamp `accept_lock_at` so a long-but-live accept is never declared
        stale and stolen while it is still minting;
      * detect (token-guarded, so only OUR claim matches) that a takeover HAS
        happened and stop this worker immediately -- otherwise the wedged worker
        would resume against a `to_mint` it computed before the takeover and
        mint the same units the takeover holder is already minting.

    Raises 409 on a lost claim. Whatever was already minted stays (it is real
    stock and the per-line count guard makes the takeover's own arithmetic see
    it); the takeover holder owns the receipt from here."""
    if not token:
        return
    state["units"] += 1
    now = datetime.now()
    due = (
        state["units"] >= _GRN_ACCEPT_HEARTBEAT_UNITS
        or (now - state["at"]).total_seconds() >= _GRN_ACCEPT_HEARTBEAT_SECONDS
    )
    if not due:
        return
    # NOTE the cadence counters are NOT reset here. They are reset only when the
    # write comes back with a DEFINITE answer, so an errored heartbeat re-arms
    # the fence on the very next unit instead of buying the worker another 25
    # units / 10 seconds of unfenced minting.
    res = _guarded_grn_write(
        grn_repo,
        {"grn_id": grn_id, "accept_lock_token": token},
        {"$set": {"accept_lock_at": now.isoformat()}},
    )
    if res is None:
        # No atomic primitive at all (minimal mock): there is no lock to keep
        # fresh and no takeover to detect. Never block receiving.
        state.update({"units": 0, "at": now, "errors": 0, "confirmed_at": now})
        return
    if res is _GRN_WRITE_ERROR:
        # UNKNOWN -- and unknown must NEVER read as "still ours". That is the
        # exact fail-open collapse the CLAIM path was fixed for, and here it is
        # CORRELATED, not independent: the stepdown that wedges a socket for
        # minutes is the same event that makes this write raise. Reading it as
        # "keep minting" re-opened the full double mint (a panel reproduced 34
        # units for a 20-unit receipt, HTTP 200, receipt looking clean).
        state["errors"] += 1
        unconfirmed_for = (now - state["confirmed_at"]).total_seconds()
        if (
            state["errors"] >= _GRN_ACCEPT_HEARTBEAT_MAX_ERRORS
            or unconfirmed_for >= _GRN_ACCEPT_LOCK_STALE_SECONDS / 2
        ):
            logger.error(
                "[VENDOR] GRN %s: accept claim UNVERIFIABLE for %.0fs / %s "
                "consecutive heartbeat errors -- stopping the mint rather than "
                "minting against a claim we can no longer prove we hold",
                grn_id,
                unconfirmed_for,
                state["errors"],
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "Could not confirm this goods receipt is still reserved, so "
                    "nothing further was added. Open the receiving screen and "
                    "accept it again -- the units already received are safe and "
                    "will not be counted twice."
                ),
            )
        logger.warning(
            "[VENDOR] GRN %s accept heartbeat could not be written (%s in a "
            "row, %.0fs since the claim was last confirmed)",
            grn_id,
            state["errors"],
            unconfirmed_for,
        )
        return
    # A definite answer: the cadence and the error run both reset.
    state.update({"units": 0, "at": now, "errors": 0})
    if not res:
        logger.error(
            "[VENDOR] GRN %s accept claim was TAKEN OVER mid-mint -- aborting "
            "this worker's remaining units to avoid a double mint",
            grn_id,
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "Accepting this goods receipt took too long and was taken over "
                "by another attempt. Refresh -- the units already received are "
                "safe and will not be counted twice."
            ),
        )
    state["confirmed_at"] = now


def _grn_already_minted(stock_repo, flt: dict) -> int:
    """How many units this GRN LINE has already put into stock_units.

    Counts through the RAW collection when one is available, because
    BaseRepository.count swallows driver errors and returns 0
    (base_repository.py:230-243) -- and a silent 0 here is a licence to mint an
    entire receipt a second time. A failure must reach the caller, which aborts.
    Falls back to repo.count only for minimal mocks that expose no collection.

    This count is ALSO the origin of each unit's `line_unit_seq` ordinal, so it
    is what makes the unique (source_id, grn_line_index, line_unit_seq) index
    line up across retries instead of colliding."""
    coll = getattr(stock_repo, "collection", None)
    counter = getattr(coll, "count_documents", None)
    if callable(counter):
        return int(counter(flt) or 0)
    repo_count = getattr(stock_repo, "count", None)
    if callable(repo_count):
        return int(repo_count(flt) or 0)
    raise RuntimeError("stock repository exposes no way to count minted units")


def _stock_create_raises_on_duplicate(stock_repo) -> bool:
    """Does this stock repository's create() accept raise_on_duplicate?

    BaseRepository does (it re-raises DuplicateKeyError instead of swallowing
    it into a silent None); minimal test/mock repos take only the document.
    Probed once per accept so a duplicate rejection is reported EXPLICITLY
    rather than being indistinguishable from a generic insert failure."""
    try:
        import inspect

        return "raise_on_duplicate" in inspect.signature(stock_repo.create).parameters
    except Exception:  # noqa: BLE001
        return False


# One-shot, process-wide cache for the backstop-index presence check.
# Exposed so tests can reset it.
_GRN_UNIT_INDEX_STATE: dict = {"checked": False, "present": None}


def _grn_unit_index_present(stock_repo):
    """Is the DB-level backstop actually installed? Checked ONCE per process.

    ensure_indexes builds every index fail-soft, so uniq_grn_line_unit_seq can
    be ABSENT in prod with nothing whatsoever saying so -- a safety net whose
    disappearance is invisible. It is a BACKSTOP, not the primary defence (the
    atomic claim, the fail-closed heartbeat and the fail-closed per-line count
    all sit above it), so a missing index must NOT block a real delivery from
    being received -- but it must be LOUD. Returns True / False / None (could
    not determine)."""
    if _GRN_UNIT_INDEX_STATE["checked"]:
        return _GRN_UNIT_INDEX_STATE["present"]
    present = None
    try:
        coll = getattr(stock_repo, "collection", None)
        info_fn = getattr(coll, "index_information", None)
        if callable(info_fn):
            present = _GRN_UNIT_INDEX_NAME in (info_fn() or {})
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[VENDOR] could not verify the %s index: %s", _GRN_UNIT_INDEX_NAME, exc
        )
        present = None
    _GRN_UNIT_INDEX_STATE.update({"checked": True, "present": present})
    if present is False:
        logger.error(
            "[VENDOR] STOCK BACKSTOP MISSING: the unique index %s is NOT "
            "installed on stock_units. GRN receiving continues (the accept "
            "claim and the fail-closed heartbeat still hold), but the "
            "database-level duplicate-unit guard is absent -- investigate the "
            "index build now.",
            _GRN_UNIT_INDEX_NAME,
        )
    return present


def _is_grn_unit_duplicate(exc) -> bool:
    """Did OUR unique index fire, or some other unique index on stock_units?

    Treating ANY DuplicateKeyError as "already minted, skip" would silently DROP
    a real received unit the day another unique index lands on this collection
    (there is already one on `serial`). Attribute the error before skipping."""
    details = getattr(exc, "details", None) or {}
    keypat = details.get("keyPattern")
    if isinstance(keypat, dict) and set(keypat) == {
        "source_id",
        "grn_line_index",
        "line_unit_seq",
    }:
        return True
    for blob in (details.get("errmsg"), keypat, str(exc)):
        if blob and _GRN_UNIT_INDEX_NAME in str(blob):
            return True
    return False


# "This ordinal already exists" -- the index did its job. DISTINCT from a falsy
# return, which now means the insert genuinely did not land.
_GRN_MINT_DUPLICATE = object()


def _grn_mint_unit(stock_repo, doc: dict, raises_on_duplicate: bool):
    """Insert ONE serialized unit for a GRN line.

    Returns the created row, ``_GRN_MINT_DUPLICATE`` when the unique
    (source_id, grn_line_index, line_unit_seq) index rejected it, or a FALSY
    value when the insert genuinely failed.

    That three-way answer is load-bearing. A duplicate is the EXPECTED outcome
    of the last remaining race (an insert already in flight inside a worker that
    got declared stale and taken over) -- skipping it is correct. A real failure
    is the opposite: `seq` must NOT advance past it, because the loop would
    leave a HOLE in the line's ordinals that the unique index then makes
    PERMANENTLY unfillable -- the re-accept computes `already` from the row
    COUNT, lands on an ordinal that already exists, is rejected as a duplicate,
    and the missing physical unit can never be received. Collapsing the two into
    one falsy return is exactly that bug.

    A DuplicateKeyError from ANY OTHER index is re-raised: skipping it would
    silently lose a real received unit. NOTE the real BaseRepository.create
    swallows every non-duplicate exception and returns None (it only re-raises
    DuplicateKeyError under raise_on_duplicate), so in production a generic
    insert failure arrives here as a falsy return, not as an exception."""
    try:
        if raises_on_duplicate:
            return stock_repo.create(doc, raise_on_duplicate=True)
        return stock_repo.create(doc)
    except Exception as exc:  # noqa: BLE001
        if exc.__class__.__name__ != "DuplicateKeyError":
            raise
        if not _is_grn_unit_duplicate(exc):
            logger.error(
                "[VENDOR] GRN %s line %s unit #%s hit a DuplicateKeyError from "
                "a DIFFERENT index -- not skipping (that would lose a real "
                "received unit): %s",
                doc.get("source_id"),
                doc.get("grn_line_index"),
                doc.get("line_unit_seq"),
                exc,
            )
            raise
        logger.warning(
            "[VENDOR] GRN %s line %s unit #%s was already minted by another "
            "attempt -- the unique key rejected the duplicate, skipping",
            doc.get("source_id"),
            doc.get("grn_line_index"),
            doc.get("line_unit_seq"),
        )
        return _GRN_MINT_DUPLICATE


def _grn_accept_lock_minutes_left(locked_at) -> Optional[int]:
    """Whole minutes until a held lock goes stale, or None if it cannot be
    computed. Used to make the 409 HONEST: after a hard process kill (SIGKILL /
    OOM / redeploy -- nothing any except/finally can catch) the wait really is
    the stale window, so "wait a moment" would be a lie."""
    if not locked_at:
        return None
    try:
        held_for = (
            datetime.now() - datetime.fromisoformat(str(locked_at))
        ).total_seconds()
    except (TypeError, ValueError):
        return None
    remaining = _GRN_ACCEPT_LOCK_STALE_SECONDS - held_for
    if remaining <= 0:
        return None
    return max(1, int(remaining // 60) + (1 if remaining % 60 else 0))


def _grn_accept_conflict(grn_repo, grn_id: str) -> HTTPException:
    """The 409 the LOSER of the accept claim gets. Re-reads the GRN so the
    message tells the operator which of the two races they lost."""
    current = None
    try:
        current = grn_repo.find_by_id(grn_id)
    except Exception:  # noqa: BLE001
        current = None
    status = (current or {}).get("status")
    if status and status not in _GRN_ACCEPTABLE_STATUSES:
        detail = (
            "This goods receipt was already accepted -- its stock is on the "
            "shelf. Refresh to see the current status."
        )
    else:
        detail = (
            "This goods receipt is already being accepted right now. Do not "
            "submit it twice."
        )
        mins = _grn_accept_lock_minutes_left((current or {}).get("accept_lock_at"))
        if mins:
            detail += (
                f" If the first attempt has died, this clears automatically in "
                f"about {mins} minute(s)."
            )
    return HTTPException(status_code=409, detail=detail)


@router.post("/grn/{grn_id}/accept")
async def accept_grn(
    grn_id: str, current_user: dict = Depends(require_roles(*_VENDOR_ROLES))
):
    """Post a goods-receipt note: mint serialized stock for the accepted units,
    advance the PO to PARTIALLY_RECEIVED / RECEIVED, and write an audit trail.

    Stock is written one row per physical unit into the canonical serialized
    `stock_units` collection via get_stock_repository -- the SAME path the
    inventory /stock/add screen uses (barcode + location + AVAILABLE status), so
    a GRN-received unit is a first-class sellable unit. There is NO parallel
    stock write.

    Double-click safe FOR THIS ENDPOINT (F8): acceptance is CLAIMED with one
    guarded single-document update (status-keyed + lock field) before any stock
    is minted, so of two concurrent POSTs on the SAME grn_id exactly one mints
    and the other gets a 409. (POST /grn/express is NOT covered: each click
    creates a NEW grn_id, so the per-GRN claim never engages -- that needs a
    create-time duplicate guard, tracked separately.) Sequential re-POSTs are
    additionally idempotent -- the minting loop skips any (grn_id, product_id,
    grn_line_index) that already has units in stock_units, so a partially-failed
    accept can be safely retried without double-counting.

    Fail-soft ordering: the stock write happens first; the GRN status flip, the
    PO state update, and the per-unit stock_audit rows all follow and are each
    wrapped so that a logging/secondary failure can never lose the stock that
    was already received.
    """
    return await _accept_grn_impl(grn_id, current_user)


async def _accept_grn_impl(grn_id: str, current_user: dict) -> dict:
    """Shared GRN-accept engine behind POST /grn/{grn_id}/accept (and
    POST /grn/express).

    Behavior-preserving extraction of the original accept_grn body: the
    store-scope guard, the PENDING/PARTIALLY_ACCEPTED status gate, idempotent
    per-(grn, line) stock minting, PO receipt math and the audit trail all run
    here unchanged for both callers. Callers pass the authenticated
    ``current_user`` their own ``require_roles(*_VENDOR_ROLES)`` gate produced.
    """
    grn_repo = get_grn_repository()
    stock_repo = get_stock_repository()
    po_repo = get_purchase_order_repository()

    if grn_repo is None:
        return {"message": "GRN accepted, stock added"}

    grn = grn_repo.find_by_id(grn_id)
    if not grn:
        raise HTTPException(status_code=404, detail="GRN not found")

    # Store-scope (SEC #2 object-level pattern, same as download_grn_doc): a
    # cross-store role (SUPERADMIN/ADMIN) may accept any GRN; a store-level caller
    # may only accept GRNs stamped with one of their stores. A mismatch reads as
    # 404 (not 403) so a GRN's existence in another store isn't disclosed. This
    # gates the stock mint + PO advance + audit writes below.
    if not can_access_store_scoped(grn.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="GRN not found")

    # PENDING is the normal first accept. PARTIALLY_ACCEPTED is re-accept after a
    # "Catalog now" -- some lines were held last time because their product was
    # not yet catalogued; the per-(grn,product) idempotency guard below skips the
    # already-minted lines and mints only the newly-resolved ones.
    if grn.get("status") not in ("PENDING", "PARTIALLY_ACCEPTED"):
        raise HTTPException(status_code=400, detail="GRN is not pending")

    # W1.4 / OS-006 (belt and braces): accepting mints real stock_units at the
    # GRN's store. A legacy/imported GRN stamped with an ONLINE store must not
    # create owned stock on a pooled, stockless store.
    if is_online_store(None, grn.get("store_id")):
        raise HTTPException(
            status_code=400,
            detail=(
                "This goods receipt is addressed to an online store, which "
                "holds no stock. Re-create it against a physical shop."
            ),
        )

    # F8 -- CLAIM the receipt before a single unit is minted. The status test
    # above is only a friendly error message; THIS guarded single-document
    # update is the authority. Exactly one of two racing double-click POSTs
    # matches the filter, so exactly one runs the minting loop; the loser 409s
    # without touching stock, the PO or the audit trail.
    # The claim time is captured HERE, not inside the mint body. Everything
    # between this line and the first unit -- the PO fetch, the product lookup,
    # the cost backfill write -- is a round trip that can park on a blackholed
    # socket for minutes (prod sets no socketTimeoutMS). Seeding the heartbeat
    # clock after those reads let a woken worker start with a FRESH clock, so
    # the first tick was not due and it minted the whole delivery without ever
    # re-proving ownership. Measured on the previous build: 24 real sellable
    # units behind a receipt that had been VOIDed in the meantime.
    claimed_at = datetime.now()
    claim_token = _claim_grn_for_accept(grn_repo, grn_id, current_user.get("user_id"))
    if claim_token is None:
        raise _grn_accept_conflict(grn_repo, grn_id)

    try:
        return _accept_grn_claimed(
            grn_id,
            grn,
            current_user,
            grn_repo,
            stock_repo,
            po_repo,
            claim_token,
            claimed_at,
        )
    except Exception:
        # Nothing was committed we can attribute to this call, or the accept
        # died half-way: hand the claim back (token-guarded) so the operator can
        # retry immediately instead of waiting out the stale window. The
        # per-(grn, line) mint guard makes that retry idempotent.
        _release_grn_accept_claim(grn_repo, grn_id, claim_token)
        raise


def _accept_grn_claimed(
    grn_id: str,
    grn: dict,
    current_user: dict,
    grn_repo,
    stock_repo,
    po_repo,
    claim_token: Optional[str] = None,
    claimed_at=None,
) -> dict:
    """The accept body, run ONLY by the caller that won the F8 claim.

    Unchanged from the original inline body: idempotent per-(grn, line) stock
    minting, the Hub-Phase-2 ghost-stock hold, cost backfill, the GRN status
    flip and the PO receipt math. Extracted so the claim can be released on any
    failure without re-indenting the whole engine."""
    store_id = grn.get("store_id")
    po_id = grn.get("po_id")
    grn_number = grn.get("grn_number")
    user_id = current_user.get("user_id")

    # Phase 2 (inventory valuation): build a per-product unit_price map from the
    # PO so each minted serialized unit is stamped with its PROVISIONAL cost
    # (the agreed PO price). This is the receipt-time cost; the purchase invoice
    # later trues it up to the actually-billed price. ADDITIVE + fail-soft: any
    # problem here leaves po_unit_price empty and the units mint exactly as
    # before (no unit_cost), never blocking receiving.
    po_unit_price: dict = {}
    po_for_cost = None
    if po_repo is not None and po_id:
        try:
            po_for_cost = po_repo.find_by_id(po_id)
            for it in (po_for_cost or {}).get("items", []) or []:
                if not isinstance(it, dict):
                    continue
                pid = it.get("product_id")
                if pid is None or pid in po_unit_price:
                    continue
                try:
                    po_unit_price[pid] = round(float(it.get("unit_price") or 0), 2)
                except (TypeError, ValueError):
                    continue
        except Exception:  # noqa: BLE001
            po_unit_price = {}

    minted_stock_ids: List[str] = []
    units_added = 0
    # Hub Phase 2: lines whose product is not yet on the spine are HELD (not
    # minted as ghost stock) -> the GRN stays PARTIALLY_ACCEPTED and the FE shows
    # a "Catalog now" affordance; re-accepting after cataloguing mints them.
    unresolved_lines: List[dict] = []
    product_repo = get_product_repository()
    # F8 round 2: heartbeat state for the whole mint (across every line), so a
    # long accept keeps its claim fresh and stops dead if the claim is stolen.
    # `confirmed_at` starts at the claim (the claim IS proof of ownership) and
    # only advances on a heartbeat that came back with a definite answer.
    #
    # The clock comes from the CALLER, stamped at claim time -- NOT from here.
    # Stamping it here (after the PO fetch above) erased however long that read
    # had parked, so a worker wedged inside it woke with a fresh clock, found
    # the first tick not due (units < 25, elapsed < 10s) and minted with no
    # token check at all. With the claim time, any gap longer than
    # _GRN_ACCEPT_HEARTBEAT_SECONDS makes the FIRST tick due, so a woken worker
    # re-proves ownership before minting a single unit. Zero cost on the happy
    # path: the first unit is milliseconds from the claim, so no extra write.
    _now = claimed_at or datetime.now()
    heartbeat_state = {"units": 0, "at": _now, "errors": 0, "confirmed_at": _now}
    # Probed ONCE per accept, not per unit.
    mint_raises_on_duplicate = _stock_create_raises_on_duplicate(stock_repo)
    # Verify the DB-level backstop exists (cached process-wide, never blocking).
    if stock_repo is not None:
        _grn_unit_index_present(stock_repo)

    if stock_repo is not None:
        for line_index, item in enumerate(grn.get("items", []) or []):
            try:
                accepted_qty = int(item.get("accepted_qty", 0) or 0)
            except (TypeError, ValueError):
                accepted_qty = 0
            product_id = item.get("product_id")
            if accepted_qty <= 0 or not product_id:
                continue

            # Hub Phase 2 ghost-stock gate: only mint against a product that
            # exists on the `products` spine. An uncatalogued line is HELD (no
            # ghost stock) for "Catalog now". Fail-soft: when no product repo is
            # available we cannot verify, so we mint exactly as before.
            prod = product_repo.find_by_id(product_id) if product_repo else None
            if product_repo is not None and prod is None:
                unresolved_lines.append(
                    {
                        "product_id": product_id,
                        "accepted_qty": accepted_qty,
                        "reason": "not_catalogued",
                    }
                )
                continue

            # Idempotency guard keyed on the GRN LINE (grn_line_index), not just
            # the product: a GRN may legitimately carry two lines for the SAME
            # product (e.g. different location_code). A product-only key let line
            # B see line A's units and skip minting -> silent first-accept stock
            # loss. Keying on the line index makes each line mint its own qty and
            # still makes a re-accept idempotent.
            #
            # This read FAILS CLOSED. It used to swallow errors into
            # `already = 0`, which disarmed the two re-run paths this claim
            # machinery depends on (the token-guarded release retry and the
            # stale takeover): one transient error on a retry and an entire
            # 200-unit receipt mints a second time. The unique index on
            # (source_id, grn_line_index, line_unit_seq) is the DB-level
            # backstop, but it only lines up if the ordinals continue this
            # count -- so if we cannot verify what is already received, we STOP.
            try:
                already = _grn_already_minted(
                    stock_repo,
                    {
                        "source_type": "GRN",
                        "source_id": grn_id,
                        "product_id": product_id,
                        "grn_line_index": line_index,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[VENDOR] GRN %s line %s: could not count already-minted "
                    "units (%s) -- aborting rather than risking a double mint",
                    grn_id,
                    line_index,
                    exc,
                )
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Could not verify what has already been received for "
                        "this goods receipt, so nothing further was added. Try "
                        "again in a moment."
                    ),
                ) from exc
            if already >= accepted_qty:
                continue
            to_mint = accepted_qty - already

            location_code = item.get("location_code") or "DEFAULT"
            # Provisional receipt cost from the PO line (Phase 2 valuation).
            # Prefer the GRN line's own unit_price if it carries one, else the PO
            # price. ADDITIVE: only stamped when we have a positive cost, so a
            # priceless receipt mints exactly as before.
            try:
                line_cost = float(item.get("unit_price") or 0) or po_unit_price.get(
                    product_id, 0.0
                )
            except (TypeError, ValueError):
                line_cost = po_unit_price.get(product_id, 0.0)
            cost_fields = {}
            if line_cost and line_cost > 0:
                cost_fields = {
                    "unit_cost": round(line_cost, 2),
                    "cost_price": round(line_cost, 2),
                    "cost_source": "GRN_PO",
                }
                # Hub Phase 2 hero: receiving the goods is where the cost becomes
                # known. Backfill it onto the PRODUCT spine when the product had
                # no cost, then atomically restamp -- a DRAFT whose only gap was
                # cost_price auto-promotes to ACTIVE (purchasable) right here.
                # Never-demote + fail-soft: a promote failure never blocks minting.
                if (
                    product_repo is not None
                    and prod is not None
                    and not prod.get("cost_price")
                ):
                    try:
                        product_repo.update(
                            product_id,
                            {
                                "cost_price": round(line_cost, 2),
                                "cost_source": "GRN_PO",
                            },
                        )
                        _pm.apply_restamp_atomic(
                            product_id,
                            prod,
                            {"cost_price": round(line_cost, 2)},
                            product_repo=product_repo,
                        )
                    except Exception as _cp_exc:  # noqa: BLE001
                        logger.warning(
                            "[VENDOR] GRN cost-promote skipped for %s: %s",
                            product_id,
                            _cp_exc,
                        )

            # Hub Phase 2: only mint sellable AVAILABLE stock for a CATALOG-
            # COMPLETE product. After the cost backfill above, a product still
            # missing catalogue fields beyond cost remains a non-purchasable
            # DRAFT -- HOLD its line (like an uncatalogued line) instead of
            # minting sellable stock POS could not lawfully price/sell. Fail-soft:
            # only when a product repo is available to verify completeness.
            if product_repo is not None and prod is not None:
                merged_for_status = dict(prod)
                if cost_fields:
                    merged_for_status["cost_price"] = cost_fields["cost_price"]
                if _pm.compute_catalog_status(merged_for_status)[1]:
                    unresolved_lines.append(
                        {
                            "product_id": product_id,
                            "accepted_qty": accepted_qty,
                            "reason": "incomplete_catalog",
                        }
                    )
                    continue

            # P2 (optical batch/expiry): stamp the supplier batch + expiry on
            # each minted unit so contact lenses are dated for FEFO consumption
            # and near-expiry reporting (the stock model + FEFO helpers key on
            # batch_code/expiry_date -- the SAME fields /stock/add persists, so a
            # GRN-received CL unit is indistinguishable from a manually-added
            # one). ADDITIVE + fail-soft: a line with no batch/expiry (frames,
            # undated spectacle lenses) mints exactly as before.
            batch_fields = {}
            _bcode = item.get("batch_code") or item.get("lot_number")
            if _bcode:
                batch_fields["batch_code"] = _bcode
            if item.get("expiry_date"):
                batch_fields["expiry_date"] = item.get("expiry_date")

            # line_unit_seq is the per-line ORDINAL of each physical unit, and it
            # is what the unique index (source_id, grn_line_index, line_unit_seq)
            # constrains. It MUST be derived from `already` -- the count of units
            # this line has already put in stock_units -- and never from a fresh
            # 0 or a random value:
            #   * a retry / stale-takeover mints "the remainder", so its ordinals
            #     have to CONTINUE the line's existing sequence. Restarting at 0
            #     would collide with the rows already on the shelf and the index
            #     would reject every unit -- receiving 1 unit instead of N;
            #   * a random value would be unique per attempt, so two workers
            #     minting the same physical unit would both succeed and the index
            #     would constrain nothing.
            # Deriving it from `already` makes the ordinals of a line exactly
            # 0..accepted_qty-1 no matter how many attempts it took: the winner
            # covers 0..N-1, a takeover that read `already = k` covers k..N-1, the
            # overlap is rejected by the index, and the union is exactly N units.
            for seq in range(already, already + to_mint):
                # Keep the claim alive while we work, and STOP immediately if it
                # was taken over (raises 409). `to_mint` was computed before this
                # loop started, so without this check a worker that was wedged
                # long enough to be declared stale would resume and mint the same
                # units the takeover holder is already minting.
                _grn_accept_heartbeat_tick(
                    grn_repo, grn_id, claim_token, heartbeat_state
                )
                created = _grn_mint_unit(
                    stock_repo,
                    {
                        "store_id": store_id,
                        "product_id": product_id,
                        "barcode": _grn_barcode(store_id, product_id),
                        "location_code": location_code,
                        "quantity": 1,
                        "status": "AVAILABLE",
                        "is_reserved": False,
                        "barcode_printed": False,
                        "source_type": "GRN",
                        "source_id": grn_id,
                        "grn_line_index": line_index,
                        "line_unit_seq": seq,
                        "grn_number": grn_number,
                        "po_id": po_id,
                        "created_by": user_id,
                        **cost_fields,
                        **batch_fields,
                    },
                    mint_raises_on_duplicate,
                )
                if created is _GRN_MINT_DUPLICATE:
                    # Another attempt already owns this ordinal. Advancing seq
                    # is correct here -- the unit exists, it is simply not ours.
                    continue
                if not created:
                    # A REAL insert failure (BaseRepository.create swallows every
                    # non-duplicate exception into a falsy return, so this is the
                    # only signal there is). STOP: carrying on would mint the
                    # remaining ordinals around this one and leave a hole the
                    # unique index makes permanently unfillable. Aborting here
                    # leaves the line's ordinals a contiguous prefix, so the
                    # re-accept recomputes `already` and mints exactly the
                    # missing units -- it heals.
                    logger.error(
                        "[VENDOR] GRN %s line %s: unit #%s did not insert -- "
                        "stopping so the ordinal sequence stays contiguous and "
                        "a retry can finish the receipt",
                        grn_id,
                        line_index,
                        seq,
                    )
                    raise HTTPException(
                        status_code=503,
                        detail=(
                            # Deliberately NO count. `units_added` is THIS
                            # attempt's tally and the client silently retries a
                            # 5xx three times, so after a retry that failed on
                            # its first unit the number reads "0 unit(s)" with
                            # real stock already on the shelf -- which is exactly
                            # the recount-and-hand-add this sentence exists to
                            # prevent.
                            "Some units were received before the stock store "
                            "stopped responding. Open the receiving screen and "
                            "accept it again -- the units already received are "
                            "safe and will not be counted twice."
                        ),
                    )
                if created:
                    units_added += 1
                    stock_id = created.get("stock_id") or created.get("_id")
                    if stock_id:
                        minted_stock_ids.append(str(stock_id))
                        # Fail-soft audit row per unit -- never blocks receiving.
                        _grn_stock_audit(
                            str(stock_id),
                            "AVAILABLE",
                            grn_id,
                            po_id,
                            store_id,
                            user_id,
                        )
                        # E3w: ledger the GRN mint (None -> AVAILABLE) into
                        # item_events. Additive + fail-soft: this runs AFTER the
                        # unit is already in stock_units, performs no CAS / no
                        # projection, and any error is logged + swallowed so it
                        # can never lose the received stock.
                        try:
                            from ..services import item_events as ie

                            _le_db = _get_db()
                            if _le_db is not None:
                                ie.record_post_write_event(
                                    _le_db,
                                    event_type=ie.ItemEventType.MINT,
                                    actor_id=user_id or "",
                                    stock_id=str(stock_id),
                                    from_state=None,
                                    to_state=ie.StockState.AVAILABLE,
                                    store_id=store_id,
                                    product_id=product_id,
                                    source_type="GRN",
                                    source_id=grn_id,
                                    payload={"grn_number": grn_number, "po_id": po_id},
                                )
                        except Exception as _le_exc:  # noqa: BLE001
                            logger.warning(
                                "[VENDOR] GRN mint ledger emit skipped: %s",
                                _le_exc,
                            )

    # Mark the GRN accepted -- or PARTIALLY_ACCEPTED when one or more lines were
    # HELD because their product is not yet catalogued (Hub Phase 2). A held GRN
    # is re-acceptable after "Catalog now" to mint the now-resolved lines.
    grn_status = "PARTIALLY_ACCEPTED" if unresolved_lines else "ACCEPTED"
    # (a) STATUS -- not token-guarded (a worker whose claim was stolen has still
    #     minted real units, and stranding them behind a PENDING receipt is the
    #     worse outcome), but ADVANCE-ONLY: the filter accepts only the two
    #     pre-terminal statuses, so a stale worker can never demote an ACCEPTED
    #     receipt back to PARTIALLY_ACCEPTED -- which is a CLAIMABLE status and
    #     would invite a third accept.
    status_ok = _advance_grn_terminal_status(grn_repo, grn_id, grn_status)
    # (b) WHO accepted and HOW MUCH, plus the lock release, in ONE TOKEN-GUARDED
    #     write. units_added / accepted_by are what the clerk and the manager
    #     read back to confirm a delivery, so a worker whose claim was STOLEN
    #     must not overwrite the real holder's numbers. Unlike (a), skipping this
    #     strands nothing -- the real holder wrote its own.
    _finalise_grn_accept_metadata(
        grn_repo,
        grn_id,
        claim_token,
        {
            "accepted_at": datetime.now().isoformat(),
            "accepted_by": user_id,
            "units_added": units_added,
            "unresolved_lines": unresolved_lines,
        },
    )
    if not status_ok:
        # Be honest rather than showing a green "accepted": the units ARE on the
        # shelf but the receipt did not advance, so the operator must re-accept
        # (idempotent -- the per-line count guard sees the units already minted).
        return {
            "message": (
                f"{units_added} unit(s) were received, but the receipt status "
                "could not be updated. Open the receiving screen and accept it "
                "again -- the units already received will not be counted twice."
            ),
            "grn_id": grn_id,
            "grn_status": grn.get("status"),
            "status_flip_failed": True,
            "units_added": units_added,
            "stock_ids": minted_stock_ids,
            "po_status": None,
            "unresolved_lines": unresolved_lines,
            "needs_cataloguing": bool(unresolved_lines),
            "items_added": len(
                [
                    i
                    for i in (grn.get("items", []) or [])
                    if (i.get("accepted_qty", 0) or 0) > 0
                ]
            ),
        }

    # Advance the PO received state. Sum the accepted qty across EVERY accepted
    # GRN for this PO (this one is now ACCEPTED) and compare against the ordered
    # lines: full receipt -> RECEIVED, otherwise PARTIALLY_RECEIVED. Fail-soft.
    po_status = None
    if po_repo is not None and po_id:
        try:
            po = po_repo.find_by_id(po_id)
            received_by_product = _cumulative_received_by_product(grn_repo, po_id)
            po_items = (po.get("items") if po else []) or []
            po_status = compute_po_receipt_state(po_items, received_by_product)
            # Map the cumulative per-product received qty down onto each PO line
            # + derive the line residual status (drives the receiving cockpit's
            # "open POs" / "pending not-received" panels).
            updated_items = []
            for it in po_items:
                ordered = it.get("ordered_qty", it.get("quantity", 0)) or 0
                recv = received_by_product.get(it.get("product_id"), 0)
                updated_items.append(
                    {
                        **it,
                        "received_qty": recv,
                        "line_status": (
                            "RECEIVED"
                            if ordered and recv >= ordered
                            else ("PARTIAL" if recv > 0 else "OPEN")
                        ),
                    }
                )
            po_repo.update(
                po_id,
                {
                    "status": po_status,
                    "items": updated_items,
                    "received_qty_by_product": received_by_product,
                    "total_received_qty": sum(received_by_product.values()),
                    "last_received_at": datetime.now().isoformat(),
                },
            )
        except Exception:  # noqa: BLE001
            # Never lose the stock write on a PO-update failure. Best effort:
            # at least flag the PO as partially received.
            try:
                po_repo.update(po_id, {"status": "PARTIALLY_RECEIVED"})
                po_status = "PARTIALLY_RECEIVED"
            except Exception:  # noqa: BLE001
                pass

    return {
        "message": (
            "GRN accepted, stock added"
            if not unresolved_lines
            else "GRN partially accepted -- some lines need cataloguing"
        ),
        "grn_id": grn_id,
        "grn_status": grn_status,
        "units_added": units_added,
        "stock_ids": minted_stock_ids,
        "po_status": po_status,
        # Hub Phase 2: lines held because their product is not yet on the spine.
        # The FE renders a "Catalog now" affordance; cataloguing + re-accepting
        # mints them.
        "unresolved_lines": unresolved_lines,
        "needs_cataloguing": bool(unresolved_lines),
        "items_added": len(
            [
                i
                for i in (grn.get("items", []) or [])
                if (i.get("accepted_qty", 0) or 0) > 0
            ]
        ),
    }


@router.post("/grn/express", status_code=201)
async def express_receive_grn(
    body: ExpressGRNCreate,
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """One-shot receiving chain for a CLEAN delivery (procurement Phase 2).

    Runs create -> accept server-side through the SAME shared impls the
    two-step flow uses -- every control preserved: F-S3 attachment gate +
    BUG-010 file-exists check, PO exists + receivable, F2 store re-point to
    the PO's delivery store + cross-store 404, idempotent per-(grn, line)
    stock mint, PO receipt math. Then computes the purchase-invoice DRAFT and
    the 3-way match PREVIEW via the accountant console's own code paths
    WITHOUT persisting anything (no vendor_bills write, no AP booking -- the
    accountant attestation stays human) and raises a fail-soft accountant
    task deep-linking to the booking screen.

    CLEAN-ONLY: every line must be fully accepted (rejected_qty == 0 and
    accepted_qty == received_qty > 0); anything else answers 400
    EXPRESS_NOT_CLEAN so the frontend falls back to the existing two-step
    receive (which carries the full discrepancy controls). STANDARD PO-backed
    receipts only -- a DELIVERY_CHALLAN is 400 EXPRESS_STANDARD_ONLY.

    Failure atomicity: an HTTPException BEFORE the GRN row exists propagates
    unchanged (nothing was persisted). If accept fails AFTER the GRN was
    created, a 409 with code EXPRESS_PARTIAL carrying the grn_id is returned
    so the FE can point the user at the pending-receipts panel to accept or
    void it -- never a silently stranded PENDING GRN.
    """
    # 1) STANDARD-only: a Delivery Challan has no vendor invoice at receipt
    # time, so there is nothing to draft/match -- express cannot apply.
    if body.grn_subtype == GRN_SUBTYPE_DC:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "EXPRESS_STANDARD_ONLY",
                "message": (
                    "Express receive applies to STANDARD PO-backed receipts "
                    "only. Log a Delivery Challan through the normal "
                    "receiving screen."
                ),
            },
        )

    # 2) CLEAN-ONLY guard: one stable code for every violation so the FE keys
    # on it to route the user to the two-step receive.
    for idx, item in enumerate(body.items):
        if (
            item.rejected_qty != 0
            or item.received_qty <= 0
            or item.accepted_qty != item.received_qty
        ):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "EXPRESS_NOT_CLEAN",
                    "message": (
                        f"Line {idx + 1} (product {item.product_id}) is not a "
                        "clean receipt: express requires rejected_qty=0 and "
                        "accepted_qty equal to a positive received_qty on "
                        "every line. Use the standard receiving flow for "
                        "rejections or short/over receipts."
                    ),
                },
            )

    # 3) Create the GRN through the SHARED impl -- every create-side control
    # (attachment mandatory + file-exists, PO exists + receivable, store
    # re-point + cross-store 404) runs unchanged. An HTTPException here
    # propagates as-is: nothing has been persisted yet.
    try:
        grn_create = GRNCreate(
            po_id=body.po_id,
            vendor_invoice_no=body.vendor_invoice_no,
            vendor_invoice_date=body.vendor_invoice_date,
            # Ruling 14: express receiving IS the tally, declared once at the
            # header. The caller is asserting the whole delivery arrived exactly
            # as ordered and clean (the EXPRESS_NOT_CLEAN rule below refuses
            # anything else), so each line is ticked here rather than the
            # clean-delivery chain being locked out of its own shortcut.
            items=[
                GRNItemCreate(**it.model_dump(), tallied=True) for it in body.items
            ],
            notes=body.notes,
            grn_subtype=GRN_SUBTYPE_STANDARD,
            attachment_file_id=body.attachment_file_id,
            attachment_filename=body.attachment_filename,
            attachment_mime=body.attachment_mime,
        )
    except ValueError as exc:
        # e.g. blank po_id / vendor_invoice_no -- surface as a clean 400.
        raise HTTPException(status_code=400, detail=str(exc))

    create_res = await _create_grn_impl(grn_create, current_user)
    grn_id = create_res.get("grn_id")
    grn_number = create_res.get("grn_number")

    # 4) Accept through the SHARED impl. From here on the GRN row EXISTS, so a
    # failure must never surface as a generic error that strands a PENDING
    # GRN invisibly: EXPRESS_PARTIAL + grn_id tells the FE exactly where to
    # send the user (the existing pending-receipts panel handles accept/void).
    _partial_detail = {
        "code": "EXPRESS_PARTIAL",
        "grn_id": grn_id,
        "grn_number": grn_number,
        "message": (
            f"Receipt {grn_number} was created but not accepted -- open the "
            "receiving screen to accept or void it."
        ),
    }
    # EXPRESS_PARTIAL is a 409, NOT a 500. The GRN row already exists, so this
    # is a conflict, not a server fault -- and the distinction is a stock-safety
    # one, not a semantic nicety: the frontend api client auto-retries every 5xx
    # POST three times, _create_grn_impl has no duplicate guard for STANDARD
    # receipts (only DELIVERY_CHALLAN has one), and each retry therefore creates
    # a NEW grn_id and mints the WHOLE delivery again -- which the per-(grn,
    # line, unit) unique index cannot catch, because it keys on source_id.
    # Before this PR every raise inside accept was a PRE-mint 4xx, so a retried
    # express duplicate held zero units and was harmlessly voidable; the two
    # MID-mint 503s added here (count-verify and heartbeat) fire AFTER stock is
    # on the shelf, which is what turns that retry into real phantom stock.
    try:
        accept_res = await _accept_grn_impl(grn_id, current_user)
    except HTTPException as exc:
        logger.error(
            "[VENDOR] express-receive accept failed for %s: %s",
            grn_id,
            exc.detail,
        )
        raise HTTPException(status_code=409, detail=_partial_detail)
    except Exception as exc:  # noqa: BLE001
        logger.error("[VENDOR] express-receive accept crashed for %s: %s", grn_id, exc)
        raise HTTPException(status_code=409, detail=_partial_detail)

    grn_status = accept_res.get("grn_status")
    if grn_status is not None and grn_status != "ACCEPTED":
        # PARTIALLY_ACCEPTED: one or more lines were HELD (product not yet
        # catalogued / incomplete catalog). The chain cannot finish (F3 blocks
        # the invoice draft on a non-ACCEPTED GRN), so surface the exact
        # recovery instead of a half-true success.
        # 409, not 500 -- same reason as above: this receipt already holds real
        # stock, so it must never be auto-retried into a second one.
        raise HTTPException(
            status_code=409,
            detail={
                **_partial_detail,
                "grn_status": grn_status,
                "message": (
                    f"Receipt {grn_number} was created but only partially "
                    "accepted (some lines are held for cataloguing) -- open "
                    "the receiving screen to finish it."
                ),
            },
        )

    # 5) Invoice DRAFT + 3-way match PREVIEW -- the SAME code paths the
    # accountant's console uses (F3: draft_invoice_from_grn re-asserts the
    # GRN is ACCEPTED via _load_standard_grn). NOTHING is persisted: no
    # vendor_bills write, no AP booking -- express stops at a draft. Fail-soft:
    # the receive above is already complete and correct, so a draft/match
    # problem degrades to nulls rather than failing a successful receipt.
    invoice_draft = None
    match_preview = None
    draft = None
    try:
        from . import purchase_invoices as _pi

        draft = await _pi.draft_invoice_from_grn(grn_id, current_user)
        invoice_draft = {
            "vendor_id": draft.get("vendor_id"),
            "invoice_number": draft.get("invoice_number"),
            "place_of_supply": draft.get("place_of_supply"),
            "lines_count": len(draft.get("lines") or []),
            "totals": {
                "taxable_total": draft.get("taxable_total"),
                "cgst_total": draft.get("cgst_total"),
                "sgst_total": draft.get("sgst_total"),
                "igst_total": draft.get("igst_total"),
                "tax_total": draft.get("tax_total"),
                "total": draft.get("total"),
            },
        }
        try:
            _pi_db = _pi._get_db()
        except Exception:  # noqa: BLE001
            _pi_db = None
        tolerance = _pi._resolved_purchase_config(_pi_db)["match_tolerance_pct"]
        match = _pi._run_match_for_invoice(
            _pi_db, body.po_id, grn_id, draft.get("lines") or [], tolerance
        )
        if match:
            match_preview = {
                "match_status": match.get("match_status"),
                "exception_count": len(match.get("exceptions") or []),
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[VENDOR] express-receive draft/match preview failed for %s: %s",
            grn_id,
            exc,
        )

    # 6) Accountant task (reuses the receiving-discrepancy SYSTEM-task
    # pattern; fail-soft -- a task failure never rolls back the receive).
    accountant_task_id = None
    try:
        from ..services.task_triggers import create_system_task
        from ..dependencies import get_task_repository

        grn_doc = None
        try:
            _repo = get_grn_repository()
            grn_doc = _repo.find_by_id(grn_id) if _repo is not None else None
        except Exception:  # noqa: BLE001
            grn_doc = None
        vendor_label = (
            (draft or {}).get("vendor_name")
            or (grn_doc or {}).get("vendor_name")
            or (grn_doc or {}).get("vendor_id")
            or "vendor"
        )
        book_link = f"/purchase/invoices/book?grn_id={grn_id}"
        task = create_system_task(
            get_task_repository(),
            title=f"Book purchase invoice for GRN {grn_number} ({vendor_label})",
            description=(
                f"Express receive completed for goods receipt {grn_number} "
                f"(vendor invoice {body.vendor_invoice_no}). Review the draft "
                f"and book the purchase invoice: {book_link}."
                + (
                    f" 3-way match preview: {match_preview['match_status']} "
                    f"({match_preview['exception_count']} exception(s))."
                    if match_preview
                    else ""
                )
            ),
            priority="P2",
            category="Purchase",
            store_id=(grn_doc or {}).get("store_id"),
            dedupe_ref=f"express_invoice:{grn_id}",
            assigned_to="ACCOUNTANT",
            extra={
                "link": book_link,
                "payload": {
                    "grn_id": grn_id,
                    "grn_number": grn_number,
                    "po_id": body.po_id,
                    "match_status": (match_preview or {}).get("match_status"),
                    "exception_count": (match_preview or {}).get("exception_count"),
                },
            },
        )
        if task:
            accountant_task_id = task.get("task_id")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[VENDOR] express-receive accountant task failed for %s: %s",
            grn_id,
            exc,
        )

    return {
        "grn_id": grn_id,
        "grn_number": grn_number,
        "accepted_units": accept_res.get("units_added"),
        "po_status": accept_res.get("po_status"),
        "invoice_draft": invoice_draft,
        "match_preview": match_preview,
        "accountant_task_id": accountant_task_id,
    }


@router.post("/grn/{grn_id}/void")
async def void_grn(
    grn_id: str, current_user: dict = Depends(require_roles(*_VENDOR_ROLES))
):
    """Void a goods-receipt note that never put stock on the shelf
    (duplicate/mistake cleanup).

    Two gates, and the second one matters more than it looks. PENDING-only is
    the bookkeeping gate: an ACCEPTED / PARTIALLY_ACCEPTED GRN has already
    minted stock_units and must be corrected through a vendor return. But
    PENDING does NOT imply "no stock": the accept flow flips the status only
    AFTER the mint loop, so a worker killed mid-accept leaves the receipt
    PENDING with real units already on the shelf. Voiding THAT orphans those
    units (PO receipt math only sums ACCEPTED GRNs) and licenses a full re-mint
    under a new grn_id -- which the per-(grn, line, unit) unique index cannot
    catch, because it keys on source_id. So voiding is refused whenever
    stock_units already holds a row for this receipt, and the operator is told
    to accept it again instead (that retry is idempotent).

    Store-scoped like accept (cross-store reads as 404). The receipt row is kept
    (audit trail, numbering continuity) with status VOID -- the accept endpoint
    refuses VOID rows, and PO receipt math only ever sums ACCEPTED GRNs.
    """
    grn_repo = get_grn_repository()
    if grn_repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    grn = grn_repo.find_by_id(grn_id)
    if not grn:
        raise HTTPException(status_code=404, detail="GRN not found")
    if not can_access_store_scoped(grn.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="GRN not found")
    if grn.get("status") != "PENDING":
        raise HTTPException(
            status_code=400,
            detail=(
                "Only a PENDING GRN can be voided. This one is "
                f"{grn.get('status')} -- accepted stock must be corrected via a "
                "vendor return."
            ),
        )

    # MUTUAL EXCLUSION WITH ACCEPT. Void takes the SAME guarded claim the accept
    # path uses, so the two can never interleave. A point-in-time stock gate is
    # not enough on its own: a void landing in the window between an accept's
    # claim and its FIRST mint -- a window spanning the PO fetch, the product
    # lookup, the cost backfill write and the already-minted count -- passes
    # every gate here while the accept is still about to mint. The accept then
    # puts the whole delivery onto a VOID receipt: real sellable units behind a
    # voided doc, PO receipt math permanently short (it sums ACCEPTED GRNs only),
    # and re-accepting impossible because accept refuses a non-PENDING receipt.
    # Taking the claim closes both orderings, not just accept-then-void.
    claim_token = _claim_grn_for_accept(grn_repo, grn_id, current_user.get("user_id"))
    if claim_token is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This goods receipt is being accepted right now, so it cannot "
                "be voided. Wait for that to finish, then refresh -- if it put "
                "stock on the shelf it must be accepted, never voided."
            ),
        )

    try:
        # STOCK GATE. FAILS CLOSED: if we cannot establish that this receipt put
        # nothing on the shelf, we do not void it. Read UNDER the claim, so no
        # accept can be minting while we look.
        stock_repo = get_stock_repository()
        if stock_repo is not None:
            try:
                already_minted = _grn_already_minted(
                    stock_repo, {"source_type": "GRN", "source_id": grn_id}
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[VENDOR] GRN %s: could not check for already-minted units "
                    "before voiding (%s) -- refusing the void",
                    grn_id,
                    exc,
                )
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Could not check whether this goods receipt has already "
                        "put stock on the shelf, so it was not voided. Try "
                        "again in a moment."
                    ),
                ) from exc
            if already_minted > 0:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"This goods receipt has already put {already_minted} "
                        "unit(s) into stock -- an earlier acceptance was "
                        "interrupted before it finished. It cannot be voided, "
                        "because that would leave those units on the shelf with "
                        "no receipt behind them. Accept it again to finish "
                        "receiving; the units already received will not be "
                        "counted twice."
                    ),
                )

        # TERMINAL WRITE -- guarded exactly like the accept path's, because
        # holding the claim is NOT the same as writing under it. Each element
        # closes a DIFFERENT measured defect; the attribution below was checked
        # by mutation (remove one filter, see which probe reopens), because an
        # earlier version of this comment credited the wrong filter and would
        # have led the next reader to delete the one that is doing real work.
        #
        #   * status PENDING -- carries BOTH the "no stall, two clerks" shape
        #     and the parked-count shape. The PENDING assertion above reads the
        #     doc fetched BEFORE the claim, and the claim itself admits
        #     PARTIALLY_ACCEPTED, so without this filter a colleague's accept
        #     landing in between voids a receipt that now holds stock. Measured
        #     with the token filter REMOVED: both of those still 409 here,
        #     because by then the doc is ACCEPTED / PARTIALLY_ACCEPTED and no
        #     longer matches.
        #
        #   * accept_lock_token -- its UNIQUE job is the shape where the doc is
        #     still PENDING when the parked void wakes up, so the status filter
        #     cannot help: an accept takes the stale claim over, mints every
        #     unit, and its terminal flip CANNOT be written (see
        #     _advance_grn_terminal_status -- "the receipt stays in its previous
        #     status"), then _finalise_grn_accept_metadata clears the token.
        #     Doc PENDING, stock on the shelf, token gone. Measured with this
        #     filter removed: 200 {"grn_status": "VOID"} over 24 real units.
        #     Regression-tested by
        #     test_a_parked_void_cannot_void_a_receipt_whose_flip_failed.
        #
        # And branching on the RESULT is what stops a swallowed write answering
        # a green "GRN voided" over a doc that is still PENDING.
        void_patch = {
            "status": "VOID",
            "voided_at": datetime.now().isoformat(),
            "voided_by": current_user.get("user_id"),
        }
        written = _guarded_grn_write(
            grn_repo,
            {
                "grn_id": grn_id,
                "status": "PENDING",
                "accept_lock_token": claim_token,
            },
            {"$set": void_patch},
        )
        if written is _GRN_WRITE_ERROR:
            # Deliberately does NOT claim "nothing was voided": the write may
            # have applied server-side and only its reply been lost, in which
            # case the receipt IS void. The stock gate above already proved zero
            # units, so no stock is at risk either way -- but the message must
            # not assert an outcome we cannot see.
            raise HTTPException(
                status_code=503,
                detail=(
                    "The database did not confirm whether this goods receipt "
                    "was voided. Refresh to see its current state before trying "
                    "again -- no stock was affected."
                ),
            )
        if written is None:
            # Minimal mock with no atomic primitive: plain write, but still
            # check it landed rather than assuming it did.
            if not grn_repo.update(grn_id, void_patch):
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "The goods receipt could not be voided. Refresh and try "
                        "again; nothing was voided."
                    ),
                )
        elif not written:
            try:
                current = grn_repo.find_by_id(grn_id)
            except Exception:  # noqa: BLE001
                current = None
            status_now = (current or {}).get("status") or "unknown"
            logger.error(
                "[VENDOR] GRN %s: void did NOT apply -- the receipt is now %s "
                "(it changed while this void was in flight)",
                grn_id,
                status_now,
            )
            if status_now in _GRN_TERMINAL_ACCEPT_STATUSES:
                detail = (
                    f"This goods receipt is now {status_now} and holds stock, "
                    "so it cannot be voided -- accepted stock must be corrected "
                    "via a vendor return. Refresh to see its current state."
                )
            else:
                detail = (
                    "This goods receipt changed while it was being voided (it "
                    f"is now {status_now}), so nothing was voided. Refresh and "
                    "check its current state before trying again."
                )
            raise HTTPException(status_code=409, detail=detail)

        # Fail-soft audit trail (same contract as the other GRN mutations).
        try:
            audit = get_audit_repository()
            if audit is not None:
                audit.create(
                    {
                        "kind": "grn_void",
                        "entity_type": "grn",
                        "entity_id": grn_id,
                        "action": "VOID",
                        "performed_by": current_user.get("user_id"),
                        "details": {
                            "grn_number": grn.get("grn_number"),
                            "po_id": grn.get("po_id"),
                            "store_id": grn.get("store_id"),
                        },
                    }
                )
        except Exception:  # noqa: BLE001 - audit must never block the void
            pass

        return {
            "message": "GRN voided",
            "grn_id": grn_id,
            "grn_number": grn.get("grn_number"),
            "grn_status": "VOID",
        }
    finally:
        # VOID is terminal, so the claim is always handed back -- on the happy
        # path and on every refusal above.
        _release_grn_accept_claim(grn_repo, grn_id, claim_token)


@router.post("/grn/{grn_id}/escalate")
async def escalate_grn(
    grn_id: str,
    note: str = Query(...),
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """Escalate GRN to HQ for review"""
    grn_repo = get_grn_repository()

    if grn_repo is not None:
        grn = grn_repo.find_by_id(grn_id)
        if not grn:
            raise HTTPException(status_code=404, detail="GRN not found")

        grn_repo.update(
            grn_id,
            {
                "status": "ESCALATED",
                "escalated_at": datetime.now().isoformat(),
                "escalated_by": current_user.get("user_id"),
                "escalation_note": note,
            },
        )

    return {"message": "GRN escalated to HQ", "grn_id": grn_id}


# ============================================================================
# VENDOR PORTAL TOKEN ENDPOINTS
# ============================================================================
# Issue / list / revoke long-lived bearer tokens that grant a single vendor
# access to the public `/vendor-portal/{token_id}/...` surface. Only
# SUPERADMIN / ADMIN can mint or revoke (token === credential, treat
# generation like creating an API key).


class PortalTokenIssueRequest(BaseModel):
    ttl_days: Optional[int] = 365


def _require_admin(current_user: dict) -> None:
    """Refuse if the caller isn't SUPERADMIN or ADMIN."""
    roles = current_user.get("roles", []) or []
    if not any(r in roles for r in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(
            status_code=403, detail="Only SUPERADMIN/ADMIN can manage portal tokens"
        )


@router.post("/{vendor_id}/portal-token", status_code=201)
async def issue_portal_token(
    vendor_id: str,
    body: Optional[PortalTokenIssueRequest] = None,
    current_user: dict = Depends(get_current_user),
):
    """Mint a fresh portal token for a vendor.

    Returns the token_id (the bearer secret) plus a ready-to-share URL.
    Existing active tokens for the vendor are NOT auto-revoked; admin
    rotates manually via DELETE.
    """
    _require_admin(current_user)

    vendor_repo = get_vendor_repository()
    if vendor_repo is None:
        raise HTTPException(status_code=503, detail="Vendor storage unavailable")
    vendor = vendor_repo.find_by_id(vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")

    token_repo = get_vendor_portal_token_repository()
    if token_repo is None:
        raise HTTPException(status_code=503, detail="Portal token storage unavailable")

    ttl_days = (body.ttl_days if body and body.ttl_days else 365) or 365
    token = token_repo.issue(
        vendor_id=vendor_id,
        vendor_name=vendor.get("trade_name") or vendor.get("legal_name") or vendor_id,
        created_by=current_user.get("user_id") or "system",
        ttl_days=int(ttl_days),
    )

    # Audit
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "vendor.portal_token_issue",
                    "entity_type": "vendor",
                    "entity_id": vendor_id,
                    "user_id": current_user.get("user_id"),
                    "detail": {
                        "token_id": token.get("token_id"),
                        "ttl_days": ttl_days,
                        "vendor_name": token.get("vendor_name"),
                    },
                }
            )
    except Exception:
        pass

    return {
        "token_id": token.get("token_id"),
        "vendor_id": vendor_id,
        "vendor_name": token.get("vendor_name"),
        "expires_at": (
            token.get("expires_at").isoformat()
            if hasattr(token.get("expires_at"), "isoformat")
            else token.get("expires_at")
        ),
        # Frontend convenience — admin can copy this URL and email it
        # to the lab. Backend doesn't enforce anything about the host
        # part; the relative path is what matters for the API.
        "portal_path": f"/vendor-portal/{token.get('token_id')}",
        "message": "Vendor portal token issued",
    }


@router.get("/{vendor_id}/portal-tokens")
async def list_portal_tokens(
    vendor_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Return all tokens (active + revoked) for a vendor."""
    _require_admin(current_user)
    repo = get_vendor_portal_token_repository()
    if repo is None:
        return {"vendor_id": vendor_id, "tokens": []}
    rows = repo.list_for_vendor(vendor_id)
    # Mask the token_id in the list view to prevent shoulder-surfing —
    # admin sees the prefix only, can copy from the issue response.
    masked = []
    for r in rows:
        tid = r.get("token_id") or ""
        masked.append(
            {
                "token_id_prefix": tid[:8] + "..." if tid else "",
                "vendor_id": r.get("vendor_id"),
                "vendor_name": r.get("vendor_name"),
                "active": r.get("active", False),
                "created_at": (
                    r.get("created_at").isoformat()
                    if hasattr(r.get("created_at"), "isoformat")
                    else r.get("created_at")
                ),
                "created_by": r.get("created_by"),
                "expires_at": (
                    r.get("expires_at").isoformat()
                    if hasattr(r.get("expires_at"), "isoformat")
                    else r.get("expires_at")
                ),
                "last_used_at": (
                    r.get("last_used_at").isoformat()
                    if hasattr(r.get("last_used_at"), "isoformat")
                    else r.get("last_used_at")
                ),
                "use_count": r.get("use_count", 0),
            }
        )
    return {"vendor_id": vendor_id, "tokens": masked}


@router.delete("/{vendor_id}/portal-token/{token_id}")
async def revoke_portal_token(
    vendor_id: str,
    token_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Revoke (deactivate) a portal token. Token row is kept for audit."""
    _require_admin(current_user)
    repo = get_vendor_portal_token_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Portal token storage unavailable")
    doc = repo.find_by_id(token_id)
    if doc is None or doc.get("vendor_id") != vendor_id:
        raise HTTPException(status_code=404, detail="Token not found")
    if not repo.revoke(token_id, current_user.get("user_id") or "system"):
        raise HTTPException(status_code=500, detail="Failed to revoke token")
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "vendor.portal_token_revoke",
                    "entity_type": "vendor",
                    "entity_id": vendor_id,
                    "user_id": current_user.get("user_id"),
                    "detail": {"token_id": token_id},
                }
            )
    except Exception:
        pass
    return {
        "token_id": token_id,
        "vendor_id": vendor_id,
        "active": False,
        "message": "Token revoked",
    }


# ============================================================================
# ACCOUNTS-PAYABLE: vendor bills, payments, debit notes, ledger, aging
# ============================================================================
# The PO/GRN flow above tracks GOODS. This block tracks MONEY: a vendor bill
# (purchase invoice) is the payable; payments (with optional TDS) and debit
# notes discharge it. Pure money/date math lives in services/ap_engine.py so
# these handlers stay thin (fetch rows -> call engine -> return).
#
# Route-order: every route here is a decorator, so it registers BEFORE the
# catch-all `/{vendor_id}` added at the very bottom. `/ap-aging` (one segment)
# therefore resolves to its own handler, not to get_vendor.


class VendorBillCreate(BaseModel):
    bill_number: str  # the vendor's own invoice / bill number
    bill_date: str  # ISO date (YYYY-MM-DD)
    taxable_amount: float = Field(..., ge=0)
    tax_amount: float = Field(0, ge=0)
    total_amount: float = Field(..., gt=0)
    po_id: Optional[str] = None
    grn_id: Optional[str] = None
    notes: Optional[str] = None


class VendorPaymentCreate(BaseModel):
    amount: float = Field(..., gt=0)  # cash actually paid to the vendor
    payment_date: str  # ISO date
    mode: str = "BANK"  # CASH / BANK / UPI / CHEQUE / NEFT
    bill_id: Optional[str] = None  # allocate to a bill; else on-account/advance
    tds_section: Optional[str] = "NONE"  # see ap_engine.TDS_SECTIONS
    tds_base: Optional[float] = Field(default=None, ge=0)  # base for auto-TDS
    tds_amount: Optional[float] = Field(default=None, ge=0)  # explicit override
    reference: Optional[str] = None  # UTR / cheque no / txn id
    notes: Optional[str] = None


# Recognized vendor credit-note / debit-note types. Every type lands in the
# SAME vendor_debit_notes collection and reduces the payable by its `amount`
# (ap_engine.build_aging / build_ledger treat all rows identically), so the
# GL/ledger treatment of the two new types (DISCOUNT_CN, QUALITY_CN) mirrors the
# existing ones exactly -- the type only categorises WHY the payable dropped.
#   RETURN_CN  -- goods returned to vendor (RTV) -- the historical default here.
#   SCHEME_CN  -- scheme / target / volume rebate from the vendor.
#   DISCOUNT_CN-- a negotiated post-billing price discount (NEW).
#   QUALITY_CN -- compensation for defective / sub-spec goods kept, not returned (NEW).
# (VOLUME_REBATE is the machine-posted scheme source written by rebate_engine;
#  it is recognised on read but not a manual-create option here.)
VENDOR_CN_TYPES = ("RETURN_CN", "SCHEME_CN", "DISCOUNT_CN", "QUALITY_CN")


class DebitNoteCreate(BaseModel):
    amount: float = Field(..., gt=0)
    date: str  # ISO date
    reason: str
    bill_id: Optional[str] = None  # allocate to a bill; else on-account
    grn_id: Optional[str] = None  # link to the rejected-goods GRN, if any
    # Credit-note category. Defaults to RETURN_CN (the historical behaviour --
    # debit notes here were created for rejected/returned goods).
    cn_type: str = "RETURN_CN"

    @field_validator("cn_type")
    @classmethod
    def _validate_cn_type(cls, v):
        v = (v or "RETURN_CN").strip().upper()
        if v not in VENDOR_CN_TYPES:
            raise ValueError("cn_type must be one of " + ", ".join(VENDOR_CN_TYPES))
        return v


def _clean(doc: dict) -> dict:
    """Strip Mongo's _id so a freshly-inserted doc is JSON-serialisable."""
    return {k: v for k, v in doc.items() if k != "_id"}


def _recompute_bill_status(db, bill_id: Optional[str]) -> None:
    """Re-derive a bill's status (OUTSTANDING / PARTIAL / PAID) from its
    allocated payments + debit notes. Fail-soft."""
    if db is None or not bill_id:
        return
    try:
        bill = db.get_collection("vendor_bills").find_one(
            {"bill_id": bill_id}, {"_id": 0}
        )
        if not bill:
            return
        payments = list(
            db.get_collection("vendor_payments").find({"bill_id": bill_id}, {"_id": 0})
        )
        debit_notes = list(
            db.get_collection("vendor_debit_notes").find(
                {"bill_id": bill_id}, {"_id": 0}
            )
        )
        out = ap_engine.bill_outstanding(bill, payments, debit_notes)
        total = float(bill.get("total_amount") or 0)
        if out <= 0.01:
            status = "PAID"
        elif out < total:
            status = "PARTIAL"
        else:
            status = "OUTSTANDING"
        db.get_collection("vendor_bills").update_one(
            {"bill_id": bill_id},
            {"$set": {"outstanding": out, "status": status}},
        )
    except Exception:
        pass


@router.get("/ap-aging")
async def ap_aging(
    as_of: Optional[str] = Query(None, description="ISO date; defaults to today"),
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Org-wide accounts-payable aging, grouped by vendor + grand totals.

    Buckets each outstanding bill by days past its due date (current / 1-30 /
    31-60 / 61-90 / 90+). ADMIN / ACCOUNTANT only.
    """
    db = _get_db()
    if db is None:
        return {"as_of": as_of, "totals": {}, "vendors": []}
    try:
        bills = list(
            db.get_collection("vendor_bills").find(
                {"status": {"$ne": "PAID"}}, {"_id": 0}
            )
        )
        payments = list(db.get_collection("vendor_payments").find({}, {"_id": 0}))
        debit_notes = list(db.get_collection("vendor_debit_notes").find({}, {"_id": 0}))
    except Exception:
        bills, payments, debit_notes = [], [], []
    return ap_engine.build_aging_by_vendor(bills, payments, debit_notes, as_of)


@router.post("/{vendor_id}/bills", status_code=201)
async def create_vendor_bill(
    vendor_id: str,
    bill: VendorBillCreate,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Record a vendor bill (purchase invoice) as a payable. Due date is
    derived from the vendor's credit terms."""
    vendor_repo = get_vendor_repository()
    vendor = vendor_repo.find_by_id(vendor_id) if vendor_repo is not None else None
    if vendor_repo is not None and vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")

    # Data-entry guard: taxable + tax should reconcile to the bill total
    # (allow Rs 1 of rounding slack).
    if abs((bill.taxable_amount + bill.tax_amount) - bill.total_amount) > 1.0:
        raise HTTPException(
            status_code=400,
            detail="taxable_amount + tax_amount must equal total_amount",
        )

    # Accounting period lock: cannot record vendor bills into a closed month.
    db_early = _get_db()
    if db_early is not None:
        from .finance import check_period_locked

        check_period_locked(db_early, bill.bill_date)

    # This door accepts a grn_id but validated NOTHING about it -- so the same
    # two money leaks the first-class purchase-invoice door had were reachable
    # here too: bill another vendor's receipt, or bill one receipt again and
    # again. Reuse the purchase-invoice guards (imported at call time, like
    # check_period_locked above, so no cross-router import cycle). No grn_id ->
    # a plain header bill, unchanged.
    if bill.grn_id:
        from .purchase_invoices import assert_grn_billable_header_only

        assert_grn_billable_header_only(db_early, bill.grn_id, vendor_id)

    # Duplicate bill guard: the same vendor invoice number must not be recorded
    # twice for the same vendor. A double-entry would double the outstanding
    # payable and produce a duplicate payment row in the ledger.
    if db_early is not None:
        try:
            dup = db_early.get_collection("vendor_bills").find_one(
                {"vendor_id": vendor_id, "bill_number": bill.bill_number},
                {"_id": 0, "bill_id": 1},
            )
            if dup:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Bill number '{bill.bill_number}' is already recorded "
                        f"for this vendor. Duplicate vendor invoices are not allowed."
                    ),
                )
        except HTTPException:
            raise
        except Exception:
            pass  # fail-soft: skip dup check on DB error, proceed with insert

    credit_days = int((vendor or {}).get("credit_days", 30) or 30)
    due_date = ap_engine.compute_due_date(bill.bill_date, credit_days)
    bill_id = str(uuid.uuid4())
    doc = {
        "bill_id": bill_id,
        "vendor_id": vendor_id,
        "vendor_name": (vendor or {}).get("trade_name")
        or (vendor or {}).get("legal_name"),
        "bill_number": bill.bill_number,
        "bill_date": bill.bill_date,
        "due_date": due_date,
        "credit_days": credit_days,
        "taxable_amount": round(bill.taxable_amount, 2),
        "tax_amount": round(bill.tax_amount, 2),
        "total_amount": round(bill.total_amount, 2),
        "outstanding": round(bill.total_amount, 2),
        "po_id": bill.po_id,
        "grn_id": bill.grn_id,
        "notes": bill.notes,
        "status": "OUTSTANDING",
        "created_by": current_user.get("user_id"),
        "created_at": datetime.now().isoformat(),
    }
    db = _get_db()
    if db is not None:
        try:
            db.get_collection("vendor_bills").insert_one(dict(doc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Failed to save bill") from exc
    return _clean(doc)


@router.get("/{vendor_id}/bills")
async def list_vendor_bills(
    vendor_id: str,
    status: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """List a vendor's bills (newest first)."""
    db = _get_db()
    if db is None:
        return {"bills": [], "total": 0}
    flt: dict = {"vendor_id": vendor_id}
    if status:
        flt["status"] = status
    try:
        bills = list(db.get_collection("vendor_bills").find(flt, {"_id": 0}))
    except Exception:
        bills = []
    bills.sort(key=lambda b: b.get("bill_date") or "", reverse=True)
    return {"bills": bills, "total": len(bills)}


def _rejected_goods_hold(db, bill_id: Optional[str]) -> Optional[str]:
    """Owner ruling 7: a purchase bill may not be passed for payment while goods
    on its goods receipt were REJECTED and no debit note has been raised.

    Returns a plain-English reason to refuse the payment, or None when the bill
    is clear. Reads the bill -> its receipts -> the rejected quantities, then
    looks for a debit note that references either that receipt or the bill
    itself (the DebitNoteCreate schema has carried `grn_id` -- "link to the
    rejected-goods GRN" -- since it was written; nothing had ever read it).

    A DC-consolidated bill stores grn_id None and carries its receipts in
    linked_dc_ids (they live in the same `grns` collection). Reading grn_id
    alone paid the very delivery a GRN-linked bill would have held, so BOTH
    fields are walked here -- every payment routes through this one helper.

    Fail-soft: any error returns None, because a lookup failure must not block
    a legitimate payment.
    """
    if db is None or not bill_id:
        return None
    try:
        bill = db.get_collection("vendor_bills").find_one(
            {"bill_id": bill_id},
            {"_id": 0, "grn_id": 1, "linked_dc_ids": 1, "bill_number": 1},
        )
        if not bill:
            return None
        receipt_ids = [g for g in [bill.get("grn_id")] if g]
        receipt_ids += [d for d in (bill.get("linked_dc_ids") or []) if d]
        if not receipt_ids:
            return None
        grns = db.get_collection("grns")
        notes = db.get_collection("vendor_debit_notes")
        for grn_id in receipt_ids:
            grn = grns.find_one(
                {"grn_id": grn_id}, {"_id": 0, "items": 1, "grn_number": 1}
            )
            if not grn:
                continue
            rejected = 0
            for it in grn.get("items") or []:
                try:
                    rejected += int(it.get("rejected_qty") or 0)
                except (TypeError, ValueError):
                    continue
            if rejected <= 0:
                continue
            if any(
                notes.find_one(flt, {"_id": 0, "debit_note_id": 1})
                for flt in ({"grn_id": grn_id}, {"bill_id": bill_id})
            ):
                continue
            return (
                f"{rejected} unit(s) on goods receipt "
                f"{grn.get('grn_number') or grn_id} were rejected and no debit "
                f"note has been raised against the vendor. Raise the debit note "
                f"for the rejected goods first - then this bill can be paid."
            )
        return None
    except Exception:  # noqa: BLE001 - a lookup failure must not block payment
        return None


@router.post("/{vendor_id}/payments", status_code=201)
async def create_vendor_payment(
    vendor_id: str,
    payment: VendorPaymentCreate,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Record a payment to a vendor (optionally allocated to a bill, optionally
    with TDS withheld). Recomputes the allocated bill's status."""
    vendor_repo = get_vendor_repository()
    vendor = vendor_repo.find_by_id(vendor_id) if vendor_repo is not None else None
    if vendor_repo is not None and vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")

    # TDS: explicit amount wins; else auto-compute from section + base.
    tds_section = (payment.tds_section or "NONE").upper()
    if payment.tds_amount is not None:
        tds_amount = round(payment.tds_amount, 2)
    elif tds_section != "NONE":
        base = payment.tds_base if payment.tds_base is not None else payment.amount
        tds_amount = ap_engine.compute_tds(base, tds_section)["tds_amount"]
    else:
        tds_amount = 0.0

    # Accounting period lock: cannot record vendor payments into a closed month.
    db = _get_db()
    if db is not None:
        from .finance import check_period_locked

        check_period_locked(db, payment.payment_date)

    # Owner ruling 7: HOLD the bill while goods were rejected and no debit note
    # exists. Until now a rejection inside the 5% match tolerance was paid in
    # full, silently -- we paid for the defects AND claimed the ITC on them.
    hold = _rejected_goods_hold(db, payment.bill_id)
    if hold:
        raise HTTPException(
            status_code=409,
            detail={"code": "REJECTED_GOODS_NO_DEBIT_NOTE", "message": hold},
        )

    payment_id = str(uuid.uuid4())
    doc = {
        "payment_id": payment_id,
        "vendor_id": vendor_id,
        "vendor_name": (vendor or {}).get("trade_name")
        or (vendor or {}).get("legal_name"),
        "bill_id": payment.bill_id,
        "amount": round(payment.amount, 2),
        "mode": payment.mode,
        "payment_date": payment.payment_date,
        "tds_section": tds_section,
        "tds_base": payment.tds_base,
        "tds_amount": tds_amount,
        "reference": payment.reference,
        "notes": payment.notes,
        "created_by": current_user.get("user_id"),
        "created_at": datetime.now().isoformat(),
    }
    db = _get_db()
    if db is not None:
        try:
            db.get_collection("vendor_payments").insert_one(dict(doc))
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail="Failed to save payment"
            ) from exc
        _recompute_bill_status(db, payment.bill_id)
    return _clean(doc)


@router.get("/{vendor_id}/payments")
async def list_vendor_payments(
    vendor_id: str,
    current_user: dict = Depends(get_current_user),
):
    """List a vendor's payments (newest first)."""
    db = _get_db()
    if db is None:
        return {"payments": [], "total": 0}
    try:
        rows = list(
            db.get_collection("vendor_payments").find(
                {"vendor_id": vendor_id}, {"_id": 0}
            )
        )
    except Exception:
        rows = []
    rows.sort(key=lambda p: p.get("payment_date") or "", reverse=True)
    return {"payments": rows, "total": len(rows)}


@router.post("/{vendor_id}/debit-notes", status_code=201)
async def create_debit_note(
    vendor_id: str,
    note: DebitNoteCreate,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Issue a debit note against a vendor (e.g. for rejected/returned goods).
    Reduces the payable. Recomputes the allocated bill's status."""
    vendor_repo = get_vendor_repository()
    vendor = vendor_repo.find_by_id(vendor_id) if vendor_repo is not None else None
    if vendor_repo is not None and vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")

    dn_id = str(uuid.uuid4())
    prefix = vendor_id[:3].upper() if vendor_id else "DN"
    doc = {
        "debit_note_id": dn_id,
        "debit_note_number": f"DN-{prefix}-{datetime.now().strftime('%y%m%d%H%M')}",
        "vendor_id": vendor_id,
        "vendor_name": (vendor or {}).get("trade_name")
        or (vendor or {}).get("legal_name"),
        "bill_id": note.bill_id,
        "grn_id": note.grn_id,
        "amount": round(note.amount, 2),
        "date": note.date,
        "reason": note.reason,
        # Credit-note category (RETURN_CN / SCHEME_CN / DISCOUNT_CN / QUALITY_CN).
        # `source` mirrors it for parity with the machine-posted rebate CN rows
        # (rebate_engine writes source=VOLUME_REBATE) so every AP/ledger reader
        # can categorise a credit note by a single field.
        "cn_type": note.cn_type,
        "source": note.cn_type,
        "created_by": current_user.get("user_id"),
        "created_at": datetime.now().isoformat(),
    }
    db = _get_db()
    if db is not None:
        try:
            db.get_collection("vendor_debit_notes").insert_one(dict(doc))
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail="Failed to save debit note"
            ) from exc
        _recompute_bill_status(db, note.bill_id)
    return _clean(doc)


@router.get("/{vendor_id}/debit-notes")
async def list_debit_notes(
    vendor_id: str,
    current_user: dict = Depends(get_current_user),
):
    """List a vendor's debit notes (newest first)."""
    db = _get_db()
    if db is None:
        return {"debit_notes": [], "total": 0}
    try:
        rows = list(
            db.get_collection("vendor_debit_notes").find(
                {"vendor_id": vendor_id}, {"_id": 0}
            )
        )
    except Exception:
        rows = []
    rows.sort(key=lambda d: d.get("date") or "", reverse=True)
    return {"debit_notes": rows, "total": len(rows)}


@router.get("/{vendor_id}/ledger")
async def vendor_ledger(
    vendor_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Full vendor ledger: bills (credit) + payments + debit notes (debit) with
    a running payable balance, plus an aging snapshot for the same vendor."""
    db = _get_db()
    vendor_repo = get_vendor_repository()
    vendor = vendor_repo.find_by_id(vendor_id) if vendor_repo is not None else None
    if db is None:
        return {
            "vendor_id": vendor_id,
            "vendor": vendor,
            "ledger": ap_engine.build_ledger([], [], []),
            "aging": ap_engine.build_aging([], [], []),
        }
    try:
        bills = list(
            db.get_collection("vendor_bills").find({"vendor_id": vendor_id}, {"_id": 0})
        )
        payments = list(
            db.get_collection("vendor_payments").find(
                {"vendor_id": vendor_id}, {"_id": 0}
            )
        )
        debit_notes = list(
            db.get_collection("vendor_debit_notes").find(
                {"vendor_id": vendor_id}, {"_id": 0}
            )
        )
    except Exception:
        bills, payments, debit_notes = [], [], []
    return {
        "vendor_id": vendor_id,
        "vendor": vendor,
        "ledger": ap_engine.build_ledger(bills, payments, debit_notes),
        "aging": ap_engine.build_aging(bills, payments, debit_notes),
    }


# ============================================================================
# INV-13: VENDOR PERFORMANCE SCORING + PURCHASE-HISTORY ANALYTICS
# ============================================================================
# Score vendors on delivery timeliness and goods quality from GRN data, and
# surface a purchase-history breakdown so buying decisions are data-driven
# rather than WhatsApp-tracked.
#
# Performance score:
#   on_time_rate    = GRNs accepted within PO expected_date / total GRNs
#   acceptance_rate = total_accepted / total_received across GRNs
#   overall_score   = simple weighted average (40 % timeliness + 60 % quality)
#
# All computations are fail-soft: a missing DB or collection returns honest
# empty states.  No fabricated numbers (SYSTEM_INTENT).


def _vendor_mtd_spend(db, vendor_id: str) -> float:
    """Sum of this vendor's bills dated in the CURRENT calendar month (rupees).

    Reads vendor_bills.total_amount where bill_date (fallback created_at) falls
    in the current month. Fail-soft: missing DB / collection / parse error -> 0.0
    (an honest zero, never a fabricated number per SYSTEM_INTENT)."""
    if db is None:
        return 0.0
    # BUG-104. The VALUE side (`bill_date`) is already an IST business-date
    # string, so the month PREFIX it is matched against must be the IST month
    # too. On the UTC clock, between 00:00 and 05:30 IST on the 1st this asked
    # for LAST month and the vendor's month-to-date spend read as the previous
    # month's total for five and a half hours every month.
    month_prefix = now_ist().strftime("%Y-%m")  # "2026-06"
    total = 0.0
    try:
        bills = db.get_collection("vendor_bills").find(
            {"vendor_id": vendor_id},
            {"_id": 0, "total_amount": 1, "bill_date": 1, "created_at": 1},
        )
        for b in bills:
            when = str(b.get("bill_date") or b.get("created_at") or "")[:7]
            if when == month_prefix:
                try:
                    total += float(b.get("total_amount") or 0)
                except (TypeError, ValueError):
                    pass
    except Exception:  # noqa: BLE001
        return 0.0
    return round(total, 2)


def _vendor_qc_pass_rate(db, vendor_id: str):
    """Quality pass-rate for a vendor, joining GRN QC + workshop QC outcomes.

    Two QC signals are unioned into one pass-rate:
      * GRN QC: units accepted vs received across this vendor's ACCEPTED GRNs
        (total_accepted / total_received).
      * Workshop QC: lens jobs routed to this vendor (lab) -- a job passes when
        qc_passed is True (or waived), fails when qc_passed is False. Only jobs
        that actually went through QC count toward the sample.

    Returns (pass_rate, sample_size). pass_rate is units+jobs passed / total
    units+jobs evaluated, rounded to 4 dp; None when there is NO QC signal at
    all. Fail-soft: any error -> (None, 0). No fabricated numbers."""
    if db is None:
        return None, 0
    passed = 0
    total = 0
    # --- GRN QC (goods-receipt inspection outcome) ---
    try:
        grns = db.get_collection("grns").find(
            {"vendor_id": vendor_id, "status": "ACCEPTED"},
            {"_id": 0, "total_received": 1, "total_accepted": 1},
        )
        for g in grns:
            try:
                recv = int(g.get("total_received") or 0)
                acc = int(g.get("total_accepted") or 0)
            except (TypeError, ValueError):
                continue
            if recv > 0:
                total += recv
                passed += max(0, min(acc, recv))
    except Exception:  # noqa: BLE001
        pass
    # --- Workshop QC (lens jobs routed to this lab/vendor) ---
    try:
        jobs = db.get_collection("workshop_jobs").find(
            {"vendor_id": vendor_id},
            {"_id": 0, "qc_passed": 1, "qc_waived": 1, "qc_history": 1},
        )
        for j in jobs:
            history = [h for h in (j.get("qc_history") or []) if isinstance(h, dict)]
            had_qc = bool(history) or (j.get("qc_passed") is not None)
            if not had_qc:
                continue
            total += 1
            if history:
                ok = not any(h.get("passed") is False for h in history)
            else:
                ok = bool(j.get("qc_passed")) or bool(j.get("qc_waived"))
            if ok:
                passed += 1
    except Exception:  # noqa: BLE001
        pass

    if total <= 0:
        return None, 0
    return round(passed / total, 4), total


@router.get("/{vendor_id}/performance")
async def vendor_performance(
    vendor_id: str,
    months: int = Query(
        6,
        ge=1,
        le=24,
        description="Number of rolling months to include in the score window.",
    ),
    current_user: dict = Depends(get_current_user),
):
    """Return a performance score for the vendor over the last `months` months (INV-13).

    Score components:
      - acceptance_rate: fraction of received units accepted (quality signal).
      - on_time_rate: fraction of GRN accepts that landed on or before the PO
        expected_date (delivery punctuality signal).
      - overall_score: 60 % acceptance + 40 % on_time (0-100 index).

    When fewer than 3 GRNs exist in the window the score is flagged
    ``insufficient_data: true`` so callers know the rating has low confidence.
    Honest empty state: if the vendor has no GRN history an honest zero is
    returned, not a fabricated average.
    """
    db = _get_db()
    vendor_repo = get_vendor_repository()
    vendor = vendor_repo.find_by_id(vendor_id) if vendor_repo is not None else None

    # MTD spend is independent of GRN history -- compute it up front so even a
    # vendor with no GRNs in the window still reports what we've billed this
    # month. Fail-soft: any error -> 0.0 (honest, never fabricated).
    mtd_spend = _vendor_mtd_spend(db, vendor_id)
    # QC pass-rate joins GRN QC (accepted/received) + workshop QC (job pass/fail)
    # for this vendor. Fail-soft: returns None when there is no QC signal at all.
    qc_pass_rate, qc_sample = _vendor_qc_pass_rate(db, vendor_id)

    empty = {
        "vendor_id": vendor_id,
        "vendor_name": (
            (vendor.get("trade_name") or vendor.get("legal_name")) if vendor else None
        ),
        "window_months": months,
        "grns_evaluated": 0,
        "acceptance_rate": None,
        "on_time_rate": None,
        "qc_pass_rate": qc_pass_rate,
        "qc_sample_size": qc_sample,
        "mtd_spend": mtd_spend,
        "overall_score": None,
        "insufficient_data": True,
        "note": "No GRN data found for this vendor in the selected window.",
    }
    if db is None:
        return empty

    try:
        from datetime import timedelta

        # BUG-104 sweep finding, NOT an IST bug -- a FRAME-TYPE bug of the same
        # family as buy_desk.py:110. `grns.created_at` is written by
        # BaseRepository._add_timestamps as a BSON **datetime** (it overwrites
        # whatever the caller passed), and Mongo type-brackets: a STRING $gte
        # never matches a Date, so this query returned ZERO rows and the vendor
        # GRN scorecard has been permanently, silently empty. Pass the datetime.
        # `cutoff` itself is pure elapsed time (now minus N days) in the same
        # naive-UTC frame the instants are stored in, so it needs no IST shift.
        cutoff = datetime.now() - timedelta(days=months * 30)

        grn_coll = db.get_collection("grns")
        grns = list(
            grn_coll.find(
                {
                    "vendor_id": vendor_id,
                    "status": "ACCEPTED",
                    "created_at": {"$gte": cutoff},
                },
                {
                    "_id": 0,
                    "grn_id": 1,
                    "po_id": 1,
                    "created_at": 1,
                    "total_received": 1,
                    "total_accepted": 1,
                    "total_rejected": 1,
                    "accepted_at": 1,
                },
            ).limit(500)
        )

        if not grns:
            return empty

        total_received = 0
        total_accepted = 0
        on_time_count = 0
        grns_with_po_date = 0

        po_coll = db.get_collection("purchase_orders")

        for grn in grns:
            recv = int(grn.get("total_received") or 0)
            acc = int(grn.get("total_accepted") or 0)
            total_received += recv
            total_accepted += acc

            # Punctuality: compare GRN accepted_at vs PO expected_date
            po_id = grn.get("po_id")
            if po_id:
                try:
                    po = po_coll.find_one(
                        {"po_id": po_id}, {"_id": 0, "expected_date": 1}
                    )
                    if po and po.get("expected_date"):
                        expected = str(po["expected_date"])[:10]
                        accepted_at = str(
                            grn.get("accepted_at") or grn.get("created_at") or ""
                        )[:10]
                        if accepted_at and accepted_at <= expected:
                            on_time_count += 1
                        grns_with_po_date += 1
                except Exception:
                    pass

        n = len(grns)
        acceptance_rate = (
            round(total_accepted / total_received, 4) if total_received > 0 else None
        )
        on_time_rate = (
            round(on_time_count / grns_with_po_date, 4)
            if grns_with_po_date > 0
            else None
        )

        if acceptance_rate is not None and on_time_rate is not None:
            overall_score = round((acceptance_rate * 0.6 + on_time_rate * 0.4) * 100, 1)
        elif acceptance_rate is not None:
            overall_score = round(acceptance_rate * 100, 1)
        else:
            overall_score = None

        return {
            "vendor_id": vendor_id,
            "vendor_name": (
                (vendor.get("trade_name") or vendor.get("legal_name"))
                if vendor
                else None
            ),
            "window_months": months,
            "grns_evaluated": n,
            "total_received": total_received,
            "total_accepted": total_accepted,
            "total_rejected": total_received - total_accepted,
            "acceptance_rate": acceptance_rate,
            "on_time_rate": on_time_rate,
            "qc_pass_rate": qc_pass_rate,
            "qc_sample_size": qc_sample,
            "mtd_spend": mtd_spend,
            "on_time_grns": on_time_count,
            "grns_with_po_date": grns_with_po_date,
            "overall_score": overall_score,
            "score_label": (
                (
                    "Excellent"
                    if (overall_score or 0) >= 90
                    else (
                        "Good"
                        if (overall_score or 0) >= 75
                        else "Average" if (overall_score or 0) >= 50 else "Poor"
                    )
                )
                if overall_score is not None
                else None
            ),
            "insufficient_data": n < 3,
        }

    except Exception as exc:
        logger.warning("[INV-13] vendor_performance failed for %s: %s", vendor_id, exc)
        return empty


@router.get("/{vendor_id}/purchase-history")
async def vendor_purchase_history(
    vendor_id: str,
    months: int = Query(
        12,
        ge=1,
        le=36,
        description="Rolling-months window for the purchase-history report.",
    ),
    current_user: dict = Depends(get_current_user),
):
    """Return purchase-history analytics for one vendor (INV-13).

    Returns:
      - monthly breakdown of PO value, received units, and accepted units.
      - top-5 products ordered from this vendor by spend.
      - summary totals for the window.

    Honest empty state: no fabricated numbers; all fields are derived from real
    PO + GRN data or explicitly null.
    """
    db = _get_db()
    vendor_repo = get_vendor_repository()
    vendor = vendor_repo.find_by_id(vendor_id) if vendor_repo is not None else None

    empty = {
        "vendor_id": vendor_id,
        "vendor_name": (
            (vendor.get("trade_name") or vendor.get("legal_name")) if vendor else None
        ),
        "window_months": months,
        "total_pos": 0,
        "total_spend": 0.0,
        "total_units_received": 0,
        "monthly": [],
        "top_products": [],
    }
    if db is None:
        return empty

    try:
        from datetime import timedelta

        # Same frame-type bug as the scorecard above: purchase_orders /
        # grns.created_at are BSON datetimes (BaseRepository._add_timestamps),
        # so the old ISO-STRING $gte matched nothing and this whole vendor
        # spend chart rendered empty. Compare Date to Date.
        cutoff = datetime.now() - timedelta(days=months * 30)

        po_coll = db.get_collection("purchase_orders")
        grn_coll = db.get_collection("grns")

        pos = list(
            po_coll.find(
                {
                    "vendor_id": vendor_id,
                    "status": {"$nin": ["CANCELLED", "DRAFT"]},
                    "created_at": {"$gte": cutoff},
                },
                {
                    "_id": 0,
                    "po_id": 1,
                    "po_number": 1,
                    "created_at": 1,
                    "total_amount": 1,
                    "items": 1,
                    "status": 1,
                },
            )
            .sort("created_at", 1)
            .limit(500)
        )

        grns = list(
            grn_coll.find(
                {
                    "vendor_id": vendor_id,
                    "status": "ACCEPTED",
                    "created_at": {"$gte": cutoff},
                },
                {
                    "_id": 0,
                    "created_at": 1,
                    "total_received": 1,
                    "total_accepted": 1,
                    "po_id": 1,
                },
            ).limit(1000)
        )

        # Monthly breakdown keyed by YYYY-MM
        monthly: dict = {}
        product_spend: dict = {}

        for po in pos:
            # BUG-104, VALUE rule (+5:30): the month this spend is charted
            # under is a business month. A PO raised 1-Jun 02:00 IST used to
            # land in MAY's bar.
            month = ist_date_str(po.get("created_at"))[:7]
            if not month:
                continue
            bucket = monthly.setdefault(
                month, {"month": month, "pos": 0, "spend": 0.0, "units_received": 0}
            )
            bucket["pos"] += 1
            bucket["spend"] = round(
                bucket["spend"] + float(po.get("total_amount") or 0), 2
            )
            # Accumulate per-product spend
            for item in po.get("items") or []:
                pid = item.get("product_id", "")
                name = item.get("product_name", "") or item.get("name", "")
                sku = item.get("sku", "")
                spend = float(item.get("unit_price") or 0) * int(
                    item.get("quantity") or 0
                )
                if pid:
                    if pid not in product_spend:
                        product_spend[pid] = {
                            "product_id": pid,
                            "name": name,
                            "sku": sku,
                            "spend": 0.0,
                            "units": 0,
                        }
                    product_spend[pid]["spend"] = round(
                        product_spend[pid]["spend"] + spend, 2
                    )
                    product_spend[pid]["units"] += int(item.get("quantity") or 0)

        for grn in grns:
            # Same chart, same frame as the PO leg above (VALUE rule).
            month = ist_date_str(grn.get("created_at"))[:7]
            if not month:
                continue
            bucket = monthly.setdefault(
                month, {"month": month, "pos": 0, "spend": 0.0, "units_received": 0}
            )
            bucket["units_received"] += int(grn.get("total_received") or 0)

        monthly_list = sorted(monthly.values(), key=lambda x: x["month"])
        top_products = sorted(product_spend.values(), key=lambda x: -x["spend"])[:5]

        total_spend = sum(b["spend"] for b in monthly_list)
        total_units = sum(b["units_received"] for b in monthly_list)

        return {
            "vendor_id": vendor_id,
            "vendor_name": (
                (vendor.get("trade_name") or vendor.get("legal_name"))
                if vendor
                else None
            ),
            "window_months": months,
            "total_pos": len(pos),
            "total_spend": round(total_spend, 2),
            "total_units_received": total_units,
            "monthly": monthly_list,
            "top_products": top_products,
        }

    except Exception as exc:
        logger.warning(
            "[INV-13] vendor_purchase_history failed for %s: %s", vendor_id, exc
        )
        return empty


# ============================================================================
# FIN-11: TDS threshold status + 26Q/27EQ quarterly export
# ============================================================================


@router.get("/tds/threshold-status")
async def get_tds_threshold_status(
    vendor_id: str = Query(..., description="Vendor ID to check TDS threshold for"),
    section: str = Query(..., description="TDS section code e.g. 194C_OTHER"),
    current_payment: float = Query(
        ..., ge=0, description="Current payment amount (before TDS)"
    ),
    fy_start: Optional[str] = Query(
        None,
        description="Financial year start date (YYYY-MM-DD); defaults to current FY 1-Apr",
    ),
    current_user: dict = Depends(require_roles("ADMIN", "ACCOUNTANT")),
):
    """FIN-11: Check whether TDS applies on a vendor payment given cumulative
    spend to that vendor in the current financial year.

    Returns the threshold status including whether TDS must be deducted,
    the taxable base (amount above the threshold), and the computed TDS.
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    # Determine current FY start.
    # BUG-104: this carried its OWN `now.month >= 4` financial-year rule off
    # `datetime.utcnow()`. Railway runs UTC, so between 00:00 and 05:30 IST on
    # 1 April that rule still returns the PREVIOUS financial year -- and this
    # figure decides whether the s.194Q / 206C TDS threshold has been crossed,
    # so a whole extra year of payments got counted into the cumulative and TDS
    # was deducted (or the threshold declared crossed) on the wrong basis.
    # ist.fy_start_year_ist is the single definition of the April rule.
    fy_default = datetime(fy_start_year_ist(), 4, 1)
    if fy_start:
        from ..services.ap_engine import parse_date as _pd

        fy_start_dt = _pd(fy_start) or fy_default
    else:
        fy_start_dt = fy_default

    # Sum all payments to this vendor in the current FY.
    # `payment_date` is a DATE-ONLY ISO string ('2026-04-01'), so the bound has
    # to be date-only too: the old `fy_start_dt.isoformat()` produced
    # '2026-04-01T00:00:00', which sorts AFTER '2026-04-01' and silently
    # excluded every payment made on 1 April from the FY cumulative.
    try:
        past_payments = list(
            db.get_collection("vendor_payments").find(
                {
                    "vendor_id": vendor_id,
                    "payment_date": {"$gte": fy_start_dt.date().isoformat()},
                },
                {"amount": 1, "tds_amount": 1, "_id": 0},
            )
        )
    except Exception:
        past_payments = []

    cumulative_fy = sum(
        float(p.get("amount") or 0) + float(p.get("tds_amount") or 0)
        for p in past_payments
    )

    # Fetch admin-edited TDS rate overrides (fail-soft)
    overrides = None
    try:
        cfg = db.get_collection("tds_rate_config").find_one({}, {"_id": 0})
        if cfg:
            overrides = cfg.get("rates")
    except Exception:
        pass

    result = ap_engine.tds_threshold_status(
        section=section,
        cumulative_paid_fy=cumulative_fy,
        current_payment=current_payment,
        overrides=overrides,
    )
    result["vendor_id"] = vendor_id
    result["fy_start"] = fy_start_dt.date().isoformat()
    result["cumulative_fy_payments"] = round(cumulative_fy, 2)
    return result


@router.get("/tds/26q-export")
async def export_26q(
    fy: Optional[int] = Query(
        None,
        description="Financial year start (e.g. 2025 for FY 2025-26). Defaults to current FY.",
    ),
    quarter: Optional[int] = Query(
        None,
        ge=1,
        le=4,
        description="Quarter (1-4). If omitted, returns all quarters of the FY.",
    ),
    current_user: dict = Depends(require_roles("ADMIN", "ACCOUNTANT")),
):
    """FIN-11: Export TDS deduction data for quarterly 26Q (TDS on payments)
    and 27EQ (TCS under 206C) returns.

    Returns structured rows grouped by FY and quarter, plus a summary.
    Accountant uses this to file the quarterly returns with the Income Tax dept.
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    # BUG-104: same private `now.month >= 4` FY rule off the UTC box clock as
    # the threshold endpoint above. On 1 April 00:00-05:30 IST the accountant
    # opening the 26Q export got the PRIOR year's return. Use the one
    # definition. The bounds are date-only because `payment_date` is a
    # date-only ISO string -- '2026-04-01T00:00:00' sorts after '2026-04-01',
    # which dropped every 1-April payment out of Q1 of the return.
    target_fy = fy if fy else fy_start_year_ist()
    fy_start = datetime(target_fy, 4, 1)
    fy_end = datetime(target_fy + 1, 3, 31, 23, 59, 59)

    try:
        payments = list(
            db.get_collection("vendor_payments").find(
                {
                    "payment_date": {
                        "$gte": fy_start.date().isoformat(),
                        "$lte": fy_end.date().isoformat(),
                    },
                    "tds_amount": {"$gt": 0},
                },
                {"_id": 0},
            )
        )
    except Exception:
        payments = []

    # Enrich with vendor name/PAN from the vendors collection (best-effort).
    try:
        vendor_ids = list({p.get("vendor_id") for p in payments if p.get("vendor_id")})
        vendor_docs = list(
            db.get_collection("vendors").find(
                {"vendor_id": {"$in": vendor_ids}},
                {"vendor_id": 1, "name": 1, "pan": 1, "_id": 0},
            )
        )
        vendor_map = {v["vendor_id"]: v for v in vendor_docs}
        for p in payments:
            vdoc = vendor_map.get(p.get("vendor_id"), {})
            p.setdefault("vendor_name", vdoc.get("name", ""))
            p.setdefault("vendor_pan", vdoc.get("pan", ""))
    except Exception:
        pass

    export = ap_engine.build_26q_export(payments)

    if quarter:
        fy_key = f"{target_fy}-{(target_fy + 1) % 100:02d}"
        q_key = f"Q{quarter}"
        return {
            "fy": fy_key,
            "quarter": q_key,
            "form_26q": export["form_26q"].get(fy_key, {}).get(q_key, []),
            "form_27eq": export["form_27eq"].get(fy_key, {}).get(q_key, []),
            "summary": export["summary"],
        }
    return export


# ============================================================================
# F8 - PO vs GRN VARIANCE / BACKORDER
# ----------------------------------------------------------------------------
# Read-mostly accountability surface: every open/partial PO line whose received
# (ACCEPTED) qty trails the ordered qty is surfaced with its open qty, days
# overdue, and an explicit aging enum. An ADMIN/ACCOUNTANT can dismiss a line
# with a mandatory justification (single-doc $push on the PO + one audit row).
# NO order/POS/payment/AP mutation happens here -- the dismiss only annotates
# PO metadata; the debit-note hint is a prompt the operator may ignore.
# ============================================================================

# Roles that may see the variance report (read). Adds STORE_MANAGER to the AP
# pair so a store can chase its own late deliveries; SUPERADMIN auto-passes.
_VARIANCE_READ_ROLES = ("ADMIN", "ACCOUNTANT", "STORE_MANAGER", "AREA_MANAGER")


def _resolve_booked_bills(lines: list) -> None:
    """F8 P3: stamp booked_bill_id on each variance-report row, in place.

    The dismiss endpoint only suggests a debit note when BOTH grn_id and
    bill_id are supplied -- but report rows never carried them, so the prompt
    was unreachable from the UI. The engine now stamps latest_accepted_grn_id
    (pure, from the GRNs it already reads); this helper resolves the booked
    invoice side: the newest vendor_bill linked to the row's PO whose lines
    contain the row's product. Read-only + strictly fail-soft -- no DB / any
    error leaves booked_bill_id None and the report still serves.
    """
    if not lines:
        return
    for ln in lines:
        ln.setdefault("booked_bill_id", None)
    db = None
    try:
        db = _get_db()
    except Exception:  # noqa: BLE001
        db = None
    if db is None:
        return
    po_ids = sorted({ln.get("po_id") for ln in lines if ln.get("po_id")})
    if not po_ids:
        return
    try:
        bills = list(
            db.get_collection("vendor_bills").find(
                {"po_id": {"$in": po_ids}},
                {
                    "_id": 0,
                    "bill_id": 1,
                    "po_id": 1,
                    "lines": 1,
                    "invoice_date": 1,
                    "created_at": 1,
                },
            )
        )
    except Exception:  # noqa: BLE001
        return
    # Newest first (string-coerced so mixed str/datetime stamps can't raise),
    # so the FIRST bill matching a (po, product) is the latest booking.
    bills.sort(
        key=lambda b: (
            str(b.get("invoice_date") or ""),
            str(b.get("created_at") or ""),
        ),
        reverse=True,
    )
    bills_by_po: dict = {}
    for b in bills:
        bills_by_po.setdefault(b.get("po_id"), []).append(b)
    for ln in lines:
        pid = ln.get("product_id")
        for b in bills_by_po.get(ln.get("po_id"), []):
            if any(
                isinstance(bl, dict) and bl.get("product_id") == pid
                for bl in (b.get("lines") or [])
            ):
                ln["booked_bill_id"] = b.get("bill_id")
                break


class DismissVarianceRequest(BaseModel):
    product_id: str
    # Mandatory justification, >= 10 chars: a dismiss is an accountable decision
    # (the line is no longer chased), so it must carry a real reason.
    reason: str = Field(..., min_length=10)
    # Optional links: when BOTH are present and the booked invoice over-bills the
    # accepted qty, the response prompts a debit note (prompt only -- no AP write).
    grn_id: Optional[str] = None
    bill_id: Optional[str] = None


@router.get("/variance-report")
async def po_grn_variance_report(
    store_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_roles(*_VARIANCE_READ_ROLES)),
):
    """PO-ordered vs GRN-received variance + backorder report.

    For every open PO (SENT / ACKNOWLEDGED / PARTIALLY_RECEIVED), compares the
    ordered qty per product against the cumulative ACCEPTED qty across its GRNs
    and returns the per-line variance (open_qty, received/accepted/rejected,
    SHORT/OVER/EXACT/UNMATCHED, days_overdue, aging_status enum). Fully-satisfied
    lines (open_qty 0 + EXACT) are omitted; dismissed lines are hidden.

    Fail-soft: no DB -> empty list. Pure variance math lives in
    po_variance_engine so this handler only fetches + filters.
    """
    from ..services import po_variance_engine

    po_repo = get_purchase_order_repository()
    grn_repo = get_grn_repository()
    if po_repo is None:
        return {"lines": [], "total": 0}

    active_store = validate_store_access(store_id, current_user)

    try:
        # limit=0 -> ALL open POs; the variance total + backorders must not be
        # silently capped at the default 100 (a chain can have >100 open POs).
        pos = po_repo.find_pending(limit=0) or []
    except Exception:  # noqa: BLE001
        pos = []

    if active_store:
        pos = [p for p in pos if p.get("delivery_store_id") == active_store]

    # Fetch each PO's GRNs (by po_id). Fail-soft per PO.
    grns_by_po: dict = {}
    for po in pos:
        pid = po.get("po_id")
        if not pid:
            continue
        try:
            grns_by_po[pid] = grn_repo.find_by_po(pid) if grn_repo is not None else []
        except Exception:  # noqa: BLE001
            grns_by_po[pid] = []

    lines = po_variance_engine.variance_report_lines(pos, grns_by_po)
    total = len(lines)
    page = lines[skip : skip + limit]
    # F8 P3: resolve booked_bill_id per (po, product) for the served page only
    # (one $in query), so the Dismiss flow can pass grn_id + bill_id and the
    # debit-note prompt is reachable. Fail-soft -- never blocks the report.
    _resolve_booked_bills(page)
    return {"lines": page, "total": total}


@router.post("/purchase-orders/{po_id}/dismiss-variance")
async def dismiss_po_variance(
    po_id: str,
    body: DismissVarianceRequest,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Dismiss a PO-vs-GRN variance/backorder line with a justification.

    Records the dismissal on the PO (single find_one_and_update $push to
    dismissed_variances[] -- ONE document, ONE collection; no cross-collection
    atomic write, per CORRECTIONS P0-1) plus one immutable audit row
    (AuditRepository.create). When grn_id + bill_id are both supplied and the
    booked invoice over-bills the accepted qty for the product, the response
    PROMPTS a debit note (suggested amount = over-billed qty * invoice price) --
    a hint only; it never creates a debit note or touches AP.

    Does NOT change PO status, GRN stock, or payable. The dismissed task is
    handled by the caller (status DISMISSED, not COMPLETED, so SLA logic does
    not treat it as genuine resolution).
    """
    po_repo = get_purchase_order_repository()
    if po_repo is None:
        raise HTTPException(status_code=503, detail="Purchase orders unavailable")

    po = po_repo.find_by_id(po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    reason = (body.reason or "").strip()
    if len(reason) < 10:
        raise HTTPException(
            status_code=400,
            detail="A dismissal reason of at least 10 characters is required.",
        )

    # Debit-note suggestion: only when BOTH a GRN and a booked invoice are linked
    # AND the invoice over-bills the accepted qty for this product. Fail-soft.
    debit_note_suggested = False
    suggested_amount: Optional[float] = None
    if body.grn_id and body.bill_id:
        try:
            db = _get_db()
            grn = None
            grn_repo = get_grn_repository()
            if grn_repo is not None:
                grn = grn_repo.find_by_id(body.grn_id)
            bill = None
            if db is not None:
                bill = db.get_collection("vendor_bills").find_one(
                    {"bill_id": body.bill_id}, {"_id": 0}
                )
            accepted_qty = 0
            for it in (grn or {}).get("items", []) or []:
                if isinstance(it, dict) and it.get("product_id") == body.product_id:
                    try:
                        accepted_qty += int(it.get("accepted_qty", 0) or 0)
                    except (TypeError, ValueError):
                        continue
            invoiced_qty = 0.0
            invoice_unit_price = 0.0
            for ln in (bill or {}).get("lines", []) or []:
                if isinstance(ln, dict) and ln.get("product_id") == body.product_id:
                    try:
                        q = float(ln.get("qty", 0) or 0)
                    except (TypeError, ValueError):
                        q = 0.0
                    invoiced_qty += q
                    try:
                        up = float(ln.get("unit_price", 0) or 0)
                    except (TypeError, ValueError):
                        up = 0.0
                    if up <= 0 and q:
                        try:
                            taxable = float(ln.get("taxable", 0) or 0)
                        except (TypeError, ValueError):
                            taxable = 0.0
                        up = taxable / q if q else 0.0
                    if up > invoice_unit_price:
                        invoice_unit_price = up
            over_billed = invoiced_qty - accepted_qty
            if over_billed > 0 and invoice_unit_price > 0:
                debit_note_suggested = True
                suggested_amount = round(over_billed * invoice_unit_price, 2)
        except Exception:  # noqa: BLE001
            debit_note_suggested = False
            suggested_amount = None

    now_iso = datetime.now().isoformat()
    entry = {
        "product_id": body.product_id,
        "reason": reason,
        "dismissed_by": current_user.get("user_id"),
        "dismissed_at": now_iso,
        "grn_id": body.grn_id,
        "bill_id": body.bill_id,
        "debit_note_suggested": debit_note_suggested,
        "suggested_amount": suggested_amount,
    }

    # Single-document, single-collection mutation (CORRECTIONS P0-1).
    try:
        po_repo.collection.find_one_and_update(
            {"po_id": po_id},
            {
                "$push": {"dismissed_variances": entry},
                "$set": {"updated_at": now_iso},
            },
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail="Failed to record variance dismissal"
        ) from exc

    # One immutable audit row (AuditRepository.create -- NOT a dual balance
    # write). Fail-soft: a missing audit must not undo the dismissal.
    try:
        audit_repo = get_audit_repository()
        if audit_repo is not None:
            audit_repo.create(
                {
                    "action": "po_variance_dismiss",
                    "entity_type": "purchase_order",
                    "entity_id": po_id,
                    "target": po_id,
                    "user_id": current_user.get("user_id"),
                    "store_id": po.get("delivery_store_id"),
                    "before": {
                        "product_id": body.product_id,
                        "po_status": po.get("status"),
                    },
                    "after": {
                        "reason": reason,
                        "dismissed_by": current_user.get("user_id"),
                        "debit_note_suggested": debit_note_suggested,
                    },
                    "timestamp": datetime.now(),
                }
            )
    except Exception:  # noqa: BLE001
        pass

    return {
        "dismissed": True,
        "po_id": po_id,
        "product_id": body.product_id,
        "vendor_id": po.get("vendor_id"),
        "debit_note_suggested": debit_note_suggested,
        "suggested_amount": suggested_amount,
    }


# ============================================================================
# Catch-all parametric routes — registered LAST so they do not shadow
# specific paths above (`/purchase-orders`, `/grn`, `/ap-aging`, etc.).
# FastAPI resolves routes in registration order.
# ============================================================================
router.add_api_route("/{vendor_id}", get_vendor, methods=["GET"])
