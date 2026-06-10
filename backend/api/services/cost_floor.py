"""
Fcostfloor -- POS sell-path cost+pct% price floor (DECISIONS sec 9, Phase 2).
=============================================================================
Blocks an order line whose EFFECTIVE per-unit taxable sell price -- after the
per-line discount AND its share of the cart-level discount -- falls below
``cost_price x (1 + pct/100)``.

Contract (packet ``docs/roadmap/features/Fcostfloor.md`` + owner sign-off
2026-06-09 "enable everywhere"):

* The floor pct comes from E2 ``get_policy("pricing.cost_floor_pct", scope)``
  (registry default 10.0). The enable switch is the E2 bool policy
  ``pricing.cost_floor_enabled`` -- default ON (global), store-overridable so
  the orchestrator can opt a store out.
* FAIL-OPEN: a line with no known cost (missing product doc, virtual
  custom-/lens- SKU, cost_price absent/0) is NOT floored -- a chain with
  patchy cost data must sell normally. Same for a line the GST pass did not
  stamp a ``taxable_value`` onto (defensive).
* Rs 0 / 100%-discount lines stay EXEMPT (they are gated by the C-4
  zero-total approval requirement instead).
* RAW COST ONLY: reads the server-side ``cost_at_sale`` snapshot (stamped
  from ``_cost_by_pid`` -- the raw ``product_repo.cost_price``), never a
  masked/role-filtered DTO. When F35 cost-masking ships on the READ side,
  this WRITE-path guard keeps reading the raw catalog cost.
* COMPOSES with the role/category/brand discount caps: those run before this
  in create_order and are untouched; the floor is an ADDITIONAL lower bound.
* GST-exclusive like-for-like: the comparison uses the line's stamped
  ``taxable_value`` (GST-exclusive in both inclusive + exclusive pricing
  modes) against the GST-exclusive catalog cost. Boundary eff == floor is
  ACCEPTED.
* ``pct <= 0`` disables the post-discount pass (packet acceptance test 3:
  knob lowered to 0 -> a deep-discount line is accepted; the legacy
  pre-discount cost+0% check in orders.py still applies independently).
* Flag OFF -> immediate no-op: order-create behavior is byte-identical to
  the pre-change code (the legacy orders.py checks are not touched).

Pure read-only math over the already-computed line finals: no GST math,
payment capture, item_total computation, or order persistence change.
No emoji in this file (Windows cp1252).
"""
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

FLAG_KEY = "pricing.cost_floor_enabled"
PCT_KEY = "pricing.cost_floor_pct"

# Mirror the legacy orders.py float-compare tolerance so "exactly at the
# floor" is accepted (packet acceptance test 7).
_EPS = 1e-6


def _floor_policy(store_id: Optional[str]) -> Tuple[bool, float]:
    """Resolve (enabled, pct) from E2 with store>entity>global scoping.

    Fail-soft: if the policy engine is unavailable, fall back to the registry
    code defaults (enabled=True, pct=10.0) -- matching what a fresh DB
    resolves to, so behavior stays deterministic either way.
    """
    try:
        from .policy_engine import get_policy

        scope = {"store_id": store_id} if store_id else None
        enabled = bool(get_policy(FLAG_KEY, scope, default=True))
        pct = float(get_policy(PCT_KEY, scope, default=10.0))
        return enabled, pct
    except Exception:  # noqa: BLE001
        return True, 10.0


def _num(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def enforce_cost_floor(
    items_data: List[Dict[str, Any]],
    cost_by_pid: Optional[Dict[str, float]] = None,
    store_id: Optional[str] = None,
) -> None:
    """Raise HTTP 400 when any priced line's effective post-discount per-unit
    taxable price is below cost x (1 + pct/100). Call AFTER
    ``_compute_per_category_gst`` has stamped ``taxable_value`` per line.

    ``items_data`` rows must carry ``taxable_value`` (post per-line AND cart
    discount, GST-exclusive), ``quantity``, ``discount_percent`` and the raw
    ``cost_at_sale`` snapshot. ``cost_by_pid`` is the raw server-side cost
    map (fallback lookup; keys may predate product-id canonicalisation).
    """
    enabled, pct = _floor_policy(store_id)
    if not enabled:
        return  # flag OFF -> byte-identical pre-change behavior
    if pct is None or pct <= 0:
        return  # knob at 0 -> post-discount floor disabled (test 3)

    for it in items_data or []:
        if not isinstance(it, dict):
            continue
        pid = str(it.get("product_id") or "")
        # Raw server-side cost snapshot (NEVER a masked DTO -- see module doc).
        cost = _num(it.get("cost_at_sale"))
        if cost is None and cost_by_pid:
            cost = _num(cost_by_pid.get(pid))
        if cost is None or cost <= 0:
            continue  # FAIL-OPEN: unknown/zero cost -> floor cannot evaluate

        qty = _num(it.get("quantity")) or 0.0
        if qty <= 0:
            continue  # defensive -- schema already requires quantity >= 1

        # Rs 0 / 100%-discount exemption (C-4 approval-gated, packet test 6).
        disc_pct = _num(it.get("discount_percent")) or 0.0
        if disc_pct >= 100.0 - _EPS:
            continue

        taxable = _num(it.get("taxable_value"))
        if taxable is None:
            continue  # GST pass did not stamp this line -> fail-open
        eff_unit = taxable / qty
        if eff_unit <= _EPS:
            continue  # Rs 0 line (free item / 100% cart discount) -> exempt

        floor = cost * (1.0 + pct / 100.0)
        if eff_unit < floor - _EPS:
            name = it.get("product_name") or pid or "item"
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Effective price Rs{round(eff_unit, 2)} for {name} is "
                    f"below the cost+{round(pct, 2)}% floor "
                    f"Rs{round(floor, 2)}. Contact a manager."
                ),
            )
