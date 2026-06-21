"""
IMS 2.0 — SENTINEL: System Health & Monitoring
=================================================
Hero Identity: The Sentinels (Marvel)
"Tireless, relentless, always scanning. Never sleeps, never misses a threat."

24/7 watchdog for infrastructure, performance, and data integrity.
Monitors API health, database, frontend metrics, deployments, and data integrity.

SENTINEL is TOGGLEABLE — can be turned ON/OFF by Superadmin.
Default schedule: Every 60 seconds.
"""

from typing import Dict, List, Any, Optional
from datetime import datetime, timezone, timedelta
import logging
import time
import os
import httpx

from ..base import JarvisAgent, AgentType, AgentResponse, AgentContext, HealthStatus

logger = logging.getLogger(__name__)

# health_checks is high-churn telemetry (a row per ~60s SENTINEL tick) and would
# grow UNBOUNDED. It is capped two ways: a TTL index (database/connection.py
# ensure_indexes) AND this defensive in-tick prune, which bounds the collection
# even before/without the TTL index. (A TTL index build needs >=500MB free disk;
# on a small Mongo volume the index can be deferred while this prune caps growth.)
HEALTH_CHECK_RETENTION_DAYS = 14


def prune_health_checks(col, retention_days: int = HEALTH_CHECK_RETENTION_DAYS, now=None) -> int:
    """Delete health_checks rows older than `retention_days`. Fail-soft: returns
    the number deleted, or 0 on any error / missing collection. Kept pure so it
    is unit-testable with a fake collection (no Mongo required)."""
    if col is None:
        return 0
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retention_days)
    try:
        res = col.delete_many({"timestamp": {"$lt": cutoff}})
        return int(getattr(res, "deleted_count", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[SENTINEL] health_checks prune skipped: %s", exc)
        return 0


# API base URL for health checks
API_BASE_URL = os.getenv(
    "API_BASE_URL",
    os.getenv("RAILWAY_PUBLIC_DOMAIN", "http://localhost:8000")
)
if API_BASE_URL and not API_BASE_URL.startswith("http"):
    API_BASE_URL = f"https://{API_BASE_URL}"


class SentinelAgent(JarvisAgent):
    """
    System Health & Monitoring Agent.
    Runs every 60 seconds checking:
      1. API router health (ping key endpoints)
      2. Database health (connection pool, slow queries)
      3. Agent health (poll all agents' health_check)
      4. Data integrity (orphan checks, count mismatches)
    """

    agent_id = "sentinel"
    agent_name = "SENTINEL"
    agent_type = AgentType.MONITOR
    description = "System health & monitoring — API, database, agents, data integrity"
    version = "1.0.0"
    toggleable = True

    capabilities = [
        "api_health_check",
        "db_health_check",
        "agent_health_poll",
        "data_integrity_check",
        "alert_management",
        "health_scoring",
    ]

    def __init__(self, db=None):
        super().__init__(db=db)
        self._health_score = 100
        self._last_reported_health_score = 100
        self._last_health_data: Dict[str, Any] = {}
        self._alerts: List[Dict] = []

    async def _do_background_work(self):
        """
        Main background tick — runs every 60 seconds.
        Performs health checks across all domains and computes health score.
        """
        results = {}

        # 1. Database health check
        db_health = await self._check_db_health()
        results["database"] = db_health

        # 2. API router health check (ping critical endpoints)
        api_health = await self._check_api_health()
        results["api"] = api_health

        # 3. Frontend reachability (HEAD request to Vercel)
        frontend_health = await self._check_frontend_health()
        results["frontend"] = frontend_health

        # 4. Agent health check
        agent_health = await self._check_agent_health()
        results["agents"] = agent_health

        # 5. Data integrity check (every 10th run to save resources)
        if self._run_count % 10 == 0:
            integrity = await self._check_data_integrity()
            results["data_integrity"] = integrity

        # Compute overall health score
        self._health_score = self._compute_health_score(results)
        results["overall_score"] = self._health_score

        # Store health check result
        self._last_health_data = results
        await self._store_health_check(results)

        # Auto-alert only on the downward transition (healthy -> degraded).
        # Prevents alert/event spam every 60s when the system stays below 70.
        if self._health_score < 70 and self._last_reported_health_score >= 70:
            failing = []
            for domain in ("database", "api", "frontend", "agents"):
                d = results.get(domain) or {}
                if d.get("status") in ("unhealthy", "degraded"):
                    failing.append(domain)
            await self._create_alert(
                severity="HIGH" if self._health_score >= 40 else "CRITICAL",
                domain="system",
                message=(
                    f"System health {self._health_score}/100 — "
                    f"{', '.join(failing) if failing else 'multiple components'} degraded"
                ),
                details={"score": self._health_score, "failing": failing},
            )
            await self.emit_event("system.degraded", {
                "score": self._health_score,
                "failing": failing,
                "details": results,
            })
            logger.warning(f"[SENTINEL] System health DEGRADED: {self._health_score}/100 — {failing}")

        self._last_reported_health_score = self._health_score

    async def run(self, query: str, context: AgentContext) -> AgentResponse:
        """Handle on-demand health queries from CORTEX."""
        return AgentResponse(
            success=True,
            agent_id=self.agent_id,
            data={
                "health_score": self._health_score,
                "last_check": self._last_health_data,
                "active_alerts": self._alerts[-10:],
            },
            message=f"System health: {self._health_score}/100",
        )

    async def on_event(self, event: str, payload: Dict[str, Any]):
        """SENTINEL listens for system + audit signals.

        New (May 2026): `audit.alert` events — fired by `services/audit_alerts.py`
        whenever a sensitive write hits an order / line item / P&L period.
        SENTINEL turns CRITICAL/HIGH events into alert rows + (when configured)
        Slack pings via the existing observability helper.

        Also (May 2026): `agent.error` events — emitted by the base agent when a
        background tick raises — become HIGH alerts so a failing agent shows up
        in the alert tail + activity feed (replaces the old sentry.io path).
        """
        if event == "agent.error":
            await self._create_alert(
                severity="HIGH",
                domain="agent",
                message=(
                    f"{payload.get('agent_name') or payload.get('agent_id') or 'agent'} "
                    f"tick failed: {str(payload.get('error', ''))[:200]}"
                ),
                details=payload,
            )
            return

        if event == "audit.alert":
            severity = (payload.get("severity") or "LOW").upper()
            action = payload.get("action") or "audit.unknown"
            entity_id = payload.get("entity_id") or "?"
            user_id = payload.get("user_id") or "system"

            # Always file the alert in our in-memory tail (surfaced via /run query)
            await self._create_alert(
                severity=severity,
                domain="audit",
                message=f"{action} on {entity_id} by {user_id}",
                details=payload,
            )

            # Mid+ severities also dispatch a Slack ping via the existing
            # observability hook, which is fail-soft on missing webhook URL
            if severity in {"HIGH", "CRITICAL"}:
                try:
                    from observability import notify_slack
                    await notify_slack(
                        severity=severity,
                        title=f"[AUDIT] {action}",
                        body=(
                            f"User: {user_id}\n"
                            f"Entity: {payload.get('entity_type')} {entity_id}\n"
                            f"Diff: {payload.get('diff') or {}}\n"
                            f"Context: {payload.get('context') or {}}"
                        ),
                    )
                except Exception as e:
                    logger.debug(f"[SENTINEL] Slack notify on audit failed (soft): {e}")
            return

        # Existing handlers stay default-passthrough (base no-op).

    async def health_check(self) -> Dict[str, Any]:
        """Self-diagnostic — returns SENTINEL's own health plus system score."""
        base = await super().health_check()
        base["system_health_score"] = self._health_score
        base["active_alerts"] = len(self._alerts)
        return base

    # ===== Health Check Implementations =====

    async def _check_db_health(self) -> Dict[str, Any]:
        """Check MongoDB connection and basic metrics."""
        start = time.time()
        result = {"status": "unknown", "checks": {}}

        try:
            if self.db is None:
                result["status"] = "unhealthy"
                result["error"] = "No database connection"
                return result

            # Test basic read
            col = self.get_collection("stores")
            if col is not None:
                store_count = col.count_documents({})
                result["checks"]["stores_count"] = store_count
                result["checks"]["read_ok"] = True
            else:
                result["checks"]["read_ok"] = False

            # Test users collection
            users_col = self.get_collection("users")
            if users_col is not None:
                user_count = users_col.count_documents({})
                result["checks"]["users_count"] = user_count

            # Test orders collection
            orders_col = self.get_collection("orders")
            if orders_col is not None:
                order_count = orders_col.count_documents({})
                result["checks"]["orders_count"] = order_count

            elapsed = (time.time() - start) * 1000
            result["response_time_ms"] = round(elapsed, 2)
            result["status"] = "healthy" if elapsed < 2000 else "degraded"

        except Exception as e:
            result["status"] = "unhealthy"
            result["error"] = str(e)
            logger.error(f"[SENTINEL] DB health check failed: {e}")

        return result

    async def _check_api_health(self) -> Dict[str, Any]:
        """
        Ping critical API endpoints on this very instance. Uses the
        in-process HTTP client so we measure the deployed router stack
        end-to-end, not just a function call.

        Checks: /api/v1/health (anon — required to be public), plus a
        couple of authenticated routes via the configured API_BASE_URL
        if a SENTINEL_API_TOKEN is provided (optional; skipped otherwise
        so we don't store a long-lived token in env by default).
        """
        result: Dict[str, Any] = {"status": "unknown", "checks": {}}
        start = time.time()

        # Cache the token across ticks if provisioned. Empty string = skip.
        token = os.getenv("SENTINEL_API_TOKEN", "")

        targets = [
            {"name": "health", "path": "/api/v1/health", "auth": False, "method": "GET"},
        ]
        if token:
            targets.extend([
                {"name": "auth_me", "path": "/api/v1/auth/me", "auth": True, "method": "GET"},
                {"name": "products_count", "path": "/api/v1/products?limit=1", "auth": True, "method": "GET"},
            ])

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                for tgt in targets:
                    t0 = time.time()
                    headers = {}
                    if tgt["auth"] and token:
                        headers["Authorization"] = f"Bearer {token}"
                    try:
                        r = await client.request(
                            tgt["method"],
                            f"{API_BASE_URL}{tgt['path']}",
                            headers=headers,
                        )
                        result["checks"][tgt["name"]] = {
                            "status_code": r.status_code,
                            "ok": 200 <= r.status_code < 400,
                            "response_time_ms": round((time.time() - t0) * 1000, 1),
                        }
                    except Exception as e:
                        result["checks"][tgt["name"]] = {
                            "error": str(e)[:200],
                            "ok": False,
                        }
        except Exception as e:
            result["error"] = str(e)[:200]

        checks = result["checks"]
        if not checks:
            result["status"] = "unknown"
        elif all(c.get("ok") for c in checks.values()):
            result["status"] = "healthy"
        elif any(c.get("ok") for c in checks.values()):
            result["status"] = "degraded"
        else:
            result["status"] = "unhealthy"
        result["response_time_ms"] = round((time.time() - start) * 1000, 1)
        return result

    async def _check_frontend_health(self) -> Dict[str, Any]:
        """HEAD request to the Vercel frontend to confirm it's serving.

        Default target: FRONTEND_BASE_URL env, falling back to the public
        Vercel domain. Counts 2xx and 3xx as healthy (Vercel may redirect
        /). 5xx or timeout = unhealthy.
        """
        result: Dict[str, Any] = {"status": "unknown"}
        url = os.getenv(
            "FRONTEND_BASE_URL", "https://ims-2-0-railway.vercel.app"
        ).rstrip("/")
        start = time.time()
        try:
            async with httpx.AsyncClient(
                timeout=8.0, follow_redirects=False
            ) as client:
                # GET (not HEAD — some hosts return 405 on HEAD)
                r = await client.get(url)
            elapsed = round((time.time() - start) * 1000, 1)
            result["url"] = url
            result["status_code"] = r.status_code
            result["response_time_ms"] = elapsed
            if 200 <= r.status_code < 400:
                result["status"] = "healthy"
            elif 400 <= r.status_code < 500:
                # 4xx is not "down" — likely a misconfigured route — degraded
                result["status"] = "degraded"
            else:
                result["status"] = "unhealthy"
        except httpx.TimeoutException:
            result["status"] = "unhealthy"
            result["error"] = "timeout"
        except Exception as e:
            result["status"] = "unhealthy"
            result["error"] = str(e)[:200]
        return result

    async def _check_agent_health(self) -> Dict[str, Any]:
        """Poll all registered agents' health_check()."""
        from ..registry import AGENT_REGISTRY

        result = {"agents": {}, "total": 0, "healthy": 0, "unhealthy": 0}

        for agent_id, agent in AGENT_REGISTRY.items():
            if agent_id == self.agent_id:
                continue  # Don't check ourselves
            try:
                health = await agent.health_check()
                result["agents"][agent_id] = health
                result["total"] += 1
                if health.get("health") == "healthy":
                    result["healthy"] += 1
                else:
                    result["unhealthy"] += 1
            except Exception as e:
                result["agents"][agent_id] = {"health": "unknown", "error": str(e)}
                result["total"] += 1
                result["unhealthy"] += 1

        result["status"] = "healthy" if result["unhealthy"] == 0 else "degraded"
        return result

    async def _check_data_integrity(self) -> Dict[str, Any]:
        """Check for data integrity issues — orphan records, mismatches."""
        result = {"issues": [], "status": "healthy"}

        try:
            orders_col = self.get_collection("orders")
            customers_col = self.get_collection("customers")

            if orders_col is not None and customers_col is not None:
                # Check for orders without valid customer references
                recent_orders = list(orders_col.find(
                    {"created_at": {"$gte": datetime.now(timezone.utc) - timedelta(days=7)}},
                    {"customer_id": 1, "order_number": 1}
                ).limit(100))

                orphan_count = 0
                for order in recent_orders:
                    cust_id = order.get("customer_id")
                    if cust_id:
                        customer = customers_col.find_one({"_id": cust_id})
                        if not customer:
                            # Also try string match
                            customer = customers_col.find_one({"customer_id": cust_id})
                        if not customer:
                            orphan_count += 1

                if orphan_count > 0:
                    result["issues"].append({
                        "type": "ORPHAN_ORDERS",
                        "severity": "MEDIUM",
                        "count": orphan_count,
                        "message": f"{orphan_count} recent orders reference non-existent customers",
                    })
                    result["status"] = "degraded"

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"[SENTINEL] Data integrity check failed: {e}")

        return result

    # ===== Health Scoring =====

    def _compute_health_score(self, results: Dict[str, Any]) -> int:
        """
        Compute overall system health score (0-100).
        Weights: Database (30%), API (20%), Frontend (15%), Agents (20%),
                 Data Integrity (15%).

        Each component's status maps to a deduction proportional to its
        weight: unhealthy = full deduction, degraded = half, unknown =
        quarter. Agents use ratio of unhealthy/total. Integrity uses
        issue count capped at the weight.
        """
        score = 100

        def deduct(status: str, weight: int) -> int:
            if status == "unhealthy":
                return weight
            if status == "degraded":
                return weight // 2
            if status == "unknown":
                return weight // 4
            return 0

        score -= deduct(results.get("database", {}).get("status", "unknown"), 30)
        score -= deduct(results.get("api", {}).get("status", "unknown"), 20)
        score -= deduct(results.get("frontend", {}).get("status", "unknown"), 15)

        agents = results.get("agents", {})
        if agents.get("total", 0) > 0:
            unhealthy_ratio = agents.get("unhealthy", 0) / max(agents.get("total", 1), 1)
            score -= int(unhealthy_ratio * 20)

        integrity = results.get("data_integrity", {})
        issue_count = len(integrity.get("issues", []))
        if issue_count > 0:
            score -= min(issue_count * 5, 15)

        return max(0, min(100, score))

    # ===== Storage =====

    async def _store_health_check(self, results: Dict[str, Any]):
        """Store health check results in MongoDB."""
        try:
            col = self.get_collection("health_checks")
            if col is not None:
                col.insert_one({
                    "timestamp": datetime.now(timezone.utc),
                    "score": self._health_score,
                    "results": results,
                    "agent_id": self.agent_id,
                })
                # Defensive retention: cap this high-churn collection on each
                # tick so it self-bounds even before the TTL index can build.
                prune_health_checks(col)
        except Exception as e:
            logger.warning(f"[SENTINEL] Failed to store health check: {e}")

    # ===== Alert Management =====

    async def _create_alert(self, severity: str, domain: str, message: str, details: Dict = None):
        """Create a new alert."""
        alert = {
            "severity": severity,
            "domain": domain,
            "message": message,
            "details": details or {},
            "timestamp": datetime.now(timezone.utc),
            "acknowledged": False,
        }
        self._alerts.append(alert)
        # Keep last 100 alerts in memory
        if len(self._alerts) > 100:
            self._alerts = self._alerts[-100:]

        # Store in MongoDB
        try:
            col = self.get_collection("alert_history")
            if col is not None:
                col.insert_one(alert)
        except Exception as e:
            logger.warning(f"[SENTINEL] Failed to store alert: {e}")
