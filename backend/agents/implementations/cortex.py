"""
IMS 2.0 — CORTEX: The Orchestrator
====================================
Hero Identity: Professor X (Marvel)
"The world's most powerful telepath reads intent and dispatches the right agent."

Routes every query to the right agent, coordinates multi-agent workflows,
manages agent lifecycle, handles retries & fallbacks.

CORTEX is a CORE agent — cannot be toggled OFF.
"""

from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
import logging
import time
import os
import httpx

from ..base import JarvisAgent, AgentType, AgentResponse, AgentContext

logger = logging.getLogger(__name__)

# Claude API config
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODEL = os.getenv("JARVIS_MODEL", "claude-sonnet-4-20250514")


# Intent classification result
INTENT_KEYWORDS = {
    "sentinel": ["health", "status", "uptime", "error", "crash", "down", "monitor", "api", "server", "deploy"],
    "oracle": ["analyze", "analysis", "forecast", "predict", "trend", "anomaly", "fraud", "discount abuse",
               "benchmark", "compare stores", "revenue", "sales insight", "churn", "why did", "root cause"],
    "taskmaster": ["create task", "reorder", "escalat", "sop", "shift", "schedule", "overdue", "auto",
                   "follow up", "remind", "assign", "checklist"],
    "megaphone": ["campaign", "whatsapp", "sms", "birthday", "reminder", "marketing", "outreach",
                  "review", "nps", "customer engagement", "rx expir"],
    "nexus": ["sync", "shopify", "razorpay", "shiprocket", "tally", "integration", "webhook", "import", "export"],
    "pixel": ["ui", "ux", "performance", "accessibility", "page speed", "layout", "responsive", "audit page"],
}


class CortexOrchestrator(JarvisAgent):
    """
    The Orchestrator — routes queries to the right agent(s),
    coordinates multi-agent workflows, manages lifecycle.
    """

    agent_id = "cortex"
    agent_name = "CORTEX"
    agent_type = AgentType.ORCHESTRATOR
    description = "Orchestrator — routes queries, coordinates multi-agent workflows, manages agent lifecycle"
    version = "1.0.0"
    toggleable = False  # Core agent — always on

    capabilities = [
        "intent_classification",
        "agent_dispatch",
        "multi_agent_coordination",
        "retry_fallback",
        "agent_lifecycle",
        "priority_queue",
    ]

    async def _do_background_work(self):
        """
        CORTEX is event-driven, so its background tick is lightweight:
        - Check health of all registered agents
        - Clean up stale conversation contexts
        """
        from ..registry import AGENT_REGISTRY

        for agent_id, agent in AGENT_REGISTRY.items():
            if agent_id == self.agent_id:
                continue
            try:
                health = await agent.health_check()
                if health.get("health") == "unhealthy":
                    logger.warning(f"[CORTEX] Agent {agent_id} is unhealthy: {health.get('last_error')}")
            except Exception as e:
                logger.error(f"[CORTEX] Health check failed for {agent_id}: {e}")

    async def run(self, query: str, context: AgentContext) -> AgentResponse:
        """
        Main entry point for user queries.
        Classifies intent, dispatches to target agent(s), synthesizes response.
        """
        start = time.time()

        # Step 1: Classify intent → determine target agents
        targets = await self._classify_intent(query)

        if not targets:
            # No agent matched — handle as general query
            return AgentResponse(
                success=True,
                agent_id=self.agent_id,
                data={"type": "general", "targets": []},
                message="Query processed as general question",
                execution_time_ms=(time.time() - start) * 1000,
            )

        # Step 2: Dispatch to target agent(s)
        results = await self._dispatch(targets, query, context)

        # Step 3: Merge and return
        elapsed = (time.time() - start) * 1000
        return AgentResponse(
            success=True,
            agent_id=self.agent_id,
            data={
                "targets": [t[0] for t in targets],
                "results": results,
            },
            message=f"Dispatched to {len(targets)} agent(s)",
            execution_time_ms=elapsed,
        )

    async def _classify_intent(self, query: str) -> List[Tuple[str, float]]:
        """
        Classify user query into target agent(s) with confidence scores.
        Uses keyword matching (fast) with Claude as optional enhancement.

        Returns: [(agent_id, confidence), ...]
        """
        query_lower = query.lower()
        scores: Dict[str, float] = {}

        # Keyword-based classification
        for agent_id, keywords in INTENT_KEYWORDS.items():
            score = 0
            for keyword in keywords:
                if keyword in query_lower:
                    score += 1.0 / len(keywords)  # Normalize by keyword count
            if score > 0:
                scores[agent_id] = min(score * 2, 1.0)  # Cap at 1.0

        # Sort by score descending, filter low confidence
        targets = [
            (agent_id, score)
            for agent_id, score in sorted(scores.items(), key=lambda x: -x[1])
            if score >= 0.1
        ]

        return targets[:3]  # Max 3 agents per query

    async def _dispatch(self, targets: List[Tuple[str, float]],
                        query: str, context: AgentContext) -> Dict[str, Any]:
        """Dispatch query to target agents and collect results."""
        from ..registry import AGENT_REGISTRY

        results = {}
        for agent_id, confidence in targets:
            agent = AGENT_REGISTRY.get(agent_id)
            if not agent:
                results[agent_id] = {"error": f"Agent {agent_id} not registered"}
                continue

            # Check if agent is enabled
            config = await agent.get_config()
            if config and not config.get("enabled", True):
                results[agent_id] = {"skipped": True, "reason": "Agent is disabled"}
                continue

            try:
                ctx = AgentContext(
                    user_id=context.user_id,
                    store_id=context.store_id,
                    query=query,
                    parent_agent=self.agent_id,
                )
                response = await agent.run(query, ctx)
                results[agent_id] = response.to_dict() if response else {"error": "No response"}
            except Exception as e:
                logger.error(f"[CORTEX] Dispatch to {agent_id} failed: {e}")
                results[agent_id] = {"error": str(e)}

                # Retry once after 1 second
                try:
                    import asyncio
                    await asyncio.sleep(1)
                    response = await agent.run(query, ctx)
                    results[agent_id] = response.to_dict() if response else {"error": "No response after retry"}
                    results[agent_id]["retried"] = True
                except Exception as e2:
                    results[agent_id] = {"error": str(e2), "retried": True, "retry_failed": True}

        return results

    async def get_agent_statuses(self) -> Dict[str, Any]:
        """Get status of all registered agents (for the control panel)."""
        from ..registry import AGENT_REGISTRY

        statuses = {}
        for agent_id, agent in AGENT_REGISTRY.items():
            try:
                health = await agent.health_check()
                config = await agent.get_config()
                statuses[agent_id] = {
                    **health,
                    "enabled": config.get("enabled", True) if config else True,
                    "toggleable": agent.toggleable,
                    "schedule_type": config.get("schedule_type", "unknown") if config else "unknown",
                    "schedule_value": config.get("schedule_value", "") if config else "",
                }
            except Exception as e:
                statuses[agent_id] = {
                    "agent_id": agent_id,
                    "health": "unknown",
                    "error": str(e),
                }
        return statuses
