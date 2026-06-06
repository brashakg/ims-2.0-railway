"""BUG-117d: a non-zero cylinder requires an axis (1-180) -- an un-grindable Rx
otherwise. BUG-117c: prism magnitude must be 0-10 and base in {UP,DOWN,IN,OUT}.
Enforced on EyeData, the shared per-eye model used by every Rx write path."""
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from api.routers.prescriptions import EyeData  # noqa: E402


# ---- BUG-117d: cyl requires axis ----
def test_cyl_without_axis_rejected():
    with pytest.raises(ValidationError):
        EyeData(cyl="-2.00")  # no axis


def test_cyl_with_axis_ok():
    e = EyeData(cyl="-2.00", axis=90)
    assert e.cyl == "-2.00" and e.axis == 90


def test_plano_cyl_without_axis_ok():
    # cyl 0 (plano) needs no axis.
    assert EyeData(cyl="0").axis is None


def test_no_cyl_ok():
    assert EyeData(sph="-1.00").axis is None


# ---- BUG-117c: prism magnitude + base ----
def test_valid_prism_and_base():
    e = EyeData(prism="2", base="UP")
    assert e.prism == "2" and e.base == "UP"


def test_prism_over_range_rejected():
    with pytest.raises(ValidationError):
        EyeData(prism="15")


def test_prism_non_numeric_rejected():
    with pytest.raises(ValidationError):
        EyeData(prism="lots")


def test_bad_base_direction_rejected():
    with pytest.raises(ValidationError):
        EyeData(base="SIDEWAYS")


def test_base_case_insensitive():
    assert EyeData(base="down").base == "down"  # accepted (validated case-insensitively)
