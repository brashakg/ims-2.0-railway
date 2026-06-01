"""
Tests for the e-commerce category map (BVI Phase 1 foundation).

Pure-function bidirectional mapping IMS <-> BVI <-> Shopify productType, with a
fail-soft passthrough contract for unknown values. No DB / app import needed.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_ecom_category_map.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from api.services import ecom_category_map as m  # noqa: E402


# A representative slice of the known mappings: (ims, bvi, shopify).
KNOWN = [
    ("FRAME", "SPECTACLES", "Eyeglasses"),
    ("SUNGLASS", "SUNGLASSES", "Sunglasses"),
    ("CONTACT_LENS", "CONTACT_LENSES", "Contact Lenses"),
    ("READING_GLASSES", "READING_GLASSES", "Reading Glasses"),
    ("WATCH", "WATCHES", "Watches"),
]


@pytest.mark.parametrize("ims,bvi,shop", KNOWN)
def test_ims_bvi_roundtrip(ims, bvi, shop):
    assert m.ims_to_bvi(ims) == bvi
    assert m.bvi_to_ims(bvi) == ims


@pytest.mark.parametrize("ims,bvi,shop", KNOWN)
def test_ims_shopify_roundtrip(ims, bvi, shop):
    assert m.ims_to_shopify_type(ims) == shop
    assert m.shopify_type_to_ims(shop) == ims


@pytest.mark.parametrize("ims,bvi,shop", KNOWN)
def test_bvi_shopify_roundtrip(ims, bvi, shop):
    assert m.bvi_to_shopify_type(bvi) == shop
    assert m.shopify_type_to_bvi(shop) == bvi


def test_full_triangle_roundtrip():
    """IMS -> BVI -> Shopify -> back to IMS lands where it started for a known
    category (the auto-collection lineage relies on this closing)."""
    ims = "FRAME"
    bvi = m.ims_to_bvi(ims)
    shop = m.bvi_to_shopify_type(bvi)
    assert m.shopify_type_to_ims(shop) == ims


def test_shopify_lookup_is_case_insensitive():
    assert m.shopify_type_to_ims("eyeglasses") == "FRAME"
    assert m.shopify_type_to_ims("EYEGLASSES") == "FRAME"
    assert m.shopify_type_to_bvi("  sunglasses  ") == "SUNGLASSES"


def test_input_normalisation_for_enum_keys():
    """IMS/BVI keys normalise (trim, upper, hyphen/space -> underscore)."""
    assert m.ims_to_bvi("frame") == "SPECTACLES"
    assert m.ims_to_bvi("  Frame  ") == "SPECTACLES"
    assert m.bvi_to_ims("contact-lenses") == "CONTACT_LENS"


def test_unknown_passes_through_failsoft():
    """The locked fail-soft contract: an unmapped value flows through unchanged
    (normalised for enum keys; original trimmed string for Shopify) rather than
    raising or being dropped."""
    assert m.ims_to_bvi("GADGET") == "GADGET"
    assert m.ims_to_shopify_type("GADGET") == "GADGET"
    assert m.bvi_to_ims("MYSTERY") == "MYSTERY"
    assert m.shopify_type_to_ims("Telescopes") == "Telescopes"
    assert m.shopify_type_to_bvi("Telescopes") == "Telescopes"


def test_empty_and_none_are_safe():
    assert m.ims_to_bvi(None) == ""
    assert m.ims_to_bvi("") == ""
    assert m.shopify_type_to_ims(None) == ""


def test_is_known_helpers():
    assert m.is_known_ims("FRAME") is True
    assert m.is_known_ims("frame") is True
    assert m.is_known_ims("GADGET") is False
    assert m.is_known_bvi("SPECTACLES") is True
    assert m.is_known_bvi("MYSTERY") is False
    assert m.is_known_shopify_type("Eyeglasses") is True
    assert m.is_known_shopify_type("Telescopes") is False


def test_solutions_maps_to_accessories():
    """BVI SOLUTIONS (CL care fluids) files under IMS ACCESSORIES; the IMS->BVI
    representative for ACCESSORIES is the earlier, more specific row."""
    assert m.bvi_to_ims("SOLUTIONS") == "ACCESSORIES"
    assert m.ims_to_bvi("ACCESSORIES") == "ACCESSORIES"  # first row wins


def test_all_mappings_returns_copy():
    table = m.all_mappings()
    assert isinstance(table, list) and table
    # Mutating the returned copy must not corrupt the module table.
    table.append({"ims": "X", "bvi": "Y", "shopify": "Z"})
    assert m.is_known_ims("X") is False
