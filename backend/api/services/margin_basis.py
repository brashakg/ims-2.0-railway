"""Margin basis -- compare cost and price on the SAME side of GST.

House rule (services/cost_floor.py): catalogue ``cost_price`` (and an order
line's ``cost_at_sale`` snapshot of it) is GST-EXCLUSIVE, while MRP,
offer_price, selling_price and an order line's unit_price / item_total are
GST-INCLUSIVE under the active pricing mode (gst_rates.gst_pricing_mode,
default "inclusive"; prod runs inclusive and every real order is stamped
``pricing_model: "inclusive"``). Taking an ex-GST cost away from an incl-GST
price counts the GST the shop owes the government as the shop's own margin.

So every margin / gross-profit figure takes the GST out of the price first,
HERE. Callers never divide by (1 + rate) themselves.

  * An ORDER is already split: ``reports.gst_base._order_taxable_and_tax``
    (grand_total - round_off - tax) is its taxable value -- reused, not copied.
  * An ORDER LINE carries ``taxable_value`` (post line + cart discount, ex-GST
    in both pricing modes), stamped by orders._compute_per_category_gst and
    shopify_ingest.
  * A CATALOGUE price has no split yet: ``price_ex_gst`` backs the GST out at
    the product's rate (gst_rates.resolve_gst_rate), exactly as the till would.
"""

from typing import Any, Optional

from .gst_rates import gst_pricing_mode, resolve_gst_rate


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def price_ex_gst(price: Any, gst_rate: Any) -> float:
    """A catalogue / counter price -> its GST-exclusive taxable value, read the
    way the order engine reads it: inclusive mode backs the GST out
    (price / (1 + rate/100)); exclusive mode the price already IS taxable."""
    p = _f(price)
    if gst_pricing_mode() == "exclusive":
        return p
    return p / (1.0 + _f(gst_rate) / 100.0)


def product_gst_rate(product: dict) -> float:
    """The GST rate the till would charge on this catalogue product."""
    product = product or {}
    return resolve_gst_rate(
        hsn_code=product.get("hsn_code"), category=product.get("category")
    )


def margin_pct_ex_gst(price: Any, cost: Any, gst_rate: Any) -> Optional[float]:
    """Gross margin % of an incl-GST price over an ex-GST cost, both ex-GST:
    (net - cost) / net * 100, 2 dp. None when the price is not positive."""
    net = price_ex_gst(price, gst_rate)
    if net <= 0:
        return None
    return round((net - _f(cost)) / net * 100.0, 2)


def order_revenue_ex_gst(order: dict) -> float:
    """An order's revenue net of GST (its taxable value) -- the GST-return
    reader's own derivation, so the P&L and GSTR-1 can never disagree."""
    from ..routers.reports.gst_base import _order_taxable_and_tax

    return _order_taxable_and_tax(order or {})[0]


def line_revenue_ex_gst(item: dict) -> float:
    """An order line's revenue net of GST: its stamped ``taxable_value``.
    A legacy line without one backs the GST out of item_total at its rate."""
    item = item or {}
    if item.get("taxable_value") is not None:
        return _f(item.get("taxable_value"))
    gross = item.get("item_total")
    if gross is None:
        gross = item.get("total")
    rate = item.get("gst_rate")
    if rate is None:
        rate = resolve_gst_rate(
            hsn_code=item.get("hsn_code") or item.get("hsn"),
            category=item.get("item_type") or item.get("category"),
        )
    return price_ex_gst(gross, rate)


# The same rule for an $unwind-ed ``items`` line inside a Mongo $group, for the
# readers that aggregate in the database. Every real line carries
# taxable_value (prod 2026-10-19: 972 of 972); the fallback backs GST out of
# item_total at the line's own gst_rate.
LINE_REVENUE_EX_GST_EXPR = {
    "$ifNull": [
        "$items.taxable_value",
        {
            "$divide": [
                {"$ifNull": ["$items.item_total", 0]},
                {"$add": [1, {"$divide": [{"$ifNull": ["$items.gst_rate", 0]}, 100]}]},
            ]
        },
    ]
}


def line_unit_cost(item: dict) -> float:
    """An order line's ex-GST unit cost: the ``cost_at_sale`` snapshot
    orders/create.py stamps (legacy ``cost_price`` / ``costPrice`` accepted).
    Unknown -> 0."""
    item = item or {}
    for k in ("cost_at_sale", "cost_price", "costPrice"):
        if item.get(k) is not None:
            return _f(item.get(k))
    return 0.0
