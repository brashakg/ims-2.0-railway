"""
IMS 2.0 - Orders Router
========================
Sales order management endpoints
"""

from fastapi import APIRouter, HTTPException, Depends, Query, Header
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Any, Dict, List, Optional
from datetime import datetime, date, timedelta
from enum import Enum
import math
import uuid
import secrets

import logging

logger = logging.getLogger(__name__)

from .auth import get_current_user
from ..dependencies import (
    get_order_repository,
    get_customer_repository,
    get_stock_repository,
    get_product_repository,
    get_walkin_counter_repository,
    validate_store_access,
)

# BUG-005 / BUG-006 (patient-safety): the POS / order-create path must run the
# SAME clinical Rx-power validation the clinical paths use, and must not let a
# spectacle-lens / contact-lens line be ordered without a prescription. Reuse
# the canonical shared validators (NOT a re-derivation of the limits).
from ..services.rx_validation import (
    _validate_rx_number as _validate_rx_power,
    _validate_axis as _validate_rx_axis,
    is_rx_required_line as _is_rx_required_line,
)


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


# Discount caps (category + luxury brand) come from the canonical
# api.services.pricing_caps -- NEVER re-implement them here. The old local
# table under-capped PREMIUM (5% vs 20%) / MASS (10% vs 15%) / LUXURY (2% vs 5%)
# and applied no luxury BRAND cap at all, contradicting SYSTEM_INTENT 3 and
# blocking legitimate discounts at POS.

# Roles permitted to create / modify POS orders. Excludes ACCOUNTANT,
# CATALOG_MANAGER, OPTOMETRIST, WORKSHOP_STAFF (out of POS scope) and INVESTOR
# (read-only, also blocked by middleware). CASHIER is payment-only -> may record
# payments but not create orders, so it is intentionally NOT in this set.
POS_WRITE_ROLES = (
    "SUPERADMIN",
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "SALES_CASHIER",
    "SALES_STAFF",
)

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
from ..services.gst_rates import resolve_gst_rate, gst_pricing_mode

# LOW_GST_CATEGORIES retained for any external reference / readability; it is
# the set of categories the canonical table bills at 5%.
from ..services.gst_rates import GST_CATEGORY_TABLE as _GST_CATEGORY_TABLE

# _normalize_category maps the many category spellings (FRAMES / FR / "frame")
# to the canonical hint used as a GST_CATEGORY_TABLE key. Used by the C-2 guard
# below to tell a KNOWN category from a junk/typo one.
from ..services.gst_rates import _normalize_category as _normalize_gst_category

LOW_GST_CATEGORIES = {
    cat for cat, (_hsn, rate) in _GST_CATEGORY_TABLE.items() if rate == 5.0
}


def _is_known_gst_category(value) -> bool:
    """True if `value` resolves to a real GST_CATEGORY_TABLE entry (not the
    optical-dominant DEFAULT_GST_RATE fallback). Mirrors the normalisation
    resolve_gst_rate() uses, so 'WATCH' / 'WT' / 'frames' all count as known
    while a junk string like 'FOOBAR' does not. Used by C-2 to decide whether
    a provided category is trustworthy or we must fall back to item_type."""
    if not value:
        return False
    norm = _normalize_gst_category(value)
    if norm in _GST_CATEGORY_TABLE:
        return True
    # Defensive: also accept the plain upper form (covers any table key that
    # is not itself in the _CATEGORY_HINT map, e.g. SMARTGLASSES / WALL_CLOCK).
    return str(value).strip().upper() in _GST_CATEGORY_TABLE


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
    mode = gst_pricing_mode()  # "inclusive" (default) | "exclusive" — flag-flippable
    subtotal = 0.0
    gross_total = 0.0
    item_discount_sum = 0.0
    per_rate_taxable: dict = {}
    per_rate_tax: dict = {}
    for it in items or []:
        line_subtotal = float(it.get("item_total") or 0.0)
        subtotal += line_subtotal
        item_discount_sum += float(it.get("discount_amount") or 0.0)
        # C-2: `item_type` is AUTHORITATIVE for GST. Resolution order for a line:
        #   1. explicit per-item HSN / gst_rate    (most authoritative -- handled
        #      inside resolve_gst_rate, which checks the HSN before any category)
        #   2. the item_type's table rate          (when item_type maps to a known
        #      GST_CATEGORY_TABLE entry -- WINS even over a VALID `category`)
        #   3. the category's table rate           (when item_type is unknown)
        #   4. the optical-dominant DEFAULT          (both unknown)
        # Rationale: item_type is the line's true tax nature at POS. A
        # SUNGLASS sold under a FRAMES category must bill 18% (the sunglass
        # rate), not 5% -- the catalog `category` is a merchandising bucket and
        # must not undercharge GST. An explicit HSN still trumps everything
        # because resolve_gst_rate consults `by_hsn` first.
        item_type_val = it.get("item_type") or ""
        if _is_known_gst_category(item_type_val):
            cat = item_type_val
        else:
            cat = it.get("category") or item_type_val or ""
        hsn = it.get("hsn_code") or it.get("hsn") or None
        rate = resolve_gst_rate(hsn_code=hsn, category=cat)
        # GST mode (GST_PRICING_MODE, per-request):
        #   inclusive (default) — item_total IS the all-in price; the GST is the
        #     component WITHIN it: taxable = gross/(1+rate); tax = gross-taxable.
        #     (QA F3 / owner: the counter price is inclusive.)
        #   exclusive (legacy)  — item_total is the pre-tax taxable; GST on top.
        # The flag lets the mode be flipped on Railway without a redeploy (instant
        # atomic rollback). taxable + tax == grand_total in BOTH modes.
        line_gross = round(line_subtotal * cart_factor, 2)
        gross_total += line_gross
        if mode == "exclusive":
            line_taxable = line_gross
            line_tax = round(line_gross * (rate / 100.0), 2)
        else:
            line_taxable = round(line_gross / (1.0 + rate / 100.0), 2)
            line_tax = round(line_gross - line_taxable, 2)
        per_rate_taxable[rate] = round(
            per_rate_taxable.get(rate, 0.0) + line_taxable, 2
        )
        per_rate_tax[rate] = round(per_rate_tax.get(rate, 0.0) + line_tax, 2)
        it["gst_rate"] = rate
        it["taxable_value"] = line_taxable
        it["tax_amount"] = line_tax
    taxable = round(sum(per_rate_taxable.values()), 2)
    tax = round(sum(per_rate_tax.values()), 2)
    # grand_total (caller: taxable + tax) now equals the gross the customer pays.
    cart_discount_amount = (
        round(subtotal - round(gross_total, 2), 2) if cart_discount_pct > 0 else 0.0
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
        "pricing_model": mode,
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
    # Loyalty-points redemption recorded as an internal tender. The points are
    # already atomically debited by POST /loyalty/redeem BEFORE this payment is
    # recorded, so add_payment does NOT re-redeem -- it just records the rupee
    # value so it counts toward amount_paid (non-CREDIT) and reduces balance_due.
    # Previously absent from the enum: the POS LOYALTY tender 422'd and was
    # swallowed, so the customer's points were burned yet the order still showed
    # that amount as owing (a double charge).
    LOYALTY = "LOYALTY"


class OrderItemCreate(BaseModel):
    item_type: str  # FRAME, LENS, CONTACT_LENS, ACCESSORY, SERVICE
    product_id: str
    # POS-9: length cap on product_name — receipts/Tally/reports truncate or
    # 500 on very long names; 200 chars covers the widest real product name.
    product_name: Optional[str] = Field(None, max_length=200)
    sku: Optional[str] = None
    brand: Optional[str] = None
    subbrand: Optional[str] = None
    category: Optional[str] = None
    # C-3: GENEROUS upper bounds that can never reject a real optical order but
    # stop a malicious/garbage payload (e.g. unit_price=1.7e308 or quantity=1e9)
    # from overflowing unit_price*quantity to Infinity and 500ing on JSON
    # serialisation. 1 crore per unit / 1000 units is far above any real line.
    quantity: int = Field(default=1, ge=1, le=1000)
    unit_price: float = Field(..., ge=0, le=10_000_000)
    discount_percent: float = Field(default=0, ge=0, le=100)
    # C-4 (DELTA 2): per-line discount accountability. The POS already sends
    # these (posStore CartLineItem.discount_approved_by / discount_reason);
    # they were silently dropped before. Captured here so a 100%-line-discount
    # order can carry its own approver + reason for the required-approval gate.
    discount_approved_by: Optional[str] = None
    # POS-9: cap discount_reason at 200 chars (free text, shown on audit rows).
    discount_reason: Optional[str] = Field(None, max_length=200)
    prescription_id: Optional[str] = None
    lens_options: Optional[dict] = None  # coating, tint, etc.
    lens_details: Optional[dict] = None  # type, material, coatings
    # POS-10: per-line staff note (e.g. "wrap bifocal, tight frame").
    # Persisted on the order item so workshop + invoice can display it.
    # POS-9: capped at 200 chars to protect receipts/Tally from runaway text.
    item_note: Optional[str] = Field(None, max_length=200)
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
    # BUG-005: cylinder axis (whole degree 1-180). Required when cyl is non-zero
    # (a toric lens is un-grindable without an axis). Optional so a sphere-only /
    # frame-only line is unaffected. Enforced by _validate_order_line_rx, not a
    # field_validator, because the cyl-requires-axis rule is cross-field.
    axis: Optional[int] = None

    @field_validator("unit_price")
    @classmethod
    def _unit_price_finite(cls, v: float) -> float:
        # C-3: explicitly reject NaN / +-Infinity so a non-finite price can
        # never reach the money math (the le/ge bounds already catch these,
        # but this is the contract the bound enforces -- belt and braces).
        if not math.isfinite(v):
            raise ValueError("unit_price must be a finite number")
        return v


class PaymentCreate(BaseModel):
    # Accept both `method` (canonical) and `mode` (legacy, still used by
    # the Orders-page Collect Payment modal). pydantic aliasing means the
    # request can send either — we canonicalize on the model.
    method: PaymentMethod = Field(..., validation_alias="method")
    # BUG-108 residual: gt=0 already rejects NaN/<=0; the le bound additionally
    # rejects +Infinity so a payment can't store inf on the order doc.
    amount: float = Field(..., gt=0, le=100_000_000)
    reference: Optional[str] = None
    # Gift-voucher code (only used when method=GIFT_VOUCHER). The POS sends
    # both `reference` and `voucher_code` set to the card code; we prefer
    # this explicit field and fall back to `reference` for older callers.
    voucher_code: Optional[str] = None
    # EMI-specific fields (only required when method=EMI)
    emi_months: Optional[int] = Field(None, ge=3, le=24)
    emi_provider: Optional[str] = None  # e.g., "BAJAJ", "HDFC", "ICICI"
    # POS-2: the loan principal (order_total - down_payment). When provided,
    # build_emi_schedule uses this amount so the schedule reflects the full
    # financed balance, not just the down-payment recorded in `amount`.
    emi_principal: Optional[float] = Field(None, gt=0, le=100_000_000)

    model_config = ConfigDict(populate_by_name=True)

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
    # POS-9: server-side cap on cart notes. 500 chars is generous for an optical
    # note; beyond that the field can corrupt receipt/Tally line widths or store
    # XSS/null-byte payloads. Enforced alongside the 200-char item_note cap.
    notes: Optional[str] = Field(None, max_length=500)
    # POS-10: order_type (sale_type from posStore: 'quick_sale' | 'full_sale')
    # was silently dropped on create. Persist it so reports/audit can distinguish
    # a quick-POS sale from a full workshop-linked order.
    order_type: Optional[str] = Field(None, max_length=50)
    expected_delivery_days: int = Field(default=7, ge=1)
    # Phase 6.7 — delivery scheduling + order-level discount
    delivery_date: Optional[date] = None
    delivery_time_slot: Optional[str] = None  # e.g. "10:00-12:00"
    delivery_priority: Optional[str] = Field(
        default="NORMAL"
    )  # NORMAL | EXPRESS | URGENT
    cart_discount_percent: float = Field(default=0.0, ge=0.0, le=100.0)
    cart_discount_amount: float = Field(default=0.0, ge=0.0)
    # POS-9: cap cart-level discount reason (same 200-char limit as item reasons).
    cart_discount_reason: Optional[str] = Field(None, max_length=200)
    cart_discount_approved_by: Optional[str] = None
    # Incentive integration — explicit salesperson attribution (POS picker)
    # and the Visufit measurement ID for the per-staff Visufit coverage gate.
    salesperson_id: Optional[str] = None
    salesperson_name: Optional[str] = None
    visufit_id: Optional[str] = None

    @field_validator("delivery_priority")
    @classmethod
    def _validate_delivery_priority(cls, v: Optional[str]) -> Optional[str]:
        # C-7: only the three known priorities (matches posStore +
        # the FE priority select). Absent/None is allowed (defaults NORMAL on
        # the order doc). Reject any other string with a clean 422.
        if v is None:
            return v
        allowed = {"NORMAL", "EXPRESS", "URGENT"}
        upper = str(v).strip().upper()
        if upper not in allowed:
            raise ValueError("delivery_priority must be one of NORMAL, EXPRESS, URGENT")
        return upper

    @field_validator("delivery_date")
    @classmethod
    def _validate_delivery_date_not_past(cls, v: Optional[date]) -> Optional[date]:
        # C-8: a delivery cannot be scheduled in the past. Today is allowed.
        # Absent/None is allowed (falls back to expected_delivery_days).
        if v is not None and v < date.today():
            raise ValueError("delivery_date cannot be in the past")
        # POS operational-wins: reject an absurd far-future date (fat-finger like
        # 2099-12-31) that would create an order that never fulfils. 365 days is
        # far beyond any real optical job (lab turnaround is days, not months).
        if v is not None and v > date.today() + timedelta(days=365):
            raise ValueError("delivery_date cannot be more than 365 days out")
        return v


class OrderUpdate(BaseModel):
    notes: Optional[str] = None
    expected_delivery: Optional[date] = None


# ============================================================================
# Build item #16 — SUPERADMIN post-creation order edit (revenue/GST/audit)
# ============================================================================
class SuperadminEditLine(BaseModel):
    """One line in a SUPERADMIN order edit. Mirrors the persisted order-line
    shape (so an existing line can be edited in place by keeping its item_id,
    or a new line added by omitting it). GST is resolved server-side from
    item_type / category / hsn_code -- the client never sets the rate."""

    item_id: Optional[str] = None
    item_type: str
    product_id: Optional[str] = None
    product_name: Optional[str] = Field(None, max_length=200)
    sku: Optional[str] = None
    brand: Optional[str] = None
    subbrand: Optional[str] = None
    category: Optional[str] = None
    hsn_code: Optional[str] = None
    quantity: int = Field(default=1, ge=1, le=1000)
    unit_price: float = Field(..., ge=0, le=10_000_000)
    discount_percent: float = Field(default=0, ge=0, le=100)
    cost_at_sale: Optional[float] = None
    prescription_id: Optional[str] = None
    lens_options: Optional[dict] = None
    lens_details: Optional[dict] = None
    item_note: Optional[str] = Field(None, max_length=200)
    sph: Optional[float] = None
    cyl: Optional[float] = None
    add: Optional[float] = None
    axis: Optional[int] = None

    @field_validator("unit_price")
    @classmethod
    def _unit_price_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("unit_price must be a finite number")
        return v


class SuperadminOrderEdit(BaseModel):
    """Pre-invoice SUPERADMIN edit payload. A non-empty ``reason`` is mandatory
    (the edit writes an immutable audit row). ``items`` replaces the whole line
    set when provided; ``customer_id`` / ``customer_name`` reassign the order's
    customer when provided; ``cart_discount_percent`` re-applies the order-level
    discount."""

    reason: str = Field(..., min_length=4, max_length=500)
    items: Optional[List[SuperadminEditLine]] = None
    customer_id: Optional[str] = None
    customer_name: Optional[str] = Field(None, max_length=200)
    cart_discount_percent: Optional[float] = Field(None, ge=0.0, le=100.0)
    cart_discount_reason: Optional[str] = Field(None, max_length=200)
    notes: Optional[str] = Field(None, max_length=500)


class SuperadminInvoiceChange(BaseModel):
    """Post-issue SUPERADMIN correction. ``mode`` chooses between a REVISED
    invoice (new serial; original marked superseded/void) and a CREDIT/DEBIT
    note (delta linked to the original invoice, original left intact). The
    SUPERADMIN supplies the corrected lines / customer / cart discount exactly
    as in the pre-invoice edit; the delta is derived server-side."""

    mode: str  # REVISED_INVOICE | CREDIT_NOTE
    reason: str = Field(..., min_length=4, max_length=500)
    items: Optional[List[SuperadminEditLine]] = None
    customer_id: Optional[str] = None
    customer_name: Optional[str] = Field(None, max_length=200)
    cart_discount_percent: Optional[float] = Field(None, ge=0.0, le=100.0)
    cart_discount_reason: Optional[str] = Field(None, max_length=200)

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        upper = str(v or "").strip().upper()
        if upper not in ("REVISED_INVOICE", "CREDIT_NOTE"):
            raise ValueError("mode must be REVISED_INVOICE or CREDIT_NOTE")
        return upper


# ============================================================================
# Rx validation for order lines (BUG-005 patient-safety + BUG-006 Rx-required)
# ============================================================================
# Roles permitted to override an EXPIRED prescription (Store-Manager and up).
# A SALES_STAFF / SALES_CASHIER must escalate; HQ roles + the store manager may
# proceed on an expired Rx (a deliberate, audited clinical decision).
_RX_EXPIRY_OVERRIDE_ROLES = (
    "SUPERADMIN",
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
)


def _validate_order_line_rx(item, customer_id: str, current_user: dict) -> None:
    """Validate ONE order line's clinical Rx data. Raises HTTPException(422) on a
    bad/missing value -- never 500s. Pure validation; touches no pricing/GST math.

    BUG-005: range / 0.25-step / whole-axis / cyl-requires-axis check on the
             line's sph/cyl/add/axis via the SAME canonical clinical validators.
    BUG-006: a SPECTACLE (Rx) lens line must carry a prescription_id that
             resolves to a real prescription for THIS customer; an expired Rx is
             allowed only for Store-Manager+. CONTACT LENSES are EXEMPT from this
             hard gate (owner policy 2026-06-18 "block Rx lenses, allow contacts").
    Frame-only / contact-lens / non-Rx lines are not Rx-gated -- but their
    powers (if any) are STILL range-checked above (BUG-005 is universal).
    """
    name = getattr(item, "product_name", None) or getattr(item, "product_id", "") or "item"

    # --- BUG-005: power-value validation (range + 0.25 grid + axis rules) ------
    sph = getattr(item, "sph", None)
    cyl = getattr(item, "cyl", None)
    add = getattr(item, "add", None)
    axis = getattr(item, "axis", None)
    try:
        _validate_rx_power(sph, "sph")
        _validate_rx_power(cyl, "cyl")
        _validate_rx_power(add, "add")
        # AXIS: whole 1-180, and MANDATORY when cyl is non-zero.
        _validate_rx_axis(axis, cyl=cyl)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid prescription power on '{name}': {exc}",
        )

    # --- BUG-006: Rx-required for SPECTACLE-lens lines (contacts EXEMPT) -------
    item_type = getattr(item, "item_type", None)
    category = getattr(item, "category", None)
    if not _is_rx_required_line(item_type, category):
        return  # frame / sunglass / accessory / service / CONTACT-LENS -> no Rx needed

    rx_id = (getattr(item, "prescription_id", None) or "").strip()
    if not rx_id:
        raise HTTPException(
            status_code=422,
            detail=(
                f"'{name}' is a prescription lens and requires a linked "
                f"prescription. Select the customer's Rx before billing."
            ),
        )

    # Resolve the Rx and confirm it belongs to THIS customer. Fail-SOFT on a
    # missing/unavailable prescription repo (do not 500 / do not block billing
    # when the clinical store is down) -- the prescription_id presence check
    # above already enforces the core BUG-006 requirement.
    try:
        from ..dependencies import get_prescription_repository

        rx_repo = get_prescription_repository()
    except Exception:  # noqa: BLE001
        rx_repo = None
    if rx_repo is None:
        return

    try:
        rx = rx_repo.find_by_id(rx_id)
    except Exception:  # noqa: BLE001
        return  # repo error -> fail-soft, don't block the sale

    if rx is None:
        raise HTTPException(
            status_code=422,
            detail=f"Prescription '{rx_id}' for '{name}' was not found.",
        )
    # The Rx must belong to the order's customer (no cross-customer Rx).
    rx_customer = rx.get("customer_id") or rx.get("customerId")
    if customer_id and rx_customer and str(rx_customer) != str(customer_id):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Prescription '{rx_id}' does not belong to this customer; "
                f"select a prescription for the billed customer."
            ),
        )

    # Expiry: an expired Rx may only be dispensed by Store-Manager+ (override).
    try:
        from .prescriptions import _rx_validity

        _expiry, is_valid = _rx_validity(rx)
    except Exception:  # noqa: BLE001
        is_valid = None  # can't compute -> don't block (fail-soft)
    if is_valid is False:
        roles = current_user.get("roles", []) or []
        if not any(r in roles for r in _RX_EXPIRY_OVERRIDE_ROLES):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Prescription '{rx_id}' for '{name}' has EXPIRED. A Store "
                    f"Manager or higher must approve dispensing on an expired Rx."
                ),
            )


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
    limit: int = Query(50, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
):
    """List orders with filters"""
    repo = get_order_repository()
    active_store = validate_store_access(store_id, current_user)

    if repo is not None:
        if customer_id:
            orders = repo.find_by_customer(customer_id, limit=limit)
            # Store-scope: a store-level role must not enumerate a customer's
            # orders from OTHER stores via ?customer_id (cross-store IDOR).
            # Cross-store roles (ADMIN/AREA_MANAGER/SUPERADMIN) are unaffected.
            from ..dependencies import filter_docs_by_store

            orders = filter_docs_by_store(orders, current_user)
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
    active_store = validate_store_access(store_id, current_user)

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
    active_store = validate_store_access(store_id, current_user)

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
    active_store = validate_store_access(store_id, current_user)

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
    active_store = validate_store_access(store_id, current_user)

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
    active_store = validate_store_access(store_id, current_user)

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
    active_store = validate_store_access(store_id, current_user)

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


