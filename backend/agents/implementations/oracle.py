"""
IMS 2.0 — ORACLE: AI Analysis & Intelligence
==============================================
Hero Identity: Oracle / Barbara Gordon (DC)
"After hanging up the Batgirl cowl, Barbara became Oracle — the all-seeing
analyst who feeds intelligence to every hero. ORACLE does the same:
analyzing every data stream to surface insights that drive action."

ORACLE runs hourly anomaly scans + an end-of-day full sweep:
  - Sales anomaly detection (today's revenue vs 4-week trailing average)
  - Discount abuse pattern detection (staff hitting cap repeatedly)
  - Demand forecasting (seasonal, weekly cycle, festival)
  - Customer churn risk scoring
  - Fraud detection (impossible Rx values, suspicious patterns)

Default schedule: Hourly cron + daily 10 PM EOD sweep.
Output: anomalies dropped into MongoDB `anomalies` collection.
Severity-based, with `anomaly.detected` event emitted to CORTEX +
TASKMASTER (TASKMASTER may auto-create a task if severity ≥ HIGH).
"""

from typing import Dict, Any, List
from datetime import datetime, timezone, timedelta
import json
import logging
import statistics
import uuid

from ..base import JarvisAgent, AgentType, AgentResponse, AgentContext
from ..claude_client import call_claude, call_claude_json, is_claude_available
from api.utils.ist import now_ist

logger = logging.getLogger(__name__)


# System prompt for anomaly narrative enrichment. Kept short; ORACLE tick
# fires hourly and each anomaly = one Claude call, so we want cheap Haiku.
_ANOMALY_NARRATIVE_SYSTEM = """You are ORACLE, the analysis agent for an Indian \
optical retail chain (Better Vision Optics). You receive a raw anomaly \
detection and your job is to produce a one-paragraph plain-English \
narrative explaining what likely happened and a single actionable \
recommendation.

Rules:
- The store owner is reading this at a glance. Be direct, no fluff.
- Indian Rupees in ₹, lakhs as "L" (e.g. ₹12.4L), crores as "Cr".
- Don't invent numbers that aren't in the input.
- Recommendation must be concrete and executable by a store manager in one shift.
- Output strictly the JSON object requested, no markdown fence.
"""


_ON_DEMAND_SYSTEM = """You are ORACLE, the analysis brain for Better Vision \
Optics. You help the CEO investigate questions that start with "why" or \
"what's driving" by correlating recent anomalies, sales data, and \
inventory metrics. Be a detective, not a spokesperson. Cite specific \
numbers from the provided context. If the data doesn't support a \
conclusion, say so directly."""


