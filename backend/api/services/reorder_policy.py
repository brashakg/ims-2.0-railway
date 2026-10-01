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
  - agents/implementations/oracle.py      predictive reorder proposals

No emojis (Windows cp1252). auto_reorder_disabled / reorder_level /
is_low_stock are pure; on_hand / low_stock_rows read the collections handed in.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


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


# ---------------------------------------------------------------------------
# REORDER LEVEL - PER SHOP (owner ruling D12, 2026-09-29; audit F73)
# ---------------------------------------------------------------------------
# A product's low-stock level is set shop by shop:
#     products.reorder_levels = {<store_id>: int}
# -1, a missing shop, garbage or no shop at all = NOT SET = no low-stock alert
# (screens say "not set", never -1). 0 is a real level (alert once the shop
# runs out). The old chain-wide `reorder_point` is never read: a shop without
# its own level has none. reorder_level / is_low_stock are THE rule, the shop
# is a REQUIRED keyword so a reader that forgets it fails loudly instead of
# reading a chain value; low_stock_rows is THE low-stock list every count,
# list, dashboard and agent reads. The only writer is
# PUT /api/v1/inventory/reorder-levels/{product_id}.

LEVELS_FIELD = "reorder_levels"


def reorder_level(product: Any, *, store_id: Optional[str]) -> Optional[int]:
    """THIS shop's reorder level for the product, or None = not set."""
    if not store_id or not isinstance(product, dict):
        return None
    levels = product.get(LEVELS_FIELD)
    if not isinstance(levels, dict):
        return None
    try:
        level = int(levels.get(store_id))
    except (TypeError, ValueError):
        return None
    return level if level >= 0 else None


def is_low_stock(product: Any, on_hand: Any, *, store_id: Optional[str]) -> bool:
    """True when the shop has a level and its on-hand is at or under it."""
    level = reorder_level(product, store_id=store_id)
    try:
        return level is not None and int(on_hand or 0) <= level
    except (TypeError, ValueError):
        return False


def _coll(source):
    """A repository (its .collection) or a raw collection. isinstance, never
    getattr: a pymongo Collection answers ANY attribute with a sub-collection."""
    from database.repositories.base_repository import BaseRepository

    return source.collection if isinstance(source, BaseRepository) else source


def on_hand(
    stock_units, *, store_id: Optional[str], product_ids=None
) -> Dict[Tuple[str, str], int]:
    """Sellable units per (product_id, store_id) -- the count a level is
    compared with (the shared on-hand rule, so the stock ledger's count and
    this one agree). store_id None = every shop. {} if the read fails."""
    from .item_events import on_hand_match

    stock_units = _coll(stock_units)
    if stock_units is None:
        return {}
    match: Dict[str, Any] = dict(on_hand_match())
    if store_id:
        match["store_id"] = store_id
    if product_ids is not None:
        match["product_id"] = {"$in": list(product_ids)}
    out: Dict[Tuple[str, str], int] = {}
    try:
        for r in stock_units.aggregate(
            [
                {"$match": match},
                {
                    "$group": {
                        "_id": {"p": "$product_id", "s": "$store_id"},
                        # One row == one unit; a legacy row with no quantity is 1.
                        "qty": {"$sum": {"$ifNull": ["$quantity", 1]}},
                    }
                },
            ]
        ):
            key = r.get("_id") or {}
            if key.get("p") is not None:
                out[(str(key["p"]), str(key.get("s")))] = int(r.get("qty") or 0)
    except Exception:  # noqa: BLE001 - unreadable stock alerts nothing
        return {}
    return out


def low_stock_rows(
    products, stock_units, *, store_id: Optional[str]
) -> List[Dict[str, Any]]:
    """THE low-stock list: one row per (product, shop) at or under that
    shop's own level -- a shop that has sold out (0 units) included.
    store_id None = every shop (the owner's all-shops views). Each row:
    {_id, product_id, store_id, quantity, reorder_point, sku, name}.
    `products` / `stock_units`: repositories or raw collections. A missing
    collection or a failed read -> [] (never alert on a level we could not
    read)."""
    # ponytail: reads every product that has ANY level, then filters in
    # Python; move the shop filter into the query if the catalogue reaches
    # tens of thousands of levelled products.
    products = _coll(products)
    if products is None or _coll(stock_units) is None:
        return []
    try:
        docs = list(
            products.find(
                {LEVELS_FIELD: {"$exists": True}},
                {
                    "_id": 0,
                    "product_id": 1,
                    "sku": 1,
                    "name": 1,
                    "is_active": 1,
                    LEVELS_FIELD: 1,
                },
            )
        )
    except Exception:  # noqa: BLE001
        return []
    docs = [d for d in docs if d.get("product_id")]
    if not docs:
        return []
    counts = on_hand(
        stock_units, store_id=store_id, product_ids=[str(d["product_id"]) for d in docs]
    )
    rows: List[Dict[str, Any]] = []
    for p in docs:
        pid = str(p["product_id"])
        levels = p.get(LEVELS_FIELD)
        shops = (
            [store_id] if store_id else list(levels) if isinstance(levels, dict) else []
        )
        for shop in shops:
            qty = counts.get((pid, shop), 0)
            # A discontinued product that has sold out here is nothing to restock.
            if not qty and p.get("is_active") is False:
                continue
            if is_low_stock(p, qty, store_id=shop):
                rows.append(
                    {
                        "_id": pid,
                        "product_id": pid,
                        "store_id": shop,
                        "quantity": qty,
                        "reorder_point": reorder_level(p, store_id=shop),
                        "sku": p.get("sku") or "",
                        "name": p.get("name") or "",
                    }
                )
    rows.sort(key=lambda r: (r["quantity"], r["store_id"], r["product_id"]))
    return rows