def build_emi_schedule(principal: float, annual_rate: float, months: int) -> dict:
    """Reconcile an EMI plan so the installments sum EXACTLY to total_payable.

    P3-C. The equal monthly installment (standard amortization formula) is
    rounded to paise for display, but `monthly_emi * months` then drifts from
    the true cost of credit by up to a few paise. A customer paying N equal
    rounded installments would under/over-pay the principal+interest.

    Fix: `total_payable` is the AUTHORITATIVE total (unrounded EMI x months,
    rounded once to paise). The schedule pays `monthly_emi` for the first
    (months - 1) installments and a `last_installment` that absorbs the
    rounding remainder, so:

        monthly_emi * (months - 1) + last_installment == total_payable   (exact)

    `interest_amount = total_payable - principal`. All values are display /
    documentation only -- the recorded payment amount (and therefore what
    reduces balance_due) is unchanged elsewhere.

    Returns the dict embedded as `emi_details` (minus provider, which the
    caller adds).
    """
    months = int(months)
    principal = float(principal)
    monthly_rate = annual_rate / 12 / 100
    if monthly_rate > 0:
        emi_amount = (
            principal
            * monthly_rate
            * (1 + monthly_rate) ** months
            / ((1 + monthly_rate) ** months - 1)
        )
    else:
        emi_amount = principal / months

    monthly_emi = round(emi_amount, 2)
    # Authoritative total cost of credit (rounded once from the exact EMI).
    total_payable = round(emi_amount * months, 2)
    # Last installment absorbs the accumulated rounding so the schedule sums
    # to total_payable to the paisa. round() tames float noise (e.g. a
    # 4999.999999999 -> 5000.00).
    last_installment = round(total_payable - monthly_emi * (months - 1), 2)
    interest_amount = round(total_payable - principal, 2)

    return {
        "tenure_months": months,
        "annual_rate": annual_rate,
        "monthly_emi": monthly_emi,
        "last_installment": last_installment,
        "total_payable": total_payable,
        "interest_amount": interest_amount,
    }


def _order_create_response(order: dict) -> dict:
    """The POST /orders success envelope. Shared by a fresh create and the C-5
    idempotency replay so a duplicated request gets a byte-identical response.
    Reads from a persisted order doc (snake_case)."""
    return {
        "order_id": order.get("order_id"),
        "order_number": order.get("order_number"),
        "status": order.get("status") or "DRAFT",
        "grand_total": order.get("grand_total"),
        "message": "Order created successfully",
    }


def _resolve_product_doc(product_repo, pid: str):
    """Tolerant product existence lookup for order-create.

    Imported products are often referenced by their Mongo `_id` or `sku`
    rather than `product_id` (the same tolerance the admin catalog uses).
    Tries product_id, then sku, then _id (string or ObjectId). Returns the
    product doc or None. Existence only — pricing comes from the order payload.
    """
    if not pid:
        return None
    # None-safe: the live seeded-catalog path calls this with product_repo=None
    # (products live in catalog_products, resolved below). Only touch the products
    # repo when it is actually present.
    if product_repo is not None:
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
            if product is not None:
                return product
        except Exception:
            product = None
    # C-1: seeded catalog products live in the `catalog_products` collection
    # (served by GET /catalog/products), NOT in `products`. When the lookup
    # above misses, fall back to catalog_products by the same id so a
    # catalog-only product can still be ordered. Fail-soft: any error -> None.
    return _resolve_catalog_product_doc(pid)


def _canonical_pid(product_repo, pid: str) -> str:
    """NEW-ORDER-PRODUCTID-STAMP: resolve a client-supplied product reference
    (which may be a SKU or a Mongo _id, e.g. for imported/catalog products) to
    the catalog's CANONICAL product_id, so persisted order lines reconcile
    against the catalog instead of storing a raw SKU. Virtual skus
    (custom-/lens-/lens-sug-) and unresolvable ids are returned unchanged."""
    if not pid or pid.startswith(("custom-", "lens-", "lens-sug-")):
        return pid
    try:
        doc = _resolve_product_doc(product_repo, pid)
    except Exception:  # noqa: BLE001 -- resolution is best-effort
        doc = None
    if not doc:
        return pid
    return str(doc.get("id") or doc.get("product_id") or pid)


def _get_catalog_collection():
    """Return the `catalog_products` Mongo collection, or None if the DB is
    unavailable. Mirrors catalog.py's accessor; module-level + import-light so
    tests can monkeypatch it. Fail-soft."""
    try:
        from ..dependencies import get_db

        db = get_db()
        if db is not None and getattr(db, "is_connected", False):
            return db.get_collection("catalog_products")
    except Exception:  # noqa: BLE001
        pass
    return None


def _resolve_catalog_product_doc(pid: str):
    """C-1 fallback: look a product up in `catalog_products` by id/sku and map
    its (nested) shape to the flat fields the order-create path reads
    (product_id, name, mrp/offer_price/cost_price, category, gst_rate/hsn_code,
    discount_category, item_type). Returns the mapped dict or None. Fail-soft.

    The catalog doc stores pricing under a nested `pricing` block and the name
    under `title`; the order path reads flat `cost_price` (COGS snapshot) and
    `discount_category` (category-cap), so we surface those at the top level.
    """
    if not pid:
        return None
    coll = _get_catalog_collection()
    if coll is None:
        return None
    try:
        doc = coll.find_one({"$or": [{"id": pid}, {"sku": pid}, {"_id": pid}]})
    except Exception:  # noqa: BLE001
        return None
    if not doc:
        return None
    pricing = doc.get("pricing") or {}
    category = doc.get("category")
    mapped = {
        "product_id": doc.get("id") or pid,
        "id": doc.get("id") or pid,
        "sku": doc.get("sku"),
        "name": doc.get("title") or doc.get("name"),
        "model": doc.get("title") or doc.get("name"),
        "category": category,
        "item_type": category,
        "hsn_code": doc.get("hsn_code"),
        "gst_rate": doc.get("gst_rate"),
        "mrp": pricing.get("mrp"),
        "offer_price": pricing.get("offer_price"),
        "cost_price": pricing.get("cost_price"),
        "discount_category": pricing.get("discount_category"),
        # brand is needed for the luxury-brand discount cap (Cartier 2% etc.).
        # The catalog doc may hold it flat or under pricing; surface either.
        "brand": doc.get("brand") or pricing.get("brand"),
        "is_active": doc.get("is_active", True),
        # Mark the source so any future caller can tell a catalog-resolved
        # product from a `products` one without re-querying.
        "_resolved_from": "catalog_products",
    }
    return mapped


# Item types / product_id prefixes that have NO serialized stock to mark sold.
# SERVICE = labour line (eg fitting); EYE_TEST/etc = a clinical consultation line
# (GST-exempt SAC 9993, see gst_rates.py) -- a service, not a stocked good;
# custom-/lens-/lens-sug- = virtual POS items the configurator/suggestion helper
# generates on the fly. These never carry a stock_unit row, so trying to
# mark_sold them is a no-op (not an error).
_VIRTUAL_PID_PREFIXES = ("custom-", "lens-", "lens-sug-")
_NON_SERIALIZED_ITEM_TYPES = {
    "SERVICE",
    "EYE_TEST",
    "EYE_EXAM",
    "EYE_CHECKUP",
    "CONSULT",
    "CONSULTATION",
    "OPTOMETRY",
}

