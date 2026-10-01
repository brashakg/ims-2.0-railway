"""
IMS 2.0 - Online vs in-store stock reconciliation (pure)
========================================================
Prevents overselling between the physical stores (IMS/Mongo = master on-hand)
and the online store (Shopify = listed quantity). The model the council chose:
the online channel should never list MORE than the physical on-hand, and
ideally a CONSERVATIVE slice of it (on-hand minus a safety buffer) so a walk-in
sale can't strand an online order.

Honesty rule (audit fix-round P1): an UNKNOWN listed quantity (online=None --
the live Shopify read didn't cover that SKU) is classified LISTED_UNKNOWN,
never OK. Only a KNOWN listed quantity may earn an OK/risk verdict. The same
rule on the other column (recheck round 1): an UNKNOWN on-hand (in_store=None
-- the shop list or the stock read failed) is ONHAND_UNKNOWN, never a
confident 0 + OVERSELL_RISK.

DB-free + unit-testable. The catalog router fetches per-SKU {in_store, online,
is_online} plus the per-LOCATION counts (the writer's number and the units
no shelf backs, shopify_stock_parity.unbacked_units) and calls
reconcile_items(). There is no pooled comparison here: recommend_allocation is
the writer's per-shop call (online_stock_writeback), never a pooled total.
"""

from typing import List, Optional

# Status codes, worst first.
OVERSELL_RISK = "OVERSELL_RISK"  # online listed > physical on-hand (can oversell)
OVER_ALLOCATED = "OVER_ALLOCATED"  # online listed > safe allocation but <= on-hand
ONHAND_UNKNOWN = "ONHAND_UNKNOWN"  # online SKU, the IMS on-hand could not be read
LISTED_UNKNOWN = "LISTED_UNKNOWN"  # online SKU, listed qty not covered by the live read
OK = "OK"  # online within the safe allocation (listed qty KNOWN)
NOT_ONLINE = "NOT_ONLINE"  # product isn't listed online -> not assessed
# Live on Shopify, but its Shopify item is shared with another IMS product, so
# the writer sends neither: not assessed, and the cause is named (fix in IMS).
SHARES_SHOPIFY_ITEM = "SHARES_SHOPIFY_ITEM"

_ORDER = {
    OVERSELL_RISK: 0,
    OVER_ALLOCATED: 1,
    ONHAND_UNKNOWN: 2,
    LISTED_UNKNOWN: 3,
    OK: 4,
    SHARES_SHOPIFY_ITEM: 5,
    NOT_ONLINE: 6,
}


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def recommend_allocation(
    on_hand, safety_buffer: int = 0, max_online: Optional[int] = None
) -> int:
    """Conservative online quantity = on_hand - safety_buffer, floored at 0 and
    optionally capped at max_online."""
    rec = max(0, _int(on_hand) - max(0, _int(safety_buffer)))
    if max_online is not None:
        rec = min(rec, max(0, _int(max_online)))
    return rec


def classify(
    in_store: Optional[int],
    online: Optional[int],
    is_online: Optional[bool],
    over: Optional[int],
    excess: Optional[int],
    shares_item: bool = False,
) -> str:
    """online=None means the listed quantity is UNKNOWN (the live read did not
    cover this SKU) -> LISTED_UNKNOWN, never a confident OK. in_store=None
    means the ON-HAND is unknown (the shop list or the stock read failed) ->
    ONHAND_UNKNOWN, never a confident 0 + OVERSELL_RISK.

    ``over``: the units listed that no shelf backs, counted LOCATION BY
    LOCATION by the caller (shopify_stock_parity.unbacked_units) -- there is
    no pooled ``online - in_store`` here: a pooled total lets one shop's
    shelf (or unmapped Pune's) back another shop's listing. None = a shop
    behind a listing could not be read -> ONHAND_UNKNOWN.

    ``excess``: the same count against the WRITER's number (its buffer, its
    online block) instead of the shelf -> OVER_ALLOCATED; None is unknown.

    ``is_online`` None means WHETHER the listing is live is unknown (the
    live-listing read failed) -> LISTED_UNKNOWN, never a confident
    NOT_ONLINE. ``shares_item``: the listing is live but its Shopify item is
    shared with another product (the writer refuses it) -> SHARES_SHOPIFY_ITEM,
    never NOT_ONLINE."""
    if shares_item:
        return SHARES_SHOPIFY_ITEM
    if is_online is None:
        return LISTED_UNKNOWN
    if not is_online:
        return NOT_ONLINE
    if in_store is None:
        return ONHAND_UNKNOWN
    if online is None:
        return LISTED_UNKNOWN
    if over is None:
        return ONHAND_UNKNOWN
    if over > 0:
        return OVERSELL_RISK
    if excess is None:
        return ONHAND_UNKNOWN
    if excess > 0:
        return OVER_ALLOCATED
    return OK


def reconcile_items(items: List[dict]) -> dict:
    """items: [{sku, name?, in_store, online, is_online, recommended,
    unbacked, excess}] where online may be None = listed qty unknown and
    in_store may be None = on-hand unknown; ``recommended`` is the writer's
    number and ``unbacked`` / ``excess`` are classify's per-location ``over``
    / ``excess`` -- all three from the caller, None = unknown. Nothing here
    re-derives them from a pooled on-hand.
    Returns per-SKU rows (recommended + status + delta) sorted worst-first
    (status, then units no shelf backs, then delta), plus a summary. `delta`
    = ``excess``: units listed beyond what the writer sends, location by
    location (positive => listed more than is safe); None when unknown."""
    keyed: List[tuple] = []
    counts = {
        OVERSELL_RISK: 0,
        OVER_ALLOCATED: 0,
        ONHAND_UNKNOWN: 0,
        LISTED_UNKNOWN: 0,
        OK: 0,
        NOT_ONLINE: 0,
        SHARES_SHOPIFY_ITEM: 0,
    }
    oversell_units = 0

    for it in items or []:
        if not isinstance(it, dict):
            continue
        in_store_raw = it.get("in_store")
        in_store: Optional[int] = None if in_store_raw is None else _int(in_store_raw)
        online_raw = it.get("online")
        online: Optional[int] = None if online_raw is None else _int(online_raw)
        # An explicit None: whether the listing is live is unknown.
        is_online = None if "is_online" in it and it["is_online"] is None else bool(it.get("is_online"))
        over, excess = it.get("unbacked"), it.get("excess")
        status = classify(in_store, online, is_online, over, excess, bool(it.get("shares_item")))
        counts[status] = counts.get(status, 0) + 1
        if status == OVERSELL_RISK:
            oversell_units += over
        delta = None if in_store is None or online is None else excess
        row = {
            "sku": it.get("sku"),
            "name": it.get("name"),
            "in_store": in_store,
            "online": online,
            "recommended": it.get("recommended"),
            "delta": delta,
            "status": status,
        }
        keyed.append(((_ORDER.get(status, 9), -(over or 0), -(delta or 0)), row))

    keyed.sort(key=lambda kr: kr[0])
    rows = [r for _, r in keyed]
    return {
        "items": rows,
        "summary": {
            "total": len(rows),
            "oversell_risk": counts[OVERSELL_RISK],
            "over_allocated": counts[OVER_ALLOCATED],
            "onhand_unknown": counts[ONHAND_UNKNOWN],
            "listed_unknown": counts[LISTED_UNKNOWN],
            "ok": counts[OK],
            "not_online": counts[NOT_ONLINE],
            "shares_item": counts[SHARES_SHOPIFY_ITEM],
            "oversell_risk_units": oversell_units,
        },
    }
