"""
IMS 2.0 - Lens catalog payload validation tests (Branch B' sub-PR 1)
====================================================================
Pure unit tests for backend/api/services/lens_catalog_validation.py. No
DB, no FastAPI -- exercises the enum-membership check, range validation,
add-nullness for SV vs bifocal, slug round-trip, bulk-import shape,
compute_available math, and the enum-config validator.

Mirrors test_fixture_validation.py's style.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import lens_catalog_validation as lcv  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enum_config(brands=None, series=None):
    """Build an enum_config dict for the tests. Defaults populate every
    enum_type so the validator never fails on a missing key."""
    return {
        "coatings": ["ANTI_BLUE", "GREEN_COAT", "DUAL_COAT", "HC"],
        "brands": brands if brands is not None else ["Essilor", "Zeiss"],
        "series": series if series is not None else [],
        "indexes": [1.50, 1.56, 1.60, 1.67, 1.74],
        "materials": ["CR39", "POLY", "MR8"],
        "lens_types": ["SV", "BIFOCAL", "PROGRESSIVE"],
    }


def _base_line(**overrides):
    base = {
        "brand": "Essilor",
        "series": "Crizal",
        "index": 1.60,
        "material": "MR8",
        "lens_type": "SV",
        "coating": "ANTI_BLUE",
        "mrp": 4500.0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Slug
# ---------------------------------------------------------------------------


def test_slug_round_trip_basic():
    slug = lcv.slugify_lens_line(
        "Essilor", "Crizal Forte", 1.60, "MR8", "SV", "ANTI_BLUE"
    )
    assert slug == "essilor-crizal-forte-1p60-mr8-sv-anti-blue"


def test_slug_normalises_case_and_diacritics():
    # 'ESSILOR' / 'Essilor' / 'essilor' should all slug the same; same
    # for 'Crizàl' vs 'Crizal'.
    a = lcv.slugify_lens_line(
        "ESSILOR", "Crizal", 1.60, "MR8", "SV", "ANTI_BLUE"
    )
    b = lcv.slugify_lens_line(
        "essilor", "crizal", 1.60, "MR8", "SV", "ANTI_BLUE"
    )
    assert a == b


def test_slug_empty_component_raises():
    with pytest.raises(ValueError) as exc:
        lcv.slugify_lens_line("", "Crizal", 1.60, "MR8", "SV", "ANTI_BLUE")
    assert "brand" in str(exc.value).lower()


def test_slug_index_two_decimal_places():
    # 1.6 and 1.60 collapse; 1.67 stays distinct.
    a = lcv.slugify_lens_line(
        "Brand", "Series", 1.6, "MR8", "SV", "ANTI_BLUE"
    )
    b = lcv.slugify_lens_line(
        "Brand", "Series", 1.60, "MR8", "SV", "ANTI_BLUE"
    )
    c = lcv.slugify_lens_line(
        "Brand", "Series", 1.67, "MR8", "SV", "ANTI_BLUE"
    )
    assert a == b
    assert a != c


# ---------------------------------------------------------------------------
# lens_catalog enum membership
# ---------------------------------------------------------------------------


def test_catalog_happy_path():
    out = lcv.validate_lens_catalog_payload(
        _base_line(), enum_config=_enum_config()
    )
    assert out["brand"] == "Essilor"
    assert out["coating"] == "ANTI_BLUE"
    assert out["index"] == 1.60
    # SV auto-derives has_add=False + add_range=None.
    assert out["has_add"] is False
    assert out["add_range"] is None
    # Defaults landed.
    assert out["gst_rate"] == 5.0
    assert out["hsn_code"] == "9001"
    assert out["is_active"] is True


def test_catalog_coating_not_in_enum_rejected():
    payload = _base_line(coating="CHROMATIC")  # not in enum_config
    with pytest.raises(ValueError) as exc:
        lcv.validate_lens_catalog_payload(payload, enum_config=_enum_config())
    assert "coating" in str(exc.value).lower()


def test_catalog_brand_not_in_enum_rejected():
    payload = _base_line(brand="Hoya")  # not in default _enum_config brands
    with pytest.raises(ValueError):
        lcv.validate_lens_catalog_payload(payload, enum_config=_enum_config())


def test_catalog_empty_brands_enum_raises_helpful():
    # When the brands list is empty, the validator should nudge the owner
    # to populate Settings instead of pretending the brand is fine.
    with pytest.raises(ValueError) as exc:
        lcv.validate_lens_catalog_payload(
            _base_line(), enum_config=_enum_config(brands=[])
        )
    assert "settings" in str(exc.value).lower()


def test_catalog_index_tolerance_match():
    # 1.6 should match the seeded 1.60.
    payload = _base_line(index=1.6)
    out = lcv.validate_lens_catalog_payload(payload, enum_config=_enum_config())
    assert out["index"] == 1.60


def test_catalog_index_not_in_enum_rejected():
    payload = _base_line(index=1.55)  # not in enum_config indexes
    with pytest.raises(ValueError):
        lcv.validate_lens_catalog_payload(payload, enum_config=_enum_config())


def test_catalog_progressive_auto_has_add_true():
    # PROGRESSIVE lens_type should auto-derive has_add=True + default
    # add_range.
    payload = _base_line(lens_type="PROGRESSIVE")
    out = lcv.validate_lens_catalog_payload(payload, enum_config=_enum_config())
    assert out["has_add"] is True
    assert out["add_range"]["min"] == 0.75
    assert out["add_range"]["max"] == 3.50


def test_catalog_sv_with_add_range_rejected():
    # SV cannot ship an add_range -- the customer would never see it.
    payload = _base_line(
        lens_type="SV",
        has_add=False,
        add_range={"min": 1.0, "max": 2.0, "step": 0.25},
    )
    with pytest.raises(ValueError) as exc:
        lcv.validate_lens_catalog_payload(payload, enum_config=_enum_config())
    assert "add_range" in str(exc.value).lower()


def test_catalog_series_brand_specific_constraint():
    # If enum_config provides a series list for the brand, validate against
    # it. Otherwise accept any non-empty string.
    cfg = _enum_config(
        series=[{"Essilor": ["Crizal", "Varilux"]}],
    )
    # Crizal is in Essilor's list -> ok.
    out = lcv.validate_lens_catalog_payload(_base_line(), enum_config=cfg)
    assert out["series"] == "Crizal"
    # Not-in-list -> 400.
    with pytest.raises(ValueError):
        lcv.validate_lens_catalog_payload(
            _base_line(series="MadeUp"), enum_config=cfg
        )


# ---------------------------------------------------------------------------
# Range validators
# ---------------------------------------------------------------------------


def test_catalog_negative_step_rejected():
    payload = _base_line(sph_range={"min": -8, "max": 6, "step": -0.25})
    with pytest.raises(ValueError) as exc:
        lcv.validate_lens_catalog_payload(payload, enum_config=_enum_config())
    assert "step" in str(exc.value).lower()


def test_catalog_min_greater_than_max_rejected():
    payload = _base_line(sph_range={"min": 5, "max": -5, "step": 0.25})
    with pytest.raises(ValueError):
        lcv.validate_lens_catalog_payload(payload, enum_config=_enum_config())


# ---------------------------------------------------------------------------
# lens_stock_lines payload
# ---------------------------------------------------------------------------


def _base_line_doc(has_add=False):
    return {
        "lens_line_id": "essilor-crizal-1p60-mr8-sv-anti-blue",
        "sph_range": {"min": -8.0, "max": 6.0, "step": 0.25},
        "cyl_range": {"min": -4.0, "max": 0.0, "step": 0.25},
        "has_add": has_add,
        "add_range": (
            {"min": 0.75, "max": 3.50, "step": 0.25} if has_add else None
        ),
    }


def test_stock_line_happy_path_sv():
    out = lcv.validate_lens_stock_line_payload(
        {
            "lens_line_id": "essilor-crizal-1p60-mr8-sv-anti-blue",
            "store_id": "STR-001",
            "sph": -2.0,
            "cyl": -1.25,
            "add": None,
            "on_hand": 5,
        },
        lens_line=_base_line_doc(),
    )
    assert out["add"] is None
    assert out["on_hand"] == 5
    assert out["reserved"] == 0


def test_stock_line_sv_with_add_rejected():
    with pytest.raises(ValueError) as exc:
        lcv.validate_lens_stock_line_payload(
            {
                "lens_line_id": "x",
                "store_id": "STR-001",
                "sph": -2,
                "cyl": 0,
                "add": 1.5,  # add on an SV line
                "on_hand": 3,
            },
            lens_line=_base_line_doc(has_add=False),
        )
    assert "single-vision" in str(exc.value).lower()


def test_stock_line_multifocal_without_add_rejected():
    with pytest.raises(ValueError) as exc:
        lcv.validate_lens_stock_line_payload(
            {
                "lens_line_id": "x",
                "store_id": "STR-001",
                "sph": -2,
                "cyl": 0,
                "add": None,  # missing add on a progressive line
                "on_hand": 3,
            },
            lens_line=_base_line_doc(has_add=True),
        )
    assert "add" in str(exc.value).lower()


def test_stock_line_sph_outside_range_rejected():
    with pytest.raises(ValueError) as exc:
        lcv.validate_lens_stock_line_payload(
            {
                "lens_line_id": "x",
                "store_id": "STR-001",
                "sph": -12.0,  # outside line sph_range [-8, 6]
                "cyl": 0,
                "add": None,
                "on_hand": 3,
            },
            lens_line=_base_line_doc(),
        )
    assert "sph" in str(exc.value).lower()


def test_stock_line_negative_qty_rejected():
    with pytest.raises(ValueError):
        lcv.validate_lens_stock_line_payload(
            {
                "lens_line_id": "x",
                "store_id": "STR-001",
                "sph": 0,
                "cyl": 0,
                "add": None,
                "on_hand": -1,
            },
            lens_line=_base_line_doc(),
        )


# ---------------------------------------------------------------------------
# Bulk import
# ---------------------------------------------------------------------------


def test_bulk_import_json_list():
    line = _base_line_doc()
    line["lens_line_id"] = "x"
    rows = [
        {"sph": -2.0, "cyl": 0.0, "qty": 3, "store_id": "STR-001"},
        {"sph": -2.25, "cyl": 0.0, "qty": 5, "store_id": "STR-001"},
    ]
    out = lcv.validate_bulk_import_payload(rows, lens_line=line)
    assert len(out) == 2
    assert out[0]["on_hand"] == 3
    assert out[1]["on_hand"] == 5


def test_bulk_import_csv_sv():
    line = _base_line_doc()
    line["lens_line_id"] = "x"
    csv = (
        "sph,cyl,qty,store_id\n"
        "-2.0,0,3,STR-001\n"
        "-2.25,-1.0,7,STR-001\n"
    )
    out = lcv.validate_bulk_import_payload(csv, lens_line=line)
    assert len(out) == 2
    assert out[1]["cyl"] == -1.0
    assert out[1]["on_hand"] == 7


def test_bulk_import_csv_bad_qty():
    line = _base_line_doc()
    line["lens_line_id"] = "x"
    csv = "sph,cyl,qty,store_id\n-2,0,three,STR-001\n"
    with pytest.raises(ValueError) as exc:
        lcv.validate_bulk_import_payload(csv, lens_line=line)
    assert "qty" in str(exc.value).lower()


def test_bulk_import_row_index_in_error():
    line = _base_line_doc()
    line["lens_line_id"] = "x"
    rows = [
        {"sph": -2, "cyl": 0, "qty": 1, "store_id": "STR-001"},
        {"sph": -12, "cyl": 0, "qty": 1, "store_id": "STR-001"},
    ]
    with pytest.raises(ValueError) as exc:
        lcv.validate_bulk_import_payload(rows, lens_line=line)
    # Row index must be in the message so a partial-import debug is tractable.
    assert "row 1" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Enum config validator
# ---------------------------------------------------------------------------


def test_enum_config_coatings_dedup_and_strip():
    out = lcv.validate_enum_config_payload(
        "coatings", ["ANTI_BLUE ", " ANTI_BLUE", "GREEN_COAT", "", "GREEN_COAT"]
    )
    assert out == ["ANTI_BLUE", "GREEN_COAT"]


def test_enum_config_indexes_must_be_gt_one():
    with pytest.raises(ValueError):
        lcv.validate_enum_config_payload("indexes", [1.0, 1.5])


def test_enum_config_unknown_enum_type_rejected():
    with pytest.raises(ValueError) as exc:
        lcv.validate_enum_config_payload("colours", ["RED"])
    assert "colours" in str(exc.value).lower()


def test_enum_config_series_brand_dict_shape():
    out = lcv.validate_enum_config_payload(
        "series",
        [
            {"Essilor": ["Crizal", "Varilux", "Crizal"]},
            {"Zeiss": ["DriveSafe"]},
        ],
    )
    assert out == [
        {"Essilor": ["Crizal", "Varilux"]},
        {"Zeiss": ["DriveSafe"]},
    ]


def test_enum_config_series_bad_shape_rejected():
    with pytest.raises(ValueError):
        lcv.validate_enum_config_payload("series", ["just-a-string"])


# ---------------------------------------------------------------------------
# compute_available
# ---------------------------------------------------------------------------


def test_compute_available_basic():
    assert lcv.compute_available(10, 3) == 7
    assert lcv.compute_available(0, 0) == 0
    # Never negative -- a stale set_on_hand below reserved must clamp.
    assert lcv.compute_available(2, 5) == 0
    assert lcv.compute_available(None, None) == 0