# Item types whose stock is reserved by the LENS hook (reserve_for_order_item),
# NOT by the serialized stock_units availability gate below.
_LENS_RESERVED_ITEM_TYPES = {"LENS"}


def _assert_serialized_stock_available(
    items_data: List[dict], store_id: Optional[str]
) -> None:
    """BUG-097: reject order creation (409) when a SERIALIZED non-lens line does
    not have enough AVAILABLE units in stock_units -- closes the non-lens oversell
    where _mark_units_sold silently continued on 0 available.

    Only enforced for a product that IS serialized-tracked at this store
    (count(any status) > 0); a product with no stock_units row (a service, a
    virtual item, or simply not unit-tracked) is left UNAFFECTED so a legit sale is
    never false-blocked. Lens lines are gated by the lens reserve; a line carrying
    an explicit stock_id flows through the mark_sold path.

    NOTE: this is a pre-persist availability ASSERT -- a strict improvement over
    the silent oversell, but check-then-act, so two highly-concurrent orders for
    the last unit can still both pass. A fully-atomic reserve (find_one_and_update
    with a status=AVAILABLE filter, mirroring the lens hook) is the follow-up.
    """
    if not store_id or not items_data:
        return
    try:
        stock_repo = get_stock_repository()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STOCK] availability assert: repo unavailable: %s", exc)
        stock_repo = None
    if stock_repo is None:
        return  # no stock backend -> fail-soft (pre-existing behaviour)

    need: Dict[str, int] = {}
    for line in items_data:
        if not isinstance(line, dict):
            continue
        it = (line.get("item_type") or "").upper()
        if it in _NON_SERIALIZED_ITEM_TYPES or it in _LENS_RESERVED_ITEM_TYPES:
            continue
        pid = line.get("product_id") or ""
        if not pid or pid.startswith(_VIRTUAL_PID_PREFIXES):
            continue
        if line.get("stock_id"):
            continue  # explicit unit -> handled by mark_sold
        need[pid] = need.get(pid, 0) + int(line.get("quantity") or 1)

    for pid, qty in need.items():
        try:
            tracked = stock_repo.count({"product_id": pid, "store_id": store_id})
        except Exception:  # noqa: BLE001
            tracked = 0
        if not tracked:
            continue  # not serialized-tracked here -> never false-block a sale
        try:
            avail = stock_repo.find_available(pid, store_id)
        except Exception:  # noqa: BLE001
            continue  # availability lookup failed -> fail-soft
        if avail < qty:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Insufficient stock for '{pid}': {avail} available at this "
                    f"store, {qty} requested. Cannot oversell."
                ),
            )


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
                # Path 2: FIFO-allocate from product+store via an ATOMIC claim
                # (find_one_and_update on status=AVAILABLE). Closes the prior
                # check-then-act race where two concurrent last-unit sales both
                # picked AND marked the SAME unit SOLD. `used` excludes units
                # already claimed earlier in THIS order (two lines, same product).
                if not store_id:
                    continue
                try:
                    claimed = stock_repo.claim_one_available(
                        pid, store_id, order_id, used
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[STOCK] claim_one_available(%s,%s) failed: %s",
                        pid,
                        store_id,
                        exc,
                    )
                    claimed = None
                if claimed:
                    sid = str(claimed)
                else:
                    # No AVAILABLE unit to claim: genuinely out of stock (the
                    # pre-persist assert normally catches this) or we lost a
                    # concurrent race for the last unit. Log for ops visibility.
                    logger.warning(
                        "[STOCK] no AVAILABLE unit to claim for %s @ %s (order %s) "
                        "-- possible concurrent sale of the last unit",
                        pid,
                        store_id,
                        order_id,
                    )

            if sid:
                used.add(sid)
                marked.append(sid)
                # E3w-DEFERRED: POS-sell ledger emit needs POS sign-off. The
                # AVAILABLE -> SOLD item_events emit for this revenue-critical
                # path is intentionally NOT wired here; it is owner-gated to a
                # separate item (and the /items/{id}/sell route gates it behind
                # FF_E3_POS_SELL). Do NOT add a record_event call here.

    return marked


