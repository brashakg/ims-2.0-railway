"""F19 - Dynamic landed-cost purchase matrix (pure allocation engine).

Captures freight / duty / customs / forex / insurance components on a vendor
bill and allocates them across the bill's line SKUs so per-unit cost reflects
the real cost-to-shelf (margins are otherwise overstated by exactly the
un-allocated freight/duty spend). ALL math is integer paise (house money
convention -- see services/non_adapt.rupees_to_paise).

Allocation methods:
    BY_VALUE  - proportional to line value (taxable, else qty * unit cost)
    BY_QTY    - proportional to line quantity
    BY_WEIGHT - proportional to line weight (fail-LOUD if any weight missing/0)

Invariants (the whole point of this engine):
    * sum(per-line landed_alloc_paise) == sum(component amount_paise) EXACTLY.
      Proportional floor-division leaves a residual of at most (n_lines - 1)
      paise; the WHOLE residual is assigned to the largest-base line (tie ->
      first such line), so the allocation is paise-exact and deterministic.
    * per-line: landed_alloc_paise == landed_per_unit_paise * qty
      + landed_remainder_paise (for integral qty > 0; a non-integral or zero
      qty keeps the entire allocation as the line-level remainder, since a
      "per unit" figure is only meaningful for whole units).
    * Pure dicts in / dicts out. No DB, no I/O, no clock. Unknown method,
      negative component amounts, BY_WEIGHT with missing/zero weights, or a
      zero allocation basis (when there IS money to allocate) raise ValueError
      -- fail loudly, never silently mis-cost inventory.

Line dict keys read here (all optional unless the method needs them):
    qty               - number (float ok; per-unit split needs integral qty)
    unit_cost_paise   - explicit integer-paise unit cost (wins when present)
    unit_price        - RUPEES (vendor-bill lines store rupees) -> paise
    taxable           - RUPEES line value (vendor-bill computed lines carry it)
    value_paise       - explicit integer-paise line value (wins when present)
    weight            - per-line weight (any consistent unit across the bill)
    landed_per_unit_paise - read back by landed_unit_cost_paise()

NO emoji in this file (Windows cp1252).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

ALLOCATION_METHODS = ("BY_VALUE", "BY_QTY", "BY_WEIGHT")

COMPONENT_TYPES = ("FREIGHT", "DUTY", "CUSTOMS", "FOREX", "INSURANCE", "OTHER")

# Integer scaling for fractional bases (qty 1.5, weight 0.25) so the share
# math stays in pure ints: milli-units / milli-weight are exact enough for any
# real purchase line while keeping sums overflow-safe (Python ints are
# unbounded anyway).
_BASE_SCALE = 1000


# ---------------------------------------------------------------------------
# Coercers (pure, total unless the spec says fail-loud)
# ---------------------------------------------------------------------------


def _num(value: Any, default: float = 0.0) -> float:
    """Coerce to float; garbage/None -> default. Never raises."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_paise(value: Any) -> int:
    """Coerce a paise amount to int (half-up on stray floats). Never raises;
    validation of sign happens in components_total_paise (fail-loud there)."""
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def _rupees_to_paise(value: Any) -> int:
    """Rupees -> integer paise, half-up (mirrors non_adapt.rupees_to_paise;
    duplicated as a 3-line private to keep this module import-free/pure)."""
    if value is None:
        return 0
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return 0


def _qty(line: Dict[str, Any]) -> float:
    return _num((line or {}).get("qty"), 0.0)


def _integral_qty(line: Dict[str, Any]) -> int:
    """The line qty as a whole number of units, or 0 when the qty is missing,
    non-positive, or fractional (no meaningful per-unit split)."""
    q = _qty(line)
    if q > 0 and float(q).is_integer():
        return int(q)
    return 0


