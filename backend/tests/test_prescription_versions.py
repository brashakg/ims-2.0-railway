"""
IMS 2.0 — 4-version prescription model tests
==============================================

Verifies the May 2026 clinical-workflow upgrade: each visit captures
4 distinct Rx states (before_testing / after_testing / manual / final)
in addition to the legacy single-Rx flow which keeps working unchanged.

Twelve scenarios:
  1. backfill_versions_from_top_level: legacy doc → synthetic versions.final
  2. backfill: doc that already has versions → no-op
  3. merge_version: writes the named slot
  4. merge_version: rejects unknown version_name
  5. merge_version: 409-ish (raises) when status=finalized
  6. can_finalize: requires `final` with both eyes populated
  7. can_finalize: returns False if already finalized
  8. mirror_final_to_top_level: copies final into top-level right/left/pd
  9. mirror_final_to_top_level: stamps status=finalized + finalized_at
 10. progression_diffs: 3 visits → 2 deltas, signed correctly
 11. progression_diffs: empty / single-visit input → empty deltas
 12. _eye_delta: missing field on either side → None (not 0)
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_backfill_legacy_to_synthetic_versions():
    from api.services.prescription_versions import backfill_versions_from_top_level
    legacy = {
        "prescription_id": "P-1",
        "right_eye": {"sphere": -1.50, "cylinder": -0.25, "axis": 90, "addition": None},
        "left_eye":  {"sphere": -1.75, "cylinder": -0.50, "axis": 85, "addition": None},
        "pd": 62,
        "created_at": "2026-04-01T10:00:00Z",
        "optometrist_id": "u-opto",
    }
    out = backfill_versions_from_top_level(legacy)
    assert out["versions"]["before_testing"] is None
    assert out["versions"]["after_testing"] is None
    assert out["versions"]["manual"] is None
    final = out["versions"]["final"]
    assert final is not None
    assert final["right_eye"]["sphere"] == -1.50
    assert final["pd"] == 62
    assert final["source"] == "optometrist_signoff"


def test_backfill_noop_when_versions_present():
    from api.services.prescription_versions import backfill_versions_from_top_level
    doc_with_versions = {
        "prescription_id": "P-2",
        "versions": {
            "before_testing": {"right_eye": {"sphere": -2.0}, "pd": 60},
            "after_testing": None,
            "manual": None,
            "final": None,
        },
        "right_eye": {"sphere": -2.0},  # also has top-level (mirrored at finalize time)
    }
    out = backfill_versions_from_top_level(doc_with_versions)
    # No synthesis — versions.before_testing preserved
    assert out["versions"]["before_testing"]["right_eye"]["sphere"] == -2.0


def test_merge_version_writes_named_slot():
    from api.services.prescription_versions import merge_version
    doc = {"prescription_id": "P-1", "status": "in_progress"}
    new_doc = merge_version(
        doc, "before_testing",
        {"right_eye": {"sphere": -1.0}, "left_eye": {"sphere": -1.25}, "pd": 60,
         "source": "auto_ref"},
        captured_by="u-opto",
    )
    assert new_doc["versions"]["before_testing"]["right_eye"]["sphere"] == -1.0
    assert new_doc["versions"]["before_testing"]["captured_by"] == "u-opto"
    # Other slots untouched
    assert new_doc["versions"].get("after_testing") is None


def test_merge_version_rejects_unknown_version():
    from api.services.prescription_versions import merge_version
    doc = {"status": "in_progress"}
    with pytest.raises(ValueError, match="version_name"):
        merge_version(doc, "post_op", {}, captured_by="u-1")


def test_merge_version_blocks_finalized_doc():
    from api.services.prescription_versions import merge_version
    doc = {"status": "finalized", "versions": {"final": {"right_eye": {"sphere": -1.0}}}}
    with pytest.raises(ValueError, match="finalized"):
        merge_version(doc, "manual", {"right_eye": {"sphere": -2.0}}, captured_by="u-1")


def test_can_finalize_requires_final_with_both_eyes():
    from api.services.prescription_versions import can_finalize
    # Missing final entirely → False
    assert can_finalize({"status": "in_progress", "versions": {"final": None}}) is False
    # Final present but missing left_eye → False
    assert can_finalize({
        "status": "in_progress",
        "versions": {"final": {"right_eye": {"sphere": -1.0}}},
    }) is False
    # Final with both eyes → True
    assert can_finalize({
        "status": "in_progress",
        "versions": {"final": {"right_eye": {"sphere": -1.0}, "left_eye": {"sphere": -1.25}}},
    }) is True


def test_can_finalize_false_when_already_finalized():
    from api.services.prescription_versions import can_finalize
    assert can_finalize({
        "status": "finalized",
        "versions": {"final": {"right_eye": {"sphere": -1}, "left_eye": {"sphere": -1}}},
    }) is False


def test_mirror_final_copies_into_top_level():
    from api.services.prescription_versions import mirror_final_to_top_level
    doc = {
        "status": "in_progress",
        "versions": {
            "final": {
                "right_eye": {"sphere": -2.50, "cylinder": -0.75, "axis": 180},
                "left_eye": {"sphere": -2.75, "cylinder": -0.50, "axis": 175},
                "pd": 62,
            },
        },
    }
    out = mirror_final_to_top_level(doc)
    assert out["right_eye"]["sphere"] == -2.50
    assert out["left_eye"]["axis"] == 175
    assert out["pd"] == 62


def test_mirror_final_stamps_status_and_timestamp():
    from api.services.prescription_versions import mirror_final_to_top_level
    doc = {
        "status": "in_progress",
        "versions": {"final": {"right_eye": {"sphere": -1.0}, "left_eye": {"sphere": -1.0}, "pd": 60}},
    }
    out = mirror_final_to_top_level(doc)
    assert out["status"] == "finalized"
    assert out["finalized_at"] is not None


def test_progression_diffs_three_visits():
    """3 visits over a year → 2 deltas, sphere getting more negative
    by 0.25 each visit (myopia progression)."""
    from api.services.prescription_versions import progression_diffs
    history = [
        {
            "prescription_id": "P-1",
            "visit_at": "2025-04-01T10:00:00Z",
            "right_eye": {"sphere": -1.50, "cylinder": -0.25, "axis": 90, "addition": None},
            "left_eye": {"sphere": -1.75, "cylinder": -0.50, "axis": 85, "addition": None},
        },
        {
            "prescription_id": "P-2",
            "visit_at": "2025-10-01T10:00:00Z",
            "right_eye": {"sphere": -1.75, "cylinder": -0.25, "axis": 90, "addition": None},
            "left_eye": {"sphere": -2.00, "cylinder": -0.50, "axis": 85, "addition": None},
        },
        {
            "prescription_id": "P-3",
            "visit_at": "2026-04-01T10:00:00Z",
            "right_eye": {"sphere": -2.00, "cylinder": -0.25, "axis": 90, "addition": None},
            "left_eye": {"sphere": -2.25, "cylinder": -0.50, "axis": 85, "addition": None},
        },
    ]
    deltas = progression_diffs(history)
    assert len(deltas) == 2
    # First delta: sphere -1.50 → -1.75 = -0.25
    assert deltas[0]["right_eye"]["sphere_delta"] == -0.25
    assert deltas[0]["left_eye"]["sphere_delta"] == -0.25
    assert deltas[0]["from_prescription_id"] == "P-1"
    assert deltas[0]["to_prescription_id"] == "P-2"
    # Second delta: same -0.25 progression
    assert deltas[1]["right_eye"]["sphere_delta"] == -0.25


def test_progression_diffs_handles_empty_or_single():
    from api.services.prescription_versions import progression_diffs
    assert progression_diffs([]) == []
    assert progression_diffs([{"prescription_id": "P-1", "right_eye": {}}]) == []


def test_eye_delta_returns_none_when_field_missing():
    from api.services.prescription_versions import _eye_delta
    delta = _eye_delta(
        {"sphere": -1.0, "cylinder": -0.5},  # axis missing
        {"sphere": -1.25, "cylinder": -0.5, "axis": 90},
    )
    assert delta["sphere_delta"] == -0.25
    assert delta["cylinder_delta"] == 0.0
    assert delta["axis_delta"] is None  # one side missing → None, NOT 90
