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
from datetime import datetime, timezone, timedelta
import logging

from ..base import JarvisAgent, AgentType, AgentResponse, AgentContext
from ..nexus_providers import (
    SyncResult,
    shopify_pull_orders,
    razorpay_list_payments,
    shiprocket_track_awb,
    tally_build_day_voucher_xml,
)

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
        Dispatch to the right provider client. Each call returns a
        SyncResult which we normalize into the sync_runs row shape.
        Fails soft — a single bad integration returns ok=False but
        doesn't stop the tick from processing the others.
        """
        ran_at = datetime.now(timezone.utc).isoformat()
        result: SyncResult

        try:
            if integ_type == "shopify":
                # Pull recent orders from Shopify for fulfillment routing.
                # Catalog push happens via explicit product-updated events.
                result = await shopify_pull_orders(self.db, since_hours=2)
            elif integ_type == "razorpay":
                # Reconcile payments — read-only, safe in any DISPATCH_MODE
                result = await razorpay_list_payments(self.db, since_hours=2)
            elif integ_type == "shiprocket":
                # Pull tracking for recently-shipped orders — one call per AWB.
                # MVP: just heartbeat for now; real iteration happens in
                # _sync_shiprocket_outbound below when we have outbound AWBs.
                result = await self._sync_shiprocket_outbound()
            elif integ_type == "tally":
                # Nightly export (only runs at 23:00 per INTEGRATION_SCHEDULES)
                result = await self._build_tally_export()
            else:
                # Integrations we declared but don't yet have a client for
                result = SyncResult(
                    ok=True,
                    provider=integ_type,
                    kind="pull",
                    items_synced=0,
                    notes=f"{integ_type} — no provider client yet, heartbeat only",
                )
        except Exception as e:
            logger.warning(f"[NEXUS] {integ_type} sync raised: {e}")
            result = SyncResult(ok=False, provider=integ_type, kind="pull", error=str(e))

        # Normalize for sync_runs collection
        return {
            "integration": integ_type,
            "ok": result.ok,
            "ran_at": ran_at,
            "kind": result.kind,
            "items_synced": result.items_synced,
            "error": result.error,
            "notes": result.notes,
            "payload": result.payload,
        }

    async def _sync_shiprocket_outbound(self) -> SyncResult:
        """
        For each order in SHIPPED state with an AWB, pull the latest
        tracking status from Shiprocket and update orders if changed.
        """
        orders_coll = self.get_collection("orders")
        if orders_coll is None:
            return SyncResult(ok=True, provider="shiprocket", kind="pull",
                              notes="orders collection unavailable — heartbeat")
        try:
            shipped_with_awb = list(orders_coll.find({
                "status": "SHIPPED",
                "awb": {"$exists": True, "$ne": ""},
            }).limit(50))
        except Exception as e:
            return SyncResult(ok=False, provider="shiprocket", kind="pull", error=str(e))

        updated = 0
        for order in shipped_with_awb:
            awb = order.get("awb")
            r = await shiprocket_track_awb(self.db, awb)
            if not r.ok:
                continue
            new_status = (r.payload or {}).get("latest_status")
            if new_status and new_status != order.get("tracking_status"):
                try:
                    orders_coll.update_one(
                        {"_id": order["_id"]},
                        {"$set": {"tracking_status": new_status,
                                  "tracking_updated_at": datetime.now(timezone.utc).isoformat()}},
                    )
                    updated += 1
                except Exception as e:
                    logger.warning(f"[NEXUS] Order tracking update failed: {e}")

        return SyncResult(
            ok=True, provider="shiprocket", kind="pull",
            items_synced=updated,
            notes=f"Checked {len(shipped_with_awb)} AWBs, {updated} status changes",
        )

    async def _build_tally_export(self) -> SyncResult:
        """
        Nightly Tally XML export — aggregate today's sales orders into
        a single voucher XML, write to tally_exports collection for the
        CA to download. Real push to Tally's HTTP Server is optional
        (some tenants prefer manual import).
        """
        orders_coll = self.get_collection("orders")
        export_coll = self.get_collection("tally_exports")
        if orders_coll is None or export_coll is None:
            return SyncResult(ok=True, provider="tally", kind="export",
                              notes="required collection unavailable — heartbeat")

        try:
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            todays_orders = list(orders_coll.find({
                "created_at": {"$gte": today.isoformat()},
                "status": {"$in": ["COMPLETED", "DELIVERED", "PAID"]},
            }))
        except Exception as e:
            return SyncResult(ok=False, provider="tally", kind="export", error=str(e))

        if not todays_orders:
            return SyncResult(ok=True, provider="tally", kind="export",
                              notes="No completed orders today — nothing to export")

        xml = tally_build_day_voucher_xml(todays_orders)

        try:
            export_coll.insert_one({
                "agent_id": self.agent_id,
                "export_date": today.isoformat(),
                "voucher_count": len(todays_orders),
                "xml": xml,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "consumed": False,
            })
        except Exception as e:
            return SyncResult(ok=False, provider="tally", kind="export",
                              error=f"tally_exports write failed: {e}")

        return SyncResult(
            ok=True, provider="tally", kind="export",
            items_synced=len(todays_orders),
            notes=f"Exported {len(todays_orders)} voucher(s), XML size {len(xml)} bytes",
        )

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
