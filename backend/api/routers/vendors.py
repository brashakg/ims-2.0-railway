"""
IMS 2.0 - Vendors Router
=========================
Real database queries for vendor and purchase order management
"""

import logging
import re

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Optional
from datetime import datetime
import uuid
from .auth import get_current_user, require_roles
from ..dependencies import (
    get_vendor_repository,
    get_purchase_order_repository,
    get_grn_repository,
    get_stock_repository,
    get_vendor_portal_token_repository,
    get_audit_repository,
    validate_store_access,
)
from ..services import ap_engine

router = APIRouter()
logger = logging.getLogger(__name__)

# Roles permitted to mutate vendors, purchase orders and goods-receipt notes.
# Mirrors the frontend /purchase/* route guards. SUPERADMIN auto-passes.
_VENDOR_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT")

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


class POItemCreate(BaseModel):
    product_id: str
    product_name: str
    sku: str
    # A PO line must order at least one unit at a non-negative price. Without
    # these bounds a negative quantity / price would persist a corrupt PO and
    # poison the subtotal/GST math (subtotal = sum(quantity * unit_price)).
    quantity: int = Field(..., ge=1)
    unit_price: float = Field(..., ge=0)


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
    po_item_id: str
    product_id: str
    # Receipt quantities are counts of physical units -- never negative. A
    # negative received/accepted/rejected qty would mint a negative stock
    # movement and corrupt the PO receipt-state + accepted-qty rollups.
    received_qty: int = Field(..., ge=0)
    accepted_qty: int = Field(..., ge=0)
    rejected_qty: int = Field(0, ge=0)
    rejection_reason: Optional[str] = None
    # Receiving location for the minted serialized units (optional; falls back
    # to "DEFAULT" on the stock unit). Lets the receiver bin goods at post time.
    location_code: Optional[str] = None

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


class GRNCreate(BaseModel):
    po_id: str
    vendor_invoice_no: str
    vendor_invoice_date: str
    # A GRN with zero items is meaningless and would mark a PO as having
    # been received without actually recording any goods.
    items: List[GRNItemCreate] = Field(..., min_length=1)
    notes: Optional[str] = None


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def generate_po_number(store_id: str) -> str:
    """Generate unique PO number"""
    prefix = store_id[:3].upper() if store_id else "HQ"
    timestamp = datetime.now().strftime("%y%m%d%H%M")
    return f"PO-{prefix}-{timestamp}"


def generate_grn_number(store_id: str) -> str:
    """Generate unique GRN number"""
    prefix = store_id[:3].upper() if store_id else "HQ"
    timestamp = datetime.now().strftime("%y%m%d%H%M")
    return f"GRN-{prefix}-{timestamp}"


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
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

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

    po_id = str(uuid.uuid4())
    po_number = generate_po_number(po.delivery_store_id)

    # Validate vendor exists
    if vendor_repo is not None:
        vendor = vendor_repo.find_by_id(po.vendor_id)
        if vendor is None:
            raise HTTPException(status_code=404, detail="Vendor not found")

    # Calculate totals
    subtotal = sum(item.quantity * item.unit_price for item in po.items)
    tax = subtotal * 0.18  # Assuming 18% GST
    total = subtotal + tax

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
                "items": [item.model_dump() for item in po.items],
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


@router.get("/purchase-orders/{po_id}")
async def get_po(po_id: str, current_user: dict = Depends(get_current_user)):
    """Get purchase order details"""
    po_repo = get_purchase_order_repository()

    if po_repo is None:
        return {"po_id": po_id}

    po = po_repo.find_by_id(po_id)
    if not po:
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

        if po.get("status") != "DRAFT":
            raise HTTPException(status_code=400, detail="Only draft POs can be sent")

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
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """List GRNs with filters"""
    grn_repo = get_grn_repository()
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

    if grn_repo is None:
        return {"grns": [], "total": 0}

    filter_dict = {}
    if active_store:
        filter_dict["store_id"] = active_store
    if status:
        filter_dict["status"] = status
    if po_id:
        filter_dict["po_id"] = po_id

    grns = grn_repo.find_many(filter_dict, skip=skip, limit=limit)

    return {"grns": grns or [], "total": len(grns) if grns else 0}


