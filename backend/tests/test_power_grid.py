"""Unit tests for services/power_grid.py (pure lens/CL availability grids)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.power_grid import (  # noqa: E402
    format_power, sph_range, cyl_range, build_lens_grid, build_cl_grid,
)


def test_format_power():
    assert format_power(0) == "0.00"
    assert format_power(-1.25) == "-1.25"
    assert format_power(2) == "+2.00"
    assert format_power(-8.0) == "-8.00"
    assert format_power(0.13) == "+0.25"   # snaps to 0.25 step
    assert format_power(None) is None
    assert format_power("") is None
    assert format_power("x") is None


def test_ranges():
    sr = sph_range()
    assert sr[0] == "-8.00" and sr[-1] == "+6.00"
    assert "0.00" in sr
    cr = cyl_range()
    assert cr[0] == "0.00" and cr[-1] == "-4.00"


def test_build_lens_grid_counts():
    products = [
        {"product_id": "p1", "sph": -2.0, "cyl": -0.5},
        {"product_id": "p2", "sph": -2.0, "cyl": -0.5},   # same cell, different SKU
        {"product_id": "p3", "sph": 1.0, "cyl": 0},        # plano cyl -> 0.00
    ]
    on_hand = {"p1": 5, "p2": 3, "p3": 10}
    g = build_lens_grid(products, on_hand)
    assert g["grid"]["-2.00"]["-0.50"]["count"] == 8
    assert g["grid"]["-2.00"]["-0.50"]["skus"] == 2
    assert g["grid"]["-2.00"]["-0.50"]["in_stock"] is True
    assert g["grid"]["+1.00"]["0.00"]["count"] == 10
    assert g["total_units"] == 18


def test_build_lens_grid_out_of_range():
    # +9.00 is beyond the default +6.00 ceiling -> counted as out_of_range.
    g = build_lens_grid([{"product_id": "x", "sph": 9.0, "cyl": 0}], {"x": 4})
    assert g["total_units"] == 0
    assert g["out_of_range_units"] == 4


def test_build_lens_grid_skips_no_sph():
    g = build_lens_grid([{"product_id": "x", "cyl": -1.0}], {"x": 4})
    assert g["total_units"] == 0


def test_build_cl_grid_dynamic_axes():
    products = [
        {"product_id": "c1", "cl_power": -3.0, "base_curve": 8.6},
        {"product_id": "c2", "cl_power": -3.0, "base_curve": 8.4},
        {"product_id": "c3", "cl_power": -1.0, "base_curve": 8.6},
    ]
    on_hand = {"c1": 6, "c2": 2, "c3": 9}
    near = {"c1": True}
    g = build_cl_grid(products, on_hand, near)
    assert g["power_range"] == ["-3.00", "-1.00"]
    assert "8.6" in g["curve_range"] and "8.4" in g["curve_range"]
    assert g["grid"]["-3.00"]["8.6"]["count"] == 6
    assert g["grid"]["-3.00"]["8.6"]["near_expiry"] is True
    assert g["grid"]["-1.00"]["8.6"]["count"] == 9
    assert g["grid"]["-1.00"]["8.4"]["count"] == 0  # absent combo -> empty cell
    assert g["total_units"] == 17
