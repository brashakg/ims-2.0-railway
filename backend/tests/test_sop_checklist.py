"""
SOP daily-checklist tests (Tasks/SOP Phase 4)
=============================================
Pure tests of services.sop_checklist -- the merge/progress/toggle logic that
backs daily SOP-checklist completion tracking. No DB.
"""

import io
import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from api.services.sop_checklist import (  # noqa: E402
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


def test_no_built_in_sop_templates_exist_any_more():
    """The inverse of the test that used to live here.

    There WAS a built-in starter set, and it was not a harmless placeholder:
    the daily generator fell back to it when a store had configured nothing, so
    a shop was ISSUED REAL TASKS from invented procedure - verify a Rs 5,000
    opening float, retain Rs 5,000 overnight, collect a minimum 50% advance.
    Figures nobody at this company chose, given to staff as the company's rules,
    and indistinguishable from policy the owner wrote.

    Owner ruling 2026-09-03: delete them. An empty checklist is honest; a
    fabricated one is worse than none. This test fails if anyone reintroduces a
    default set."""
    import api.services.sop_checklist as mod

    assert not hasattr(mod, "DEFAULT_SOP_TEMPLATES"), (
        "a built-in SOP template set is back - staff cannot tell invented "
        "procedure from the store's own"
    )
    # Scan CODE, not comments. The note recording WHY these were deleted has
    # to quote the figures it deleted, and a comment is never issued to a
    # member of staff - only a template string can be.
    src = io.open(mod.__file__, encoding="utf-8").read()
    code = "\n".join(
        ln for ln in src.splitlines() if not ln.lstrip().startswith("#")
    )
    for invented in ("5,000", "5000", "starting float", "minimum 50%"):
        assert invented not in code, f"fabricated SOP figure {invented!r} is back"
