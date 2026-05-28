"""
IMS 2.0 - Customer Returns / Exchange money-math engine
=======================================================
Pure functions, no DB, so the money math is deterministic + unit-tested. The
router persists; this module only computes.

IMPORTANT - GST convention (corrected 2026-05-28)
-------------------------------------------------
The refund a customer is owed is the GST-INCLUSIVE GROSS rupees they actually
PAID for the returned units (Indian MRP convention). IMS orders store a NET
(pre-GST) `unit_price` on each line and add GST ON TOP (orders.py:
grand_total = taxable + tax). So the gross a customer paid for a line is:

    gross_unit = unit_price * (1 + gst_rate / 100)

Earlier this module summed `return_qty * unit_price` with no gross-up, which
dropped the GST and UNDER-refunded the customer by the tax fraction (the
QA-confirmed bug: order paid Rs 1,404 = Rs 1,190 net + Rs 214 GST, refunded
only Rs 1,190). `returned_value` now grosses each line up by its `gst_rate`
so the figure is the gross rupees billed. A line that is ALREADY gross simply
carries `gst_rate = 0` and is summed unchanged (backward compatible).

The concepts:
  - returned_value      = sum(return_qty * gross_unit) over the returned items
                          (GST-INCLUSIVE gross = what the customer paid).
  - restocking_fee      = optional absolute Rs deduction for damaged / opened
                          goods. 0 <= fee <= gross. Net refund = gross - fee.
  - exchange settlement = replacement_total - returned_value:
                            > 0 -> COLLECT (customer pays the difference)
                            < 0 -> REFUND  (shop owes the customer)
                            ~ 0 -> EVEN    (within a small epsilon)
  - credit-note amount  = the returned_value (issued to the customer as credit).
  - gst_breakup         = backs the tax OUT of a gross figure for the credit
                          note / GSTR-1 reversal (tax is NOT added on top).
"""

from __future__ import annotations

from typing import Any, Dict, List

# Settlement directions
COLLECT = "COLLECT"  # replacement worth more -> customer pays the difference
REFUND = "REFUND"  # replacement worth less -> shop refunds the difference
EVEN = "EVEN"  # within epsilon -> no money moves

# Rupee epsilon: anything below 1 paisa is treated as zero so float dust in the
# subtraction does not flip an even exchange into a 0.00x COLLECT/REFUND.
_EPS = 0.005


def _coerce_qty(value: Any, label: str) -> float:
    """Validate + coerce a quantity. Raises ValueError on negative / non-numeric."""
    try:
        qty = float(value if value is not None else 0)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number")
    if qty < 0:
        raise ValueError(f"{label} cannot be negative")
    return qty


def _coerce_price(value: Any, label: str) -> float:
    """Validate + coerce a unit price. Raises ValueError on negative / non-numeric."""
    try:
        price = float(value if value is not None else 0)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number")
    if price < 0:
        raise ValueError(f"{label} cannot be negative")
    return price


def _coerce_rate(value: Any, label: str = "gst_rate") -> float:
    """Validate + coerce a GST rate percentage. Defaults to 0 (no gross-up).

    A None / absent rate means "this price is already gross" -> 0 so the line
    is summed unchanged. Negative rates are rejected.
    """
    try:
        rate = float(value if value is not None else 0)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number")
    if rate < 0:
        raise ValueError(f"{label} cannot be negative")
    return rate


def gross_unit_price(unit_price: Any, gst_rate: Any) -> float:
    """GST-INCLUSIVE gross unit price = net unit_price grossed up by gst_rate.

    gross = unit_price * (1 + gst_rate / 100), rounded to 2dp. When gst_rate is
    0 / None the price is returned unchanged (it is already gross). Raises
    ValueError on negative / non-numeric input.
    """
    price = _coerce_price(unit_price, "unit_price")
    rate = _coerce_rate(gst_rate)
    return round(price * (1.0 + rate / 100.0), 2)


def returned_value(items: List[Dict[str, Any]]) -> float:
    """GST-INCLUSIVE gross value of the returned items.

    For each line: return_qty * unit_price * (1 + gst_rate/100), accumulated
    and rounded ONCE at the end (2dp) - the same single-round convention the
    original net math used, so a line with no rate (gst_rate absent / 0)
    returns an identical figure to before this fix. `items` is a list of dicts
    each carrying `return_qty` (or `quantity`), `unit_price`, and optional
    `gst_rate` (absent / 0 -> price already gross). Raises ValueError if any
    qty/price/rate is negative or non-numeric.
    """
    total = 0.0
    for it in items or []:
        qty = _coerce_qty(it.get("return_qty", it.get("quantity")), "return_qty")
        price = _coerce_price(it.get("unit_price"), "unit_price")
        rate = _coerce_rate(it.get("gst_rate"))
        total += qty * price * (1.0 + rate / 100.0)
    return round(total, 2)


