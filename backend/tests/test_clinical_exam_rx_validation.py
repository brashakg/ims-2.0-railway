"""
IMS 2.0 - the eye-exam API must REJECT an impossible power on every tab
=======================================================================
Owner report 2026-08-24: "-9999" was accepted as a lensometer power.

A frontend-only fix is not a fix: POST /clinical/tests/{id}/complete is a plain
HTTP endpoint and is reachable without the form. These tests drive the REAL
endpoint through a TestClient and assert on the RESPONSE STATUS and the
RESPONSE BODY, not on a log line. No database is needed -- range validation
runs before any repository is touched, so a clean payload gets past it (see
_assert_accepted) and a bad one never does.

Covered, per the canonical bounds in api/services/rx_validation.py:
  * -9999 and +9999 sphere                       -> 422
  * a FRACTIONAL axis (90.5)                     -> 422 (not silently rounded)
  * a 0 axis recorded with a non-zero cylinder   -> 422 (0 is not a meridian)
  * an out-of-range MONOCULAR per-eye PD         -> 422
  * ordinary in-range values                     -> NOT 422 (positive control)

...on the lensometer, auto-refractometer, subjective-refraction AND final-Rx
blocks -- asserted as a SET of surfaces, not a count.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import clinical  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


def _client():
    app = FastAPI()
    app.include_router(clinical.router, prefix="/clinical")

    async def _fake_user():
        return {
            "user_id": "u1",
            "username": "opto",
            "full_name": "Dr Rao",
            "active_store_id": "store-001",
            "roles": ["OPTOMETRIST"],
        }

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


CLEAN_FINAL = {
    "rightEye": {"sphere": "-2.25", "cylinder": "-0.75", "axis": "90"},
    "leftEye": {"sphere": "+4.00"},
}


def _post(body: dict):
    return _client().post("/clinical/tests/test-1/complete", json=body)


def _assert_accepted(resp, where: str):
    """The payload CLEARED the clinical range gate.

    Range validation runs before the test row is looked up, so a clean payload
    lands on the lookup: 200 when a repository serves the row, 404 ("Test not
    found") when it does not. Either way it is not a 422, and a 422 is the only
    thing this control is about. Asserting an exact 200 would make the control
    depend on which repository the suite happens to have installed."""
    assert resp.status_code != 422, f"{where}: a VALID reading was rejected -- {resp.text[:300]}"
    assert resp.status_code in (200, 404), f"{where}: {resp.status_code} {resp.text[:300]}"


# The four exam blocks that carry a refraction, by their wire key. The final Rx
# travels as rightEye/leftEye at the top level; the other three are nested.
EXAM_BLOCKS = ("lensometer", "autoRef", "subjectiveRx")


def _body_with(block: str, right_eye: dict) -> dict:
    """A payload whose ONLY problem (if any) lives on `block`'s right eye."""
    if block == "final":
        body = dict(CLEAN_FINAL)
        body["rightEye"] = right_eye
        return body
    body = dict(CLEAN_FINAL)
    body[block] = {"rightEye": right_eye, "leftEye": {}}
    return body


ALL_SURFACES = EXAM_BLOCKS + ("final",)


BAD_EYES = {
    "sph_minus_9999": {"sphere": "-9999"},
    "sph_plus_9999": {"sphere": "+9999"},
    "fractional_axis": {"cylinder": "-1.00", "axis": "90.5"},
    "zero_axis_with_cyl": {"cylinder": "-1.00", "axis": "0"},
    "cyl_without_axis": {"cylinder": "-1.00"},
    "off_grid_sph": {"sphere": "-1.30"},
    "pd_out_of_range": {"pd": "9999"},
}


@pytest.mark.parametrize("surface", ALL_SURFACES)
@pytest.mark.parametrize("case", sorted(BAD_EYES))
def test_impossible_power_is_rejected(surface: str, case: str):
    resp = _post(_body_with(surface, BAD_EYES[case]))
    assert resp.status_code == 422, (
        f"{surface}/{case} was ACCEPTED: {resp.status_code} {resp.text[:300]}"
    )


@pytest.mark.parametrize("surface", ALL_SURFACES)
def test_ordinary_readings_are_accepted(surface: str):
    """POSITIVE CONTROL. A gate that refuses everything would pass every
    rejection assertion above and be worse than the reported bug."""
    resp = _post(
        _body_with(
            surface,
            {
                "sphere": "-2.50",
                "cylinder": "-0.75",
                "axis": "90",
                "add": "+2.00",
                # 32.5mm is an ordinary MONOCULAR per-eye PD.
                "pd": "32.5",
            },
        )
    )
    _assert_accepted(resp, surface)


def test_a_refraction_only_test_still_completes():
    """POSITIVE CONTROL: no exam blocks at all is the common case."""
    _assert_accepted(_post(CLEAN_FINAL), "refraction-only")


def test_keratometry_out_of_range_is_rejected():
    body = dict(CLEAN_FINAL)
    body["autoRef"] = {"rightEye": {"k1": "-9999"}, "leftEye": {}}
    assert _post(body).status_code == 422


def test_keratometry_in_range_is_accepted():
    body = dict(CLEAN_FINAL)
    body["autoRef"] = {
        "rightEye": {"k1": "42.50", "k1Axis": "175", "k2": "43.25", "k2Axis": "85"},
        "leftEye": {},
    }
    _assert_accepted(_post(body), "keratometry in range")


def test_slit_lamp_iop_out_of_range_is_rejected():
    body = dict(CLEAN_FINAL)
    body["slitLamp"] = {"rightEye": {"iop": 220}, "leftEye": {}}
    assert _post(body).status_code == 422


def test_slit_lamp_findings_are_accepted():
    body = dict(CLEAN_FINAL)
    body["slitLamp"] = {
        "rightEye": {"cornea": "Clear", "lens": "Clear", "iop": 14},
        "leftEye": {"cornea": "Clear", "iop": 15},
        "remarks": "unremarkable",
    }
    _assert_accepted(_post(body), "slit lamp findings")


# ---------------------------------------------------------------------------
# The exam blocks must actually be STORED. A validated field that is thrown
# away the moment the dialog closes is not a fix -- and the edit screen cannot
# show a lensometer reading that was never persisted.
# ---------------------------------------------------------------------------
def test_exam_blocks_survive_into_the_stored_document():
    from api.routers.clinical import EyeTestData

    data = EyeTestData(
        rightEye={"sphere": "-2.25"},
        leftEye={"sphere": "-2.00"},
        lensometer={"rightEye": {"sphere": "-2.00"}, "leftEye": {"sphere": "-1.75"}},
        autoRef={"rightEye": {"sphere": "-2.25", "k1": "42.50"}, "leftEye": {}},
        subjectiveRx={"rightEye": {"sphere": "-2.25"}, "leftEye": {}},
        slitLamp={"rightEye": {"cornea": "Clear", "iop": 14}, "leftEye": {}},
    )

    assert data.lensometer is not None
    assert data.lensometer.right_eye.sphere == "-2.00"
    assert data.auto_ref is not None and data.auto_ref.right_eye.k1 == "42.50"
    assert data.subjective_rx is not None
    assert data.slit_lamp is not None and data.slit_lamp.right_eye.iop == 14

    stored = clinical._exam_blocks_for_storage(data)
    assert set(stored) == {"lensometer", "auto_ref", "subjective_rx", "slit_lamp"}
    assert stored["lensometer"]["right_eye"]["sphere"] == "-2.00"
    assert stored["auto_ref"]["right_eye"]["k1"] == "42.50"
    assert stored["slit_lamp"]["right_eye"]["iop"] == 14


def test_no_exam_blocks_stores_nothing_extra():
    """A quick refraction-only test must be byte-for-byte what it was before."""
    from api.routers.clinical import EyeTestData

    data = EyeTestData(rightEye={"sphere": "-1.00"}, leftEye={"sphere": "-1.00"})
    assert clinical._exam_blocks_for_storage(data) == {}


# ---------------------------------------------------------------------------
# The SIGN must survive the wire. See test_clinical_plus_sign.py for the full
# treatment; this pins the one fact the validator must not break.
# ---------------------------------------------------------------------------
def test_a_signed_string_round_trips_through_validation():
    from api.services.rx_validation import _validate_rx_value

    assert _validate_rx_value("+4.00", "sph") == "+4.00"
    assert _validate_rx_value("-4.00", "sph") == "-4.00"
