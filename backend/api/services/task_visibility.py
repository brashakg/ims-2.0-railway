"""
IMS 2.0 - Who sees a task. ONE rule, used by every task read and write.
======================================================================
Owner ruling 2026-10-12 (and 2026-09-03 "assign to PEOPLE, not titles"):

  * a staff member sees -- and may act on -- a task only when it is assigned
    to them (assigned_to = their user id), or to their ROLE at their shop (a
    task addressed to a role, with no person on it yet);
  * whoever RAISED a task by hand also sees it and can follow it up, whatever
    their role: POST /tasks stamps assigned_by + created_via "manual", and
    only that marker counts. A system, SOP or review task never makes anyone
    its creator (the person who pressed a button did not raise it);
  * STORE_MANAGER sees every task of their shop(s);
  * AREA_MANAGER sees every task of their area's shops;
  * ADMIN / SUPERADMIN see all.

"Their shop(s)" is the caller's store reach (token store_ids + active store),
the same reach every other store gate uses (dependencies.user_store_scope).

The rule is a Mongo filter, so a list, a count and a single-task open all run
the SAME rule in the database: a task the caller cannot see is simply not
found (404, never 403, so task ids do not leak). This module only decides who
SEES a task -- who a task is ASSIGNED to stays with task_triggers / escalation.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Optional

from ..dependencies import user_store_scope
from . import catalog_flags
from .user_roles import DEPRECATED_ROLE_ALIASES

# Roles that see every task of the shops they reach.
_SHOP_WIDE_ROLES = {"STORE_MANAGER", "AREA_MANAGER"}

# Who manages tasks (owner ruling 2026-10-13, control over convenience):
# cancel, reassign, hand a task to a named person, issue the day's SOP tasks.
# The rbac rows for /reassign and /auto-generate name exactly these roles.
MANAGER_ROLES = ("STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN")

# The marker POST /tasks stamps on a task a person raised by hand. Only it
# makes assigned_by a "creator" the rule lets follow the task up.
MANUAL = "manual"

# What a task list returns when no status is asked for: every task but the
# cancelled ones (PENDING and any legacy status included). THE task total
# counts exactly this, so the total always equals the list behind it.
NOT_CANCELLED = {"status": {"$nin": ["CANCELLED", "cancelled", "CANCELED", "canceled"]}}


def task_roles(user: dict) -> list:
    """``user``'s roles as every task rule reads them. Upper case (a legacy row
    or token may say "cashier"). A sales cashier is both a sales person and a
    cashier (owner 2026-10-13). Sign-in folds SALES_CASHIER into SALES_STAFF
    (user_roles.DEPRECATED_ROLE_ALIASES), so SALES_STAFF also answers to the
    old name a task may still be addressed to."""
    held = {str(r).strip().upper() for r in (user.get("roles") or []) if r}
    roles = set(held)
    if "SALES_CASHIER" in held:
        roles |= {"SALES_STAFF", "CASHIER"}
    roles |= {old for old, new in DEPRECATED_ROLE_ALIASES.items() if new in held}
    return sorted(roles)


def is_manager(user: dict) -> bool:
    """THE manager test for tasks: the role gate on cancel, reassign, a named
    escalation and the day's SOP tasks, read the same way as visibility."""
    return bool(set(task_roles(user)) & set(MANAGER_ROLES))


def visibility_filter(current_user: dict) -> Dict[str, Any]:
    """The tasks ``current_user`` may see, as a Mongo filter. ``{}`` = all."""
    # Roles compared case-insensitively, here and on the task (task_roles).
    roles = task_roles(current_user)
    is_cross, stores = user_store_scope({**current_user, "roles": roles})
    flags = catalog_flags.handles_flags(current_user)
    if is_cross and flags:
        return {}
    # A catalogue-flag task belongs to no shop: the people who raise and clear
    # flags (catalog_flags.handles_flags) see every one; nobody else sees any.
    if not flags:
        rest = {} if is_cross else _seen(current_user, roles, stores)
        hide = {"payload.kind": {"$ne": catalog_flags.TASK_KIND}}
        return {"$and": [hide, rest]} if rest else hide
    seen = _seen(current_user, roles, stores)
    seen["$or"].append({"payload.kind": catalog_flags.TASK_KIND})
    return seen


def _seen(current_user: dict, roles: list, stores) -> Dict[str, Any]:
    # store_ids is unchecked user data: a None / number / "" in it would make
    # sorted() raise TypeError (a 500 on every task door). Drop it.
    shops = {"$in": sorted({s for s in stores if isinstance(s, str) and s})}
    uid = current_user.get("user_id")
    seen = [{"assigned_to": uid}, {"assigned_by": uid, "created_via": MANUAL}] if uid else []
    if set(roles) & _SHOP_WIDE_ROLES:
        seen.append({"store_id": shops})
    elif roles:
        anyof = "|".join(re.escape(r) for r in roles)
        seen.append({"assigned_to": {"$regex": f"^({anyof})$", "$options": "i"}, "store_id": shops})
    # Nobody to match (no id, no role): see nothing rather than everything.
    return {"$or": seen or [{"task_id": {"$in": []}}]}


def visible(filters: Optional[Dict[str, Any]], current_user: dict) -> Dict[str, Any]:
    """``filters`` narrowed to what ``current_user`` may see. Apply it LAST:
    the result is wrapped in $and, so a caller's own $or survives."""
    vis = visibility_filter(current_user)
    if not vis:
        return dict(filters or {})
    return {"$and": [dict(filters or {}), vis]}


def overdue_filter() -> Dict[str, Any]:
    """Open or in progress and past due (legacy rows carry due_date)."""
    now = datetime.now()
    return {
        "status": {"$in": ["OPEN", "IN_PROGRESS", "open", "in_progress"]},
        "$or": [{"due_at": {"$lt": now}}, {"due_date": {"$lt": now}}],
    }


def task_counts(
    repo: Any, current_user: dict, filters: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Status + overdue counts over the tasks the caller can see. THE task
    count: GET /tasks/summary, /reports/tasks/summary and the reports
    dashboard all read it. Each status count equals GET /tasks?status=<it>,
    and `total` equals GET /tasks with no status (NOT_CANCELLED). Tolerant
    of canonical UPPERCASE and legacy lowercase status rows."""
    base = dict(filters or {})

    def _count(extra: Dict[str, Any]) -> int:
        return repo.count(visible({**base, **extra}, current_user))

    by_status = {
        s: _count({"status": {"$in": [s, s.lower()]}})
        for s in ("OPEN", "IN_PROGRESS", "COMPLETED", "ESCALATED")
    }
    overdue = _count(overdue_filter())
    # Both the nested `summary` (canonical keys) and the flat keys the
    # dashboard cards read directly (open = open + in progress).
    return {
        "summary": by_status,
        "open": by_status["OPEN"] + by_status["IN_PROGRESS"],
        "completed": by_status["COMPLETED"],
        "escalated": by_status["ESCALATED"],
        "overdue": overdue,
        "overdue_count": overdue,
        "escalated_count": by_status["ESCALATED"],
        "total": _count(NOT_CANCELLED),
    }
