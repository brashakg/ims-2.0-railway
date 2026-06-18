"""
IMS 2.0 - SUPERADMIN post-creation order edit (build item #16)
==============================================================
Revenue / GST / audit-critical helpers for the SUPERADMIN-only ability to edit
an order AFTER it has left DRAFT (CONFIRMED / PROCESSING / READY) and, once an
invoice has been ISSUED, to make a GST-compliant correction without ever
silently mutating the issued invoice.

This module is PURE / DB-free on purpose so the money + GST + note math is unit
testable in isolation. The router (api/routers/orders.py) owns persistence,
RBAC, period-lock, audit-row writes and invoice-serial allocation; it imports:

  * rebuild_edited_line(...)    -> normalise one incoming edit line into the
                                   persisted order-line shape create_order writes.
  * recompute_totals(items, cart_discount_pct) -> per-category GST aggregation
                                   (delegates to the canonical _compute_per_category_gst
                                   so the edit path bills EXACTLY like create/add/remove).
  * order_money_snapshot(order) -> a small dict of the money-relevant fields
                                   used as the before/after audit snapshot + the
                                   delta basis for a credit / debit note.
  * compute_invoice_delta(before, after) -> {direction, amount, ...} describing
                                   whether the change refunds (CREDIT note) or
                                   collects (DEBIT note) and by how much.
  * build_credit_note_doc(...)  -> the credit/debit-note document linked to the
                                   original invoice.

GST law context (SYSTEM_INTENT / project_business_rules):
  * An issued tax invoice is immutable (Rule 46 + serial uniqueness). A
    post-issue correction is EITHER a fresh REVISED invoice (new serial; the
    original is marked superseded/void with a pointer) OR a credit note (for a
    reduction) / debit note (for an increase) carrying the delta and linked to
    the original invoice number. SUPERADMIN chooses per case (owner decision).
  * Period lock still applies in BOTH the router paths (a locked month -> 423).

No emojis (Windows cp1252). ASCII only.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional


# Order statuses that are eligible for the SUPERADMIN pre-invoice inline edit.
# DRAFT keeps the existing PUT /{order_id} path; DELIVERED / CANCELLED are
# terminal and must use a credit/debit note or a revised invoice, never an
# inline edit of the line items.
PRE_INVOICE_EDITABLE_STATUSES = ("CONFIRMED", "PROCESSING", "READY")

# Note types for the post-issue correction.
CREDIT_NOTE = "CREDIT_NOTE"
DEBIT_NOTE = "DEBIT_NOTE"


def _f(value: Any, default: float = 0.0) -> float:
    """Coerce to float, fail-soft to ``default`` on junk / None."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def rebuild_edited_line(line: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise ONE incoming superadmin-edit line into the persisted order-line
    shape (the same keys create_order writes), recomputing the per-line
    discount + ``item_total`` from unit_price / quantity / discount_percent.

    The caller has already RBAC-gated this to SUPERADMIN, so the catalog
    MRP / cost / cap guards that constrain ordinary POS staff are intentionally
    NOT re-applied here -- a SUPERADMIN correction (e.g. honouring a price the
    customer was promised, fixing a mis-keyed line) is an audited override by
    design. Power / GST resolution is left to recompute_totals via the canonical
    per-category helper, exactly like the create / add paths.

    An existing ``item_id`` is preserved (so the line keeps its identity across
    the edit); a new line gets a fresh uuid. Returns a fresh dict (never mutates
    the input).
    """
    qty = int(line.get("quantity") or 1)
    if qty < 1:
        qty = 1
    unit_price = _f(line.get("unit_price"))
    disc_pct = _f(line.get("discount_percent"))
    if disc_pct < 0:
        disc_pct = 0.0
    if disc_pct > 100:
        disc_pct = 100.0

    gross = round(unit_price * qty, 2)
    discount_amount = round(gross * (disc_pct / 100.0), 2)
    item_total = round(gross - discount_amount, 2)

    out: Dict[str, Any] = {
        "item_id": str(line.get("item_id") or uuid.uuid4()),
        "item_type": line.get("item_type"),
        "product_id": line.get("product_id"),
        "product_name": line.get("product_name"),
        "sku": line.get("sku"),
        "brand": line.get("brand"),
        "subbrand": line.get("subbrand"),
        "category": line.get("category"),
        "hsn_code": line.get("hsn_code") or line.get("hsn"),
        "quantity": qty,
        "unit_price": unit_price,
        "discount_percent": disc_pct,
        "discount_amount": discount_amount,
        "item_total": item_total,
        # Carry COGS + clinical fields through unchanged when supplied so a
        # historical P&L / lab spec is not silently dropped on an edit.
        "cost_at_sale": line.get("cost_at_sale"),
        "prescription_id": line.get("prescription_id"),
        "lens_options": line.get("lens_options"),
        "lens_details": line.get("lens_details"),
        "sph": line.get("sph"),
        "cyl": line.get("cyl"),
        "add": line.get("add"),
        "axis": line.get("axis"),
        "item_note": line.get("item_note") or None,
    }
    return out


def recompute_totals(items: List[Dict[str, Any]], cart_discount_pct: float, gst_fn) -> Dict[str, Any]:
    """Recompute order money via the canonical per-category GST helper.

    ``gst_fn`` is orders._compute_per_category_gst, injected so this module
    stays DB / import-cycle free. It stamps gst_rate / taxable_value /
    tax_amount onto each item IN PLACE (same as create) and returns the
    aggregate dict; we add the resolved ``grand_total`` on top.
    """
    gst = gst_fn(items, cart_discount_pct)
    grand_total = round(gst["taxable"] + gst["tax"], 2)
    gst["grand_total"] = grand_total
    return gst


def order_money_snapshot(order: Dict[str, Any]) -> Dict[str, Any]:
    """A compact, money-relevant snapshot used as the before/after audit state
    AND the basis for an invoice delta. Pure read; never mutates ``order``."""
    return {
        "subtotal": _f(order.get("subtotal")),
        "cart_discount_percent": _f(order.get("cart_discount_percent")),
        "cart_discount_amount": _f(order.get("cart_discount_amount")),
        "total_discount": _f(order.get("total_discount")),
        "tax_amount": _f(order.get("tax_amount")),
        "grand_total": _f(order.get("grand_total")),
        "amount_paid": _f(order.get("amount_paid")),
        "balance_due": _f(order.get("balance_due")),
        "customer_id": order.get("customer_id"),
        "customer_name": order.get("customer_name"),
        "invoice_number": order.get("invoice_number"),
        "item_count": len(order.get("items") or []),
        "items": [
            {
                "item_id": it.get("item_id"),
                "product_name": it.get("product_name"),
                "quantity": it.get("quantity"),
                "unit_price": _f(it.get("unit_price")),
                "discount_percent": _f(it.get("discount_percent")),
                "item_total": _f(it.get("item_total")),
                "gst_rate": _f(it.get("gst_rate")),
                "taxable_value": _f(it.get("taxable_value")),
                "tax_amount": _f(it.get("tax_amount")),
            }
            for it in (order.get("items") or [])
        ],
    }


def compute_invoice_delta(
    before: Dict[str, Any], after: Dict[str, Any]
) -> Dict[str, Any]:
    """Describe the money change between two snapshots for note issuance.

    Returns:
      {
        "old_grand_total": float,
        "new_grand_total": float,
        "delta": float,            # signed: new - old
        "abs_delta": float,        # always >= 0 (the note amount)
        "old_tax": float,
        "new_tax": float,
        "tax_delta": float,        # signed change in GST component
        "direction": "REDUCE" | "INCREASE" | "NONE",
        "note_type": CREDIT_NOTE | DEBIT_NOTE | None,
      }

    * grand_total went DOWN  -> the customer is owed money -> CREDIT note.
    * grand_total went UP    -> the customer owes more     -> DEBIT note.
    * unchanged              -> no note needed.
    """
    old_gt = round(_f(before.get("grand_total")), 2)
    new_gt = round(_f(after.get("grand_total")), 2)
    delta = round(new_gt - old_gt, 2)
    old_tax = round(_f(before.get("tax_amount")), 2)
    new_tax = round(_f(after.get("tax_amount")), 2)

    if delta < 0:
        direction = "REDUCE"
        note_type: Optional[str] = CREDIT_NOTE
    elif delta > 0:
        direction = "INCREASE"
        note_type = DEBIT_NOTE
    else:
        direction = "NONE"
        note_type = None

    return {
        "old_grand_total": old_gt,
        "new_grand_total": new_gt,
        "delta": delta,
        "abs_delta": round(abs(delta), 2),
        "old_tax": old_tax,
        "new_tax": new_tax,
        "tax_delta": round(new_tax - old_tax, 2),
        "direction": direction,
        "note_type": note_type,
    }


def generate_note_number(note_type: str) -> str:
    """Short, human-friendly credit/debit-note id, e.g. CN-250618-AB12CD.

    A SEPARATE series from the GST invoice serial: a note is not an invoice and
    must not consume an invoice number (it references one). Distinct CN/DN
    prefixes keep the two ledgers unambiguous in Tally + GSTR-1 CDNR.
    """
    prefix = "DN" if note_type == DEBIT_NOTE else "CN"
    stamp = datetime.now().strftime("%y%m%d")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:6].upper()}"


def build_credit_note_doc(
    *,
    order: Dict[str, Any],
    delta: Dict[str, Any],
    reason: str,
    user_id: Optional[str],
    note_number: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a credit-note (reduction) or debit-note (increase) document linked
    to the original ISSUED invoice. The ORIGINAL invoice stays intact; this note
    carries ONLY the signed delta + its GST component so GSTR-1 CDNR + Tally
    reconcile against the original invoice number.

    Pure: returns the document; the router persists it (collection
    ``credit_note_ledger`` for a credit note so it shares the store-credit
    ledger the customer card / POS-redeem reads; ``debit_note_ledger`` for a
    debit note). Never raises.
    """
    note_type = delta["note_type"] or CREDIT_NOTE
    amount = delta["abs_delta"]
    now = datetime.now().isoformat()
    return {
        "note_number": note_number or generate_note_number(note_type),
        "note_type": note_type,  # CREDIT_NOTE | DEBIT_NOTE
        "order_id": order.get("order_id"),
        "order_number": order.get("order_number"),
        "original_invoice_number": order.get("invoice_number"),
        "customer_id": order.get("customer_id"),
        "customer_name": order.get("customer_name"),
        "store_id": order.get("store_id"),
        # Signed change in grand-total + its GST component, both rounded.
        "amount": amount,
        "tax_amount": round(abs(delta["tax_delta"]), 2),
        "taxable_amount": round(amount - abs(delta["tax_delta"]), 2),
        "old_grand_total": delta["old_grand_total"],
        "new_grand_total": delta["new_grand_total"],
        "reason": reason,
        "issued_by": user_id,
        "status": "ISSUED",
        "created_at": now,
        "source": "SUPERADMIN_ORDER_EDIT",
    }
