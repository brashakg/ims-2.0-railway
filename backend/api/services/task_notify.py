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

# Quiet hours (server local clock): no WhatsApp between these hours.
QUIET_START_HOUR = 21  # 9 PM
QUIET_END_HOUR = 9  # 9 AM
# Priorities that bypass quiet hours (true emergencies).
_ALWAYS_WHATSAPP = {"P0", "P1"}


def notification_priority(task_priority: Any) -> str:
    # Known P-codes map; anything missing/unknown -> NORMAL.
    return _PRIORITY_MAP.get(str(task_priority or "").upper(), "NORMAL")


def build_escalation_notification(
    task: Dict[str, Any], target_user_id: str, reason: str
) -> Dict[str, Any]:
    """Build a NOTIFICATION_SCHEMA in-app notification doc. Pure."""
    title = task.get("title") or "Task"
    priority = str(task.get("priority") or "P3").upper()
    return {
        "notification_id": f"NTF-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}",
        "notification_type": "task_escalation",
        "user_id": target_user_id,
        "title": f"Escalated to you: {title}",
        "message": (f"[{priority}] '{title}' was escalated to you. Reason: {reason}."),
        "entity_type": "task",
        "entity_id": task.get("task_id"),
        "action_url": "/tasks",
        "channels": ["IN_APP"],
        "priority": notification_priority(priority),
        "status": "SENT",
        "created_at": datetime.now(),
    }


def escalation_whatsapp_text(task: Dict[str, Any], reason: str) -> str:
    """Plain-text WhatsApp body for an escalation. Pure."""
    title = task.get("title") or "Task"
    priority = str(task.get("priority") or "P3").upper()
    store = task.get("store_id") or "-"
    return (
        f"IMS escalation [{priority}]\n"
        f"Task: {title}\n"
        f"Store: {store}\n"
        f"Reason: {reason}\n"
        f"Open IMS > Tasks to action it."
    )


def whatsapp_allowed(task_priority: Any, *, now: Optional[datetime] = None) -> bool:
    """Quiet-hours gate for WhatsApp. Pure.

    Returns False during 21:00-09:00 (server local clock) UNLESS the task is
    P0/P1 (emergencies always notify). In-app notifications ignore this gate."""
    if str(task_priority or "P3").upper() in _ALWAYS_WHATSAPP:
        return True
    now = now or datetime.now()
    hour = now.hour
    # Quiet window wraps midnight: [21:00, 24:00) U [00:00, 09:00).
    in_quiet = hour >= QUIET_START_HOUR or hour < QUIET_END_HOUR
    return not in_quiet


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
