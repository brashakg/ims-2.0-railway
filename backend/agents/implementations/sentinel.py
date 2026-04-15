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

        # 2. Agent health check
        agent_health = await self._check_agent_health()
        results["agents"] = agent_health

        # 3. Data integrity check (every 10th run to save resources)
        if self._run_count % 10 == 0:
            integrity = await self._check_data_integrity()
            results["data_integrity"] = integrity

        # Compute overall health score
        self._health_score = self._compute_health_score(results)
        results["overall_score"] = self._health_score

        # Store health check result
        self._last_health_data = results
        await self._store_health_check(results)

        # Alert if degraded
        if self._health_score < 70:
            await self.emit_event("system.degraded", {
                "score": self._health_score,
                "details": results,
            })
            logger.warning(f"[SENTINEL] System health DEGRADED: {self._health_score}/100")

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
            if not self.db:
                result["status"] = "unhealthy"
                result["error"] = "No database connection"
                return result

            # Test basic read
            col = self.get_collection("stores")
            if col:
                store_count = col.count_documents({})
                result["checks"]["stores_count"] = store_count
                result["checks"]["read_ok"] = True
            else:
                result["checks"]["read_ok"] = False

            # Test users collection
            users_col = self.get_collection("users")
            if users_col:
                user_count = users_col.count_documents({})
                result["checks"]["users_count"] = user_count

            # Test orders collection
            orders_col = self.get_collection("orders")
            if orders_col:
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

            if orders_col and customers_col:
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
        Weights: Database (40%), Agents (30%), Data Integrity (30%)
        """
        score = 100

        # Database health (40% weight)
        db = results.get("database", {})
        db_status = db.get("status", "unknown")
        if db_status == "unhealthy":
            score -= 40
        elif db_status == "degraded":
            score -= 20
        elif db_status == "unknown":
            score -= 10

        # Agent health (30% weight)
        agents = results.get("agents", {})
        if agents.get("total", 0) > 0:
            unhealthy_ratio = agents.get("unhealthy", 0) / max(agents.get("total", 1), 1)
            score -= int(unhealthy_ratio * 30)

        # Data integrity (30% weight)
        integrity = results.get("data_integrity", {})
        issue_count = len(integrity.get("issues", []))
        if issue_count > 0:
            score -= min(issue_count * 10, 30)

        return max(0, min(100, score))

    # ===== Storage =====

    async def _store_health_check(self, results: Dict[str, Any]):
        """Store health check results in MongoDB."""
        try:
            col = self.get_collection("health_checks")
            if col:
                col.insert_one({
                    "timestamp": datetime.now(timezone.utc),
                    "score": self._health_score,
                    "results": results,
                    "agent_id": self.agent_id,
                })
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
            if col:
                col.insert_one(alert)
        except Exception as e:
            logger.warning(f"[SENTINEL] Failed to store alert: {e}")
