"""
IMS 2.0 — MEGAPHONE: Marketing & Engagement
==============================================
Hero Identity: Black Canary / Dinah Lance (DC)
"Her Canary Cry is unstoppable communication power. Reaches every customer
at the right moment."

MEGAPHONE owns OUTBOUND customer communication on cadence:
  - Rx expiry reminders (90 / 30 / 7 day windows)
  - Birthday + anniversary greetings
  - Walk-out follow-ups (visited but didn't buy)
  - NPS surveys post-delivery
  - Bulk campaigns scheduled by marketing

Default schedule: Every 30 minutes + daily 9 AM batch.
DND respect: queues but does not send between 21:00 and 09:00 IST,
except critical transactional (kept on the notification_service path).

Scope of MVP implementation:
  - Scans customers / orders for trigger events (Rx expiry, birthday)
  - Queues notifications to MongoDB notification_logs collection in PENDING
  - Real WhatsApp Business / SMS provider dispatch is wired through
    notification_service.py, which is the existing send path
  - Respects DND quiet hours
"""

from typing import Dict, Any, List
from datetime import datetime, timezone, timedelta
import logging

from ..base import JarvisAgent, AgentType, AgentResponse, AgentContext
from ..providers import send_whatsapp, send_sms, dispatch_mode, provider_ready

logger = logging.getLogger(__name__)

# How many PENDING notifications to drain per tick. Keeps the 30-min loop
# bounded and stops a backed-up queue from taking hours to clear (at 60/tick
# we drain 120/hour = 2,880/day, well above typical Rx reminder volume).
DRAIN_BATCH_SIZE = 60


