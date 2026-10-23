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

The same holds for a CANCELLED (or CANCELED) order that carries an invoice
number (PR #1264, the refund-advance door: an invoiced order is cancelled only
after a credit note for the whole invoice). Its invoice stays filed and its
credit note does the reversal -- dropping the invoice as well reversed the
tax twice. A cancelled order with no invoice number is not a sale.
"""

from __future__ import annotations

import re
from typing import Any, Dict

_NOT_SALE = ("DRAFT", "HISTORICAL")
# Not a sale UNLESS invoiced (CANCELLED) / invoiced or shipped (VOID): these
# statuses are read and judged one by one (is_real_sale).
_CANCELLED = ("CANCELLED", "CANCELED")
_VOID = ("VOID", "VOIDED")
# status_before_void values that mean the goods had left the shop.
_SHIPPED = ("SHIPPED", "DELIVERED")

# Kept as a list: tests and older callers read the excluded statuses (an
# invoiced CANCELLED order is the exception real_sale_match handles).
EXCLUDED_ORDER_STATUSES = [s for st in _NOT_SALE + ("CANCELLED",) for s in (st, st.lower())]
_JUDGED = _CANCELLED + _VOID
_JUDGED_STATUSES = [s for st in _JUDGED for s in (st, st.lower(), st.title())]
_NOT_SALE_RE = re.compile(r"^(draft|historical)$", re.IGNORECASE)
_JUDGED_RE = re.compile(r"^(void(ed)?|cancel(l)?ed)$", re.IGNORECASE)

def real_sale_match(db=None, *, orders=None) -> Dict[str, Any]:
    """THE rule as a Mongo filter to spread into a query
    (`{**real_sale_match(db), "store_id": ...}`). Pass the database, or the
    orders collection as `orders=`; neither gives the status part alone.

    Two steps, deliberately: the status part is a plain field filter, and the
    VOID orders that are NOT sales (is_real_sale, below) are read first and
    excluded by order_id -- no top-level $or, which every caller's query
    would have to merge with its own.
    ponytail: one extra read of the VOID and CANCELLED orders per query; fine
    while they number in the hundreds -- stamp a `real_sale` bool on the order
    at void / cancel time if they ever number in the tens of thousands."""
    # Case-insensitive, as is_real_sale: Mongo's $nin / $in take a regex
    # beside the plain strings (`Cancelled`, `Void` count too).
    match: Dict[str, Any] = {
        "status": {"$nin": [*(s for st in _NOT_SALE for s in (st, st.lower())), _NOT_SALE_RE]}
    }
    if orders is None:
        if db is None:
            return match
        orders = db.get_collection("orders") if hasattr(db, "get_collection") else db["orders"]
    not_sales = [
        o.get("order_id")
        for o in orders.find({"status": {"$in": [*_JUDGED_STATUSES, _JUDGED_RE]}})
        if isinstance(o, dict)
        and o.get("order_id")
        and str(o.get("status") or "").upper() in _JUDGED
        and not is_real_sale(o)
    ]
    if not_sales:
        match["order_id"] = {"$nin": not_sales}
    return match


def is_real_sale(order: Dict[str, Any]) -> bool:
    """THE definition, for an order in hand (real_sale_match is its query)."""
    status = str((order or {}).get("status") or "").upper()
    if status in _NOT_SALE:
        return False
    if status in _CANCELLED:
        return bool(str(order.get("invoice_number") or "").strip())
    if status in _VOID:
        return bool(str(order.get("invoice_number") or "").strip()) or (
            str(order.get("status_before_void") or "").upper() in _SHIPPED
        )
    return True
