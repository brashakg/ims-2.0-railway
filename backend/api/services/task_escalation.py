"""
IMS 2.0 - Task escalation chain (role ladder)
=============================================
Resolve *who* an SLA-breached task escalates to, by climbing the org
hierarchy:

    worker (any) -> STORE_MANAGER -> AREA_MANAGER -> ADMIN -> SUPERADMIN
    CATALOG_MANAGER -> ADMIN -> SUPERADMIN

The decision of *whether* to escalate lives in ``task_sla.should_escalate``;
this module decides the *target*. Store-scoped rungs (STORE_MANAGER,
AREA_MANAGER) are resolved against the task's store; ADMIN/SUPERADMIN are
global. If a rung has no eligible user covering the store, we climb to the
next rung up so a breach is never silently dropped.

The resolver takes a ``find_by_role(role, store_id) -> list[user]`` callable
rather than a repository, so it is pure-ish and trivially testable, and so
both the API (UserRepository.find_by_role) and the TASKMASTER agent (a raw
collection query) can drive it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

# Ascending authority. Anyone not listed is a "worker" (rank 1).
_RANK: Dict[str, int] = {
    "SUPERADMIN": 5,
    "ADMIN": 4,
    "AREA_MANAGER": 3,
    "STORE_MANAGER": 2,
}

# Rungs we escalate *to*, lowest first. (Workers escalate to STORE_MANAGER.)
ESCALATION_RUNGS: List[str] = ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"]

# Rungs that are scoped to a single store/area (resolved with the store id).
_STORE_SCOPED = {"STORE_MANAGER", "AREA_MANAGER"}

# Roles that work for the legal entity, not for one shop: a shop's manager can
# neither open their work (a catalogue manager's is Catalogue > Needs review)
# nor do it, so a breach of theirs goes to the admins -- handing it to the
# shop's store manager would leave it with nobody who can act on it.
_ENTITY_ROLES = {"CATALOG_MANAGER"}


def _authority(roles: Any) -> int:
    """Highest authority rank among a user's roles (worker == 1)."""
    best = 1
    for r in roles or []:
        best = max(best, _RANK.get(str(r).strip().upper(), 1))
    return best


def next_rung_role(current_roles: Any, category: Any = None) -> Optional[str]:
    """Return the role to escalate TO given the current owner's roles.

    A catalogue task (category "Catalogue") held by a catalogue manager goes
    to the admins whatever else that person is: a store or area manager can
    open neither Needs review nor the product editor. Their other tasks climb
    the ladder of their highest rank.

    None means the owner is already at the top (SUPERADMIN) -- nowhere left
    to escalate."""
    auth = _authority(current_roles)
    entity = {str(r).strip().upper() for r in current_roles or []} & _ENTITY_ROLES
    if entity and (auth == 1 or (str(category or "").strip().lower() == "catalogue" and auth < 4)):
        return "ADMIN"
    if auth >= 5:  # SUPERADMIN
        return None
    if auth == 4:  # ADMIN -> SUPERADMIN
        return "SUPERADMIN"
    if auth == 3:  # AREA_MANAGER -> ADMIN
        return "ADMIN"
    if auth == 2:  # STORE_MANAGER -> AREA_MANAGER
        return "AREA_MANAGER"
    return "STORE_MANAGER"  # worker -> STORE_MANAGER


def resolve_escalation_target(
    find_by_role: Callable[[str, Optional[str]], List[Dict[str, Any]]],
    store_id: Optional[str],
    assignee_user: Optional[Dict[str, Any]],
    category: Any = None,
) -> Optional[Dict[str, Any]]:
    """Find the next person up the ladder to own a breached task.

    ``find_by_role(role, store_id)`` returns active users with that role
    (store_id None => global). Returns the chosen user dict, or None if the
    chain is exhausted (no one above, or no users configured at all).

    Climbs past empty rungs: e.g. a store with no AREA_MANAGER escalates
    straight to ADMIN. Never returns the current assignee."""
    assignee_user = assignee_user or {}
    assignee_id = assignee_user.get("user_id")
    target_role = next_rung_role(assignee_user.get("roles"), category)

    # Guard against pathological loops (max 4 rungs in the ladder).
    for _ in range(len(ESCALATION_RUNGS) + 1):
        if not target_role:
            return None
        scoped = target_role in _STORE_SCOPED
        try:
            candidates = find_by_role(target_role, store_id if scoped else None) or []
        except Exception:
            candidates = []
        for c in candidates:
            if c.get("user_id") and c.get("user_id") != assignee_id:
                return c
        # Nobody at this rung covers the store -- climb one more.
        target_role = next_rung_role([target_role])

    return None


def merge_into_twin(
    find_one: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
    task: Dict[str, Any],
    target: Optional[Dict[str, Any]],
    *,
    by: str,
    now: datetime,
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """One thing told to several people at once -- a task each, sharing an
    ``escalation_group`` (the catalogue managers' tasks for one held receipt)
    -- climbs to ONE task at the next rung. When the person a breached task
    would go to already holds an open task of its group, the breached one is
    closed into that task instead of handed over a second time: returns the
    (fields to set, history entry) that close it, else None (escalate as
    usual). Both escalation engines -- TASKMASTER's tick and
    POST /tasks/auto-escalate-overdue -- call this. A failed lookup escalates:
    a duplicate beats a lost breach."""
    group = task.get("escalation_group")
    uid = (target or {}).get("user_id")
    if not group or not uid:
        return None
    try:
        twin = find_one(
            {
                "escalation_group": group,
                "assigned_to": uid,
                "status": {"$in": ["OPEN", "IN_PROGRESS", "ESCALATED"]},
                "task_id": {"$ne": task.get("task_id")},
            }
        )
    except Exception:  # noqa: BLE001
        return None
    if not twin:
        return None
    # A person reads this note: their name, never a raw user id (R1-103).
    who = target.get("full_name") or target.get("username") or target.get("name") or uid
    note = f"Already with {who} as task {twin.get('task_id')}."
    return (
        {
            "status": "COMPLETED",
            "completed_at": now,
            "completed_by": by,
            "completion_notes": note,
            "updated_at": now,
        },
        {"action": "completed", "by": by, "notes": note, "at": now},
    )
