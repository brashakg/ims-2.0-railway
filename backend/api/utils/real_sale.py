"""THE rule for which orders are real sales -- revenue in the books AND a supply
on the GST returns. One rule, so the books (P&L, cash flow, receivables, Tally,
reconciliation, the GST cross-check) and the returns (GSTR-1, GSTR-3B, the GST
report) can never disagree about an order.

Leaf module: imports nothing from the app.

Not a sale:
  * DRAFT      -- never booked;
  * CANCELLED  -- reversed before it was a sale;
  * HISTORICAL -- a pre-IMS import, settled outside IMS books;
  * a VOID / VOIDED order (deleted in Shopify, services/shopify_order_delete)
    that was NEVER invoiced and never shipped.

Owner ruling 2026-10-22: a VOID order that HAD been invoiced (it carries an
invoice_number) or had shipped is STILL a sale -- filed in the returns and
counted as revenue; reversing it takes a proper return / credit note. Only a
VOID with no invoice number that never shipped leaves both.

The GST-document view (``gst_documents=True``, PR #1264): only for the GST
returns that ALSO file credit notes (GSTR-1, GSTR-3B). There a CANCELLED order
stays filed when the refund-advance door cancelled it AFTER issuing a credit
note for its whole invoice (it carries an invoice_number and a COMPLETED
advance refund with that note): the invoice is filed and its note reverses it
-- dropping the invoice as well reversed the tax twice. Every other cancelled
order (an unpaid online cancel with an invoice number, a plain cancel) stays
out, and the books (P&L, receivables, Tally sales, the ticker) keep the
default rule, which drops every cancelled order.
"""

from __future__ import annotations

import re
from typing import Any, Dict

_NOT_SALE = ("CANCELLED", "DRAFT", "HISTORICAL")
_VOID = ("VOID", "VOIDED")
_CANCELLED = ("CANCELLED", "CANCELED")
# status_before_void values that mean the goods had left the shop.
_SHIPPED = ("SHIPPED", "DELIVERED")

# Kept as a list: tests and older callers read the excluded statuses.
EXCLUDED_ORDER_STATUSES = [s for st in _NOT_SALE for s in (st, st.lower())]
_VOID_STATUSES = [s for st in _VOID for s in (st, st.lower(), st.title())]
_NOT_SALE_RE = re.compile(r"^(cancelled|draft|historical)$", re.IGNORECASE)
_VOID_RE = re.compile(r"^void(ed)?$", re.IGNORECASE)
# The GST-document view judges cancelled orders one by one instead.
_GST_NOT_SALE = ("DRAFT", "HISTORICAL")
_GST_NOT_SALE_RE = re.compile(r"^(draft|historical)$", re.IGNORECASE)
_CANCELLED_STATUSES = [s for st in _CANCELLED for s in (st, st.lower(), st.title())]
_CANCELLED_RE = re.compile(r"^cancel(l)?ed$", re.IGNORECASE)


def _reversed_by_whole_credit_note(order: Dict[str, Any]) -> bool:
    """An invoiced order the refund-advance door cancelled after a credit note
    for its whole invoice was issued and its refund COMPLETED."""
    return (
        bool(str(order.get("invoice_number") or "").strip())
        and str(order.get("advance_refund_status") or "").upper() == "COMPLETED"
        and bool(str(order.get("advance_refund_credit_note") or "").strip())
    )

def real_sale_match(db=None, *, orders=None, gst_documents: bool = False) -> Dict[str, Any]:
    """THE rule as a Mongo filter to spread into a query
    (`{**real_sale_match(db), "store_id": ...}`). Pass the database, or the
    orders collection as `orders=`; neither gives the status part alone.

    Two steps, deliberately: the status part is a plain field filter, and the
    VOID orders that are NOT sales (is_real_sale, below) are read first and
    excluded by order_id -- no top-level $or, which every caller's query
    would have to merge with its own.
    ponytail: one extra read of the VOID orders per query; fine while deleted
    Shopify orders number in the hundreds -- stamp a `real_sale` bool on the
    order at void time if they ever number in the tens of thousands."""
    # Case-insensitive, as is_real_sale: Mongo's $nin / $in take a regex
    # beside the plain strings (`Cancelled`, `Void` count too).
    if gst_documents:
        not_sale = [s for st in _GST_NOT_SALE for s in (st, st.lower())] + [_GST_NOT_SALE_RE]
        judged = [*_VOID_STATUSES, _VOID_RE, *_CANCELLED_STATUSES, _CANCELLED_RE]
        judged_upper = _VOID + _CANCELLED
    else:
        not_sale = [*EXCLUDED_ORDER_STATUSES, _NOT_SALE_RE]
        judged = [*_VOID_STATUSES, _VOID_RE]
        judged_upper = _VOID
    match: Dict[str, Any] = {"status": {"$nin": not_sale}}
    if not gst_documents:
        # Books: a refund that went through with its cancel still pending is
        # not a sale either -- it reads the same as once the cancel lands.
        match["advance_refund_status"] = {"$nin": ["COMPLETED", "completed"]}
    if orders is None:
        if db is None:
            return match
        orders = db.get_collection("orders") if hasattr(db, "get_collection") else db["orders"]
    # ponytail: the GST view also reads every cancelled order per query; fine
    # while they number in the hundreds -- stamp a bool if they ever do not.
    out = [
        o
        for o in orders.find({"status": {"$in": judged}})
        if isinstance(o, dict)
        and str(o.get("status") or "").upper() in judged_upper
        and not is_real_sale(o, gst_documents=gst_documents)
    ]
    if gst_documents:
        # By _id -- always present and unique -- so a cancelled order with no
        # order_id is not filed, and a cancelled twin of a live order_id does
        # not pull the live invoice out of the return.
        ids = [o["_id"] for o in out if o.get("_id") is not None]
        if ids:
            match["_id"] = {"$nin": ids}
        out = [o for o in out if o.get("_id") is None]  # an in-memory test double only
    not_sales = [o.get("order_id") for o in out if o.get("order_id")]
    if not_sales:
        match["order_id"] = {"$nin": not_sales}
    return match


def is_real_sale(order: Dict[str, Any], gst_documents: bool = False) -> bool:
    """THE definition, for an order in hand (real_sale_match is its query).
    ``gst_documents``: the GST-document view (module docstring)."""
    status = str((order or {}).get("status") or "").upper()
    if gst_documents and status in _CANCELLED:
        return _reversed_by_whole_credit_note(order)
    if status in _NOT_SALE:
        return False
    if not gst_documents and str((order or {}).get("advance_refund_status") or "").upper() == "COMPLETED":
        return False
    if status in _VOID:
        return bool(str(order.get("invoice_number") or "").strip()) or (
            str(order.get("status_before_void") or "").upper() in _SHIPPED
        )
    return True
