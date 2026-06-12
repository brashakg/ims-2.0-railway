"""N7 - Contact-lens / lens Purchase-Order generator (pure service).

Turns per-power lens replenishment needs (from the Base-Bank replenishment
endpoint or the lens-stock gap planner) into vendor-grouped DRAFT purchase
order lines whose lines carry the power cell (SPH/CYL/ADD + qty), so a
supplier receives an exact power-grid order.

Pure functions only: no DB access, no FastAPI imports. The router
(backend/api/routers/cl_po.py) does the I/O and persistence.

Need row shape (input):
    {lens_line_id|product_id, sph, cyl, add, qty, description?, sku?,
     unit_price?}

PO line shape (output):
    {product_id, lens_line_id, product_name, sku, quantity, unit_price,
     power: {sph, cyl, add}, description}
"""

from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = [
    "build_po_lines",
    "group_needs_by_vendor",
    "describe_line",
    "format_power_value",
]


def _coerce_qty(value: Any) -> int:
    """Coerce a quantity to a non-negative int. Garbage coerces to 0 so the
    caller's qty<=0 drop discards it (never raises)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def _power_component(value: Any) -> Optional[float]:
    """Normalise one power component (sph/cyl/add) to a 2-dp float or None.

    None / empty / non-numeric values resolve to None so "+2.00", "2" and
    2.0 all snap to the same cell key (2.0)."""
    if value is None or value == "":
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def format_power_value(value: float) -> str:
    """Render a dioptre value the way an optical PO reads: signed, 2 dp.

    -2.0 -> "-2.00", 1.5 -> "+1.50", 0 -> "0.00"."""
    v = float(value)
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.2f}"


def describe_line(
    description: Optional[str],
    sph: Optional[float],
    cyl: Optional[float],
    add: Optional[float],
) -> str:
    """Human PO-line description, e.g. "Acuvue Oasys SPH -2.00 CYL -0.75".

    CYL/ADD are omitted when absent or zero (a spherical-only cell reads
    "Brand Line SPH -2.00"); SPH is included whenever present, including
    plano (0.00) cells."""
    parts: List[str] = []
    base = (description or "").strip()
    if base:
        parts.append(base)
    if sph is not None:
        parts.append("SPH " + format_power_value(sph))
    if cyl is not None and cyl != 0:
        parts.append("CYL " + format_power_value(cyl))
    if add is not None and add != 0:
        parts.append("ADD " + format_power_value(add))
    return " ".join(parts)


def _cell_key(need: Dict[str, Any]) -> Tuple[str, Optional[float], Optional[float], Optional[float]]:
    """Merge key for one need row: (item id, sph, cyl, add)."""
    item_id = str(need.get("lens_line_id") or need.get("product_id") or "")
    return (
        item_id,
        _power_component(need.get("sph")),
        _power_component(need.get("cyl")),
        _power_component(need.get("add")),
    )


def build_po_lines(needs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert raw need rows into PO line dicts carrying a power cell.

    - drops rows with qty <= 0 (after int coercion; garbage qty -> 0 -> drop)
    - merges duplicate power cells (same item + sph/cyl/add), summing qty
    - normalises sph/cyl/add to 2-dp floats (or None)
    - builds a human description like "Acuvue Oasys SPH -2.00 CYL -0.75"

    Deterministic: output preserves first-seen order of distinct cells.
    """
    merged: Dict[Tuple[str, Optional[float], Optional[float], Optional[float]], Dict[str, Any]] = {}
    order: List[Tuple[str, Optional[float], Optional[float], Optional[float]]] = []

    for need in needs or []:
        if not isinstance(need, dict):
            continue
        qty = _coerce_qty(need.get("qty"))
        if qty <= 0:
            continue
        key = _cell_key(need)
        if key in merged:
            merged[key]["quantity"] += qty
            continue

        item_id, sph, cyl, add = key
        try:
            unit_price = float(need.get("unit_price") or 0.0)
        except (TypeError, ValueError):
            unit_price = 0.0
        if unit_price < 0:
            unit_price = 0.0
        name = (str(need.get("description") or "")).strip() or item_id
        line = {
            "product_id": str(need.get("product_id") or need.get("lens_line_id") or ""),
            "lens_line_id": need.get("lens_line_id"),
            "product_name": name,
            "sku": str(need.get("sku") or ""),
            "quantity": qty,
            "unit_price": unit_price,
            "power": {"sph": sph, "cyl": cyl, "add": add},
            "description": describe_line(need.get("description"), sph, cyl, add) or item_id,
        }
        merged[key] = line
        order.append(key)

    return [merged[k] for k in order]


def group_needs_by_vendor(
    needs: List[Dict[str, Any]],
    vendor_resolver: Callable[[Dict[str, Any]], Optional[str]],
) -> Dict[Optional[str], List[Dict[str, Any]]]:
    """Group PO lines by preferred vendor.

    vendor_resolver maps a need row (lens_line_id/product_id) to a vendor_id
    or None. Lines without a resolvable vendor (or whose resolver raises -
    fail-soft) group under vendor_id=None so the PO drafts with vendor_id
    null (the frontend disables send on a vendor-less draft).

    Returns {vendor_id_or_None: [po_line, ...]}; groups whose lines all drop
    (qty <= 0) are omitted. Deterministic first-seen vendor order.
    """
    by_vendor: Dict[Optional[str], List[Dict[str, Any]]] = {}
    order: List[Optional[str]] = []

    for need in needs or []:
        if not isinstance(need, dict):
            continue
        vendor_id: Optional[str] = None
        if callable(vendor_resolver):
            try:
                vendor_id = vendor_resolver(need)
            except Exception:  # noqa: BLE001 - resolver failure must not kill the draft
                vendor_id = None
        if vendor_id is not None:
            vendor_id = str(vendor_id)
        if vendor_id not in by_vendor:
            by_vendor[vendor_id] = []
            order.append(vendor_id)
        by_vendor[vendor_id].append(need)

    out: Dict[Optional[str], List[Dict[str, Any]]] = {}
    for vid in order:
        lines = build_po_lines(by_vendor[vid])
        if lines:
            out[vid] = lines
    return out
