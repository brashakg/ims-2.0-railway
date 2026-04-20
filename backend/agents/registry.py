"""
IMS 2.0 — Agent Registry
==========================
Central registry for all Jarvis agents.
Manages agent instances, event dispatch, and discovery.

Phase 6.2 change
----------------
Event dispatch now routes through `event_bus.EventBus` which fans out via
Redis pub/sub when REDIS_URL is configured. The public API
(`subscribe_event`, `dispatch_event`) is unchanged — existing callers
keep working — but events emitted in one Railway worker now reach
subscribers in every worker.

When Redis isn't configured the bus falls back to in-process dispatch
(identical to pre-6.2 behavior), so dev machines without Redis still run
the full agent stack locally.
"""

from typing import Dict, Optional, List, Any
import logging

from .base import JarvisAgent
from .event_bus import get_event_bus

logger = logging.getLogger(__name__)


# ============================================================================
# GLOBAL AGENT REGISTRY
# ============================================================================

AGENT_REGISTRY: Dict[str, JarvisAgent] = {}

# Event subscribers: { "event_name": [agent_id, ...] }
# Kept as the source of truth for "who cares about this event" so the bus's
# listener loop can walk it on every inbound message. Populating it via
# subscribe_event() is unchanged from Phase 3.
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


async def _deliver_to_subscribers(event: str, payload: Dict[str, Any], source: str = ""):
    """
    Walk _event_subscriptions for this event and call each subscribing
    agent's on_event. Used as the local-dispatch callback registered with
    the bus, AND as the direct-dispatch path when running in in-process
    fallback mode.
    """
    subscribers = _event_subscriptions.get(event, [])
    for agent_id in subscribers:
        if agent_id == source:
            continue  # Don't deliver event back to the emitting agent
        agent = AGENT_REGISTRY.get(agent_id)
        if agent is None:
            continue
        try:
            await agent.on_event(event, payload)
        except Exception as e:
            logger.error(f"[EVENT] {agent_id} failed handling {event}: {e}")


def subscribe_event(event: str, agent_id: str):
    """
    Subscribe an agent to an event type.

    Registers the event -> agent mapping locally AND ensures the bus has a
    single handler for this event that fans out via _deliver_to_subscribers.
    The handler is installed idempotently — calling subscribe_event repeatedly
    for the same event doesn't stack up N handlers.
    """
    if event not in _event_subscriptions:
        _event_subscriptions[event] = []
        # First subscriber for this event — install the fan-out handler on
        # the bus. Later subscribe_event calls for the same event just
        # append to the list; the single handler already dispatches to all.
        try:
            bus = get_event_bus()
            bus.register(event, _deliver_to_subscribers)
        except Exception as e:
            logger.warning(f"[REGISTRY] Could not register bus handler for {event}: {e}")

    if agent_id not in _event_subscriptions[event]:
        _event_subscriptions[event].append(agent_id)


async def dispatch_event(event: str, payload: Dict[str, Any], source: str = ""):
    """
    Publish an event through the bus.

    When Redis is connected, the bus fans out to every worker (including
    this one) and the listener loop on each worker calls
    _deliver_to_subscribers locally. When Redis is absent, the bus
    dispatches directly in-process. Either way the caller's experience is
    identical: the coroutine returns once the event has been handed off.
    """
    try:
        bus = get_event_bus()
        await bus.publish(event, payload, source=source)
    except Exception as e:
        logger.error(f"[EVENT] Bus publish failed for {event}: {e}")
        # Last-resort fallback: deliver locally so at least this worker's
        # subscribers fire. Better a partial dispatch than none.
        try:
            await _deliver_to_subscribers(event, payload, source)
        except Exception:
            pass


def initialize_registry(db=None):
    """
    Create and register all agent instances.
    Called during FastAPI startup. Wires the 8-agent Jarvis ecosystem
    (CORTEX + SENTINEL + 5 domain agents from Phase 3 + JARVIS NLP core
    that lives in api/routers/jarvis.py).
    """
    # Bind the DB to the event bus so publish() can persist to
    # agent_events for the activity feed + audit trail.
    try:
        get_event_bus(db=db)
    except Exception as e:
        logger.warning(f"[REGISTRY] Event bus init warning: {e}")

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
