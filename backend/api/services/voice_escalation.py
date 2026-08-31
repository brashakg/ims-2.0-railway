"""
IMS 2.0 - Voice escalation rung (MSG91 channel expansions)
==========================================================
When a P1 SYSTEM task (till variance, SLA breach) passes its acknowledgement
window unacked, place ONE TTS voice call to the store manager's mobile via
the MSG91 voice API; pressing 1 in the IVR acknowledges the task through the
MSG91 voice webhook (/api/v1/integrations/msg91/webhooks/voice).

THIS IS A RUNG, NOT A BRAIN. The escalation ladder - who escalates, when,
to whom - stays entirely with the existing task engine (task_sla.
should_escalate + task_escalation.resolve_escalation_target). This module is
invoked from the ONE escalation alert site (task_notify.notify_escalation,
which both TASKMASTER's tick and the /tasks auto-escalate endpoint route
through) and only ADDS a voice call beside the in-app bell + WhatsApp.

Safety:
  - Policy-gated: msg.voice_escalation (registered, default "off").
  - DISPATCH_MODE-gated inside the provider: while dark the call is
    SIMULATED - proves the payload shape, rings no phone.
  - ONE call per task, ever: an atomic $exists-guarded claim on the task doc
    (`voice_escalation` field) makes the dedupe race-proof across workers.
  - Fail-soft everywhere: a voice hiccup never breaks an escalation.

ASCII only (cp1252). Never logs a full phone number.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

POLICY_KEY = "msg.voice_escalation"

# The ack-window breach reason produced by task_sla.should_escalate. The
# voice rung fires ONLY for this breach class ("passed its escalation window
# unacked") - an overdue-but-acknowledged task already has a human on it.
_ACK_BREACH_PREFIX = "Not acknowledged"


def _get_db():
    """Live db-like object or None. Never truth-test a pymongo object."""
    try:
        from database.connection import get_db as _gd

        conn = _gd()
        if conn is None:
            return None
        database = getattr(conn, "db", None)
        if database is not None:
            return database
        if hasattr(conn, "get_collection"):
            return conn
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[VOICE_ESC] _get_db failed: %s", exc)
        return None


def _policy_on(store_id: Optional[str]) -> bool:
    """Fail-CLOSED policy read: any error means off (no surprise calls)."""
    try:
        from api.services.policy_engine import get_policy

        scope = {"store_id": store_id} if store_id else None
        return str(get_policy(POLICY_KEY, scope) or "off").strip().lower() == "on"
    except Exception:  # noqa: BLE001
        return False


def _store_manager(db, store_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The active STORE_MANAGER for the task's store, with a phone. None when
    unresolvable. Same users-collection query shape TASKMASTER uses."""
    if db is None or not store_id:
        return None
    try:
        users = db.get_collection("users")
        for user in users.find(
            {"roles": "STORE_MANAGER", "is_active": True, "store_ids": store_id}
        ):
            if user.get("phone") or user.get("mobile"):
                return dict(user)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[VOICE_ESC] store manager lookup failed: %s", exc)
    return None


def voice_text(task: Dict[str, Any]) -> str:
    """The TTS script. Pure."""
    title = str(task.get("title") or "a critical task")[:120]
    store = str(task.get("store_id") or "your store")
    return (
        f"This is an automated alert from IMS for {store}. "
        f"A priority-one task, {title}, has not been acknowledged. "
        "Press 1 to acknowledge this task, or open IMS Tasks now."
    )


