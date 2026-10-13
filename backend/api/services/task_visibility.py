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

# Roles that see every task of the shops they reach.
_SHOP_WIDE_ROLES = {"STORE_MANAGER", "AREA_MANAGER"}

# The marker POST /tasks stamps on a task a person raised by hand. Only it
# makes assigned_by a "creator" the rule lets follow the task up.
MANUAL = "manual"

# What a task list returns when no status is asked for: every task but the
# cancelled ones (PENDING and any legacy status included). THE task total
# counts exactly this, so the total always equals the list behind it.
NOT_CANCELLED = {"status": {"$nin": ["CANCELLED", "cancelled", "CANCELED", "canceled"]}}


def visibility_filter(current_user: dict) -> Dict[str, Any]:
    """The tasks ``current_user`` may see, as a Mongo filter. ``{}`` = all."""
    # Roles compared case-insensitively, here and on the task (a legacy row
    # may say "cashier").
    roles = sorted({str(r).strip().upper() for r in (current_user.get("roles") or []) if r})
    is_cross, stores = user_store_scope({**current_user, "roles": roles})
    if is_cross:
        return {}
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
    return {"$or": seen} if seen else {"task_id": {"$in": []}}


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