@router.post("/grn", status_code=201)
async def create_grn(
    grn: GRNCreate, current_user: dict = Depends(require_roles(*_VENDOR_ROLES))
):
    """Create a new GRN"""
    grn_repo = get_grn_repository()
    po_repo = get_purchase_order_repository()

    grn_id = str(uuid.uuid4())
    grn_number = generate_grn_number(current_user.get("active_store_id"))

    # Validate PO exists
    po = None
    if po_repo is not None:
        po = po_repo.find_by_id(grn.po_id)
        if not po:
            raise HTTPException(status_code=404, detail="Purchase order not found")

        if po.get("status") not in _RECEIVABLE_PO_STATUSES:
            raise HTTPException(
                status_code=400, detail="PO is not in receivable status"
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
        "vendor_id": po.get("vendor_id") if po else None,
        "vendor_name": po.get("vendor_name") if po else None,
        "store_id": current_user.get("active_store_id"),
        "vendor_invoice_no": grn.vendor_invoice_no,
        "vendor_invoice_date": grn.vendor_invoice_date,
        "items": item_docs,
        "total_received": total_received,
        "total_accepted": total_accepted,
        "total_rejected": total_rejected,
        "total_ordered": total_ordered,
        "notes": grn.notes,
        "status": "PENDING",
        "created_by": current_user.get("user_id"),
        "created_at": datetime.now().isoformat(),
    }

    if grn_repo is not None:
        grn_repo.create(grn_doc)

    # Anti-fraud / variance: a receiving discrepancy (rejected goods or a
    # short/over shipment vs the PO) raises an accountable SYSTEM task so it is
    # investigated rather than silently absorbed. Fail-soft -- a task failure
    # must never break the GRN save.
    if grn_has_discrepancy(grn_doc):
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
        "total_received": total_received,
        "has_discrepancy": grn_has_discrepancy(grn_doc),
        "message": "GRN created",
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

    return grn


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

    Idempotent: a re-POST is guarded by the PENDING status check, and -- belt
    and suspenders -- the minting loop skips any (grn_id, product_id) that
    already has units in stock_units, so a partially-failed accept can be safely
    retried without double-counting.

    Fail-soft ordering: the stock write happens first; the GRN status flip, the
    PO state update, and the per-unit stock_audit rows all follow and are each
    wrapped so that a logging/secondary failure can never lose the stock that
    was already received.
    """
    grn_repo = get_grn_repository()
    stock_repo = get_stock_repository()
    po_repo = get_purchase_order_repository()

    if grn_repo is None:
        return {"message": "GRN accepted, stock added"}

    grn = grn_repo.find_by_id(grn_id)
    if not grn:
        raise HTTPException(status_code=404, detail="GRN not found")

    if grn.get("status") != "PENDING":
        raise HTTPException(status_code=400, detail="GRN is not pending")

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

    if stock_repo is not None:
        for item in grn.get("items", []) or []:
            try:
                accepted_qty = int(item.get("accepted_qty", 0) or 0)
            except (TypeError, ValueError):
                accepted_qty = 0
            product_id = item.get("product_id")
            if accepted_qty <= 0 or not product_id:
                continue

            # Idempotency guard: if this GRN already minted units for this
            # product (a previous accept that failed mid-way), don't mint again.
            try:
                already = stock_repo.count(
                    {
                        "source_type": "GRN",
                        "source_id": grn_id,
                        "product_id": product_id,
                    }
                )
            except Exception:  # noqa: BLE001
                already = 0
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
            for _ in range(to_mint):
                created = stock_repo.create(
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
                        "grn_number": grn_number,
                        "po_id": po_id,
                        "created_by": user_id,
                        **cost_fields,
                    }
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

    # Mark the GRN accepted.
    grn_repo.update(
        grn_id,
        {
            "status": "ACCEPTED",
            "accepted_at": datetime.now().isoformat(),
            "accepted_by": user_id,
            "units_added": units_added,
        },
    )

    # Advance the PO received state. Sum the accepted qty across EVERY accepted
    # GRN for this PO (this one is now ACCEPTED) and compare against the ordered
    # lines: full receipt -> RECEIVED, otherwise PARTIALLY_RECEIVED. Fail-soft.
    po_status = None
    if po_repo is not None and po_id:
        try:
            po = po_repo.find_by_id(po_id)
            received_by_product = _cumulative_received_by_product(grn_repo, po_id)
            po_status = compute_po_receipt_state(
                po.get("items") if po else [], received_by_product
            )
            po_repo.update(
                po_id,
                {
                    "status": po_status,
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
        "message": "GRN accepted, stock added",
        "grn_id": grn_id,
        "units_added": units_added,
        "stock_ids": minted_stock_ids,
        "po_status": po_status,
        "items_added": len(
            [
                i
                for i in (grn.get("items", []) or [])
                if (i.get("accepted_qty", 0) or 0) > 0
            ]
        ),
    }


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


class DebitNoteCreate(BaseModel):
    amount: float = Field(..., gt=0)
    date: str  # ISO date
    reason: str
    bill_id: Optional[str] = None  # allocate to a bill; else on-account
    grn_id: Optional[str] = None  # link to the rejected-goods GRN, if any


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

    empty = {
        "vendor_id": vendor_id,
        "vendor_name": (vendor.get("trade_name") or vendor.get("legal_name")) if vendor else None,
        "window_months": months,
        "grns_evaluated": 0,
        "acceptance_rate": None,
        "on_time_rate": None,
        "overall_score": None,
        "insufficient_data": True,
        "note": "No GRN data found for this vendor in the selected window.",
    }
    if db is None:
        return empty

    try:
        from datetime import timedelta

        cutoff = datetime.now() - timedelta(days=months * 30)

        grn_coll = db.get_collection("grns")
        grns = list(
            grn_coll.find(
                {
                    "vendor_id": vendor_id,
                    "status": "ACCEPTED",
                    "created_at": {"$gte": cutoff.isoformat()},
                },
                {"_id": 0, "grn_id": 1, "po_id": 1, "created_at": 1,
                 "total_received": 1, "total_accepted": 1, "total_rejected": 1,
                 "accepted_at": 1},
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
                    po = po_coll.find_one({"po_id": po_id}, {"_id": 0, "expected_date": 1})
                    if po and po.get("expected_date"):
                        expected = str(po["expected_date"])[:10]
                        accepted_at = str(grn.get("accepted_at") or grn.get("created_at") or "")[:10]
                        if accepted_at and accepted_at <= expected:
                            on_time_count += 1
                        grns_with_po_date += 1
                except Exception:
                    pass

        n = len(grns)
        acceptance_rate = round(total_accepted / total_received, 4) if total_received > 0 else None
        on_time_rate = round(on_time_count / grns_with_po_date, 4) if grns_with_po_date > 0 else None

        if acceptance_rate is not None and on_time_rate is not None:
            overall_score = round((acceptance_rate * 0.6 + on_time_rate * 0.4) * 100, 1)
        elif acceptance_rate is not None:
            overall_score = round(acceptance_rate * 100, 1)
        else:
            overall_score = None

        return {
            "vendor_id": vendor_id,
            "vendor_name": (vendor.get("trade_name") or vendor.get("legal_name")) if vendor else None,
            "window_months": months,
            "grns_evaluated": n,
            "total_received": total_received,
            "total_accepted": total_accepted,
            "total_rejected": total_received - total_accepted,
            "acceptance_rate": acceptance_rate,
            "on_time_rate": on_time_rate,
            "on_time_grns": on_time_count,
            "grns_with_po_date": grns_with_po_date,
            "overall_score": overall_score,
            "score_label": (
                "Excellent" if (overall_score or 0) >= 90
                else "Good" if (overall_score or 0) >= 75
                else "Average" if (overall_score or 0) >= 50
                else "Poor"
            ) if overall_score is not None else None,
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
        "vendor_name": (vendor.get("trade_name") or vendor.get("legal_name")) if vendor else None,
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

        cutoff = datetime.now() - timedelta(days=months * 30)

        po_coll = db.get_collection("purchase_orders")
        grn_coll = db.get_collection("grns")

        pos = list(
            po_coll.find(
                {
                    "vendor_id": vendor_id,
                    "status": {"$nin": ["CANCELLED", "DRAFT"]},
                    "created_at": {"$gte": cutoff.isoformat()},
                },
                {"_id": 0, "po_id": 1, "po_number": 1, "created_at": 1,
                 "total_amount": 1, "items": 1, "status": 1},
            ).sort("created_at", 1).limit(500)
        )

        grns = list(
            grn_coll.find(
                {
                    "vendor_id": vendor_id,
                    "status": "ACCEPTED",
                    "created_at": {"$gte": cutoff.isoformat()},
                },
                {"_id": 0, "created_at": 1, "total_received": 1, "total_accepted": 1,
                 "po_id": 1},
            ).limit(1000)
        )

        # Monthly breakdown keyed by YYYY-MM
        monthly: dict = {}
        product_spend: dict = {}

        for po in pos:
            month = str(po.get("created_at") or "")[:7]
            if not month:
                continue
            bucket = monthly.setdefault(month, {"month": month, "pos": 0, "spend": 0.0, "units_received": 0})
            bucket["pos"] += 1
            bucket["spend"] = round(bucket["spend"] + float(po.get("total_amount") or 0), 2)
            # Accumulate per-product spend
            for item in po.get("items") or []:
                pid = item.get("product_id", "")
                name = item.get("product_name", "") or item.get("name", "")
                sku = item.get("sku", "")
                spend = float(item.get("unit_price") or 0) * int(item.get("quantity") or 0)
                if pid:
                    if pid not in product_spend:
                        product_spend[pid] = {"product_id": pid, "name": name, "sku": sku, "spend": 0.0, "units": 0}
                    product_spend[pid]["spend"] = round(product_spend[pid]["spend"] + spend, 2)
                    product_spend[pid]["units"] += int(item.get("quantity") or 0)

        for grn in grns:
            month = str(grn.get("created_at") or "")[:7]
            if not month:
                continue
            bucket = monthly.setdefault(month, {"month": month, "pos": 0, "spend": 0.0, "units_received": 0})
            bucket["units_received"] += int(grn.get("total_received") or 0)

        monthly_list = sorted(monthly.values(), key=lambda x: x["month"])
        top_products = sorted(product_spend.values(), key=lambda x: -x["spend"])[:5]

        total_spend = sum(b["spend"] for b in monthly_list)
        total_units = sum(b["units_received"] for b in monthly_list)

        return {
            "vendor_id": vendor_id,
            "vendor_name": (vendor.get("trade_name") or vendor.get("legal_name")) if vendor else None,
            "window_months": months,
            "total_pos": len(pos),
            "total_spend": round(total_spend, 2),
            "total_units_received": total_units,
            "monthly": monthly_list,
            "top_products": top_products,
        }

    except Exception as exc:
        logger.warning("[INV-13] vendor_purchase_history failed for %s: %s", vendor_id, exc)
        return empty


# ============================================================================
# FIN-11: TDS threshold status + 26Q/27EQ quarterly export
# ============================================================================


@router.get("/tds/threshold-status")
async def get_tds_threshold_status(
    vendor_id: str = Query(..., description="Vendor ID to check TDS threshold for"),
    section: str = Query(..., description="TDS section code e.g. 194C_OTHER"),
    current_payment: float = Query(..., ge=0, description="Current payment amount (before TDS)"),
    fy_start: Optional[str] = Query(None, description="Financial year start date (YYYY-MM-DD); defaults to current FY 1-Apr"),
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

    # Determine current FY start
    now = datetime.utcnow()
    if fy_start:
        from ..services.ap_engine import parse_date as _pd
        fy_start_dt = _pd(fy_start) or datetime(now.year if now.month >= 4 else now.year - 1, 4, 1)
    else:
        fy_start_dt = datetime(now.year if now.month >= 4 else now.year - 1, 4, 1)

    # Sum all payments to this vendor in the current FY
    try:
        past_payments = list(db.get_collection("vendor_payments").find(
            {
                "vendor_id": vendor_id,
                "payment_date": {"$gte": fy_start_dt.isoformat()},
            },
            {"amount": 1, "tds_amount": 1, "_id": 0},
        ))
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
    fy: Optional[int] = Query(None, description="Financial year start (e.g. 2025 for FY 2025-26). Defaults to current FY."),
    quarter: Optional[int] = Query(None, ge=1, le=4, description="Quarter (1-4). If omitted, returns all quarters of the FY."),
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

    now = datetime.utcnow()
    target_fy = fy if fy else (now.year if now.month >= 4 else now.year - 1)
    fy_start = datetime(target_fy, 4, 1)
    fy_end = datetime(target_fy + 1, 3, 31, 23, 59, 59)

    try:
        payments = list(db.get_collection("vendor_payments").find(
            {
                "payment_date": {
                    "$gte": fy_start.isoformat(),
                    "$lte": fy_end.isoformat(),
                },
                "tds_amount": {"$gt": 0},
            },
            {"_id": 0},
        ))
    except Exception:
        payments = []

    # Enrich with vendor name/PAN from the vendors collection (best-effort).
    try:
        vendor_ids = list({p.get("vendor_id") for p in payments if p.get("vendor_id")})
        vendor_docs = list(db.get_collection("vendors").find(
            {"vendor_id": {"$in": vendor_ids}},
            {"vendor_id": 1, "name": 1, "pan": 1, "_id": 0},
        ))
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
# Catch-all parametric routes — registered LAST so they do not shadow
# specific paths above (`/purchase-orders`, `/grn`, `/ap-aging`, etc.).
# FastAPI resolves routes in registration order.
# ============================================================================
router.add_api_route("/{vendor_id}", get_vendor, methods=["GET"])
