"""
IMS 2.0 — GST rate + HSN canonical-table tests (TAX-CRITICAL)
=============================================================

Locks in the Indian GST 2.0 rates (effective 22 Sep 2025) for every
optical-retail product category, and guarantees that:

  master rate (products.py create-path)  ==  billed rate (orders.py)

for the SAME category. The single source of truth is
api/services/gst_rates.py; orders.py and products.py both read it.

Rates verified against the 56th GST Council press release (3 Sep 2025),
Annexure-I:
  9001 Contact lenses; Spectacle lenses ...... 12% -> 5%   (Sr. 351)
  9003 Frames and mountings for spectacles ... 12% -> 5%   (Sr. 352)
  9004 Spectacles, corrective ................ 12% -> 5%   (Sr. 353)
Non-corrective sunglasses (9004) stay at 18%. Watches 9101/9102 and
smartwatches stay at 18%. Hearing aids 9021 (complete) are NIL/exempt.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# Canonical table — gst_rate_for_category
# ============================================================================

# (category, expected_rate). Covers the schema enum, seed plural forms, and
# the short UI codes used at product create.
EXPECTED_RATES = [
    # --- 5% : frames / spectacle & contact lenses / corrective spectacles ---
    ("FRAME", 5.0),
    ("FRAMES", 5.0),
    ("FR", 5.0),
    ("OPTICAL_LENS", 5.0),
    ("RX_LENSES", 5.0),
    ("LENS", 5.0),
    ("LS", 5.0),
    ("READING_GLASSES", 5.0),
    ("RG", 5.0),
    ("CONTACT_LENS", 5.0),
    ("CONTACT_LENSES", 5.0),
    ("COLORED_CONTACT_LENS", 5.0),
    ("COLOUR_CONTACTS", 5.0),
    ("CL", 5.0),
    ("CCL", 5.0),
    ("SPECTACLE", 5.0),
    ("COMPLETE_SPECTACLE", 5.0),
    # --- 18% : sunglasses / watches / smartwatches / clocks / accessories ---
    ("SUNGLASS", 18.0),
    ("SUNGLASSES", 18.0),
    ("SG", 18.0),
    ("WATCH", 18.0),
    ("WRIST_WATCHES", 18.0),
    ("WT", 18.0),
    ("SMARTWATCH", 18.0),
    ("SMARTWATCHES", 18.0),
    ("SMTWT", 18.0),
    ("WALL_CLOCK", 18.0),
    ("WALL_CLOCKS", 18.0),
    ("CK", 18.0),
    ("ACCESSORIES", 18.0),
    ("ACCESSORY", 18.0),
    ("ACC", 18.0),
    ("SERVICES", 18.0),
    ("SERVICE", 18.0),
    # --- 18% : smart eyewear (GST-REVIEW placeholder) ---
    ("SMARTGLASSES", 18.0),
    ("SMTSG", 18.0),
    ("SMTFR", 18.0),
    # --- NIL/exempt : hearing aids (complete devices, HSN 9021) ---
    ("HEARING_AID", 0.0),
    ("HEARING_AIDS", 0.0),
    ("HA", 0.0),
]


@pytest.mark.parametrize("category,expected", EXPECTED_RATES)
def test_gst_rate_for_category(category, expected):
    from api.services.gst_rates import gst_rate_for_category

    assert gst_rate_for_category(category) == expected


def test_gst_rate_is_case_insensitive():
    from api.services.gst_rates import gst_rate_for_category

    assert gst_rate_for_category("frame") == 5.0
    assert gst_rate_for_category("Sunglass") == 18.0
    assert gst_rate_for_category("  contact_lens  ") == 5.0


def test_gst_rate_unknown_defaults_to_5():
    # Optical-dominant fallback (changed 18% -> 5% on 2026-05-28 after QA found
    # an uncategorized product billed at 18%). The real guard is the block-save
    # in routers/products.py; this fallback just biases the unknown case to the
    # dominant optical rate. See gst_rates.DEFAULT_GST_RATE.
    from api.services.gst_rates import gst_rate_for_category, DEFAULT_GST_RATE

    assert DEFAULT_GST_RATE == 5.0
    assert gst_rate_for_category("WIDGETS_THAT_DO_NOT_EXIST") == 5.0
    assert gst_rate_for_category("") == 5.0
    assert gst_rate_for_category(None) == 5.0  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "category,expected_hsn",
    [
        ("FRAME", "900311"),
        ("OPTICAL_LENS", "900150"),
        ("READING_GLASSES", "900490"),
        ("CONTACT_LENS", "900130"),
        ("COLORED_CONTACT_LENS", "900130"),
        ("SUNGLASS", "900410"),
        ("WATCH", "910111"),
        ("SMARTWATCH", "910221"),
        ("WALL_CLOCK", "910500"),
        ("HEARING_AID", "902140"),
        ("ACCESSORIES", "392690"),
        ("SERVICES", "998599"),
    ],
)
def test_hsn_for_category(category, expected_hsn):
    from api.services.gst_rates import hsn_for_category

    assert hsn_for_category(category) == expected_hsn


def test_hsn_unknown_is_none():
    from api.services.gst_rates import hsn_for_category

    assert hsn_for_category("NOPE") is None


# ============================================================================
# Billing engine (orders.py) reads the canonical table
# ============================================================================


# These three were the live billing BUG before this change: the canonical
# product/order enum values OPTICAL_LENS / READING_GLASSES / COLORED_CONTACT_LENS
# were NOT in the old LOW_GST_CATEGORIES set, so POS billed them at 18% instead
# of the correct 5%.
@pytest.mark.parametrize(
    "category,expected",
    [
        ("OPTICAL_LENS", 5.0),
        ("READING_GLASSES", 5.0),
        ("COLORED_CONTACT_LENS", 5.0),
        ("FRAME", 5.0),
        ("CONTACT_LENS", 5.0),
        ("SUNGLASS", 18.0),
        ("WATCH", 18.0),
    ],
)
def test_orders_gst_rate_for_category_matches_table(category, expected):
    from api.routers.orders import _gst_rate_for_category

    assert _gst_rate_for_category(category) == expected


def test_orders_low_gst_set_contains_the_optical_categories():
    """The 5% set must include the canonical optical enum values."""
    from api.routers.orders import LOW_GST_CATEGORIES

    for cat in (
        "FRAME",
        "OPTICAL_LENS",
        "READING_GLASSES",
        "CONTACT_LENS",
        "COLORED_CONTACT_LENS",
        "LENS",
    ):
        assert cat in LOW_GST_CATEGORIES
    # and must NOT include the 18% ones
    for cat in ("SUNGLASS", "WATCH", "SMARTWATCH", "ACCESSORIES"):
        assert cat not in LOW_GST_CATEGORIES


def test_billing_uses_canonical_rate_in_compute():
    """End-to-end: an OPTICAL_LENS line bills at 5% (was 18% before the fix).
    GST-INCLUSIVE: the Rs 1000 is all-in -> taxable 952.38 + GST 47.62 extracted."""
    from api.routers.orders import _compute_per_category_gst

    items = [{"item_total": 1000.0, "category": "OPTICAL_LENS"}]
    out = _compute_per_category_gst(items, 0)
    assert items[0]["gst_rate"] == 5.0
    assert items[0]["tax_amount"] == 47.62
    assert items[0]["taxable_value"] == 952.38
    assert out["tax"] == 47.62


# ============================================================================
# Master == billing : products.py create-path defaults match orders.py
# ============================================================================


def test_products_cl_default_constant_matches_billing():
    """The CL default constant in products.py equals the billed CL rate."""
    from api.routers.products import CL_GST_DEFAULT, CL_HSN_DEFAULT
    from api.routers.orders import _gst_rate_for_category

    assert CL_GST_DEFAULT == _gst_rate_for_category("CONTACT_LENS") == 5.0
    assert CL_HSN_DEFAULT == "90013000"


def test_products_default_helpers_match_billing_for_every_enum_category():
    """For each product-master category enum value, the rate products.py would
    stamp at create == the rate orders.py would bill. This is the master==billing
    guarantee the whole exercise is about."""
    from api.services.gst_rates import gst_rate_for_category
    from api.routers.orders import _gst_rate_for_category

    # schemas.py PRODUCT_SCHEMA.category enum
    product_categories = [
        "FRAME",
        "SUNGLASS",
        "READING_GLASSES",
        "OPTICAL_LENS",
        "CONTACT_LENS",
        "COLORED_CONTACT_LENS",
        "WATCH",
        "SMARTWATCH",
        "SMARTGLASSES",
        "WALL_CLOCK",
        "ACCESSORIES",
        "SERVICES",
    ]
    for cat in product_categories:
        master_rate = gst_rate_for_category(cat)  # products.py create default
        billed_rate = _gst_rate_for_category(cat)  # orders.py billing
        assert master_rate == billed_rate, (
            f"master/billing GST mismatch for {cat}: "
            f"master={master_rate} billed={billed_rate}"
        )