def net_refund(gross: Any, restocking_fee: Any = 0) -> float:
    """Net refund owed to the customer = gross - restocking_fee.

    `gross` is the GST-inclusive returned value. `restocking_fee` is an
    optional absolute Rs deduction for damaged / opened goods. The fee must be
    >= 0 and <= gross. Raises ValueError otherwise. Rounded to 2dp.
    """
    g = round(_coerce_price(gross, "gross"), 2)
    fee = round(_coerce_price(restocking_fee, "restocking_fee"), 2)
    if fee > g + _EPS:
        raise ValueError(
            "restocking_fee cannot exceed the gross refund of "
            f"Rs {g:.2f} (got Rs {fee:.2f})"
        )
    return round(g - fee, 2)


def gst_breakup(gross: Any, gst_rate: Any) -> Dict[str, float]:
    """Back the GST OUT of a GST-INCLUSIVE gross figure (NOT added on top).

    For a credit note / GSTR-1 reversal we must report the taxable base and the
    tax that were INSIDE the gross the customer paid:

        taxable = gross / (1 + rate/100)
        tax     = gross - taxable

    Returns {gross, taxable, tax, gst_rate}. A 0 / None rate yields tax 0 and
    taxable == gross. Raises ValueError on negative / non-numeric input.
    """
    g = round(_coerce_price(gross, "gross"), 2)
    rate = _coerce_rate(gst_rate)
    if rate <= 0:
        return {"gross": g, "taxable": g, "tax": 0.0, "gst_rate": rate}
    taxable = round(g / (1.0 + rate / 100.0), 2)
    tax = round(g - taxable, 2)
    return {"gross": g, "taxable": taxable, "tax": tax, "gst_rate": rate}


def exchange_settlement(
    returned_total: float, replacement_items: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Compute the money settlement for an EXCHANGE.

    replacement_total = sum(quantity * gross_unit) over replacement_items,
    where gross_unit grosses each replacement's NET `unit_price` up by its
    `gst_rate` (absent / 0 -> already gross), mirroring returned_value.
    difference        = round(replacement_total - returned_total, 2)
      difference > 0  -> COLLECT (customer pays |difference|)
      difference < 0  -> REFUND  (shop owes |difference|)
      |difference| ~0 -> EVEN

    `returned_total` is expected to already be the GST-inclusive returned_value.
    Returns a dict with `replacement_total`, `returned_value`, `difference`
    (ABSOLUTE rupee amount), and `direction`. Raises ValueError on bad input.
    """
    returned_total = round(_coerce_price(returned_total, "returned_value"), 2)

    replacement_total = 0.0
    for it in replacement_items or []:
        qty = _coerce_qty(it.get("quantity", it.get("return_qty")), "quantity")
        price = _coerce_price(it.get("unit_price"), "unit_price")
        rate = _coerce_rate(it.get("gst_rate"))
        replacement_total += qty * price * (1.0 + rate / 100.0)
    replacement_total = round(replacement_total, 2)

    difference = round(replacement_total - returned_total, 2)
    if abs(difference) < _EPS:
        direction = EVEN
    elif difference > 0:
        direction = COLLECT
    else:
        direction = REFUND

    return {
        "replacement_total": replacement_total,
        "returned_value": returned_total,
        "difference": round(abs(difference), 2),
        "direction": direction,
    }


def dominant_gst_rate(items: List[Dict[str, Any]]) -> float:
    """The GST rate carrying the most gross value across the returned lines.

    Used to pick a single rate for the credit-note GST back-out when the return
    spans lines. Ties + empty -> 0.0. Defensive: never raises on bad input.
    """
    by_rate: Dict[float, float] = {}
    for it in items or []:
        try:
            qty = _coerce_qty(it.get("return_qty", it.get("quantity")), "qty")
            gross_unit = gross_unit_price(it.get("unit_price"), it.get("gst_rate"))
            rate = _coerce_rate(it.get("gst_rate"))
        except ValueError:
            continue
        by_rate[rate] = round(by_rate.get(rate, 0.0) + qty * gross_unit, 2)
    if not by_rate:
        return 0.0
    return max(by_rate, key=lambda r: by_rate[r])
