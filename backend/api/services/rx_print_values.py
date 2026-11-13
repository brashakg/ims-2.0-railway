"""
IMS 2.0 - absence rules for values printed on a patient's Rx card
==================================================================

ONE definition of "this cell carries nothing the patient should be shown",
shared by every Rx print renderer.

It lives in services/ rather than inside a router precisely because there is
more than ONE patient-facing card: `routers/prescriptions.py` renders the
spectacle and contact-lens cards, `routers/clinical.py` renders the clinic Rx
card, and `PrescriptionPrint.tsx` renders the React A5 card. The same screen
(PrescriptionsPage) offers more than one of them. The rule drifting between
those siblings is exactly how a card reading "PD: None" reached a patient --
so the rule gets one home and every renderer imports it.

The junk tokens are the LITERAL strings produced by stringifying an empty value
in Python ("None" -- what `str(data.right_eye.get("pd", ""))` wrote before
#969), in JavaScript ("null" / "undefined"), or from a NaN. They arrive from
legacy rows, CSV/Excel imports, integrations and device feeds, so the print
layer defends itself rather than trusting every writer upstream.

CLINICALLY LOAD-BEARING: a genuine 0 / 0.0 / "0" is a REAL prescription value
-- a cylinder of 0, an axis of 0 and a prism of 0 each mean something specific.
Every check here is an explicit emptiness test and NEVER a truthiness test.
`if not value` / `x or y` would silently erase real clinical data from a
patient's card, which is worse than printing junk.

PURE (no I/O). ASCII-only source (Windows cp1252 safe).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

# Compared case-insensitively AFTER stripping, so "  None  ", "NULL" and a
# whitespace-only cell are all caught.
ABSENT_RX_TOKENS = frozenset({"", "none", "null", "undefined", "nan"})


def is_absent_rx_value(value: Any) -> bool:
    """True when an Rx cell carries nothing the patient should be shown.

    A genuine 0 is NOT absent. See the module docstring.
    """
    if value is None:
        return True
    # NaN is the only value that is not equal to itself. The JavaScript guard
    # treats NaN as absent, so the Python twin must too -- otherwise the two
    # sides of the same rule disagree and one card prints a literal "nan".
    if isinstance(value, (float, Decimal)):
        return value != value
    if isinstance(value, str):
        return value.strip().lower() in ABSENT_RX_TOKENS
    return False


def first_present_rx_value(*values: Any) -> Any:
    """First value that is actually PRESENT, else None.

    Use this instead of `a or b` when falling back between Rx sources: `or`
    swallows a real 0 (a PD of 0, a cylinder of 0) and also lets a junk string
    win, because a non-empty string is truthy.
    """
    for value in values:
        if not is_absent_rx_value(value):
            return value
    return None


def rx_text_or(value: Any, fallback: str = "-") -> str:
    """Render a value for a card, substituting `fallback` when it is absent.

    `dict.get(key, default)` is NOT a substitute: the Rx write paths store the
    key with a None value when a field is left blank, so the default never
    fires and the card printed the literal "None".
    """
    if is_absent_rx_value(value):
        return fallback
    return str(value)


# ---------------------------------------------------------------------------
# THE display renderers for an Rx power / axis
# ---------------------------------------------------------------------------
# These moved here from routers/clinical.py (which still re-exports them, so
# every existing import and its pinned tests keep working). They live beside
# the absence rule because they answer the SAME question for the same cards,
# and because the surfaces that needed them most were not importing them at
# all: the workshop LAB JOB-CARD -- the traveler the lens grinder reads -- and
# the customer's Rx portal both interpolated the stored string raw, so a power
# saved as "4" or "4.0" was handed to the lab and to the patient with no sign.
#
# CLINICAL-CRITICAL: the sign of a power is the difference between two
# different lenses. A plus power printed without its plus is not a cosmetic
# defect on a job card.


def format_rx_value(v) -> str:
    """Render an Rx power for display/print.

    Rules (Rx business rules, IMS 2.0):
      - None / missing / empty -> "" (blank cell)
      - exactly 0 (any numeric form) -> "Plano"
      - otherwise -> explicit sign + 2 decimals, e.g. "-1.25", "+0.50"

    PURE function (no I/O) so it can be unit-tested in isolation. Accepts ints,
    floats, or numeric strings (the prescriptions collection stores eye powers
    as strings, e.g. {"sph": "-1.25"}). Non-numeric junk -> "" rather than a
    crash, keeping the print endpoint fail-soft.
    """
    if v is None:
        return ""
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return ""
        try:
            num = float(s)
        except ValueError:
            return ""
    else:
        try:
            num = float(v)
        except (TypeError, ValueError):
            return ""
    # Normalise -0.0 and tiny float noise to a clean zero -> "Plano".
    if abs(num) < 0.005:
        return "Plano"
    sign = "+" if num > 0 else "-"
    return f"{sign}{abs(num):.2f}"


def format_axis_value(v) -> str:
    """Render an AXIS (1-180 whole degrees). Blank for None/missing, else int.

    An axis is a MERIDIAN, not a power: it is never signed. Keeping it in the
    same module as format_rx_value is what stops a future edit from "helpfully"
    giving it a plus.
    """
    if v is None:
        return ""
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return ""
        try:
            return str(int(round(float(s))))
        except ValueError:
            return ""
    try:
        return str(int(round(float(v))))
    except (TypeError, ValueError):
        return ""


def rx_power_or(value, fallback: str = "-") -> str:
    """A dioptric power for a printed card, or `fallback` when nothing was
    recorded.

    The difference from `rx_text_or` is the whole point of this function:
    rx_text_or prints the STORED STRING VERBATIM, so a power stored as "4"
    printed as "4". This one renders it, so it prints "+4.00".

    A recorded zero renders as "Plano" -- a real finding, never the fallback.
    """
    if is_absent_rx_value(value):
        return fallback
    rendered = format_rx_value(value)
    # Junk that survived the absence check (e.g. "abc") renders as "" here;
    # fall back rather than printing an empty cell.
    return rendered if rendered != "" else fallback


def rx_axis_or(value, fallback: str = "-") -> str:
    """An AXIS for a printed card, or `fallback` when nothing was recorded."""
    if is_absent_rx_value(value):
        return fallback
    rendered = format_axis_value(value)
    return rendered if rendered != "" else fallback


# ---------------------------------------------------------------------------
# Binocular IPD and the recorded issue date (audit L2-CLIN-F02 / L2-CLIN-F03)
# ---------------------------------------------------------------------------
# Every Rx writer stores these under different keys: the clinical exam writes
# `ipd` + `test_date`, the counter/POS create writes `ipd` + `prescription_date`
# (and mirrors test_date), the 4-version finalize mirrors a top-level `pd`, and
# older imports carry `pupillary_distance`. Each reader picked ONE spelling, so
# the same saved exam printed without its IPD and reached the patient portal as
# "Issued -". One reader rule, used by every print door and the portal.
#
# The binocular IPD is NEVER derived from the per-eye monocular PDs (and never
# the reverse): a missing measurement stays missing.


def rx_binocular_pd(rx: Any) -> Any:
    """The recorded binocular (inter-pupillary) distance, else None."""
    if not isinstance(rx, dict):
        return None
    return first_present_rx_value(
        rx.get("ipd"), rx.get("pd"), rx.get("pupillary_distance")
    )


def rx_issue_date_value(rx: Any) -> Any:
    """The recorded issue date of an Rx (raw stored value), else None.

    `prescription_date` (counter/POS Rx) first, then `test_date` (the clinical
    exam's Rx). `created_at` is deliberately NOT here: it is when the row was
    written, not a date a clinician recorded.
    """
    if not isinstance(rx, dict):
        return None
    return first_present_rx_value(
        rx.get("prescription_date"), rx.get("test_date"), rx.get("testDate")
    )


def rx_date_ymd(raw: Any) -> str:
    """A stored Rx date as the IST calendar day 'YYYY-MM-DD' ('' when absent).

    BUG-104 VALUE rule, shared by the print doors: the clinical exam stamps
    `test_date` as a UTC instant ('...+00:00'), so slicing [:10] printed
    YESTERDAY for an exam recorded 00:00-05:30 IST. Offset-bearing strings and
    datetimes are shifted to IST; a naive string is a business date already
    and passes through; an unparseable string falls back to its first 10 chars.
    """
    from datetime import datetime

    from ..utils.ist import ist_date_str

    if is_absent_rx_value(raw):
        return ""
    if isinstance(raw, datetime):
        return ist_date_str(raw)
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError:
            return raw[:10]
        if parsed.tzinfo is not None:
            return ist_date_str(parsed)
        return parsed.strftime("%Y-%m-%d")
    return str(raw)
