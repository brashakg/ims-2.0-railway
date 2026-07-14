"""
Tests for the BVI attribute -> Shopify tag generator (api/services/shopify_tag_gen).

The IMS -> Shopify push must reproduce the exact `<prefix>_<value>` filter tags
that the BVI admin app auto-generates, so flipping IMS writes LIVE (a
productUpdate REPLACES the whole tags array) does not wipe the storefront's
facets. These tests pin the tokens for the main categories against the live
Shopify vocabulary convention (lower-case prefix, slugified value), the
fail-soft contract, and the build_product_input merge.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_shopify_tag_gen.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services.shopify_tag_gen import (  # noqa: E402
    slugify_tag_value,
    generate_attribute_tags,
    merge_tag_lists,
)
from api.services import shopify_push  # noqa: E402


# ===========================================================================
# slugify_tag_value -- byte-for-byte parity with BVI slugifyTagValue()
# ===========================================================================

def test_slugify_matches_shopify_convention():
    assert slugify_tag_value("Ray-Ban") == "ray-ban"
    assert slugify_tag_value("Full Frame") == "full-frame"
    assert slugify_tag_value("1109/71") == "1109-71"        # colour code separators
    assert slugify_tag_value("UV 400") == "uv-400"
    assert slugify_tag_value("Rose Gold") == "rose-gold"
    # booleans -> yes/no (BVI tagsForProductAttributes)
    assert slugify_tag_value(True) == "yes"
    assert slugify_tag_value(False) == "no"
    # numbers stringify
    assert slugify_tag_value(58) == "58"
    # blank / None never yield a dangling "prefix_" token
    assert slugify_tag_value(None) == ""
    assert slugify_tag_value("   ") == ""
    assert slugify_tag_value(" -- ") == ""


# ===========================================================================
# FRAME -- the core eyewear tokens the task calls out explicitly
# ===========================================================================

def test_frame_emits_prefixed_attribute_tags():
    attrs = {
        "brand_name": "Ray-Ban",
        "model_no": "RB3025",
        "colour_code": "1109/71",
        "gender": "Men",
        "shape": "Round",
        "frame_type": "Full Frame",
        "frame_material": "Acetate",
        "temple_material": "Metal",
        "frame_color": "Havana",
        "temple_color": "Gold",
        "lens_size": 58,
        "bridge_width": 14,
        "temple_length": 140,
    }
    tags = generate_attribute_tags("FRAME", attrs)
    # The exact tokens the storefront filters on (matches live Shopify vocab).
    for expected in [
        "brand_ray-ban",
        "modelno_rb3025",
        "gender_men",
        "shape_round",
        "frametype_full-frame",
        "framematerial_acetate",
        "templematerial_metal",
        "framecolor_havana",
        "templecolor_gold",
        "colorcode_1109-71",
        "framesize_58",
        "bridge_14",
        "templelength_140",
    ]:
        assert expected in tags, f"missing {expected}"
    # No dangling category_ / subbrand_ / origin_ tokens (parity with BVI).
    assert not any(t.startswith("category_") for t in tags)
    assert not any(t.startswith("subbrand_") for t in tags)
    assert not any(t.startswith("origin_") for t in tags)
    # Deterministic + de-duped.
    assert len(tags) == len(set(tags))


# ===========================================================================
# SUNGLASS -- adds the lens split (lens/tint/polarization/uv/material)
# ===========================================================================

def test_sunglass_emits_lens_tags():
    attrs = {
        "brand_name": "Ray-Ban",
        "shape": "Aviator",
        "lens_colour": "Green",
        "tint": "Gradient",
        "polarization": "Polarized",
        "uv_protection": "UV400",
        "lens_material": "Polycarbonate",
    }
    tags = generate_attribute_tags("SUNGLASS", attrs)
    for expected in [
        "brand_ray-ban",
        "shape_aviator",
        "lenscolour_green",
        "tint_gradient",
        "polarization_polarized",
        "uvprotection_uv400",
        "lensmaterial_polycarbonate",
    ]:
        assert expected in tags, f"missing {expected}"


# ===========================================================================
# WATCH -- dial / strap / case / movement tokens
# ===========================================================================

def test_watch_emits_watch_tags():
    attrs = {
        "brand_name": "Timex",
        "gender": "Women",
        "dial_color": "Blue",
        "strap_color": "Rose Gold",
        "strap_material": "Metal",
        "case_color": "Rose Gold",
        "case_material": "Metal",
        "movement": "Quartz",
        "movement_type": "Analog",
    }
    tags = generate_attribute_tags("WATCH", attrs)
    for expected in [
        "brand_timex",
        "gender_women",
        "dialcolor_blue",
        "strapcolor_rose-gold",
        "strapmaterial_metal",
        "casecolor_rose-gold",
        "casematerial_metal",
        "movement_quartz",
        "movementtype_analog",
    ]:
        assert expected in tags, f"missing {expected}"


# ===========================================================================
# Aliases resolve -- short codes / long alternates map to the same registry
# ===========================================================================

def test_category_aliases_resolve():
    # "SG" short code and "SUNGLASSES" long alternate both -> SUNGLASS registry.
    for cat in ("SG", "SUNGLASSES", "sunglass"):
        tags = generate_attribute_tags(cat, {"tint": "Solid"})
        assert "tint_solid" in tags


# ===========================================================================
# Fail-soft -- empty / unknown never crash; unknown -> empty list
# ===========================================================================

def test_empty_and_unknown_are_safe():
    assert generate_attribute_tags("FRAME", {}) == []
    assert generate_attribute_tags("FRAME", None) == []
    assert generate_attribute_tags(None, None) == []
    assert generate_attribute_tags("TOTALLY_UNKNOWN", {"brand_name": "X"}) == []
    # blank / whitespace / None values are skipped, not emitted as "prefix_"
    assert generate_attribute_tags("FRAME", {"gender": "   ", "shape": None}) == []
    # an unmapped attribute on a known category is skipped silently
    assert generate_attribute_tags("FRAME", {"not_a_real_attr": "value"}) == []


def test_extras_override_fills_top_level_brand():
    # No brand_name in attributes; extras injects the top-level product.brand.
    tags = generate_attribute_tags("FRAME", {}, {"brand_name": "Ray-Ban"})
    assert tags == ["brand_ray-ban"]
    # blank extras are ignored (never override a real value with "")
    tags2 = generate_attribute_tags("FRAME", {"brand_name": "Titan"}, {"brand_name": "   "})
    assert tags2 == ["brand_titan"]


# ===========================================================================
# merge_tag_lists + build_product_input -- union without dupes, lower-cased
# ===========================================================================

def test_merge_tag_lists_dedupes_and_lowercases():
    merged = merge_tag_lists(["New", "shape_round"], ["brand_ray-ban", "shape_round"])
    # existing first (lower-cased), then generated not already present; no dup.
    assert merged == ["new", "shape_round", "brand_ray-ban"]
    # blanks / None dropped
    assert merge_tag_lists(["", None, "  "], ["gender_men"]) == ["gender_men"]


def test_build_product_input_merges_seo_and_generated_tags():
    product = {
        "id": "P1",
        "title": "RB Aviator",
        "brand": "Ray-Ban",
        "category": "SUNGLASS",
        "attributes": {"shape": "Round", "frame_color": "Havana"},
        "ecom": {
            "status": "PUBLISHED",
            "seo": {"tags": ["New", "shape_round"]},  # one overlaps a generated tag
        },
    }
    inp = shopify_push.build_product_input(product, [])
    tags = inp["tags"]
    # generated attribute tags are present...
    assert "brand_ray-ban" in tags          # from top-level brand via extras
    assert "shape_round" in tags
    assert "framecolor_havana" in tags
    # ...the manual browse tag survives (lower-cased)...
    assert "new" in tags
    # ...and the overlap is de-duped (shape_round exactly once).
    assert tags.count("shape_round") == 1
    assert len(tags) == len(set(tags))


def test_build_product_input_no_attributes_still_ok():
    # A product with no attributes / unknown category: tags fall back to just
    # the manual seo.tags (lower-cased), and nothing crashes.
    product = {
        "id": "P2",
        "title": "Mystery",
        "category": "SERVICES",
        "ecom": {"status": "DRAFT", "seo": {"tags": ["Featured"]}},
    }
    inp = shopify_push.build_product_input(product, [])
    assert inp["tags"] == ["featured"]
