"""IMS 2.0 - the screen's category->tax table, checked against the SERVER

A CROSS-LANGUAGE differential probe. It reads the real TypeScript file --
``frontend/src/constants/gst.ts`` -- and asks the server's own resolver to
price every HSN that table quotes. If the two ever answer differently, this
fails, naming the category.

WHY IT READS THE .ts INSTEAD OF A COPY
--------------------------------------
The defect this round is about is a rule with two implementations. The obvious
"fix" -- transcribe the server's table into a TS test and assert equality --
adds a THIRD copy and pins nothing: change a rate in ``gst_rates.py`` and the
stored rate moves (it feeds ``_HSN_RATES`` -> ``resolve_gst_rate_strict``)
while the frontend preview and the hand-copied literal both stay green. That is
the same bug class one file over. So nothing is transcribed here: the .ts file
IS the input.

WHAT THIS DOES NOT CLAIM
------------------------
Only the RATE is checked. Two categories (SMTSG / SMTFR) deliberately carry a
different HSN CODE on the screen (900410) than the server's own category
default (852580); both are 18%, so the tax agrees, and moving the code is a
data decision about 35 live products, not a rename. Pinned below so it stays
visible rather than becoming an omission.
"""

from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-category-tax")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services.gst_rates import (  # noqa: E402
    GST_CATEGORY_TABLE,
    resolve_gst_rate_strict,
)

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GST_TS = os.path.join(_REPO, "frontend", "src", "constants", "gst.ts")

_ENTRY_RE = re.compile(
    r"^\s*([A-Z][A-Z0-9_]*):\s*\{\s*hsn:\s*'(\d+)',\s*rate:\s*(\d+(?:\.\d+)?)\s*\}",
    re.MULTILINE,
)


def _parse_category_tax():
    """{CATEGORY: (hsn, rate)} straight out of the TypeScript source."""
    with open(GST_TS, "r", encoding="utf-8") as fh:
        src = fh.read()
    start = src.index("const CATEGORY_TAX = {")
    end = src.index("\n};", start)
    return {
        m.group(1): (m.group(2), float(m.group(3)))
        for m in _ENTRY_RE.finditer(src[start:end])
    }


CATEGORY_TAX = _parse_category_tax()


def test_the_probe_actually_read_the_table():
    """A regex that matched nothing would make every test below vacuous.

    The screen's table carries every canonical category name, every seed
    plural/alt form and every short picker code -- forty-odd entries. Anything
    near zero means the .ts moved and this file is no longer probing anything.
    """
    assert (
        len(CATEGORY_TAX) >= 40
    ), f"parsed only {len(CATEGORY_TAX)} entries from {GST_TS}"
    assert CATEGORY_TAX["FRAME"] == ("900311", 5.0)
    assert CATEGORY_TAX["CCL"] == ("900130", 5.0)


@pytest.mark.parametrize("category", sorted(CATEGORY_TAX))
def test_every_category_the_screen_prices_matches_what_the_hsn_settles(category):
    hsn, screen_rate = CATEGORY_TAX[category]
    server_rate, missing = resolve_gst_rate_strict(hsn)
    assert missing is None, (
        f"{category}: the screen quotes HSN {hsn} but the server cannot price "
        f"it ({missing}). A category the screen offers must be priceable."
    )
    assert server_rate == screen_rate, (
        f"{category}: screen says {screen_rate}% on HSN {hsn}, server says "
        f"{server_rate}%. This is the Colour Contact Lens bug returning."
    )


@pytest.mark.parametrize(
    "category", sorted(set(CATEGORY_TAX) & set(GST_CATEGORY_TABLE))
)
def test_the_two_category_tables_agree_on_the_rate(category):
    """Where BOTH sides know a category by name, the rate must be the same.

    This is the direct FE<->BE comparison; the test above is the stronger one
    (screen rate vs what its own HSN settles), but this catches a category the
    server prices by NAME and the screen prices differently.
    """
    _screen_hsn, screen_rate = CATEGORY_TAX[category]
    server_hsn, server_rate = GST_CATEGORY_TABLE[category]
    assert screen_rate == float(server_rate), (
        f"{category}: screen {screen_rate}% vs server {server_rate}% "
        f"(server HSN {server_hsn})"
    )


def test_the_known_hsn_code_difference_is_still_only_smart_eyewear():
    """The one place the CODE differs, declared. Rate agrees, so no money moves.

    If a second category starts differing, this fails and someone has to decide
    on purpose instead of discovering it in a GSTR-1 reconciliation.
    """
    differing = {
        cat: (CATEGORY_TAX[cat][0], GST_CATEGORY_TABLE[cat][0])
        for cat in set(CATEGORY_TAX) & set(GST_CATEGORY_TABLE)
        if CATEGORY_TAX[cat][0] != str(GST_CATEGORY_TABLE[cat][0])
    }
    assert differing == {
        "SMARTGLASSES": ("900410", "852580"),
        "SMTSG": ("900410", "852580"),
        "SMTFR": ("900410", "852580"),
    }, differing
