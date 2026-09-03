"""
IMS 2.0 - SOP daily checklist logic
===================================
Pure helpers for daily SOP-checklist completion tracking. A checklist is a
*run* of an SOP template at a store on a date: the template provides the
steps; a `sop_completions` doc records which steps are ticked.

Everything here is pure (template steps + stored completion items in, merged
view / progress / toggled items out) so it unit-tests without a DB.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def progress_of(items: List[Dict[str, Any]]) -> Dict[str, int]:
    """{done, total, percent} for a list of merged checklist items."""
    total = len(items or [])
    done = sum(1 for i in (items or []) if i.get("completed"))
    percent = round(done / total * 100) if total else 0
    return {"done": done, "total": total, "percent": percent}


def merge_checklist(
    steps: List[Dict[str, Any]],
    completion_items: Optional[List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Overlay stored completion state onto the template's steps.

    ``steps`` come from the SOP template ({step_number, instruction, warning}).
    ``completion_items`` are the persisted ticks. Returns (merged_items,
    progress). Steps with no stored entry render as not-completed; stored
    entries for steps no longer in the template are dropped."""
    by_step = {ci.get("step_number"): ci for ci in (completion_items or [])}
    merged: List[Dict[str, Any]] = []
    for s in steps or []:
        sn = s.get("step_number")
        ci = by_step.get(sn, {})
        merged.append(
            {
                "step_number": sn,
                "instruction": s.get("instruction"),
                "warning": s.get("warning"),
                "completed": bool(ci.get("completed", False)),
                "completed_by": ci.get("completed_by"),
                "completed_at": ci.get("completed_at"),
            }
        )
    return merged, progress_of(merged)


def apply_item_toggle(
    completion_items: Optional[List[Dict[str, Any]]],
    steps: List[Dict[str, Any]],
    step_number: int,
    completed: bool,
    *,
    by: Optional[str],
    at: Any,
) -> List[Dict[str, Any]]:
    """Return the updated completion-items list with one step toggled.

    Pure. Keeps only steps that still exist in the template (so a template
    edit can't leave orphan ticks), sorted by step_number. When unchecking,
    completed_by / completed_at are cleared."""
    valid = {s.get("step_number") for s in (steps or [])}
    items: Dict[Any, Dict[str, Any]] = {
        ci.get("step_number"): dict(ci) for ci in (completion_items or [])
    }
    items[step_number] = {
        "step_number": step_number,
        "completed": bool(completed),
        "completed_by": by if completed else None,
        "completed_at": at if completed else None,
    }
    return [items[k] for k in sorted(items, key=lambda x: (x is None, x)) if k in valid]


def completion_status(progress: Dict[str, int]) -> str:
    """COMPLETED when every step is ticked (and there is at least one), else
    IN_PROGRESS."""
    if progress.get("total", 0) > 0 and progress.get("done", 0) >= progress["total"]:
        return "COMPLETED"
    return "IN_PROGRESS"


# Starter daily SOP templates seeded on demand (opening / closing / stock
# count) so a fresh store has usable checklists out of the box. Mirrors the
# old hard-coded frontend DEFAULT_CHECKLISTS, now persisted + editable.
# DEFAULT_SOP_TEMPLATES removed (owner ruling 2026-09-03).
#
# Four fabricated procedures with invented figures - a Rs 5,000 float, a
# Rs 100 variance threshold, a 50% minimum advance - that were written into the
# database as if they were this company's policy. A staff member reading them
# had no way to know they were a placeholder.
#
# Nothing replaces them here. The real procedures are written by a manager at
# /tasks/sops, and until they are, the checklist shows an empty state saying so.
_SOP_TEMPLATES_DELIBERATELY_ABSENT = True



def default_template_steps(items: List[str]) -> List[Dict[str, Any]]:
    """Turn a flat list of step strings into template step dicts."""
    return [
        {"step_number": i + 1, "instruction": text, "warning": None}
        for i, text in enumerate(items)
    ]