async def maybe_voice_escalate(
    task: Dict[str, Any], reason: str, db=None
) -> str:
    """Place at most ONE voice call for an escalating task. Returns an honest
    verdict: "called" | "simulated" | "duplicate" | "skipped:<why>". Never
    raises.

    Order of guards matters: the cheap pure checks run before any DB or
    provider work, and the atomic claim runs BEFORE the call so two workers
    escalating the same task cannot both dial.
    """
    if not _policy_on(task.get("store_id")):
        return "skipped:policy_off"
    if str(task.get("priority") or "").upper() != "P1":
        return "skipped:not_p1"
    if str(task.get("source") or "").upper() != "SYSTEM":
        return "skipped:not_system_task"
    if not str(reason or "").startswith(_ACK_BREACH_PREFIX):
        return "skipped:not_ack_breach"
    if task.get("acknowledged_at"):
        return "skipped:already_acknowledged"
    task_id = task.get("task_id")
    if not task_id:
        return "skipped:no_task_id"

    if db is None:
        db = _get_db()
    if db is None:
        return "skipped:storage_unavailable"

    manager = _store_manager(db, task.get("store_id"))
    if not manager:
        return "skipped:no_store_manager_phone"
    phone = manager.get("phone") or manager.get("mobile") or ""

    now = datetime.now(timezone.utc).isoformat()
    try:
        tasks = db.get_collection("tasks")
        # Atomic ONE-call claim: only the worker that first stamps the field
        # proceeds. A task already carrying voice_escalation never re-dials.
        res = tasks.update_one(
            {"task_id": task_id, "voice_escalation": {"$exists": False}},
            {
                "$set": {
                    "voice_escalation": {
                        "status": "PLACING",
                        "to_user_id": manager.get("user_id"),
                        "claimed_at": now,
                    }
                }
            },
        )
        if not getattr(res, "modified_count", 0):
            return "duplicate"
    except Exception as exc:  # noqa: BLE001
        logger.warning("[VOICE_ESC] claim failed: %s", exc)
        return "skipped:storage_unavailable"

    from agents.providers import send_voice_call  # lazy import

    result = await send_voice_call(phone, voice_text(task))
    stamp = {
        "voice_escalation.status": result.status,
        "voice_escalation.provider_id": result.provider_id,
        "voice_escalation.placed_at": datetime.now(timezone.utc).isoformat(),
    }
    if result.error:
        stamp["voice_escalation.error"] = str(result.error)[:200]
    try:
        tasks.update_one({"task_id": task_id}, {"$set": stamp})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[VOICE_ESC] result stamp failed: %s", exc)
    logger.info(
        "[VOICE_ESC] voice call %s for task %s (to user %s, ...%s)",
        result.status,
        task_id,
        manager.get("user_id"),
        str(phone)[-4:],
    )
    if result.status == "SENT":
        return "called"
    if result.status == "SIMULATED":
        return "simulated"
    return f"skipped:call_{str(result.status).lower()}"


# ---------------------------------------------------------------------------
# IVR press-1 acknowledgement (called by the msg91 voice webhook channel)
# ---------------------------------------------------------------------------

# Keys MSG91's voice webhook may carry the pressed digit under (their voice
# report shape is panel-documented; parse leniently, act only on exactly "1").
_DTMF_KEYS = ("dtmf", "digits", "digit", "ivr_input", "key_pressed", "pressed")


def _iter_voice_acks(payload: Any) -> list:
    """Yield provider ids whose voice report carries a pressed "1"."""
    out = []
    items = payload if isinstance(payload, list) else [payload]
    for item in items:
        if not isinstance(item, dict):
            continue
        candidates = [item]
        data = item.get("data")
        if isinstance(data, dict):
            candidates.append(data)
        for d in candidates:
            digit = None
            for key in _DTMF_KEYS:
                if d.get(key) is not None:
                    digit = str(d.get(key)).strip()
                    break
            if digit != "1":
                continue
            pid = (
                d.get("request_id")
                or d.get("requestId")
                or d.get("call_id")
                or d.get("messageId")
                or d.get("message_id")
            )
            if pid:
                out.append(str(pid))
    return out


def apply_voice_acks(payload: Any, db=None) -> int:
    """Acknowledge tasks whose voice-escalation call reported a pressed "1".

    Mirrors POST /tasks/{id}/acknowledge exactly (status -> IN_PROGRESS +
    acknowledged_at/by) and adds the audit trail naming the channel: a task
    `history` entry with channel="voice_ivr" plus acknowledged_via on the
    doc. Idempotent - an already-acknowledged / terminal task is left alone.
    Returns how many tasks were acknowledged. Never raises.
    """
    acked = 0
    pids = _iter_voice_acks(payload)
    if not pids:
        return 0
    if db is None:
        db = _get_db()
    if db is None:
        return 0
    try:
        tasks = db.get_collection("tasks")
    except Exception:  # noqa: BLE001
        return 0
    for pid in pids:
        try:
            task = tasks.find_one({"voice_escalation.provider_id": pid})
            if not task:
                continue
            status = str(task.get("status") or "").strip().upper()
            if status in ("COMPLETED", "CANCELLED", "IN_PROGRESS"):
                continue
            if task.get("acknowledged_at"):
                continue
            now = datetime.now(timezone.utc).isoformat()
            by = (task.get("voice_escalation") or {}).get("to_user_id")
            res = tasks.update_one(
                {"task_id": task.get("task_id"), "acknowledged_at": None},
                {
                    "$set": {
                        "status": "IN_PROGRESS",
                        "acknowledged_at": now,
                        "acknowledged_by": by,
                        "acknowledged_via": "voice_ivr",
                        "updated_at": now,
                        "voice_escalation.acked_at": now,
                    },
                    "$push": {
                        "history": {
                            "action": "acknowledged",
                            "channel": "voice_ivr",
                            "by": by,
                            "at": now,
                            "provider_message_id": pid,
                            "notes": "Acknowledged by pressing 1 on the "
                            "escalation voice call",
                        }
                    },
                },
            )
            if getattr(res, "modified_count", 0):
                acked += 1
                logger.info(
                    "[VOICE_ESC] task %s acknowledged via voice IVR (call %s)",
                    task.get("task_id"),
                    pid,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[VOICE_ESC] voice ack failed for %s: %s", pid, exc)
    return acked
