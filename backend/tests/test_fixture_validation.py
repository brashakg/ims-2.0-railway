"""
IMS 2.0 - Display fixture / placement validator tests (v2-2a)
==============================================================
Pure unit tests against backend/api/services/fixture_validation.py. No DB,
no FastAPI -- exercises the enum boundaries, capacity rules, position /
code normalisation, and the placement<->fixture merch consistency check.

Mirrors test_org_validation.py's style.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import fixture_validation as fv  # noqa: E402


# ---------------------------------------------------------------------------
# Enum boundaries
# ---------------------------------------------------------------------------


def _base_fixture():
    """A minimal valid fixture payload -- tests tweak one field at a time."""
    return {
        "store_id": "STR-001",
        "code": "w-01",
        "name": "Wall - Designer",
        "type": "wall",
        "floor": "ground",
        "zone": "A",
        "capacity": 80,
        "merch": ["Frame"],
    }


def test_fixture_valid_round_trip():
    out = fv.validate_fixture_payload(_base_fixture())
    # Code uppercases.
    assert out["code"] == "W-01"
    # Name unchanged.
    assert out["name"] == "Wall - Designer"
    assert out["type"] == "wall"
    assert out["zone"] == "A"
    assert out["capacity"] == 80
    assert out["merch"] == ["Frame"]
    # Defaults.
    assert out["is_active"] is True


def test_fixture_invalid_type_rejected():
    p = _base_fixture()
    p["type"] = "ceiling"  # not in FIXTURE_TYPES
    with pytest.raises(ValueError) as exc:
        fv.validate_fixture_payload(p)
    assert "type" in str(exc.value)


def test_fixture_invalid_floor_rejected():
    p = _base_fixture()
    p["floor"] = "rooftop"
    with pytest.raises(ValueError):
        fv.validate_fixture_payload(p)


def test_fixture_invalid_zone_rejected():
    p = _base_fixture()
    p["zone"] = "D"  # only A/B/C/-
    with pytest.raises(ValueError):
        fv.validate_fixture_payload(p)


def test_fixture_zone_dash_is_valid():
    """Storage / clinic fixtures use '-' for zone (not in a customer zone)."""
    p = _base_fixture()
    p["zone"] = "-"
    p["floor"] = "storage"
    p["type"] = "drawer"
    out = fv.validate_fixture_payload(p)
    assert out["zone"] == "-"


def test_fixture_all_eight_types_accept():
    """Each of the 8 documented FIXTURE_TYPES is accepted."""
    for t in ("window", "wall", "pillar", "counter", "cabinet",
              "gondola", "drawer", "fridge"):
        p = _base_fixture()
        p["type"] = t
        out = fv.validate_fixture_payload(p)
        assert out["type"] == t


# ---------------------------------------------------------------------------
# Capacity rules
# ---------------------------------------------------------------------------


def test_fixture_capacity_must_be_positive():
    p = _base_fixture()
    p["capacity"] = 0
    with pytest.raises(ValueError):
        fv.validate_fixture_payload(p)
    p["capacity"] = -5
    with pytest.raises(ValueError):
        fv.validate_fixture_payload(p)


def test_fixture_capacity_rejects_non_int():
    p = _base_fixture()
    p["capacity"] = "many"
    with pytest.raises(ValueError):
        fv.validate_fixture_payload(p)
    p["capacity"] = 5.5
    with pytest.raises(ValueError):
        fv.validate_fixture_payload(p)
    # bool is a subclass of int -- must be rejected explicitly.
    p["capacity"] = True
    with pytest.raises(ValueError):
        fv.validate_fixture_payload(p)


# ---------------------------------------------------------------------------
# Code + name normalization
# ---------------------------------------------------------------------------


def test_fixture_code_uppercases_and_strips():
    p = _base_fixture()
    p["code"] = "  wd-01  "
    out = fv.validate_fixture_payload(p)
    assert out["code"] == "WD-01"


def test_fixture_code_rejects_whitespace_inside():
    """W 01 with an internal space differs from W-01 silently in the UNIQUE
    index -- force the staff to use a separator that survives normalization."""
    p = _base_fixture()
    p["code"] = "W 01"
    with pytest.raises(ValueError):
        fv.validate_fixture_payload(p)


def test_fixture_empty_code_rejected():
    p = _base_fixture()
    p["code"] = "   "
    with pytest.raises(ValueError):
        fv.validate_fixture_payload(p)


def test_fixture_empty_name_rejected():
    p = _base_fixture()
    p["name"] = ""
    with pytest.raises(ValueError):
        fv.validate_fixture_payload(p)


# ---------------------------------------------------------------------------
# merch[] subset validation
# ---------------------------------------------------------------------------


def test_fixture_merch_must_be_subset():
    p = _base_fixture()
    p["merch"] = ["Frame", "Toys"]  # Toys is not in CATALOG_TYPES
    with pytest.raises(ValueError):
        fv.validate_fixture_payload(p)


def test_fixture_merch_dedupes_and_preserves_order():
    p = _base_fixture()
    p["merch"] = ["Frame", "Frame", "Lens"]
    out = fv.validate_fixture_payload(p)
    assert out["merch"] == ["Frame", "Lens"]


def test_fixture_merch_empty_list_allowed():
    """A generic display rack with no merch gating is legal -- the GRN modal
    just won't filter by category for that fixture."""
    p = _base_fixture()
    p["merch"] = []
    out = fv.validate_fixture_payload(p)
    assert out["merch"] == []


