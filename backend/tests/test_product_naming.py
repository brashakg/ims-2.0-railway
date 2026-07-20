"""Unit tests for the pure product_naming SEO builders.

Deterministic, DB-free. Covers the eyewear name shape, graceful degradation of
missing fields, title-casing that preserves model numbers, colour-name vs
colour-code selection, gender qualifiers, the <=70 char clamp, handle slugging,
and the create-door name-minting integration.
"""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services.product_naming import (  # noqa: E402
    MAX_NAME_LEN,
    build_handle,
    build_product_name,
    build_seo_description,
    build_seo_title,
    needs_name,
    resolve_category_word,
)


# ---------------------------------------------------------------------------
# Canonical happy path
# ---------------------------------------------------------------------------


def test_sunglass_full_shape_colour():
    doc = {
        "category": "SUNGLASS",
        "attributes": {
            "brand_name": "Ray-Ban",
            "model_no": "RB3025",
            "shape": "Aviator",
            "frame_color": "Polished Gold",
        },
    }
    assert build_product_name(doc) == "Ray-Ban RB3025 Aviator Sunglasses - Polished Gold"


def test_frame_gender_qualifier():
    doc = {
        "category": "FRAME",
        "attributes": {
            "brand_name": "Vogue",
            "model_no": "VO5239",
            "shape": "Cat Eye",
            "gender": "Women",
            "frame_color": "Tortoise",
        },
    }
    # FRAME -> "Eyeglasses", Women -> "Women's".
    assert build_product_name(doc) == "Vogue VO5239 Cat Eye Women's Eyeglasses - Tortoise"


def test_model_number_case_preserved():
    # A digit-bearing token must survive title-casing verbatim (RB3025, OO9208).
    doc = {
        "category": "SUNGLASS",
        "attributes": {"brand_name": "oakley", "model_no": "OO9208", "frame_color": "black"},
    }
    assert build_product_name(doc) == "Oakley OO9208 Sunglasses - Black"


def test_contact_lens_no_colour():
    doc = {
        "category": "CONTACT_LENS",
        "attributes": {"brand_name": "Acuvue", "model_name": "Oasys"},
    }
    assert build_product_name(doc) == "Acuvue Oasys Contact Lenses"


def test_coloured_contact_lens_colour_name():
    doc = {
        "category": "COLORED_CONTACT_LENS",
        "attributes": {
            "brand_name": "Freshlook",
            "model_name": "Colorblends",
            "colour_name": "Green",
        },
    }
    assert build_product_name(doc) == "Freshlook Colorblends Colour Contact Lenses - Green"


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


def test_missing_shape_drops_out():
    doc = {
        "category": "SUNGLASS",
        "attributes": {"brand_name": "Oakley", "model_no": "OO9208", "frame_color": "Black"},
    }
    assert build_product_name(doc) == "Oakley OO9208 Sunglasses - Black"


def test_missing_colour_drops_separator():
    doc = {
        "category": "FRAME",
        "attributes": {"brand_name": "Burberry", "model_no": "B 3142"},
    }
    name = build_product_name(doc)
    assert name == "Burberry B 3142 Eyeglasses"
    assert " - " not in name


def test_bare_code_colour_is_skipped():
    # colour_code "1109/71" is an opaque code, not a shopper-facing colour.
    doc = {
        "category": "FRAME",
        "attributes": {"brand_name": "Burberry", "model_no": "B 3142", "colour_code": "1109/71"},
    }
    assert build_product_name(doc) == "Burberry B 3142 Eyeglasses"


def test_colour_name_preferred_over_code():
    doc = {
        "category": "FRAME",
        "attributes": {
            "brand_name": "Titan",
            "model_no": "T123",
            "frame_color": "Matte Black",
            "colour_code": "071",
        },
    }
    assert build_product_name(doc) == "Titan T123 Eyeglasses - Matte Black"


def test_model_name_equal_shape_deduped():
    doc = {
        "category": "SUNGLASS",
        "attributes": {"brand_name": "Ray-Ban", "model_name": "Aviator", "shape": "Aviator"},
    }
    # "Aviator" appears once, not twice.
    assert build_product_name(doc) == "Ray-Ban Aviator Sunglasses"


def test_unisex_gender_omitted():
    doc = {
        "category": "SUNGLASS",
        "attributes": {"brand_name": "Ray-Ban", "model_no": "RB2140", "gender": "Unisex"},
    }
    assert build_product_name(doc) == "Ray-Ban RB2140 Sunglasses"


def test_empty_dict_returns_blank():
    assert build_product_name({}) == ""
    assert build_product_name(None) == ""  # type: ignore[arg-type]


