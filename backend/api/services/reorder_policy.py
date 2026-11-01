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
                              catalogued) is inactive too but NOT discontinued
                              -- until it is switched on or deleted.

Consumers: buy_qty (below) applies it, and every reader that suggests a buy
asks buy_qty -- services/reorder_list.py (the Reorder list), the stock alerts,
Jarvis, the purchase-recommendations report, the analytics demand forecast and
ORACLE's draft-PO proposals.

No emojis (Windows cp1252). auto_reorder_disabled / reorder_level /
is_low_stock are pure; on_hand / low_stock_rows read the collections handed in.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


# Every product field discontinued() reads. A reader that projects products
# asks for all of these, or the rule judges a doc missing half its facts.
DISCONTINUED_FIELDS = (
    "is_active", "provisional", "bought_before_catalogued", "switched_on_at", "deleted_at",
)


def discontinued(product: Any) -> bool:
    """THE discontinued rule: inactive (is_active False), unless it is a
    provisional buy nobody has switched on or deleted yet.

    The PO door (product_master, ruling 13) stamps a buy-first-catalogue-later
    product `provisional` and inactive, and finishing its catalogue does not
    switch it on, so a frame bought on a PO, catalogued and received is still
    inactive and must not read 'Discontinued'. `provisional` is never
    cleared, so the two facts that end 'new' are read instead: switched_on_at
    (ProductRepository.update stamps it whenever a door writes is_active True;
    every spine door writes through it; the restamp that switches a finished
    draft on stamps it too) and deleted_at (the catalog DELETE stamps it on
    the spine). Finishing an ordered draft clears `provisional` (PR #1171) and
    stamps `bought_before_catalogued` instead, which this rule reads alike. catalog_status is NOT one of them: the
    catalogue restamp moves it without switching anything on.
    ponytail: a provisional product never switched on whose is_active False is
    merely re-sent still reads new (nothing tells that write from an edit
    that left the toggle alone); deleting it is what retires it."""
    if not isinstance(product, dict) or product.get("is_active") is not False:
        return False
    # A PO-door buy: `provisional` while it is an ordered draft, and
    # `bought_before_catalogued` once finishing it has cleared `provisional`
    # (product_master.restamp_on_update, PR #1171) -- set once, never cleared.
    still_new = (
        bool(product.get("provisional") or product.get("bought_before_catalogued"))
        and not product.get("switched_on_at")
        and not product.get("deleted_at")
    )
    return not still_new


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

# A Mongo key (reorder_levels.<store_id>): no dots, no "$". The write model and
# the migration script both read this one pattern.
STORE_KEY_PATTERN = r"^[A-Za-z0-9_-]+$"

# The largest level there is; anything above reads as not set (the PUT refuses it too).
MAX_LEVEL = 100000


def _whole(value: Any) -> Optional[int]:
    """A real integer or None. bool, non-integral floats, inf/NaN, strings and
    anything else are NOT numbers a level (or a count) may be read from.
    Never raises."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        try:
            return int(value) if value == int(value) else None
        except (OverflowError, ValueError):  # inf, NaN
            return None
    return None


# Public name for the one whole-number reader (the migration script reads old values with it).
whole_number = _whole


def reorder_level(product: Any, *, store_id: Optional[str]) -> Optional[int]:
    """THIS shop's reorder level for the product, or None = not set.
    Only a real whole number >= 0 is a level; garbage is not set."""
    if not store_id or not isinstance(product, dict):
        return None
    levels = product.get(LEVELS_FIELD)
    if not isinstance(levels, dict):
        return None
    level = _whole(levels.get(store_id))
    return level if level is not None and 0 <= level <= MAX_LEVEL else None


def is_low_stock(product: Any, on_hand: Any, *, store_id: Optional[str]) -> bool:
    """True when the shop has a level and its on-hand is at or under it."""
    level = reorder_level(product, store_id=store_id)
    if level is None:
        return False
    count = 0 if on_hand is None else _whole(on_hand)
    return count is not None and count <= level


def top_up(level: Any, on_hand: Any) -> int:
    """Units to get back to one above the level (0 when there is no level or
    the shop is already above it) -- the level half of buy_qty, which every
    reader asks; Jarvis's value-at-risk reads the bare gap."""
    lvl = _whole(level)
    count = 0 if on_hand is None else _whole(on_hand)
    if lvl is None or not 0 <= lvl <= MAX_LEVEL or count is None:
        return 0
    return max(0, lvl + 1 - count)


