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

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
import logging

from ..base import JarvisAgent, AgentType, AgentResponse, AgentContext
from api.utils.ist import now_ist
from ..nexus_providers import (
    SyncResult,
    shopify_pull_orders,
    razorpay_list_payments,
    shiprocket_track_awb,
    tally_build_day_voucher_xml,
    validate_voucher_balance,
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

        now = now_ist()
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

    async def _build_tally_export(self, target_date: Optional[datetime] = None,
                                   store_id: Optional[str] = None) -> SyncResult:
        """
        Nightly Tally XML export — split per active store.

        For each active store with at least one matching order on
        `target_date` (defaults to today UTC), generates a voucher XML,
        runs a balance-validation gate (taxable + tax ≈ grand_total per
        order; subtotal arithmetic per batch), and writes one row per
        (date, store_id) tuple to `tally_exports`.

        Per the user direction (Phase I-6) the CA imports the XML in a
        Tally Company **per branch** through Remote Desktop, so each
        store gets its own download. Old single-doc rows (pre-I-6)
        remain queryable; new rows are distinguished by the presence
        of a `store_id` field.

        When `store_id` is supplied (manual /regenerate trigger), only
        that store is processed. Without it, all active stores run.

        When the orders for a store fail balance validation, the row
        is still written (so the operator can see what's wrong) but
        with `balanced=False`, `balance_issues`, and an `_UNBALANCED`
        XML filename suffix. An `agent.event("tally.unbalanced", ...)`
        is emitted so SENTINEL can flag it.
        """
        orders_coll = self.get_collection("orders")
        export_coll = self.get_collection("tally_exports")
        if orders_coll is None or export_coll is None:
            return SyncResult(ok=True, provider="tally", kind="export",
                              notes="required collection unavailable — heartbeat")

        # Resolve the day-window for the export
        day_anchor = target_date or datetime.now(timezone.utc)
        day_start = day_anchor.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        export_date_iso = day_start.isoformat()

        # created_at is stored as a naive-UTC BSON DATETIME for both POS orders
        # (BaseRepository._add_timestamps) and online orders (shopify_ingest now
        # writes datetimes too). LEGACY online orders wrote ISO STRINGS. MongoDB
        # type-brackets a Date range away from a string field, so query BOTH shapes
        # via $or -- otherwise one type is silently dropped from the Tally day
        # export. (Reused for the store loop below; see _dt_or_str_created_range.)
        ds_dt = day_start.replace(tzinfo=None) if getattr(day_start, "tzinfo", None) else day_start
        de_dt = day_end.replace(tzinfo=None) if getattr(day_end, "tzinfo", None) else day_end
        created_or = [
            {"created_at": {"$gte": ds_dt, "$lt": de_dt}},
            {"created_at": {"$gte": day_start.isoformat(), "$lt": day_end.isoformat()}},
        ]

        # Resolve the list of stores to process
        try:
            from api.dependencies import get_store_repository
            store_repo = get_store_repository()
        except Exception as e:
            logger.warning(f"[NEXUS] StoreRepository unavailable: {e}")
            store_repo = None

        if store_id:
            store = store_repo.find_by_id(store_id) if store_repo else None
            stores = [store] if store else []
            if not stores:
                return SyncResult(ok=False, provider="tally", kind="export",
                                  error=f"store_id '{store_id}' not found")
        else:
            stores = store_repo.find_active() if store_repo else []
            if not stores:
                # Fallback: no store directory available — single-row legacy path
                logger.warning("[NEXUS] No active stores; falling back to chain-wide single export.")
                return await self._build_tally_export_legacy(day_start, day_end, orders_coll, export_coll)

        rows_written = 0
        unbalanced_stores: List[str] = []
        total_vouchers = 0

        for store in stores:
            sid = store.get("store_id")
            if not sid:
                continue
            try:
                orders = list(orders_coll.find({
                    "$or": created_or,
                    "status": {"$in": ["COMPLETED", "DELIVERED", "PAID"]},
                    "store_id": sid,
                }))
            except Exception as e:
                logger.warning(f"[NEXUS] orders query failed for store {sid}: {e}")
                continue

            if not orders:
                continue

            balance = validate_voucher_balance(orders)
            store_meta = {
                "store_id": sid,
                "store_code": store.get("store_code") or sid,
                "store_name": store.get("store_name") or sid,
            }
            xml = tally_build_day_voucher_xml(orders, store_meta=store_meta)

            row = {
                "agent_id": self.agent_id,
                "export_date": export_date_iso,
                "store_id": sid,
                "store_code": store_meta["store_code"],
                "store_name": store_meta["store_name"],
                "voucher_count": len(orders),
                "xml": xml,
                "balanced": balance["ok"],
                "balance_check": balance,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "consumed": False,
            }

            try:
                # Replace any prior row for the same (date, store_id) so
                # /regenerate is idempotent. Compound natural key.
                export_coll.update_one(
                    {"export_date": export_date_iso, "store_id": sid},
                    {"$set": row},
                    upsert=True,
                )
                rows_written += 1
                total_vouchers += len(orders)
                if not balance["ok"]:
                    unbalanced_stores.append(sid)
            except Exception as e:
                logger.warning(f"[NEXUS] tally_exports write failed for store {sid}: {e}")

        if unbalanced_stores:
            try:
                from ..registry import dispatch_event
                await dispatch_event(
                    "tally.unbalanced",
                    {"date": export_date_iso, "stores": unbalanced_stores},
                )
            except Exception:
                pass  # event bus is fail-soft

        if rows_written == 0:
            return SyncResult(ok=True, provider="tally", kind="export",
                              notes="No completed orders today across active stores.")

        notes = f"Exported {rows_written} store row(s), {total_vouchers} voucher(s) total"
        if unbalanced_stores:
            notes += f", {len(unbalanced_stores)} unbalanced"
        return SyncResult(
            ok=True,
            provider="tally",
            kind="export",
            items_synced=total_vouchers,
            notes=notes,
        )

    async def _build_tally_export_legacy(self, day_start, day_end, orders_coll, export_coll) -> SyncResult:
        """Single-row chain-wide export — used when StoreRepository
        is unavailable so we never silently skip the export. Mirrors
        the pre-I-6 behavior."""
        # Dual-type created_at match: orders persist created_at as a naive-UTC BSON
        # datetime (POS + online), but legacy online orders wrote ISO strings -- a
        # Date range never matches a string field (Mongo type bracketing), so $or
        # both shapes to avoid silently dropping either from the export.
        ds_dt = day_start.replace(tzinfo=None) if getattr(day_start, "tzinfo", None) else day_start
        de_dt = day_end.replace(tzinfo=None) if getattr(day_end, "tzinfo", None) else day_end
        try:
            todays_orders = list(orders_coll.find({
                "$or": [
                    {"created_at": {"$gte": ds_dt, "$lt": de_dt}},
                    {"created_at": {"$gte": day_start.isoformat(), "$lt": day_end.isoformat()}},
                ],
                "status": {"$in": ["COMPLETED", "DELIVERED", "PAID"]},
            }))
        except Exception as e:
            return SyncResult(ok=False, provider="tally", kind="export", error=str(e))

        if not todays_orders:
            return SyncResult(ok=True, provider="tally", kind="export",
                              notes="No completed orders today (legacy path)")

        balance = validate_voucher_balance(todays_orders)
        xml = tally_build_day_voucher_xml(todays_orders)
        try:
            export_coll.insert_one({
                "agent_id": self.agent_id,
                "export_date": day_start.isoformat(),
                "voucher_count": len(todays_orders),
                "xml": xml,
                "balanced": balance["ok"],
                "balance_check": balance,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "consumed": False,
            })
        except Exception as e:
            return SyncResult(ok=False, provider="tally", kind="export",
                              error=f"tally_exports write failed: {e}")

        return SyncResult(
            ok=True, provider="tally", kind="export",
            items_synced=len(todays_orders),
            notes=f"Legacy chain-wide export: {len(todays_orders)} voucher(s)",
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
        """
        Webhook events — Phase I-2.

        Two payload shapes are handled here:

        1. {"webhook_id": <uuid>, "vendor": "razorpay|shopify|shiprocket"}
           Emitted by `api/routers/webhooks.py` after a signed inbound POST.
           We look up the inbox row, dispatch to the vendor-specific
           handler stub, and mark the row processed=true.

        2. {"integration": "shopify"}  (legacy / scheduler-style)
           Older callers that just want to nudge a poll. We re-run the
           integration's sync.
        """
        if event != "webhook.received":
            return

        webhook_id = payload.get("webhook_id")
        vendor = payload.get("vendor") or payload.get("integration")

        if webhook_id and vendor:
            await self._handle_inbox_webhook(webhook_id, vendor)
            return

        # Legacy/scheduler shape — just trigger a sync.
        if vendor:
            logger.info(f"[NEXUS] Webhook nudge for {vendor} — running sync")
            await self._run_integration_sync(vendor)

    async def _handle_inbox_webhook(self, webhook_id: str, vendor: str):
        """
        Read the inbox row, hand off to vendor handler, mark processed.

        Stays fail-soft: a missing inbox row, a handler exception, or a
        Mongo update failure is logged but never raised — the agent loop
        must keep ticking.
        """
        coll = self.get_collection("webhook_inbox")
        if coll is None:
            logger.warning(f"[NEXUS] webhook_inbox collection unavailable — cannot drain {webhook_id}")
            return

        try:
            doc = coll.find_one({"webhook_id": webhook_id})
        except Exception as e:
            logger.warning(f"[NEXUS] inbox lookup failed for {webhook_id}: {e}")
            return

        if not doc:
            logger.info(f"[NEXUS] inbox row not found for webhook_id={webhook_id} (already swept?)")
            return

        if doc.get("processed"):
            # Idempotent — another worker already drained it.
            return

        webhook_payload = doc.get("payload") or {}
        handler_error: str = ""
        try:
            if vendor == "razorpay":
                await self._handle_razorpay_webhook(webhook_payload)
            elif vendor == "shopify":
                # Stash the inbox headers so the handler can read the Shopify
                # topic (orders/create) + the X-Shopify-Webhook-Id for the
                # idempotent ingestion, WITHOUT changing the handler's 1-arg
                # call signature (the handler still takes just `payload`).
                self._current_webhook_headers = doc.get("headers") or {}
                await self._handle_shopify_webhook(webhook_payload)
            elif vendor == "shiprocket":
                await self._handle_shiprocket_webhook(webhook_payload)
            else:
                handler_error = f"unknown_vendor:{vendor}"
                logger.warning(f"[NEXUS] no handler for vendor={vendor!r} (webhook_id={webhook_id})")
        except Exception as e:
            handler_error = f"{type(e).__name__}: {e}"
            logger.warning(f"[NEXUS] {vendor} handler raised on {webhook_id}: {e}")

        # Mark processed regardless of handler outcome — the inbox row's
        # job is "we received and acknowledged this". Handler-specific
        # state (retries, dead-letter) goes on a future iteration.
        try:
            coll.update_one(
                {"webhook_id": webhook_id},
                {"$set": {
                    "processed": True,
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                    **({"handler_error": handler_error} if handler_error else {}),
                }},
            )
        except Exception as e:
            logger.warning(f"[NEXUS] processed-flag update failed on {webhook_id}: {e}")

    # ------------------------------------------------------------------
    # Vendor-specific handlers — stubs for now. Each one is the logical
    # entry point for "translate this vendor envelope into an IMS-side
    # mutation". MVP: log the event type. Phase I-3 will fill in:
    #   razorpay:  payment.captured -> mark order PAID, refund.created -> mark REFUNDED
    #   shopify:   orders/create -> sync inbound order, products/update -> refresh catalog mirror
    #   shiprocket: shipment status -> update order.tracking_status
    # ------------------------------------------------------------------

    async def _handle_razorpay_webhook(self, payload: Dict[str, Any]):
        evt = payload.get("event") or payload.get("type") or "unknown"
        logger.info(f"[NEXUS] razorpay webhook event={evt}")

    # Shopify ORDER topics the mapper handles. orders/create + orders/paid create
    # the IMS order (count-once); orders/updated + orders/cancelled SYNC the status
    # of the order already booked (the mapper never creates a 2nd for the same id).
    _SHOPIFY_ORDER_TOPICS = (
        "orders/create",
        "orders/paid",
        "orders/updated",
        "orders/cancelled",
        "orders/fulfilled",
        "orders/partially_fulfilled",
    )

    # BVI-retirement phase 0: the non-order Shopify topics IMS must handle once BVI
    # is retired. A dispatch table (topic -> handler-method name) replaces the old
    # blanket "non-order topics just log" early-return. Each handler is fail-soft.
    #   refunds/create      -> GST credit note (output-tax reversal) + stock restock
    #   fulfillments/*      -> reconcile shipped/tracking onto the IMS online order
    #   customers/*         -> upsert into IMS CRM (dedupe on mobile/email, merge)
    #   orders/delete       -> VOID the IMS online order (soft; never hard-delete)
    #   customers/delete    -> flag the IMS customer for data erasure (keep history)
    #   app/uninstalled     -> loud alert + integration-health record (token gone)
    _SHOPIFY_TOPIC_HANDLERS = {
        "refunds/create": "_handle_shopify_refund_topic",
        "fulfillments/create": "_handle_shopify_fulfillment_topic",
        "fulfillments/update": "_handle_shopify_fulfillment_topic",
        "customers/create": "_handle_shopify_customer_topic",
        "customers/update": "_handle_shopify_customer_topic",
        "orders/delete": "_handle_shopify_order_delete_topic",
        "customers/delete": "_handle_shopify_customer_delete_topic",
        "app/uninstalled": "_handle_shopify_app_uninstalled_topic",
    }

    # Catalog / collection / inventory reverse-sync is CONSCIOUSLY UNBUILT (owner
    # policy = edit-only-in-IMS: products, collections and stock levels are edited
    # in IMS and pushed OUT to Shopify, never pulled back). We match these topic
    # families so they log a clear, greppable "intentionally ignored" line rather
    # than falling through as an anonymous no-op.
    _SHOPIFY_EDIT_ONLY_PREFIXES = (
        "products/",
        "collections/",
        "inventory_levels/",
        "inventory_items/",
        "product_listings/",
    )

    async def _handle_shopify_webhook(self, payload: Dict[str, Any]):
        """Route a verified Shopify webhook to the right IMS handler via the
        topic dispatch table.

        For the seller's own bettervision.in storefront, IMS is the GST invoice
        system-of-record. ORDER topics route through api.services.online_order_
        mapper (the SINGLE authoritative Shopify-order -> IMS-order mapper); the
        non-order topics IMS took over from BVI (refunds / fulfillments /
        customers) route through their dedicated services. Catalog / collection /
        inventory topics are intentionally ignored (edit-only-in-IMS) but LOGGED so
        the choice is greppable, not silent.

        The Shopify topic + X-Shopify-Webhook-Id are read from the inbox headers
        stashed on `self._current_webhook_headers` by `_handle_inbox_webhook`
        (kept off the call signature so the existing 1-arg handler contract is
        preserved). They also fall back to fields on the payload body.

        Fail-soft: every handler swallows its own errors and SIMULATES when the DB
        is unavailable; any residual error is swallowed by the caller.
        """
        headers = getattr(self, "_current_webhook_headers", None)
        headers = headers if isinstance(headers, dict) else {}
        topic = (
            payload.get("topic")
            or payload.get("event")
            or headers.get("x-shopify-topic")
            or "unknown"
        )
        logger.info(f"[NEXUS] shopify webhook topic={topic}")
        t = str(topic).strip().lower()

        # Order topics keep their existing mapper path (count-once + status sync).
        if t in self._SHOPIFY_ORDER_TOPICS:
            await self._dispatch_shopify_order(payload, str(topic), headers)
            return

        # Non-order topics IMS now owns (BVI-retirement phase 0).
        handler_name = self._SHOPIFY_TOPIC_HANDLERS.get(t)
        if handler_name is not None:
            handler = getattr(self, handler_name, None)
            if handler is not None:
                await handler(payload, str(topic))
                return

        # Catalog / collection / inventory reverse-sync: consciously unbuilt.
        if t.startswith(self._SHOPIFY_EDIT_ONLY_PREFIXES):
            logger.info(
                "[NEXUS] shopify topic=%s intentionally ignored under "
                "edit-only-in-IMS policy (catalog/inventory reverse-sync "
                "consciously unbuilt)",
                topic,
            )
            return

        # Anything else: an unmapped topic. Log so it is greppable, do nothing.
        logger.info("[NEXUS] shopify topic=%s has no IMS handler -- ignored", topic)

    async def _dispatch_shopify_order(self, payload, topic: str, headers: Dict[str, Any]):
        """Translate a verified Shopify ORDER webhook into a canonical IMS order
        via api.services.online_order_mapper (Phase 3b):
          * orders/create + orders/paid -> idempotently create an IMS order tagged
            channel='ONLINE' with a consecutive GST tax invoice (per-line HSN +
            taxable + tax, IGST vs CGST+SGST by place of supply), AFTER resolving
            each Shopify variant -> catalog_variants -> the IMS sku and matching /
            creating the IMS customer.
          * orders/updated + orders/cancelled -> SYNC the existing order's
            payment / fulfillment / lifecycle status (NEVER a 2nd order).
        A replayed delivery (same Shopify order id or same X-Shopify-Webhook-Id)
        creates NOTHING further -- revenue is never double-counted. Fail-soft."""
        try:
            from api.services.online_order_mapper import map_shopify_order

            result = map_shopify_order(
                payload,
                self.db,
                webhook_id=headers.get("x-shopify-webhook-id"),
                topic=str(topic),
            )
            logger.info(
                "[NEXUS] shopify order map -> status=%s ims_order=%s invoice=%s "
                "customer=%s store=%s",
                result.get("status"),
                result.get("order_id"),
                result.get("invoice_number"),
                result.get("customer_id"),
                result.get("store_id"),
            )
        except Exception as e:  # noqa: BLE001 - never let mapping crash the loop
            logger.warning(f"[NEXUS] shopify order mapping failed: {e}")

    async def _handle_shopify_refund_topic(self, payload: Dict[str, Any], topic: str):
        """refunds/create -> GST credit note (output-tax reversal) + stock restock,
        via api.services.shopify_refund. DEFAULT routes to an accountant review
        queue; full auto-credit-note+restock is DARK behind SHOPIFY_REFUND_AUTO.
        Idempotent on the Shopify refund id. Fail-soft."""
        try:
            from api.services.shopify_refund import handle_shopify_refund

            headers = getattr(self, "_current_webhook_headers", None)
            headers = headers if isinstance(headers, dict) else {}
            result = handle_shopify_refund(
                self.db,
                payload,
                webhook_id=headers.get("x-shopify-webhook-id"),
                topic=str(topic),
            )
            logger.info(
                "[NEXUS] shopify refund -> status=%s refund=%s order=%s gross=%s",
                result.get("status"),
                result.get("refund_id"),
                result.get("order_id"),
                result.get("gross_refund"),
            )
        except Exception as e:  # noqa: BLE001 - never let a refund crash the loop
            logger.warning(f"[NEXUS] shopify refund handling failed: {e}")

    async def _handle_shopify_fulfillment_topic(self, payload: Dict[str, Any], topic: str):
        """fulfillments/create + fulfillments/update -> reconcile shipped status +
        tracking onto the matching IMS online order, via
        api.services.shopify_fulfillment. Fail-soft if the order isn't found."""
        try:
            from api.services.shopify_fulfillment import reconcile_fulfillment

            result = reconcile_fulfillment(self.db, payload, topic=str(topic))
            logger.info(
                "[NEXUS] shopify fulfilment -> status=%s order=%s fulfilment=%s "
                "order_status=%s",
                result.get("status"),
                result.get("order_id"),
                result.get("fulfillment_id"),
                result.get("order_status"),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[NEXUS] shopify fulfilment handling failed: {e}")

    async def _handle_shopify_customer_topic(self, payload: Dict[str, Any], topic: str):
        """customers/create + customers/update -> upsert into the IMS CRM (dedupe
        on mobile/email, merge don't clobber), via api.services.shopify_customer_
        sync. Fail-soft."""
        try:
            from api.services.shopify_customer_sync import upsert_shopify_customer

            result = upsert_shopify_customer(self.db, payload, topic=str(topic))
            logger.info(
                "[NEXUS] shopify customer -> status=%s customer=%s shopify_id=%s",
                result.get("status"),
                result.get("customer_id"),
                result.get("shopify_customer_id"),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[NEXUS] shopify customer handling failed: {e}")

    async def _handle_shopify_order_delete_topic(self, payload: Dict[str, Any], topic: str):
        """orders/delete -> VOID the matching IMS online order (soft status change +
        shopify_deleted_at stamp; NEVER hard-delete), via
        api.services.shopify_order_delete. Idempotent on the shopify_deleted_at
        marker. Fail-soft if the order isn't found."""
        try:
            from api.services.shopify_order_delete import handle_shopify_order_delete

            result = handle_shopify_order_delete(self.db, payload, topic=str(topic))
            logger.info(
                "[NEXUS] shopify order delete -> status=%s order=%s shopify_order=%s",
                result.get("status"),
                result.get("order_id"),
                result.get("shopify_order_id"),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[NEXUS] shopify order delete handling failed: {e}")

    async def _handle_shopify_customer_delete_topic(self, payload: Dict[str, Any], topic: str):
        """customers/delete -> flag the matching IMS customer for data erasure
        (shopify_erasure_requested + timestamp; NEVER hard-delete PII-linked
        history), via api.services.shopify_customer_delete. Idempotent. Fail-soft."""
        try:
            from api.services.shopify_customer_delete import handle_shopify_customer_delete

            result = handle_shopify_customer_delete(self.db, payload, topic=str(topic))
            logger.info(
                "[NEXUS] shopify customer delete -> status=%s customer=%s shopify_id=%s",
                result.get("status"),
                result.get("customer_id"),
                result.get("shopify_customer_id"),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[NEXUS] shopify customer delete handling failed: {e}")

    async def _handle_shopify_app_uninstalled_topic(self, payload: Dict[str, Any], topic: str):
        """app/uninstalled -> LOUD log + integration-health/alert record + a HIGH
        audit alert that the Shopify connection is gone (access token revoked), via
        api.services.shopify_app_uninstall. Idempotent (the HIGH alert fires once).
        Fail-soft."""
        try:
            from api.services.shopify_app_uninstall import handle_shopify_app_uninstalled

            result = await handle_shopify_app_uninstalled(self.db, payload, topic=str(topic))
            logger.error(
                "[NEXUS] shopify APP UNINSTALLED -> status=%s shop=%s alerted=%s",
                result.get("status"),
                result.get("shop"),
                result.get("alerted"),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[NEXUS] shopify app-uninstalled handling failed: {e}")

    async def _handle_shiprocket_webhook(self, payload: Dict[str, Any]):
        evt = payload.get("current_status") or payload.get("event") or "unknown"
        logger.info(f"[NEXUS] shiprocket webhook status={evt}")

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
