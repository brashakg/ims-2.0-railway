"""
IMS 2.0 - Orders Router
========================
Sales order management endpoints
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
from datetime import datetime, date, timedelta
from enum import Enum
import uuid

import logging

logger = logging.getLogger(__name__)

from .auth import get_current_user
from ..dependencies import (
    get_order_repository,
    get_customer_repository,
    get_stock_repository,
    get_product_repository,
    get_walkin_counter_repository,
)

# Discount cap by product discount_category (mirrors billing.py caps)
CATEGORY_DISCOUNT_CAPS = {
    "LUXURY": 2.0,
    "PREMIUM": 5.0,
    "MASS": 10.0,
    "NON_DISCOUNTABLE": 0.0,
}

# Per-category GST is sourced from the canonical table in
# api/services/gst_rates.py (single source of truth, shared with the product
# master in products.py so a product's master rate == what POS bills it).
# Indian GST 2.0 (effective 22 Sep 2025): 5% for frames / spectacle &
# contact lenses / corrective spectacles, 18% otherwise. That table is the
# backend mirror of the frontend's getGSTRateByCategory.
from ..services.gst_rates import gst_rate_for_category as _gst_rate_for_category

# resolve_gst_rate layers the SUPERADMIN-editable HSN->GST master over the
# static canonical table, so a govt rate change is an in-app edit (Settings ->
# HSN & GST Rates) with no code change. Fail-soft: falls back to the static
# table when the master/DB is unavailable.
from ..services.gst_rates import resolve_gst_rate

# LOW_GST_CATEGORIES retained for any external reference / readability; it is
# the set of categories the canonical table bills at 5%.
from ..services.gst_rates import GST_CATEGORY_TABLE as _GST_CATEGORY_TABLE

LOW_GST_CATEGORIES = {
    cat for cat, (_hsn, rate) in _GST_CATEGORY_TABLE.items() if rate == 5.0
}


def _compute_per_category_gst(items: list, cart_discount_pct: float) -> dict:
    """Per-category GST aggregation. Mirrors the frontend's getGrandTotal
    so cart total = sum of taxable + sum of tax across rates.

    Each item dict must carry `item_total` (line subtotal AFTER per-item
    discount) and `category` (or `item_type` as fallback). Stamps
    `gst_rate`, `taxable_value`, `tax_amount` onto each item in place
    for line-by-line invoice math.

    Returns a dict with:
      subtotal              — sum of item_total before cart discount
      taxable               — sum of taxable across rates AFTER cart discount
      tax                   — sum of tax across rates
      dominant_rate         — highest-revenue rate (legacy `tax_rate` field)
      cart_discount_amount  — subtotal − taxable when cart_discount_pct > 0
      total_discount        — cart_discount_amount + Σ item.discount_amount
                              (used by Pune Module iii payout aggregation)
    """
    cart_discount_pct = max(0.0, min(100.0, cart_discount_pct or 0.0))
    cart_factor = 1.0 - (cart_discount_pct / 100.0)
    subtotal = 0.0
    item_discount_sum = 0.0
    per_rate_taxable: dict = {}
    per_rate_tax: dict = {}
    for it in items or []:
        line_subtotal = float(it.get("item_total") or 0.0)
        subtotal += line_subtotal
        item_discount_sum += float(it.get("discount_amount") or 0.0)
        cat = it.get("category") or it.get("item_type") or ""
        hsn = it.get("hsn_code") or it.get("hsn") or None
        rate = resolve_gst_rate(hsn_code=hsn, category=cat)
        line_taxable = round(line_subtotal * cart_factor, 2)
        line_tax = round(line_taxable * (rate / 100.0), 2)
        per_rate_taxable[rate] = round(
            per_rate_taxable.get(rate, 0.0) + line_taxable, 2
        )
        per_rate_tax[rate] = round(per_rate_tax.get(rate, 0.0) + line_tax, 2)
        it["gst_rate"] = rate
        it["taxable_value"] = line_taxable
        it["tax_amount"] = line_tax
    taxable = round(sum(per_rate_taxable.values()), 2)
    tax = round(sum(per_rate_tax.values()), 2)
    cart_discount_amount = (
        round(subtotal - taxable, 2) if cart_discount_pct > 0 else 0.0
    )
    dominant_rate = (
        max(per_rate_taxable, key=per_rate_taxable.get) if per_rate_taxable else 18.0
    )
    total_discount = round(item_discount_sum + cart_discount_amount, 2)
    return {
        "subtotal": round(subtotal, 2),
        "taxable": taxable,
        "tax": tax,
        "dominant_rate": dominant_rate,
        "cart_discount_amount": cart_discount_amount,
        "total_discount": total_discount,
    }


router = APIRouter()


# ============================================================================
# HELPER: Convert snake_case to camelCase for frontend compatibility
# ============================================================================


def to_camel_case(snake_str: str) -> str:
    """Convert snake_case to camelCase"""
    components = snake_str.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


def order_to_frontend(order: dict) -> dict:
    """Convert order dict from snake_case to camelCase for frontend"""
    if order is None:
        return order

    # Map of snake_case keys to camelCase
    key_map = {
        "order_id": "id",
        "order_number": "orderNumber",
        "store_id": "storeId",
        "customer_id": "customerId",
        "customer_name": "customerName",
        "customer_phone": "customerPhone",
        "patient_id": "patientId",
        "patient_name": "patientName",
        "salesperson_id": "salespersonId",
        "grand_total": "grandTotal",
        "tax_amount": "taxAmount",
        "tax_rate": "taxRate",
        "amount_paid": "amountPaid",
        "balance_due": "balanceDue",
        "payment_status": "paymentStatus",
        "total_discount": "totalDiscount",
        "expected_delivery": "expectedDelivery",
        "created_at": "createdAt",
        "updated_at": "updatedAt",
        "delivered_at": "deliveredAt",
        "cancelled_at": "cancelledAt",
        "cancelled_by": "cancelledBy",
        "cancellation_reason": "cancellationReason",
        "invoice_number": "invoiceNumber",
        "invoice_date": "invoiceDate",
        "created_by": "createdBy",
    }

    # Status field needs special handling - backend uses 'status', frontend uses 'orderStatus'
    result = {}
    for key, value in order.items():
        # Drop MongoDB's auto-generated BSON ObjectId — it's not JSON-serialisable
        # via Pydantic/FastAPI's default encoder and the TechCherry import
        # leaves these in place because insert_one() auto-mints them. Orders
        # have their own `order_id` / `order_number` for client-side keys, so
        # `_id` is never needed in the API response.
        # This was the root cause of "GET /api/v1/orders?store_id=BV-PUN-01"
        # 500ing after the May 2026 TechCherry migration:
        #   ValueError: [TypeError("'ObjectId' object is not iterable")...]
        if key == "_id":
            continue
        if key == "status":
            result["orderStatus"] = value
        elif key == "items" and isinstance(value, list):
            # Convert item fields
            result["items"] = [item_to_frontend(item) for item in value]
        elif key == "payments" and isinstance(value, list):
            # Convert payment fields
            result["payments"] = [payment_to_frontend(p) for p in value]
        elif key == "status_history" and isinstance(value, list):
            # Convert status_history fields (timestamp -> timestamp, changed_by -> changedBy)
            result["statusHistory"] = []
            for entry in value:
                history_entry = {
                    "status": entry.get("status"),
                    "timestamp": entry.get("timestamp"),
                    "changedBy": entry.get("changed_by"),
                }
                result["statusHistory"].append(history_entry)
        elif key in key_map:
            result[key_map[key]] = value
        else:
            # Keep other fields as-is (already camelCase or no mapping needed)
            result[key] = value

    return result


def item_to_frontend(item: dict) -> dict:
    """Convert order item from snake_case to camelCase"""
    if not item:
        return item

    key_map = {
        "item_id": "id",
        "item_type": "itemType",
        "product_id": "productId",
        "product_name": "productName",
        "unit_price": "unitPrice",
        "discount_percent": "discountPercent",
        "discount_amount": "discountAmount",
        "item_total": "finalPrice",
        "final_price": "finalPrice",
        "prescription_id": "prescriptionId",
        "lens_options": "lensOptions",
    }

    result = {}
    for key, value in item.items():
        # Drop `_id` ObjectId — same reasoning as order_to_frontend.
        if key == "_id":
            continue
        if key in key_map:
            result[key_map[key]] = value
        else:
            result[key] = value
    return result


def payment_to_frontend(payment: dict) -> dict:
    """Convert payment from snake_case to camelCase"""
    if not payment:
        return payment

    key_map = {
        "payment_id": "id",
        "received_by": "receivedBy",
        "received_at": "paidAt",
    }

    result = {}
    for key, value in payment.items():
        if key == "_id":
            continue  # Drop ObjectId — same reasoning as order_to_frontend.
        if key in key_map:
            result[key_map[key]] = value
        else:
            result[key] = value
    return result


# ============================================================================
# ENUMS & SCHEMAS
# ============================================================================


class OrderStatus(str, Enum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


# Valid state transitions — only these moves are allowed
VALID_TRANSITIONS = {
    "DRAFT": {"CONFIRMED", "CANCELLED"},
    "CONFIRMED": {
        "PROCESSING",
        "READY",
        "CANCELLED",
    },  # READY for quick-sale (no workshop)
    "PROCESSING": {"READY", "CANCELLED"},
    "READY": {"DELIVERED", "CANCELLED"},
    "DELIVERED": set(),  # Terminal
    "CANCELLED": set(),  # Terminal
}


def validate_status_transition(current: str, target: str) -> bool:
    """Check if an order status transition is valid."""
    allowed = VALID_TRANSITIONS.get(current, set())
    return target in allowed


class PaymentMethod(str, Enum):
    CASH = "CASH"
    UPI = "UPI"
    CARD = "CARD"
    BANK_TRANSFER = "BANK_TRANSFER"
    EMI = "EMI"
    CREDIT = "CREDIT"
    GIFT_VOUCHER = "GIFT_VOUCHER"


class OrderItemCreate(BaseModel):
    item_type: str  # FRAME, LENS, CONTACT_LENS, ACCESSORY, SERVICE
    product_id: str
    product_name: Optional[str] = None
    sku: Optional[str] = None
    brand: Optional[str] = None
    subbrand: Optional[str] = None
    category: Optional[str] = None
    quantity: int = Field(default=1, ge=1)
    unit_price: float = Field(..., ge=0)
    discount_percent: float = Field(default=0, ge=0, le=100)
    prescription_id: Optional[str] = None
    lens_options: Optional[dict] = None  # coating, tint, etc.
    lens_details: Optional[dict] = None  # type, material, coatings
    # Optional explicit serialized stock unit (when the POS knows which unit
    # is being sold, e.g. barcode-scan flow). Used by _mark_units_sold to flip
    # the unit to SOLD with the order_id so a future return can re-activate
    # exactly the unit that left. Absent -> FIFO allocation by product+store.
    stock_id: Optional[str] = None
    # Lens catalog cell coordinates (Branch B', sub-PR 4). Set by the FE
    # when a LENS item is configured via the Power Grid. The lens_stock_hook
    # uses these to atomically reserve the cell at POS Step 6; missing values
    # mean the line is a legacy / free-text lens entry and the hook no-ops.
    lens_line_id: Optional[str] = None
    sph: Optional[float] = None
    cyl: Optional[float] = None
    add: Optional[float] = None


class PaymentCreate(BaseModel):
    # Accept both `method` (canonical) and `mode` (legacy, still used by
    # the Orders-page Collect Payment modal). pydantic aliasing means the
    # request can send either — we canonicalize on the model.
    method: PaymentMethod = Field(..., validation_alias="method")
    amount: float = Field(..., gt=0)
    reference: Optional[str] = None
    # Gift-voucher code (only used when method=GIFT_VOUCHER). The POS sends
    # both `reference` and `voucher_code` set to the card code; we prefer
    # this explicit field and fall back to `reference` for older callers.
    voucher_code: Optional[str] = None
    # EMI-specific fields (only required when method=EMI)
    emi_months: Optional[int] = Field(None, ge=3, le=24)
    emi_provider: Optional[str] = None  # e.g., "BAJAJ", "HDFC", "ICICI"

    class Config:
        # Permit both "method" (native) and "mode" (legacy alias) in JSON.
        populate_by_name = True

    def __init__(self, **data):
        # pydantic 2 validation_alias is restrictive; accept "mode" as a
        # fallback if "method" isn't present. Normalises legacy callers.
        if "method" not in data and "mode" in data:
            data["method"] = data["mode"]
        super().__init__(**data)


class OrderCreate(BaseModel):
    customer_id: str
    patient_id: Optional[str] = None
    items: List[OrderItemCreate]
    notes: Optional[str] = None
    expected_delivery_days: int = Field(default=7, ge=1)
    # Phase 6.7 — delivery scheduling + order-level discount
    delivery_date: Optional[date] = None
    delivery_time_slot: Optional[str] = None  # e.g. "10:00-12:00"
    delivery_priority: Optional[str] = Field(
        default="NORMAL"
    )  # NORMAL | EXPRESS | URGENT
    cart_discount_percent: float = Field(default=0.0, ge=0.0, le=100.0)
    cart_discount_amount: float = Field(default=0.0, ge=0.0)
    cart_discount_reason: Optional[str] = None
    cart_discount_approved_by: Optional[str] = None
    # Incentive integration — explicit salesperson attribution (POS picker)
    # and the Visufit measurement ID for the per-staff Visufit coverage gate.
    salesperson_id: Optional[str] = None
    salesperson_name: Optional[str] = None
    visufit_id: Optional[str] = None


class OrderUpdate(BaseModel):
    notes: Optional[str] = None
    expected_delivery: Optional[date] = None


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("")
async def list_orders(
    store_id: Optional[str] = Query(None),
    status: Optional[OrderStatus] = Query(None),
    customer_id: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """List orders with filters"""
    repo = get_order_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo is not None:
        if customer_id:
            orders = repo.find_by_customer(customer_id, limit=limit)
        elif active_store:
            orders = repo.find_by_store(
                active_store,
                from_date=from_date,
                to_date=to_date,
                status=status.value if status else None,
            )
        else:
            filter_dict = {}
            if status:
                filter_dict["status"] = status.value
            orders = repo.find_many(filter_dict, skip=skip, limit=limit)

        # Convert to frontend format (camelCase)
        from ..utils.pagination import paginate

        orders_formatted = [order_to_frontend(o) for o in orders]
        page = (skip // limit) + 1 if limit > 0 else 1
        result = paginate(orders_formatted, page=page, page_size=limit)
        result["orders"] = result["data"]  # backward compat
        return result

    return {
        "orders": [],
        "total": 0,
        "data": [],
        "pagination": {"total": 0, "page": 1, "page_size": limit, "total_pages": 0},
    }


# NOTE: Specific routes MUST come before /{order_id} to avoid being matched as order_id
@router.get("/pending/delivery")
async def get_pending_deliveries(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get orders pending delivery"""
    repo = get_order_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo is not None:
        orders = repo.find_ready_for_delivery(active_store)
        orders_formatted = [order_to_frontend(o) for o in orders]
        return {"orders": orders_formatted}

    return {"orders": []}


