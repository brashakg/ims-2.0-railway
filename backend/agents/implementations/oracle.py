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
import logging
import statistics

from ..base import JarvisAgent, AgentType, AgentResponse, AgentContext

logger = logging.getLogger(__name__)


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
        now = datetime.now(timezone.utc)
        is_eod = now.hour == 22  # 10 PM hourly slot doubles as EOD

        anomalies: List[Dict[str, Any]] = []

        # 1. Sales anomaly: today's revenue vs 4-week same-day-of-week average
        anomalies.extend(await self._detect_sales_anomalies())

        # 2. Discount abuse: staff with > 3 max-cap discounts today
        anomalies.extend(await self._detect_discount_abuse())

        # 3. Fraud signal: impossible Rx values (SPH > 25, etc.) — would
        #    have been caught at validation but defense-in-depth catches
        #    anything sneaking through
        anomalies.extend(await self._detect_rx_anomalies())

        # Persist + emit
        if anomalies:
            await self._record_anomalies(anomalies, eod=is_eod)
            await self._emit_for_severe(anomalies)

        self._anomalies_found += len(anomalies)
        logger.info(f"[ORACLE] tick complete — {len(anomalies)} anomalies "
                    f"({'EOD' if is_eod else 'hourly'} scan)")

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
        """Emit anomaly.detected events for HIGH severity items."""
        from ..registry import dispatch_event
        for a in anomalies:
            if a.get("severity") in ("HIGH", "CRITICAL"):
                try:
                    await dispatch_event("anomaly.detected", a, source=self.agent_id)
                except Exception as e:
                    logger.warning(f"[ORACLE] Event dispatch failed: {e}")

    async def run(self, query: str, context: AgentContext) -> AgentResponse:
        """On-demand: list recent unresolved anomalies."""
        coll = self.get_collection("anomalies")
        if coll is None:
            return AgentResponse(success=False, agent_id=self.agent_id, message="anomalies collection unavailable")
        try:
            recent = list(coll.find({"resolved": False}, {"_id": 0}).sort("detected_at", -1).limit(20))
        except Exception as e:
            return AgentResponse(success=False, agent_id=self.agent_id, message=str(e))
        return AgentResponse(
            success=True,
            agent_id=self.agent_id,
            data={"unresolved_anomalies": recent, "count": len(recent)},
            message=f"ORACLE: {len(recent)} unresolved anomaly/ies",
        )
