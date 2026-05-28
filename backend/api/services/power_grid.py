"""
IMS 2.0 - Lens / contact-lens power-availability grids (pure)
============================================================
Builds the SPH x CYL on-hand matrix for spectacle (stock) lenses and a
power x base-curve matrix for contact lenses. DB-free: the router fetches the
products + an {product_id: on_hand_count} map and calls these.

Power formatting is canonical optical: signed, 2-decimal, snapped to the 0.25
dioptre step ("+2.00", "-1.25", "0.00") so a product's stored power lands in the
right cell regardless of how it was typed.
"""

from typing import List, Optional


def format_power(value, signed: bool = True) -> Optional[str]:
    """Snap to 0.25 and format as a signed 2dp dioptre string. None on junk."""
    if value is None or value == "":
        return None
    try:
        v = round(float(value) * 4) / 4
    except (TypeError, ValueError):
        return None
    if v == 0:
        return "0.00"
    if signed and v > 0:
        return f"+{v:.2f}"
    return f"{v:.2f}"


def sph_range(lo: float = -8.0, hi: float = 6.0, step: float = 0.25) -> List[str]:
    """SPH row labels, most-minus to most-plus."""
    n = int(round((hi - lo) / step))
    return [format_power(lo + i * step) for i in range(n + 1)]


def cyl_range(lo: float = -4.0, hi: float = 0.0, step: float = 0.25) -> List[str]:
    """CYL column labels, 0.00 down to -4.00 (minus-cyl convention)."""
    n = int(round((hi - lo) / step))
    return [format_power(hi - i * step) for i in range(n + 1)]


def _pid(p: dict):
    return p.get("product_id") or p.get("_id")


def build_lens_grid(
    products: List[dict],
    on_hand: dict,
    sphs: Optional[List[str]] = None,
    cyls: Optional[List[str]] = None,
) -> dict:
    """SPH x CYL on-hand grid for stock spectacle lenses.

    products: [{product_id, sph, cyl, ...}]; on_hand: {product_id: count}.
    A lens with no cylinder is bucketed at CYL 0.00 (spherical). Powers outside
    the displayed range are summed into `out_of_range` instead of dropped.
    """
    sphs = sphs or sph_range()
    cyls = cyls or cyl_range()
    grid = {
        s: {c: {"count": 0, "skus": 0, "in_stock": False} for c in cyls} for s in sphs
    }
    total_units = 0
    out_of_range = 0

    for p in products or []:
        if not isinstance(p, dict):
            continue
        s = format_power(p.get("sph"))
        if s is None:
            continue
        c = format_power(p.get("cyl")) or "0.00"
        cnt = on_hand.get(_pid(p), 0)
        try:
            cnt = int(cnt or 0)
        except (TypeError, ValueError):
            cnt = 0
        if s in grid and c in grid[s]:
            cell = grid[s][c]
            cell["count"] += cnt
            cell["skus"] += 1
            cell["in_stock"] = cell["count"] > 0
            total_units += cnt
        else:
            out_of_range += cnt

    return {
        "sph_range": sphs,
        "cyl_range": cyls,
        "grid": grid,
        "total_units": total_units,
        "out_of_range_units": out_of_range,
    }


def build_cl_grid(
    products: List[dict],
    on_hand: dict,
    near_expiry: Optional[dict] = None,
) -> dict:
    """Contact-lens availability by power (rows) x base-curve (cols). Power +
    base-curve axes are dynamic (only values that exist). near_expiry:
    {product_id: bool} flags cells holding near-expiry stock."""
    near_expiry = near_expiry or {}
    cells: dict = {}
    powers: set = set()
    curves: set = set()

    for p in products or []:
        if not isinstance(p, dict):
            continue
        pw = format_power(p.get("cl_power"))
        if pw is None:
            continue
        bc = p.get("base_curve")
        try:
            bc = f"{float(bc):.1f}" if bc not in (None, "") else "--"
        except (TypeError, ValueError):
            bc = "--"
        powers.add(pw)
        curves.add(bc)
        cnt = on_hand.get(_pid(p), 0)
        try:
            cnt = int(cnt or 0)
        except (TypeError, ValueError):
            cnt = 0
        d = cells.setdefault((pw, bc), {"count": 0, "skus": 0, "near_expiry": False})
        d["count"] += cnt
        d["skus"] += 1
        if near_expiry.get(_pid(p)):
            d["near_expiry"] = True

    power_list = sorted(powers, key=lambda x: float(x))
    curve_list = sorted(curves)
    grid: dict = {}
    total_units = 0
    for pw in power_list:
        grid[pw] = {}
        for bc in curve_list:
            cell = cells.get((pw, bc), {"count": 0, "skus": 0, "near_expiry": False})
            cell["in_stock"] = cell["count"] > 0
            grid[pw][bc] = cell
            total_units += cell["count"]

    return {
        "power_range": power_list,
        "curve_range": curve_list,
        "grid": grid,
        "total_units": total_units,
    }