@router.post("", status_code=201)
async def create_order(
    order: OrderCreate,
    current_user: dict = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Create new sales order.

    C-5 (DELTA 3): supports an OPTIONAL `Idempotency-Key` request header. When a
    non-empty key is supplied and an order with that key already exists for this
    store, the EXISTING order is returned (same response shape as a fresh
    create) instead of creating a duplicate -- this makes a double-clicked /
    retried "Pay now" safe. The key is persisted on the order doc. Fail-soft:
    with no DB (or no header) the behaviour is identical to before.
    """
    # RBAC: only POS-facing roles may create orders. This was relying on the
    # frontend alone -> ACCOUNTANT / OPTOMETRIST / CATALOG_MANAGER / WORKSHOP_STAFF
    # could all POST an order. Enforce server-side.
    if not any(r in current_user.get("roles", []) for r in POS_WRITE_ROLES):
        raise HTTPException(
            status_code=403, detail="Your role is not permitted to create orders."
        )
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

    # Accounting period lock: cannot create orders in a closed month.
    # IST audit: the lock day must be the IST business day, not the UTC day --
    # date.today() on Railway (UTC) is yesterday between 00:00-05:30 IST, which
    # falsely blocked POS orders for 5.5h on the 1st after a month-lock.
    db = _get_db()
    if db is not None:
        from .finance import check_period_locked
        from ..utils.ist import ist_today

        check_period_locked(db, ist_today())

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
            # Products-convergence ③: billing requires the `products` SPINE. A
            # product that resolves ONLY from catalog_products (no spine row) is
            # not a governed billing master -- fail LOUD instead of silently
            # billing off the catalog (the path the discount-cap could not fully
            # enforce). Post step-10 the catalog door writes the spine, so this
            # only fires for a legacy un-converged catalog-only product; re-save
            # it via Catalog/Add Product to mint its spine row.
            if product.get("_resolved_from") == "catalog_products":
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{item.product_name or pid} exists only in the catalog "
                        f"and has no billing master record. Re-save it via "
                        f"Catalog / Add Product before selling it."
                    ),
                )

    # BUG-005 / BUG-006 (patient-safety): validate every line's clinical Rx
    # powers (range / 0.25 grid / whole-axis / cyl-requires-axis) and require a
    # valid prescription on spectacle-lens / contact-lens lines. Runs BEFORE any
    # money math (validation only -- it never touches pricing/GST/payment) so a
    # clinically impossible lens power or a lens line with no Rx is rejected with
    # a clear 422 before it can be persisted and sent to the lab.
    for item in order.items:
        _validate_order_line_rx(item, order.customer_id, current_user)

    # BUG-119/BUG-118: the real server-side price ceiling / cost floor / discount
    # validation runs in the totals loop below (AFTER the idempotency check, using
    # the catalog MRP/offer/cost snapshot). The OrderItemCreate model carries no
    # mrp/offer_price field, so the old getattr(item,"mrp",0) guard here was always
    # reading 0 and never fired -- a client could set any unit_price.

    if order_repo is not None and customer_repo is not None:
        # C-5 (DELTA 3): order-create idempotency. If the request carries a
        # non-empty Idempotency-Key and an order with that key already exists
        # for this store, return THAT order rather than creating a duplicate.
        # Looked up before any work so a retried "Pay now" is a cheap no-op.
        # Fail-soft: any lookup error falls through to a normal create.
        idem_key = (idempotency_key or "").strip()
        if idem_key:
            try:
                existing = order_repo.find_one(
                    {"idempotency_key": idem_key, "store_id": store_id}
                )
                if existing:
                    return _order_create_response(existing)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[ORDERS] idempotency lookup skipped: %s", exc)

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
        # BUG-119/BUG-118: snapshot the catalog MRP + offer_price per product so
        # the pricing loop can enforce a server-side price ceiling (unit_price may
        # never exceed the product's real MRP, or its offer_price when HQ has
        # already discounted it), a cost floor, and the "no further discount on an
        # HQ-discounted (offer<MRP) item" rule. The client unit_price/discount is
        # never trusted for these. Fail-soft: absent product -> no constraint.
        _mrp_by_pid: Dict[str, float] = {}
        _offer_by_pid: Dict[str, float] = {}

        def _num_or_none(v):
            try:
                f = float(v)
                return f
            except (TypeError, ValueError):
                return None

        try:
            if product_repo is not None:
                _seen_pids: set = set()
                for it in order.items:
                    pid = it.product_id or ""
                    if not pid or pid in _seen_pids:
                        continue
                    if pid.startswith(("custom-", "lens-", "lens-sug-")):
                        continue
                    _seen_pids.add(pid)
                    pdoc = _resolve_product_doc(product_repo, pid)
                    if not pdoc:
                        continue
                    c = _num_or_none(pdoc.get("cost_price"))
                    if c is not None:
                        _cost_by_pid[pid] = c
                    m = _num_or_none(pdoc.get("mrp"))
                    if m is not None and m > 0:
                        _mrp_by_pid[pid] = m
                    o = _num_or_none(pdoc.get("offer_price"))
                    if o is not None and o > 0:
                        _offer_by_pid[pid] = o
        except Exception:
            # Pricing snapshot is fail-soft -- never block order create.
            _cost_by_pid, _mrp_by_pid, _offer_by_pid = {}, {}, {}

        # Calculate totals
        items_data = []
        subtotal = 0.0

        # NEW-ORDER-PRODUCTID-STAMP: resolve each client-supplied product ref
        # (which may be a SKU or _id) to the catalog's canonical product_id ONCE,
        # so the persisted order lines reconcile against the catalog instead of
        # storing a raw SKU. Virtual + unresolvable ids map to themselves.
        _canon_by_pid: Dict[str, str] = {}
        for _it in order.items:
            _ip = _it.product_id or ""
            if _ip and _ip not in _canon_by_pid:
                _canon_by_pid[_ip] = _canonical_pid(product_repo, _ip)

        # Retrieve user discount cap for enforcement.
        # Use the role-aware effective cap helper rather than the raw user
        # document field — that one defaults to 10% even for SUPERADMIN,
        # which was the long-standing "why is my cap 10%" bug.
        from api.services.role_caps import effective_discount_cap

        user_roles = current_user.get("roles", [])
        user_discount_cap = effective_discount_cap(
            user_roles, current_user.get("discount_cap")
        )
        # Only HQ roles bypass discount caps. STORE_MANAGER has a real 20%
        # cap (SYSTEM_INTENT discount matrix) and MUST flow through the
        # effective_cap + category-cap path -- it was incorrectly bypassing.
        is_admin = any(r in user_roles for r in ["SUPERADMIN", "ADMIN"])

        for item in order.items:
            item_total = item.unit_price * item.quantity

            # ---- BUG-119/BUG-118: server-side price integrity (non-virtual) ----
            # Enforce the catalog MRP/offer ceiling, a cost floor, and the
            # "no further discount on an HQ-discounted item" rule. `_eff_disc` is
            # the effective discount = the LARGER of the explicit discount_percent
            # and the discount implied by unit_price vs MRP, so a low unit_price is
            # capped exactly like an explicit discount and cannot bypass the cap.
            _pid = item.product_id or ""
            _eff_disc = item.discount_percent
            if _pid and not _pid.startswith(("custom-", "lens-", "lens-sug-")):
                _mrp = _mrp_by_pid.get(_pid)
                _offer = _offer_by_pid.get(_pid)
                _cost = _cost_by_pid.get(_pid)
                _up = item.unit_price
                _hq_discounted = bool(_offer and _mrp and _offer < _mrp)
                _ceiling = _offer if _hq_discounted else _mrp
                if _ceiling and _up > _ceiling + 1e-6:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"unit_price Rs{_up} exceeds the catalog "
                            f"{'offer price' if _hq_discounted else 'MRP'} "
                            f"Rs{_ceiling} for {item.product_name or _pid}."
                        ),
                    )
                # Cost floor: never sell a PRICED line below cost. A Rs0 line is a
                # free / 100%-discount item gated by the approval requirement (C-4),
                # so it is exempt here.
                if _cost and _cost > 0 and _up > 1e-6 and _up < _cost - 1e-6:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"unit_price Rs{_up} is below cost Rs{_cost} for "
                            f"{item.product_name or _pid}. Contact a manager."
                        ),
                    )
                # BUG-118 (SYSTEM_INTENT s3): an HQ-discounted item (offer<MRP)
                # sells at exactly offer_price -- no further store discount. A lower
                # unit_price OR any explicit discount_percent is a further discount.
                if not is_admin and _hq_discounted and (
                    item.discount_percent > 0 or _up < _offer - 1e-6
                ):
                    logger.warning(
                        "[ORDERS] BUG-118 blocked further discount on HQ-discounted %s by %s",
                        _pid, current_user.get("user_id"),
                    )
                    raise HTTPException(
                        status_code=403,
                        detail=(
                            f"{item.product_name or _pid} is already discounted by HQ "
                            f"(offer Rs{_offer} < MRP Rs{_mrp}); no further store "
                            f"discount is allowed. Contact an administrator to override."
                        ),
                    )
                # Effective discount for the cap check (max of explicit + implied).
                if _mrp and _up < _mrp - 1e-6:
                    _implied = (_mrp - _up) / _mrp * 100.0
                    _eff_disc = max(item.discount_percent, _implied)

            # Enforce discount cap (admins bypass) -- against the EFFECTIVE discount
            effective_cap = user_discount_cap
            if not is_admin and _eff_disc > 0:
                # Look up category cap for this product — read from the
                # products collection (same fix as the existence-check
                # above; the original code hit stock_units and always
                # returned None, leaving effective_cap = user's full cap).
                try:
                    pr = get_product_repository()
                    if (
                        item.product_id
                        and not item.product_id.startswith(
                            ("custom-", "lens-", "lens-sug-")
                        )
                    ):
                        # Products-convergence: resolve via the SAME path
                        # order-create used for existence (spine first, then the
                        # catalog_products fallback) so a catalog-only product's
                        # discount_category + brand are also cap-enforced. The
                        # old pr.find_by_id() hit only the spine -> None for a
                        # catalog-only product -> the cap silently no-op'd (a
                        # luxury item could take the full role cap).
                        product = _resolve_product_doc(pr, item.product_id)
                        if product:
                            # Canonical category + luxury-brand cap (SYSTEM_INTENT
                            # discount matrix). Pass the real discount_category
                            # (NOT product `category`, which is an item-type, not a
                            # discount tier); pricing_caps defaults unknown/missing
                            # to MASS and applies the lower luxury brand cap.
                            from api.services.pricing_caps import (
                                effective_discount_cap as product_discount_cap,
                            )

                            cat_brand_cap = product_discount_cap(
                                product.get("discount_category"),
                                product.get("brand"),
                            )
                            effective_cap = min(user_discount_cap, cat_brand_cap)
                except Exception:
                    # FAIL-CLOSED on the cap-TIGHTENING path (council go-live
                    # blocker, ruling sec.1). A thrown product/category resolver
                    # must NEVER widen the allowed discount back to the loose
                    # user/role cap -- that silently dropped the 2-5% luxury
                    # brand floor and let a Cartier/Gucci frame take the full
                    # role cap (live compliance under-enforcement).
                    #
                    # We cannot re-read the DB here (it just threw), so we
                    # tighten using the STRONGEST signal still on the line-item
                    # payload itself: its brand. brand_cap_for() is a pure,
                    # DB-free luxury-brand lookup. If the line names a luxury
                    # brand, its (low) cap is applied as a floor even though the
                    # DB lookup failed -> the luxury floor cannot vanish on a
                    # resolver error.
                    #
                    # DELIBERATELY NOT over-corrected: a plain/MASS line (no
                    # luxury brand on the payload) returns None from
                    # brand_cap_for(), so effective_cap stays at the normal
                    # user/role cap and an ordinary legitimate sale is NOT
                    # blocked. "We couldn't determine the tier" only tightens
                    # when there is a concrete luxury signal; it never invents a
                    # cap that blocks a plain item.
                    try:
                        from api.services.pricing_caps import (
                            brand_cap_for as _luxury_brand_cap,
                        )

                        _bcap = _luxury_brand_cap(item.brand)
                        if _bcap is not None:
                            effective_cap = min(effective_cap, _bcap)
                    except Exception:
                        # Even the pure fallback failed: do not loosen. Leave
                        # effective_cap as-is (the user/role cap) so a normal
                        # sale still goes through, but never widened.
                        pass

                if _eff_disc > effective_cap + 1e-9:
                    raise HTTPException(
                        status_code=403,
                        detail=f"Discount {round(_eff_disc, 2)}% on {item.product_name or item.product_id} "
                        f"(explicit discount and/or unit price below MRP) exceeds your limit of "
                        f"{effective_cap}%. Contact a manager for approval.",
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
                    "product_id": _canon_by_pid.get(item.product_id or "") or item.product_id,
                    "product_name": item.product_name,
                    "sku": item.sku,
                    "brand": item.brand,
                    "subbrand": item.subbrand,
                    "category": item.category,
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "discount_percent": item.discount_percent,
                    "discount_amount": discount_amount,
                    # C-4 (DELTA 2): per-line approver + reason (consumed by the
                    # required-approval gate for a 100%-line-discount order).
                    "discount_approved_by": item.discount_approved_by,
                    "discount_reason": item.discount_reason,
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
                    # BUG-005: persist the validated cylinder axis alongside the
                    # power so the lab gets the full grind spec.
                    "axis": getattr(item, "axis", None),
                    # POS-10: per-line staff note (e.g. "tight frame — be careful
                    # tightening screws"). Carried from posStore CartLineItem.
                    "item_note": getattr(item, "item_note", None) or None,
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
        # Order-level cart discount must honour the SAME caps as per-item
        # discounts: the role cap AND the strictest category / luxury-brand cap
        # across the cart's real product lines. It used to be clamped only to the
        # role cap, so a cart discount could land >cap on a Cartier (2%) or
        # NON_DISCOUNTABLE (0%) line the per-item path above would block.
        if not is_admin and cart_discount_percent > 0:
            cart_cap = user_discount_cap
            from api.services.pricing_caps import (
                effective_discount_cap as _line_discount_cap,
            )

            _cap_repo = get_product_repository()
            for _it in order.items:
                _pid = getattr(_it, "product_id", None)
                if not _pid or _pid.startswith(("custom-", "lens-", "lens-sug-")):
                    continue
                # BUG-118: a cart-level discount on an HQ-discounted line is the
                # same forbidden "further discount" (SYSTEM_INTENT s3) -- block it.
                _m = _mrp_by_pid.get(_pid)
                _o = _offer_by_pid.get(_pid)
                if _o and _m and _o < _m:
                    raise HTTPException(
                        status_code=403,
                        detail=(
                            f"Cannot apply a cart discount: "
                            f"{getattr(_it, 'product_name', None) or _pid} is already "
                            f"HQ-discounted (offer Rs{_o} < MRP Rs{_m}). Contact an "
                            f"administrator to override."
                        ),
                    )
                try:
                    # Products-convergence: spine-first, catalog-fallback resolve
                    # so a catalog-only product is cap-enforced at cart level too
                    # (was _cap_repo.find_by_id -> spine only -> None -> no cap).
                    _prod = _resolve_product_doc(_cap_repo, _pid)
                except Exception:
                    _prod = None
                if _prod:
                    cart_cap = min(
                        cart_cap,
                        _line_discount_cap(
                            _prod.get("discount_category"), _prod.get("brand")
                        ),
                    )
            if cart_discount_percent > cart_cap + 1e-9:
                raise HTTPException(
                    status_code=403,
                    detail=f"Cart discount {cart_discount_percent}% exceeds the "
                    f"maximum {cart_cap}% allowed for these items "
                    f"(role + category/brand caps). Contact a manager for approval.",
                )

        # ============================================================
        # F11/F12 ADVANCED PROMOTIONS + CROSS-CATEGORY BUNDLING (DARK)
        # ------------------------------------------------------------
        # Gated by PROMO_ENGINE_ENABLED (default OFF). When OFF this block is a
        # COMPLETE no-op: `applied_promos` stays [] and items_data is untouched,
        # so the GST + grand_total math below is byte-identical to the pre-promo
        # path (asserted by test_promo_engine dark-by-default). When ON, the
        # pure engine (services/promo_engine.evaluate_promos) picks the single
        # best promo (EXCLUSIVE by default; a campaign may opt into stacking),
        # NEVER breaches the category/luxury caps (the engine clamps), and the
        # resulting per-line rupee discount reduces each line's item_total before
        # GST. The atomic uses_count guard + promo_applications audit row are
        # written by promotions.commit_promo_application AFTER the order persists.
        # Fully fail-soft: any promo error logs + skips -- a promo can NEVER
        # block or alter the sale beyond the discount it grants.
        applied_promos: List[Dict[str, Any]] = []
        promo_total_discount = 0.0
        promo_evaluation: Optional[Dict[str, Any]] = None
        try:
            from .promotions import promo_engine_enabled, evaluate_for_order

            if promo_engine_enabled() and db is not None:
                # PURE evaluate (no DB writes yet) so the per-line discount can be
                # folded into the GST math; the atomic uses_count $inc +
                # promo_applications audit row are committed AFTER the order
                # persists (commit_promo_application below, with the real order_id).
                promo_evaluation = evaluate_for_order(
                    db,
                    store_id=store_id,
                    customer_id=order.customer_id,
                    items=items_data,
                    customer=customer,
                )
                if promo_evaluation and promo_evaluation.get("applied"):
                    promo_per_line = promo_evaluation.get("per_line_discount") or {}
                    applied_promos = promo_evaluation.get("applied_promos") or []
                    # Apply the per-line discount to each line's all-in total. The
                    # engine already clamped each share to that line's category/
                    # luxury cap, so this can never breach a hardlock.
                    for _line in items_data:
                        _lid = str(_line.get("item_id") or "")
                        _pd = float(promo_per_line.get(_lid, 0.0) or 0.0)
                        if _pd > 0:
                            _new_total = max(
                                0.0, float(_line.get("item_total") or 0.0) - _pd
                            )
                            _line["promo_discount_amount"] = round(_pd, 2)
                            _line["item_total"] = round(_new_total, 2)
                            promo_total_discount += _pd
                    promo_total_discount = round(promo_total_discount, 2)
        except Exception as _promo_exc:  # noqa: BLE001 - promos never block a sale
            logger.warning("[PROMO] order-create evaluation skipped: %s", _promo_exc)
            applied_promos, promo_total_discount, promo_evaluation = [], 0.0, None

        gst = _compute_per_category_gst(items_data, cart_discount_percent)
        taxable_after_cart_discount = gst["taxable"]
        tax_amount = gst["tax"]
        cart_discount_amount = gst["cart_discount_amount"]
        total_discount = gst["total_discount"]
        tax_rate = gst["dominant_rate"]
        grand_total = round(taxable_after_cart_discount + tax_amount, 2)

        # Fcostfloor (DECISIONS sec 9, owner sign-off 2026-06-09): E2-flag-
        # gated post-discount cost+pct% floor on each DISCOUNTED line's
        # EFFECTIVE per-unit taxable price (after the per-line discount AND
        # its share of the cart discount, as stamped by
        # _compute_per_category_gst). Owner rev 2: pure full-sticker lines
        # are exempt -- the flag below tells the guard whether a cart-level
        # discount applies to this order (server-derived, never the raw
        # client amount). Read-only math over the already-computed line
        # finals -- no GST, payment or persistence change. Fail-OPEN on
        # missing/zero cost; Rs 0 / 100%-discount lines stay
        # C-4-approval-gated-exempt; the floor COMPOSES with (never
        # replaces) the role/category/brand caps above. Flag off ->
        # immediate no-op (pre-change behavior). Raises BEFORE the lens
        # reserve below so a floor 400 leaks no reservation.
        from ..services.cost_floor import enforce_cost_floor

        enforce_cost_floor(
            items_data,
            _cost_by_pid,
            store_id,
            order_has_cart_discount=bool(
                cart_discount_percent > 0 or cart_discount_amount > 0
            ),
        )

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

        # BUG-097: block serialized non-lens oversell BEFORE reserving any lens
        # cells, so a 409 here leaks no reservation.
        _assert_serialized_stock_available(items_data, store_id)

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
                                "failed (line %s): %s",
                                prev_idx,
                                inner_rb,
                            )
                except Exception as rb_exc:  # noqa: BLE001
                    logger.warning(
                        "[LENS_HOOK] rollback outer error: %s",
                        rb_exc,
                    )
            raise
        except Exception as exc:  # noqa: BLE001
            # Non-blocking soft failure (mongo blip, etc.). Tag the
            # order and continue -- never crash POS create on a hook
            # error. Revenue protection takes priority.
            logger.warning(
                "[LENS_HOOK] reserve fail-soft pre-create: %s",
                exc,
            )
            lens_reserve_failed = True

        # C-4 (DELTA 2): a fully-discounted / Rs 0 order (100% line or cart
        # discount, or a grand_total that rounds to 0) is a sensitive giveaway
        # that REQUIRES explicit approval -- it is no longer silently
        # auto-stamped. When the order triggers the zero-total condition, the
        # request MUST carry an approver AND a non-empty reason at the level
        # that triggered it ("whichever applies"):
        #   * cart-level trigger (cart_discount 100% / grand_total 0)
        #       -> cart_discount_approved_by + cart_discount_reason
        #   * a 100% LINE discount
        #       -> that line's discount_approved_by + discount_reason
        #          (the order-wide cart_discount_* fields are accepted as a
        #          fallback so a single order-level sign-off still works)
        # Missing approver or empty reason -> HTTP 400. When approval IS
        # present we ALLOW the sale and still write the immutable
        # ORDER_ZERO_TOTAL_APPROVED audit row (below).
        acting_user_id = current_user.get("user_id")

        def _nonempty(value) -> bool:
            return bool(value is not None and str(value).strip())

        full_line_items = [
            it
            for it in items_data
            if float((it or {}).get("discount_percent") or 0) >= 100.0
        ]
        has_full_line_discount = bool(full_line_items)
        cart_level_zero = round(grand_total, 2) <= 0.0 or cart_discount_percent >= 100.0
        is_zero_total = cart_level_zero or has_full_line_discount

        cart_discount_approved_by = order.cart_discount_approved_by
        cart_discount_reason = order.cart_discount_reason
        zero_total_approved_by = None
        if is_zero_total:
            # Resolve the effective approver + reason from whichever level
            # supplied them: cart-level first, then any 100%-discount line.
            approver = (
                cart_discount_approved_by
                if _nonempty(cart_discount_approved_by)
                else None
            )
            reason = cart_discount_reason if _nonempty(cart_discount_reason) else None
            if approver is None and has_full_line_discount:
                for it in full_line_items:
                    if _nonempty((it or {}).get("discount_approved_by")):
                        approver = it["discount_approved_by"]
                        break
            if reason is None and has_full_line_discount:
                for it in full_line_items:
                    if _nonempty((it or {}).get("discount_reason")):
                        reason = it["discount_reason"]
                        break

            if approver is None or reason is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Zero-total or 100% discount requires an approver and a "
                        "reason."
                    ),
                )

            zero_total_approved_by = approver

        order_data = {
            "order_id": precomputed_order_id,
            "order_number": generate_order_number(store_id),
            # Public order-tracking token — long, unguessable, customer-facing.
            # Powers the no-login /portal/track/{token} link + QR. Additive;
            # does not touch any POS pricing/tax logic. Backfill-safe: orders
            # created before this field get a token lazily minted on lookup
            # (see portal.ensure_tracking_token).
            "tracking_token": secrets.token_urlsafe(24),
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
            "cart_discount_reason": cart_discount_reason,
            # C-4 (DELTA 2): the approver the POS supplied (now REQUIRED for a
            # zero-total / 100%-discount order -- never auto-stamped).
            "cart_discount_approved_by": cart_discount_approved_by,
            "tax_rate": tax_rate,
            "tax_amount": tax_amount,
            "total_discount": total_discount,
            "grand_total": grand_total,
            # Self-label the GST model this order was billed under, so any
            # deploy-skew / flag-flip order is identifiable + reports can trust
            # the stored per-line taxable/tax without guessing the era.
            "pricing_model": gst.get("pricing_model", "inclusive"),
            "amount_paid": 0.0,
            "balance_due": grand_total,
            "payment_status": "UNPAID",
            "status": "DRAFT",
            "expected_delivery": expected_delivery.isoformat(),
            "delivery_time_slot": order.delivery_time_slot,
            "delivery_priority": (order.delivery_priority or "NORMAL").upper(),
            "notes": order.notes,
            # POS-10: order_type persisted so reports/audit can distinguish
            # a quick POS sale from a full workshop-linked order.
            "order_type": (order.order_type or None),
            "payments": [],
            "lens_reservations": lens_reservations,
            "lens_reserve_failed": bool(lens_reserve_failed),
            # C-4: zero-total accountability flags (persisted so reports + the
            # order view can surface a Rs 0 sale and who approved it).
            "zero_total": bool(is_zero_total),
            "zero_total_approved_by": zero_total_approved_by,
            # F11/F12: promos that fired on this order (empty [] when the engine
            # is dark or nothing matched -- the dark-default path). Surfaced on
            # the order view, the Tally JV, and the Offer Tally report.
            "applied_promos": applied_promos,
            "promo_discount_total": round(promo_total_discount, 2),
            # C-5 (DELTA 3): the request's Idempotency-Key (None when absent).
            # A repeat POST with the same key returns this order rather than
            # creating a duplicate (store-scoped lookup at the top of create).
            "idempotency_key": (idem_key or None),
            # POS-12: initial status_history entry so the DRAFT create is always
            # the first row in the timeline. Subsequent status changes append via
            # OrderRepository.update_status -> $push {"status_history": {...}}.
            "status_history": [
                {
                    "status": "DRAFT",
                    "timestamp": datetime.now().isoformat(),
                    "changed_by": current_user.get("user_id") or "system",
                }
            ],
        }

        try:
            # P3-B: order_number carries a UNIQUE sparse index. Under
            # concurrency two creates can mint the same value and the loser
            # hits a Mongo E11000 -- which previously 500'd. create_unique
            # regenerates JUST the order_number and retries (bounded), mirroring
            # vouchers.issue_voucher. order_id / _id are stable UUIDs and never
            # change across retries. Behaviour-preserving on the no-collision
            # path: a single insert, identical doc.
            created = order_repo.create_unique(
                order_data,
                number_field="order_number",
                regenerate=lambda: generate_order_number(store_id),
            )
        except Exception as create_exc:  # noqa: BLE001
            # Order persist failed AFTER reservations succeeded -- run
            # the compensating release so the cells don't leak.
            logger.error(
                "[ORDERS] order_repo.create failed; releasing %d lens "
                "reservations for order %s",
                len(lens_reservations),
                precomputed_order_id,
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
            # C-4: write an immutable audit row for a zero-total / fully
            # discounted sale so a Rs 0 order is never silent. Uses the same
            # append-only AuditRepository every other sensitive action uses
            # (returns / payouts / price edits). Fail-soft: an audit failure
            # must NEVER block the sale.
            if is_zero_total:
                try:
                    from ..dependencies import get_audit_repository

                    audit = get_audit_repository()
                    if audit is not None:
                        audit.create(
                            {
                                "action": "ORDER_ZERO_TOTAL_APPROVED",
                                "entity_type": "order",
                                "entity_id": created_order_id,
                                "store_id": store_id,
                                "user_id": acting_user_id,
                                "severity": "WARNING",
                                "details": {
                                    "grand_total": grand_total,
                                    "subtotal": subtotal,
                                    "cart_discount_percent": cart_discount_percent,
                                    "has_full_line_discount": has_full_line_discount,
                                    # C-4 (DELTA 2): approval is now REQUIRED,
                                    # so the approver + reason are always real
                                    # (never auto-stamped).
                                    "approved_by": zero_total_approved_by,
                                    "reason": cart_discount_reason,
                                },
                                "created_at": datetime.now().isoformat(),
                            }
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[ORDERS] zero-total audit skipped: %s", exc)

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

            # IMS = inventory MASTER (council B11): after the in-store sale
            # reduces on-hand, push the reduced AVAILABLE qty to Shopify so the
            # website can't oversell. Gated (IMS_SHOPIFY_WRITES + DISPATCH_MODE)
            # + fire-and-forget + fully fail-soft: a Shopify error can NEVER
            # block or slow the sale. No online mapping for a SKU -> no-op.
            try:
                from ..services.online_stock_writeback import writeback_after_sale

                writeback_after_sale(None, items_data, store_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[STOCK] online write-back skipped: %s", exc)

            # F11/F12: commit the promo application AFTER the order persists --
            # atomically $inc each fired promo's uses_count (guarded
            # find_one_and_update; concurrent terminals can't overshoot) and write
            # the immutable promo_applications audit row with the real order_id +
            # margin estimate. Fully fail-soft: any error logs + skips; the order
            # is already saved with applied_promos stamped, so the audit is
            # best-effort and never blocks the sale. No-op when nothing fired.
            if applied_promos and promo_evaluation is not None:
                try:
                    from .promotions import commit_promo_application

                    commit_promo_application(
                        db,
                        order_id=created_order_id,
                        order_number=created.get("order_number") or "",
                        store_id=store_id,
                        customer_id=order.customer_id,
                        cashier_id=current_user.get("user_id"),
                        items=items_data,
                        # commit reads the RAW engine dict (fired/breakdown/...),
                        # which evaluate_for_order nests under "evaluation".
                        evaluation=(promo_evaluation or {}).get("evaluation") or {},
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[PROMO] commit skipped: %s", exc)

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

            # C-5 (DELTA 3): same envelope the idempotency replay returns, so a
            # retried request is indistinguishable from the original create.
            return _order_create_response(created)

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
            # Store-scope: only view an order in a store the user can access
            # (raises 403 otherwise; SUPERADMIN/ADMIN pass through).
            validate_store_access(order.get("store_id"), current_user)
            # Backfill-safe: ensure a public tracking token exists so the
            # staff-facing order view can render the customer-tracking QR
            # even for orders created before that field existed. Fail-soft.
            if not order.get("tracking_token"):
                try:
                    from .portal import ensure_tracking_token

                    order["tracking_token"] = ensure_tracking_token(repo, order)
                except Exception:  # noqa: BLE001
                    pass
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

        # IDOR guard: mirror GET /{order_id} -- only act on an order in a store
        # the caller can access (403 otherwise; SUPERADMIN/ADMIN pass through).
        validate_store_access(existing.get("store_id"), current_user)

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


# ============================================================================
# Build item #16 - SUPERADMIN post-creation order edit (revenue/GST/audit)
# ============================================================================
def _require_superadmin(current_user: dict) -> None:
    """Gate an endpoint to SUPERADMIN only (build item #16 is SUPERADMIN-only by
    owner decision -- a post-creation order/invoice change is a privileged,
    audited override). Raises 403 for every other role."""
    if "SUPERADMIN" not in (current_user.get("roles") or []):
        raise HTTPException(
            status_code=403,
            detail="Only a SUPERADMIN may edit an order after it is created.",
        )


def _write_order_edit_audit(
    *,
    action: str,
    order: dict,
    before: dict,
    after: dict,
    reason: str,
    user_id: Optional[str],
    extra: Optional[dict] = None,
) -> bool:
    """Write the SYNCHRONOUS, immutable hash-chained audit row for a
    SUPERADMIN order/invoice change BEFORE the endpoint returns.

    Unlike the fire-and-forget alert on the DRAFT PUT path, this is a blocking
    write (revenue/GST/audit-critical, SYSTEM_INTENT 10 "Audit Everything"):
    the before/after snapshots + the human reason are committed to the
    append-only ``audit_logs`` chain (audit_chain.append_audit_entry) so the
    change is tamper-evident at GET /api/v1/audit/verify. We use the chained
    fields the hash commits to: before_state / after_state / diff / detail.
    Returns True on a chained write; False if no audit repo (DB-less).
    """
    from ..dependencies import get_audit_repository
    from ..services.order_superadmin_edit import build_money_diff

    audit = get_audit_repository()
    if audit is None:
        return False
    row = {
        "action": action,
        "entity_type": "order",
        "entity_id": order.get("order_id"),
        "store_id": order.get("store_id"),
        "user_id": user_id,
        "severity": "WARNING",
        "detail": reason,
        "before_state": before,
        "after_state": after,
        "diff": build_money_diff(before, after),
        "timestamp": datetime.now().isoformat(),
        "created_at": datetime.now().isoformat(),
    }
    if extra:
        row["context"] = extra
    audit.create(row)
    return True


def _rebuilt_items_or_existing(
    body_items, existing_items: list
) -> list:
    """Resolve the corrected line set for an edit. When the caller supplies
    ``items`` it REPLACES the whole set (each normalised via rebuild_edited_line
    so item_total/discount are recomputed server-side); when omitted, the
    existing lines are kept (a customer-only / cart-discount-only edit)."""
    from ..services.order_superadmin_edit import rebuild_edited_line

    if body_items is None:
        return [dict(it) for it in (existing_items or [])]
    rebuilt = []
    for line in body_items:
        payload = line.model_dump() if hasattr(line, "model_dump") else dict(line)
        rebuilt.append(rebuild_edited_line(payload))
    if not rebuilt:
        raise HTTPException(
            status_code=400, detail="An edited order must keep at least one item."
        )
    return rebuilt


@router.put("/{order_id}/superadmin-edit")
async def superadmin_edit_order(
    order_id: str,
    body: SuperadminOrderEdit,
    current_user: dict = Depends(get_current_user),
):
    """SUPERADMIN-only PRE-INVOICE order edit (build item #16, part 1).

    Allowed when the order is CONFIRMED / PROCESSING / READY and NO tax invoice
    has been issued yet. Edits the item set (qty / unit_price / discount /
    add / remove), the order-level cart discount, and/or the customer, then
    RECOMPUTES per-category GST + grand_total via the canonical
    ``_compute_per_category_gst`` (so the edit bills EXACTLY like create/add/
    remove). A non-empty ``reason`` is mandatory and a synchronous immutable
    audit row (before/after/diff) is written BEFORE returning. Guards:
      * RBAC -> 403 non-SUPERADMIN;
      * period lock -> 423 (cannot edit into a closed accounting month);
      * an already-invoiced order -> 409 (use /superadmin-invoice-change);
      * DRAFT -> 400 (use the ordinary PUT /{order_id} edit);
      * terminal DELIVERED / CANCELLED -> 400.
    """
    _require_superadmin(current_user)
    repo = get_order_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Order store unavailable")

    from ..services.order_superadmin_edit import (
        PRE_INVOICE_EDITABLE_STATUSES,
        recompute_totals,
        order_money_snapshot,
    )

    order = repo.find_by_id(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    validate_store_access(order.get("store_id"), current_user)

    status = str(order.get("status") or "").upper()
    if status == "DRAFT":
        raise HTTPException(
            status_code=400,
            detail="A DRAFT order is edited via PUT /orders/{id}, not this endpoint.",
        )
    if order.get("invoice_number"):
        raise HTTPException(
            status_code=409,
            detail=(
                "A tax invoice has been issued for this order. Use "
                "/orders/{id}/superadmin-invoice-change (revised invoice or "
                "credit/debit note) -- an issued invoice must never be mutated."
            ),
        )
    if status not in PRE_INVOICE_EDITABLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Order status {status or 'UNKNOWN'} is not editable. Only "
                f"{', '.join(PRE_INVOICE_EDITABLE_STATUSES)} orders without an "
                f"invoice can be edited."
            ),
        )

    # Period lock: cannot edit money into a closed accounting month (423).
    db = _get_db()
    if db is not None:
        from .finance import check_period_locked
        from ..utils.ist import ist_today

        check_period_locked(db, ist_today())

    before = order_money_snapshot(order)

    # Resolve corrected lines + cart discount + customer.
    new_items = _rebuilt_items_or_existing(body.items, order.get("items"))
    cart_discount_pct = (
        body.cart_discount_percent
        if body.cart_discount_percent is not None
        else float(order.get("cart_discount_percent") or 0.0)
    )
    gst = recompute_totals(new_items, cart_discount_pct, _compute_per_category_gst)

    update_data: Dict[str, Any] = {
        "items": new_items,
        "subtotal": gst["subtotal"],
        "cart_discount_percent": max(0.0, min(100.0, cart_discount_pct or 0.0)),
        "cart_discount_amount": gst["cart_discount_amount"],
        "tax_rate": gst["dominant_rate"],
        "tax_amount": gst["tax"],
        "total_discount": gst["total_discount"],
        "grand_total": gst["grand_total"],
        "pricing_model": gst.get("pricing_model", "inclusive"),
        "updated_by": current_user.get("user_id"),
        "superadmin_edited": True,
        "superadmin_edit_reason": body.reason,
        "superadmin_edited_at": datetime.now().isoformat(),
    }
    if body.cart_discount_reason is not None:
        update_data["cart_discount_reason"] = body.cart_discount_reason
    if body.notes is not None:
        update_data["notes"] = body.notes
    if body.customer_id is not None:
        update_data["customer_id"] = body.customer_id
    if body.customer_name is not None:
        update_data["customer_name"] = body.customer_name

    # Recompute the receivable so a changed grand_total flows to balance_due /
    # payment_status (a SUPERADMIN edit can raise or lower what is owed).
    amount_paid = float(order.get("amount_paid") or 0.0)
    balance_due = round(gst["grand_total"] - amount_paid, 2)
    update_data["balance_due"] = max(0.0, balance_due)
    if balance_due <= 0.01:
        update_data["payment_status"] = "PAID"
    elif amount_paid > 0:
        update_data["payment_status"] = "PARTIAL"
    else:
        update_data["payment_status"] = "UNPAID"

    after_order = dict(order)
    after_order.update(update_data)
    after = order_money_snapshot(after_order)

    # SYNCHRONOUS immutable audit BEFORE persisting/returning.
    _write_order_edit_audit(
        action="ORDER_SUPERADMIN_EDIT",
        order=order,
        before=before,
        after=after,
        reason=body.reason,
        user_id=current_user.get("user_id"),
    )

    if not repo.update(order_id, update_data):
        raise HTTPException(status_code=500, detail="Failed to save order edit")

    return {
        "order_id": order_id,
        "message": "Order edited",
        "grand_total": gst["grand_total"],
        "tax_amount": gst["tax"],
        "balance_due": update_data["balance_due"],
        "before": before,
        "after": after,
        "audit_note": (
            "An immutable audit entry recording this change was written."
        ),
    }


@router.put("/{order_id}/superadmin-invoice-change")
async def superadmin_invoice_change(
    order_id: str,
    body: SuperadminInvoiceChange,
    current_user: dict = Depends(get_current_user),
):
    """SUPERADMIN-only POST-INVOICE correction (build item #16, part 2).

    For an order that ALREADY carries a GST tax invoice. An issued tax invoice
    is immutable (Rule 46) -- it is NEVER silently mutated. The SUPERADMIN
    chooses ``mode`` (owner decision = support BOTH):

      * REVISED_INVOICE -- allocate a NEW invoice serial for the corrected
        order, mark the ORIGINAL superseded/void with a pointer to the new
        serial, persist the corrected lines/totals, and link original<->revised
        (``revised_invoices``).
      * CREDIT_NOTE -- compute the money delta (grand_total down -> CREDIT note,
        up -> DEBIT note), issue a note for the DELTA linked to the original
        invoice (the customer-facing credit-note ledger so POS-redeem / the
        customer card see it), and leave the ORIGINAL invoice + order totals
        intact.

    Both paths: RBAC 403 non-SUPERADMIN, mandatory reason, period-lock 423, and
    a synchronous immutable before/after/diff audit row.
    """
    _require_superadmin(current_user)
    repo = get_order_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Order store unavailable")

    from ..services.order_superadmin_edit import (
        recompute_totals,
        order_money_snapshot,
        compute_invoice_delta,
        build_credit_note_doc,
        build_revised_invoice_doc,
    )

    order = repo.find_by_id(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    validate_store_access(order.get("store_id"), current_user)

    original_invoice = order.get("invoice_number")
    if not original_invoice:
        raise HTTPException(
            status_code=409,
            detail=(
                "No tax invoice has been issued for this order yet. Use "
                "/orders/{id}/superadmin-edit for a pre-invoice edit."
            ),
        )

    # Period lock: a post-issue correction is a financial posting (423).
    db = _get_db()
    if db is not None:
        from .finance import check_period_locked
        from ..utils.ist import ist_today

        check_period_locked(db, ist_today())

    before = order_money_snapshot(order)
    new_items = _rebuilt_items_or_existing(body.items, order.get("items"))
    cart_discount_pct = (
        body.cart_discount_percent
        if body.cart_discount_percent is not None
        else float(order.get("cart_discount_percent") or 0.0)
    )
    gst = recompute_totals(new_items, cart_discount_pct, _compute_per_category_gst)

    # The "after" money picture the correction targets.
    corrected = dict(order)
    corrected.update(
        {
            "items": new_items,
            "subtotal": gst["subtotal"],
            "cart_discount_percent": max(0.0, min(100.0, cart_discount_pct or 0.0)),
            "cart_discount_amount": gst["cart_discount_amount"],
            "tax_rate": gst["dominant_rate"],
            "tax_amount": gst["tax"],
            "total_discount": gst["total_discount"],
            "grand_total": gst["grand_total"],
        }
    )
    if body.customer_id is not None:
        corrected["customer_id"] = body.customer_id
    if body.customer_name is not None:
        corrected["customer_name"] = body.customer_name
    after = order_money_snapshot(corrected)

    store_doc = None
    try:
        if db is not None:
            store_doc = db.get_collection("stores").find_one(
                {"store_id": order.get("store_id")}
            )
    except Exception:  # noqa: BLE001
        store_doc = None

    if body.mode == "REVISED_INVOICE":
        # Allocate a fresh GST serial for the revised invoice.
        new_invoice_number = repo.next_invoice_number(
            order.get("store_id"), store_doc=store_doc
        )
        revised_doc = build_revised_invoice_doc(
            order=order,
            new_invoice_number=new_invoice_number,
            before=before,
            after=after,
            reason=body.reason,
            user_id=current_user.get("user_id"),
        )
        # Persist corrected order under the NEW serial; the OLD serial is kept
        # on the doc for traceability + marked superseded.
        update_data: Dict[str, Any] = {
            "items": new_items,
            "subtotal": gst["subtotal"],
            "cart_discount_percent": corrected["cart_discount_percent"],
            "cart_discount_amount": gst["cart_discount_amount"],
            "tax_rate": gst["dominant_rate"],
            "tax_amount": gst["tax"],
            "total_discount": gst["total_discount"],
            "grand_total": gst["grand_total"],
            "pricing_model": gst.get("pricing_model", "inclusive"),
            "invoice_number": new_invoice_number,
            "invoice_date": datetime.now(),
            "superseded_invoice_number": original_invoice,
            "invoice_revision_id": revised_doc["revision_id"],
            "superadmin_edited": True,
            "superadmin_edit_reason": body.reason,
            "superadmin_edited_at": datetime.now().isoformat(),
            "updated_by": current_user.get("user_id"),
        }
        if body.customer_id is not None:
            update_data["customer_id"] = body.customer_id
        if body.customer_name is not None:
            update_data["customer_name"] = body.customer_name
        # Recompute the receivable against the revised grand_total.
        amount_paid = float(order.get("amount_paid") or 0.0)
        balance_due = round(gst["grand_total"] - amount_paid, 2)
        update_data["balance_due"] = max(0.0, balance_due)
        update_data["payment_status"] = (
            "PAID"
            if balance_due <= 0.01
            else ("PARTIAL" if amount_paid > 0 else "UNPAID")
        )

        # SYNCHRONOUS immutable audit BEFORE persisting.
        _write_order_edit_audit(
            action="ORDER_INVOICE_REVISED",
            order=order,
            before=before,
            after=after,
            reason=body.reason,
            user_id=current_user.get("user_id"),
            extra={
                "original_invoice_number": original_invoice,
                "revised_invoice_number": new_invoice_number,
                "revision_id": revised_doc["revision_id"],
            },
        )

        # Persist the revised-invoice link record (best-effort, fail-soft).
        if db is not None:
            try:
                db.get_collection("revised_invoices").insert_one(dict(revised_doc))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[ORDERS] revised_invoices insert failed: %s", exc)

        if not repo.update(order_id, update_data):
            raise HTTPException(
                status_code=500, detail="Failed to issue revised invoice"
            )

        return {
            "order_id": order_id,
            "mode": "REVISED_INVOICE",
            "message": "Revised invoice issued; original invoice superseded.",
            "original_invoice_number": original_invoice,
            "revised_invoice_number": new_invoice_number,
            "revision_id": revised_doc["revision_id"],
            "grand_total": gst["grand_total"],
            "balance_due": update_data["balance_due"],
            "audit_note": "An immutable audit entry recording this change was written.",
        }

    # mode == CREDIT_NOTE  -> issue a credit (reduction) / debit (increase) note
    # for the DELTA, linked to the ORIGINAL invoice. The original invoice +
    # order totals are LEFT INTACT.
    delta = compute_invoice_delta(before, after)
    if delta["direction"] == "NONE":
        raise HTTPException(
            status_code=400,
            detail=(
                "The corrected order has the same grand total as the original; "
                "there is no delta to issue a credit/debit note for."
            ),
        )

    note_doc = build_credit_note_doc(
        order=order,
        delta=delta,
        reason=body.reason,
        user_id=current_user.get("user_id"),
    )

    # SYNCHRONOUS immutable audit BEFORE issuing the note.
    _write_order_edit_audit(
        action="ORDER_INVOICE_CREDIT_NOTE",
        order=order,
        before=before,
        after=after,
        reason=body.reason,
        user_id=current_user.get("user_id"),
        extra={
            "note_number": note_doc["note_number"],
            "note_type": note_doc["note_type"],
            "amount": note_doc["amount"],
            "original_invoice_number": original_invoice,
        },
    )

    # Persist the note. A CREDIT note also bumps the customer's store-credit
    # ledger balance (reuse returns.py machinery) so POS-redeem / the customer
    # card see the credit; a DEBIT note (customer owes more) is recorded for the
    # accountant but is NOT store credit. Both fail-soft.
    if db is not None:
        try:
            coll_name = (
                "credit_note_ledger"
                if note_doc["note_type"] == "CREDIT_NOTE"
                else "debit_note_ledger"
            )
            db.get_collection(coll_name).insert_one(dict(note_doc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ORDERS] note ledger insert failed: %s", exc)

    if note_doc["note_type"] == "CREDIT_NOTE":
        try:
            from .returns import _issue_store_credit

            _issue_store_credit(
                order.get("customer_id"),
                note_doc["amount"],
                reason=f"Credit note {note_doc['note_number']} "
                f"(order edit on invoice {original_invoice})",
                ref=note_doc["note_number"],
                current_user=current_user,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ORDERS] store-credit bump skipped: %s", exc)

    return {
        "order_id": order_id,
        "mode": "CREDIT_NOTE",
        "message": (
            f"{note_doc['note_type']} {note_doc['note_number']} issued for the "
            f"delta; original invoice {original_invoice} left intact."
        ),
        "note_number": note_doc["note_number"],
        "note_type": note_doc["note_type"],
        "amount": note_doc["amount"],
        "original_invoice_number": original_invoice,
        "delta": delta,
        "audit_note": "An immutable audit entry recording this change was written.",
    }


@router.post("/{order_id}/items")
async def add_order_item(
    order_id: str, item: OrderItemCreate, current_user: dict = Depends(get_current_user)
):
    """Add item to order (only DRAFT orders)"""
    # BUG-119/BUG-118: real price-integrity validation is below (after the DRAFT
    # check) using the catalog MRP/offer/cost -- the OrderItemCreate model carries
    # no mrp/offer_price field, so the old getattr(item,"mrp",0) guard never fired.

    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        # IDOR guard: mirror GET /{order_id} -- only act on an order in a store
        # the caller can access (403 otherwise; SUPERADMIN/ADMIN pass through).
        validate_store_access(order.get("store_id"), current_user)

        if order.get("status") != "DRAFT":
            raise HTTPException(
                status_code=400, detail="Can only add items to DRAFT orders"
            )

        # BUG-005 / BUG-006 (patient-safety): same Rx-power validation +
        # Rx-required check the create path runs, for a line appended to a DRAFT
        # order. Validation only -- no pricing/GST change. Uses the order's
        # customer so an expired / cross-customer / missing Rx is caught here too.
        _validate_order_line_rx(
            item, order.get("customer_id") or order.get("customerId") or "", current_user
        )

        # Enforce role + category + luxury-brand discount cap on items added to a
        # DRAFT order. This path previously checked ONLY the role cap (a bypass of
        # the category/brand caps); now consistent with create_order's per-item gate.
        from api.services.role_caps import effective_discount_cap

        _role_cap = effective_discount_cap(
            current_user.get("roles", []), current_user.get("discount_cap")
        )
        _is_admin = any(
            r in current_user.get("roles", []) for r in ("SUPERADMIN", "ADMIN")
        )
        _cap = _role_cap
        _eff_disc = item.discount_percent
        _pid = item.product_id or ""
        # Fcostfloor (chair P1): raw catalog cost for THIS line; stamped as
        # cost_at_sale below and fed to the floor pass. None (virtual id /
        # missing product / no cost_price) keeps the line fail-open.
        _cost = None
        if _pid and not _pid.startswith(("custom-", "lens-", "lens-sug-")):
            try:
                pr = get_product_repository()
                product = pr.find_by_id(_pid) if pr is not None else None
            except Exception:
                product = None
            if product:
                def _n(v):
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        return None

                _mrp = _n(product.get("mrp"))
                _offer = _n(product.get("offer_price"))
                _cost = _n(product.get("cost_price"))
                _up = item.unit_price
                _hq = bool(_offer and _mrp and _offer < _mrp)
                _ceiling = _offer if _hq else (_mrp if (_mrp and _mrp > 0) else None)
                if _ceiling and _up > _ceiling + 1e-6:
                    raise HTTPException(
                        status_code=400,
                        detail=f"unit_price Rs{_up} exceeds the catalog "
                        f"{'offer price' if _hq else 'MRP'} Rs{_ceiling} "
                        f"for {item.product_name or _pid}.",
                    )
                if _cost and _cost > 0 and _up > 1e-6 and _up < _cost - 1e-6:
                    raise HTTPException(
                        status_code=400,
                        detail=f"unit_price Rs{_up} is below cost Rs{_cost} for "
                        f"{item.product_name or _pid}. Contact a manager.",
                    )
                if not _is_admin and _hq and (
                    item.discount_percent > 0 or _up < _offer - 1e-6
                ):
                    raise HTTPException(
                        status_code=403,
                        detail=f"{item.product_name or _pid} is already discounted by "
                        f"HQ (offer Rs{_offer} < MRP Rs{_mrp}); no further store "
                        f"discount is allowed. Contact an administrator to override.",
                    )
                if _mrp and _mrp > 0 and _up < _mrp - 1e-6:
                    _eff_disc = max(item.discount_percent, (_mrp - _up) / _mrp * 100.0)
                from api.services.pricing_caps import (
                    effective_discount_cap as product_discount_cap,
                )

                _cap = min(
                    _role_cap,
                    product_discount_cap(
                        product.get("discount_category"), product.get("brand")
                    ),
                )
            else:
                # FAIL-CLOSED on the cap-TIGHTENING path (mirror create_order
                # ~line 1443). The product/category resolver returned nothing
                # (it threw, or the id wasn't found), so the category/luxury-brand
                # tightening above was skipped and _cap would silently stay at the
                # loose role cap -- dropping the 2-5% luxury brand floor and
                # letting a Cartier/Gucci frame take the full role cap when added
                # to a DRAFT order. We cannot re-read the DB (it just failed), so
                # we tighten using the STRONGEST signal still on the line payload:
                # its brand. brand_cap_for() is a pure, DB-free luxury-brand
                # lookup -- a named luxury brand keeps its low floor even on a
                # resolver error. DELIBERATELY NOT over-corrected: a plain/MASS
                # line (no luxury brand -> brand_cap_for returns None) keeps the
                # normal role cap, so an ordinary add is NOT blocked.
                try:
                    from api.services.pricing_caps import (
                        brand_cap_for as _luxury_brand_cap,
                    )

                    _bcap = _luxury_brand_cap(item.brand)
                    if _bcap is not None:
                        _cap = min(_cap, _bcap)
                except Exception:
                    pass  # even the pure fallback failed: do not loosen
        if not _is_admin and _eff_disc > _cap + 1e-9:
            raise HTTPException(
                status_code=403,
                detail=f"Discount {round(_eff_disc, 2)}% exceeds your limit of "
                f"{_cap}%. Contact a manager for approval.",
            )

        # Calculate item totals
        item_total = item.unit_price * item.quantity
        discount_amount = item_total * (item.discount_percent / 100)
        item_subtotal = item_total - discount_amount

        item_data = {
            "item_id": str(uuid.uuid4()),
            "item_type": item.item_type,
            "product_id": item.product_id,
            # Parity with create_order's line shape (chair P1): the name makes
            # the floor 400 actionable; cost_at_sale freezes COGS exactly like
            # create does (None when unknown -> floor fails open on this line).
            "product_name": item.product_name,
            "cost_at_sale": _cost,
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

        # Fcostfloor (chair P1): the add-items path must honor the SAME
        # post-discount cost+pct% floor as create_order -- it mirrored every
        # legacy guard but skipped the floor, so a cap-legal line that nets
        # below cost*(1+pct/100) could be appended to a clean DRAFT order.
        # Validate the COMBINED line list on the just-recomputed taxable
        # finals BEFORE persisting: a 400 here leaves the order untouched.
        # Owner rev 2 (discounted sales only): the cart-discount presence is
        # derived from the persisted order doc's cart_discount fields.
        from ..services.cost_floor import enforce_cost_floor

        def _f(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        enforce_cost_floor(
            items,
            {_pid: _cost},
            order.get("store_id"),
            order_has_cart_discount=bool(
                _f(cart_discount_percent) > 0
                or _f(order.get("cart_discount_amount")) > 0
            ),
        )

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

        # IDOR guard: mirror GET /{order_id} -- only act on an order in a store
        # the caller can access (403 otherwise; SUPERADMIN/ADMIN pass through).
        validate_store_access(order.get("store_id"), current_user)

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


# Item-type / category buckets used to decide whether a confirmed order needs a
# workshop/lab job. Mirrors the POS client's Phase-6.8 gate so the safety-net and
# the client agree on what "a fitting order" is.
_WORKSHOP_LENS_TYPES = {
    "LENS",
    "OPTICAL_LENS",
    "OPTICAL_LENSES",
    "SPECTACLE_LENS",
    "SPECTACLE_LENSES",
    "RX_LENSES",
    "LENSES",
}
_WORKSHOP_FRAME_TYPES = {"FRAME", "FRAMES", "SPECTACLE_FRAME", "SUNGLASS", "SUNGLASSES"}


def _order_item_kind(it: dict) -> str:
    return str(it.get("item_type") or it.get("category") or "").strip().upper()


def _order_needs_fitting(order: dict) -> bool:
    """A spectacle job needs the lab/workshop when it has a lens to grind, or a
    frame paired with a prescription. Accessory / watch / contact-lens-box /
    service- or eye-test-only orders do NOT."""
    items = order.get("items") or []
    has_lens = any(
        _order_item_kind(it) in _WORKSHOP_LENS_TYPES or it.get("lens_details")
        for it in items
    )
    if has_lens:
        return True
    has_frame = any(_order_item_kind(it) in _WORKSHOP_FRAME_TYPES for it in items)
    has_rx = bool(order.get("prescription_id")) or any(
        it.get("prescription_id") for it in items
    )
    return bool(has_frame and has_rx)


def _ensure_workshop_job_for_order(
    order: dict, by_user: Optional[str]
) -> Optional[str]:
    """Idempotently ensure a CONFIRMED fitting order has a workshop/lab job, and
    that the order carries the reverse `workshop_job_id` pointer.

    The POS happy-path already creates the job from the client (Phase 6.8); this
    is the SAFETY NET for (a) that client call failing and (b) orders confirmed
    via a non-POS path (e.g. the Orders page). It NEVER creates a duplicate
    (skips when find_by_order already has a job) and NEVER raises -- a
    workshop-job hiccup must never block confirming a paid order. Returns the
    job_id (created or pre-existing), else None.
    """
    try:
        if not _order_needs_fitting(order):
            return None
        order_id = order.get("order_id")
        if not order_id:
            return None
        from ..dependencies import get_workshop_repository

        wrepo = get_workshop_repository()
        if wrepo is None:
            return None
        order_repo = get_order_repository()

        existing = wrepo.find_by_order(order_id)
        if existing:
            jid = existing[0].get("job_id")
            # Backfill the reverse pointer if the client created the job but
            # never stamped it back onto the order.
            if jid and not order.get("workshop_job_id") and order_repo is not None:
                try:
                    order_repo.update(
                        order_id,
                        {
                            "workshop_job_id": jid,
                            "workshop_job_number": existing[0].get("job_number"),
                        },
                    )
                except Exception:
                    pass
            return jid

        items = order.get("items") or []
        frame = next(
            (it for it in items if _order_item_kind(it) in _WORKSHOP_FRAME_TYPES), None
        )
        lens = next(
            (
                it
                for it in items
                if _order_item_kind(it) in _WORKSHOP_LENS_TYPES
                or it.get("lens_details")
            ),
            None,
        )
        rx_id = (
            (lens or {}).get("prescription_id")
            or (frame or {}).get("prescription_id")
            or order.get("prescription_id")
            or ""
        )
        # expected_date: the order's scheduled delivery (YYYY-MM-DD) or +5 days.
        exp = str(order.get("expected_delivery") or "")[:10]
        if not exp:
            exp = (date.today() + timedelta(days=5)).isoformat()

        from .workshop import generate_job_number

        job_data = {
            "job_number": generate_job_number(wrepo),
            "order_id": order_id,
            "store_id": order.get("store_id"),
            "frame_details": (
                {
                    "product_id": frame.get("product_id"),
                    "name": frame.get("product_name") or frame.get("name"),
                    "sku": frame.get("sku"),
                    "brand": frame.get("brand"),
                }
                if frame
                else {}
            ),
            "lens_details": (
                lens.get("lens_details")
                if (lens and lens.get("lens_details"))
                else (
                    {
                        "product_id": lens.get("product_id"),
                        "name": lens.get("product_name") or lens.get("name"),
                    }
                    if lens
                    else {}
                )
            ),
            "prescription_id": rx_id,
            "fitting_instructions": None,
            "special_notes": order.get("notes"),
            "expected_date": exp,
            "status": "PENDING",
            "created_by": by_user,
            # Provenance: spawned by the confirm safety-net, not the POS client.
            "auto_created": True,
        }
        created = wrepo.create(job_data)
        if created and order_repo is not None:
            try:
                order_repo.update(
                    order_id,
                    {
                        "workshop_job_id": created.get("job_id"),
                        "workshop_job_number": created.get("job_number"),
                    },
                )
            except Exception:
                pass
        return created.get("job_id") if created else None
    except Exception as e:  # never block a confirm
        import logging

        logging.getLogger(__name__).warning(
            "[ORDERS] workshop auto-link skipped for %s: %s",
            order.get("order_id"),
            e,
        )
        return None


@router.post("/{order_id}/confirm")
async def confirm_order(order_id: str, current_user: dict = Depends(get_current_user)):
    """Confirm order (DRAFT -> CONFIRMED)"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        # IDOR guard: mirror GET /{order_id} -- only act on an order in a store
        # the caller can access (403 otherwise; SUPERADMIN/ADMIN pass through).
        validate_store_access(order.get("store_id"), current_user)

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
            # POS operational-wins: guarantee a fitting order has a workshop/lab
            # job once it's committed. Idempotent + fail-soft (the POS client may
            # already have created it; a non-POS confirm path may not have).
            workshop_job_id = _ensure_workshop_job_for_order(
                order, current_user.get("user_id")
            )
            return {
                "order_id": order_id,
                "status": "CONFIRMED",
                "message": "Order confirmed",
                "workshop_job_id": workshop_job_id,
            }

        raise HTTPException(status_code=500, detail="Failed to confirm order")

    return {"order_id": order_id, "status": "CONFIRMED"}


@router.post("/{order_id}/payments")
async def add_payment(
    order_id: str,
    payment: PaymentCreate,
    current_user: dict = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Add payment to order.

    POS-14: supports an optional ``Idempotency-Key`` header. When a non-empty
    key is supplied and a payment with that key already exists on this order,
    the EXISTING payment_id is returned without recording a duplicate. This
    makes a double-clicked "Pay" button safe. The key is stamped on the payment
    row and looked up before any write. Fail-soft: if the lookup fails, the
    normal (non-idempotent) path runs.
    """
    repo = get_order_repository()

    if repo is not None:
        # POS-14: idempotency guard — look for an existing payment with this key
        # on the same order before recording another. Fail-soft.
        # isinstance guard: a direct (non-HTTP) call leaves the Header(...) default
        # object in idempotency_key, which has no .strip(); treat it as absent.
        idem_key = idempotency_key.strip() if isinstance(idempotency_key, str) else ""
        if idem_key:
            try:
                order_doc = repo.find_by_id(order_id)
                if order_doc:
                    # IDOR guard: the replay must not leak another store's
                    # payment row -- same store check as the main path below.
                    validate_store_access(order_doc.get("store_id"), current_user)
                    for existing_pmt in order_doc.get("payments") or []:
                        if existing_pmt.get("idempotency_key") == idem_key:
                            return {
                                "payment_id": existing_pmt.get("payment_id"),
                                "message": "Payment recorded",
                                "amount": existing_pmt.get("amount"),
                                "order_status": order_doc.get("status", "DRAFT"),
                                "payment_status": order_doc.get("payment_status", "UNPAID"),
                                "_idempotent_replay": True,
                            }
            except HTTPException:
                raise
            except Exception as _idem_exc:  # noqa: BLE001
                logger.warning("[ORDERS] payment idempotency lookup skipped: %s", _idem_exc)
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        # IDOR guard: mirror GET /{order_id} -- only act on an order in a store
        # the caller can access (403 otherwise; SUPERADMIN/ADMIN pass through).
        validate_store_access(order.get("store_id"), current_user)

        if order.get("status") == "CANCELLED":
            raise HTTPException(
                status_code=400, detail="Cannot add payment to cancelled order"
            )

        balance_due = order.get("balance_due", order.get("grand_total", 0))
        # C-9: a CREDIT tender is a pay-later PROMISE, not cash collected, so it
        # is exempt from the over-tender block -- matching OrderRepository.
        # add_payment (which excludes CREDIT from its cash-collected/over-tender
        # math). A real-money tender (CASH/UPI/CARD/etc.) still cannot exceed the
        # balance due.
        if payment.method != PaymentMethod.CREDIT and payment.amount > balance_due:
            raise HTTPException(
                status_code=400,
                detail=f"Payment amount exceeds balance due (Rs {balance_due})",
            )

        # POS-4: credit-limit (khata) guard.
        # When a CREDIT tender is used, enforce the per-customer credit limit.
        # A limit of 0 means unlimited. Fail-soft: if we cannot read the
        # customer record the check is skipped (behaviour-preserving).
        if payment.method == PaymentMethod.CREDIT:
            customer_id_for_limit = order.get("customer_id")
            if customer_id_for_limit and not customer_id_for_limit.startswith(
                "walkin-"
            ):
                try:
                    from .customers import _ar_outstanding

                    customer_repo = get_customer_repository()
                    customer_doc = (
                        customer_repo.find_by_id(customer_id_for_limit)
                        if customer_repo is not None
                        else None
                    )
                    credit_limit = float((customer_doc or {}).get("credit_limit") or 0)
                    if credit_limit > 0:
                        ar_now = _ar_outstanding(customer_id_for_limit, customer_doc)
                        if ar_now + payment.amount > credit_limit:
                            raise HTTPException(
                                status_code=400,
                                detail=(
                                    f"Credit limit exceeded: customer limit is "
                                    f"Rs {credit_limit:.2f}, current AR outstanding "
                                    f"Rs {ar_now:.2f}. Adding Rs {payment.amount:.2f} "
                                    f"would exceed by Rs "
                                    f"{(ar_now + payment.amount - credit_limit):.2f}."
                                ),
                            )
                except HTTPException:
                    raise
                except Exception as _exc:  # noqa: BLE001
                    logger.warning("[ORDERS] credit-limit check skipped: %s", _exc)

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

            # POS-2 + P3-C: use emi_principal (financed balance) when the
            # caller provides it; fall back to payment.amount for backward
            # compat. This lets the POS record the down-payment in `amount`
            # (which reduces balance_due correctly) while the schedule
            # reflects the full loan amount (order_total - down_payment).
            schedule_principal = payment.emi_principal or payment.amount
            emi_details = build_emi_schedule(
                principal=schedule_principal,
                annual_rate=emi_annual_rate,
                months=payment.emi_months,
            )
            emi_details["provider"] = payment.emi_provider or "STORE"
            # Record the down-payment separately so the full EMI picture is
            # on the order document alongside the financed balance.
            if payment.emi_principal and payment.emi_principal != payment.amount:
                emi_details["down_payment"] = round(float(payment.amount), 2)
                emi_details["financed_amount"] = round(float(payment.emi_principal), 2)

        payment_data = {
            "payment_id": str(uuid.uuid4()),
            "method": payment.method.value,
            "amount": payment.amount,
            "reference": payment.reference,
            "received_by": current_user.get("user_id"),
            "received_at": datetime.now().isoformat(),
            # POS-14: persist the idempotency key on the row so a duplicate POST
            # with the same key is caught by the guard at the top of this handler.
            "idempotency_key": (idem_key or None),
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

        # IDOR guard: mirror GET /{order_id} -- only act on an order in a store
        # the caller can access (403 otherwise; SUPERADMIN/ADMIN pass through).
        validate_store_access(order.get("store_id"), current_user)

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

        # IDOR guard: mirror GET /{order_id} -- only act on an order in a store
        # the caller can access (403 otherwise; SUPERADMIN/ADMIN pass through).
        validate_store_access(order.get("store_id"), current_user)

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
            # CRM-9: Auto-trigger NPS survey on delivery (fail-soft — a survey
            # failure must NEVER block the delivery confirmation).
            try:
                from ..services.nps_trigger import trigger_nps_on_delivery
                await trigger_nps_on_delivery(order, current_user)
            except Exception as _nps_exc:
                logger.warning("[ORDERS] NPS auto-trigger failed (non-fatal): %s", _nps_exc)
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
    # RBAC: cancelling a sale is a POS-tier action, same tier as order
    # create/confirm/deliver. Previously ANY authenticated role (ACCOUNTANT,
    # OPTOMETRIST, WORKSHOP_STAFF, ...) could cancel any order.
    if not any(r in current_user.get("roles", []) for r in POS_WRITE_ROLES):
        raise HTTPException(
            status_code=403, detail="Your role is not permitted to cancel orders."
        )
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        # IDOR guard: mirror GET /{order_id} -- only act on an order in a store
        # the caller can access (403 otherwise; SUPERADMIN/ADMIN pass through).
        validate_store_access(order.get("store_id"), current_user)

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
                        order_id,
                        idx,
                        rel_exc,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[LENS_HOOK] cancel release outer error %s: %s",
                order_id,
                exc,
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


def _invoice_state_code(*candidates) -> str:
    """Best-effort 2-digit GST state code from the first usable candidate.
    Accepts a 2-digit code, a 2-letter / full state name, or a 15-char GSTIN
    (state = first two chars). ASCII-only; never raises."""
    try:
        from ..services.org_validation import (
            normalize_state_code,
            INDIAN_STATE_CODES,
        )
    except Exception:  # noqa: BLE001
        normalize_state_code = None
        INDIAN_STATE_CODES = {}

    def _valid_code(code) -> str:
        """Accept a 2-digit string only if it's a real GST state code."""
        c = str(code or "").strip()
        if (
            len(c) == 2
            and c.isdigit()
            and (not INDIAN_STATE_CODES or c in INDIAN_STATE_CODES)
        ):
            return c
        return ""

    for cand in candidates:
        if cand is None:
            continue
        s = str(cand).strip()
        if not s:
            continue
        # A full GSTIN -> first two chars are the state code.
        if len(s) == 15 and s[:2].isdigit():
            code = _valid_code(s[:2])
            if code:
                return code
        # 2-digit code / 2-letter abbreviation / full state name.
        if normalize_state_code is not None:
            code = _valid_code(normalize_state_code(s))
            if code:
                return code
        # Bare 2-digit code (when org_validation is unavailable).
        code = _valid_code(s[:2])
        if code:
            return code
    return ""


def _customer_state_code(customer: Optional[dict]) -> str:
    """Resolve the customer's place-of-supply state code from (in order):
    customer GSTIN -> billing_address.state_code -> billing_address.state.
    Empty string when nothing usable is present."""
    if not isinstance(customer, dict):
        return ""
    addr = customer.get("billing_address") or {}
    if not isinstance(addr, dict):
        addr = {}
    return _invoice_state_code(
        customer.get("gstin"),
        addr.get("state_code"),
        addr.get("state"),
        customer.get("state_code"),
        customer.get("state"),
    )


def _build_invoice_gst_split(
    items: list, store: Optional[dict], customer: Optional[dict]
) -> dict:
    """C-6 (DELTA 4): per-rate CGST/SGST/IGST breakup for an order invoice.

    Place of supply = the CUSTOMER's state; supplier state = the STORE's state.
      * intra-state (or customer state unknown -> safe default for a single-
        state retailer): each rate's tax splits into CGST + SGST (each rate/2).
      * inter-state (both states known and different): the full tax is IGST.

    The split is computed from the order's ALREADY-STORED per-line
    `taxable_value` + `tax_amount`, so it reconciles to grand_total in BOTH
    pricing modes (inclusive / exclusive) without re-deriving tax. Never raises.

    Returns:
      {
        "place_of_supply": "<2-digit code or ''>",
        "place_of_supply_assumed": bool,   # True when defaulted to intra
        "interstate": bool,
        "store_gstin": "<gstin or ''>",
        "customer_gstin": "<gstin or ''>",
        "rows": [{"rate", "taxable", "cgst", "sgst", "igst", "tax"}],
        "totals": {"taxable", "cgst", "sgst", "igst", "tax"},
      }
    """
    store = store if isinstance(store, dict) else {}
    store_gstin = str(store.get("gstin") or "").strip()
    customer_gstin = (
        str((customer or {}).get("gstin") or "").strip()
        if isinstance(customer, dict)
        else ""
    )

    supplier_state = _invoice_state_code(store.get("state_code"), store_gstin)
    customer_state = _customer_state_code(customer)

    # Inter-state only when BOTH states are known and differ. Missing customer
    # state -> assume intra (CGST+SGST), the safe default for a single-state
    # retailer; flag it so the caller/print layer can note the assumption.
    if customer_state and supplier_state:
        interstate = customer_state != supplier_state
        assumed = False
    else:
        interstate = False
        assumed = True

    place_of_supply = customer_state or supplier_state

    # Aggregate stored per-line taxable + tax by GST rate.
    per_rate: dict = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        try:
            rate = round(float(it.get("gst_rate") or 0.0), 2)
        except (TypeError, ValueError):
            rate = 0.0
        try:
            taxable = float(it.get("taxable_value") or 0.0)
        except (TypeError, ValueError):
            taxable = 0.0
        try:
            tax = float(it.get("tax_amount") or 0.0)
        except (TypeError, ValueError):
            tax = 0.0
        agg = per_rate.setdefault(rate, {"taxable": 0.0, "tax": 0.0})
        agg["taxable"] = round(agg["taxable"] + taxable, 2)
        agg["tax"] = round(agg["tax"] + tax, 2)

    rows = []
    for rate in sorted(per_rate.keys()):
        taxable = round(per_rate[rate]["taxable"], 2)
        tax = round(per_rate[rate]["tax"], 2)
        if interstate:
            cgst = 0.0
            sgst = 0.0
            igst = tax
        else:
            cgst = round(tax / 2.0, 2)
            # Residual on SGST so a half-paisa never drifts one-sided; keeps
            # cgst + sgst == the stored line tax exactly.
            sgst = round(tax - cgst, 2)
            igst = 0.0
        rows.append(
            {
                "rate": rate,
                "taxable": taxable,
                "cgst": cgst,
                "sgst": sgst,
                "igst": igst,
                "tax": tax,
            }
        )

    totals = {
        "taxable": round(sum(r["taxable"] for r in rows), 2),
        "cgst": round(sum(r["cgst"] for r in rows), 2),
        "sgst": round(sum(r["sgst"] for r in rows), 2),
        "igst": round(sum(r["igst"] for r in rows), 2),
        "tax": round(sum(r["tax"] for r in rows), 2),
    }
    return {
        "place_of_supply": place_of_supply,
        "place_of_supply_assumed": assumed,
        "interstate": interstate,
        "store_gstin": store_gstin,
        "customer_gstin": customer_gstin,
        "rows": rows,
        "totals": totals,
    }


@router.get("/{order_id}/invoice")
async def get_invoice(order_id: str, current_user: dict = Depends(get_current_user)):
    """Get/generate invoice for order"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        # IDOR guard: mirror GET /{order_id} -- an invoice carries customer
        # name/GSTIN + line-level pricing; only a caller with access to the
        # order's store may read it (SUPERADMIN/ADMIN pass through).
        validate_store_access(order.get("store_id"), current_user)

        if order.get("status") == "DRAFT":
            raise HTTPException(
                status_code=400, detail="Cannot generate invoice for DRAFT orders"
            )

        # GST compliance: store must have GSTIN configured before generating invoice
        store_id = order.get("store_id") or current_user.get("active_store_id")
        store_doc = None
        if store_id:
            try:
                from ..dependencies import get_store_repository

                store_repo = get_store_repository()
                if store_repo:
                    store_doc = store_repo.find_by_id(store_id)
                    if store_doc and not store_doc.get("gstin"):
                        raise HTTPException(
                            status_code=400,
                            detail="Cannot generate invoice: store GSTIN is not configured. "
                            "Update store settings with a valid GSTIN first.",
                        )
            except HTTPException:
                raise
            except Exception:
                pass  # don't block invoice if store lookup fails

        # C-6 (DELTA 4): resolve the customer so the CGST/SGST/IGST split can
        # use the customer's state as the place of supply. Fail-soft: a missing
        # customer (e.g. walk-in) just leaves the customer state unknown, which
        # the split defaults to intra-state.
        customer_doc = None
        try:
            customer_id = order.get("customer_id")
            if customer_id:
                customer_repo = get_customer_repository()
                if customer_repo is not None:
                    customer_doc = customer_repo.find_by_id(customer_id)
        except Exception:
            customer_doc = None

        # Return existing invoice or generate a new one.
        #
        # GST compliance (P3-A): a NEW invoice gets a consecutive serial that
        # is unique per (configured-prefix, financial year) -- e.g.
        # BV/2026-27/000123 -- allocated atomically via a counters doc so two
        # simultaneous bills can't share a serial (Rule 46(b)). The prefix is
        # the store's CONFIGURED invoice_prefix (falling back to global invoice
        # settings, then "INV"); store_doc was already loaded above for the
        # GSTIN check, so we hand it straight to the allocator. OLD orders keep
        # whatever invoice_number they already carry (including the legacy
        # BV/INV/{year}/{order_id[:8]} format); we never rewrite a stored
        # number, so historical invoices stay resolvable exactly as before.
        invoice_number = order.get("invoice_number")
        if not invoice_number:
            # Best-effort unique index (idempotent; no-op if already present).
            try:
                repo.ensure_invoice_index()
            except Exception:  # noqa: BLE001 - index is defense-in-depth only
                pass
            invoice_number = repo.next_invoice_number(store_id, store_doc=store_doc)
            repo.set_invoice(order_id, invoice_number)

        # Convert items to camelCase
        items_formatted = [item_to_frontend(item) for item in order.get("items", [])]

        # C-6 (DELTA 4): per-rate CGST/SGST/IGST tax summary + place of supply.
        gst_split = _build_invoice_gst_split(
            order.get("items", []), store_doc, customer_doc
        )

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
            # C-6 (DELTA 4): GST place-of-supply split. All ADDITIVE -- existing
            # fields above are untouched.
            "placeOfSupply": gst_split["place_of_supply"],
            "placeOfSupplyAssumed": gst_split["place_of_supply_assumed"],
            "interstate": gst_split["interstate"],
            "storeGstin": gst_split["store_gstin"],
            "customerGstin": gst_split["customer_gstin"],
            "taxSummary": gst_split["rows"],
            "taxTotals": gst_split["totals"],
        }

    return {"invoiceNumber": "BV/INV/2024/0001", "orderId": order_id}


# ============================================================================
# POS-7: BOPIS / ship-from-store
# ============================================================================


class BOPISLine(BaseModel):
    """One item line that must be fulfilled from another store."""

    product_id: str
    sku: str = ""
    product_name: str = ""
    quantity: int = Field(..., ge=1)
    unit_price: float = Field(..., ge=0)
    source_store_id: str = Field(
        ..., description="The store that has the stock to ship"
    )
    source_store_name: str = ""


class BOPISRequest(BaseModel):
    items: list[BOPISLine]
    pickup_store_id: str = Field(
        ..., description="The store where the customer will collect the goods"
    )
    pickup_store_name: str = ""
    notes: str = ""
    priority: str = "normal"  # matches TransferPriority enum in transfers.py


@router.post("/{order_id}/bopis-transfer")
async def create_bopis_transfer(
    order_id: str,
    body: BOPISRequest,
    current_user: dict = Depends(get_current_user),
):
    """POS-7: Create inter-store transfer request(s) for items on an order
    that are not in stock at the selling store.

    Groups requested items by source_store_id and creates one transfer
    request per source store directed at the pickup store. Returns the
    transfer IDs so the POS display can track fulfillment status.

    Auth: same as order-confirm — any authenticated POS user.
    Fail-soft: if the transfers collection is unavailable, the transfer IDs
    are synthetic UUIDs so the order can still be finalised.
    """
    repo = get_order_repository()
    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        # IDOR guard: mirror GET /{order_id} -- only act on an order in a store
        # the caller can access (403 otherwise; SUPERADMIN/ADMIN pass through).
        validate_store_access(order.get("store_id"), current_user)
        if order.get("status") == "CANCELLED":
            raise HTTPException(
                status_code=400, detail="Cannot add BOPIS transfer to a cancelled order"
            )

    if not body.items:
        raise HTTPException(status_code=400, detail="No BOPIS items provided")

    # Group lines by source store.
    from collections import defaultdict

    by_source: dict = defaultdict(list)
    for line in body.items:
        by_source[line.source_store_id].append(line)

    # Import transfers helper (late import to avoid circular deps)
    try:
        from ..dependencies import get_db

        db = get_db()
        transfers_coll = (
            db.get_collection("stock_transfers")
            if db is not None and db.is_connected
            else None
        )
    except Exception:
        transfers_coll = None

    created_transfers = []
    user_id = current_user.get("user_id", "")
    now = datetime.now().isoformat()

    for source_store_id, lines in by_source.items():
        transfer_id = str(uuid.uuid4())
        transfer_doc = {
            "id": transfer_id,
            "order_id": order_id,
            "transfer_type": "store_to_store",
            "source": "bopis",
            "from_location_id": source_store_id,
            "from_location_name": lines[0].source_store_name or source_store_id,
            "to_location_id": body.pickup_store_id,
            "to_location_name": body.pickup_store_name or body.pickup_store_id,
            "status": "pending_approval",
            "priority": body.priority or "normal",
            "notes": body.notes or f"BOPIS order {order_id}",
            "requested_by": user_id,
            "created_at": now,
            "status_history": [
                {"status": "pending_approval", "changed_at": now, "changed_by": user_id}
            ],
            "items": [
                {
                    "transfer_item_id": str(uuid.uuid4()),
                    "product_id": ln.product_id,
                    "sku": ln.sku,
                    "product_name": ln.product_name,
                    "quantity_requested": ln.quantity,
                    "quantity_shipped": 0,
                    "quantity_received": 0,
                    "unit_cost": ln.unit_price,
                }
                for ln in lines
            ],
        }
        try:
            if transfers_coll is not None:
                transfers_coll.update_one(
                    {"id": transfer_id},
                    {"$set": transfer_doc},
                    upsert=True,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ORDERS] BOPIS transfer persist failed: %s", exc)

        created_transfers.append(
            {
                "transfer_id": transfer_id,
                "from_store_id": source_store_id,
                "to_store_id": body.pickup_store_id,
                "item_count": len(lines),
            }
        )

    # Stamp the transfer IDs onto the order doc for tracking.
    if repo is not None:
        try:
            existing_bopis = order.get("bopis_transfer_ids") or []
            existing_bopis += [t["transfer_id"] for t in created_transfers]
            repo.update(order_id, {"bopis_transfer_ids": existing_bopis})
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ORDERS] BOPIS order-stamp failed: %s", exc)

    return {
        "order_id": order_id,
        "transfers": created_transfers,
        "message": f"Created {len(created_transfers)} BOPIS transfer request(s)",
    }


# ============================================================================
# POS-6: UPI QR code endpoint
# ============================================================================


@router.get("/{order_id}/upi-qr")
async def get_upi_qr(
    order_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Return a UPI deep-link (and optional QR data-URI) for an order.

    Resolves the store's UPI VPA from the stores collection (upi_vpa field).
    Returns 400 when the store has no VPA configured so the operator knows
    exactly which setting to fill in.

    No Razorpay creds required -- the UPI link is pure NPCI standard.
    The QR data-URI (base-64 PNG) is included when the optional `qrcode`
    Python package is installed; otherwise only the link is returned.
    Fail-soft: any DB error returns a useful error response (never 500).
    """
    repo = get_order_repository()

    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    order = repo.find_by_id(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    # Store-scope check (same pattern as GET /orders/{id}).
    validate_store_access(order.get("store_id"), current_user)

    store_id = order.get("store_id") or current_user.get("active_store_id") or ""

    # Resolve the store's UPI VPA.
    try:
        from ..dependencies import get_db as _get_db_dep

        db_conn = _get_db_dep()
        raw_db = getattr(db_conn, "db", None) if db_conn is not None else None
    except Exception:
        raw_db = None

    from ..services.upi_qr import (
        _resolve_store_vpa,
        _resolve_merchant_name,
        build_upi_link,
        build_qr_data_uri,
    )

    vpa = _resolve_store_vpa(raw_db, store_id)
    if not vpa:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Store '{store_id}' does not have a UPI VPA configured. "
                "Go to Settings -> Stores -> UPI VPA and enter the store's "
                "Virtual Payment Address (e.g. bettervision.bok@upi)."
            ),
        )

    merchant = _resolve_merchant_name(raw_db, store_id)
    order_ref = order.get("order_number") or order_id
    grand_total = float(order.get("grand_total") or 0.0)

    upi_link = build_upi_link(vpa, merchant, grand_total, order_ref)
    qr_image = build_qr_data_uri(upi_link)

    return {
        "order_id": order_id,
        "order_number": order_ref,
        "store_id": store_id,
        "vpa": vpa,
        "merchant": merchant,
        "amount": grand_total,
        "currency": "INR",
        "upi_link": upi_link,
        "qr_image": qr_image,  # None when qrcode lib absent
        "qr_available": qr_image is not None,
    }
