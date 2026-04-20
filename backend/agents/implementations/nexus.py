"""
IMS 2.0 — NEXUS: Data & Integration Orchestration
====================================================
Hero Identity: Cyborg / Victor Stone (DC)
"Can interface with any digital system on Earth. NEXUS bridges every
external platform into one unified data layer."

NEXUS owns the integration boundary:
  - Shopify product / order / inventory sync
  - Razorpay payment reconciliation
  - Shiprocket shipment lifecycle (label, AWB, tracking)
  - Tally daily ledger export at 11 PM
  - Webhook processing queue
  - Sync status tracking with retry + dead-letter

Default schedule: Hourly + on webhook event.
Activation: Only the integrations that have credentials configured run;
otherwise the sub-task no-ops.

Scope of MVP implementation:
  - Reads connected integrations from MongoDB `integrations` collection
  - Logs a sync_run heartbeat per integration that's enabled
  - Emits `sync.failed` events on any failure (handled by SENTINEL + CORTEX)
  - Real provider API calls are wired through admin.py routes which
    NEXUS would call into next pass
"""

from typing import Dict, Any, List
from datetime import datetime, timezone
import logging

from ..base import JarvisAgent, AgentType, AgentResponse, AgentContext

logger = logging.getLogger(__name__)


# Integrations NEXUS knows how to sync. Each declares its sync cadence
# (subset of the agent's hourly tick — e.g. tally is once-a-day at 23:00).
INTEGRATION_SCHEDULES = {
    "shopify":    {"every_tick": True,                       "tier": 1},  # bi-directional product/order
    "razorpay":   {"every_tick": True,                       "tier": 1},  # payment reconciliation
    "shiprocket": {"every_tick": True,                       "tier": 1},  # shipment status
    "tally":      {"only_at_hour": 23,                       "tier": 2},  # nightly ledger export
    "whatsapp":   {"every_tick": False, "via_event": True,   "tier": 1},  # event-driven only (notif_service)
    "gst_portal": {"only_at_hour": 23,                       "tier": 2},  # nightly GSTIN cache refresh
}


class NexusAgent(JarvisAgent):
    """Integration orchestration — sync, webhook routing, retry, audit."""

    agent_id = "nexus"
    agent_name = "NEXUS"
    agent_type = AgentType.INTEGRATOR
    description = "Data & integrations — Shopify, Razorpay, Shiprocket, Tally, GST, webhook queue"
    version = "1.0.0"
    toggleable = True

    capabilities = [
        "shopify_sync",
        "razorpay_reconcile",
        "shiprocket_status_pull",
        "tally_daily_export",
        "webhook_queue_drain",
        "sync_audit",
    ]

    def __init__(self, db=None):
        super().__init__(db=db)
        self._sync_runs = 0

    async def _do_background_work(self):
        """
        Hourly tick. For each enabled integration whose schedule fires this
        hour, run the sync and record a sync_run row. Real sync work hands
        off to the existing admin.py integration endpoints (next pass).
        """
        configured = await self._get_enabled_integrations()
        if not configured:
            logger.debug("[NEXUS] No integrations enabled — tick skipped")
            return

        now = datetime.now(timezone.utc)
        runs = []

        for integ_type in configured:
            schedule = INTEGRATION_SCHEDULES.get(integ_type, {})
            should_run = (
                schedule.get("every_tick", False)
                or schedule.get("only_at_hour") == now.hour
            )
            if not should_run:
                continue
            try:
                result = await self._run_integration_sync(integ_type)
                runs.append(result)
                if not result.get("ok"):
                    await self._emit_sync_failed(integ_type, result.get("error"))
            except Exception as e:
                logger.warning(f"[NEXUS] {integ_type} sync raised: {e}")
                await self._emit_sync_failed(integ_type, str(e))

        if runs:
            await self._record_sync_runs(runs)
            self._sync_runs += len(runs)
            logger.info(f"[NEXUS] Tick complete — {len(runs)} integration(s) synced")

    async def _get_enabled_integrations(self) -> List[str]:
        """Return integration IDs that are configured + enabled."""
        coll = self.get_collection("integrations")
        if coll is None:
            return []
        try:
            return [
                doc["type"]
                for doc in coll.find({"enabled": True}, {"_id": 0, "type": 1})
                if doc.get("type")
            ]
        except Exception:
            return []

    async def _run_integration_sync(self, integ_type: str) -> Dict[str, Any]:
        """
        Heartbeat sync — records a successful "ping" run. Real implementation
        would call into the matching admin.py route or provider client.
        Designed to fail soft so a single broken integration doesn't kill
        the tick for the others.
        """
        return {
            "integration": integ_type,
            "ok": True,
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "kind": "heartbeat",
            "items_synced": 0,
            "notes": "MVP heartbeat — provider client wiring is next pass",
        }

    async def _record_sync_runs(self, runs: List[Dict[str, Any]]):
        coll = self.get_collection("sync_runs")
        if coll is None:
            return
        try:
            for r in runs:
                coll.insert_one({**r, "agent_id": self.agent_id})
        except Exception as e:
            logger.warning(f"[NEXUS] Failed to record sync_runs: {e}")

    async def _emit_sync_failed(self, integ_type: str, error: str):
        from ..registry import dispatch_event
        try:
            await dispatch_event(
                "sync.failed",
                {"integration": integ_type, "error": error or "unknown"},
                source=self.agent_id,
            )
        except Exception:
            pass

    async def on_event(self, event: str, payload: Dict[str, Any]):
        """Webhook events → drain the queue immediately."""
        if event == "webhook.received":
            integ = payload.get("integration")
            if integ:
                logger.info(f"[NEXUS] Webhook for {integ} — running sync")
                await self._run_integration_sync(integ)

    async def run(self, query: str, context: AgentContext) -> AgentResponse:
        """On-demand: report recent sync runs."""
        coll = self.get_collection("sync_runs")
        if coll is None:
            return AgentResponse(success=False, agent_id=self.agent_id, message="sync_runs collection unavailable")
        try:
            recent = list(coll.find({"agent_id": self.agent_id}, {"_id": 0}).sort("ran_at", -1).limit(20))
        except Exception as e:
            return AgentResponse(success=False, agent_id=self.agent_id, message=str(e))
        return AgentResponse(
            success=True,
            agent_id=self.agent_id,
            data={"recent_syncs": recent, "session_count": self._sync_runs},
            message=f"NEXUS: {len(recent)} recent sync run(s)",
        )
