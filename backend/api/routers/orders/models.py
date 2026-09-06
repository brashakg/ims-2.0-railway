"""Order create/update request models and the SUPERADMIN edit payloads.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.
"""

import math
from datetime import date, timedelta
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from .pricing import OrderItemCreate


class SalespersonSplit(BaseModel):
    """One share of a two-way sales credit (owner spec 13, default 50/50).

    Percent is the SHARE OF CREDIT, not money: it splits incentive points and
    attribution, never the customer's bill. The bill total is untouched.
    """

    salesperson_id: str = Field(..., min_length=1)
    salesperson_name: Optional[str] = None
    percent: float = Field(..., gt=0, le=100)

    @field_validator("salesperson_id")
    @classmethod
    def _strip_id(cls, v: str) -> str:
        # Normalise at the door so the id that is VALIDATED is the id that is
        # PERSISTED. Otherwise " u-a " passes every check and is then stored
        # verbatim as a primary that matches no user.
        return str(v).strip()


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
    # OWNER SPEC 13: sales credit may be SPLIT TWO WAYS (default 50/50) when two
    # people worked the sale — the POS chip shows "Priya 50 · Arjun 50".
    # ADDITIVE: salesperson_id/_name remain the PRIMARY attribution every
    # existing reader (reports, incentive queries, exports) already uses; the
    # split is the finer record laid alongside. When a split is supplied the
    # primary is derived from it, so the two can never disagree.
    salespersons: Optional[List["SalespersonSplit"]] = None
    visufit_id: Optional[str] = None

    @field_validator("salespersons")
    @classmethod
    def _validate_split(cls, v, info):
        # NOTE: a zero/negative share and a >100 share are already rejected
        # declaratively by SalespersonSplit.percent (gt=0, le=100). Re-checking
        # them here would be the same rule written twice, free to drift.
        if v is None:
            return v
        if len(v) == 0:
            return None
        if len(v) > 2:
            raise ValueError("Sales credit splits two ways at most.")
        total = round(sum(float(s.percent) for s in v), 2)
        if abs(total - 100.0) > 0.01:
            raise ValueError(f"Sales credit must add up to 100% (got {total}%).")
        ids = [s.salesperson_id for s in v]
        if any(not i for i in ids):
            raise ValueError("Every split entry needs a salesperson.")
        if len(set(ids)) != len(ids):
            raise ValueError("The same salesperson cannot take both shares.")
        # The picker and the split must agree about who sold it. Silently
        # letting the split overwrite a conflicting salesperson_id would hide a
        # client bug in staff pay, so a picker naming nobody in the split is a
        # 422. When it DOES name someone, its name belongs to that entry --
        # resolved here so the "who does salesperson_name describe" rule lives
        # in one place instead of being re-derived at the persistence site.
        picked = str(info.data.get("salesperson_id") or "").strip()
        if picked:
            if picked not in ids:
                raise ValueError(
                    "salesperson_id must be one of the split salespersons."
                )
            for s in v:
                if s.salesperson_id == picked and not s.salesperson_name:
                    s.salesperson_name = info.data.get("salesperson_name")
        return v

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
