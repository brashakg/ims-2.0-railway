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
"""

from __future__ import annotations

from typing import Any, Dict

_NOT_SALE = ("CANCELLED", "DRAFT", "HISTORICAL")
_VOID = ("VOID", "VOIDED")
# status_before_void values that mean the goods had left the shop.
_SHIPPED = ("SHIPPED", "DELIVERED")

# Kept as a list: tests and older callers read the excluded statuses.
EXCLUDED_ORDER_STATUSES = [s for st in _NOT_SALE for s in (st, st.lower())]
_VOID_STATUSES = [s for st in _VOID for s in (st, st.lower())]
_SHIPPED_STATUSES = [s for st in _SHIPPED for s in (st, st.lower())]

# Top-level Mongo clause (spread it into a find / $match: `{**REAL_SALE_MATCH,
# "store_id": ...}`). It owns `$and`, so a query must not set its own `$and`.
REAL_SALE_MATCH: Dict[str, Any] = {
    "$and": [
        {"status": {"$nin": EXCLUDED_ORDER_STATUSES}},
        {
            "$or": [
                {"status": {"$nin": _VOID_STATUSES}},
                {"invoice_number": {"$nin": [None, ""]}},
                {"status_before_void": {"$in": _SHIPPED_STATUSES}},
            ]
        },
    ]
}


def is_real_sale(order: Dict[str, Any]) -> bool:
    """The Python twin of REAL_SALE_MATCH, for an order already in hand."""
    status = str((order or {}).get("status") or "").upper()
    if status in _NOT_SALE:
        return False
    if status in _VOID:
        return bool(str(order.get("invoice_number") or "").strip()) or (
            str(order.get("status_before_void") or "").upper() in _SHIPPED
        )
    return True
