"""Regression (audit P1, clinic C3): a FINALIZED Rx must expose the canonical
sph/cyl/add that print + POS read, even though the 4-version model stores
sphere/cylinder/addition -- otherwise a finalized Rx printed blank. And
progression must compute across docs of either field shape."""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-key-rx-shape")

from api.services.prescription_versions import (  # noqa: E402
    mirror_final_to_top_level,
    progression_diffs,
)


def test_mirror_exposes_canonical_sph_cyl_add():
    doc = {
        "versions": {
            "final": {
                "right_eye": {"sphere": "-2.25", "cylinder": "-1.00", "axis": 90, "addition": "2.00"},
                "left_eye": {"sphere": "-1.50", "axis": 180},
                "pd": 62,
            }
        }
    }
    re = mirror_final_to_top_level(doc)["right_eye"]
    # canonical keys the print card + POS read
    assert re["sph"] == "-2.25" and re["cyl"] == "-1.00" and re["add"] == "2.00" and re["axis"] == 90
    # aliases retained so progression (sphere/cylinder) still resolves
    assert re["sphere"] == "-2.25" and re["cylinder"] == "-1.00"


def test_progression_spans_mixed_field_shapes():
    hist = [
        {
            "prescription_id": "A",
            "created_at": "2025-01-01",
            "right_eye": {"sph": "-1.00", "cyl": "-0.50"},  # create-path shape
            "left_eye": {},
        },
        {
            "prescription_id": "B",
            "created_at": "2026-01-01",
            "right_eye": {"sphere": "-1.75", "cylinder": "-0.50"},  # version-path shape
            "left_eye": {},
        },
    ]
    d = progression_diffs(hist)[0]["right_eye"]
    assert d["sphere_delta"] == -0.75 and d["cylinder_delta"] == 0.0
