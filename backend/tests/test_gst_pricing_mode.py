"""
IMS 2.0 - GST_PRICING_MODE flag tests
=====================================
The pricing model is read PER REQUEST from the GST_PRICING_MODE env var so it
can be flipped on Railway without a redeploy (instant atomic rollback). Default
"inclusive" (the counter price is all-in; tax extracted from within). This locks:
  - the gst_pricing_mode() helper (default + parse + junk-tolerance);
  - _compute_per_category_gst branches correctly per mode;
  - the order self-labels via "pricing_model" so reports/forensics never guess.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_pricing_mode_default_inclusive(monkeypatch):
    from api.services.gst_rates import gst_pricing_mode

    monkeypatch.delenv("GST_PRICING_MODE", raising=False)
    assert gst_pricing_mode() == "inclusive"


def test_pricing_mode_explicit_exclusive(monkeypatch):
    from api.services.gst_rates import gst_pricing_mode

    monkeypatch.setenv("GST_PRICING_MODE", "exclusive")
    assert gst_pricing_mode() == "exclusive"
    # case-insensitive + whitespace tolerant
    monkeypatch.setenv("GST_PRICING_MODE", "  EXCLUSIVE ")
    assert gst_pricing_mode() == "exclusive"


def test_pricing_mode_junk_falls_back_to_inclusive(monkeypatch):
    from api.services.gst_rates import gst_pricing_mode

    monkeypatch.setenv("GST_PRICING_MODE", "banana")
    assert gst_pricing_mode() == "inclusive"


def test_compute_inclusive_default(monkeypatch):
    """Default (inclusive): a Rs 1000 FRAME @5% is all-in -> taxable 952.38 +
    GST 47.62; grand (taxable+tax) = 1000; order self-labels inclusive."""
    monkeypatch.delenv("GST_PRICING_MODE", raising=False)
    from api.routers.orders import _compute_per_category_gst

    items = [{"item_total": 1000.0, "category": "FRAME"}]
    out = _compute_per_category_gst(items, 0)
    assert out["taxable"] == 952.38
    assert out["tax"] == 47.62
    assert round(out["taxable"] + out["tax"], 2) == 1000.0
    assert out["pricing_model"] == "inclusive"
    assert items[0]["taxable_value"] == 952.38
    assert items[0]["tax_amount"] == 47.62


def test_compute_exclusive_flag(monkeypatch):
    """Flag flipped to exclusive: the Rs 1000 line is pre-tax; GST added on top
    -> taxable 1000 + GST 50; grand 1050; order self-labels exclusive."""
    monkeypatch.setenv("GST_PRICING_MODE", "exclusive")
    from api.routers.orders import _compute_per_category_gst

    items = [{"item_total": 1000.0, "category": "FRAME"}]
    out = _compute_per_category_gst(items, 0)
    assert out["taxable"] == 1000.0
    assert out["tax"] == 50.0
    assert round(out["taxable"] + out["tax"], 2) == 1050.0
    assert out["pricing_model"] == "exclusive"
    assert items[0]["taxable_value"] == 1000.0
    assert items[0]["tax_amount"] == 50.0


def test_compute_exclusive_mixed_cart(monkeypatch):
    """Exclusive, mixed cart: 1000 FRAME (5%) + 2000 SUNGLASS (18%) ->
    tax = 50 + 360 = 410; grand = 3000 + 410 = 3410 (the legacy figures)."""
    monkeypatch.setenv("GST_PRICING_MODE", "exclusive")
    from api.routers.orders import _compute_per_category_gst

    out = _compute_per_category_gst(
        [
            {"item_total": 1000.0, "category": "FRAME"},
            {"item_total": 2000.0, "category": "SUNGLASS"},
        ],
        0,
    )
    assert out["taxable"] == 3000.0
    assert out["tax"] == 410.0
    assert round(out["taxable"] + out["tax"], 2) == 3410.0
