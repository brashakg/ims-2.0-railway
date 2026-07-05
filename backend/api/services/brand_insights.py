"""
IMS 2.0 - Brand insights (Inventory > Insights > Brands)
========================================================
Pure fold/KPI math for the brand-wise inventory insights table. No DB access
here -- the router (GET /inventory/brand-insights) supplies:

  * product_docs   -- projected `products` spine docs ({_id, product_id,
                      brand, offer_price, mrp}); BOTH id keys map to the same
                      brand/price entry because order items and stock_units
                      have historically stamped either one.
  * on_hand_by_pid -- {product_id: units} from the stock_units rollup
                      (inventory._on_hand_by_product: ON_HAND allowlist +
                      EXCLUDED_STATUSES from the item-event ledger).
  * sales_by_pid   -- {product_id: {"units": int, "revenue": float}} from ONE
                      orders aggregation over the window (same qty/item_total
                      field conventions as collection_insights._movement_pipeline).

KPI semantics deliberately REUSE collection_insights so the Brands tab, the
Collections tab and the /collections pages can never disagree on the math:
  * sell_through_percent = collection_insights.sell_through * 100
    (sold / (sold + on_hand); None when there is no signal).
  * days_cover = collection_insights.days_of_cover (on_hand / (sold30/30),
    capped 999; None when no stock AND no sales). Windows other than 30 days
    are normalised to a 30-day-equivalent rate first.
  * stock value prices on-hand units at offer_price, falling back to mrp,
    else 0 -- with the same positive-number coercion _value_stock uses.

Blank / missing brands fold into "Unknown". Rows sort by revenue desc.
Kept pure so it is unit-tested directly (mirrors inventory_intel.py).
No emoji (Windows cp1252).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from . import collection_insights as _ci

UNKNOWN_BRAND = "Unknown"


def normalize_brand(raw: Any) -> str:
    """Strip whitespace; blank / missing / non-string -> "Unknown"."""
    if isinstance(raw, str):
        name = raw.strip()
        if name:
            return name
    return UNKNOWN_BRAND


def brand_maps(
    product_docs: List[Dict[str, Any]],
) -> Tuple[Dict[str, str], Dict[str, float]]:
    """Fold projected product docs into two lookup maps:

      brand_by_pid -- pid -> normalised brand (BOTH `product_id` and str(_id)
                      keys are registered, matching the two id conventions in
                      orders.items / stock_units).
      value_by_pid -- pid -> per-unit valuation: offer_price, fallback mrp,
                      else 0 (positive-number coercion via collection_insights
                      so junk 0/negative prices read as absent).
    """
    brand_by_pid: Dict[str, str] = {}
    value_by_pid: Dict[str, float] = {}
    for doc in product_docs or []:
        if not isinstance(doc, dict):
            continue
        brand = normalize_brand(doc.get("brand"))
        unit_value = (
            _ci._num(doc.get("offer_price")) or _ci._num(doc.get("mrp")) or 0.0
        )
        for key in (doc.get("product_id"), doc.get("_id")):
            if key is None:
                continue
            pid = str(key)
            if pid and pid not in brand_by_pid:
                brand_by_pid[pid] = brand
                value_by_pid[pid] = unit_value
    return brand_by_pid, value_by_pid


def sell_through_percent(units_sold: int, units_on_hand: int) -> Optional[float]:
    """collection_insights.sell_through as a 0..100 percentage (1dp).
    None when there is neither stock nor sales (no signal)."""
    st = _ci.sell_through(units_sold, units_on_hand)
    if st is None:
        return None
    return round(st * 100.0, 1)


def days_cover(
    units_on_hand: int, units_sold: int, period_days: int = 30
) -> Optional[float]:
    """collection_insights.days_of_cover over an arbitrary window: the window's
    sales are normalised to a 30-day-equivalent rate first (days_of_cover
    divides by sold/30). A non-zero sold count never rounds down to zero --
    that would misreport "selling slowly" as "not moving" (999)."""
    units_sold = max(int(units_sold or 0), 0)
    period_days = max(int(period_days or 30), 1)
    if period_days == 30:
        sold30 = units_sold
    elif units_sold == 0:
        sold30 = 0
    else:
        sold30 = max(1, int(round(units_sold * 30.0 / period_days)))
    return _ci.days_of_cover(units_on_hand, sold30)


def fold_brand_rows(
    product_docs: List[Dict[str, Any]],
    on_hand_by_pid: Dict[str, int],
    sales_by_pid: Dict[str, Dict[str, Any]],
    period_days: int = 30,
) -> List[Dict[str, Any]]:
    """Compose the per-brand KPI rows. A pid absent from the product spine
    (ghost order line / orphan stock row) folds into "Unknown" rather than
    being dropped, so the totals stay honest.

    Returns rows sorted by revenue desc (ties: stock_value desc, brand asc):
      [{brand, units_on_hand, stock_value, units_sold, revenue,
        sell_through_percent, days_cover}]
    """
    brand_by_pid, value_by_pid = brand_maps(product_docs)

    acc: Dict[str, Dict[str, Any]] = {}

    def _bucket(brand: str) -> Dict[str, Any]:
        return acc.setdefault(
            brand,
            {
                "brand": brand,
                "units_on_hand": 0,
                "stock_value": 0.0,
                "units_sold": 0,
                "revenue": 0.0,
            },
        )

    for pid, qty in (on_hand_by_pid or {}).items():
        try:
            units = int(qty)
        except (TypeError, ValueError):
            continue
        if units <= 0:
            continue
        pid = str(pid)
        row = _bucket(brand_by_pid.get(pid, UNKNOWN_BRAND))
        row["units_on_hand"] += units
        row["stock_value"] += units * float(value_by_pid.get(pid, 0.0))

    for pid, sale in (sales_by_pid or {}).items():
        if not isinstance(sale, dict):
            continue
        try:
            units = int(sale.get("units") or 0)
        except (TypeError, ValueError):
            units = 0
        try:
            revenue = float(sale.get("revenue") or 0.0)
        except (TypeError, ValueError):
            revenue = 0.0
        if units <= 0 and revenue == 0.0:
            continue
        row = _bucket(brand_by_pid.get(str(pid), UNKNOWN_BRAND))
        row["units_sold"] += max(units, 0)
        row["revenue"] += revenue

    rows: List[Dict[str, Any]] = []
    for row in acc.values():
        row["stock_value"] = round(row["stock_value"], 2)
        row["revenue"] = round(row["revenue"], 2)
        row["sell_through_percent"] = sell_through_percent(
            row["units_sold"], row["units_on_hand"]
        )
        row["days_cover"] = days_cover(
            row["units_on_hand"], row["units_sold"], period_days
        )
        rows.append(row)

    rows.sort(key=lambda r: (-r["revenue"], -r["stock_value"], r["brand"]))
    return rows
