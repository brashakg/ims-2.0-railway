"""IMS 2.0 - THE stock value rule (audit F47, owner ruling 2026-09-28).

The stock at a shop is worth what it COST: every unit physically there (on the
shelf or reserved -- the PHYSICAL half of item_events.on_hand_match) at the
cost stamped on it at goods receipt (unit_cost, else cost_price), else its
product's cost_price. Sold, transferred-out and written-off units are not
stock. Selling value (offer price / MRP) is a different figure and is labelled
as such wherever it is shown.

A unit with none of those prices (a zero or blank cost is no price) is
UNCOSTED: it adds nothing to the value, but it is counted (`cost_known`
False, by_product's `uncosted_units`, uncosted()) so a reader can say "N units
have no cost" instead of a headline that looks complete, and it never drags a
per-unit cost towards Rs 0 (unit_cost averages the costed units only, and is
None when none is).

Readers: the stock ledger rows (cost_value / unit_cost / uncosted_units),
Stock aging, and the report 'stock value' figures (/reports/inventory,
/inventory/summary, /inventory/valuation, /stock/count). Who may see it:
cost_mask.can_see_cost(user, "purchase") -- the buyers' rule (managers and
accounts see what was paid), never the counter.
No DB import; takes the repositories the caller already holds.
"""

from typing import Dict, List, Optional

from .item_events import on_hand_match


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _price(v) -> float:
    """A cost figure, or 0.0 when it is no price at all (blank, zero, junk or
    negative)."""
    c = _f(v)
    return c if c > 0 else 0.0


def _own_cost(unit: dict) -> float:
    return _price(unit.get("unit_cost")) or _price(unit.get("cost_price"))


def _pieces(unit: dict) -> float:
    """A unit with no quantity is one piece."""
    return 1 if unit.get("quantity") is None else _f(unit.get("quantity"))


def shelf_units(stock_repo, product_repo, store_id: Optional[str]) -> List[dict]:
    """The units physically at `store_id` (every shop when None), each carrying
    `cost_value` = pieces x its cost and `cost_known` (False: no cost resolves,
    so cost_value is 0 and the unit is counted as uncosted)."""
    flt = dict(on_hand_match(include_reserved=True))
    if store_id:
        flt["store_id"] = store_id
    units = stock_repo.find_many(flt, limit=0) or []
    product_cost: Dict[str, float] = {}
    for pid in {u.get("product_id") for u in units if not _own_cost(u)}:
        doc = product_repo.find_by_id(pid) if product_repo is not None and pid else None
        product_cost[pid] = _price((doc or {}).get("cost_price"))
    for u in units:
        cost = _own_cost(u) or product_cost.get(u.get("product_id"), 0.0)
        u["cost_known"] = cost > 0
        u["cost_value"] = round(_pieces(u) * cost, 2)
    return units


def by_product(units: List[dict]) -> Dict[str, Dict[str, Optional[float]]]:
    """{product_id: {"units": pieces, "cost": rupees, "uncosted_units": pieces
    with no cost, "unit_cost": cost per COSTED piece (None when none is)}} over
    shelf_units()."""
    out: Dict[str, Dict[str, Optional[float]]] = {}
    for u in units:
        row = out.setdefault(
            u.get("product_id"), {"units": 0.0, "cost": 0.0, "uncosted_units": 0.0}
        )
        row["units"] += _pieces(u)
        row["cost"] = round(row["cost"] + u["cost_value"], 2)
        if not u.get("cost_known"):
            row["uncosted_units"] += _pieces(u)
    for row in out.values():
        costed = row["units"] - row["uncosted_units"]
        row["unit_cost"] = round(row["cost"] / costed, 2) if costed > 0 else None
    return out


def total(units: List[dict]) -> float:
    return round(sum(u["cost_value"] for u in units), 2)


def uncosted(units: List[dict]) -> float:
    """Pieces in shelf_units() whose cost is unknown -- the figure a stock
    value headline shows beside itself ("N units have no cost")."""
    return sum(_pieces(u) for u in units if not u.get("cost_known"))
