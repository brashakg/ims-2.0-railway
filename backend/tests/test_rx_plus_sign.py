"""
IMS 2.0 - a POSITIVE power must print with its "+"
===================================================
Owner report 2026-08-24: "when putting +4.00 power, plus is removed and only
4.00 is shown. throughout our app make sure for positive power + is displayed".

The sign of an optical power is the difference between two different lenses. A
+4.00 read as 4 by someone who assumes the house convention is minus-cyl, or a
lab tech reading a job card, is a wrong lens in a patient's glasses.

The stored value is a string and, for anything written before this change, it
is UNSIGNED ("4", "4.0"): the frontend coerced the typed "+4.00" to a number
before sending it. So the display side cannot assume the sign is in the data --
it has to RENDER the power, not echo it. These tests pin the two server-side
surfaces that echoed it:

  * the WORKSHOP LAB JOB-CARD (routers/labels._rx_summary) -- the traveler the
    lens grinder actually reads. Highest stakes on this list.
  * the PRESCRIPTIONS PRINT CARD (routers/prescriptions._build_*_print_html) --
    the patient's copy.

Both now go through api/services/rx_print_values, which is also where the
clinic's own A5 card has always rendered from -- one renderer, so the two cards
this app can print for the SAME prescription stop disagreeing.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# The shared renderer
# ---------------------------------------------------------------------------
class TestRxPowerOr:
    def test_positive_gets_its_plus_from_an_unsigned_stored_value(self):
        from api.services.rx_print_values import rx_power_or

        # THE REQUIREMENT. This is exactly what is in the database today.
        assert rx_power_or("4") == "+4.00"
        assert rx_power_or("4.0") == "+4.00"
        assert rx_power_or(4) == "+4.00"

    def test_an_already_signed_value_is_unchanged(self):
        from api.services.rx_print_values import rx_power_or

        assert rx_power_or("+4.00") == "+4.00"

    def test_a_minus_is_NEVER_lost(self):
        from api.services.rx_print_values import rx_power_or

        assert rx_power_or("-4") == "-4.00"
        assert rx_power_or("-0.75") == "-0.75"
        assert rx_power_or(-4.0) == "-4.00"

    def test_a_blank_never_becomes_a_power(self):
        from api.services.rx_print_values import rx_power_or

        # The other half of the rule: absence must not be rendered as +0.00.
        for absent in (None, "", "   ", "None", "null", "undefined"):
            assert rx_power_or(absent) == "-", absent

    def test_a_recorded_zero_is_a_finding_not_an_absence(self):
        from api.services.rx_print_values import rx_power_or

        assert rx_power_or(0) == "Plano"
        assert rx_power_or("0.00") == "Plano"

    def test_an_axis_is_never_signed(self):
        from api.services.rx_print_values import rx_axis_or

        assert rx_axis_or("90") == "90"
        assert rx_axis_or(180) == "180"
        assert rx_axis_or(None) == "-"


# ---------------------------------------------------------------------------
# The LAB JOB-CARD. The lens grinder reads this string.
# ---------------------------------------------------------------------------
class TestLabJobCard:
    def _summary(self, right):
        from api.routers.labels import _rx_summary

        return _rx_summary({"right_eye": right, "left_eye": {}})["right"]

    def test_a_plus_power_reaches_the_grinder_WITH_its_sign(self):
        assert self._summary({"sph": "4"}) == "SPH +4.00"
        assert self._summary({"sph": "4.0"}) == "SPH +4.00"

    def test_a_minus_power_keeps_its_sign(self):
        assert self._summary({"sph": "-4.00"}) == "SPH -4.00"

    def test_a_full_eye_renders_every_field_signed_correctly(self):
        got = self._summary({"sph": "4", "cyl": "-0.75", "axis": 90, "add": "2"})
        assert got == "SPH +4.00 CYL -0.75 AX 90 ADD +2.00"

    def test_a_recorded_plano_is_not_dropped_from_the_job_card(self):
        # The old truthiness filter (`if eye.get("sph")`) omitted the SPH line
        # entirely for a plano, so the lab could not tell "no sphere needed"
        # from "sphere never measured".
        assert self._summary({"sph": "0", "cyl": "-1.00", "axis": 90}) == (
            "SPH Plano CYL -1.00 AX 90"
        )

    def test_an_unrecorded_power_produces_no_line(self):
        # POSITIVE CONTROL in the other direction: a fix that prints something
        # for everything would invent a power the clinician never recorded.
        assert self._summary({"cyl": "-1.00", "axis": 90}) == "CYL -1.00 AX 90"
        assert self._summary({}) == ""


# ---------------------------------------------------------------------------
# The patient's printed prescription card.
# ---------------------------------------------------------------------------
class TestPrintedRxCard:
    def _html(self, right, left=None):
        from api.routers.prescriptions import _build_spectacle_print_html

        return _build_spectacle_print_html(
            {
                "prescription_number": "RX-1",
                "prescription_date": "2026-08-24T00:00:00",
                "expiry_date": "2027-08-24T00:00:00",
                "right_eye": right,
                "left_eye": left or {},
            }
        )

    def test_a_plus_sphere_prints_with_its_plus(self):
        assert "+4.00" in self._html({"sph": "4"})

    def test_a_minus_sphere_prints_with_its_minus(self):
        html = self._html({"sph": "-4"})
        assert "-4.00" in html
        assert "+4.00" not in html

    def test_the_axis_is_not_given_a_sign(self):
        html = self._html({"sph": "4", "cyl": "-1.00", "axis": 90})
        assert ">90<" in html
        assert "+90" not in html

    def test_a_blank_eye_prints_dashes_not_powers(self):
        html = self._html({})
        assert "+0.00" not in html

    def test_the_contact_lens_card_signs_its_powers_too(self):
        from api.routers.prescriptions import _build_cl_print_html

        html = _build_cl_print_html(
            {
                "prescription_number": "RX-2",
                "prescription_date": "2026-08-24T00:00:00",
                "expiry_date": "2027-08-24T00:00:00",
                "cl_right": {"cl_power": "4", "base_curve": "8.6"},
                "cl_left": {},
            }
        )
        assert "+4.00" in html


# ---------------------------------------------------------------------------
# The write path. A signed string must survive validation and storage.
# ---------------------------------------------------------------------------
class TestSignSurvivesTheWire:
    def test_the_validator_passes_a_signed_string_through_verbatim(self):
        from api.services.rx_validation import _validate_rx_value

        assert _validate_rx_value("+4.00", "sph") == "+4.00"

    def test_the_eye_test_write_stores_the_signed_string_verbatim(self):
        from api.routers.clinical import _power_for_storage

        # The frontend now sends "+4.00" instead of the number 4; this is the
        # function that decides what lands in the prescriptions collection.
        assert _power_for_storage({"sphere": "+4.00"}, "sphere", "sph") == "+4.00"
        assert _power_for_storage({"sphere": "-4.00"}, "sphere", "sph") == "-4.00"
        # ...and a genuine plano is still preserved, not blanked.
        assert _power_for_storage({"sphere": "0.00"}, "sphere", "sph") == "0.00"

    def test_a_signed_string_is_accepted_by_the_prescription_eye_model(self):
        from api.routers.prescriptions import EyeData

        eye = EyeData(sph="+4.00", cyl="-0.75", axis=90)
        assert eye.sph == "+4.00"

    @pytest.mark.parametrize("value,expected", [("+4.00", "+4.00"), ("-4.00", "-4.00")])
    def test_the_stored_signed_string_still_renders_correctly(self, value, expected):
        from api.services.rx_print_values import rx_power_or

        assert rx_power_or(value) == expected


class TestPlanoIsPlanoHoweverItIsSpelled:
    """Keeping the normalised text ("0.00") instead of coercing to a number
    ("0") must not change what the validator accepts.

    The plano short-circuit was spelled `value.strip() == "0"` -- string
    equality against ONE spelling of zero. Sending "0.00" therefore skipped the
    shortcut and fell through to the range check, and for ADD (valid range
    0.75-4.00, the limits of a PRESCRIBED reading addition) a patient who needs
    NO reading addition had their entire prescription refused.
    """

    @pytest.mark.parametrize("field", ["sph", "cyl", "add"])
    @pytest.mark.parametrize("zero", ["0", "0.00", "+0.00", "-0.00", " 0 "])
    def test_every_spelling_of_zero_is_accepted_as_plano(self, field, zero):
        from api.services.rx_validation import _validate_rx_value

        assert _validate_rx_value(zero, field) == zero

    def test_a_zero_reading_addition_does_not_refuse_the_prescription(self):
        # The exact regression: single-vision patient, no ADD.
        from api.services.rx_validation import _validate_rx_value

        assert _validate_rx_value("0.00", "add") == "0.00"

    # ---- positive controls: the gate is still a gate --------------------
    @pytest.mark.parametrize(
        "field,value",
        [("sph", "-9999"), ("sph", "9999"), ("add", "9999"),
         ("sph", "-30.00"), ("cyl", "-8.00"), ("sph", "1.30"), ("add", "0.50")],
    )
    def test_out_of_range_is_still_rejected(self, field, value):
        from api.services.rx_validation import _validate_rx_value

        with pytest.raises(ValueError):
            _validate_rx_value(value, field)

    @pytest.mark.parametrize(
        "field,value",
        [("sph", "-2.00"), ("sph", "+4.00"), ("cyl", "-0.75"), ("add", "+2.00")],
    )
    def test_ordinary_values_are_still_accepted(self, field, value):
        from api.services.rx_validation import _validate_rx_value

        assert _validate_rx_value(value, field) == value
