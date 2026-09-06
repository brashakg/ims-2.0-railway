"""Shared orders plumbing: the router, the DB handle, the role tuples, the
GST per-category engine, the frontend serialisers, the status machine and
the Rx/stock hold helpers.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.

The import block below is the ORIGINAL orders.py block, kept whole on
purpose: a few names are no longer referenced in THIS file (the sub-modules
import what they need directly), but they are part of the module surface the
single file exposed and __init__.py re-exports them, so
``from api.routers.orders import cash_denom / is_online_store`` and the
tests that monkeypatch them keep working unchanged.
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

logger = logging.getLogger(__package__)

from ..auth import get_current_user
from ...dependencies import (
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
from database.repositories.order_repository import derive_bill_type
from ...services.rx_validation import (
    _validate_rx_number as _validate_rx_power,
    _validate_axis as _validate_rx_axis,
    is_rx_required_line as _is_rx_required_line,
)
from ...services import cash_denominations as cash_denom


def _get_db():
    """Raw MongoDB handle, or None when unavailable (mock / no-DB mode)."""
    try:
        from ...dependencies import get_db

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

# Roles permitted to CLOSE A HANDOVER (mark an order ready / delivered). This is
# deliberately NOT POS_WRITE_ROLES: that set's exclusion is scoped to order
# CREATION ("CASHIER is payment-only -> may record payments but not create
# orders"), and delivering is not creating.
#
# THE RULE, stated once so it is applied consistently: whoever this codebase lets
# SCAN A JOB TO DELIVERED at the pickup counter must also be able to CLOSE the
# order. Anything else leaves someone able to physically hand the glasses over
# but unable to finish the transaction -- revenue and NPS stuck at READY with no
# in-app escalation, and (because the Orders screen renders Mark Delivered with
# no role condition) a visible button that 403s in front of the customer.
#
# The scan roles are labels.SCAN_ROLES and workshop._LAB_SCAN_ROLES. Both carry
# CASHIER ("included so a front-desk cashier can scan a job to DELIVERED at
# pickup") AND WORKSHOP_STAFF. An earlier round applied this reasoning to CASHIER
# and missed WORKSHOP_STAFF, which sits in the identical position in the identical
# two tuples -- so both are here now. The QC gate, not the role list, is the real
# patient-safety control on this path.
#
# NOTE (open, owner decision): OPTOMETRIST is on the Orders nav in the frontend
# but is in NEITHER scan-role tuple, so it is deliberately not added here. That
# FE/BE mismatch predates this change and is flagged, not decided.
HANDOVER_ROLES = POS_WRITE_ROLES + ("CASHIER", "WORKSHOP_STAFF")

# Per-category GST is sourced from the canonical table in
# api/services/gst_rates.py (single source of truth, shared with the product
# master in products.py so a product's master rate == what POS bills it).
# Indian GST 2.0 (effective 22 Sep 2025): 5% for frames / spectacle &
# contact lenses / corrective spectacles, 18% otherwise. That table is the
# backend mirror of the frontend's getGSTRateByCategory.
from ...services.gst_rates import gst_rate_for_category as _gst_rate_for_category

# W1.4 / OS-005: shared ONLINE store-type detector (single backend source of
# truth, mirrors the frontend storeMode helper). Used to block manual POS
# billing under a stockless pooled ONLINE store.
from ...services.stores_util import is_online_store

# resolve_gst_rate layers the SUPERADMIN-editable HSN->GST master over the
# static canonical table, so a govt rate change is an in-app edit (Settings ->
# HSN & GST Rates) with no code change. Fail-soft: falls back to the static
# table when the master/DB is unavailable.
from ...services.gst_rates import resolve_gst_rate, gst_pricing_mode

# LOW_GST_CATEGORIES retained for any external reference / readability; it is
# the set of categories the canonical table bills at 5%.
from ...services.gst_rates import GST_CATEGORY_TABLE as _GST_CATEGORY_TABLE

# _normalize_category maps the many category spellings (FRAMES / FR / "frame")
# to the canonical hint used as a GST_CATEGORY_TABLE key. Used by the C-2 guard
# below to tell a KNOWN category from a junk/typo one.
from ...services.gst_rates import _normalize_category as _normalize_gst_category

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


def _stamp_status_actor_names(orders: list) -> None:
    """Name the person on the order Status Timeline, not their user id.

    Both writers stamp ``current_user["user_id"]`` into ``status_history``
    (the DRAFT entry at create, later entries via update_status), and the
    order itself stores ``created_by`` -- the timeline printed all of them
    raw. Resolve names on the way OUT, on COPIES of the nested history
    entries: the stored history is an audit trail and must keep the id and
    nothing else (see purchase_invoices._stamp_bill_actor_names for the
    house shape). Batched -- ONE users read for the whole page of orders.
    An id that no longer resolves (e.g. a deleted QA login) gets NO
    ``_name`` sibling and the screen prints the id verbatim -- never an
    invented name. Fail-soft: a dead users read leaves the ids in place.
    """
    from ...services.name_resolver import stamp_user_names

    rows = [o for o in orders if isinstance(o, dict)]
    hist_entries = []
    for o in rows:
        hist = [dict(e) for e in (o.get("status_history") or []) if isinstance(e, dict)]
        if hist:
            # Detach from the stored entries BEFORE stamping so the name can
            # never leak into a document that is later written back.
            o["status_history"] = hist
            hist_entries.extend(hist)
    stamp_user_names(_get_db(), rows + hist_entries, ("created_by", "changed_by"))


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
        "created_by_name": "createdByName",
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
                # Present only when the id resolved to a person (see
                # _stamp_status_actor_names) -- an unresolved id has no
                # _name sibling and the timeline prints the id verbatim.
                if entry.get("changed_by_name"):
                    history_entry["changedByName"] = entry.get("changed_by_name")
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


# ---------------------------------------------------------------------------
# Clinical Rx FLAG-AND-HOLD delivery guard (enforcement half of the online
# flag-and-hold policy). PR #947 shipped the VISIBLE + RELEASABLE half (the
# hold chip + the ADMIN/SUPERADMIN clear-rx-hold route) and explicitly deferred
# this ENFORCEMENT half.
#
# An online spectacle-lens order booked without a valid, customer-matching,
# non-expired prescription is stamped ``rx_pending`` + ``fulfillment_hold`` at
# ingest (owner decision 2026-06-30, services/online_rx_hold.py). Such an order
# must NOT be advanced to READY / DELIVERED / FULFILLED until the hold is
# cleared -- otherwise a spectacle sale could be dispensed / shipped with no
# prescription on file. The hold is released by ADMIN/SUPERADMIN via
# POST /api/v1/online-store/orders/{order_id}/clear-rx-hold, which sets BOTH
# flags back to False; a released (or never-held) order therefore passes here.
# ---------------------------------------------------------------------------

# Plain-English 400 messages shown to staff (ASCII only -- Windows cp1252).
# The refusal NAMES the hold it enforces: telling staff "Rx hold" for a stock
# miss sent them chasing a prescription that was never the problem.
RX_HOLD_BLOCK_DETAIL = (
    "This order is on Rx hold - clear the hold before marking it " "delivered/ready."
)
STOCK_HOLD_BLOCK_DETAIL = (
    "This order is on stock hold - it is paid but its units could not be "
    "claimed from stock (oversell). Resolve the stock, then clear the hold "
    "before marking it delivered/ready."
)
RX_AND_STOCK_HOLD_BLOCK_DETAIL = (
    "This order is on Rx hold AND stock hold - clear both before marking it "
    "delivered/ready."
)

# Before the stock hold owned its own field (stock_hold_reason), ingest
# stamped its reason INTO rx_hold_reason with this exact opening. Legacy
# held orders are recognised by it so they still release/report honestly.
_LEGACY_STOCK_REASON_PREFIX = "Stock could not be claimed"


def order_hold_kinds(order: Optional[dict]) -> list:
    """Which ACTIVE hold(s) an order carries: ["RX"], ["STOCK"], or both.

    An order is held while EITHER ``rx_pending`` OR ``fulfillment_hold`` is
    truthy (the release-side predicate in online_store_orders.clear_rx_hold
    sets both to False). Within a held order:
      * RX    -- rx_pending is set (the clinical flag-and-hold).
      * STOCK -- a stock-miss marker exists: its own stock_hold_reason field,
        or (legacy shape) the stock reason ingest used to write into
        rx_hold_reason.
      * A held order with NO marker at all is the historical Rx shape.
    Empty list == not held. Tolerant of a missing/None order."""
    if not order or not (order.get("rx_pending") or order.get("fulfillment_hold")):
        return []
    kinds = []
    if order.get("rx_pending"):
        kinds.append("RX")
    if order.get("stock_hold_reason") or str(
        order.get("rx_hold_reason") or ""
    ).startswith(_LEGACY_STOCK_REASON_PREFIX):
        kinds.append("STOCK")
    if not kinds:
        kinds.append("RX")
    return kinds


def order_has_active_rx_hold(order: Optional[dict]) -> bool:
    """True when an order still carries an ACTIVE flag-and-hold (Rx or stock
    -- both ride fulfillment_hold; see order_hold_kinds). A released or
    never-held order returns False and advances normally."""
    return bool(order_hold_kinds(order))


def assert_no_active_rx_hold(order: Optional[dict]) -> None:
    """Reject (400) any advance to READY / DELIVERED / FULFILLED on an order
    that still carries an active flag-and-hold, NAMING the hold (Rx, stock,
    or both). No-op for a non-held (or cleared) order, so it never blocks
    normal fulfillment."""
    kinds = order_hold_kinds(order)
    if not kinds:
        return
    if kinds == ["STOCK"]:
        detail = STOCK_HOLD_BLOCK_DETAIL
    elif "STOCK" in kinds:
        detail = RX_AND_STOCK_HOLD_BLOCK_DETAIL
    else:
        detail = RX_HOLD_BLOCK_DETAIL
    raise HTTPException(status_code=400, detail=detail)
