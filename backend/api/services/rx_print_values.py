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