def test_fixture_merch_rejects_non_string_entries():
    p = _base_fixture()
    p["merch"] = ["Frame", 7]
    with pytest.raises(ValueError):
        fv.validate_fixture_payload(p)


# ---------------------------------------------------------------------------
# PATCH semantics
# ---------------------------------------------------------------------------


def test_fixture_patch_partial_uses_existing():
    """A PATCH that only sends capacity must NOT re-require type/floor/zone."""
    existing = fv.validate_fixture_payload(_base_fixture())
    out = fv.validate_fixture_payload({"capacity": 100}, existing=existing)
    assert out["capacity"] == 100
    # Round-trip fields from existing.
    assert out["type"] == "wall"
    assert out["zone"] == "A"


# ---------------------------------------------------------------------------
# Placement payload
# ---------------------------------------------------------------------------


def _base_placement():
    return {
        "sku": "BV-RB-AV-5823",
        "store_id": "STR-001",
        "fixture_id": "wd-01",
        "qty": 1,
        "position": "shelf-2 . slot-04",
    }


def test_placement_valid_round_trip():
    out = fv.validate_placement_payload(_base_placement())
    assert out["sku"] == "BV-RB-AV-5823"
    assert out["qty"] == 1
    assert out["position"] == "shelf-2 . slot-04"
    # is_primary default
    assert out["is_primary"] is False


def test_placement_qty_must_be_positive_int():
    p = _base_placement()
    p["qty"] = 0
    with pytest.raises(ValueError):
        fv.validate_placement_payload(p)
    p["qty"] = -3
    with pytest.raises(ValueError):
        fv.validate_placement_payload(p)
    p["qty"] = "five"
    with pytest.raises(ValueError):
        fv.validate_placement_payload(p)


def test_placement_position_trimmed_and_nullable():
    p = _base_placement()
    p["position"] = "  bin-A1 . power matrix  "
    out = fv.validate_placement_payload(p)
    assert out["position"] == "bin-A1 . power matrix"

    p["position"] = "   "  # whitespace-only -> cleared
    out = fv.validate_placement_payload(p)
    assert out["position"] is None


def test_placement_missing_sku_rejected():
    p = _base_placement()
    p["sku"] = "   "
    with pytest.raises(ValueError):
        fv.validate_placement_payload(p)


def test_placement_with_fixture_cross_store_rejected():
    """Placement.store_id MUST match fixture.store_id -- a Bokaro placement
    cannot reference a Pune fixture."""
    p = _base_placement()
    fixture = {"store_id": "STR-OTHER", "merch": ["Frame"]}
    with pytest.raises(ValueError) as exc:
        fv.validate_placement_payload(p, fixture=fixture)
    assert "store" in str(exc.value).lower()


def test_placement_merch_consistency_blocks_cl_at_frame_fixture():
    """A CL SKU cannot be placed on a Frame-only fixture."""
    p = _base_placement()
    p["product_category"] = "CONTACT_LENS"
    fixture = {"store_id": "STR-001", "merch": ["Frame"]}
    with pytest.raises(ValueError) as exc:
        fv.validate_placement_payload(p, fixture=fixture)
    assert "merch" in str(exc.value).lower() or "category" in str(exc.value).lower()


def test_placement_merch_consistency_allows_frame_at_frame_fixture():
    p = _base_placement()
    p["product_category"] = "FRAME"
    fixture = {"store_id": "STR-001", "merch": ["Frame"]}
    out = fv.validate_placement_payload(p, fixture=fixture)
    assert out["sku"] == p["sku"]


def test_placement_merch_consistency_no_gate_when_merch_empty():
    """A fixture with empty merch[] accepts anything -- the floor sometimes
    needs a generic rack."""
    p = _base_placement()
    p["product_category"] = "CONTACT_LENS"
    fixture = {"store_id": "STR-001", "merch": []}
    out = fv.validate_placement_payload(p, fixture=fixture)
    assert out["sku"] == p["sku"]


def test_placement_merch_skipped_when_no_category_hint():
    """The merch check is OPTIONAL -- if the FE doesn't pass product_category
    the placement still goes through."""
    p = _base_placement()
    fixture = {"store_id": "STR-001", "merch": ["Frame"]}
    out = fv.validate_placement_payload(p, fixture=fixture)
    assert out["sku"] == p["sku"]


# ---------------------------------------------------------------------------
# placement_total_at_fixture + over_capacity helpers
# ---------------------------------------------------------------------------


def test_placement_total_sums_qty():
    placements = [{"qty": 3}, {"qty": 2}, {"qty": 1}]
    assert fv.placement_total_at_fixture(placements) == 6


def test_placement_total_handles_junk_rows():
    placements = [{"qty": 3}, {"qty": "junk"}, {"qty": None}, {}]
    # Only the numeric 3 contributes; rest silently skipped.
    assert fv.placement_total_at_fixture(placements) == 3


def test_over_capacity_boundary():
    """A fixture with capacity 5 returns False at total == 5, True at total > 5."""
    fixture = {"capacity": 5}
    assert fv.over_capacity(fixture, [{"qty": 3}, {"qty": 2}]) is False
    assert fv.over_capacity(fixture, [{"qty": 3}, {"qty": 3}]) is True


def test_over_capacity_handles_zero_capacity():
    """Capacity 0 / missing -> never reports over. Capacity is required by
    the schema, so this is a defensive branch only."""
    assert fv.over_capacity({"capacity": 0}, [{"qty": 10}]) is False
    assert fv.over_capacity({}, [{"qty": 10}]) is False
