"""IMS 2.0 - "which state is this?" must have ONE answer in the purchase chain
==============================================================================
A DIFFERENTIAL probe, not a unit test of one function. Every implementation
that answers the same question is fed the same inputs and the answers diffed.
That is what caught the defect this file pins:

``rtv_debit_note.state_code_of`` used to be a private copy of an older version
of the orders helper. It never gained the bare-leading-2-digit fallback, so
"27-Maharashtra" -- the GST portal's own display form, and what an imported
vendor row carries -- resolved to "" on the debit note while resolving to "27"
on the purchase order and the purchase bill. "" reads as "state unknown",
which takes the conservative intra-state default, so the debit note reversing
an IGST purchase came out CGST + SGST. Two implementations, two verdicts, one
invoice chain. The copy was DELETED (not synchronised); these tests fail if it
comes back in any spelling.

The second half of the file pins the parsers that are still SEPARATE, with
their measured disagreements, so nobody writes "this is the single place a
state code is parsed" again without re-measuring. It is a tripwire, not an
endorsement: when one of those modules is folded in, this table is what tells
you to update the claim in org_validation.resolve_state_code's docstring.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-state-parser")
os.environ.setdefault("ENVIRONMENT", "test")

from api.routers import orders as orders_router  # noqa: E402
from api.services import gstn_export, itc_reconcile, print_legal  # noqa: E402
from api.services import purchase_invoice_engine as pinv  # noqa: E402
from api.services import rtv_debit_note as rtv  # noqa: E402
from api.services.org_validation import resolve_state_code  # noqa: E402

# Every implementation reachable from the PO -> bill -> debit-note -> sale
# chain. Enumerated by searching for the RULE, not the function name:
# grep -rn "def .*state_code|def .*inter.*state|\[:2\]" backend/api
CHAIN_PARSERS = {
    "org_validation.resolve_state_code": lambda v: resolve_state_code(v),
    "purchase_invoice_engine.state_code_of": lambda v: pinv.state_code_of(v),
    "orders._invoice_state_code": lambda v: orders_router._invoice_state_code(v),
    "rtv_debit_note.state_code_of": lambda v: rtv.state_code_of(v),
}

# input -> the one answer all four must give.
CHAIN_CASES = {
    "27-Maharashtra": "27",  # GST portal display form  <-- the defect
    "20-Jharkhand": "20",  # ditto                      <-- the defect
    "27 - Maharashtra": "27",
    "27": "27",
    "MH": "27",
    "Maharashtra": "27",
    "jharkhand": "20",
    "27AAAAA0000A1Z5": "27",  # full GSTIN
    "1900": "",  # a digit run, not a labelled code
    "190001": "",  # a Srinagar PIN, not West Bengal
    "2712": "",
    "88-Nowhere": "",  # 88 is not a GST state code
    "xyz": "",
    "": "",
}


@pytest.mark.parametrize("value,expected", sorted(CHAIN_CASES.items()))
def test_every_parser_in_the_invoice_chain_gives_the_same_answer(value, expected):
    answers = {name: fn(value) for name, fn in CHAIN_PARSERS.items()}
    disagreed = {n: a for n, a in answers.items() if a != expected}
    assert not disagreed, (
        f"{value!r} should resolve to {expected!r} everywhere in the purchase "
        f"chain; these disagreed: {disagreed}"
    )


def test_a_longer_digit_run_is_not_a_state_code():
    """The guard the fallback comment promises actually exists.

    Before 2026-08-27 the comment claimed "1900" resolved to nothing while the
    code returned "19" (West Bengal). A PIN code in a state field became a
    place of supply. Two digits must be the whole value, or be followed by a
    non-digit.
    """
    assert resolve_state_code("190001") == ""
    assert resolve_state_code("1900") == ""
    assert resolve_state_code("2712") == ""
    # ... without breaking the form the fallback exists for.
    assert resolve_state_code("27-Maharashtra") == "27"
    assert resolve_state_code("27 - Maharashtra") == "27"
    assert resolve_state_code("27") == "27"
    # A truncated GSTIN still leads with its state: digits, then a letter.
    assert resolve_state_code("27ABCDE1234F1Z") == "27"
    # "99" IS a real entry (Centre Jurisdiction), so it resolves. This guard is
    # about digit runs; INDIAN_STATE_CODES is what rejects a fake state.
    assert resolve_state_code("99-Nowhere") == "99"
    assert resolve_state_code("88-Nowhere") == ""


# ---------------------------------------------------------------------------
# The requirement, at the door: a debit note reversing an inter-state purchase
# ---------------------------------------------------------------------------

MH_VENDOR = {"name": "Luxottica India", "state": "27-Maharashtra"}
JH_SELLER = {"name": "BV Opticals", "state": "20-Jharkhand"}
LINE = [{"sku": "RB1", "qty": 2, "unit_cost": 1000.0, "gst_rate": 18.0}]


def _note(vendor, seller):
    return rtv.build_debit_note(
        {"store_id": "BV-01"}, vendor, LINE, "DN/BV/2026-27/000001", seller=seller
    )


def test_debit_note_on_a_portal_form_vendor_charges_igst_like_the_bill_did():
    note = _note(MH_VENDOR, JH_SELLER)
    assert note["vendor"]["state_code"] == "27"
    assert note["seller"]["state_code"] == "20"
    assert note["is_inter_state"] is True
    assert note["place_of_supply"] == "27"
    ln = note["lines"][0]
    assert ln["igst_paise"] == 36000
    assert ln["cgst_paise"] == 0 and ln["sgst_paise"] == 0
    # ... and the purchase ORDER / purchase BILL parser reads the same rows the
    # same way, which is the whole point of deleting the local copy.
    assert pinv.state_code_of("27-Maharashtra") == "27"
    assert pinv.state_code_of("20-Jharkhand") == "20"


def test_debit_note_within_one_state_still_splits_cgst_sgst():
    note = _note(MH_VENDOR, {"name": "BV Mumbai", "state": "27-Maharashtra"})
    assert note["is_inter_state"] is False
    ln = note["lines"][0]
    assert ln["cgst_paise"] == 18000
    assert ln["sgst_paise"] == 18000
    assert ln["igst_paise"] == 0


def test_an_unanswerable_state_takes_the_documented_intra_default():
    """What the deleted copy was masking, tested explicitly.

    A vendor whose state field holds something no parser can settle (here a PIN
    code, which used to resolve to "19") must fall to the documented safe
    intra-state default -- not to a fabricated state.
    """
    note = _note({"name": "Unknown Optics", "state": "190001"}, JH_SELLER)
    assert note["vendor"]["state_code"] == ""
    assert note["is_inter_state"] is False
    assert note["place_of_supply"] == "20"  # falls back to the seller's state
    ln = note["lines"][0]
    assert ln["igst_paise"] == 0
    assert ln["cgst_paise"] + ln["sgst_paise"] == 36000


# ---------------------------------------------------------------------------
# TRIPWIRE: the parsers that are still separate, and how they differ
# ---------------------------------------------------------------------------
# Not in the PO -> bill -> debit-note chain, so not fixed here, but MEASURED so
# the claim in org_validation.resolve_state_code's docstring stays honest. If a
# row here starts failing, someone consolidated a parser -- good; update that
# docstring's list and this table together.
#
# gstn_export._state_code takes (state_name, gstin="") and its callers only
# ever hand it a NAME, so the "" it returns for a GSTIN below is its signature,
# not a bug. Its own 38-entry state-name table is the drift risk.

SURVIVORS = {
    "print_legal._state_code_of": lambda v: print_legal._state_code_of(v),
    "itc_reconcile._state_code": lambda v: itc_reconcile._state_code(v),
    "gstn_export._state_code": lambda v: gstn_export._state_code(v),
}

KNOWN_DIVERGENCE = {
    #  input             (chain, print_legal, itc_reconcile, gstn_export)
    "27-Maharashtra": ("27", "27", "27", ""),
    "Maharashtra (27)": ("", "27", "27", ""),
    "MH": ("27", "", "", ""),
    "Maharashtra": ("27", "", "", "27"),
    "27AAAAA0000A1Z5": ("27", "27", "27", ""),
    "190001": ("", "19", "19", ""),
}


@pytest.mark.parametrize("value,expected", sorted(KNOWN_DIVERGENCE.items()))
def test_the_state_parsers_outside_the_invoice_chain_still_answer_differently(
    value, expected
):
    got = (resolve_state_code(value),) + tuple(fn(value) for fn in SURVIVORS.values())
    assert got == expected, (
        f"{value!r}: the measured answers moved. Someone changed or "
        f"consolidated a state parser -- update the survivor list in "
        f"org_validation.resolve_state_code and this table together."
    )


# ---------------------------------------------------------------------------
# The other half of the same rule: who halves the tax
# ---------------------------------------------------------------------------
# "which state is this?" decides IGST vs CGST+SGST; the splitter decides how
# CGST+SGST is halved. Same disease, so the same differential treatment. The
# ONE thing that must hold everywhere is that the odd paisa is neither dropped
# nor invented. Which HEAD it lands on is not guaranteed and is not asserted
# here -- gst_rates.split_gst says so in its own docstring.


def test_every_splitter_keeps_cgst_plus_sgst_equal_to_the_tax():
    from api.routers.transfers import _tax_split
    from api.services.gst_rates import split_gst
    from api.services.rtv_debit_note import _split_line_tax

    bad = []
    for paise in range(1, 2001):
        tax = paise / 100.0
        c, sg, ig = split_gst(tax, False)
        if round(c + sg, 2) != round(tax, 2) or ig != 0.0:
            bad.append(("gst_rates.split_gst", tax, c, sg))
        c, sg, ig = _tax_split(tax, False)
        if round(c + sg, 2) != round(tax, 2) or ig != 0.0:
            bad.append(("transfers._tax_split", tax, c, sg))
        # rtv mints in integer paise: 100% of `paise` is `paise` of tax.
        d = _split_line_tax(paise, 100.0, False)
        if d["cgst_paise"] + d["sgst_paise"] != paise or d["igst_paise"]:
            bad.append(("rtv._split_line_tax", tax, d["cgst_paise"], d["sgst_paise"]))
    assert bad == [], bad[:5]

    # The naive form the reporting screens still use, for contrast: halving
    # BOTH heads loses the odd paisa. Not fixed here (it is a change to those
    # documents), but measured, so nobody records it as equivalent.
    naive_lost = [
        t / 100.0
        for t in range(1, 2001)
        if round(round(t / 200.0, 2) * 2, 2) != round(t / 100.0, 2)
    ]
    assert len(naive_lost) == 1000, len(naive_lost)


def test_inter_state_is_all_igst_in_every_splitter():
    from api.routers.transfers import _tax_split
    from api.services.gst_rates import split_gst
    from api.services.rtv_debit_note import _split_line_tax

    assert split_gst(224.85, True) == (0.0, 0.0, 224.85)
    assert _tax_split(224.85, True) == (0.0, 0.0, 224.85)
    d = _split_line_tax(22485, 100.0, True)
    assert (d["cgst_paise"], d["sgst_paise"], d["igst_paise"]) == (0, 0, 22485)
