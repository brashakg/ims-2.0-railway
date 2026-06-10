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
from api.utils.ist import now_ist, now_ist_naive, ist_day_start_utc

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

        # 3b. EOD ONLY (#40): VIP-churn scan. It writes each VIP's vip_churn_risk
        #     subdoc + the daily per-store snapshot and self-enriches its own
        #     top-10 narratives; it returns the HIGH-label anomalies (already
        #     carrying the narrative keys) so they persist + emit like the rest.
        if is_eod:
            # IST-NAIVE clock: orders' created_at is naive (datetime.now); subtracting
            # an aware now_ist() would TypeError and abort the whole EOD sweep.
            anomalies.extend(await self._scan_vip_churn(now_ist_naive()))

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

    async def _scan_vip_churn(self, now) -> List[Dict[str, Any]]:
        """#40 EOD VIP-churn scan. Aggregates each customer's booked orders ->
        LTV + completed-purchase dates + count; for VIPs (LTV >= 1,00,000 AND
        >= 3 orders) computes the personalised-interval churn subdoc and writes
        it back; enriches the top-10 by risk_score with a Claude one-liner;
        upserts ONE per-store daily snapshot; returns HIGH-label anomalies for
        emit. Fail-soft: returns [] if the DB/collections are unavailable."""
        from api.services.vip_churn import (
            compute_vip_churn, VIP_LTV_THRESHOLD, VIP_MIN_ORDERS,
        )

        customers = self.get_collection("customers")
        orders = self.get_collection("orders")
        if customers is None or orders is None:
            return []
        try:
            pipeline = [
                {"$match": {"status": {"$nin": ["DRAFT", "CANCELLED", "draft", "cancelled"]},
                            "customer_id": {"$nin": [None, ""]}}},
                {"$group": {"_id": "$customer_id",
                            "ltv": {"$sum": {"$ifNull": ["$grand_total", {"$ifNull": ["$total", 0]}]}},
                            "dates": {"$push": "$created_at"},
                            "count": {"$sum": 1}}},
                {"$match": {"ltv": {"$gte": VIP_LTV_THRESHOLD}, "count": {"$gte": VIP_MIN_ORDERS}}},
            ]
            agg = list(orders.aggregate(pipeline))
        except Exception:  # noqa: BLE001
            return []

        scored = []          # (risk_score, cid, name, store, sub, ltv) for WATCH/HIGH
        by_store: Dict[str, Dict[str, Any]] = {}
        for row in agg:
            cid = row.get("_id")
            dates = [d for d in (row.get("dates") or []) if isinstance(d, datetime)]
            sub = compute_vip_churn(dates, row.get("ltv", 0), row.get("count", 0), now)
            if sub is None:
                continue
            try:
                customers.update_one({"customer_id": cid}, {"$set": {"vip_churn_risk": sub}})
            except Exception:  # noqa: BLE001
                pass
            try:
                cdoc = customers.find_one(
                    {"customer_id": cid},
                    {"_id": 0, "name": 1, "full_name": 1, "primary_store_id": 1, "store_ids": 1},
                ) or {}
            except Exception:  # noqa: BLE001
                cdoc = {}
            store = cdoc.get("primary_store_id") or (cdoc.get("store_ids") or ["UNKNOWN"])[0] or "UNKNOWN"
            name = cdoc.get("name") or cdoc.get("full_name") or cid
            label = sub["risk_label"]
            st = by_store.setdefault(store, {"vip": 0, "watch": 0, "high": 0, "top": []})
            st["vip"] += 1
            if label == "WATCH":
                st["watch"] += 1
            elif label == "HIGH":
                st["high"] += 1
            if label in ("WATCH", "HIGH"):
                scored.append((sub["risk_score"], cid, name, store, sub, float(row.get("ltv", 0) or 0)))
                st["top"].append({"customer_id": cid, "name": name,
                                  "ltv": round(float(row.get("ltv", 0) or 0), 2),
                                  "overdue_by_days": sub["overdue_by_days"], "risk_label": label})

        # Top-10 by risk_score get a Claude narrative (hard cap; fail-soft).
        scored.sort(key=lambda x: -x[0])
        if is_claude_available():
            for _score, cid, name, _store, sub, _ltv in scored[:10]:
                try:
                    narrative, _rec = await self._enrich_with_claude({
                        "kind": "vip_churn",
                        "severity": "HIGH" if sub["risk_label"] == "HIGH" else "MEDIUM",
                        "summary": (f"VIP '{name}' overdue by {sub['overdue_by_days']}d "
                                    f"(usual interval {sub['usual_interval_days']}d)"),
                    })
                    if narrative:
                        customers.update_one({"customer_id": cid},
                                             {"$set": {"vip_churn_risk.narrative": narrative}})
                except Exception:  # noqa: BLE001
                    pass

        # One snapshot per store per day (upsert keyed on store + scan_date).
        snaps = self.get_collection("vip_churn_snapshots")
        if snaps is not None:
            scan_date = now.strftime("%Y-%m-%d")
            for store, st in by_store.items():
                try:
                    snaps.update_one(
                        {"store_id": store, "scan_date": scan_date},
                        {"$set": {"snapshot_id": f"VCS-{uuid.uuid4().hex[:10]}", "store_id": store,
                                  "scanned_at": now, "scan_date": scan_date,
                                  "vip_count": st["vip"], "watch_count": st["watch"],
                                  "high_risk_count": st["high"],
                                  "top_10": sorted(st["top"], key=lambda x: -x["overdue_by_days"])[:10]}},
                        upsert=True,
                    )
                except Exception:  # noqa: BLE001
                    pass

        # HIGH-label anomalies for _emit_for_severe (carry the narrative keys so
        # _record_anomalies / downstream consumers don't trip on missing fields).
        high_anomalies: List[Dict[str, Any]] = []
        for _score, cid, name, store, sub, _ltv in scored:
            if sub["risk_label"] == "HIGH":
                high_anomalies.append({
                    "kind": "vip_churn", "severity": "HIGH",
                    "summary": (f"VIP '{name}' overdue by {sub['overdue_by_days']} days "
                                f"(usual interval {sub['usual_interval_days']}d)"),
                    "customer_id": cid, "store_id": store,
                    "overdue_by_days": sub["overdue_by_days"],
                    "narrative": sub.get("narrative"), "recommended_action": None,
                    "ai_powered": sub.get("narrative") is not None,
                })
        logger.info(f"[ORACLE] VIP-churn EOD scan: {len(scored)} at-risk VIPs "
                    f"across {len(by_store)} store(s); {len(high_anomalies)} HIGH")
        return high_anomalies

    # Static lead-time used in the recommended-qty cover when a product carries
    # no per-product lead time. Conservative single value; the suggestion is a
    # human-reviewed DRAFT, so over/under by a few units is corrected at approve.
    _DEFAULT_LEAD_TIME_DAYS = 7

    def _reorder_horizon_days(self) -> int:
        """E2-configurable stockout horizon (#7). days_remaining below this
        triggers a reorder suggestion. Fail-soft to the 14-day default."""
        try:
            from api.services import policy_engine
            v = policy_engine.get_policy(
                "predictive_purchasing.horizon_days", scope={}, default=14,
            )
            v = int(v)
            return v if v >= 1 else 14
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[ORACLE] reorder horizon lookup failed (default 14): {e}")
            return 14

    async def _propose_reorders(self) -> int:
        """
        #7 Predictive purchasing. For every (product, store) that is on track to
        run OUT of stock within the configured horizon (default 14 days) at its
        recent SALES VELOCITY, enqueue a reversible ``draft_po`` change-proposal
        for human review. Approving it (SUPERADMIN/ADMIN) creates a DRAFT
        purchase order via the shared executor; SENDING the PO to the vendor
        remains a separate, non-reversible, human step.

        This is READ-ONLY analytics + a suggestion record only. It NEVER auto-
        creates or sends a PO that commits money. The trigger is the projected
        days-of-stock horizon (NOT a static reorder_point breach): a fast-moving
        SKU above its reorder point can still be flagged, and a slow SKU below it
        is left alone if demand won't exhaust it inside the horizon.

        Burn rate (units/day) is computed in PYTHON from booked ``orders`` line
        items over a 7-day + 30-day trailing window (no aggregation pipeline, so
        the behaviour is identical over a fake in-memory DB and real Mongo). The
        7-day rate is primary; a SKU with zero 7-day sales falls back to its 30-
        day rate so a weekly-selling SKU is not mistaken for a dead one.

        One proposal per (product_id, store_id) per day, de-duped on
        ``draft_po:{product_id}:{store_id}:{date}``. Capped at 50 per tick. Fully
        fail-soft - returns the count enqueued (0 on any problem) and never raises.
        """
        orders_coll = self.get_collection("orders")
        if orders_coll is None:
            return 0
        # Lazy imports keep ORACLE importable even if a module is absent.
        try:
            from ..proposals import create_proposal
            from ..predictive_reorder import (
                tally_demand_by_product_store, burn_rates, days_remaining,
                recommended_qty, projected_stockout_iso,
            )
        except Exception as e:  # pragma: no cover
            logger.debug(f"[ORACLE] predictive-reorder import failed: {e}")
            return 0

        now = now_ist_naive()
        horizon = self._reorder_horizon_days()

        # 1. Booked-order demand over the trailing 30 days (Python-side tally).
        try:
            orders = list(orders_coll.find({
                "status": {"$nin": ["CANCELLED", "DRAFT"]},
                "created_at": {"$gte": now - timedelta(days=30)},
            }))
        except Exception as e:
            logger.debug(f"[ORACLE] order demand scan error: {e}")
            # Fall back to an unfiltered scan if the DB rejects the filter shape
            # (e.g. a fake DB that ignores comparison operators on created_at).
            try:
                orders = list(orders_coll.find({}))
            except Exception:  # noqa: BLE001
                return 0

        demand = tally_demand_by_product_store(orders, now=now)
        if not demand:
            return 0

        # 2. On-hand per (product_id, store_id) from stock_units (AVAILABLE),
        #    summed in Python. Missing collection -> on_hand defaults to 0.
        #    P2 hardening: the scan is bounded SERVER-SIDE to the demanded
        #    product ids + on-hand-ish statuses instead of a full-collection
        #    scan every tick (stock_units is one doc per physical unit -- the
        #    biggest collection in the system). Semantics are preserved
        #    exactly: the status list mirrors item_events.ON_HAND_STATUSES
        #    (upper+lower variants, the same list inventory.py filters with),
        #    and `$in` with None matches BOTH a null and a MISSING status in
        #    real Mongo, so legacy blank/absent-status units still count as
        #    on-hand. The Python-side normalization below stays as the
        #    canonical filter so the fallback full scan behaves identically.
        wanted_ids = sorted({pid for (pid, _store) in demand.keys()})
        try:
            from api.services.item_events import ON_HAND_STATUSES as _on_hand_statuses
        except Exception:  # noqa: BLE001  # pragma: no cover - defensive
            _on_hand_statuses = ["AVAILABLE", "available", "IN_STOCK", "in_stock"]
        on_hand_map: Dict[tuple, int] = {}
        stock_coll = self.get_collection("stock_units")
        if stock_coll is not None:
            stock_query = {
                "status": {"$in": list(_on_hand_statuses) + [None, ""]},
                "$or": [
                    {"product_id": {"$in": wanted_ids}},
                    {"sku": {"$in": wanted_ids}},
                ],
            }
            stock_proj = {"_id": 0, "product_id": 1, "sku": 1, "store_id": 1,
                          "status": 1, "quantity": 1}
            try:
                units = list(stock_coll.find(stock_query, stock_proj))
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[ORACLE] filtered stock_units scan failed "
                             f"(full-scan fallback): {e}")
                try:
                    units = list(stock_coll.find({}))
                except Exception as e2:  # noqa: BLE001
                    logger.debug(f"[ORACLE] stock_units scan error: {e2}")
                    units = []
            for su in units:
                pid = su.get("product_id") or su.get("sku")
                if not pid:
                    continue
                status = str(su.get("status") or "").upper()
                # RESERVED is held for an order -> NOT sellable on-hand (mirrors
                # item_events.ON_HAND_STATUSES + inventory.py). Only AVAILABLE/
                # IN_STOCK (or a legacy blank status) count toward the reorder
                # on-hand signal; counting RESERVED would over-state stock and
                # SUPPRESS a legitimate reorder (the opposite of this feature's job).
                if status and status not in ("AVAILABLE", "IN_STOCK"):
                    continue
                key = (str(pid), str(su.get("store_id") or "UNKNOWN"))
                qty = su.get("quantity")
                qty = 1 if qty is None else qty
                try:
                    qty = int(qty)
                except (TypeError, ValueError):
                    qty = 0
                on_hand_map[key] = on_hand_map.get(key, 0) + qty

        # 3. Product master lookup for preferred_vendor_id + reorder_point.
        #    P2 hardening: server-side filter on the demanded ids over the SAME
        #    identifier fields the match loop reads (product_id / sku / _id --
        #    string _ids match; IMS products always carry a string product_id)
        #    + a projection, instead of a full products scan.
        products_coll = self.get_collection("products")
        prod_by_id: Dict[str, Dict[str, Any]] = {}
        if products_coll is not None:
            wanted = set(wanted_ids)
            prod_query = {"$or": [
                {"product_id": {"$in": wanted_ids}},
                {"sku": {"$in": wanted_ids}},
                {"_id": {"$in": wanted_ids}},
            ]}
            prod_proj = {"product_id": 1, "sku": 1, "preferred_vendor_id": 1,
                         "default_vendor_id": 1, "vendor_id": 1,
                         "reorder_point": 1}
            try:
                prods = list(products_coll.find(prod_query, prod_proj))
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[ORACLE] filtered products scan failed "
                             f"(full-scan fallback): {e}")
                try:
                    prods = list(products_coll.find({}))
                except Exception as e2:  # noqa: BLE001
                    logger.debug(f"[ORACLE] products scan error: {e2}")
                    prods = []
            for p in prods:
                for idf in ("product_id", "sku", "_id"):
                    val = p.get(idf)
                    if val is not None and str(val) in wanted:
                        prod_by_id.setdefault(str(val), p)

        today = now.strftime("%y%m%d")
        enqueued = 0
        CAP = 50
        for (pid, store_id), d in demand.items():
            if enqueued >= CAP:
                break
            rates = burn_rates(d["units_7d"], d["units_30d"])
            eff = rates["effective"]
            on_hand = on_hand_map.get((pid, store_id), 0)
            d_left = days_remaining(on_hand, eff)
            if not (d_left < horizon):
                continue  # not at risk inside the horizon -> no suggestion

            prod = prod_by_id.get(pid, {})
            preferred_vendor_id = (
                prod.get("preferred_vendor_id")
                or prod.get("default_vendor_id")
                or prod.get("vendor_id")
            )
            reorder_point = prod.get("reorder_point") or 0
            try:
                reorder_point = int(reorder_point)
            except (TypeError, ValueError):
                reorder_point = 0
            qty = recommended_qty(
                on_hand=on_hand, effective_rate=eff, horizon_days=horizon,
                lead_time_days=self._DEFAULT_LEAD_TIME_DAYS,
                reorder_point=reorder_point,
            )
            name = d.get("name") or pid
            brand = d.get("brand") or ""
            label = (f"{brand} {name}".strip()) or pid
            stockout = projected_stockout_iso(now, d_left)
            vendor_missing = not bool(preferred_vendor_id)

            try:
                result = create_proposal(
                    self.db,
                    created_by_agent=self.agent_id,
                    proposal_type="draft_po",
                    title=f"Reorder {label} - {d_left:.1f} days of stock left",
                    rationale=(
                        f"{label} at store {store_id} has {on_hand} on hand and is "
                        f"selling ~{eff:.2f}/day, so stock runs out in about "
                        f"{d_left:.1f} days - inside the {horizon}-day horizon. "
                        f"Approving drafts a purchase order for {qty} unit(s) "
                        f"(NOT sent to the vendor - sending is a separate manual "
                        f"step)."
                        + ("" if not vendor_missing else
                           " No preferred vendor is set on this product - assign "
                           "one before ordering.")
                    ),
                    payload={
                        "product_id": pid,
                        "sku": pid,
                        "store_id": store_id,
                        "quantity": qty,
                        "vendor_id": preferred_vendor_id,
                        "vendor_missing": vendor_missing,
                        "on_hand": on_hand,
                        "reorder_point": reorder_point,
                        "burn_rate_7d": round(rates["burn_rate_7d"], 4),
                        "burn_rate_30d": round(rates["burn_rate_30d"], 4),
                        "days_remaining": round(d_left, 2),
                        "projected_stockout_date": stockout,
                        "horizon_days": horizon,
                        "product_name": name,
                        "brand": brand,
                        "category": d.get("category") or "",
                    },
                    before_state={
                        "product_id": pid, "store_id": store_id,
                        "on_hand": on_hand, "days_remaining": round(d_left, 2),
                        "draft_po": None,
                    },
                    dedupe_key=f"draft_po:{pid}:{store_id}:{today}",
                )
                if result:
                    enqueued += 1
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(
                    f"[ORACLE] failed to enqueue reorder proposal for "
                    f"{pid}@{store_id}: {e}"
                )
        return enqueued

    async def _detect_sales_anomalies(self) -> List[Dict[str, Any]]:
        """Compare today's revenue against trailing 4-week average."""
        coll = self.get_collection("orders")
        if coll is None:
            return []
        try:
            # IST business day expressed in the naive-UTC frame `created_at` is
            # stored in. The old `.isoformat()` STRING bound never matched a
            # BSON Date (Mongo type bracketing) -- this scan silently found
            # ZERO orders on every tick. Datetime bounds also fix the
            # UTC-midnight (05:30 IST) day boundary.
            today_start = ist_day_start_utc()
            today_total = sum(
                o.get("grand_total", 0) or 0
                for o in coll.find({"created_at": {"$gte": today_start}})
            )
            # Trailing 4 same-weekdays (IST days are a constant 24h: no DST)
            same_weekday_totals = []
            for weeks_ago in (1, 2, 3, 4):
                day = today_start - timedelta(days=7 * weeks_ago)
                day_end = day + timedelta(days=1)
                day_total = sum(
                    o.get("grand_total", 0) or 0
                    for o in coll.find({
                        "created_at": {"$gte": day, "$lt": day_end},
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
            # Naive-UTC instant of IST midnight -- a string bound here matched
            # ZERO BSON-Date docs (type bracketing), making this scan dead.
            today_start = ist_day_start_utc()
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
            # Naive-UTC instant of IST midnight -- a string bound here matched
            # ZERO BSON-Date docs (type bracketing), making this scan dead.
            today_start = ist_day_start_utc()
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

