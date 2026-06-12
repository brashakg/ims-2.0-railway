"""F19 - Dynamic landed-cost purchase matrix (pure allocation engine).

Captures freight/duty/customs/forex/insurance components on a vendor bill and
allocates them across the bill's line SKUs so per-unit cost reflects the real
cost-to-shelf. ALL math is integer paise (house money convention).

Allocation methods:
    BY_VALUE  - proportional to line value (qty * unit_cost_paise)
    BY_QTY    - proportional to line quantity
    BY_WEIGHT - proportional to line weight (fail-loud if any weight missing)

Invariants:
    - sum(per-line allocations) == sum(component amounts) EXACTLY (paise-exact;
      residual paise from proportional rounding is assigned to the largest line).
    - Pure dicts in / dicts out. No DB, no I/O, no datetime.now().

NO emoji in this file (Windows cp1252).
"""

from __future__ import annotations

ALLOCATION_METHODS = ("BY_VALUE", "BY_QTY", "BY_WEIGHT")

COMPONENT_TYPES = ("FREIGHT", "DUTY", "CUSTOMS", "FOREX", "INSURANCE", "OTHER")


def allocate_landed_costs(lines, components, method):
    """Allocate landed-cost components across bill lines.

    Args:
        lines: list of dicts with at least qty, unit_cost_paise and
            (for BY_WEIGHT) weight per line.
        components: list of {type, label, amount_paise} dicts.
        method: one of ALLOCATION_METHODS.

    Returns:
        list of per-line allocation dicts (landed_alloc_paise,
        landed_per_unit_paise, landed_remainder_paise), paise-exact.
    """
    raise NotImplementedError("F19 skeleton - implemented in follow-up commit")


def landed_unit_cost_paise(line):
    """Base unit cost + allocated landed per-unit cost, integer paise."""
    raise NotImplementedError("F19 skeleton - implemented in follow-up commit")
