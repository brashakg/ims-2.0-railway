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

import uuid
from typing import Any, Dict, List, Optional, Tuple


def progress_of(items: List[Dict[str, Any]]) -> Dict[str, int]:
    """{done, total, percent} for a list of merged checklist items."""
    total = len(items or [])
    done = sum(1 for i in (items or []) if i.get("completed"))
    percent = round(done / total * 100) if total else 0
    return {"done": done, "total": total, "percent": percent}


def _belongs(step: Dict[str, Any], tick: Dict[str, Any]) -> bool:
    """Is this stored tick one given for this step? By the step's stable
    ``step_id`` (audit R28). A legacy tick (no step_id) belongs only to the
    step that held that number when ids were first assigned
    (``legacy_step_number``), or to a template never re-saved since."""
    sid = step.get("step_id")
    if tick.get("step_id"):
        return bool(sid) and tick["step_id"] == sid
    if sid:
        legacy = step.get("legacy_step_number")
        return legacy is not None and tick.get("step_number") == legacy
    return tick.get("step_number") == step.get("step_number")


def _tick_for(step: Dict[str, Any], completion_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The latest stored tick that belongs to THIS step, or {}.

    Audit R28: ticks used to be joined on the mutable ``step_number``, so a
    reorder handed the tick for "count display pieces" to "verify safe is
    locked". A tick whose recorded instruction differs from the step's
    current wording is not shown as done either: the stored row is kept as
    evidence, it just signs nothing it was not given for."""
    tick: Dict[str, Any] = {}
    for ci in completion_items:
        if _belongs(step, ci):
            tick = ci
    if tick.get("instruction") is not None and tick["instruction"] != step.get("instruction"):
        return {}
    return tick


def merge_checklist(
    steps: List[Dict[str, Any]],
    completion_items: Optional[List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Overlay stored completion state onto the template's steps.

    ``steps`` come from the SOP template ({step_id, step_number, instruction,
    warning}). ``completion_items`` are the persisted ticks. Returns
    (merged_items, progress). Steps with no tick of their own render as
    not-completed; ticks for steps no longer in the template are not shown."""
    items = list(completion_items or [])
    merged: List[Dict[str, Any]] = []
    for s in steps or []:
        ci = _tick_for(s, items)
        merged.append(
            {
                "step_id": s.get("step_id"),
                "step_number": s.get("step_number"),
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

    Pure. The tick is written against the step's identity (step_id) and
    records the instruction it was given for (audit R28). Every other stored
    tick, including one for a step since removed or reworded, is kept as it
    was: completion evidence is never rewritten by a later toggle."""
    step = next((s for s in steps or [] if s.get("step_number") == step_number), None)
    if step is None:
        return list(completion_items or [])
    # Replace this step's own earlier tick; keep everything else (other
    # steps, removed steps, ticks given for an older wording).
    kept = [
        dict(ci) for ci in (completion_items or [])
        if not (_belongs(step, ci) and ci.get("instruction") in (None, step.get("instruction")))
    ]
    kept.append(
        {
            "step_id": step.get("step_id"),
            "step_number": step_number,
            "instruction": step.get("instruction"),
            "completed": bool(completed),
            "completed_by": by if completed else None,
            "completed_at": at if completed else None,
        }
    )
    return kept


def assign_step_ids(
    new_steps: List[Dict[str, Any]],
    old_steps: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Give every saved step a stable ``step_id`` (audit R28).

    A step the editor sends back with its known step_id keeps it; one without
    an id keeps the id of the old step with the same instruction (so a pure
    reorder from any client keeps identity); anything else is new work and
    gets a new id. A step that existed before ids did remembers the number
    it had then (``legacy_step_number``) so ticks stored against that number
    stay with it -- and with nothing else."""
    old = [dict(s) for s in (old_steps or [])]
    used: set = set()

    def _claim(match) -> Optional[Dict[str, Any]]:
        for i, o in enumerate(old):
            if i not in used and match(o):
                used.add(i)
                return o
        return None

    out: List[Dict[str, Any]] = []
    for s in new_steps or []:
        s = dict(s)
        s.pop("legacy_step_number", None)
        sid = s.get("step_id")
        o = _claim(lambda o: o.get("step_id") == sid) if sid else None
        if o is None:
            o = _claim(lambda o: o.get("instruction") == s.get("instruction"))
        if o is None:
            s["step_id"] = uuid.uuid4().hex[:12]
        else:
            s["step_id"] = o.get("step_id") or uuid.uuid4().hex[:12]
            legacy = o.get("legacy_step_number") if o.get("step_id") else o.get("step_number")
            if legacy is not None:
                s["legacy_step_number"] = legacy
        out.append(s)
    return out


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
