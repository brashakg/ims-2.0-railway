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
  * the line carries a product_id (we cannot mint a unit without one)

Everything takes plain dicts/values and returns plain dicts so it is trivially
testable and never touches Mongo.
"""

from __future__ import annotations

from typing import Any, Dict, List

# Condition that is safe to put back on the shelf. OPENED / DAMAGED are held.
RESELLABLE_CONDITION = "GOOD"


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
    a product_id is present. Defensive: any malformed / missing field makes the
    answer False rather than raising.
    """
    if not isinstance(line, dict):
        return False
    condition = str(line.get("condition", RESELLABLE_CONDITION) or "").upper()
    if condition != RESELLABLE_CONDITION:
        return False
    if not _truthy_restock_flag(line):
        return False
    if not line.get("product_id"):
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
    if _unit_count(line) <= 0:
        return "ZERO_QTY"
    return "NOT_RESELLABLE"
