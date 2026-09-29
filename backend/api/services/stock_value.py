"""IMS 2.0 - THE stock value rule (audit F47, owner ruling 2026-09-28).

The stock at a shop is worth what it COST: every unit physically there (on the
shelf or reserved -- the PHYSICAL half of item_events.on_hand_match) at the
cost stamped on it at goods receipt (unit_cost, else cost_price), else its
product's cost_price. Sold, transferred-out and written-off units are not
stock. Selling value (offer price / MRP) is a different figure and is labelled
as such wherever it is shown.

Readers: the stock ledger rows (cost_value / unit_cost), Stock aging, and the
report 'stock value' figures (/reports/inventory, /inventory/summary,
/inventory/valuation, /stock/count). Who may see it:
cost_mask.can_see_cost(user, "stock") -- managers and accounts, never the counter.
No DB import; takes the repositories the caller already holds.
"""

from typing import Dict, List, Optional

from .item_events import on_hand_match


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _own_cost(unit: dict) -> float:
    return _f(unit.get("unit_cost")) or _f(unit.get("cost_price"))


def shelf_units(stock_repo, product_repo, store_id: Optional[str]) -> List[dict]:
    """The units physically at `store_id` (every shop when None), each carrying
    `cost_value` = quantity x its cost. A unit with no quantity is one piece."""
    flt = dict(on_hand_match(include_reserved=True))
    if store_id:
        flt["store_id"] = store_id
    units = stock_repo.find_many(flt, limit=0) or []
    product_cost: Dict[str, float] = {}
    for pid in {u.get("product_id") for u in units if not _own_cost(u)}:
        doc = product_repo.find_by_id(pid) if product_repo is not None and pid else None
        product_cost[pid] = _f((doc or {}).get("cost_price"))
    for u in units:
        qty = 1 if u.get("quantity") is None else _f(u.get("quantity"))
        cost = _own_cost(u) or product_cost.get(u.get("product_id"), 0.0)
        u["cost_value"] = round(qty * cost, 2)
    return units


def by_product(units: List[dict]) -> Dict[str, Dict[str, float]]:
    """{product_id: {"units": pieces, "cost": rupees}} over shelf_units()."""
    out: Dict[str, Dict[str, float]] = {}
    for u in units:
        row = out.setdefault(u.get("product_id"), {"units": 0.0, "cost": 0.0})
        row["units"] += 1 if u.get("quantity") is None else _f(u.get("quantity"))
        row["cost"] = round(row["cost"] + u["cost_value"], 2)
    return out


def total(units: List[dict]) -> float:
    return round(sum(u["cost_value"] for u in units), 2)
