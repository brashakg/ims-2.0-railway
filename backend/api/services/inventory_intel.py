"""
IMS 2.0 - Inventory intelligence
================================
Pure decision logic (no DB) for:
  * inter-store transfer recommendations: rebalance a deficit store from a
    surplus store, product by product.
  * staff stock accountability: attribute a completed count's shrinkage to the
    store's assigned stock custodian.

Kept pure so it's unit-tested directly; the router supplies the data.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def recommend_transfers(
    deficit_store: str,
    low_products: List[Dict[str, Any]],
    store_levels: Dict[str, Dict[str, int]],
    threshold: int = 5,
    target: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Suggest inter-store transfers to refill a deficit store.

    - deficit_store: the store that is short.
    - low_products: [{product_id, product_name?, quantity}] already <= threshold
      at deficit_store.
    - store_levels: {product_id: {store_id: available_qty}} across ALL stores.
    - target: level to refill the deficit store toward (default threshold*2).

    For each low product we find the store with the most EXCESS above target and
    move min(need, excess) units. A source is only tapped for what keeps it at
    or above target, so we never create a new deficit. Pure + deterministic.
    """
    refill_to = target if target is not None else threshold * 2
    recs: List[Dict[str, Any]] = []

    for lp in low_products:
        pid = lp.get("product_id")
        if not pid:
            continue
        levels = store_levels.get(pid, {})
        have = int(levels.get(deficit_store, lp.get("quantity", 0)) or 0)
        need = refill_to - have
        if need <= 0:
            continue

        best_store = None
        best_excess = 0
        for sid, qty in levels.items():
            if sid == deficit_store:
                continue
            excess = int(qty or 0) - refill_to
            if excess > best_excess:
                best_store, best_excess = sid, excess

        if best_store is None:
            continue
        move = min(need, best_excess)
        if move <= 0:
            continue
        recs.append(
            {
                "product_id": pid,
                "product_name": lp.get("product_name"),
                "sku": lp.get("sku"),
                "from_store": best_store,
                "to_store": deficit_store,
                "quantity": move,
                "to_store_qty": have,
                "from_store_qty": int(levels.get(best_store, 0) or 0),
            }
        )

    # Biggest, most-urgent moves first.
    recs.sort(key=lambda r: (r["to_store_qty"], -r["quantity"]))
    return recs


def shrinkage_by_custodian(
    counts: List[Dict[str, Any]], custodians: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Attribute each completed count's shrinkage to its store's custodian.

    - counts: [{store_id, audit_number?, shrinkage_percentage, completed_at}]
    - custodians: {store_id: {staff_id, staff_name}}
    Returns rows with the responsible custodian filled in (None when unassigned).
    """
    out: List[Dict[str, Any]] = []
    for c in counts or []:
        cust = custodians.get(c.get("store_id")) or {}
        out.append(
            {
                "store_id": c.get("store_id"),
                "audit_number": c.get("audit_number"),
                "shrinkage_percentage": c.get("shrinkage_percentage", 0),
                "completed_at": c.get("completed_at"),
                "custodian_id": cust.get("staff_id"),
                "custodian_name": cust.get("staff_name"),
            }
        )
    return out