def base_unit_cost_paise(line: Dict[str, Any]) -> int:
    """The line's BASE per-unit cost in integer paise (before landed costs).

    Precedence: explicit ``unit_cost_paise`` -> taxable/qty (the true billed
    per-unit value, net of line discounts) -> unit_price rupees. Floor on the
    taxable/qty division (cost never rounds up out of thin air)."""
    if not isinstance(line, dict):
        return 0
    explicit = line.get("unit_cost_paise")
    if explicit is not None:
        return max(0, _int_paise(explicit))
    qi = _integral_qty(line)
    taxable = line.get("taxable")
    if taxable is not None and qi > 0:
        return max(0, _rupees_to_paise(taxable) // qi)
    return max(0, _rupees_to_paise(line.get("unit_price")))


def line_value_paise(line: Dict[str, Any]) -> int:
    """The line's total value in integer paise (the BY_VALUE basis).

    Precedence: explicit ``value_paise`` -> ``taxable`` rupees -> qty * unit
    cost. Negative coerces to 0 (a negative-value purchase line is not a thing
    this allocator prices)."""
    if not isinstance(line, dict):
        return 0
    explicit = line.get("value_paise")
    if explicit is not None:
        return max(0, _int_paise(explicit))
    taxable = line.get("taxable")
    if taxable is not None:
        return max(0, _rupees_to_paise(taxable))
    return max(0, _int_paise(_qty(line) * base_unit_cost_paise(line)))


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------


def components_total_paise(components: Optional[List[Dict[str, Any]]]) -> int:
    """Sum component amount_paise (integer). FAIL-LOUD on a negative amount --
    a negative landed-cost component would silently shrink inventory cost."""
    total = 0
    for comp in components or []:
        if not isinstance(comp, dict):
            continue
        amt = _int_paise(comp.get("amount_paise"))
        if amt < 0:
            raise ValueError(
                "Landed-cost component amounts must be >= 0 paise "
                f"(got {amt} for {comp.get('type') or 'component'})"
            )
        total += amt
    return total


# ---------------------------------------------------------------------------
# Allocation bases
# ---------------------------------------------------------------------------


def _bases(lines: List[Dict[str, Any]], method: str) -> List[int]:
    """Integer allocation basis per line for the given method.

    BY_VALUE  -> line value in paise (already integer).
    BY_QTY    -> qty scaled x1000 (handles fractional qty exactly).
    BY_WEIGHT -> weight scaled x1000; ANY missing/zero/negative weight raises
                 ValueError (fail loud -- a silent zero would dump that line's
                 share onto the others and mis-cost everything).
    """
    if method == "BY_VALUE":
        return [line_value_paise(ln) for ln in lines]
    if method == "BY_QTY":
        return [max(0, int(round(_qty(ln) * _BASE_SCALE))) for ln in lines]
    if method == "BY_WEIGHT":
        bases = []
        for i, ln in enumerate(lines):
            w = _num((ln or {}).get("weight"), -1.0)
            if w <= 0:
                raise ValueError(
                    "BY_WEIGHT allocation requires a positive weight on every "
                    f"line; line {i} "
                    f"({(ln or {}).get('product_id') or 'no product_id'}) has "
                    f"weight={(ln or {}).get('weight')!r}"
                )
            bases.append(int(round(w * _BASE_SCALE)))
        return bases
    raise ValueError(
        f"Unknown allocation method {method!r}; expected one of "
        f"{ALLOCATION_METHODS}"
    )


# ---------------------------------------------------------------------------
# THE ALLOCATION
# ---------------------------------------------------------------------------


def allocate_landed_costs(
    lines: Optional[List[Dict[str, Any]]],
    components: Optional[List[Dict[str, Any]]],
    method: str = "BY_VALUE",
) -> List[Dict[str, Any]]:
    """Allocate landed-cost components across bill lines, paise-exact.

    Args:
        lines:      bill line dicts (see module docstring for keys read).
        components: [{type, label, amount_paise}, ...] integer paise.
        method:     one of ALLOCATION_METHODS.

    Returns one row per input line (same order):
        {
          line_index, product_id, qty,
          base,                       # the integer basis used for this line
          landed_alloc_paise,         # this line's share (sums EXACTLY)
          landed_per_unit_paise,      # alloc // qty for integral qty, else 0
          landed_remainder_paise,     # alloc - per_unit * qty (kept on line)
          landed_unit_cost_paise,     # base unit cost + per-unit landed
        }

    Empty lines -> []. Empty/zero components -> all-zero rows (a no-op
    allocation is representable; the ROUTER decides whether to 400 it).
    ValueError (fail loud): unknown method, negative component amount,
    BY_WEIGHT with a missing/zero weight, or a zero basis across ALL lines
    while there is money to allocate (cannot apportion against nothing).
    """
    if method not in ALLOCATION_METHODS:
        raise ValueError(
            f"Unknown allocation method {method!r}; expected one of "
            f"{ALLOCATION_METHODS}"
        )
    rows: List[Dict[str, Any]] = []
    lines = [ln if isinstance(ln, dict) else {} for ln in (lines or [])]
    if not lines:
        return rows

    total = components_total_paise(components)
    # Validate the basis EVEN when total is 0 for BY_WEIGHT (a captured-but-
    # zero-total bill with broken weights should still fail loud early)?
    # No: a zero total is a representable no-op; weights only matter when
    # money actually moves. Bases are computed only when total != 0.
    if total == 0:
        for i, ln in enumerate(lines):
            rows.append(
                {
                    "line_index": i,
                    "product_id": ln.get("product_id"),
                    "qty": _qty(ln),
                    "base": 0,
                    "landed_alloc_paise": 0,
                    "landed_per_unit_paise": 0,
                    "landed_remainder_paise": 0,
                    "landed_unit_cost_paise": base_unit_cost_paise(ln),
                }
            )
        return rows

    bases = _bases(lines, method)
    weight_sum = sum(bases)
    if weight_sum <= 0:
        raise ValueError(
            f"Cannot allocate {total} paise {method}: the allocation basis is "
            "zero across all lines (no value/qty/weight to apportion against)"
        )

    # Floor-share each line, then hand the WHOLE residual to the largest-base
    # line (tie -> first). Deterministic + paise-exact by construction.
    allocs = [total * b // weight_sum for b in bases]
    residual = total - sum(allocs)
    if residual:
        largest_i = max(range(len(bases)), key=lambda i: (bases[i], -i))
        allocs[largest_i] += residual

    for i, ln in enumerate(lines):
        alloc = allocs[i]
        qi = _integral_qty(ln)
        if qi > 0:
            per_unit = alloc // qi
            remainder = alloc - per_unit * qi
        else:
            per_unit = 0
            remainder = alloc
        rows.append(
            {
                "line_index": i,
                "product_id": ln.get("product_id"),
                "qty": _qty(ln),
                "base": bases[i],
                "landed_alloc_paise": alloc,
                "landed_per_unit_paise": per_unit,
                "landed_remainder_paise": remainder,
                "landed_unit_cost_paise": base_unit_cost_paise(ln) + per_unit,
            }
        )
    return rows


def landed_unit_cost_paise(line: Dict[str, Any]) -> int:
    """Base unit cost + allocated landed per-unit cost, integer paise.

    Reads ``landed_per_unit_paise`` off the line (as persisted by the
    allocation) on top of the base unit cost resolution."""
    if not isinstance(line, dict):
        return 0
    return base_unit_cost_paise(line) + max(
        0, _int_paise(line.get("landed_per_unit_paise"))
    )


def landed_unit_cost_by_product(
    lines: Optional[List[Dict[str, Any]]],
    allocation_rows: Optional[List[Dict[str, Any]]],
) -> Dict[str, int]:
    """Per-product landed unit cost (integer paise) for the product-master
    write: (sum of line values + sum of allocations) // total integral qty,
    aggregated across every line of that product (a bill CAN repeat a SKU).

    Lines with no product_id or no integral qty are skipped (nothing to cost
    per unit). Pure; the router persists the result fail-soft."""
    rows = allocation_rows or []
    agg: Dict[str, Dict[str, int]] = {}
    order: List[str] = []
    for i, ln in enumerate(lines or []):
        if not isinstance(ln, dict):
            continue
        pid = ln.get("product_id")
        qi = _integral_qty(ln)
        if not pid or qi <= 0:
            continue
        alloc = 0
        if i < len(rows) and isinstance(rows[i], dict):
            alloc = _int_paise(rows[i].get("landed_alloc_paise"))
        slot = agg.setdefault(pid, {"value": 0, "alloc": 0, "qty": 0})
        if pid not in order:
            order.append(pid)
        slot["value"] += line_value_paise(ln)
        slot["alloc"] += alloc
        slot["qty"] += qi
    return {
        pid: (agg[pid]["value"] + agg[pid]["alloc"]) // agg[pid]["qty"]
        for pid in order
        if agg[pid]["qty"] > 0
    }
