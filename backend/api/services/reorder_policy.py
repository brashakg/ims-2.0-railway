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
  - discontinued()         -> DISABLED whatever the quantity: a
                              discontinued (soft-deleted) product is never
                              suggested for reorder, even while units of it
                              are still on a shelf (audit F48). A PROVISIONAL
                              product (ruling 13: bought before it was
                              catalogued) is inactive too but NOT discontinued.

Consumers (each guards with auto_reorder_disabled()):
  - api/routers/inventory.py      /inventory/alerts restock suggestions
  - api/routers/jarvis.py         inventory-insight reorder recommendations
  - api/routers/vendors.py        POST /purchase-orders/from-forecast
  - api/routers/reports.py        purchase-recommendations report
  - api/routers/analytics_v2.py   demand-forecast reorder_recommended
  - api/services/buy_desk.py      Buy Desk buy_signal
  - agents/implementations/taskmaster.py  auto-draft PO
  - agents/implementations/oracle.py      predictive reorder proposals

No emojis (Windows cp1252). Pure function, no DB access.
"""

from __future__ import annotations

from typing import Any


def discontinued(product: Any) -> bool:
    """THE discontinued rule: inactive (is_active False) and not provisional.

    product_master stamps a buy-first-catalogue-later product `provisional`
    and inactive precisely so it is never confused with one someone
    deliberately deactivated; nothing flips it active when cataloguing
    finishes, so is_active alone would call a frame bought last week
    'Discontinued - not reordered'."""
    return (
        isinstance(product, dict)
        and product.get("is_active") is False
        and not product.get("provisional")
    )


def auto_reorder_disabled(product: Any) -> bool:
    """True when the product has EXPLICITLY disabled auto-reorder
    (reorder_quantity present and <= 0, e.g. the -1 default the create door
    stamps) or is discontinued (discontinued()). A missing/None/garbage
    quantity returns False (legacy behaviour) so pre-backfill docs keep
    working until they are stamped.

    Accepts a `products` spine doc (top-level reorder_quantity) or a
    `catalog_products` doc (inventory.reorder_quantity)."""
    if not isinstance(product, dict):
        return False
    if discontinued(product):
        return True
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
