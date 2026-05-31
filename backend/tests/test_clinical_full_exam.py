"""
IMS 2.0 — full eye-exam persistence (clinic C6-B)
==================================================
A complete optometric exam captures more than refraction: visual acuity, IOP,
chief complaint/history, diagnosis, colour vision, cover test, dominant eye.
These were DROPPED on test completion. EyeTestData.clinical_findings is the new
OPTIONAL block; absent -> the test is a refraction-only record exactly as before.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-c6b")


def test_refraction_only_still_works_without_findings():
    from api.routers.clinical import EyeTestData

    d = EyeTestData(rightEye={"sphere": -1.0}, leftEye={"sphere": -1.0})
    assert d.clinical_findings is None  # nothing changes for a quick test


def test_full_exam_round_trips_via_camelcase_aliases():
    from api.routers.clinical import EyeTestData

    d = EyeTestData(
        rightEye={"sphere": -2.25},
        leftEye={"sphere": -2.0},
        clinicalFindings={
            "vaRightUnaided": "6/12",
            "vaRightAided": "6/6",
            "iopRight": 14.5,
            "iopLeft": 15.0,
            "chiefComplaint": "blurry distance vision",
            "diagnosis": "Myopia",
            "colourVision": "Normal",
            "dominantEye": "r",
        },
    )
    cf = d.clinical_findings
    assert cf is not None
    assert cf.va_right_unaided == "6/12" and cf.va_right_aided == "6/6"
    assert cf.iop_right == 14.5 and cf.iop_left == 15.0
    assert cf.chief_complaint == "blurry distance vision"
    assert cf.dominant_eye == "RIGHT"  # normalized from "r"


def test_findings_dump_is_lean_and_snake_case():
    from api.routers.clinical import ClinicalFindings

    cf = ClinicalFindings(vaRightUnaided="6/6", iopRight=12.0)
    dumped = cf.model_dump(exclude_none=True)
    assert dumped == {"va_right_unaided": "6/6", "iop_right": 12.0}  # only set keys


def test_out_of_range_iop_rejected():
    from pydantic import ValidationError

    from api.routers.clinical import ClinicalFindings

    with pytest.raises(ValidationError):
        ClinicalFindings(iopRight=220)  # fat-finger; clinical window is 0-80


def test_bad_dominant_eye_rejected():
    from pydantic import ValidationError

    from api.routers.clinical import ClinicalFindings

    with pytest.raises(ValidationError):
        ClinicalFindings(dominantEye="middle")


def test_repo_persists_findings_only_when_present():
    """complete_test stores clinical_findings on the test doc when given, and
    omits the key entirely for a refraction-only completion."""
    from database.repositories.clinical_repository import EyeTestRepository

    captured = {}

    class FakeRepo(EyeTestRepository):
        def __init__(self):  # bypass BaseRepository __init__ (no DB)
            pass

        def update(self, _id, data):
            captured.clear()
            captured.update(data)
            return True

    repo = FakeRepo()
    # with findings
    repo.complete_test("T1", {"sph": "-1"}, {"sph": "-1"},
                       clinical_findings={"va_right_unaided": "6/6"})
    assert captured["clinical_findings"] == {"va_right_unaided": "6/6"}
    # without findings -> key absent
    repo.complete_test("T2", {"sph": "-1"}, {"sph": "-1"})
    assert "clinical_findings" not in captured
