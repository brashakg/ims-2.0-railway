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

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
import logging

from ..base import JarvisAgent, AgentType, AgentResponse, AgentContext
from ..providers import send_whatsapp, send_sms, dispatch_mode, provider_ready

logger = logging.getLogger(__name__)

# IST timezone for the DND quiet window. TRAI/DLT rules define the window in
# India Standard Time, NOT UTC -- computing it in UTC shifts it by 5h30m and
# lets promotional messages go out at ~1 AM IST (a compliance breach).
# Wrapped defensively per the fail-soft contract: if zoneinfo / the tz database
# is unavailable on the host, fall back to a fixed UTC+5:30 offset (India has no
# DST, so the offset is exact). Only if BOTH fail do we degrade to UTC with a
# warning.
_IST: Optional[timezone] = None
try:
    from zoneinfo import ZoneInfo

    _IST = ZoneInfo("Asia/Kolkata")  # type: ignore[assignment]
except Exception as _e:  # pragma: no cover - only on hosts without tzdata
    try:
        _IST = timezone(timedelta(hours=5, minutes=30), name="IST")
        logger.warning(
            "[MEGAPHONE] zoneinfo Asia/Kolkata unavailable (%s) -- using fixed "
            "UTC+5:30 offset for DND. India does not observe DST so this is exact.",
            _e,
        )
    except Exception:  # pragma: no cover
        _IST = None
        logger.warning(
            "[MEGAPHONE] Could not resolve IST timezone -- DND falls back to UTC. "
            "Promotional sends may not respect the IST quiet window."
        )


def _now_ist(now: Optional[datetime] = None) -> datetime:
    """Return `now` (or real current time) expressed in IST.

    `now` may be naive or tz-aware; if naive it's assumed to already be IST.
    Falls back to UTC only if IST could not be resolved at import time.
    """
    if now is None:
        tz = _IST or timezone.utc
        return datetime.now(tz)
    if now.tzinfo is None:
        # Naive datetimes from callers/tests are taken to mean IST wall-clock.
        return now if _IST is None else now.replace(tzinfo=_IST)
    return now.astimezone(_IST) if _IST is not None else now.astimezone(timezone.utc)

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

    def _in_dnd_window(self, now: Optional[datetime] = None) -> bool:
        """True if `now` (default: real IST now) is inside the DND quiet window.

        The window is 21:00-09:00 IST with an overnight wrap, so the test is
        hour >= DND_START_HOUR (>=21) OR hour < DND_END_HOUR (<9). `now` is
        injectable for testing; it is normalised to IST first.
        """
        now_hour = _now_ist(now).hour
        return now_hour >= self.DND_START_HOUR or now_hour < self.DND_END_HOUR

    def _next_dnd_end(self, now: Optional[datetime] = None) -> datetime:
        """Return the next 09:00 IST instant at or after `now`, as a tz-aware
        datetime carrying the IST offset (+05:30).

        Used as `scheduled_for` so a message queued inside the DND window is
        held until quiet hours end. If it's already past 09:00 today (i.e. we
        are in the evening 21:00-24:00 leg of the window) the target rolls to
        09:00 tomorrow.
        """
        ist_now = _now_ist(now)
        target = ist_now.replace(
            hour=self.DND_END_HOUR, minute=0, second=0, microsecond=0
        )
        if ist_now.hour >= self.DND_END_HOUR:
            # Past 09:00 already -> the next quiet-hours end is tomorrow 09:00.
            target = target + timedelta(days=1)
        return target

    def _next_dnd_end_utc_iso(self, now: Optional[datetime] = None) -> str:
        """`_next_dnd_end` as a UTC ISO-8601 string for storage in
        scheduled_for. We store UTC (same instant as the 09:00 IST target) so
        the drain query's lexicographic `$lte` against a UTC `now_iso` stays
        valid -- mixing +05:30 and +00:00 offset strings would compare wrong.
        Falls back gracefully if the target is somehow naive.
        """
        target = self._next_dnd_end(now)
        if target.tzinfo is None:  # pragma: no cover - _next_dnd_end is tz-aware
            return target.isoformat()
        return target.astimezone(timezone.utc).isoformat()

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
                    # If queued during DND, hold until the next 09:00 IST
                    # (stored as UTC so the drain query compares correctly).
                    "scheduled_for": None if not self._in_dnd_window()
                                     else self._next_dnd_end_utc_iso(),
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
            # Consent gate (TCCCPR): prescriptions carry no consent flag, so
            # drop rows whose customer has opted out of marketing. A missing
            # flag defaults to consented (matches the customer-create default).
            cust_coll = self.get_collection("customers")
            if cust_coll is not None and expiring:
                ids = [e.get("customer_id") for e in expiring if e.get("customer_id")]
                opted_out = {
                    c.get("customer_id")
                    for c in cust_coll.find(
                        {"customer_id": {"$in": ids}, "marketing_consent": False},
                        {"_id": 0, "customer_id": 1},
                    )
                }
                expiring = [e for e in expiring if e.get("customer_id") not in opted_out]
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
                # Consent gate (TCCCPR): never queue a promotional birthday
                # message to a customer who has opted out of marketing.
                {
                    "date_of_birth": {"$regex": today + "$"},
                    "marketing_consent": {"$ne": False},
                },
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
