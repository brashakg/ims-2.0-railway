"""
IMS 2.0 - Returns restock decision engine
=========================================
Pure, side-effect-free decision logic for putting a returned unit back into
sellable serialized stock. No DB imports, so the "should this line be
restocked, and for how many units?" decision is deterministic + unit-tested.

The router (returns.py) owns the actual stock writes (re-activate the original
SOLD/RETURNED serialized unit, else mint a fresh AVAILABLE one). This module
only answers two questions:

  1. is a single returned line RESELLABLE?  -> should_restock(line)
  2. how many units, per product, should go back on the shelf, and is the
     return already applied (idempotency)?  -> plan_restock(...)

A line is RESELLABLE only when ALL of these hold:
  * condition is GOOD          (OPENED / DAMAGED units are NOT restocked)
  * the per-line `restock` flag is truthy (defaults True; lets the till
    explicitly suppress restock even for a GOOD-looking unit)
  * return_qty rounds to >= 1 unit
  * the line carries a product_id (we cannot mint a unit without one), and
    it is not a virtual till item (no stock row ever left for one)

It also holds THE rule for which bill lines take a serialized stock unit at
all (takes_serialized_stock): the sale asks it before claiming a unit, the
return before putting one back.

Everything takes plain dicts/values and returns plain dicts so it is trivially
testable and never touches Mongo.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Condition that is safe to put back on the shelf. OPENED / DAMAGED are held.
RESELLABLE_CONDITION = "GOOD"


# Item types / product_id prefixes that have NO serialized stock to mark sold.
# SERVICE = labour line (eg fitting); EYE_TEST/etc = a clinical consultation line
# (GST-exempt SAC 9993, see gst_rates.py) -- a service, not a stocked good;
# custom-/lens-/lens-sug- = virtual POS items the configurator/suggestion helper
# generates on the fly. These never carry a stock_unit row, so trying to
# mark_sold them is a no-op (not an error).
VIRTUAL_PID_PREFIXES = ("custom-", "lens-", "lens-sug-")
NON_SERIALIZED_ITEM_TYPES = {
    "SERVICE",
    "EYE_TEST",
    "EYE_EXAM",
    "EYE_CHECKUP",
    "CONSULT",
    "CONSULTATION",
    "OPTOMETRY",
}

# Item types whose stock is reserved by the LENS hook (reserve_for_order_item),
# NOT by the serialized stock_units availability gate.
LENS_RESERVED_ITEM_TYPES = {"LENS"}


def takes_serialized_stock(line: dict) -> bool:
    """Does this order line consume a SERIALIZED stock_units row?

    THE single predicate for that question. It previously existed three times,
    written three slightly different ways, and they drifted: the availability
    assert and the line-release both excluded LENS lines while _mark_units_sold
    did NOT. POSLayout maps OPTICAL_LENS to item_type 'LENS' and sends the REAL
    catalog product_id, so a stock-lens line had its unit flipped SOLD on the
    way in, was never availability-checked, and was never released when the line
    was removed -- stranded permanently once the order reached DELIVERED.

    Reserved-by-the-lens-hook lines and non-stocked service lines are excluded;
    so are virtual POS ids that have no stock_units row at all.

    It lives here (a service) so the return side asks the same question
    (line_stock_product_id): a line that took no unit puts none back.
    """
    if not isinstance(line, dict):
        return False
    item_type = (line.get("item_type") or "").upper()
    if (
        item_type in NON_SERIALIZED_ITEM_TYPES
        or item_type in LENS_RESERVED_ITEM_TYPES
    ):
        return False
    pid = line.get("product_id") or ""
    if not pid or pid.startswith(VIRTUAL_PID_PREFIXES):
        return False
    return True


def line_product_id(order_line: Optional[Dict[str, Any]]) -> Optional[str]:
    """THE IMS product a bill line names -- the line's identity in the return
    books. An online line keeps Shopify's product_id beside its
    ims_product_id; stock rows and return rows carry the IMS one. None for a
    line that names no product (a fee)."""
    line = order_line or {}
    pid = line.get("ims_product_id") or line.get("product_id")
    return str(pid) if pid else None


def line_stock_product_id(order_line: Optional[Dict[str, Any]]) -> Optional[str]:
    """The product of the stock rows a return of this bill line puts back or
    sets aside: line_product_id, for a line whose sale took a stock unit
    (takes_serialized_stock). None for every other line -- a lens, a service,
    an eye test, a virtual till item, a fee: no unit left for it, so none is
    put back, set aside or minted."""
    pid = line_product_id(order_line)
    if pid and takes_serialized_stock({**order_line, "product_id": pid}):
        return pid
    return None


def _truthy_restock_flag(line: Dict[str, Any]) -> bool:
    """The per-line restock flag, defaulting to True when absent.

    Accepts a real bool, or the common string encodings ("true"/"false",
    "1"/"0", "yes"/"no") that a JSON / form client might send.
    """
    if "restock" not in line or line.get("restock") is None:
        return True
    val = line.get("restock")
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        return val.strip().lower() not in ("false", "0", "no", "n", "")
    return bool(val)


def _unit_count(line: Dict[str, Any]) -> int:
    """Whole-unit quantity for a returned line (serialized stock is per-unit)."""
    raw = line.get("return_qty", line.get("quantity"))
    try:
        qty = float(raw if raw is not None else 0)
    except (TypeError, ValueError):
        return 0
    if qty <= 0:
        return 0
    return int(round(qty))


def should_restock(line: Dict[str, Any]) -> bool:
    """True iff this returned line should be put back into sellable stock.

    Resellable = GOOD condition AND restock flag truthy AND >=1 whole unit AND
    a product_id is present that is not a virtual till item. Defensive: any
    malformed / missing field makes the answer False rather than raising.
    """
    if not isinstance(line, dict):
        return False
    condition = str(line.get("condition", RESELLABLE_CONDITION) or "").upper()
    if condition != RESELLABLE_CONDITION:
        return False
    if not _truthy_restock_flag(line):
        return False
    pid = line.get("product_id")
    if not pid or str(pid).startswith(VIRTUAL_PID_PREFIXES):
        return False
    return _unit_count(line) > 0


def plan_restock(
    items: List[Dict[str, Any]], already_applied: bool = False
) -> Dict[str, Any]:
    """Decide which returned units to put back on the shelf.

    Args:
        items: the returned lines (each a dict with product_id, condition,
            return_qty/quantity, optional restock flag, sku, product_name).
        already_applied: if the return doc was already restocked once, this is
            a no-op (idempotency guard) and `units` comes back empty.

    Returns a dict:
        {
          "already_applied": bool,    # echo of the guard
          "unit_count": int,          # total individual units to restock
          "units": [                  # one entry PER physical unit to add
            {"product_id", "sku", "product_name", "condition"}, ...
          ],
          "skipped": [                # lines NOT restocked, with a reason
            {"product_id", "sku", "reason"}, ...
          ],
        }

    One entry per unit (not per line) because real on-hand is the serialized
    `stock` collection - one Mongo doc per physical unit. Returning N expanded
    units lets the router re-activate/mint exactly N rows.
    """
    units: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    if already_applied:
        return {
            "already_applied": True,
            "unit_count": 0,
            "units": [],
            "skipped": [],
        }

    for line in items or []:
        if not isinstance(line, dict):
            continue
        if should_restock(line):
            qty = _unit_count(line)
            for _ in range(qty):
                units.append(
                    {
                        "product_id": line.get("product_id"),
                        # Which order line it came off, so the router can put
                        # back the unit that line names.
                        "order_item_id": line.get("order_item_id"),
                        "sku": line.get("sku", ""),
                        "product_name": line.get("product_name", ""),
                        "condition": str(
                            line.get("condition", RESELLABLE_CONDITION)
                        ).upper(),
                    }
                )
        else:
            skipped.append(
                {
                    "product_id": line.get("product_id"),
                    "sku": line.get("sku", ""),
                    "reason": _skip_reason(line),
                }
            )

    return {
        "already_applied": False,
        "unit_count": len(units),
        "units": units,
        "skipped": skipped,
    }


def _skip_reason(line: Dict[str, Any]) -> str:
    """Human-readable reason a line was not restocked (for the return doc)."""
    if not isinstance(line, dict):
        return "INVALID_LINE"
    condition = str(line.get("condition", RESELLABLE_CONDITION) or "").upper()
    if condition != RESELLABLE_CONDITION:
        return f"CONDITION_{condition or 'UNKNOWN'}"
    if not _truthy_restock_flag(line):
        return "RESTOCK_OPTED_OUT"
    if not line.get("product_id"):
        return "NO_PRODUCT_ID"
    if str(line.get("product_id")).startswith(VIRTUAL_PID_PREFIXES):
        return "NOT_STOCK"
    if _unit_count(line) <= 0:
        return "ZERO_QTY"
    return "NOT_RESELLABLE"
