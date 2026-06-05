"""
IMS 2.0 - Tests for P3 clinical backlog items

CLI-6: FittingDetails schema now includes segment_height / pantoscopic_tilt /
       vertex_distance / wrap_angle — new optional fields, backward-compatible.

CLI-7: POST /clinical/manufacturability-check — frame+lens+Rx feasibility check.
       Pure logic in the router, no DB required for the core math.

CLI-9: GET / POST / DELETE /clinical/lens-power-combos — named Rx template store.

CLI-10: progression_diffs() returns `sphere_delta` keys; the PrescriptionHistoryModal
        now reads them correctly (JS-side fix, not tested here).  We verify the
        backend output shape so any future regression shows up here first.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("ENVIRONMENT", "test")

# ---------------------------------------------------------------------------
# CLI-6 — FittingDetails schema includes the 4 new progressive params
# ---------------------------------------------------------------------------


class TestFittingDetailsSchema:
    """The FittingDetails Pydantic model must accept the 4 new fields and
    default them to None so existing callers that don't send them are
    unaffected (additive / backward-compatible)."""

    def _model(self):
        from api.routers.workshop import FittingDetails
        return FittingDetails

    def test_segment_height_field_present(self):
        FD = self._model()
        fd = FD()
        assert hasattr(fd, "segment_height"), "segment_height field missing"
        assert fd.segment_height is None

    def test_pantoscopic_tilt_field_present(self):
        FD = self._model()
        fd = FD()
        assert hasattr(fd, "pantoscopic_tilt"), "pantoscopic_tilt field missing"
        assert fd.pantoscopic_tilt is None

    def test_vertex_distance_field_present(self):
        FD = self._model()
        fd = FD()
        assert hasattr(fd, "vertex_distance"), "vertex_distance field missing"
        assert fd.vertex_distance is None

    def test_wrap_angle_field_present(self):
        FD = self._model()
        fd = FD()
        assert hasattr(fd, "wrap_angle"), "wrap_angle field missing"
        assert fd.wrap_angle is None

    def test_progressive_params_round_trip(self):
        """All 4 new fields parse and serialise cleanly."""
        FD = self._model()
        fd = FD(
            segment_height="19",
            pantoscopic_tilt="10",
            vertex_distance="12.5",
            wrap_angle="5",
        )
        assert fd.segment_height == "19"
        assert fd.pantoscopic_tilt == "10"
        assert fd.vertex_distance == "12.5"
        assert fd.wrap_angle == "5"

    def test_existing_fields_unaffected(self):
        """Existing fields still parse as before (backward-compat guard)."""
        FD = self._model()
        fd = FD(dia="65", fh="20", b_size="30", dbl="16", tint="15%", base_curve="6")
        assert fd.dia == "65"
        assert fd.fh == "20"
        assert fd.base_curve == "6"


# ---------------------------------------------------------------------------
# CLI-7 — Manufacturability check helper logic
# ---------------------------------------------------------------------------


class TestManufacturabilityLogic:
    """Test the _check_power_in_range helper and the endpoint's pure logic
    (no Mongo needed — we test the parsing + arithmetic directly)."""

    def _check(self):
        from api.routers.clinical import _check_power_in_range
        return _check_power_in_range

    def _parse(self):
        from api.routers.clinical import _parse_float_safe
        return _parse_float_safe

    def test_parse_float_safe_none(self):
        parse = self._parse()
        assert parse(None) is None

    def test_parse_float_safe_empty_string(self):
        parse = self._parse()
        assert parse("") is None

    def test_parse_float_safe_number(self):
        parse = self._parse()
        assert parse("-1.25") == pytest.approx(-1.25)

    def test_parse_float_safe_int(self):
        parse = self._parse()
        assert parse(6) == pytest.approx(6.0)

    def test_check_power_in_range_pass(self):
        check = self._check()
        issues = []
        check("SPH", -2.5, {"min": -6.0, "max": 6.0}, issues)
        assert issues == []

    def test_check_power_in_range_below_min(self):
        check = self._check()
        issues = []
        check("Right SPH", -7.0, {"min": -6.0, "max": 6.0}, issues)
        assert len(issues) == 1
        assert "below" in issues[0]

    def test_check_power_in_range_above_max(self):
        check = self._check()
        issues = []
        check("Left CYL", 4.0, {"min": -4.0, "max": 2.0}, issues)
        assert len(issues) == 1
        assert "exceeds" in issues[0]

    def test_check_power_in_range_missing_rng(self):
        """Empty range dict means no check — no issue appended."""
        check = self._check()
        issues = []
        check("ADD", 1.5, {}, issues)
        assert issues == []

    def test_check_power_in_range_none_value(self):
        """None value means field not prescribed — no issue."""
        check = self._check()
        issues = []
        check("SPH", None, {"min": -6.0, "max": 6.0}, issues)
        assert issues == []

    def test_frame_geometry_math(self):
        """Rule: min B = 2 * seg_height + 2 mm."""
        parse = self._parse()
        b = parse("38")   # mm
        sh = parse("22")  # seg height mm
        min_b = sh * 2 + 2  # 46
        # B=38 < min_b=46 -> warning expected
        assert b < min_b, "Expect too-small frame for this seg height"

    def test_frame_geometry_ok(self):
        parse = self._parse()
        b = parse("52")
        sh = parse("20")
        min_b = sh * 2 + 2  # 42
        assert b >= min_b


# ---------------------------------------------------------------------------
# CLI-9 — LensPowerComboCreate Pydantic schema
# ---------------------------------------------------------------------------


class TestLensPowerComboSchema:
    """Validate the LensPowerComboCreate schema independently of Mongo."""

    def _model(self):
        from api.routers.clinical import LensPowerComboCreate
        return LensPowerComboCreate

    def test_minimal_create(self):
        M = self._model()
        obj = M(name="Myopia mild")
        assert obj.name == "Myopia mild"
        assert obj.right_eye is None
        assert obj.notes is None

    def test_with_eye_data(self):
        M = self._model()
        obj = M(
            name="Standard bifocal",
            right_eye={"sph": "-1.00", "cyl": "-0.50", "axis": "180", "add": "1.75"},
            left_eye={"sph": "-1.25", "cyl": "0.00", "axis": "0", "add": "1.75"},
            notes="Standard ADD for 45-55yr",
        )
        assert obj.right_eye["sph"] == "-1.00"
        assert obj.notes == "Standard ADD for 45-55yr"

    def test_name_required(self):
        M = self._model()
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            M()

    def test_name_too_long_rejected(self):
        M = self._model()
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            M(name="x" * 101)  # max 100

    def test_notes_too_long_rejected(self):
        M = self._model()
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            M(name="ok", notes="n" * 501)  # max 500


# ---------------------------------------------------------------------------
# CLI-10 — progression_diffs() returns `_delta` suffixed keys
# ---------------------------------------------------------------------------


class TestProgressionDeltaKeys:
    """Guard against regression: the backend must emit *_delta keys so the
    fixed PrescriptionHistoryModal.tsx DeltaRow can read them."""

    def test_sphere_delta_key_present(self):
        from api.services.prescription_versions import progression_diffs

        history = [
            {
                "prescription_id": "P-1",
                "created_at": "2025-01-01T00:00:00",
                "right_eye": {"sph": "-1.50", "cyl": "0.00"},
                "left_eye": {"sph": "-1.25", "cyl": "0.00"},
            },
            {
                "prescription_id": "P-2",
                "created_at": "2026-01-01T00:00:00",
                "right_eye": {"sph": "-1.75", "cyl": "0.00"},
                "left_eye": {"sph": "-1.50", "cyl": "0.00"},
            },
        ]
        deltas = progression_diffs(history)
        assert len(deltas) == 1
        d = deltas[0]

        # Must have `_delta` suffix keys (what the FE reads after the CLI-10 fix)
        assert "sphere_delta" in d["right_eye"], "sphere_delta key missing — CLI-10 regression"
        assert "cylinder_delta" in d["right_eye"], "cylinder_delta key missing"
        assert "sphere_delta" in d["left_eye"], "sphere_delta key missing on left_eye"

        # Must NOT have bare 'sphere' key (no confusion with the eye block fields)
        assert "sphere" not in d["right_eye"], (
            "Bare 'sphere' key present — DeltaRow would read this incorrectly"
        )

    def test_visit_date_key(self):
        """Backend uses from_visit_at / to_visit_at, not from_date / to_date."""
        from api.services.prescription_versions import progression_diffs

        history = [
            {
                "prescription_id": "P-A",
                "created_at": "2025-06-01T00:00:00",
                "right_eye": {"sph": "-1.00"},
                "left_eye": {},
            },
            {
                "prescription_id": "P-B",
                "created_at": "2026-06-01T00:00:00",
                "right_eye": {"sph": "-1.25"},
                "left_eye": {},
            },
        ]
        deltas = progression_diffs(history)
        assert len(deltas) == 1
        d = deltas[0]
        assert "from_visit_at" in d, "from_visit_at key missing — date field mismatch"
        assert "to_visit_at" in d, "to_visit_at key missing — date field mismatch"
        # Old keys must NOT be present (they confused the FE type before the fix)
        assert "from_date" not in d, "Stale from_date key present"
        assert "to_date" not in d, "Stale to_date key present"

    def test_delta_arithmetic(self):
        """Sphere gets more negative: -1.50 -> -1.75, delta = -0.25."""
        from api.services.prescription_versions import progression_diffs

        history = [
            {"prescription_id": "P1", "created_at": "2025-01-01", "right_eye": {"sph": "-1.50"}, "left_eye": {}},
            {"prescription_id": "P2", "created_at": "2026-01-01", "right_eye": {"sph": "-1.75"}, "left_eye": {}},
        ]
        deltas = progression_diffs(history)
        assert deltas[0]["right_eye"]["sphere_delta"] == pytest.approx(-0.25)
