"""
IMS 2.0 - Task escalation notifications
=======================================
When a task escalates to a new owner we alert them two ways:

  1. In-app  -- a row in the `notifications` collection (NOTIFICATION_SCHEMA,
                user_id-targeted) that shows in the topbar bell. Always fires.
  2. WhatsApp -- via agents.providers.send_whatsapp to the owner's phone,
                gated by DISPATCH_MODE (off/test/live) AND a quiet-hours
                window (no pings 21:00-09:00) -- EXCEPT P0/P1 which always
                go through because they are genuine emergencies.

The builders + the quiet-hours gate are pure (no IO) so they unit-test
cleanly; notify_escalation() does the two writes and is fail-soft.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Task priority (P0-P4) -> NOTIFICATION_SCHEMA priority enum.
_PRIORITY_MAP = {
    "P0": "URGENT",
    "P1": "HIGH",
    "P2": "NORMAL",
    "P3": "LOW",
    "P4": "LOW",
}

# Quiet hours are now evaluated in IST via the shared agents.quiet_hours guard
# (NOT the server-local clock -- a UTC-hosted box read its quiet window 5h30m
# off, so a low-priority escalation could ping a manager at ~02:30 IST). These
# constants are kept for reference/back-compat; the live check delegates to the
# shared helper so MEGAPHONE + task escalation + the manual send API agree.
QUIET_START_HOUR = 21  # 9 PM IST
QUIET_END_HOUR = 9  # 9 AM IST
# Priorities that bypass quiet hours (true emergencies).
_ALWAYS_WHATSAPP = {"P0", "P1"}


def notification_priority(task_priority: Any) -> str:
    # Known P-codes map; anything missing/unknown -> NORMAL.
    return _PRIORITY_MAP.get(str(task_priority or "").upper(), "NORMAL")


# Trigger keys the Settings -> Notification Templates editor can override. The
# inline strings below remain the hard-coded defaults (and the fallback when no
# saved template is enabled). Owner-edited bodies may use these placeholders:
#   {title} {priority} {reason} {store}
_INAPP_TRIGGER = "TASK_ESCALATION_INAPP"
_WHATSAPP_TRIGGER = "TASK_ESCALATION_WHATSAPP"


def _resolve_escalation_body(trigger: str, default: str, variables: Dict[str, Any]) -> str:
    """Resolve an escalation body through the saved-template resolver, falling
    back to the hard-coded default. Fail-soft: any error -> the default text
    (an escalation is never suppressed by a template problem)."""
    try:
        from api.services.notification_templates import resolve_and_render

        return resolve_and_render(
            template_id=trigger,
            trigger_event=trigger,
            default_content=default,
            variables=variables,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TASK_NOTIFY] template resolve failed, using default: %s", exc)
        try:
            return default.format(**variables)
        except Exception:  # noqa: BLE001
            return default


def build_escalation_notification(
    task: Dict[str, Any], target_user_id: str, reason: str
) -> Dict[str, Any]:
    """Build a NOTIFICATION_SCHEMA in-app notification doc. Pure (the optional
    saved-template lookup is fail-soft and falls back to the inline default)."""
    title = task.get("title") or "Task"
    priority = str(task.get("priority") or "P3").upper()
    variables = {"title": title, "priority": priority, "reason": reason}
    message = _resolve_escalation_body(
        _INAPP_TRIGGER,
        "[{priority}] '{title}' was escalated to you. Reason: {reason}.",
        variables,
    )
    return {
        "notification_id": f"NTF-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}",
        "notification_type": "task_escalation",
        "user_id": target_user_id,
        "title": f"Escalated to you: {title}",
        "message": message,
        "entity_type": "task",
        "entity_id": task.get("task_id"),
        "action_url": "/tasks",
        "channels": ["IN_APP"],
        "priority": notification_priority(priority),
        "status": "SENT",
        "created_at": datetime.now(),
    }


def escalation_whatsapp_text(task: Dict[str, Any], reason: str) -> str:
    """Plain-text WhatsApp body for an escalation. An owner-edited template
    overrides the default when enabled; otherwise the inline default is used."""
    title = task.get("title") or "Task"
    priority = str(task.get("priority") or "P3").upper()
    store = task.get("store_id") or "-"
    variables = {"title": title, "priority": priority, "reason": reason, "store": store}
    return _resolve_escalation_body(
        _WHATSAPP_TRIGGER,
        (
            "IMS escalation [{priority}]\n"
            "Task: {title}\n"
            "Store: {store}\n"
            "Reason: {reason}\n"
            "Open IMS > Tasks to action it."
        ),
        variables,
    )


def whatsapp_allowed(task_priority: Any, *, now: Optional[datetime] = None) -> bool:
    """Quiet-hours gate for WhatsApp. Pure.

    Returns False during 21:00-09:00 IST UNLESS the task is P0/P1 (emergencies
    always notify). In-app notifications ignore this gate. The window is
    evaluated in IST via the shared agents.quiet_hours guard so it matches
    MEGAPHONE's DND exactly; a naive `now` is treated as IST wall-clock."""
    if str(task_priority or "P3").upper() in _ALWAYS_WHATSAPP:
        return True
    from agents.quiet_hours import in_quiet_hours  # lazy import (shared guard)

    return not in_quiet_hours(now)


async def notify_escalation(
    notifications_coll,
    target: Optional[Dict[str, Any]],
    task: Dict[str, Any],
    reason: str,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Write the in-app notification + (conditionally) send WhatsApp.

    Fail-soft: any error is logged, never raised. Returns a small result dict
    {in_app, whatsapp} describing what happened (handy for logs/tests)."""
    result = {"in_app": False, "whatsapp": "skipped"}
    if not target or not target.get("user_id"):
        return result

    # 1. In-app notification (always).
    if notifications_coll is not None:
        try:
            notifications_coll.insert_one(
                build_escalation_notification(task, target["user_id"], reason)
            )
            result["in_app"] = True
        except Exception as e:  # noqa: BLE001
            logger.warning("[TASK_NOTIFY] in-app write failed: %s", e)

    # 2. WhatsApp (DISPATCH_MODE-gated inside the provider; quiet-hours-gated here).
    phone = target.get("phone") or target.get("mobile")
    priority = task.get("priority")
    if phone and whatsapp_allowed(priority, now=now):
        try:
            from agents.providers import send_whatsapp  # lazy import

            res = await send_whatsapp(phone, escalation_whatsapp_text(task, reason))
            result["whatsapp"] = getattr(res, "status", "sent")
        except Exception as e:  # noqa: BLE001
            logger.warning("[TASK_NOTIFY] whatsapp send failed: %s", e)
            result["whatsapp"] = "failed"
    elif phone:
        result["whatsapp"] = "quiet_hours"
    else:
        result["whatsapp"] = "no_phone"

    return result
