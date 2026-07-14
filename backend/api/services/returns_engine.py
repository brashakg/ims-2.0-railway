"""
IMS 2.0 - Customer Returns / Exchange money-math engine
=======================================================
Pure functions, no DB, so the money math is deterministic + unit-tested. The
router persists; this module only computes.

IMPORTANT - GST convention (GST-INCLUSIVE pricing, 2026-05-29)
-------------------------------------------------------------
The refund a customer is owed is the GST-INCLUSIVE GROSS rupees they actually
PAID for the returned units. Under inclusive pricing the counter price IS the
all-in amount (orders.py: grand_total = taxable + tax where taxable = gross /
(1 + rate)). So the `unit_price` this module receives is ALREADY the gross the
customer paid for one unit -- the caller (returns router `_priced_return_lines`)
resolves it from the original order line's billed amount ((taxable_value +
tax_amount) / qty), which is correct for BOTH inclusive orders AND legacy
exclusive orders (where the stored taxable+tax summed to the grossed-up amount).
This module therefore does NOT gross `unit_price` up again -- it is summed as
billed. `gst_rate` is retained ONLY so the tax can be backed OUT of the gross
for the credit note / GSTR-1 reversal.

The concepts:
  - returned_value      = sum(return_qty * unit_price) over the returned items,
                          where unit_price is the GST-INCLUSIVE gross billed.
  - restocking_fee      = optional absolute Rs deduction for damaged / opened
                          goods. 0 <= fee <= gross. Net refund = gross - fee.
  - exchange settlement = replacement_total - returned_value:
                            > 0 -> COLLECT (customer pays the difference)
                            < 0 -> REFUND  (shop owes the customer)
                            ~ 0 -> EVEN    (within a small epsilon)
                          (replacement unit_price is the inclusive offer price.)
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


def gross_unit_price(unit_price: Any, gst_rate: Any = None) -> float:
    """GST-INCLUSIVE gross unit price.

    Under inclusive pricing `unit_price` is ALREADY the gross the customer paid
    for one unit, so it is returned unchanged (rounded 2dp). `gst_rate` is
    accepted for signature/back-compat and VALIDATED (negatives rejected) but
    NOT applied -- the tax is inside the price; use `gst_breakup` to back it
    out. Raises ValueError on negative / non-numeric input.
    """
    price = _coerce_price(unit_price, "unit_price")
    _coerce_rate(gst_rate)  # validate only; do NOT gross up an inclusive price
    return round(price, 2)


def returned_value(items: List[Dict[str, Any]]) -> float:
    """GST-INCLUSIVE gross value of the returned items.

    For each line: return_qty * unit_price, where `unit_price` is the GST-
    INCLUSIVE gross billed for one unit (the caller resolves it from the
    original order line's billed amount). Accumulated and rounded ONCE at the
    end (2dp). `items` is a list of dicts each carrying `return_qty` (or
    `quantity`), `unit_price`, and optional `gst_rate` (validated but NOT
    applied -- it is the tax already inside unit_price). Raises ValueError if
    any qty/price/rate is negative or non-numeric.
    """
    total = 0.0
    for it in items or []:
        qty = _coerce_qty(it.get("return_qty", it.get("quantity")), "return_qty")
        price = _coerce_price(it.get("unit_price"), "unit_price")
        _coerce_rate(it.get("gst_rate"))  # validate only; price is inclusive
        total += qty * price
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

    replacement_total = sum(quantity * unit_price) over replacement_items,
    where unit_price is the GST-INCLUSIVE offer price of each replacement
    (the tax is inside it), mirroring returned_value.
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
        _coerce_rate(it.get("gst_rate"))  # validate only; offer price is inclusive
        replacement_total += qty * price
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


def gst_breakup_lines(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """EXACT per-line GST back-out for a credit note that spans MIXED GST rates.

    ``dominant_gst_rate`` + ``gst_breakup`` back one single rate out of the whole
    gross -- wrong when a return mixes rates (e.g. an 18% frame + a 5% lens),
    where it under/over-states the tax by hundreds of rupees. This backs the tax
    out of EACH line at ITS OWN rate and aggregates, so the credit-note tax equals
    the sum of the original line taxes (a true reversal).

    For each line: line_gross = return_qty * unit_price (the GST-INCLUSIVE gross
    billed), then ``gst_breakup(line_gross, line_rate)`` splits it. Returns
    ``{gross, taxable, tax, gst_rate, by_rate:{"<rate>":{gross,taxable,tax}}}``
    where ``gst_rate`` is the DOMINANT rate (kept for single-rate display /
    back-compat) and ``by_rate`` carries the exact split the accountant sees.
    Defensive: a bad line is skipped, never raises.
    """
    by_rate: Dict[float, Dict[str, float]] = {}
    total_gross = 0.0
    total_taxable = 0.0
    total_tax = 0.0
    for it in items or []:
        try:
            qty = _coerce_qty(it.get("return_qty", it.get("quantity")), "return_qty")
            price = _coerce_price(it.get("unit_price"), "unit_price")
            rate = _coerce_rate(it.get("gst_rate"))
        except ValueError:
            continue
        line_gross = round(qty * price, 2)
        bd = gst_breakup(line_gross, rate)
        total_gross += line_gross
        total_taxable += bd["taxable"]
        total_tax += bd["tax"]
        slot = by_rate.setdefault(rate, {"gross": 0.0, "taxable": 0.0, "tax": 0.0})
        slot["gross"] = round(slot["gross"] + line_gross, 2)
        slot["taxable"] = round(slot["taxable"] + bd["taxable"], 2)
        slot["tax"] = round(slot["tax"] + bd["tax"], 2)
    dominant = (
        max(by_rate, key=lambda r: by_rate[r]["gross"]) if by_rate else 0.0
    )
    return {
        "gross": round(total_gross, 2),
        "taxable": round(total_taxable, 2),
        "tax": round(total_tax, 2),
        "gst_rate": dominant,
        "by_rate": {str(r): v for r, v in by_rate.items()},
    }
