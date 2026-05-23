"""
IMS 2.0 - Customer Returns / Exchange money-math engine
=======================================================
Pure functions, no DB, so the money math is deterministic + unit-tested. The
router persists; this module only computes.

IMPORTANT - GST convention
--------------------------
Amounts here are GST-INCLUSIVE gross rupees (Indian MRP convention). The
unit_price the POS/frontend passes already includes GST, so we do NOT recompute
or strip GST. The refund / credit-note / exchange-settlement figures this module
returns are therefore the gross rupee amounts as billed.

The three concepts:
  - returned_value      = sum(return_qty * unit_price) over the returned items.
  - exchange settlement = replacement_total - returned_value:
                            > 0 -> COLLECT (customer pays the difference)
                            < 0 -> REFUND  (shop owes the customer)
                            ~ 0 -> EVEN    (within a small epsilon)
  - credit-note amount  = the returned_value (issued to the customer as credit).
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


def returned_value(items: List[Dict[str, Any]]) -> float:
    """Gross value of the returned items = sum(return_qty * unit_price).

    `items` is a list of dicts each carrying `return_qty` (or `quantity`) and
    `unit_price`. GST-inclusive; rounded to 2dp. Raises ValueError if any
    qty/price is negative or non-numeric.
    """
    total = 0.0
    for it in items or []:
        qty = _coerce_qty(
            it.get("return_qty", it.get("quantity")), "return_qty"
        )
        price = _coerce_price(it.get("unit_price"), "unit_price")
        total += qty * price
    return round(total, 2)


def exchange_settlement(
    returned_total: float, replacement_items: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Compute the money settlement for an EXCHANGE.

    replacement_total = sum(quantity * unit_price) over replacement_items.
    difference        = round(replacement_total - returned_total, 2)
      difference > 0  -> COLLECT (customer pays |difference|)
      difference < 0  -> REFUND  (shop owes |difference|)
      |difference| ~0 -> EVEN

    Returns a dict with `replacement_total`, `returned_value`, `difference`
    (ABSOLUTE rupee amount), and `direction`. Raises ValueError on bad input.
    """
    returned_total = round(_coerce_price(returned_total, "returned_value"), 2)

    replacement_total = 0.0
    for it in replacement_items or []:
        qty = _coerce_qty(it.get("quantity", it.get("return_qty")), "quantity")
        price = _coerce_price(it.get("unit_price"), "unit_price")
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
