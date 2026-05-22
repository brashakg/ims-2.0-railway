"""
SOP daily-checklist tests (Tasks/SOP Phase 4)
=============================================
Pure tests of services.sop_checklist -- the merge/progress/toggle logic that
backs daily SOP-checklist completion tracking. No DB.
"""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from api.services.sop_checklist import (  # noqa: E402
    DEFAULT_SOP_TEMPLATES,
    apply_item_toggle,
    completion_status,
    default_template_steps,
    merge_checklist,
    progress_of,
)

STEPS = [
    {"step_number": 1, "instruction": "Disarm alarm", "warning": None},
    {"step_number": 2, "instruction": "Lights on", "warning": None},
    {"step_number": 3, "instruction": "Boot POS", "warning": "wait 30s"},
]


# --- progress_of ------------------------------------------------------------


def test_progress_empty():
    assert progress_of([]) == {"done": 0, "total": 0, "percent": 0}


def test_progress_partial():
    items = [{"completed": True}, {"completed": False}, {"completed": True}, {"completed": False}]
    assert progress_of(items) == {"done": 2, "total": 4, "percent": 50}


def test_progress_full():
    items = [{"completed": True}, {"completed": True}]
    assert progress_of(items) == {"done": 2, "total": 2, "percent": 100}


# --- merge_checklist --------------------------------------------------------


def test_merge_no_completion_all_unticked():
    merged, prog = merge_checklist(STEPS, None)
    assert len(merged) == 3
    assert all(m["completed"] is False for m in merged)
    assert merged[2]["warning"] == "wait 30s"
    assert prog == {"done": 0, "total": 3, "percent": 0}


def test_merge_overlays_completion_state():
    completion = [
        {"step_number": 1, "completed": True, "completed_by": "u1", "completed_at": "t"},
        {"step_number": 3, "completed": True, "completed_by": "u2", "completed_at": "t2"},
    ]
    merged, prog = merge_checklist(STEPS, completion)
    by = {m["step_number"]: m for m in merged}
    assert by[1]["completed"] is True and by[1]["completed_by"] == "u1"
    assert by[2]["completed"] is False
    assert by[3]["completed"] is True
    assert prog == {"done": 2, "total": 3, "percent": 67}


def test_merge_drops_orphan_completion_entries():
    # A completion for a step the template no longer has is ignored.
    completion = [{"step_number": 99, "completed": True}]
    merged, prog = merge_checklist(STEPS, completion)
    assert len(merged) == 3
    assert prog["done"] == 0


# --- apply_item_toggle ------------------------------------------------------


def test_toggle_adds_new_completed_entry():
    items = apply_item_toggle([], STEPS, 2, True, by="u1", at="2026-05-22")
    assert len(items) == 1
    assert items[0] == {
        "step_number": 2, "completed": True, "completed_by": "u1", "completed_at": "2026-05-22",
    }


def test_toggle_uncheck_clears_attribution():
    items = [{"step_number": 1, "completed": True, "completed_by": "u1", "completed_at": "t"}]
    out = apply_item_toggle(items, STEPS, 1, False, by="u2", at="t2")
    assert out[0]["completed"] is False
    assert out[0]["completed_by"] is None
    assert out[0]["completed_at"] is None


def test_toggle_keeps_other_items_and_sorts():
    items = [{"step_number": 3, "completed": True}]
    out = apply_item_toggle(items, STEPS, 1, True, by="u1", at="t")
    assert [i["step_number"] for i in out] == [1, 3]


def test_toggle_drops_steps_not_in_template():
    items = [{"step_number": 99, "completed": True}]
    out = apply_item_toggle(items, STEPS, 1, True, by="u1", at="t")
    assert [i["step_number"] for i in out] == [1]  # orphan 99 dropped


# --- completion_status ------------------------------------------------------


def test_status_in_progress_when_partial():
    assert completion_status({"done": 1, "total": 3, "percent": 33}) == "IN_PROGRESS"


def test_status_completed_when_all_done():
    assert completion_status({"done": 3, "total": 3, "percent": 100}) == "COMPLETED"


def test_status_in_progress_when_empty_template():
    assert completion_status({"done": 0, "total": 0, "percent": 0}) == "IN_PROGRESS"


# --- defaults ---------------------------------------------------------------


def test_default_template_steps_numbering():
    steps = default_template_steps(["a", "b", "c"])
    assert [s["step_number"] for s in steps] == [1, 2, 3]
    assert steps[0]["instruction"] == "a"
    assert steps[1]["warning"] is None


def test_default_sop_templates_are_well_formed():
    assert len(DEFAULT_SOP_TEMPLATES) == 3
    titles = {t["title"] for t in DEFAULT_SOP_TEMPLATES}
    assert titles == {"Opening Checklist", "Closing Checklist", "Stock Count"}
    for t in DEFAULT_SOP_TEMPLATES:
        assert t["frequency"] == "DAILY"
        assert len(t["steps"]) >= 3
