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
# ---------------------------------------------------------------------------
# THE BINOCULAR IPD. Both write doors, because both mint/rewrite the Rx.
# ---------------------------------------------------------------------------
# The IPD is the number that centres BOTH lenses in the frame. The exam form
# gates it (40-80mm) but the server never looked at it, so an IPD of 9999 --
# from a device import, a CSV, an integration or a direct call -- reached a
# billable prescription and the lab. The per-eye MONOCULAR PD was already
# gated; this is its binocular twin.


def _put_exam(body: dict):
    return _client().put("/clinical/tests/test-1/exam", json=body)


DOORS = {"complete": _post, "exam": _put_exam}

BAD_IPD = ["9999", "0", "6.25", "banana"]


@pytest.mark.parametrize("door", sorted(DOORS))
@pytest.mark.parametrize("bad", BAD_IPD)
def test_an_impossible_binocular_ipd_is_rejected(door: str, bad: str):
    body = dict(CLEAN_FINAL)
    body["ipd"] = bad
    resp = DOORS[door](body)
    assert resp.status_code == 422, (
        f"{door}: an IPD of {bad!r} was ACCEPTED -- {resp.status_code} {resp.text[:300]}"
    )
    assert "pd" in resp.text.lower(), f"{door}: the refusal never names the PD -- {resp.text[:300]}"


@pytest.mark.parametrize("door", sorted(DOORS))
@pytest.mark.parametrize("good", ["62.5", "40", "80", ""])
def test_an_ordinary_binocular_ipd_is_accepted(door: str, good: str):
    """POSITIVE CONTROL, including the boundaries and 'not recorded'."""
    body = dict(CLEAN_FINAL)
    body["ipd"] = good
    _assert_accepted(DOORS[door](body), f"{door} ipd={good!r}")


@pytest.mark.parametrize("door", sorted(DOORS))
def test_an_absent_ipd_is_accepted(door: str):
    _assert_accepted(DOORS[door](dict(CLEAN_FINAL)), f"{door} no ipd")


# ---------------------------------------------------------------------------
# VISUAL ACUITY. Free text on the server; a Snellen fraction in the clinic.
# ---------------------------------------------------------------------------
# `va` (exam) and `acuity` (prescription) were unvalidated anywhere in backend/:
# "banana" and "20/9999" both saved with a 200 into the exam block and the
# mirrored prescription. The rule existed on the client only (rxLimits VA_SET).

BAD_VA = ["banana", "20/9999", "6/7"]


@pytest.mark.parametrize("surface", ALL_SURFACES)
@pytest.mark.parametrize("bad", BAD_VA)
def test_junk_visual_acuity_is_rejected(surface: str, bad: str):
    resp = _post(_body_with(surface, {"va": bad}))
    assert resp.status_code == 422, (
        f"{surface}: a VA of {bad!r} was ACCEPTED -- {resp.status_code} {resp.text[:300]}"
    )


@pytest.mark.parametrize("surface", ALL_SURFACES)
@pytest.mark.parametrize(
    "good", ["6/6", "6/9", "6/60", "", "CF", "HM", "PL", "NPL"]
)
def test_a_real_snellen_acuity_is_accepted(surface: str, good: str):
    """Snellen AND the four low-vision notations. Counting Fingers / Hand
    Movement / Perception of Light / No PL are everyday findings in a dense
    cataract or an advanced glaucoma; a gate that refuses them does not delete
    the finding, it makes the optometrist record a CF eye as 6/60."""
    _assert_accepted(_post(_body_with(surface, {"va": good})), f"{surface} va={good!r}")


def test_junk_acuity_is_rejected_on_the_prescriptions_endpoint_too():
    """The mirrored prescription has its own door (`acuity`), and it is the one
    that prints on the patient's card."""
    from api.services.rx_validation import _validate_visual_acuity

    for bad in BAD_VA:
        with pytest.raises(ValueError):
            _validate_visual_acuity(bad, "right eye acuity")
    for good in ("6/6", "6/18", "", None):
        assert _validate_visual_acuity(good, "right eye acuity") == good


# ---------------------------------------------------------------------------
# THE TWO VA LISTS ARE ONE LIST. Asserted as a SET and a COUNT.
# ---------------------------------------------------------------------------
# This file's header and rxLimits.ts's header each promise the other is their
# mirror. They stopped being mirrors the moment the server was widened to the
# four low-vision notations and the client was not: the exam form then refused
# CF/HM/PL/NPL that this very endpoint accepts, so the optometrist's only way
# to save was to pick a Snellen fraction the patient cannot read. A comment
# cannot hold two files together; this test can.

def _frontend_va_set() -> list:
    """The VA_SET literal as the TypeScript file actually declares it."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "frontend" / "src" / "constants" / "rxLimits.ts"
    text = src.read_text(encoding="utf-8")
    m = re.search(r"export const VA_SET = \[(.*?)\] as const;", text, re.S)
    assert m, f"VA_SET literal not found in {src} -- did it move or change shape?"
    return re.findall(r"'([^']+)'", m.group(1))


def test_the_frontend_and_backend_visual_acuity_sets_are_identical():
    from api.services.rx_validation import _VA_SET

    frontend = _frontend_va_set()
    assert set(frontend) == set(_VA_SET), (
        "the VA lists have drifted -- "
        f"client-only={sorted(set(frontend) - set(_VA_SET))}, "
        f"server-only={sorted(set(_VA_SET) - set(frontend))}. "
        "A value the server accepts but the client refuses cannot be recorded "
        "at all; a value the client offers but the server refuses is a 422 in "
        "the optometrist's face."
    )
    assert len(frontend) == len(_VA_SET) == 11, (
        f"expected 11 VA values on each side, got {len(frontend)} client / "
        f"{len(_VA_SET)} server (duplicates in either list?)"
    )


def test_every_low_vision_notation_survives_the_validator_verbatim():
    """The four are not merely 'not rejected' -- they are stored as typed."""
    from api.services.rx_validation import _validate_visual_acuity

    for notation in ("CF", "HM", "PL", "NPL"):
        assert _validate_visual_acuity(notation, "right eye acuity") == notation
