"""
IMS 2.0 - Task SLA + escalation timing
======================================
Pure, deterministic SLA logic for the task escalation engine. No DB, no IO
-- everything here is a function of the task dict + the current time, so it
is trivially unit-testable and identical across workers.

Two independent clocks per task:

  * ack clock     -- an OPEN (still-unacknowledged) task must be picked up
                     within ``ack_minutes`` of creation, else it breaches.
  * overdue clock -- any non-terminal task that passes ``due_at`` plus
                     ``grace_minutes`` breaches.

The Standard matrix below is the seeded default; Phase 2 lets SUPERADMIN
override it via settings and pass the override in as ``sla_config``.

All times are in minutes. Datetimes are compared naive-local (the task
router writes ``datetime.now()``); ISO strings (legacy / agent-written)
are tolerated and coerced.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

# priority -> {ack_minutes, grace_minutes}
# Standard matrix: P0 15m/30m, P1 1h/4h, P2 4h/1d, P3 1d/3d, P4 3d/7d
DEFAULT_SLA: Dict[str, Dict[str, int]] = {
    "P0": {"ack_minutes": 15, "grace_minutes": 30},
    "P1": {"ack_minutes": 60, "grace_minutes": 240},
    "P2": {"ack_minutes": 240, "grace_minutes": 1440},
    "P3": {"ack_minutes": 1440, "grace_minutes": 4320},
    "P4": {"ack_minutes": 4320, "grace_minutes": 10080},
}

# Statuses past which escalation never applies.
TERMINAL_STATUSES = {"COMPLETED", "CANCELLED"}


def canon_status(value: Any) -> str:
    """Normalize any task status to the canonical UPPERCASE_SNAKE form.

    Tolerates legacy lowercase ('open'), spaces ('in progress'), and a few
    synonyms ('done' -> COMPLETED, 'canceled' -> CANCELLED)."""
    if not value:
        return ""
    s = str(value).strip().upper().replace(" ", "_").replace("-", "_")
    synonyms = {
        "DONE": "COMPLETED",
        "COMPLETE": "COMPLETED",
        "CANCELED": "CANCELLED",
        "INPROGRESS": "IN_PROGRESS",
    }
    return synonyms.get(s, s)


def canon_source(value: Any) -> str:
    """Normalize a task source/type to SYSTEM | USER | SOP."""
    if not value:
        return "USER"
    s = str(value).strip().upper()
    mapping = {"MANUAL": "USER", "USER": "USER", "SOP": "SOP", "SYSTEM": "SYSTEM"}
    return mapping.get(s, "USER")


def sla_for(priority: Any, sla_config: Optional[dict] = None) -> Dict[str, int]:
    """Return the {ack_minutes, grace_minutes} row for a priority.

    Falls back to the P3 row for unknown priorities. ``sla_config`` (when
    provided) takes precedence over DEFAULT_SLA but still falls back to the
    default row if it omits the requested priority."""
    p = str(priority or "P3").strip().upper()
    if sla_config and p in sla_config:
        row = sla_config[p]
        # tolerate partial overrides
        base = DEFAULT_SLA.get(p, DEFAULT_SLA["P3"])
        return {
            "ack_minutes": int(row.get("ack_minutes", base["ack_minutes"])),
            "grace_minutes": int(row.get("grace_minutes", base["grace_minutes"])),
        }
    return DEFAULT_SLA.get(p, DEFAULT_SLA["P3"])


def _as_dt(value: Any) -> Optional[datetime]:
    """Coerce datetime | ISO-string | None to a naive datetime, or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.replace(tzinfo=None) if dt.tzinfo else dt
        except ValueError:
            return None
    return None


def should_escalate(
    task: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
    sla_config: Optional[dict] = None,
) -> Tuple[bool, str]:
    """Decide whether a task has breached SLA and must escalate.

    Returns ``(should_escalate, reason)``. Pure -- pass ``now`` for tests.
    A task that is COMPLETED/CANCELLED/ESCALATED never escalates here
    (re-escalation up further levels is Phase 2's concern)."""
    status = canon_status(task.get("status"))
    if status in TERMINAL_STATUSES or status == "ESCALATED":
        return False, ""

    now = now or datetime.now()
    if now.tzinfo:
        now = now.replace(tzinfo=None)

    sla = sla_for(task.get("priority", "P3"), sla_config)

    # Overdue clock -- due_at (canonical) or due_date (legacy fallback).
    due = _as_dt(task.get("due_at")) or _as_dt(task.get("due_date"))
    if due is not None and now >= due + timedelta(minutes=sla["grace_minutes"]):
        return True, f"Overdue past SLA grace ({sla['grace_minutes']}m)"

    # Ack clock -- only while still OPEN (unacknowledged).
    if status == "OPEN":
        created = _as_dt(task.get("created_at"))
        if created is not None and now >= created + timedelta(
            minutes=sla["ack_minutes"]
        ):
            return True, f"Not acknowledged within SLA ({sla['ack_minutes']}m)"

    return False, ""
