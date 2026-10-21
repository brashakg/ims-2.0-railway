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

    order = order or {}
    # A legacy bill carries only `total` (the P&L's old _REVENUE_EXPR read
    # grand_total ?? total); read it as the grand total, never as 0.
    if (
        order.get("grand_total") is None
        and order.get("total_amount") is None
        and order.get("total") is not None
    ):
        order = {**order, "grand_total": order.get("total")}
    return _order_taxable_and_tax(order)[0]


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
# taxable_value (prod 2026-10-19: 972 of 972) or its gst_rate, and Mongo sums
# those exactly as line_revenue_ex_gst does. A line with NEITHER needs
# resolve_gst_rate (HSN master + category table), which Mongo cannot run: its
# gross and rate hints come back in the group row and group_revenue_ex_gst
# backs the GST out in Python with line_revenue_ex_gst itself. Splice
# LINE_REVENUE_EX_GST_GROUP into the $group; read the row with
# group_revenue_ex_gst.
_LINE_GROSS = {"$ifNull": ["$items.item_total", {"$ifNull": ["$items.total", 0]}]}
_NO_RATE = {"$eq": [{"$ifNull": ["$items.gst_rate", None]}, None]}
_UNRATED = {
    "$and": [{"$eq": [{"$ifNull": ["$items.taxable_value", None]}, None]}, _NO_RATE]
}
LINE_REVENUE_EX_GST_GROUP = {
    "revenue_ex_gst": {
        "$sum": {
            "$ifNull": [
                "$items.taxable_value",
                {
                    "$cond": [
                        _NO_RATE,
                        0,  # unrated: summed into unrated_gross instead
                        {
                            "$divide": [
                                _LINE_GROSS,
                                {"$add": [1, {"$divide": ["$items.gst_rate", 100]}]},
                            ]
                        },
                    ]
                },
            ]
        }
    },
    "unrated_gross": {"$sum": {"$cond": [_UNRATED, _LINE_GROSS, 0]}},
    # $max skips nulls, so this is an unrated line's own hint.
    "unrated_hsn": {
        "$max": {"$cond": [_UNRATED, {"$ifNull": ["$items.hsn_code", "$items.hsn"]}, None]}
    },
    "unrated_category": {
        "$max": {
            "$cond": [_UNRATED, {"$ifNull": ["$items.item_type", "$items.category"]}, None]
        }
    },
}


def group_revenue_ex_gst(row: dict) -> float:
    """A $group row built with LINE_REVENUE_EX_GST_GROUP -> its revenue net of
    GST: the rated lines Mongo summed, plus the unrated gross backed out by
    line_revenue_ex_gst at the rate resolve_gst_rate gives its hints.
    ponytail: the unrated lines of one group are read at ONE rate (one hint
    pair) -- right for a per-product group; a mixed group would need its
    unrated lines grouped by (hsn, category) first."""
    row = row or {}
    rated = _f(row.get("revenue_ex_gst"))
    gross = _f(row.get("unrated_gross"))
    if not gross:
        return rated
    return rated + line_revenue_ex_gst(
        {
            "item_total": gross,
            "hsn_code": row.get("unrated_hsn"),
            "item_type": row.get("unrated_category"),
        }
    )


def line_unit_cost(item: dict, cost_by_product: Optional[dict] = None) -> Optional[float]:
    """THE ex-GST unit cost of an order line -- the P&L's COGS and every margin
    report read it here. In order: the ``cost_at_sale`` snapshot orders/create.py
    stamps (so a later cost edit never rewrites history), a legacy line
    ``cost_price`` / ``costPrice``, then the product's live catalogue
    cost_price from ``cost_by_product`` (older and online lines carry no
    snapshot). None when unknown -- the caller decides (P&L: 60% estimate)."""
    item = item or {}
    for k in ("cost_at_sale", "cost_price", "costPrice"):
        if item.get(k) is not None:
            try:
                return float(item.get(k))
            except (TypeError, ValueError):
                pass
    pid = item.get("product_id") or item.get("productId")
    cost = (cost_by_product or {}).get(pid) if pid else None
    if cost is None:
        return None
    try:
        return float(cost)
    except (TypeError, ValueError):
        return None


def cost_by_product(db) -> dict:
    """product_id (and _id) -> catalogue cost_price (ex-GST), the fallback
    ``line_unit_cost`` reads. Keyed both ways because imported orders may
    reference a product by its Mongo _id. No db / any error -> {}."""
    out: dict = {}
    try:
        for p in db.get_collection("products").find(
            {}, {"product_id": 1, "cost_price": 1}
        ):
            cp = p.get("cost_price")
            if cp is None:
                continue
            try:
                val = float(cp)
            except Exception:
                continue
            if p.get("product_id"):
                out[p["product_id"]] = val
            if p.get("_id") is not None:
                out[str(p["_id"])] = val
    except Exception:
        pass
    return out
