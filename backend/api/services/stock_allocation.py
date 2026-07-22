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
never OK. Only a KNOWN listed quantity may earn an OK/risk verdict.

DB-free + unit-testable. The catalog router fetches per-SKU {in_store, online,
is_online} and calls reconcile_items().
"""

from typing import List, Optional

# Status codes, worst first.
OVERSELL_RISK = "OVERSELL_RISK"  # online listed > physical on-hand (can oversell)
OVER_ALLOCATED = "OVER_ALLOCATED"  # online listed > safe allocation but <= on-hand
LISTED_UNKNOWN = "LISTED_UNKNOWN"  # online SKU, listed qty not covered by the live read
OK = "OK"  # online within the safe allocation (listed qty KNOWN)
NOT_ONLINE = "NOT_ONLINE"  # product isn't listed online -> not assessed

_ORDER = {OVERSELL_RISK: 0, OVER_ALLOCATED: 1, LISTED_UNKNOWN: 2, OK: 3, NOT_ONLINE: 4}


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
    in_store: int, online: Optional[int], recommended: int, is_online: bool
) -> str:
    """online=None means the listed quantity is UNKNOWN (the live read did not
    cover this SKU) -> LISTED_UNKNOWN, never a confident OK."""
    if not is_online:
        return NOT_ONLINE
    if online is None:
        return LISTED_UNKNOWN
    if online > in_store:
        return OVERSELL_RISK
    if online > recommended:
        return OVER_ALLOCATED
    return OK


def reconcile_items(
    items: List[dict],
    safety_buffer: int = 0,
    max_online: Optional[int] = None,
) -> dict:
    """items: [{sku, name?, in_store, online, is_online}] where online may be
    None = listed qty unknown. Returns per-SKU rows (recommended allocation +
    status + delta) sorted worst-first, plus a summary. `delta` = online -
    recommended (positive => listed more than is safe); None when the listed
    qty is unknown."""
    rows: List[dict] = []
    counts = {
        OVERSELL_RISK: 0,
        OVER_ALLOCATED: 0,
        LISTED_UNKNOWN: 0,
        OK: 0,
        NOT_ONLINE: 0,
    }
    oversell_units = 0

    for it in items or []:
        if not isinstance(it, dict):
            continue
        in_store = _int(it.get("in_store"))
        online_raw = it.get("online")
        online: Optional[int] = None if online_raw is None else _int(online_raw)
        is_online = bool(it.get("is_online"))
        rec = recommend_allocation(in_store, safety_buffer, max_online)
        status = classify(in_store, online, rec, is_online)
        counts[status] = counts.get(status, 0) + 1
        if status == OVERSELL_RISK:
            oversell_units += (online or 0) - in_store
        rows.append(
            {
                "sku": it.get("sku"),
                "name": it.get("name"),
                "in_store": in_store,
                "online": online,
                "recommended": rec,
                "delta": (None if online is None else online - rec),
                "status": status,
            }
        )

    rows.sort(
        key=lambda r: (
            _ORDER.get(r["status"], 9),
            -(r["delta"] if isinstance(r["delta"], int) else 0),
        )
    )
    return {
        "items": rows,
        "summary": {
            "total": len(rows),
            "oversell_risk": counts[OVERSELL_RISK],
            "over_allocated": counts[OVER_ALLOCATED],
            "listed_unknown": counts[LISTED_UNKNOWN],
            "ok": counts[OK],
            "not_online": counts[NOT_ONLINE],
            "oversell_risk_units": oversell_units,
            "safety_buffer": max(0, _int(safety_buffer)),
        },
    }
