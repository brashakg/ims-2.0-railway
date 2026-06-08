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

# Quiet-hours / IST clock is now shared so EVERY outbound path (MEGAPHONE,
# task-escalation WhatsApp, and the manual marketing send API) agree on the
# SAME 21:00-09:00 IST window computed in the SAME timezone. See
# agents.quiet_hours. _IST / _now_ist are re-exported for back-compat (and for
# the existing test_megaphone_dnd suite which imports them from here).
from ..quiet_hours import (
    _IST,
    now_ist as _now_ist,
    in_quiet_hours as _shared_in_quiet_hours,
    next_quiet_end as _shared_next_quiet_end,
    next_quiet_end_utc_iso as _shared_next_quiet_end_utc_iso,
)

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

    def _in_dnd_window(self, now: Optional[datetime] = None) -> bool:
        """True if `now` (default: real IST now) is inside the DND quiet window.

        The window is 21:00-09:00 IST with an overnight wrap. Delegates to the
        shared agents.quiet_hours guard so MEGAPHONE, task-escalation WhatsApp,
        and the manual marketing send API all use the SAME window.
        """
        return _shared_in_quiet_hours(now)

    def _next_dnd_end(self, now: Optional[datetime] = None) -> datetime:
        """Return the next 09:00 IST instant at or after `now`, as a tz-aware
        datetime carrying the IST offset (+05:30). Delegates to the shared
        agents.quiet_hours helper.

        Used as `scheduled_for` so a message queued inside the DND window is
        held until quiet hours end.
        """
        return _shared_next_quiet_end(now)

    def _next_dnd_end_utc_iso(self, now: Optional[datetime] = None) -> str:
        """`_next_dnd_end` as a UTC ISO-8601 string for storage in
        scheduled_for. We store UTC (same instant as the 09:00 IST target) so
        the drain query's lexicographic `$lte` against a UTC `now_iso` stays
        valid. Delegates to the shared agents.quiet_hours helper.
        """
        return _shared_next_quiet_end_utc_iso(now)

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

        # 1+2. Rx-expiry + birthday reminders are NO LONGER hard-coded scans here.
        #      E6 (the reminder rail) is now the SINGLE config-driven path for every
        #      recurring reminder type. The legacy _scan_rx_expiring/_scan_birthdays_today
        #      call sites are intentionally removed; equivalent reminder_rules are
        #      SEEDED active=False (see reminder_rail seed), so the owner opts each one
        #      on explicitly. This is the safe default: ZERO automated sends on deploy
        #      (and the comms channel is currently disabled -- build-dark directive).
        #      The rules are evaluated below by _run_reminder_rules through the full
        #      consent / quiet-hours / frequency-cap gate stack (not a raw insert).

        # 3. CRM-12: Dispatch SCHEDULED campaigns whose send_at is now or past.
        #    ONE_TIME campaigns with send_at <= now are dispatched via the same
        #    send path as the manual /campaigns/{id}/send endpoint.  This closes
        #    the loop: a campaign marked SCHEDULED is actually sent on a tick
        #    rather than being left waiting forever.
        scheduled_sent = await self._dispatch_scheduled_campaigns()
        if scheduled_sent > 0:
            logger.info("[MEGAPHONE] Dispatched %d scheduled campaign(s)", scheduled_sent)

        # 3b. E6 reminder rail: evaluate every ACTIVE CRON-trigger reminder rule
        #     through the gate stack (consent + quiet-hours + 30-day cap). With no
        #     active rules (the deploy default), this is a no-op.
        rule_stats = await self._run_reminder_rules(datetime.now(timezone.utc))
        if rule_stats.get("rules_run", 0) > 0:
            logger.info(
                "[MEGAPHONE] Reminder rules: ran=%d queued=%d tasks=%d skipped=%d",
                rule_stats["rules_run"],
                rule_stats["queued"],
                rule_stats["tasks"],
                rule_stats["skipped"],
            )

        # 4. Drain the queue — up to DRAIN_BATCH_SIZE PENDING messages
        drain_stats = await self._drain_pending(notif_coll)
        if drain_stats["attempted"] > 0:
            logger.info(
                f"[MEGAPHONE] Drain: attempted={drain_stats['attempted']} "
                f"sent={drain_stats['sent']} simulated={drain_stats['simulated']} "
                f"failed={drain_stats['failed']} mode={dispatch_mode()}"
            )

    # -------------------------------------------------------------------------
    # E6: reminder rail tick + event handler
    # -------------------------------------------------------------------------

    async def _run_reminder_rules(self, now) -> Dict[str, int]:
        """Evaluate every ACTIVE, CRON-trigger reminder_rules row through the
        E6 gate stack (reminder_rail.evaluate_rule). Each rule resolves its
        segment, runs consent + quiet-hours + 30-day-cap gates, and queues
        PENDING messages via the SAME send_notification path (DISPATCH_MODE
        gated -- nothing goes live with the default off).

        With no active rules (the deploy default), this is a no-op. EVENT-trigger
        rules are NOT run here -- they fire from on_event when their event lands.
        Fail-soft: one rule's failure never stops the others.
        """
        stats = {"rules_run": 0, "queued": 0, "tasks": 0, "skipped": 0}
        coll = self.get_collection("reminder_rules")
        if coll is None:
            return stats
        try:
            from api.services import reminder_rail
        except Exception:  # pragma: no cover - import guard
            try:
                from ...api.services import reminder_rail  # type: ignore[no-redef]
            except Exception as exc:  # noqa: BLE001
                logger.warning("[MEGAPHONE] reminder_rail import failed: %s", exc)
                return stats
        try:
            rules = list(
                coll.find(
                    {
                        "active": True,
                        "trigger.kind": "CRON",
                        "$or": [
                            {"deleted_at": None},
                            {"deleted_at": {"$exists": False}},
                        ],
                    }
                ).limit(100)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[MEGAPHONE] reminder_rules scan failed: %s", exc)
            return stats

        for rule in rules:
            try:
                res = await reminder_rail.evaluate_rule(self.db, rule, now=now)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[MEGAPHONE] reminder rule %s failed: %s",
                    rule.get("rule_id"), exc,
                )
                continue
            stats["rules_run"] += 1
            stats["queued"] += res.get("queued", 0)
            stats["tasks"] += res.get("tasks_created", 0)
            stats["skipped"] += (
                res.get("skipped_consent", 0)
                + res.get("skipped_freqcap", 0)
                + res.get("skipped_no_phone", 0)
            )
            # Persist per-rule counters (single-document update).
            try:
                coll.update_one(
                    {"rule_id": rule.get("rule_id")},
                    {
                        "$set": {
                            "last_run_at": datetime.now(timezone.utc).isoformat(),
                            "last_resolved": res.get("resolved", 0),
                        },
                        "$inc": {
                            "sent_count": res.get("queued", 0),
                            "skipped_count": (
                                res.get("skipped_consent", 0)
                                + res.get("skipped_freqcap", 0)
                                + res.get("skipped_no_phone", 0)
                            ),
                            "failed_count": res.get("errors", 0),
                        },
                    },
                )
            except Exception:  # noqa: BLE001
                pass
        return stats

    async def on_event(self, event: str, payload: Dict[str, Any]):
        """EVENT-trigger reminder rules. When ORACLE emits churn.detected,
        evaluate every ACTIVE EVENT-trigger churn_risk rule through the gate
        stack. Fail-soft -- never raises into the bus."""
        if event != "churn.detected":
            return
        coll = self.get_collection("reminder_rules")
        if coll is None:
            return
        try:
            from api.services import reminder_rail
        except Exception:
            try:
                from ...api.services import reminder_rail  # type: ignore[no-redef]
            except Exception as exc:  # noqa: BLE001
                logger.warning("[MEGAPHONE] reminder_rail import failed: %s", exc)
                return
        try:
            rules = list(
                coll.find(
                    {
                        "active": True,
                        "rule_type": "churn_risk",
                        "trigger.kind": "EVENT",
                        "$or": [
                            {"deleted_at": None},
                            {"deleted_at": {"$exists": False}},
                        ],
                    }
                ).limit(50)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[MEGAPHONE] churn rule scan failed: %s", exc)
            return
        for rule in rules:
            try:
                await reminder_rail.evaluate_rule(
                    self.db, rule, now=datetime.now(timezone.utc)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[MEGAPHONE] churn rule %s failed: %s", rule.get("rule_id"), exc
                )

    # -------------------------------------------------------------------------
    # CRM-12: Dispatch due SCHEDULED campaigns
    # -------------------------------------------------------------------------

    async def _dispatch_scheduled_campaigns(self) -> int:
        """Scan the campaigns collection for ONE_TIME SCHEDULED campaigns whose
        send_at is <= now and dispatch them via the campaign send path.

        Returns the number of campaigns dispatched this tick.

        Design:
          - Only ONE_TIME campaigns are dispatched here; RECURRING/TRIGGERED
            campaigns remain ACTIVE and are re-triggered by their own event/cron
            logic (a future enhancement).
          - Each campaign is dispatched ONCE by atomically setting status ->
            ACTIVE before doing the send, so a second MEGAPHONE tick (or a
            concurrent worker) will not double-send.
          - The actual fan-out reuses the campaign segments + send_notification
            path already tested by /campaigns/{id}/send — we call the service
            helper directly, not via HTTP.
          - Fail-soft: any exception inside one campaign's dispatch is caught and
            logged; other campaigns still run.
        """
        dispatched = 0
        coll = self.get_collection("campaigns")
        if coll is None:
            return 0

        now_iso = datetime.now(timezone.utc).isoformat()

        # Find SCHEDULED, ONE_TIME campaigns whose send_at is in the past.
        try:
            due = list(coll.find({
                "status": "SCHEDULED",
                "schedule.kind": "ONE_TIME",
                "schedule.send_at": {"$lte": now_iso},
            }).limit(20))  # cap per tick to avoid a burst
        except Exception as exc:
            logger.warning("[MEGAPHONE] scheduled-campaign scan failed: %s", exc)
            return 0

        for camp in due:
            campaign_id = camp.get("campaign_id", "")
            try:
                # Atomic claim: flip SCHEDULED -> ACTIVE so only one worker sends.
                result = coll.update_one(
                    {"campaign_id": campaign_id, "status": "SCHEDULED"},
                    {"$set": {"status": "ACTIVE", "updated_at": datetime.now(timezone.utc).isoformat()}},
                )
                if result.modified_count == 0:
                    # Another worker already claimed this campaign.
                    continue

                await self._execute_campaign_send(camp, coll)
                dispatched += 1

            except Exception as exc:
                logger.warning("[MEGAPHONE] Campaign %s dispatch failed: %s", campaign_id, exc)
                # Revert to SCHEDULED so it retries on the next tick.
                try:
                    coll.update_one(
                        {"campaign_id": campaign_id, "status": "ACTIVE"},
                        {"$set": {"status": "SCHEDULED"}},
                    )
                except Exception:
                    pass

        return dispatched

    async def _execute_campaign_send(
        self, camp: Dict[str, Any], coll
    ) -> None:
        """Fan-out a single campaign to its resolved audience using the shared
        notification_service path.  Mirrors campaigns.send_campaign but runs
        agent-side (no HTTP, no rate-limit check -- MEGAPHONE is a trusted actor).
        """
        try:
            from api.services import campaign_segments as seg
            from api.services.notification_service import send_notification
        except ImportError:
            from ...api.services import campaign_segments as seg  # type: ignore[no-redef]
            from ...api.services.notification_service import send_notification  # type: ignore[no-redef]

        campaign_id = camp.get("campaign_id", "")
        template_id = camp.get("template_id", "")
        store_id = camp.get("store_id") or ""
        channels = camp.get("channels") or ["WHATSAPP"]
        primary_channel = channels[0]

        # Promo quiet-hours gate: if we're in DND, defer this campaign.
        if template_id and self._in_dnd_window():
            logger.info(
                "[MEGAPHONE] Campaign %s deferred (DND window)", campaign_id
            )
            # Revert to SCHEDULED so it fires after DND ends.
            coll.update_one(
                {"campaign_id": campaign_id},
                {"$set": {"status": "SCHEDULED"}},
            )
            return

        db = self.db
        audience = seg.resolve_segment(
            db,
            camp.get("segment_key", ""),
            store_id=store_id or None,
            params=camp.get("segment_params") or {},
        )

        cust_coll = self.get_collection("customers")
        sent = skipped = failed = 0

        for r in audience:
            cid = r.get("customer_id")
            # Consent gate: respect marketing_consent == False.
            if cid and cust_coll is not None:
                cust = cust_coll.find_one({"customer_id": cid}) or {}
                if cust.get("marketing_consent") is False:
                    skipped += 1
                    continue
            phone = r.get("phone") or ""
            if not phone:
                skipped += 1
                continue
            try:
                res = await send_notification(
                    store_id=store_id,
                    customer_id=cid or "",
                    customer_phone=phone,
                    customer_name=r.get("name", "Customer"),
                    template_id=template_id,
                    channel=primary_channel,
                    variables={**(r.get("variables") or {}), "campaign_id": campaign_id},
                    category="MARKETING",
                    triggered_by="MEGAPHONE",
                    related_entity_type="campaign",
                    related_entity_id=campaign_id,
                )
                # Tag the notification row with campaign_id for analytics.
                nid = res.get("notification_id")
                if nid:
                    notif_coll = self.get_collection("notification_logs")
                    if notif_coll is not None:
                        try:
                            notif_coll.update_one(
                                {"notification_id": nid},
                                {"$set": {"campaign_id": campaign_id}},
                            )
                        except Exception:
                            pass
                sent += 1
            except Exception as exc:
                failed += 1
                logger.debug("[MEGAPHONE] Campaign %s recipient failed: %s", campaign_id, exc)

        # ONE_TIME -> COMPLETED after send; update counters.
        coll.update_one(
            {"campaign_id": campaign_id},
            {
                "$set": {
                    "status": "COMPLETED",
                    "audience_count": len(audience),
                    "last_sent_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                "$inc": {
                    "sent_count": sent,
                    "skipped_count": skipped,
                    "failed_count": failed,
                },
            },
        )
        logger.info(
            "[MEGAPHONE] Campaign %s completed: sent=%d skipped=%d failed=%d",
            campaign_id, sent, skipped, failed,
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
            # Keep the DLT audit field in lock-step so the MSG91 delivery-report
            # webhook can match this row by provider_msg_id and advance it.
            updates["provider_msg_id"] = provider_id
        if error:
            updates["failure_reason"] = error
        if status == "SENT":
            updates["sent_at"] = updates["dispatched_at"]
            # Advance the DLR ladder QUEUED -> SENT; the webhook moves it on to
            # DELIVERED/READ/FAILED when MSG91 reports back.
            updates["delivery_status"] = "SENT"
        elif status == "FAILED":
            updates["delivery_status"] = "FAILED"
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