@router.get("/unpaid/list")
async def get_unpaid_orders(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get unpaid/partially paid orders"""
    repo = get_order_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo is not None:
        orders = repo.find_unpaid(active_store)
        orders_formatted = [order_to_frontend(o) for o in orders]
        return {"orders": orders_formatted}

    return {"orders": []}


@router.get("/overdue/list")
async def get_overdue_orders(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get overdue orders (past expected delivery)"""
    repo = get_order_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo is not None:
        orders = repo.find_overdue(active_store)
        orders_formatted = [order_to_frontend(o) for o in orders]
        return {"orders": orders_formatted}

    return {"orders": []}


@router.get("/search")
async def search_orders(
    q: str = Query(..., min_length=2),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Search orders by number, customer name, or phone"""
    repo = get_order_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo is not None:
        orders = repo.search_orders(q, active_store)
        orders_formatted = [order_to_frontend(o) for o in orders]
        return {"orders": orders_formatted}

    return {"orders": []}


@router.get("/sales/summary")
async def get_sales_summary(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Get sales summary for a date range"""
    repo = get_order_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo and active_store:
        summary = repo.get_sales_summary(active_store, from_date, to_date)
        return summary

    return {
        "totalOrders": 0,
        "totalRevenue": 0,
        "totalPaid": 0,
        "avgOrderValue": 0,
        "totalItems": 0,
    }


@router.get("/status/counts")
async def get_status_counts(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get order counts by status"""
    repo = get_order_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo is not None:
        counts = repo.get_status_counts(active_store)
        return {"statusCounts": counts}

    return {"statusCounts": {}}


def generate_order_number(store_id: str) -> str:
    """Generate unique order number: ORD-BOK01-2026-A1B2C3

    Audit 2026-04-21 turned up a stray legacy order formatted as
    `BV-BV--2026-D639D3` — missing ORD- prefix and double dash. That's
    not something this function can produce, so it's either seed data
    or was minted before this helper existed. This tightened version:
      - Always emits ORD- prefix (no path can bypass it).
      - Strips the chain prefix (BV/WO) + merges the remaining segments
        without trailing dashes so pathological store_ids can't leak
        through as `ORD-BV--2026-...`.
      - Falls back to `IMS` when store_id is unusable so we never
        produce an empty prefix slot.
    """
    raw = (store_id or "").strip().upper()
    # Drop the chain prefix (BV / WO / BVO) if present so prefix focuses
    # on the store part. "BV-BOK-01" → ["BOK", "01"].
    parts = [p for p in raw.split("-") if p and p not in ("BV", "WO", "BVO")]
    if len(parts) >= 2:
        prefix = (parts[0] + parts[1])[:8]  # BOK01
    elif len(parts) == 1:
        prefix = parts[0][:8]
    else:
        prefix = "IMS"
    # Sanitize: alnum only, upper, non-empty.
    prefix = "".join(c for c in prefix if c.isalnum()) or "IMS"
    year = datetime.now().year
    short_uuid = str(uuid.uuid4())[:6].upper()
    return f"ORD-{prefix}-{year}-{short_uuid}"


def _resolve_product_doc(product_repo, pid: str):
    """Tolerant product existence lookup for order-create.

    Imported products are often referenced by their Mongo `_id` or `sku`
    rather than `product_id` (the same tolerance the admin catalog uses).
    Tries product_id, then sku, then _id (string or ObjectId). Returns the
    product doc or None. Existence only — pricing comes from the order payload.
    """
    if not pid:
        return None
    product = product_repo.find_by_id(pid)
    if product is not None:
        return product
    try:
        coll = product_repo.collection
        product = coll.find_one(
            {"$or": [{"product_id": pid}, {"sku": pid}, {"_id": pid}]}
        )
        if product is None and len(pid) == 24:
            from bson import ObjectId

            try:
                product = coll.find_one({"_id": ObjectId(pid)})
            except Exception:
                product = None
        return product
    except Exception:
        return None


# Item types / product_id prefixes that have NO serialized stock to mark sold.
# SERVICE = labour line (eg fitting); custom-/lens-/lens-sug- = virtual POS
# items the configurator/suggestion helper generates on the fly. These never
# carry a stock_unit row, so trying to mark_sold them is a no-op (not an error).
_VIRTUAL_PID_PREFIXES = ("custom-", "lens-", "lens-sug-")
_NON_SERIALIZED_ITEM_TYPES = {"SERVICE"}


def _mark_units_sold(
    order_id: str,
    items_data: List[dict],
    store_id: Optional[str],
) -> List[str]:
    """For each serialized item on a created order, flip its stock_unit row to
    SOLD with the order_id stamped on it. Returns the list of stock_ids marked.

    Two paths:
      1. Item carries an explicit stock_id (POS knew the unit; barcode-scan
         flow). Just call mark_sold(stock_id, order_id).
      2. No stock_id but a real product_id + store_id. FIFO-allocate the first
         AVAILABLE unit via find_by_product_store and mark THAT sold.

    Virtual items (SERVICE / custom-/ lens-/ lens-sug-) and items without a
    product_id are skipped silently - they have no serialized row to mark.

    Fail-soft: any lookup or write failure is logged. Order creation must
    NEVER be blocked by stock-side issues; if we can't mark a unit, the
    returns flow will fall back to the no-order-id path (any non-AVAILABLE
    unit for that product+store).
    """
    if not order_id or not items_data:
        return []
    try:
        stock_repo = get_stock_repository()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STOCK] mark_sold: stock repo unavailable: %s", exc)
        return []
    if stock_repo is None:
        return []

    marked: List[str] = []
    # Track stock_ids consumed within this order so two lines for the same
    # product_id don't grab the same unit twice.
    used: set = set()

    for line in items_data or []:
        if not isinstance(line, dict):
            continue
        item_type = (line.get("item_type") or "").upper()
        if item_type in _NON_SERIALIZED_ITEM_TYPES:
            continue
        pid = line.get("product_id") or ""
        if not pid or pid.startswith(_VIRTUAL_PID_PREFIXES):
            continue
        qty = int(line.get("quantity") or 1)
        if qty < 1:
            continue

        explicit_sid = line.get("stock_id")
        for _ in range(qty):
            sid: Optional[str] = None
            if explicit_sid and explicit_sid not in used:
                # Path 1: POS told us exactly which unit.
                try:
                    ok = stock_repo.mark_sold(explicit_sid, order_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[STOCK] mark_sold(stock_id=%s) failed: %s",
                        explicit_sid,
                        exc,
                    )
                    ok = False
                if ok:
                    sid = explicit_sid
                # Only consume the explicit stock_id once per line; the
                # remaining qty falls through to FIFO.
                explicit_sid = None
            else:
                # Path 2: FIFO-allocate from product+store.
                if not store_id:
                    continue
                try:
                    available = (
                        stock_repo.find_by_product_store(pid, store_id) or []
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[STOCK] find_by_product_store(%s,%s) failed: %s",
                        pid,
                        store_id,
                        exc,
                    )
                    available = []
                pick: Optional[Dict] = None
                for unit in available:
                    uid = unit.get("stock_id") or unit.get("_id")
                    if uid and uid not in used:
                        pick = unit
                        break
                if not pick:
                    continue
                pick_sid = pick.get("stock_id") or pick.get("_id")
                try:
                    ok = stock_repo.mark_sold(str(pick_sid), order_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[STOCK] mark_sold(fifo=%s) failed: %s", pick_sid, exc
                    )
                    ok = False
                if ok:
                    sid = str(pick_sid)

            if sid:
                used.add(sid)
                marked.append(sid)

    return marked


@router.post("", status_code=201)
async def create_order(
    order: OrderCreate, current_user: dict = Depends(get_current_user)
):
    """Create new sales order"""
    order_repo = get_order_repository()
    customer_repo = get_customer_repository()
    store_id = current_user.get("active_store_id")
    # Salesperson attribution drives the incentive engine. Prefer the
    # explicit POS picker value; fall back to the logged-in user so older
    # clients (and quick sales) still attribute somewhere.
    salesperson_id = order.salesperson_id or current_user.get("user_id")
    salesperson_name = (
        order.salesperson_name
        or current_user.get("full_name")
        or current_user.get("name")
    )

    # Validate items
    if not order.items:
        raise HTTPException(status_code=400, detail="Order must have at least one item")

    MAX_CART_ITEMS = 15
    if len(order.items) > MAX_CART_ITEMS:
        raise HTTPException(
            status_code=400,
            detail=f"Cart exceeds maximum of {MAX_CART_ITEMS} items. Split into multiple orders.",
        )

    # Validate product_ids exist.
    #
    # Audit Run #2 (2026-04-21) blocker: this used to call
    # stock_repo.find_by_id(product_id), which looks in `stock_units`
    # (keyed on stock_id), while the POS catalog + /inventory both
    # serve from `products` (keyed on product_id). Every order-create
    # failed with "Product not found: prod-fr-001". Switched to
    # ProductRepository, and added virtual-id passthroughs for the
    # POS lens configurator ("lens-*"), lens suggestion helper
    # ("lens-sug-*"), and manual custom items ("custom-*").
    product_repo = get_product_repository()
    if product_repo is not None:
        for item in order.items:
            pid = item.product_id or ""
            if not pid:
                continue
            if pid.startswith(("custom-", "lens-", "lens-sug-")):
                continue
            product = _resolve_product_doc(product_repo, pid)
            if product is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Product not found: {pid} ({item.product_name or 'unknown'})",
                )

    # Validate: offer_price cannot exceed MRP
    for item in order.items:
        offer_price = getattr(item, "offer_price", 0) or 0
        mrp = getattr(item, "mrp", 0) or 0
        if offer_price > 0 and mrp > 0 and offer_price > mrp:
            raise HTTPException(
                status_code=400,
                detail=f"Offer price (₹{offer_price}) cannot exceed MRP (₹{mrp}) for {getattr(item, 'product_name', 'item')}",
            )

    if order_repo is not None and customer_repo is not None:
        # Verify customer exists (allow walk-in with generated IDs)
        customer = customer_repo.find_by_id(order.customer_id)
        is_walkin = not customer and (
            order.customer_id.startswith("walkin-") or order.customer_id == "walk-in"
        )
        if not customer and not is_walkin:
            raise HTTPException(status_code=404, detail="Customer not found")

        customer_name = customer.get("name") if customer else "Walk-in Customer"
        customer_phone = (
            customer.get("phone") or customer.get("mobile") if customer else ""
        )

        # Pre-fetch cost_price for every product on the order so we can
        # snapshot it onto each line as cost_at_sale. This freezes COGS at
        # sale time so historical P&L doesn't drift when cost_price is
        # edited later. Virtual SKUs (custom-/lens-/lens-sug-) don't have
        # a product doc -> cost_at_sale stays None and the finance layer
        # falls back to 60% of line total.
        _cost_by_pid: Dict[str, float] = {}
        try:
            if product_repo is not None:
                for it in order.items:
                    pid = it.product_id or ""
                    if not pid or pid in _cost_by_pid:
                        continue
                    if pid.startswith(("custom-", "lens-", "lens-sug-")):
                        continue
                    pdoc = _resolve_product_doc(product_repo, pid)
                    if pdoc and pdoc.get("cost_price") is not None:
                        try:
                            _cost_by_pid[pid] = float(pdoc["cost_price"])
                        except (TypeError, ValueError):
                            pass
        except Exception:
            # Cost snapshot is fail-soft -- never block order create.
            _cost_by_pid = {}

        # Calculate totals
        items_data = []
        subtotal = 0.0

        # Retrieve user discount cap for enforcement.
        # Use the role-aware effective cap helper rather than the raw user
        # document field — that one defaults to 10% even for SUPERADMIN,
        # which was the long-standing "why is my cap 10%" bug.
        from api.services.role_caps import effective_discount_cap
        user_roles = current_user.get("roles", [])
        user_discount_cap = effective_discount_cap(
            user_roles, current_user.get("discount_cap")
        )
        is_admin = any(
            r in user_roles for r in ["SUPERADMIN", "ADMIN", "STORE_MANAGER"]
        )

        for item in order.items:
            item_total = item.unit_price * item.quantity

            # Enforce discount cap (admins bypass)
            effective_cap = user_discount_cap
            if not is_admin and item.discount_percent > 0:
                # Look up category cap for this product — read from the
                # products collection (same fix as the existence-check
                # above; the original code hit stock_units and always
                # returned None, leaving effective_cap = user's full cap).
                try:
                    pr = get_product_repository()
                    if (
                        pr is not None
                        and item.product_id
                        and not item.product_id.startswith(
                            ("custom-", "lens-", "lens-sug-")
                        )
                    ):
                        product = pr.find_by_id(item.product_id)
                        if product:
                            cat = (
                                product.get("discount_category")
                                or product.get("category")
                                or "MASS"
                            )
                            category_cap = CATEGORY_DISCOUNT_CAPS.get(cat, 10.0)
                            effective_cap = min(user_discount_cap, category_cap)
                except Exception:
                    pass  # fall back to user cap only

                if item.discount_percent > effective_cap:
                    raise HTTPException(
                        status_code=403,
                        detail=f"Discount {item.discount_percent}% on {item.product_name or item.product_id} "
                        f"exceeds your limit of {effective_cap}%. Contact a manager for approval.",
                    )

            discount_amount = item_total * (item.discount_percent / 100)
            item_subtotal = item_total - discount_amount

            # ============================================================
            # INCENTIVE AUTO-TAGGING
            # Detects qualifying items for kicker tracking at POS time
            # Tags: brand group, subbrand, kicker type, item value, discount
            # Replaces the manual PRODUCT_INCENTIVE Excel entirely
            # ============================================================
            INCENTIVE_BRANDS = {
                "ZEISS": "ZEISS",
                "SAFILO": "SAFILO",
                "CARRERA": "SAFILO",
                "POLAROID": "SAFILO",
                "MARC JACOB": "SAFILO",
                "HUGO": "SAFILO",
                "SEVENTH STREET": "SAFILO",
                "BOSS": "SAFILO",
                "TOMMY HILFIGER": "SAFILO",
                "PIERRE CARDIN": "SAFILO",
                "UNDER ARMOUR": "SAFILO",
            }
            brand_upper = (item.brand or "").upper()
            subbrand_upper = (item.subbrand or "").upper()
            product_name_upper = (item.product_name or "").upper()

            # Check brand, subbrand, AND product name for matches
            # (lens details like "1.5 ZEISS PROGRESSIVE LIGHT..." appear in product name)
            incentive_brand = None
            matched_key = None
            for key, group in INCENTIVE_BRANDS.items():
                if (
                    key in brand_upper
                    or key in subbrand_upper
                    or key in product_name_upper
                ):
                    incentive_brand = group
                    matched_key = key
                    break

            # Detect kicker type from lens_details, subbrand, or product name
            incentive_kicker = None
            incentive_lens_type = None
            incentive_addon = None

            if incentive_brand == "ZEISS":
                # Check lens_details dict first (structured data from LensDetailsModal)
                lens_type_str = ""
                if item.lens_details:
                    lens_type_str = (item.lens_details.get("type", "") or "").upper()
                    lens_material = (
                        item.lens_details.get("material", "") or ""
                    ).upper()
                    lens_coatings = " ".join(
                        item.lens_details.get("coatings", []) or []
                    ).upper()
                    lens_type_str = f"{lens_type_str} {lens_material} {lens_coatings}"

                # Also check product name (e.g., "1.5 ZEISS PROGRESSIVE LIGHT 2 3D DVP UV")
                full_check = f"{lens_type_str} {product_name_upper} {subbrand_upper}"

                if "SMARTLIFE" in full_check or "SMART LIFE" in full_check:
                    incentive_kicker = "ZEISS_SMARTLIFE"
                    incentive_lens_type = "PAL" if "PROGRESS" in full_check else "SV"
                    incentive_addon = "SMART LIFE"
                elif "PHOTOFUSION" in full_check or "PFX" in full_check:
                    incentive_kicker = "ZEISS_PHOTOFUSION"
                    incentive_lens_type = "PAL" if "PROGRESS" in full_check else "SV"
                    incentive_addon = "PFX"
                elif "PROGRESSIVE" in full_check or "PAL" in full_check:
                    incentive_kicker = "ZEISS_PROGRESSIVE"
                    incentive_lens_type = "PAL"
                elif "FSV" in full_check or "SINGLE" in full_check:
                    incentive_kicker = "ZEISS_SV"
                    incentive_lens_type = "SV"
                else:
                    incentive_kicker = "ZEISS_OTHER"

            elif incentive_brand == "SAFILO":
                if item.item_type in ("FRAME",):
                    incentive_kicker = "SAFILO_FRAME"
                elif item.item_type in ("SUNGLASS",) or (
                    item.category and "SG" in item.category.upper()
                ):
                    incentive_kicker = "SAFILO_SG"
                else:
                    incentive_kicker = "SAFILO_OTHER"

            # Build incentive tag (None if not qualifying)
            incentive_tag = None
            if incentive_brand:
                incentive_tag = {
                    "brand_group": incentive_brand,
                    "brand": item.brand,
                    "subbrand": item.subbrand or matched_key,
                    "kicker": incentive_kicker,
                    "lens_type": incentive_lens_type,
                    "addon": incentive_addon,
                    "item_value": item_subtotal,
                    "item_mrp": item.unit_price * item.quantity,
                    "discount_percent": item.discount_percent,
                    "discount_amount": discount_amount,
                    "salesperson_id": salesperson_id,
                    "tagged_at": datetime.now().isoformat(),
                }

            items_data.append(
                {
                    "item_id": str(uuid.uuid4()),
                    "item_type": item.item_type,
                    "product_id": item.product_id,
                    "product_name": item.product_name,
                    "sku": item.sku,
                    "brand": item.brand,
                    "subbrand": item.subbrand,
                    "category": item.category,
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "discount_percent": item.discount_percent,
                    "discount_amount": discount_amount,
                    "item_total": item_subtotal,
                    # COGS-freeze: snapshot the product cost at sale time so
                    # historical P&L stays stable when cost_price is edited.
                    # None when the cost is unknown (virtual / no product doc).
                    "cost_at_sale": _cost_by_pid.get(item.product_id or ""),
                    "prescription_id": item.prescription_id,
                    "lens_options": item.lens_options,
                    "lens_details": item.lens_details,
                    "incentive_tag": incentive_tag,
                    # Carry the explicit serialized unit so a future return can
                    # re-activate exactly the unit that left. Optional; FIFO
                    # allocation by product+store fills the gap when missing.
                    "stock_id": getattr(item, "stock_id", None),
                    # Lens catalog cell coordinates (B'4) -- consumed by
                    # the lens_stock_hook on reserve/commit/release.
                    "lens_line_id": getattr(item, "lens_line_id", None),
                    "sph": getattr(item, "sph", None),
                    "cyl": getattr(item, "cyl", None),
                    "add": getattr(item, "add", None),
                }
            )
            subtotal += item_subtotal

        # Phase 6.15 — per-category GST (Indian rules). Audit Run #4
        # caught a phantom-balance bug; further audit (May-2026) caught
        # the per-cat fix itself zeroing every order's tax_amount
        # because the loop read `it.get("subtotal")` while the dict was
        # built with key "item_total". The per-category math is now in
        # `_compute_per_category_gst`, used by create + add + remove.
        cart_discount_percent = max(0.0, min(100.0, order.cart_discount_percent or 0.0))
        gst = _compute_per_category_gst(items_data, cart_discount_percent)
        taxable_after_cart_discount = gst["taxable"]
        tax_amount = gst["tax"]
        cart_discount_amount = gst["cart_discount_amount"]
        total_discount = gst["total_discount"]
        tax_rate = gst["dominant_rate"]
        grand_total = round(taxable_after_cart_discount + tax_amount, 2)

        # Resolve delivery date — explicit date > expected_delivery_days
        if order.delivery_date:
            expected_delivery = datetime.combine(
                order.delivery_date, datetime.min.time()
            )
        else:
            expected_delivery = datetime.now() + timedelta(
                days=order.expected_delivery_days
            )

        # Branch B' sub-PR 4 -- atomic lens-stock reserve BEFORE the
        # order is persisted. Owner-decreed flow (2026-05-28): the POS
        # "Pay now" action validates the cart, calls reserve for each
        # lens line, and only then persists the order. If any reserve
        # 409s, the order is NEVER created and the POS surfaces a clean
        # "out of stock for SPH X CYL Y; available: N" message.
        #
        # We pre-generate the order_id so the reserve audit rows have a
        # stable source_id (`{order_id}#{line_index}`). This is the same
        # value the workshop commit + order cancel paths will use to
        # find/idempotency-check the reservation later.
        precomputed_order_id = str(uuid.uuid4())

        lens_reserve_failed = False
        lens_reservations: List[Dict[str, Any]] = []
        # Import the hook BEFORE the try so `release_for_cancel` is
        # unconditionally bound for the compensating-rollback path in the
        # except block (pylint E0601 otherwise: the import lived inside try).
        from ..services.lens_stock_hook import (
            reserve_for_order_item,
            release_for_cancel,
        )
        try:
            for idx, oi in enumerate(items_data):
                rec = await reserve_for_order_item(
                    order_item=oi,
                    order_id=precomputed_order_id,
                    line_index=idx,
                    store_id=store_id or "",
                    user=current_user,
                )
                if rec is not None:
                    lens_reservations.append({"line_index": idx, **rec})
                    if rec.get("status") == "failed":
                        lens_reserve_failed = True
        except HTTPException as exc:
            # Insufficient stock (409) -- compensating release for any
            # lines that already succeeded, then re-raise so POS sees
            # the original "available=N" message and the user can fix.
            if exc.status_code == 409:
                try:
                    for prev_idx, prev_oi in enumerate(items_data):
                        try:
                            await release_for_cancel(
                                order_item=prev_oi,
                                order_id=precomputed_order_id,
                                line_index=prev_idx,
                                store_id=store_id or "",
                                user=current_user,
                            )
                        except Exception as inner_rb:  # noqa: BLE001
                            logger.warning(
                                "[LENS_HOOK] compensating release "
                                "failed (line %s): %s", prev_idx, inner_rb,
                            )
                except Exception as rb_exc:  # noqa: BLE001
                    logger.warning(
                        "[LENS_HOOK] rollback outer error: %s", rb_exc,
                    )
            raise
        except Exception as exc:  # noqa: BLE001
            # Non-blocking soft failure (mongo blip, etc.). Tag the
            # order and continue -- never crash POS create on a hook
            # error. Revenue protection takes priority.
            logger.warning(
                "[LENS_HOOK] reserve fail-soft pre-create: %s", exc,
            )
            lens_reserve_failed = True

        order_data = {
            "order_id": precomputed_order_id,
            "order_number": generate_order_number(store_id),
            "store_id": store_id,
            "customer_id": order.customer_id,
            "customer_name": customer_name,
            "customer_phone": customer_phone,
            "patient_id": order.patient_id,
            "salesperson_id": salesperson_id,
            "salesperson_name": salesperson_name,
            "visufit_id": (order.visufit_id or None),
            "items": items_data,
            "subtotal": subtotal,
            "cart_discount_percent": cart_discount_percent,
            "cart_discount_amount": cart_discount_amount,
            "cart_discount_reason": order.cart_discount_reason,
            "cart_discount_approved_by": order.cart_discount_approved_by,
            "tax_rate": tax_rate,
            "tax_amount": tax_amount,
            "total_discount": total_discount,
            "grand_total": grand_total,
            "amount_paid": 0.0,
            "balance_due": grand_total,
            "payment_status": "UNPAID",
            "status": "DRAFT",
            "expected_delivery": expected_delivery.isoformat(),
            "delivery_time_slot": order.delivery_time_slot,
            "delivery_priority": (order.delivery_priority or "NORMAL").upper(),
            "notes": order.notes,
            "payments": [],
            "lens_reservations": lens_reservations,
            "lens_reserve_failed": bool(lens_reserve_failed),
        }

        try:
            created = order_repo.create(order_data)
        except Exception as create_exc:  # noqa: BLE001
            # Order persist failed AFTER reservations succeeded -- run
            # the compensating release so the cells don't leak.
            logger.error(
                "[ORDERS] order_repo.create failed; releasing %d lens "
                "reservations for order %s",
                len(lens_reservations), precomputed_order_id,
            )
            try:
                from ..services.lens_stock_hook import release_for_cancel
                for idx, oi in enumerate(items_data):
                    try:
                        await release_for_cancel(
                            order_item=oi,
                            order_id=precomputed_order_id,
                            line_index=idx,
                            store_id=store_id or "",
                            user=current_user,
                        )
                    except Exception:  # noqa: BLE001
                        pass  # fail-soft compensating action
            except Exception:  # noqa: BLE001
                pass
            raise HTTPException(
                status_code=500,
                detail="Failed to create order: {0}".format(create_exc),
            )

        if created:
            created_order_id = created.get("order_id") or ""
            # Flip serialized stock units to SOLD with this order_id stamped on
            # them. This is what lets returns.py reactivate THE EXACT unit that
            # left (preferred path in _reactivate_original_unit; the fallback
            # is "any non-AVAILABLE unit for this product+store" which can
            # collide across orders). Fail-soft: a stock-side failure logs and
            # never blocks the POS sale - bad stock data must not break revenue.
            try:
                _mark_units_sold(created_order_id, items_data, store_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[STOCK] mark_units_sold failed: %s", exc)

            # Pune-incentive walk-in counter (Module i, Phase 4): bump
            # the per-store-per-day counter, dedup'd by mobile.
            try:
                walkin_repo = get_walkin_counter_repository()
                if walkin_repo is not None:
                    walkin_repo.auto_increment(
                        store_id=store_id or "",
                        sales_person_id=salesperson_id or "",
                        mobile=customer_phone or None,
                    )
            except Exception:
                pass  # fail-soft — counter must never block order create

            # Loyalty engine — award earn points. Idempotent on
            # (customer_id, order_id), and fully fail-soft (any failure
            # is logged but never blocks the order response).
            try:
                from .loyalty import earn_for_order_internal

                # Skip walk-ins: they have no real customer_id to credit.
                if order.customer_id and not order.customer_id.startswith(
                    ("walkin-", "walk-in")
                ):
                    earn_for_order_internal(
                        customer_id=order.customer_id,
                        order_id=created.get("order_id") or "",
                        items=items_data,
                        rupee_value=float(taxable_after_cart_discount),
                        user_id=current_user.get("user_id"),
                        store_id=store_id,
                    )
            except Exception:
                pass  # fail-soft — loyalty must never block POS

            return {
                "order_id": created["order_id"],
                "order_number": created["order_number"],
                "status": "DRAFT",
                "grand_total": grand_total,
                "message": "Order created successfully",
            }

        raise HTTPException(status_code=500, detail="Failed to create order")

    return {
        "order_id": str(uuid.uuid4()),
        "order_number": generate_order_number(store_id or "STR"),
        "status": "DRAFT",
        "message": "Order created successfully",
    }


@router.get("/{order_id}")
async def get_order(order_id: str, current_user: dict = Depends(get_current_user)):
    """Get order details"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is not None:
            return order_to_frontend(order)
        raise HTTPException(status_code=404, detail="Order not found")

    return {"id": order_id}


@router.put("/{order_id}")
async def update_order(
    order_id: str, order: OrderUpdate, current_user: dict = Depends(get_current_user)
):
    """Update order (only DRAFT orders)"""
    repo = get_order_repository()

    if repo is not None:
        existing = repo.find_by_id(order_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Order not found")

        if existing.get("status") != "DRAFT":
            raise HTTPException(
                status_code=400, detail="Only DRAFT orders can be updated"
            )

        update_data = order.model_dump(exclude_unset=True)
        if "expected_delivery" in update_data and update_data["expected_delivery"]:
            update_data["expected_delivery"] = update_data[
                "expected_delivery"
            ].isoformat()

        update_data["updated_by"] = current_user.get("user_id")

        if repo.update(order_id, update_data):
            # Audit alert (May 2026) — fire-and-forget, never blocks order edit
            try:
                from ..services.audit_alerts import alert_order_edited

                fresh = repo.find_by_id(order_id) or {}
                import asyncio as _aio

                _aio.create_task(
                    alert_order_edited(
                        order_id,
                        before=existing,
                        after=fresh,
                        user_id=current_user.get("user_id"),
                    )
                )
            except Exception:
                pass
            return {"order_id": order_id, "message": "Order updated"}

        raise HTTPException(status_code=500, detail="Failed to update order")

    return {"order_id": order_id, "message": "Order updated"}


@router.post("/{order_id}/items")
async def add_order_item(
    order_id: str, item: OrderItemCreate, current_user: dict = Depends(get_current_user)
):
    """Add item to order (only DRAFT orders)"""
    # Validate: offer_price cannot exceed MRP
    offer_price = getattr(item, "offer_price", 0) or 0
    mrp = getattr(item, "mrp", 0) or 0
    if offer_price > 0 and mrp > 0 and offer_price > mrp:
        raise HTTPException(
            status_code=400,
            detail=f"Offer price (₹{offer_price}) cannot exceed MRP (₹{mrp})",
        )

    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        if order.get("status") != "DRAFT":
            raise HTTPException(
                status_code=400, detail="Can only add items to DRAFT orders"
            )

        # Calculate item totals
        item_total = item.unit_price * item.quantity
        discount_amount = item_total * (item.discount_percent / 100)
        item_subtotal = item_total - discount_amount

        item_data = {
            "item_id": str(uuid.uuid4()),
            "item_type": item.item_type,
            "product_id": item.product_id,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "discount_percent": item.discount_percent,
            "discount_amount": discount_amount,
            "item_total": item_subtotal,
            "prescription_id": item.prescription_id,
            "lens_options": item.lens_options,
        }

        # Add item and recalculate totals — preserves per-category GST
        # (Phase 6.15 fix) instead of stamping the order's old flat
        # tax_rate. Mirrors the frontend's getGrandTotal exactly.
        items = order.get("items", []) + [item_data]
        cart_discount_percent = order.get("cart_discount_percent", 0) or 0
        gst = _compute_per_category_gst(items, cart_discount_percent)
        grand_total = round(gst["taxable"] + gst["tax"], 2)

        repo.update(
            order_id,
            {
                "items": items,
                "subtotal": gst["subtotal"],
                "cart_discount_amount": gst["cart_discount_amount"],
                "tax_rate": gst["dominant_rate"],
                "tax_amount": gst["tax"],
                "total_discount": gst["total_discount"],
                "grand_total": grand_total,
                "balance_due": grand_total - order.get("amount_paid", 0),
            },
        )

        return {"message": "Item added to order", "item_id": item_data["item_id"]}

    return {"message": "Item added to order"}


@router.delete("/{order_id}/items/{item_id}")
async def remove_order_item(
    order_id: str, item_id: str, current_user: dict = Depends(get_current_user)
):
    """Remove item from order (only DRAFT orders)"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        if order.get("status") != "DRAFT":
            raise HTTPException(
                status_code=400, detail="Can only remove items from DRAFT orders"
            )

        items = [i for i in order.get("items", []) if i.get("item_id") != item_id]
        if len(items) == len(order.get("items", [])):
            raise HTTPException(status_code=404, detail="Item not found in order")

        # Recalculate totals (per-category GST, mirrors create_order).
        cart_discount_percent = order.get("cart_discount_percent", 0) or 0
        gst = _compute_per_category_gst(items, cart_discount_percent)
        grand_total = round(gst["taxable"] + gst["tax"], 2)

        repo.update(
            order_id,
            {
                "items": items,
                "subtotal": gst["subtotal"],
                "cart_discount_amount": gst["cart_discount_amount"],
                "tax_rate": gst["dominant_rate"],
                "tax_amount": gst["tax"],
                "total_discount": gst["total_discount"],
                "grand_total": grand_total,
                "balance_due": grand_total - order.get("amount_paid", 0),
            },
        )

        # Audit alert (May 2026) — flag the deleted item even on DRAFT
        # so the audit trail is complete; severity HIGH for DRAFT.
        try:
            from ..services.audit_alerts import alert_item_deleted
            import asyncio as _aio

            removed = next(
                (i for i in order.get("items", []) if i.get("item_id") == item_id),
                {"item_id": item_id},
            )
            _aio.create_task(
                alert_item_deleted(
                    order_id,
                    item_id,
                    item_data=removed,
                    user_id=current_user.get("user_id"),
                    order_status=order.get("status", "DRAFT"),
                )
            )
        except Exception:
            pass

        return {"message": "Item removed from order"}

    return {"message": "Item removed from order"}


@router.post("/{order_id}/confirm")
async def confirm_order(order_id: str, current_user: dict = Depends(get_current_user)):
    """Confirm order (DRAFT -> CONFIRMED)"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        if not validate_status_transition(order.get("status", ""), "CONFIRMED"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot confirm order — current status is {order.get('status')}",
            )

        if not order.get("items"):
            raise HTTPException(
                status_code=400, detail="Cannot confirm order with no items"
            )

        if repo.update_status(order_id, "CONFIRMED", current_user.get("user_id")):
            return {
                "order_id": order_id,
                "status": "CONFIRMED",
                "message": "Order confirmed",
            }

        raise HTTPException(status_code=500, detail="Failed to confirm order")

    return {"order_id": order_id, "status": "CONFIRMED"}


@router.post("/{order_id}/payments")
async def add_payment(
    order_id: str,
    payment: PaymentCreate,
    current_user: dict = Depends(get_current_user),
):
    """Add payment to order"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        if order.get("status") == "CANCELLED":
            raise HTTPException(
                status_code=400, detail="Cannot add payment to cancelled order"
            )

        balance_due = order.get("balance_due", order.get("grand_total", 0))
        if payment.amount > balance_due:
            raise HTTPException(
                status_code=400,
                detail=f"Payment amount exceeds balance due (₹{balance_due})",
            )

        # Gift voucher: REDEEM (decrement the card) before recording the
        # payment, so an abandoned sale never burns a card and there is no
        # client-side double-spend. The atomic redeem is the single source
        # of truth for spend rules + concurrency safety (see vouchers.py).
        # On failure nothing is recorded; on success we fall through to the
        # normal payment-recording path unchanged.
        if payment.method == PaymentMethod.GIFT_VOUCHER:
            from .vouchers import redeem_voucher_atomic

            voucher_code = (payment.voucher_code or payment.reference or "").strip()
            if not voucher_code:
                raise HTTPException(
                    status_code=400,
                    detail="Voucher: a voucher code is required for GIFT_VOUCHER payments",
                )
            from ..dependencies import get_seeded_db

            result = redeem_voucher_atomic(
                get_seeded_db(),
                voucher_code,
                payment.amount,
                order_id,
                current_user.get("user_id"),
            )
            if not result.get("ok"):
                raise HTTPException(
                    status_code=400, detail=f"Voucher: {result.get('reason')}"
                )

        # EMI validation and interest calculation
        emi_details = None
        if payment.method == PaymentMethod.EMI:
            if not payment.emi_months:
                raise HTTPException(
                    status_code=400,
                    detail="EMI tenure (emi_months) is required for EMI payments",
                )
            # Fetch configurable EMI rate from store settings (default 12% annual)
            emi_annual_rate = 12.0  # fallback
            try:
                from ..dependencies import get_seeded_db

                db = get_seeded_db()
                if db:
                    store_settings = db.get_collection("settings").find_one(
                        {
                            "store_id": current_user.get("active_store_id"),
                            "key": "emi_config",
                        }
                    )
                    if store_settings and store_settings.get("value", {}).get(
                        "annual_rate"
                    ):
                        emi_annual_rate = float(store_settings["value"]["annual_rate"])
            except Exception:
                pass  # use default

            monthly_rate = emi_annual_rate / 12 / 100
            months = payment.emi_months
            if monthly_rate > 0:
                emi_amount = (
                    payment.amount
                    * monthly_rate
                    * (1 + monthly_rate) ** months
                    / ((1 + monthly_rate) ** months - 1)
                )
            else:
                emi_amount = payment.amount / months

            emi_details = {
                "tenure_months": months,
                "annual_rate": emi_annual_rate,
                "monthly_emi": round(emi_amount, 2),
                "total_payable": round(emi_amount * months, 2),
                "interest_amount": round(emi_amount * months - payment.amount, 2),
                "provider": payment.emi_provider or "STORE",
            }

        payment_data = {
            "payment_id": str(uuid.uuid4()),
            "method": payment.method.value,
            "amount": payment.amount,
            "reference": payment.reference,
            "received_by": current_user.get("user_id"),
            "received_at": datetime.now().isoformat(),
        }
        if emi_details:
            payment_data["emi_details"] = emi_details

        if repo.add_payment(order_id, payment_data):
            # Auto-confirm DRAFT orders when first payment is received
            # This fixes the "stuck in DRAFT+PARTIAL" lifecycle issue
            refreshed = repo.find_by_id(order_id)
            auto_confirmed = False
            if refreshed and refreshed.get("status") == "DRAFT":
                repo.update_status(order_id, "CONFIRMED", current_user.get("user_id"))
                auto_confirmed = True

            return {
                "payment_id": payment_data["payment_id"],
                "message": "Payment recorded"
                + (" — order auto-confirmed" if auto_confirmed else ""),
                "amount": payment.amount,
                "order_status": (
                    "CONFIRMED"
                    if auto_confirmed
                    else refreshed.get("status") if refreshed else "DRAFT"
                ),
                "payment_status": (
                    refreshed.get("payment_status") if refreshed else "PARTIAL"
                ),
            }

        raise HTTPException(status_code=500, detail="Failed to add payment")

    return {"payment_id": str(uuid.uuid4()), "message": "Payment recorded"}


@router.post("/{order_id}/ready")
async def mark_ready(order_id: str, current_user: dict = Depends(get_current_user)):
    """Mark order as ready for delivery"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        if not validate_status_transition(order.get("status", ""), "READY"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot mark as ready — current status is {order.get('status')}. Valid transitions: {', '.join(VALID_TRANSITIONS.get(order.get('status', ''), set()))}",
            )

        if repo.update_status(order_id, "READY", current_user.get("user_id")):
            return {
                "order_id": order_id,
                "status": "READY",
                "message": "Order marked as ready",
            }

        raise HTTPException(status_code=500, detail="Failed to update order status")

    return {"order_id": order_id, "status": "READY"}


@router.post("/{order_id}/deliver")
async def deliver_order(order_id: str, current_user: dict = Depends(get_current_user)):
    """Deliver order to customer"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        if not validate_status_transition(order.get("status", ""), "DELIVERED"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot deliver — current status is {order.get('status')}. Must be READY.",
            )

        # Check payment status (allow partial for B2B customers)
        payment_status = order.get("payment_status", "UNPAID")
        if payment_status == "UNPAID":
            raise HTTPException(
                status_code=400,
                detail="Order must have at least partial payment before delivery",
            )

        if repo.update_status(order_id, "DELIVERED", current_user.get("user_id")):
            return {
                "order_id": order_id,
                "status": "DELIVERED",
                "message": "Order delivered",
            }

        raise HTTPException(status_code=500, detail="Failed to deliver order")

    return {"order_id": order_id, "status": "DELIVERED"}


@router.post("/{order_id}/cancel")
async def cancel_order(
    order_id: str,
    reason: str = Query(..., min_length=10),
    current_user: dict = Depends(get_current_user),
):
    """Cancel order"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        if order.get("status") == "DELIVERED":
            raise HTTPException(
                status_code=400, detail="Cannot cancel delivered orders"
            )

        if order.get("status") == "CANCELLED":
            raise HTTPException(status_code=400, detail="Order is already cancelled")

        # Update status and add cancellation reason
        repo.update(
            order_id,
            {
                "status": "CANCELLED",
                "cancellation_reason": reason,
                "cancelled_by": current_user.get("user_id"),
                "cancelled_at": datetime.now().isoformat(),
            },
        )

        # Branch B' sub-PR 4 -- release any lens-stock reservations on
        # the cancelled order so the cells return to AVAILABLE. Fully
        # fail-soft: a release that can't go through (commit already
        # happened, lens already cut) is logged but never blocks the
        # cancel response.
        try:
            from ..services.lens_stock_hook import release_for_cancel

            items_for_release = order.get("items") or []
            for idx, oi in enumerate(items_for_release):
                try:
                    await release_for_cancel(
                        order_item=oi,
                        order_id=order_id,
                        line_index=idx,
                        store_id=order.get("store_id") or "",
                        user=current_user,
                    )
                except Exception as rel_exc:  # noqa: BLE001
                    logger.warning(
                        "[LENS_HOOK] release on cancel failed "
                        "(order %s line %s): %s",
                        order_id, idx, rel_exc,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[LENS_HOOK] cancel release outer error %s: %s",
                order_id, exc,
            )

        # Audit alert (May 2026) — every cancellation is CRITICAL severity
        try:
            from ..services.audit_alerts import alert_order_cancelled
            import asyncio as _aio

            _aio.create_task(
                alert_order_cancelled(
                    order_id,
                    before=order,
                    user_id=current_user.get("user_id"),
                    reason=reason,
                )
            )
        except Exception:
            pass

        return {
            "order_id": order_id,
            "status": "CANCELLED",
            "message": "Order cancelled",
        }

    return {"order_id": order_id, "status": "CANCELLED"}


@router.get("/{order_id}/invoice")
async def get_invoice(order_id: str, current_user: dict = Depends(get_current_user)):
    """Get/generate invoice for order"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        if order.get("status") == "DRAFT":
            raise HTTPException(
                status_code=400, detail="Cannot generate invoice for DRAFT orders"
            )

        # GST compliance: store must have GSTIN configured before generating invoice
        store_id = order.get("store_id") or current_user.get("active_store_id")
        if store_id:
            try:
                from ..dependencies import get_store_repository

                store_repo = get_store_repository()
                if store_repo:
                    store = store_repo.find_by_id(store_id)
                    if store and not store.get("gstin"):
                        raise HTTPException(
                            status_code=400,
                            detail="Cannot generate invoice: store GSTIN is not configured. "
                            "Update store settings with a valid GSTIN first.",
                        )
            except HTTPException:
                raise
            except Exception:
                pass  # don't block invoice if store lookup fails

        # Return existing invoice or generate new one
        invoice_number = order.get("invoice_number")
        if not invoice_number:
            year = datetime.now().year
            short_id = order.get("order_id", "")[:8].upper()
            invoice_number = f"BV/INV/{year}/{short_id}"
            repo.set_invoice(order_id, invoice_number)

        # Convert items to camelCase
        items_formatted = [item_to_frontend(item) for item in order.get("items", [])]
        return {
            "invoiceNumber": invoice_number,
            "orderId": order_id,
            "orderNumber": order.get("order_number"),
            "customerName": order.get("customer_name"),
            "grandTotal": order.get("grand_total"),
            "amountPaid": order.get("amount_paid"),
            "balanceDue": order.get("balance_due"),
            "items": items_formatted,
            "invoiceDate": order.get("invoice_date") or datetime.now().isoformat(),
        }

    return {"invoiceNumber": "BV/INV/2024/0001", "orderId": order_id}
