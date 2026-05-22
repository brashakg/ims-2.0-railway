"""
IMS 2.0 - Task escalation chain (role ladder)
=============================================
Resolve *who* an SLA-breached task escalates to, by climbing the org
hierarchy:

    worker (any) -> STORE_MANAGER -> AREA_MANAGER -> ADMIN -> SUPERADMIN

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

from typing import Any, Callable, Dict, List, Optional

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


def _authority(roles: Any) -> int:
    """Highest authority rank among a user's roles (worker == 1)."""
    best = 1
    for r in roles or []:
        best = max(best, _RANK.get(str(r).strip().upper(), 1))
    return best


def next_rung_role(current_roles: Any) -> Optional[str]:
    """Return the role to escalate TO given the current owner's roles.

    None means the owner is already at the top (SUPERADMIN) -- nowhere left
    to escalate."""
    auth = _authority(current_roles)
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
) -> Optional[Dict[str, Any]]:
    """Find the next person up the ladder to own a breached task.

    ``find_by_role(role, store_id)`` returns active users with that role
    (store_id None => global). Returns the chosen user dict, or None if the
    chain is exhausted (no one above, or no users configured at all).

    Climbs past empty rungs: e.g. a store with no AREA_MANAGER escalates
    straight to ADMIN. Never returns the current assignee."""
    assignee_user = assignee_user or {}
    assignee_id = assignee_user.get("user_id")
    target_role = next_rung_role(assignee_user.get("roles"))

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