def test_only_category_degrades_to_category_word():
    assert build_product_name({"category": "SUNGLASS"}) == "Sunglasses"


# ---------------------------------------------------------------------------
# Explicit name (SERVICES) wins
# ---------------------------------------------------------------------------


def test_explicit_service_name_wins():
    doc = {"category": "SERVICES", "attributes": {"name": "Comprehensive Eye Test"}}
    assert build_product_name(doc) == "Comprehensive Eye Test"


def test_top_level_name_wins_verbatim():
    doc = {
        "name": "Special Edition Frame",
        "category": "FRAME",
        "attributes": {"brand_name": "Ray-Ban", "model_no": "RB1"},
    }
    assert build_product_name(doc) == "Special Edition Frame"


# ---------------------------------------------------------------------------
# Determinism, length clamp, casing
# ---------------------------------------------------------------------------


def test_deterministic():
    doc = {
        "category": "SUNGLASS",
        "attributes": {"brand_name": "Ray-Ban", "model_no": "RB3025", "frame_color": "Gold"},
    }
    assert build_product_name(doc) == build_product_name(dict(doc))


def test_length_clamped_no_trailing_separator():
    doc = {
        "category": "SUNGLASS",
        "attributes": {
            "brand_name": "Superlongbrandname International Optical House",
            "model_no": "ModelXYZ Very Long Designation Edition",
            "shape": "Rectangular",
            "frame_color": "Metallic Rose Gold Gradient",
        },
    }
    name = build_product_name(doc)
    assert len(name) <= MAX_NAME_LEN
    assert not name.endswith("-")
    assert not name.endswith(" ")
    assert "  " not in name  # no double spaces


def test_no_double_spaces_when_fields_blank():
    doc = {
        "category": "FRAME",
        "attributes": {"brand_name": "  Ray-Ban  ", "model_no": "", "frame_color": "Black"},
    }
    name = build_product_name(doc)
    assert "  " not in name
    assert name == "Ray-Ban Eyeglasses - Black"


# ---------------------------------------------------------------------------
# SEO title / handle / description
# ---------------------------------------------------------------------------


def test_seo_title_equals_product_name():
    doc = {
        "category": "SUNGLASS",
        "attributes": {"brand_name": "Ray-Ban", "model_no": "RB3025", "frame_color": "Gold"},
    }
    assert build_seo_title(doc) == build_product_name(doc)


def test_handle_is_slug():
    doc = {
        "category": "SUNGLASS",
        "attributes": {
            "brand_name": "Ray-Ban",
            "model_no": "RB3025",
            "shape": "Aviator",
            "frame_color": "Polished Gold",
        },
    }
    assert build_handle(doc) == "ray-ban-rb3025-aviator-sunglasses-polished-gold"


def test_handle_no_double_hyphen_or_edges():
    doc = {"category": "FRAME", "attributes": {"brand_name": "Ray-Ban", "model_no": "B 3142"}}
    handle = build_handle(doc)
    assert "--" not in handle
    assert not handle.startswith("-") and not handle.endswith("-")
    assert handle == "ray-ban-b-3142-eyeglasses"


def test_handle_falls_back_to_sku():
    doc = {"sku": "SG-RAYBAN-RB3025"}
    # No identity/category -> name blank -> handle from sku.
    assert build_handle(doc) == "sg-rayban-rb3025"


def test_seo_description_placeholder():
    doc = {
        "category": "SUNGLASS",
        "attributes": {"brand_name": "Ray-Ban", "model_no": "RB3025", "frame_color": "Gold"},
    }
    desc = build_seo_description(doc)
    assert desc.startswith("Buy Ray-Ban RB3025")
    assert "Ray-Ban" in desc
    assert len(desc) <= 160


def test_seo_description_blank_for_empty():
    assert build_seo_description({}) == ""


# ---------------------------------------------------------------------------
# resolve_category_word + needs_name
# ---------------------------------------------------------------------------


def test_category_word_short_codes_and_aliases():
    assert resolve_category_word("SG") == "Sunglasses"
    assert resolve_category_word("SUNGLASSES") == "Sunglasses"
    assert resolve_category_word("FR") == "Eyeglasses"
    assert resolve_category_word("CCL") == "Colour Contact Lenses"
    assert resolve_category_word("HEARING_AID") == "Hearing Aid"
    assert resolve_category_word("") == ""


def test_category_word_unknown_titlecased():
    assert resolve_category_word("MYSTERY_GADGET") == "Mystery Gadget"


def test_needs_name():
    assert needs_name({}) is True
    assert needs_name({"name": ""}) is True
    assert needs_name({"name": "   "}) is True
    assert needs_name({"name": "Ray-Ban RB3025 Sunglasses"}) is False
