"""
IMS 2.0 — Agent Registry
==========================
Central registry for all Jarvis agents.
Manages agent instances, event dispatch, and discovery.
"""

from typing import Dict, Optional, List, Any
import logging

from .base import JarvisAgent

logger = logging.getLogger(__name__)


# ============================================================================
# GLOBAL AGENT REGISTRY
# ============================================================================

AGENT_REGISTRY: Dict[str, JarvisAgent] = {}

# Event subscribers: { "event_name": [agent_id, ...] }
_event_subscriptions: Dict[str, List[str]] = {}


def register_agent(agent: JarvisAgent):
    """Register an agent instance in the global registry."""
    AGENT_REGISTRY[agent.agent_id] = agent
    logger.info(f"[REGISTRY] Registered agent: {agent.agent_id} ({agent.agent_name})")


def get_agent(agent_id: str) -> Optional[JarvisAgent]:
    """Get an agent by ID."""
    return AGENT_REGISTRY.get(agent_id)


def get_all_agents() -> Dict[str, JarvisAgent]:
    """Get all registered agents."""
    return AGENT_REGISTRY


def subscribe_event(event: str, agent_id: str):
    """Subscribe an agent to an event type."""
    if event not in _event_subscriptions:
        _event_subscriptions[event] = []
    if agent_id not in _event_subscriptions[event]:
        _event_subscriptions[event].append(agent_id)


async def dispatch_event(event: str, payload: Dict[str, Any], source: str = ""):
    """Dispatch an event to all subscribed agents."""
    subscribers = _event_subscriptions.get(event, [])
    for agent_id in subscribers:
        if agent_id == source:
            continue  # Don't send event back to the sender
        agent = AGENT_REGISTRY.get(agent_id)
        if agent:
            try:
                await agent.on_event(event, payload)
            except Exception as e:
                logger.error(f"[EVENT] {agent_id} failed handling {event}: {e}")


def initialize_registry(db=None):
    """
    Create and register all agent instances.
    Called during FastAPI startup. Wires the 8-agent Jarvis ecosystem
    (CORTEX + SENTINEL + 5 domain agents from Phase 3 + JARVIS NLP core
    that lives in api/routers/jarvis.py).
    """
    # Core agents — not toggleable
    from .implementations.cortex import CortexOrchestrator
    from .implementations.sentinel import SentinelAgent
    # Phase 3 domain agents — toggleable
    from .implementations.pixel import PixelAgent
    from .implementations.megaphone import MegaphoneAgent
    from .implementations.oracle import OracleAgent
    from .implementations.taskmaster import TaskmasterAgent
    from .implementations.nexus import NexusAgent

    # Core
    register_agent(CortexOrchestrator(db=db))
    register_agent(SentinelAgent(db=db))

    # Domain (Phase 3)
    register_agent(PixelAgent(db=db))
    register_agent(MegaphoneAgent(db=db))
    register_agent(OracleAgent(db=db))
    register_agent(TaskmasterAgent(db=db))
    register_agent(NexusAgent(db=db))

    # ── Event bus wiring ───────────────────────────────────────────
    # CORTEX is the orchestrator — it sees system-wide signals
    subscribe_event("agent.error",       "cortex")
    subscribe_event("system.degraded",   "cortex")
    subscribe_event("anomaly.detected",  "cortex")

    # SENTINEL watches everything operational
    subscribe_event("agent.error",       "sentinel")
    subscribe_event("agent.toggled",     "sentinel")
    subscribe_event("stock.below_reorder","sentinel")
    subscribe_event("anomaly.detected",  "sentinel")
    subscribe_event("sync.failed",       "sentinel")

    # PIXEL listens for Vercel deploy events to trigger an audit cycle
    subscribe_event("deploy.success",    "pixel")
    subscribe_event("deploy.failure",    "pixel")

    # TASKMASTER acts on actionable signals (the only agent that writes state)
    subscribe_event("stock.below_reorder", "taskmaster")
    subscribe_event("anomaly.detected",    "taskmaster")
    subscribe_event("sla.breached",        "taskmaster")

    # NEXUS handles inbound webhooks
    subscribe_event("webhook.received",  "nexus")

    logger.info(f"[REGISTRY] Initialized {len(AGENT_REGISTRY)} agents")
    return AGENT_REGISTRY