# ---------------------------------------------------------------------------
# THE BUY QUANTITY - one rule (owner 2026-10-22/23, stock redesign S5)
# ---------------------------------------------------------------------------
# "Reorder = the shop's level if set, else 30-day sales." Every screen, report
# and agent that suggests how many to buy asks buy_qty; nobody keeps a cover
# formula of their own. Level set: back up to one above it (top_up). Not set:
# the last 30 days' sales at the shop. Stock in the shop and pieces on SENT
# orders are taken off (drafts are not, owner D11). Rounded UP to the product's
# order quantity (reorder_quantity > 1), never past its max stock.

SALES_DAYS = 30


def buy_target(product: Any, *, store_id: Optional[str], sold_30d: Any) -> Tuple[Optional[int], Optional[str]]:
    """(pieces the shop should hold, basis): (level + 1, 'level') when the shop
    has a level, else (30-day sales, 'sales') when it sold any, else (None, None)."""
    level = reorder_level(product, store_id=store_id)
    if level is not None:
        return level + 1, "level"
    sold = _whole(sold_30d)
    if sold is not None and sold > 0:
        return sold, "sales"
    return None, None


def buy_qty(
    product: Any, *, store_id: Optional[str], on_hand: Any, on_order: Any = 0, sold_30d: Any = 0
) -> Tuple[int, Optional[str]]:
    """THE suggested buy for one product at one shop: (qty, basis). basis is
    'level', 'sales' or None (nothing to go by). qty is 0 when auto-reorder is
    off or discontinued (auto_reorder_disabled), when there is no basis, or
    when stock plus sent orders already cover the target."""
    target, basis = buy_target(product, store_id=store_id, sold_30d=sold_30d)
    if target is None or auto_reorder_disabled(product):
        return 0, basis
    have = max(0, _whole(on_hand) or 0) + max(0, _whole(on_order) or 0)
    need = top_up(target - 1, have)  # back to `target`; target - 1 >= 0 always
    if need <= 0:
        return 0, basis
    pack = _whole((product or {}).get("reorder_quantity"))
    if pack is not None and pack > 1:
        need = -(-need // pack) * pack
    cap = _whole((product or {}).get("max_stock"))
    if cap is not None and cap > 0:
        need = min(need, max(0, cap - have))
    return need, basis


def spare(product: Any, *, store_id: Optional[str], on_hand: Any, sold_30d: Any = 0) -> int:
    """Pieces a shop holds beyond its own target (buy_target): what it could
    send to another shop instead of that shop buying. No target = every piece."""
    count = max(0, _whole(on_hand) or 0)
    target, _basis = buy_target(product, store_id=store_id, sold_30d=sold_30d)
    return max(0, count - (target or 0))


def share_spares(needs: List[Tuple[Any, int]], spares: Dict[str, int]) -> Dict[Any, List[Tuple[str, int]]]:
    """THE transfer rule: share other shops' spare pieces (spare()) out over
    the short rows of ONE product. `needs` = [(row key, pieces short)] worst
    first; `spares` = {donor shop: spare pieces}. Each row takes from the
    donors with the most left, and each share comes off that donor, so one
    shop's spares are never offered twice. A row's own shop is never its
    donor (row keys are (product, shop) or any key whose [1] is the shop).
    Returns {row key: [(donor, pieces)]}."""
    left = {s: n for s, n in spares.items() if n > 0}
    out: Dict[Any, List[Tuple[str, int]]] = {}
    for key, need in needs:
        for donor in sorted(left, key=lambda s: (-left[s], s)):
            if need <= 0:
                break
            if donor == key[1] or left[donor] <= 0:
                continue
            take = min(need, left[donor])
            out.setdefault(key, []).append((donor, take))
            left[donor] -= take
            need -= take
    return out


def band(on_hand: Any, target: Optional[int], basis: Optional[str]) -> str:
    """The Reorder list's chip for a shop's stock against its buy_target. A
    shop level: stock_status (level = target - 1). 30-day sales (no level to
    halve): 'healthy' once the shop holds the sales, 'out-of-stock' when it
    holds none, else 'low'."""
    if basis == "level" and target is not None:
        return stock_status(target - 1, on_hand)
    count = max(0, _whole(on_hand) or 0)
    if target is None or count >= target:
        return "healthy"
    return "out-of-stock" if count <= 0 else "low"


def sold_by_shop(
    orders, *, store_id: Optional[str] = None, days: int = SALES_DAYS, now=None, strict: bool = False
) -> Dict[Tuple[str, str], int]:
    """Units sold per (product_id, store_id) in the last `days` days -- the
    sales the buy rule reads. ONE reader: predictive_reorder's tally (booked
    orders only; an online order counts at the shop that fulfilled it).
    {} if the read fails -- or, with strict, the error is raised (a list that
    must not read a failed read as 'sold nothing')."""
    from datetime import datetime, timedelta

    from agents.predictive_reorder import tally_demand_by_product_store
    from .online_order_status import BOOKED_STATUSES

    coll = _coll(orders)
    if coll is None:
        return {}
    now = now or datetime.utcnow()
    # No store filter in the query: an online order fulfilled by this shop
    # carries the online store's id (the tally moves it to the shop).
    match: Dict[str, Any] = {
        "created_at": {"$gte": now - timedelta(days=days)},
        "status": {"$in": sorted(BOOKED_STATUSES)},
    }
    try:
        docs = list(coll.find(match, {
            "store_id": 1, "status": 1, "created_at": 1, "items": 1,
            "fulfillment_breakdown": 1, "fulfillment_stores": 1,
        }))
    except Exception:  # noqa: BLE001
        if strict:
            raise
        return {}
    tally = tally_demand_by_product_store(docs, now=now)
    return {k: int(v["units_30d"]) for k, v in tally.items()
            if v.get("units_30d") and (not store_id or k[1] == store_id)}


def open_orders(
    purchase_orders, product_ids=None, *, strict: bool = False
) -> Tuple[Dict[Tuple[str, str], int], Dict[Tuple[str, str], int]]:
    """(on order, in draft) per (product_id, delivery shop): ordered minus
    received. On order = SENT to the vendor only (owner D11); a draft nobody
    sent is counted apart and never taken off a buy. ({}, {}) if unreadable,
    or the error raised with strict."""
    from .po_variance_engine import AWAITING_DELIVERY_STATUSES

    coll = _coll(purchase_orders)
    if coll is None:
        return {}, {}
    wanted = None if product_ids is None else {str(p) for p in product_ids}
    sent: Dict[Tuple[str, str], int] = {}
    draft: Dict[Tuple[str, str], int] = {}
    try:
        cur = coll.find(
            {"status": {"$in": [*AWAITING_DELIVERY_STATUSES, "DRAFT"]}},
            {"status": 1, "delivery_store_id": 1, "items": 1, "received_qty_by_product": 1},
        )
        for po in cur:
            out = draft if po.get("status") == "DRAFT" else sent
            shop = str(po.get("delivery_store_id") or "")
            received = dict(po.get("received_qty_by_product") or {})
            for it in po.get("items") or []:
                pid = str(it.get("product_id") or "")
                if not pid or (wanted is not None and pid not in wanted):
                    continue
                ordered = max(0, _whole(it.get("quantity")) or 0)
                # A receipt is spread over the order's lines for that product.
                got = min(ordered, max(0, _whole(received.get(pid)) or 0))
                received[pid] = (_whole(received.get(pid)) or 0) - got
                if ordered - got > 0:
                    out[(pid, shop)] = out.get((pid, shop), 0) + ordered - got
    except Exception:  # noqa: BLE001
        if strict:
            raise
        return {}, {}
    return sent, draft


def stock_status(level: Any, on_hand: Any) -> str:
    """The server's band for a shop's stock: 'not-set' (no level, no alert),
    'out-of-stock', 'critical' (at or under half the level), 'low' (at or
    under the level) or 'healthy'. The only place the bands are decided."""
    lvl = _whole(level)
    count = 0 if on_hand is None else _whole(on_hand)
    if lvl is None or not 0 <= lvl <= MAX_LEVEL or count is None:
        return "not-set"
    if count <= 0:
        return "out-of-stock" if count <= lvl else "healthy"
    if count * 2 <= lvl:  # integers only: no float, no OverflowError
        return "critical"
    return "low" if count <= lvl else "healthy"


def _coll(source):
    """A repository (its .collection) or a raw collection. isinstance, never
    getattr: a pymongo Collection answers ANY attribute with a sub-collection."""
    from database.repositories.base_repository import BaseRepository

    return source.collection if isinstance(source, BaseRepository) else source


class _StockOnly:
    """Hands the one on-hand reader the stock_units collection it was given."""

    def __init__(self, coll):
        self._coll = coll

    def get_collection(self, _name):
        return self._coll


def on_hand(
    stock_units, *, store_id: Optional[str], product_ids=None, strict: bool = False
) -> Dict[Tuple[str, str], int]:
    """Sellable units per (product_id, store_id) -- the count a level is
    compared with. This is NOT a second rule: it asks
    inventory_balancing._on_hand_by_product_store (item_events decides which
    unit is on hand). store_id None = every shop. {} if the read fails, or
    the error raised with strict."""
    from .inventory_balancing import _on_hand_by_product_store

    coll = _coll(stock_units)
    if coll is None:
        return {}
    ids = None if product_ids is None else [str(p) for p in product_ids]
    return _on_hand_by_product_store(_StockOnly(coll), ids, store_id, strict=strict)


def out_of_stock_count(
    products, stock_units, *, store_id: Optional[str]
) -> Tuple[int, int]:
    """(active products, of which sold out): sold out = no sellable unit in this
    shop (store_id None = none in any shop), by the SAME on-hand rule as the
    low-stock list -- never products.stock_quantity. (0, 0) if unreadable."""
    products = _coll(products)
    if products is None or _coll(stock_units) is None:
        return 0, 0
    try:
        ids = [
            str(d["product_id"])
            for d in products.find({}, {"_id": 0, "product_id": 1, "is_active": 1})
            if d.get("product_id") and d.get("is_active") is not False
        ]
    except Exception:  # noqa: BLE001
        return 0, 0
    have = {pid for (pid, _shop), n in on_hand(
        stock_units, store_id=store_id, product_ids=ids
    ).items() if n > 0}
    return len(ids), sum(1 for pid in ids if pid not in have)


def low_stock_rows(
    products, stock_units, *, store_id: Optional[str]
) -> List[Dict[str, Any]]:
    """THE low-stock list: one row per (product, shop) at or under that
    shop's own level -- a shop that has sold out (0 units) included.
    store_id None = every shop (the owner's all-shops views). Each row:
    {_id, product_id, store_id, quantity, reorder_point, stock_status, sku,
    name}. How many to buy is buy_qty's (the Reorder list), not this list's.
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
                        "stock_status": stock_status(
                            reorder_level(p, store_id=shop), qty
                        ),
                        "sku": p.get("sku") or "",
                        "name": p.get("name") or "",
                    }
                )
    rows.sort(key=lambda r: (r["quantity"], r["store_id"], r["product_id"]))
    return rows
