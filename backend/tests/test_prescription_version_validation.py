"""Regression: the 4-version Rx PATCH path must enforce the SAME clinical range
+ 0.25-step validation as POST/PUT (audit P2). It previously ran no range checks,
so an out-of-range power (SPH +99, CYL -50, AXIS 999) could be saved into a
version and mirrored straight to top-level right_eye on finalize."""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-key-for-rx-version")

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from api.routers.prescriptions import VersionEyeData  # noqa: E402


def test_valid_version_eye_accepted():
    e = VersionEyeData(sphere=-2.25, cylinder=-1.0, axis=90, addition=2.0)
    assert e.sphere == -2.25 and e.axis == 90 and e.addition == 2.0


def test_plano_and_blank_accepted():
    VersionEyeData(sphere=0, cylinder=0)  # plano (0 passes, like the string path)
    VersionEyeData()  # all None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sphere": 99},     # > +20
        {"sphere": -25},    # < -20
        {"cylinder": -50},  # < -6
        {"addition": 9.0},  # > +3.50
        {"sphere": 1.3},    # off the 0.25-diopter grid
        {"axis": 999},      # > 180 (structural Field bound)
        {"axis": 0},        # < 1
    ],
)
def test_out_of_range_rejected(kwargs):
    with pytest.raises(ValidationError):
        VersionEyeData(**kwargs)
