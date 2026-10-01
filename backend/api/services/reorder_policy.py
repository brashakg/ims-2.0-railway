"""
IMS 2.0 - Per-product auto-reorder policy (owner decision 2026-07-04)
=====================================================================
`reorder_quantity` on a product now DEFAULTS TO -1, which means
"no auto-reorder": the product must not be auto-suggested or auto-ordered
by any reorder engine until someone explicitly sets a positive quantity.

Semantics (single source of truth for every consumer):
  - reorder_quantity  > 0  -> auto-reorder ENABLED (the qty to order)
  - reorder_quantity <= 0  -> auto-reorder DISABLED (the -1 default)
  - field missing / None   -> legacy doc created before the -1 default;
                              treated as ENABLED so behaviour only changes
                              once the backfill script (scripts/
                              backfill_reorder_quantity_minus1.py) or the
                              create door has stamped the field.

Consumers (each guards with auto_reorder_disabled()):
  - api/routers/inventory.py      /inventory/alerts restock suggestions
  - api/routers/jarvis.py         inventory-insight reorder recommendations
  - api/routers/vendors.py        POST /purchase-orders/from-forecast
  - api/routers/reports.py        purchase-recommendations report
  - api/routers/analytics_v2.py   demand-forecast reorder_recommended
  - api/services/buy_desk.py      Buy Desk buy_signal
  - agents/implementations/taskmaster.py  auto-draft PO
  - agents/implementations/oracle.py      predictive reorder proposals

REORDER LEVEL (owner rulings 2026-09-28 and 2026-10-01): the product's
`reorder_point` is its low-stock level. Only a whole number ABOVE 0 is a level.
-1 (the create door's default), 0, anything below 0, a missing field or garbage
all mean NOT SET: no low-stock alert, no top-up, and the screens print 'not
set'. A 0 is not set too because it can never fire while a unit is on the
shelf (the low-stock list only sees products with stock), and the old form
saved 0 for a blank box. A SKU with no product row at all (e.g. deleted in the
09-07 wipe) has no level either: never on a low-stock list, never topped up.

`reorder_level` / `is_low_stock` are THE rule, called with the PRODUCT (never a
stock_units row, which is one unit and has no level). `low_stock_rows` is THE
low-stock list for a store and the ONLY caller of StockRepository.find_low_stock
(tests/test_add_product_owner_rulings.py guards that): every endpoint, report,
dashboard count, transfer recommendation and Taskmaster's auto-reorder reads it.

No emojis (Windows cp1252). No direct DB access (low_stock_rows reads through
the repositories it is handed).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def auto_reorder_disabled(product: Any) -> bool:
    """True when the product has EXPLICITLY disabled auto-reorder
    (reorder_quantity present and <= 0, e.g. the -1 default the create door
    stamps). A missing/None/garbage value returns False (legacy behaviour)
    so pre-backfill docs keep working until they are stamped.

    Accepts a `products` spine doc (top-level reorder_quantity) or a
    `catalog_products` doc (inventory.reorder_quantity)."""
    if not isinstance(product, dict):
        return False
    rq = product.get("reorder_quantity")
    if rq is None:
        inv = product.get("inventory")
        if isinstance(inv, dict):
            rq = inv.get("reorder_quantity")
    if rq is None:
        return False
    try:
        return int(rq) <= 0
    except (TypeError, ValueError):
        return False


def reorder_level(product: Any) -> Optional[int]:
    """The product's low-stock level (a whole number above 0), or None = NOT
    SET: missing, 0, -1, below 0, garbage, or no product doc at all (see the
    module docstring).

    Accepts a `products` spine doc (top-level reorder_point) or a
    `catalog_products` doc (inventory.reorder_level)."""
    if not isinstance(product, dict):
        return None
    rp = product.get("reorder_point")
    if rp is None:
        inv = product.get("inventory")
        if isinstance(inv, dict):
            rp = inv.get("reorder_level")
    try:
        level = int(rp)
    except (TypeError, ValueError):
        return None
    return level if level > 0 else None


def is_low_stock(product: Any, on_hand: Any) -> bool:
    """True when the product has a level and on_hand is at or under it."""
    level = reorder_level(product)
    try:
        return level is not None and int(on_hand or 0) <= level
    except (TypeError, ValueError):
        return False


def low_stock_rows(stock_repo, product_repo, store_id) -> List[Dict[str, Any]]:
    """THE low-stock list for a store: find_low_stock's {_id, quantity} rows,
    kept only when the product's own level says low, each row carrying that
    `reorder_point`. No product repo / a failed read -> [] (never alert on a
    level we could not read)."""
    # ponytail: counts every product on hand at the store, then joins them in
    # one $in; move the level into the aggregation ($lookup) if a store ever
    # holds tens of thousands of products.
    rows = stock_repo.find_low_stock(store_id, threshold=10**9) or []
    pids = [str(r.get("_id")) for r in rows if r.get("_id")]
    if product_repo is None or not pids:
        return []
    try:
        products = {
            str(p.get("product_id")): p
            for p in product_repo.find_many({"product_id": {"$in": pids}}, limit=len(pids)) or []
        }
    except Exception:  # noqa: BLE001 - a failed read alerts nothing, never everything
        return []
    out = []
    for r in rows:
        # A unit whose product row is gone has no level: never low (F73).
        prod = products.get(str(r.get("_id")))
        if is_low_stock(prod, r.get("quantity")):
            out.append({**r, "reorder_point": reorder_level(prod)})
    return out
