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

def real_sale_match(db=None, *, orders=None) -> Dict[str, Any]:
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
    match: Dict[str, Any] = {"status": {"$nin": EXCLUDED_ORDER_STATUSES}}
    if orders is None:
        if db is None:
            return match
        orders = db.get_collection("orders") if hasattr(db, "get_collection") else db["orders"]
    not_sales = [
        o.get("order_id")
        for o in orders.find({"status": {"$in": _VOID_STATUSES}})
        if isinstance(o, dict)
        and o.get("order_id")
        and str(o.get("status") or "").upper() in _VOID
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
    if status in _VOID:
        return bool(str(order.get("invoice_number") or "").strip()) or (
            str(order.get("status_before_void") or "").upper() in _SHIPPED
        )
    return True