class MegaphoneAgent(JarvisAgent):
    """Marketing automation — Rx expiry, birthdays, follow-ups, NPS."""

    agent_id = "megaphone"
    agent_name = "MEGAPHONE"
    agent_type = AgentType.EXECUTOR
    description = "Marketing engagement — Rx expiry, birthday, walkout, NPS, scheduled campaigns"
    version = "1.0.0"
    toggleable = True

    capabilities = [
        "rx_expiry_reminder",
        "birthday_greeting",
        "walkout_followup",
        "nps_survey_dispatch",
        "campaign_queue",
        "dnd_compliance",
    ]

    # DND window (IST hours). MEGAPHONE queues but doesn't send during this window.
    DND_START_HOUR = 21
    DND_END_HOUR = 9

    def __init__(self, db=None):
        super().__init__(db=db)
        self._queued_count = 0

    def _in_dnd_window(self) -> bool:
        """True if current time is inside the DND quiet window."""
        now_hour = datetime.now(timezone.utc).hour  # naive — real impl uses IST
        return self.DND_START_HOUR <= now_hour or now_hour < self.DND_END_HOUR

    async def _do_background_work(self):
        """
        Scan triggers and queue messages to notification_logs.
        Runs every 30 min; the actual provider dispatch is not done here —
        notification_service.py picks up PENDING entries and sends them.
        """
        notif_coll = self.get_collection("notification_logs")
        if notif_coll is None:
            logger.info("[MEGAPHONE] notification_logs unavailable — skipping")
            return

        queued_this_run = 0

        # 1. Rx expiry reminders (90 / 30 / 7 day windows)
        rx_expiring = await self._scan_rx_expiring()
        for rx in rx_expiring:
            try:
                notif_coll.insert_one({
                    "agent_id": self.agent_id,
                    "kind": "rx_expiry_reminder",
                    "customer_id": rx.get("customer_id"),
                    "patient_name": rx.get("patient_name"),
                    "channel": "whatsapp",
                    "status": "PENDING",
                    "queued_at": datetime.now(timezone.utc).isoformat(),
                    "dnd_window": self._in_dnd_window(),
                    "scheduled_for": None if not self._in_dnd_window()
                                     else datetime.now(timezone.utc).replace(hour=self.DND_END_HOUR, minute=0, second=0).isoformat(),
                })
                queued_this_run += 1
            except Exception as e:
                logger.warning(f"[MEGAPHONE] Failed to queue Rx expiry: {e}")

        # 2. Birthday greetings (today's birthdays)
        birthdays = await self._scan_birthdays_today()
        for cust in birthdays:
            try:
                notif_coll.insert_one({
                    "agent_id": self.agent_id,
                    "kind": "birthday_greeting",
                    "customer_id": cust.get("customer_id"),
                    "channel": "whatsapp",
                    "status": "PENDING",
                    "queued_at": datetime.now(timezone.utc).isoformat(),
                })
                queued_this_run += 1
            except Exception as e:
                logger.warning(f"[MEGAPHONE] Failed to queue birthday: {e}")

        self._queued_count += queued_this_run
        if queued_this_run > 0:
            logger.info(f"[MEGAPHONE] Queued {queued_this_run} notifications "
                        f"({len(rx_expiring)} Rx expiry, {len(birthdays)} birthday)")

        # 3. Drain the queue — up to DRAIN_BATCH_SIZE PENDING messages
        drain_stats = await self._drain_pending(notif_coll)
        if drain_stats["attempted"] > 0:
            logger.info(
                f"[MEGAPHONE] Drain: attempted={drain_stats['attempted']} "
                f"sent={drain_stats['sent']} simulated={drain_stats['simulated']} "
                f"failed={drain_stats['failed']} mode={dispatch_mode()}"
            )

    async def _scan_rx_expiring(self) -> List[Dict[str, Any]]:
        """Find prescriptions expiring in the next 7 / 30 / 90 days."""
        coll = self.get_collection("prescriptions")
        if coll is None:
            return []
        try:
            now = datetime.now(timezone.utc)
            cutoff = (now + timedelta(days=90)).isoformat()
            expiring = list(coll.find(
                {"expiry_date": {"$lte": cutoff, "$gt": now.isoformat()}},
                {"_id": 0, "customer_id": 1, "patient_name": 1, "expiry_date": 1},
            ).limit(50))
            return expiring
        except Exception as e:
            logger.debug(f"[MEGAPHONE] Rx scan error (non-fatal): {e}")
            return []

    async def _scan_birthdays_today(self) -> List[Dict[str, Any]]:
        """Find customers with birthday today (MM-DD match)."""
        coll = self.get_collection("customers")
        if coll is None:
            return []
        try:
            today = datetime.now(timezone.utc).strftime("%m-%d")
            # Customers store birthday as "YYYY-MM-DD" or "DD-MM-YYYY"; we
            # match on the suffix that ends with -MM-DD.
            matches = list(coll.find(
                {"date_of_birth": {"$regex": today + "$"}},
                {"_id": 0, "customer_id": 1, "name": 1},
            ).limit(50))
            return matches
        except Exception as e:
            logger.debug(f"[MEGAPHONE] Birthday scan error (non-fatal): {e}")
            return []

    # ------------------------------------------------------------------
    # Drain pass — pick up PENDING messages and dispatch via provider
    # ------------------------------------------------------------------

    async def _drain_pending(self, notif_coll) -> Dict[str, int]:
        """
        Select up to DRAIN_BATCH_SIZE PENDING messages and dispatch them.
        Respects DND: rows whose scheduled_for is in the future stay PENDING.

        Contract: every selected row transitions to SENT / SIMULATED / FAILED.
        No row stays stuck on PENDING after a dispatch attempt — the status
        column is the source of truth for monitoring.
        """
        stats = {"attempted": 0, "sent": 0, "simulated": 0, "failed": 0, "skipped": 0}

        # If MEGAPHONE is inside the DND window and the row wasn't
        # explicitly scheduled for later, we'd still not want to send.
        # Rx expiry / birthday rows already set scheduled_for to the DND
        # end-of-window when queued; we only send rows whose scheduled_for
        # is null OR <= now.
        now_iso = datetime.now(timezone.utc).isoformat()

        try:
            candidates = list(
                notif_coll.find({
                    "status": "PENDING",
                    "$or": [
                        {"scheduled_for": None},
                        {"scheduled_for": {"$lte": now_iso}},
                    ],
                }).limit(DRAIN_BATCH_SIZE)
            )
        except Exception as e:
            logger.warning(f"[MEGAPHONE] Drain candidate fetch failed: {e}")
            return stats

        for row in candidates:
            stats["attempted"] += 1
            channel = (row.get("channel") or "whatsapp").lower()
            phone = row.get("customer_phone") or row.get("phone") or ""
            message = row.get("message") or row.get("body") or ""
            template_id = row.get("template_id")

            # We need a phone and SOMETHING to send. If message is empty
            # and we can populate from template, that's notification_service's
            # job — MEGAPHONE only drains pre-populated messages.
            if not phone or not message:
                self._update_status(
                    notif_coll, row,
                    status="FAILED",
                    error="missing phone or message",
                )
                stats["failed"] += 1
                continue

            # Dispatch
            try:
                if channel == "sms":
                    result = await send_sms(phone, message)
                else:
                    result = await send_whatsapp(phone, message, template_id=template_id)
            except Exception as e:
                # Defense-in-depth — provider clients already fail soft, but
                # if something slips through we don't want to kill the drain.
                logger.warning(f"[MEGAPHONE] Unexpected provider raise: {e}")
                self._update_status(notif_coll, row, status="FAILED", error=f"unexpected: {e}")
                stats["failed"] += 1
                continue

            # Record result
            self._update_status(
                notif_coll, row,
                status=result.status,
                provider_id=result.provider_id,
                error=result.error,
            )
            if result.status == "SENT":
                stats["sent"] += 1
            elif result.status == "SIMULATED":
                stats["simulated"] += 1
            elif result.status == "FAILED":
                stats["failed"] += 1
            else:
                stats["skipped"] += 1

        return stats

    def _update_status(self, coll, row: Dict[str, Any], *, status: str,
                       provider_id: str = None, error: str = None):
        """Persist a status transition on a notification_logs row."""
        updates = {
            "status": status,
            "dispatched_at": datetime.now(timezone.utc).isoformat(),
        }
        if provider_id:
            updates["provider_id"] = provider_id
        if error:
            updates["failure_reason"] = error
        if status == "SENT":
            updates["sent_at"] = updates["dispatched_at"]
        try:
            coll.update_one({"_id": row["_id"]}, {"$set": updates})
        except Exception as e:
            logger.warning(f"[MEGAPHONE] Status update failed: {e}")

    async def run(self, query: str, context: AgentContext) -> AgentResponse:
        """On-demand: report queued/sent notifications + dispatch mode."""
        coll = self.get_collection("notification_logs")
        if coll is None:
            return AgentResponse(success=False, agent_id=self.agent_id, message="notification_logs unavailable")
        try:
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            counts = {
                "queued_today": coll.count_documents({
                    "agent_id": self.agent_id,
                    "queued_at": {"$gte": today_start},
                }),
                "sent_today": coll.count_documents({
                    "agent_id": self.agent_id,
                    "sent_at": {"$gte": today_start},
                }),
                "pending_now": coll.count_documents({"status": "PENDING"}),
                "failed_today": coll.count_documents({
                    "agent_id": self.agent_id,
                    "status": "FAILED",
                    "dispatched_at": {"$gte": today_start},
                }),
            }
        except Exception:
            counts = {"queued_today": 0, "sent_today": 0, "pending_now": 0, "failed_today": 0}

        return AgentResponse(
            success=True,
            agent_id=self.agent_id,
            data={
                **counts,
                "in_dnd": self._in_dnd_window(),
                "dispatch_mode": dispatch_mode(),
                "whatsapp_ready": provider_ready("whatsapp"),
                "sms_ready": provider_ready("sms"),
            },
            message=(
                f"MEGAPHONE · queued {counts['queued_today']} today, "
                f"sent {counts['sent_today']}, pending {counts['pending_now']}, "
                f"failed {counts['failed_today']}; DND={self._in_dnd_window()}; "
                f"mode={dispatch_mode()}"
            ),
        )
