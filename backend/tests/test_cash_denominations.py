"""
IMS 2.0 - Cash Count Block (services/cash_denominations.py) tests
=================================================================
The shared shape every denominated cash record in IMS stores. These tests are
written against the REQUIREMENTS, not the implementation:

  * blank is never zero  -- an un-entered breakdown is NOT_CAPTURED with
    ``matches_amount is None``, never a fabricated zero count and never False.
  * the amount is truth  -- a breakdown that does not add up leaves
    ``amount_paisa`` exactly as supplied and merely raises a flag.
  * one ladder            -- the Rs 20 coin exists (both count sheets in the
    app offer it); Rs 2000 does not (RBI withdrew it).
  * (kind, face) is the key -- a Rs 10 note and a Rs 10 coin are different
    money and must never merge in a per-face ledger.

No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import cash_denominations as cd  # noqa: E402


# ===========================================================================
# The ladder
# ===========================================================================


def test_ladder_carries_the_rs20_coin_and_no_rs2000_note():
    rows = cd.denomination_ladder()
    notes = [r["face"] for r in rows if r["kind"] == "note"]
    coins = [r["face"] for r in rows if r["kind"] == "coin"]
    assert notes == [500, 200, 100, 50, 20, 10]
    # The Rs 20 coin is in circulation and BOTH count sheets in the app offer
    # it; the backend grids used to omit it.
    assert coins == [20, 10, 5, 2, 1]
    assert 2000 not in notes
    assert all(r["pieces"] == 0 for r in rows)


def test_face_key_separates_a_note_from_a_coin_of_the_same_face():
    note10 = {"face": 10, "kind": "note", "pieces": 3}
    coin10 = {"face": 10, "kind": "coin", "pieces": 3}
    assert cd.face_key(note10) != cd.face_key(coin10)
    assert cd.face_key(note10) == ("note", 10)
    assert cd.face_key({"face": 50, "kind": "nonsense"}) == ("note", 50)


# ===========================================================================
# Normalisation
# ===========================================================================


def test_normalize_drops_junk_clamps_negatives_and_keeps_order():
    rows = [
        {"face": 500, "pieces": 2},              # kind defaults to note
        {"face": "x", "pieces": 5},              # bad face -> dropped
        {"face": 50, "pieces": -3},              # negative -> 0
        {"face": 5, "pieces": "4", "kind": "COIN"},
        "junk",                                   # not a dict -> dropped
    ]
    out = cd.normalize_rows(rows)
    assert len(out) == 3
    assert out[0] == {
        "face": 500,
        "kind": "note",
        "pieces": 2,
        "line_total_paisa": 500 * 100 * 2,
    }
    assert out[1]["pieces"] == 0 and out[1]["line_total_paisa"] == 0
    assert out[2]["kind"] == "coin" and out[2]["pieces"] == 4


def test_total_paisa_is_integer_exact():
    rows = [
        {"face": 500, "kind": "note", "pieces": 3},
        {"face": 20, "kind": "coin", "pieces": 2},
    ]
    assert cd.total_paisa(rows) == 154000
    assert cd.total_paisa(None) == 0


# ===========================================================================
# Suggestion
# ===========================================================================


def test_suggest_is_greedy_highest_face_first_and_prefers_the_note():
    rows = cd.suggest(76000)  # Rs 760
    assert [(r["face"], r["kind"], r["pieces"]) for r in rows] == [
        (500, "note", 1),
        (200, "note", 1),
        (50, "note", 1),
        (10, "note", 1),
    ]
    assert sum(r["line_total_paisa"] for r in rows) == 76000
    # Rs 20 comes out as a NOTE, not the coin of the same face.
    twenty = cd.suggest(2000)
    assert [(r["face"], r["kind"]) for r in twenty] == [(20, "note")]


def test_suggest_leaves_an_unrepresentable_paise_remainder_uncovered():
    # Rs 10.50 -- there is no 50-paise coin on the ladder. The suggestion must
    # NOT round the money; it covers Rs 10 and the caller's block then flags.
    rows = cd.suggest(1050)
    assert sum(r["line_total_paisa"] for r in rows) == 1000
    block = cd.build_block(rows, 1050, state=cd.STATE_SUGGESTED)
    assert block["amount_paisa"] == 1050       # the money, untouched
    assert block["matches_amount"] is False    # flagged for a human
    assert cd.is_flagged(block) is True


def test_suggest_of_zero_or_junk_is_empty():
    assert cd.suggest(0) == []
    assert cd.suggest(-500) == []
    assert cd.suggest(None) == []
    assert cd.suggest("abc") == []


# ===========================================================================
# The three states -- blank is never zero
# ===========================================================================


def test_nothing_entered_is_not_captured_not_a_zero_count():
    block = cd.build_block(None, 160000)
    assert block["state"] == cd.STATE_NOT_CAPTURED
    assert block["rows"] == []
    assert block["total_paisa"] == 0
    # THE blank-vs-zero assertion: absence is None, never False, never 0-matches.
    assert block["matches_amount"] is None
    assert block["amount_paisa"] == 160000
    assert cd.is_captured(block) is False
    assert cd.is_flagged(block) is False


def test_empty_rows_with_an_explicit_counted_state_stays_counted():
    # "I counted the change and there was none" is a real answer and must not
    # be downgraded to "nobody asked".
    block = cd.build_block([], 0, state=cd.STATE_COUNTED)
    assert block["state"] == cd.STATE_COUNTED
    assert block["matches_amount"] is True
    assert cd.is_captured(block) is True


def test_suggested_state_is_preserved_and_distinguishable_from_counted():
    rows = cd.suggest(40000)
    suggested = cd.build_block(rows, 40000, state=cd.STATE_SUGGESTED)
    counted = cd.build_block(rows, 40000, state=cd.STATE_COUNTED)
    assert suggested["state"] == cd.STATE_SUGGESTED
    assert counted["state"] == cd.STATE_COUNTED
    assert suggested["rows"] == counted["rows"]
    assert cd.is_captured(suggested) and cd.is_captured(counted)


def test_an_unknown_state_string_never_becomes_a_third_kind_of_truth():
    block = cd.build_block([{"face": 100, "kind": "note", "pieces": 1}], 10000, state="VERIFIED")
    assert block["state"] in (cd.STATE_COUNTED, cd.STATE_SUGGESTED, cd.STATE_NOT_CAPTURED)
    assert block["state"] == cd.STATE_COUNTED


# ===========================================================================
# THE MONEY TEST: a breakdown that does not add up never rewrites the amount
# ===========================================================================


def test_a_mismatched_breakdown_flags_and_leaves_the_amount_untouched():
    rows = [{"face": 500, "kind": "note", "pieces": 1}]  # Rs 500 of notes...
    block = cd.build_block(rows, 160000)                 # ...against Rs 1,600
    assert block["amount_paisa"] == 160000               # THE MONEY, unchanged
    assert block["total_paisa"] == 50000                 # what was counted
    assert block["matches_amount"] is False              # the flag
    assert cd.is_flagged(block) is True
    # And nothing was rounded, clamped or back-filled into the rows.
    assert block["rows"][0]["pieces"] == 1
    assert len(block["rows"]) == 1


def test_a_matching_breakdown_is_not_flagged():
    rows = [{"face": 500, "kind": "note", "pieces": 4}]
    block = cd.build_block(rows, 200000)
    assert block["matches_amount"] is True
    assert cd.is_flagged(block) is False


def test_total_paisa_is_derived_and_a_supplied_total_is_ignored():
    # A caller cannot smuggle a total in: it is computed from the rows.
    rows = [{"face": 100, "kind": "note", "pieces": 2, "total_paisa": 999999}]
    block = cd.build_block(rows, 20000)
    assert block["total_paisa"] == 20000
    assert "total_paisa" not in block["rows"][0]


# ===========================================================================
# Drawer counts -- here the COUNT is truth
# ===========================================================================


def test_drawer_block_makes_the_count_the_amount():
    rows = [
        {"face": 500, "kind": "note", "pieces": 4},
        {"face": 20, "kind": "coin", "pieces": 3},
    ]
    block = cd.build_drawer_block(rows)
    assert block["total_paisa"] == 206000
    assert block["amount_paisa"] == 206000
    assert block["matches_amount"] is True
    assert block["state"] == cd.STATE_COUNTED


def test_an_uncounted_drawer_is_not_captured_not_an_empty_drawer():
    block = cd.build_drawer_block(None)
    assert block["state"] == cd.STATE_NOT_CAPTURED
    assert block["matches_amount"] is None
    assert block["amount_paisa"] == 0
    assert cd.is_captured(block) is False


# ===========================================================================
# Per-face ledger
# ===========================================================================


def test_accumulate_adds_and_subtracts_by_kind_and_face():
    ledger: dict = {}
    cd.accumulate(ledger, cd.build_drawer_block([{"face": 500, "kind": "note", "pieces": 10}]), +1)
    cd.accumulate(ledger, cd.build_block([{"face": 500, "kind": "note", "pieces": 3}], 150000), -1)
    cd.accumulate(ledger, cd.build_drawer_block([{"face": 10, "kind": "coin", "pieces": 5}]), +1)
    assert ledger[("note", 500)] == 7
    assert ledger[("coin", 10)] == 5
    assert ("note", 10) not in ledger


def test_a_not_captured_block_contributes_nothing_to_the_ledger():
    ledger: dict = {("note", 500): 4}
    cd.accumulate(ledger, cd.not_captured_block(200000), +1)
    cd.accumulate(ledger, None, +1)
    assert ledger == {("note", 500): 4}


def test_ledger_rows_report_the_discrepancy_at_the_right_face():
    expected = {("note", 500): 10, ("note", 100): 4}
    counted = {("note", 500): 9, ("note", 100): 4}
    rows = cd.ledger_rows(expected, counted)
    by_key = {(r["kind"], r["face"]): r for r in rows}
    # THE SET: every ladder face is present exactly once.
    assert len(rows) == len(cd.NOTE_FACES) + len(cd.COIN_FACES)
    assert by_key[("note", 500)]["difference_pieces"] == -1
    assert by_key[("note", 500)]["difference_paisa"] == -50000
    assert by_key[("note", 100)]["difference_pieces"] == 0
    assert by_key[("coin", 1)]["expected_pieces"] == 0


def test_ledger_rows_keep_an_off_ladder_face_rather_than_dropping_it():
    # A legacy Rs 2000 note physically in the drawer is reported, not edited away.
    rows = cd.ledger_rows({("note", 2000): 1}, {})
    faces = [(r["kind"], r["face"]) for r in rows]
    assert ("note", 2000) in faces
    row = next(r for r in rows if r["face"] == 2000)
    assert row["expected_pieces"] == 1 and row["difference_pieces"] == -1