class OracleAgent(JarvisAgent):
    """AI analysis — anomaly scan, demand forecast, fraud, churn."""

    agent_id = "oracle"
    agent_name = "ORACLE"
    agent_type = AgentType.ANALYZER
    description = "AI analysis — anomaly scan, demand forecasting, churn risk, fraud detection"
    version = "1.0.0"
    toggleable = True

    capabilities = [
        "sales_anomaly_detection",
        "discount_abuse_detection",
        "demand_forecast",
        "churn_risk_scoring",
        "fraud_detection",
        "eod_sweep",
    ]

    def __init__(self, db=None):
        super().__init__(db=db)
        self._anomalies_found = 0

    async def _do_background_work(self):
        """
        Hourly anomaly scan. EOD sweep is hour=22 (10 PM) — does the same
        plus a full demand forecast refresh (left as Phase 4).
        """
        now = now_ist()
        is_eod = now.hour == 22  # 10 PM IST hourly slot doubles as EOD

        anomalies: List[Dict[str, Any]] = []

        # 1. Sales anomaly: today's revenue vs 4-week same-day-of-week average
        anomalies.extend(await self._detect_sales_anomalies())

        # 2. Discount abuse: staff with > 3 max-cap discounts today
        anomalies.extend(await self._detect_discount_abuse())

        # 3. Fraud signal: impossible Rx values (SPH > 25, etc.) — would
        #    have been caught at validation but defense-in-depth catches
        #    anything sneaking through
        anomalies.extend(await self._detect_rx_anomalies())

        # Enrich each anomaly with a Claude narrative + recommendation
        # BEFORE persisting. Failing soft: if Claude is unavailable or times
        # out, the anomaly persists without the narrative (keys still present
        # but set to None) so downstream consumers don't blow up on missing
        # fields and we still get the raw signal.
        if anomalies and is_claude_available():
            for a in anomalies:
                narrative, recommendation = await self._enrich_with_claude(a)
                a["narrative"] = narrative
                a["recommended_action"] = recommendation
                a["ai_powered"] = narrative is not None
        else:
            for a in anomalies:
                a.setdefault("narrative", None)
                a.setdefault("recommended_action", None)
                a.setdefault("ai_powered", False)

        # Persist + emit
        if anomalies:
            await self._record_anomalies(anomalies, eod=is_eod)
            await self._emit_for_severe(anomalies)

        # 4. Low-stock -> enqueue a reversible draft_po CHANGE PROPOSAL for
        #    Superadmin review (SYSTEM_INTENT section 8). ORACLE no longer
        #    relies solely on TASKMASTER silently drafting POs; it surfaces
        #    the suggestion into the approval queue so a human approves before
        #    the (reversible) DRAFT PO is created. Fail-soft.
        proposals = await self._propose_reorders()

        # 5. F34 target-ticker milestones: a one-time celebratory bell to
        #    store-floor staff when a store's MTD revenue crosses a configured
        #    threshold of its monthly REVENUE budget. Fully fail-soft.
        milestones = await self._check_milestones()

        self._anomalies_found += len(anomalies)
        logger.info(f"[ORACLE] tick complete - {len(anomalies)} anomalies "
                    f"({'EOD' if is_eod else 'hourly'} scan); "
                    f"{sum(1 for a in anomalies if a.get('ai_powered'))} ai-enriched; "
                    f"{proposals} reorder proposal(s) enqueued; "
                    f"{milestones} target milestone(s) fired")

    async def _check_milestones(self) -> int:
        """
        F34: for each store with a current-month REVENUE budget, compute MTD
        revenue and fire a one-time bell to that store's floor staff
        (SALES_CASHIER/SALES_STAFF/CASHIER ONLY -- management gets none) for any
        milestone threshold crossed and not already in ``milestones_fired``.

        Atomic, single-document writes only: ``$addToSet milestones_fired`` on the
        budget doc guards against a same-month re-fire even across workers. A
        budget doc from a PREVIOUS month whose ``milestones_fired`` is dirty is
        reset to [] (month rollover). Returns the count of milestone crossings
        notified. Wrapped end-to-end in try/except -- never crashes the tick.
        """
        try:
            from api.services import ticker_service, policy_engine

            budgets_coll = self.get_collection("budgets")
            orders_coll = self.get_collection("orders")
            if budgets_coll is None:
                return 0

            period = ticker_service.current_period()
            milestone_pcts = policy_engine.get_policy(
                "ticker.milestone_pcts", scope={},
                default=ticker_service.DEFAULT_MILESTONE_PCTS,
            )
            if not isinstance(milestone_pcts, list):
                milestone_pcts = ticker_service.DEFAULT_MILESTONE_PCTS

            # Month rollover: any REVENUE budget for a PRIOR period with a
            # non-empty milestones_fired gets reset so next month starts clean.
            try:
                for stale in budgets_coll.find({
                    "head": "REVENUE",
                    "period": {"$ne": period},
                    "milestones_fired": {"$nin": [None, []]},
                }):
                    budgets_coll.update_one(
                        {"store_id": stale.get("store_id"),
                         "period": stale.get("period"), "head": "REVENUE"},
                        {"$set": {"milestones_fired": []}},
                    )
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[ORACLE] milestone rollover reset skipped: {e}")

            fired = 0
            for bdoc in budgets_coll.find({"period": period, "head": "REVENUE"}):
                store_id = bdoc.get("store_id")
                target = bdoc.get("planned_amount")
                if not store_id or not target or float(target) <= 0:
                    continue
                already = bdoc.get("milestones_fired") or []
                mtd = ticker_service.mtd_revenue(orders_coll, store_id)
                pct = mtd / float(target) * 100 if float(target) > 0 else 0
                crossings = ticker_service.crossed_milestones(pct, milestone_pcts, already)
                for m in crossings:
                    # Single-doc atomic guard: only adds if not present (prevents
                    # a same-month re-fire). We notify ONLY when this op actually
                    # added the threshold (no double-bell on a concurrent tick).
                    res = budgets_coll.update_one(
                        {"store_id": store_id, "period": period, "head": "REVENUE",
                         "milestones_fired": {"$ne": m}},
                        {"$addToSet": {"milestones_fired": m}},
                    )
                    if getattr(res, "modified_count", 0) < 1:
                        continue
                    self._notify_floor_staff(store_id, m)
                    try:
                        await self.emit_event("target.milestone_reached", {
                            "store_id": store_id, "pct": m,
                            "mtd_revenue": round(float(mtd), 2),
                        })
                    except Exception as e:  # noqa: BLE001
                        logger.debug(f"[ORACLE] milestone event emit failed: {e}")
                    fired += 1
            return fired
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[ORACLE] _check_milestones failed (fail-soft): {e}")
            return 0

    def _notify_floor_staff(self, store_id: str, pct: int) -> None:
        """Insert one TARGET_MILESTONE bell notification per store-floor user
        (SALES_CASHIER/SALES_STAFF/CASHIER) at ``store_id``. Management tiers get
        NONE. Fail-soft: a notification failure never propagates."""
        try:
            from api.services import ticker_service

            users_coll = self.get_collection("users")
            notif_coll = self.get_collection("notifications")
            if users_coll is None or notif_coll is None:
                return
            staff = list(users_coll.find({
                "store_ids": store_id,
                "roles": {"$in": list(ticker_service.FLOOR_NOTIFY_ROLES)},
                "is_active": True,
            }))
            if not staff:
                return
            now = now_ist().replace(tzinfo=None)
            day = now.strftime("%Y%m%d")
            docs = []
            for u in staff:
                uid = u.get("user_id") or str(u.get("_id"))
                if not uid:
                    continue
                docs.append({
                    "notification_id": f"NTF-{day}-{uuid.uuid4().hex[:8].upper()}",
                    "user_id": uid,
                    "store_id": store_id,
                    "type": "TARGET_MILESTONE",
                    "notification_type": "target_milestone",
                    "title": "Monthly target milestone",
                    "message": f"Your store reached {pct}% of the monthly target.",
                    "channels": ["IN_APP"],
                    "status": "PENDING",
                    "created_at": now,
                    "read_at": None,
                    "source": "oracle_agent",
                })
            if docs:
                notif_coll.insert_many(docs, ordered=False)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[ORACLE] milestone notification insert failed: {e}")

    async def _propose_reorders(self) -> int:
        """
        For SKUs below their reorder point, enqueue a reversible ``draft_po``
        change-proposal instead of acting. Approving it (Superadmin) creates
        a DRAFT purchase order via the shared executor; sending the PO to the
        vendor remains a separate, non-reversible, human step.

        De-duped per SKU+day so the hourly tick doesn't stack identical
        suggestions. Fully fail-soft — returns the count enqueued (0 on any
        problem) and never raises.
        """
        stock_coll = self.get_collection("stock_units")
        if stock_coll is None:
            return 0
        # Lazy import keeps ORACLE importable even if proposals module is absent
        try:
            from ..proposals import create_proposal
        except Exception as e:  # pragma: no cover
            logger.debug(f"[ORACLE] proposals module import failed: {e}")
            return 0

        enqueued = 0
        try:
            low_stock = list(stock_coll.find({
                "$expr": {"$lt": ["$quantity", "$reorder_point"]},
            }).limit(20))
        except Exception as e:
            logger.debug(f"[ORACLE] low-stock scan error: {e}")
            return 0

        today = datetime.now(timezone.utc).strftime("%y%m%d")
        for item in low_stock:
            sku = item.get("sku")
            if not sku:
                continue
            on_hand = item.get("quantity", 0)
            reorder_point = item.get("reorder_point", 0)
            suggested_qty = max(reorder_point * 2 - on_hand, 1)
            try:
                result = create_proposal(
                    self.db,
                    created_by_agent=self.agent_id,
                    proposal_type="draft_po",
                    title=f"Reorder {sku} — stock {on_hand} < reorder point {reorder_point}",
                    rationale=(
                        f"On-hand quantity {on_hand} for SKU {sku} is below its "
                        f"reorder point of {reorder_point}. Approving drafts a "
                        f"purchase order for {suggested_qty} unit(s) (NOT sent to "
                        f"the vendor — sending is a separate manual step)."
                    ),
                    payload={
                        "sku": sku,
                        "quantity": suggested_qty,
                        "vendor_id": item.get("default_vendor_id"),
                        "on_hand": on_hand,
                        "reorder_point": reorder_point,
                    },
                    before_state={"sku": sku, "on_hand": on_hand,
                                  "reorder_point": reorder_point},
                    dedupe_key=f"draft_po:{sku}:{today}",
                )
                if result:
                    enqueued += 1
            except Exception as e:  # pragma: no cover — defensive
                logger.debug(f"[ORACLE] failed to enqueue reorder proposal for {sku}: {e}")
        return enqueued

    async def _detect_sales_anomalies(self) -> List[Dict[str, Any]]:
        """Compare today's revenue against trailing 4-week average."""
        coll = self.get_collection("orders")
        if coll is None:
            return []
        try:
            now = datetime.now(timezone.utc)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_total = sum(
                o.get("grand_total", 0) or 0
                for o in coll.find({"created_at": {"$gte": today_start.isoformat()}})
            )
            # Trailing 4 same-weekdays
            same_weekday_totals = []
            for weeks_ago in (1, 2, 3, 4):
                day = today_start - timedelta(days=7 * weeks_ago)
                day_end = day + timedelta(days=1)
                day_total = sum(
                    o.get("grand_total", 0) or 0
                    for o in coll.find({
                        "created_at": {"$gte": day.isoformat(), "$lt": day_end.isoformat()},
                    })
                )
                same_weekday_totals.append(day_total)
            if not same_weekday_totals or all(t == 0 for t in same_weekday_totals):
                return []
            avg = statistics.mean(same_weekday_totals)
            if avg <= 0:
                return []
            delta_pct = (today_total - avg) / avg * 100
            if abs(delta_pct) >= 30:
                return [{
                    "kind": "sales_anomaly",
                    "severity": "HIGH" if abs(delta_pct) >= 50 else "MEDIUM",
                    "summary": f"Today's sales {delta_pct:+.1f}% vs 4-wk same-weekday average",
                    "today_total": today_total,
                    "baseline_avg": avg,
                    "delta_pct": delta_pct,
                }]
        except Exception as e:
            logger.debug(f"[ORACLE] Sales anomaly scan error: {e}")
        return []

    async def _detect_discount_abuse(self) -> List[Dict[str, Any]]:
        """Flag staff hitting their discount cap > 3 times in one day."""
        coll = self.get_collection("orders")
        if coll is None:
            return []
        try:
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            tally: Dict[str, int] = {}
            for o in coll.find({"created_at": {"$gte": today_start}, "max_cap_discount": True}):
                staff = o.get("created_by") or "unknown"
                tally[staff] = tally.get(staff, 0) + 1
            anomalies = []
            for staff, n in tally.items():
                if n >= 3:
                    anomalies.append({
                        "kind": "discount_abuse",
                        "severity": "MEDIUM",
                        "summary": f"Staff {staff} hit discount cap {n}× today",
                        "staff": staff,
                        "count": n,
                    })
            return anomalies
        except Exception as e:
            logger.debug(f"[ORACLE] Discount abuse scan error: {e}")
            return []

    async def _detect_rx_anomalies(self) -> List[Dict[str, Any]]:
        """Defense-in-depth: catch any Rx with values outside business range."""
        coll = self.get_collection("prescriptions")
        if coll is None:
            return []
        try:
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            anomalies = []
            for rx in coll.find({"created_at": {"$gte": today_start}}).limit(200):
                for eye_key in ("right_eye", "left_eye"):
                    eye = rx.get(eye_key) or {}
                    sph_raw = eye.get("sph")
                    if sph_raw in (None, "", "0"):
                        continue
                    try:
                        sph = float(sph_raw)
                    except (TypeError, ValueError):
                        continue
                    if abs(sph) > 20:  # business rule: SPH ±20
                        anomalies.append({
                            "kind": "rx_out_of_range",
                            "severity": "HIGH",
                            "summary": f"Rx {rx.get('prescription_id')} {eye_key} SPH={sph} exceeds ±20.00 limit",
                            "prescription_id": rx.get("prescription_id"),
                            "eye": eye_key,
                            "sph": sph,
                        })
            return anomalies
        except Exception as e:
            logger.debug(f"[ORACLE] Rx anomaly scan error: {e}")
            return []

    async def _record_anomalies(self, anomalies: List[Dict[str, Any]], eod: bool):
        coll = self.get_collection("anomalies")
        if coll is None:
            return
        try:
            for a in anomalies:
                coll.insert_one({
                    **a,
                    "agent_id": self.agent_id,
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                    "scan_type": "eod" if eod else "hourly",
                    "resolved": False,
                })
        except Exception as e:
            logger.warning(f"[ORACLE] Failed to record anomalies: {e}")

    async def _emit_for_severe(self, anomalies: List[Dict[str, Any]]):
        """
        Emit anomaly.detected events for HIGH/CRITICAL items, and ping
        Slack for anything at or above the SLACK_ALERT_SEVERITY threshold
        (default CRITICAL). Slack is configured via SLACK_WEBHOOK_URL; if
        unset, the call silently no-ops.
        """
        from ..registry import dispatch_event

        # Optional import so ORACLE still runs in slim builds without the
        # observability module.
        try:
            from observability import notify_slack
        except Exception:  # pragma: no cover
            notify_slack = None

        for a in anomalies:
            severity = a.get("severity") or ""
            if severity not in ("HIGH", "CRITICAL"):
                continue

            try:
                await dispatch_event("anomaly.detected", a, source=self.agent_id)
            except Exception as e:
                logger.warning(f"[ORACLE] Event dispatch failed: {e}")

            # Slack alert — only the most severe rise to human attention.
            if notify_slack is not None:
                try:
                    metadata = {
                        "Kind": a.get("kind"),
                        "Severity": severity,
                    }
                    # Pull a few kind-specific fields into metadata for context
                    for k in ("today_total", "baseline_avg", "delta_pct",
                              "staff", "count", "prescription_id", "eye", "sph"):
                        if k in a and a[k] is not None:
                            metadata[k] = a[k]
                    await notify_slack(
                        severity=severity,
                        title=a.get("kind", "anomaly").replace("_", " ").title(),
                        body=a.get("narrative") or a.get("summary") or "No summary",
                        metadata=metadata,
                    )
                except Exception as e:
                    logger.warning(f"[ORACLE] Slack notify failed: {e}")

    # ------------------------------------------------------------------
    # Claude integration
    # ------------------------------------------------------------------

    async def _enrich_with_claude(self, anomaly: Dict[str, Any]) -> tuple:
        """
        Ask Claude for a one-paragraph narrative + concrete recommendation
        for a single anomaly. Returns (narrative, recommendation), either
        of which may be None on failure.
        """
        prompt = (
            "Here is an anomaly detected by our hourly scan. Explain in "
            "ONE paragraph what likely happened and give ONE concrete "
            "recommendation a store manager can act on this shift.\n\n"
            "Anomaly JSON:\n" + json.dumps(anomaly, default=str, indent=2) + "\n\n"
            "Respond with this JSON shape:\n"
            '{"narrative": "<one paragraph>", "recommendation": "<one imperative sentence>"}'
        )
        result = await call_claude_json(_ANOMALY_NARRATIVE_SYSTEM, prompt, max_tokens=400)
        if not result or not isinstance(result, dict):
            return None, None
        return result.get("narrative"), result.get("recommendation")

    async def _build_context_for_query(self, query: str) -> Dict[str, Any]:
        """
        Bundle recent data the run() query can reason over without
        needing a DB of its own. Everything is sampled — we cap each
        slice at a small number to keep the Claude token budget sane.
        """
        context: Dict[str, Any] = {"query": query}

        # Recent unresolved anomalies
        anomalies_coll = self.get_collection("anomalies")
        if anomalies_coll is not None:
            try:
                context["recent_anomalies"] = list(
                    anomalies_coll.find({"resolved": False}, {"_id": 0})
                    .sort("detected_at", -1)
                    .limit(15)
                )
            except Exception:
                context["recent_anomalies"] = []

        # Last 7 days revenue by day
        orders_coll = self.get_collection("orders")
        if orders_coll is not None:
            try:
                now = datetime.now(timezone.utc)
                by_day: Dict[str, float] = {}
                for d in range(7):
                    day_start = (now - timedelta(days=d)).replace(hour=0, minute=0, second=0, microsecond=0)
                    day_end = day_start + timedelta(days=1)
                    day_total = sum(
                        o.get("grand_total", 0) or 0
                        for o in orders_coll.find(
                            {"created_at": {"$gte": day_start.isoformat(), "$lt": day_end.isoformat()}},
                            {"grand_total": 1},
                        )
                    )
                    by_day[day_start.date().isoformat()] = round(float(day_total), 2)
                context["revenue_last_7d"] = by_day
            except Exception:
                context["revenue_last_7d"] = {}

        return context

    async def run(self, query: str, context: AgentContext) -> AgentResponse:
        """
        On-demand: answer analytical questions. Two modes:
          - Claude available: bundle recent anomalies + 7-day revenue
            into context, let Claude reason across them, return narrative
          - Claude unavailable: fall back to listing recent unresolved
            anomalies (the pre-Phase-4 behavior)
        """
        coll = self.get_collection("anomalies")
        if coll is None:
            return AgentResponse(
                success=False,
                agent_id=self.agent_id,
                message="anomalies collection unavailable",
            )

        try:
            recent = list(coll.find({"resolved": False}, {"_id": 0}).sort("detected_at", -1).limit(20))
        except Exception as e:
            return AgentResponse(success=False, agent_id=self.agent_id, message=str(e))

        # If Claude isn't available, return the raw list (old behavior)
        if not is_claude_available():
            return AgentResponse(
                success=True,
                agent_id=self.agent_id,
                data={"unresolved_anomalies": recent, "count": len(recent), "ai_powered": False},
                message=f"ORACLE: {len(recent)} unresolved anomaly/ies (deterministic)",
            )

        # Claude path — assemble context and ask
        ctx = await self._build_context_for_query(query)
        user_prompt = (
            f"Question: {query}\n\n"
            f"Context (JSON):\n{json.dumps(ctx, default=str, indent=2)}\n\n"
            "Using only the data above, produce a grounded answer. Cite "
            "specific numbers. If the data doesn't answer the question, "
            "say which collection would need to be checked and stop."
        )
        answer = await call_claude(_ON_DEMAND_SYSTEM, user_prompt, max_tokens=800)

        if not answer:
            # Soft fallback — Claude failed, still return the list
            return AgentResponse(
                success=True,
                agent_id=self.agent_id,
                data={"unresolved_anomalies": recent, "count": len(recent), "ai_powered": False},
                message=f"ORACLE: Claude unavailable, returning {len(recent)} raw anomaly/ies",
            )

        return AgentResponse(
            success=True,
            agent_id=self.agent_id,
            data={
                "answer": answer,
                "grounded_on": {
                    "anomaly_count": len(ctx.get("recent_anomalies", [])),
                    "revenue_days": len(ctx.get("revenue_last_7d", {})),
                },
                "ai_powered": True,
            },
            message=f"ORACLE answered using {len(ctx.get('recent_anomalies', []))} recent anomaly/ies",
        )

