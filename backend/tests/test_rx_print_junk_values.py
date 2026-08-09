"""
Patient-facing Rx card must never print junk placeholder strings.
=================================================================

A blank per-eye PD used to be persisted as the LITERAL string "None" by the
pre-#969 expression ``str(data.right_eye.get("pd", ""))``. #969 fixed NEW
writes, but existing rows still carry it -- and the same shape can arrive at
any time from a CSV/Excel import, an integration, or a device feed. The PRINT
path is therefore the durable place to defend: whatever is in the database, a
patient must never be handed a card that reads "PD: None".

These tests drive the REAL card renderers (`_build_spectacle_print_html` /
`_build_cl_print_html`) -- the exact HTML that goes to the patient -- not the
private `_cell` helper.

THE CRITICAL DISTINCTION: a genuine 0 (or the string "0") is a CLINICALLY REAL
value. An axis of 0, a cylinder of 0, a prism of 0 all mean something. The
emptiness check must not be truthiness-based, or it erases real clinical data
from a patient's card -- worse than the bug being fixed.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.routers.prescriptions import (  # noqa: E402
    _build_cl_print_html,
    _build_spectacle_print_html,
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _row_cells(html: str, eye_label: str) -> list:
    """Pull the rendered <td> values of one eye row out of the card HTML.

    Returns the data cells only (the leading <strong>RE</strong> label cell is
    dropped), in column order.
    """
    marker = f"<strong>{eye_label}</strong></td>"
    start = html.index(marker) + len(marker)
    end = html.index("</tr>", start)
    return [c.strip() for c in re.findall(r"<td>(.*?)</td>", html[start:end])]


def _spectacle(right=None, left=None, **extra):
    rx = {
        "prescription_number": "RX-TEST-0001",
        "prescription_date": "2026-08-10T00:00:00",
        "expiry_date": "2027-08-10T00:00:00",
        "right_eye": right or {},
        "left_eye": left or {},
    }
    rx.update(extra)
    return _build_spectacle_print_html(rx)


def _contact_lens(right=None, left=None):
    return _build_cl_print_html(
        {
            "prescription_number": "RX-TEST-0002",
            "prescription_date": "2026-08-10T00:00:00",
            "expiry_date": "2027-08-10T00:00:00",
            "cl_right": right or {},
            "cl_left": left or {},
        }
    )


# Column order of the spectacle card: SPH CYL AXIS ADD PD
SPH, CYL, AXIS, ADD, PD = range(5)


# --------------------------------------------------------------------------
# the reported bug: a stored "None" reaches the patient
# --------------------------------------------------------------------------
def test_spectacle_card_renders_dash_for_stored_none_string_pd():
    """A record carrying the string "None" must print '-', never "None"."""
    html = _spectacle(
        right={"sph": "-1.25", "cyl": "-0.50", "axis": "90", "add": "", "pd": "None"},
        left={"sph": "-1.00", "cyl": "-0.75", "axis": "85", "add": "", "pd": "None"},
    )
    assert _row_cells(html, "RE")[PD] == "-"
    assert _row_cells(html, "LE")[PD] == "-"
    # And the junk token must be nowhere on the card at all.
    assert ">None<" not in html


@pytest.mark.parametrize("junk", ["None", "NONE", "null", "undefined", "NaN", "   ", ""])
def test_spectacle_card_treats_every_junk_token_as_absent(junk):
    """"null"/"undefined"/whitespace-only are junk from JS + import paths."""
    html = _spectacle(right={"sph": junk, "cyl": junk, "axis": junk, "pd": junk})
    cells = _row_cells(html, "RE")
    assert cells[SPH] == "-"
    assert cells[CYL] == "-"
    assert cells[AXIS] == "-"
    assert cells[PD] == "-"


# --------------------------------------------------------------------------
# THE CRITICAL DISTINCTION: 0 is real clinical data, not absence
# --------------------------------------------------------------------------
def test_spectacle_card_keeps_a_genuine_numeric_zero():
    """A cylinder of 0 / axis of 0 / add of 0 is REAL. Never blank it."""
    html = _spectacle(right={"sph": 0, "cyl": 0, "axis": 0, "add": 0, "pd": 0})
    cells = _row_cells(html, "RE")
    assert cells == ["0", "0", "0", "0", "0"], cells
    assert "-" not in cells


def test_spectacle_card_keeps_a_genuine_string_zero():
    """Stored Rx values are a mix of numbers and strings; "0" is still real."""
    html = _spectacle(right={"sph": "0", "cyl": "0", "axis": "0", "add": "0", "pd": "0"})
    cells = _row_cells(html, "RE")
    assert cells == ["0", "0", "0", "0", "0"], cells


def test_spectacle_card_keeps_zero_point_zero_and_float_zero():
    """0.0 and "0.00" must survive too -- they are prescribable powers."""
    html = _spectacle(right={"sph": 0.0, "cyl": "0.00", "axis": 0, "add": "0.0", "pd": 0})
    cells = _row_cells(html, "RE")
    assert cells[SPH] == "0.0"
    assert cells[CYL] == "0.00"
    assert cells[AXIS] == "0"
    assert cells[ADD] == "0.0"
    assert cells[PD] == "0"


def test_a_missing_field_is_still_a_dash():
    """The pre-existing None/absent behaviour must not regress."""
    html = _spectacle(right={})
    assert _row_cells(html, "RE") == ["-", "-", "-", "-", "-"]


# --------------------------------------------------------------------------
# the same guard covers the contact-lens card (same `_cell` helper)
# --------------------------------------------------------------------------
def test_free_text_fields_never_print_the_word_none():
    """`prescription.get("lens_recommendation", "N/A")` did NOT protect this.

    The create path always WRITES those keys, with a None value when the
    optometrist left them blank, so dict.get's default never fired and every
    such card read "Lens Recommendation: None" to the patient.
    """
    html = _spectacle(
        lens_recommendation=None,
        coating_recommendation=None,
        remarks=None,
    )
    assert "Lens Recommendation:</strong> N/A" in html
    assert "Coating:</strong> N/A" in html
    assert "Remarks:</strong> -" in html
    assert "None" not in html


def test_free_text_fields_still_print_real_content():
    html = _spectacle(
        lens_recommendation="Progressive 1.6",
        coating_recommendation="Blue-cut AR",
        remarks="Review in 6 months",
    )
    assert "Progressive 1.6" in html
    assert "Blue-cut AR" in html
    assert "Review in 6 months" in html


def test_contact_lens_header_fields_never_print_junk():
    html = _build_cl_print_html(
        {
            "prescription_number": "RX-TEST-0003",
            "prescription_date": "2026-08-10T00:00:00",
            "expiry_date": "2027-08-10T00:00:00",
            "cl_brand": "None",
            "cl_series": None,
            "modality": "undefined",
            "color": "None",
            "remarks": None,
            "cl_right": {},
            "cl_left": {},
        }
    )
    assert "None" not in html
    assert "undefined" not in html
    # A junk colour must drop the whole Color row, not print "Color: None".
    assert "Color:" not in html


def test_contact_lens_card_renders_dash_for_junk_and_keeps_zero():
    html = _contact_lens(
        right={
            "cl_power": "None",
            "cl_cyl": 0,
            "cl_axis": "null",
            "cl_add": "0",
            "base_curve": "  ",
            "diameter": "14.2",
        }
    )
    cells = _row_cells(html, "RE")
    # POWER CYL AXIS ADD BC DIA
    assert cells == ["-", "0", "-", "0", "-", "14.2"], cells
    assert ">None<" not in html
