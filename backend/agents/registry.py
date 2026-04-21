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

from importlib import import_module

from .base import JarvisAgent
from .event_bus import get_event_bus

logger = logging.getLogger(__name__)


def _import_class(module_path: str, class_name: str):
    """
    Lazy, per-agent import so one bad module can't break `import registry`
    at module-parse time. `module_path` is relative to the `agents`
    package (e.g. `.implementations.oracle`).
    """
    module = import_module(module_path, package="agents")
    return getattr(module, class_name)


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


def _safe_register(name: str, loader):
    """
    Import + instantiate + register a single agent, catching ANY failure.

    Before Phase 6.5, initialize_registry() registered agents in a single
    straight-line function. If any one of them failed at import time
    (missing env var in a module-level getenv, incompatible Python on
    Railway, etc.) the whole registry would get only the agents that
    happened to come before the failure in the list — the rest silently
    never ran. That's how a dev machine could show 8 agents while
    production showed 5. Each agent now survives its neighbors' failures.

    `loader` is a zero-arg callable that returns a constructed agent.
    """
    try:
        agent = loader()
        register_agent(agent)
    except Exception as e:
        # Log loudly — this is the ONE error we never want to swallow.
        # The operator needs to know which agent isn't alive on this worker.
        logger.error(
            f"[REGISTRY] Failed to register agent '{name}': {type(e).__name__}: {e}",
            exc_info=True,
        )


def initialize_registry(db=None):
    """
    Create and register all 8 Jarvis agents.
    Called during FastAPI startup. Every agent is registered in its own
    try/except so one failure doesn't take out the rest.
    """
    # Bind the DB to the event bus so publish() can persist to
    # agent_events for the activity feed + audit trail.
    try:
        get_event_bus(db=db)
    except Exception as e:
        logger.warning(f"[REGISTRY] Event bus init warning: {e}")

    # Foundation — always on, NLP core (real work lives in /jarvis/query)
    _safe_register("jarvis",
        lambda: _import_class(".implementations.jarvis", "JarvisCore")(db=db))

    # Core orchestrator + health monitor — not toggleable
    _safe_register("cortex",
        lambda: _import_class(".implementations.cortex", "CortexOrchestrator")(db=db))
    _safe_register("sentinel",
        lambda: _import_class(".implementations.sentinel", "SentinelAgent")(db=db))

    # Domain agents (Phase 3) — toggleable
    _safe_register("pixel",
        lambda: _import_class(".implementations.pixel", "PixelAgent")(db=db))
    _safe_register("megaphone",
        lambda: _import_class(".implementations.megaphone", "MegaphoneAgent")(db=db))
    _safe_register("oracle",
        lambda: _import_class(".implementations.oracle", "OracleAgent")(db=db))
    _safe_register("taskmaster",
        lambda: _import_class(".implementations.taskmaster", "TaskmasterAgent")(db=db))
    _safe_register("nexus",
        lambda: _import_class(".implementations.nexus", "NexusAgent")(db=db))

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

    # Final roster log — Phase 6.5. If anything dropped out, this line
    # makes it obvious in Railway logs without needing a separate diag
    # endpoint round-trip. Looks for the canonical 8 by id and prints the
    # missing set with a CRITICAL severity prefix so it's grep-able.
    registered_ids = sorted(AGENT_REGISTRY.keys())
    missing = [aid for aid in CANONICAL_AGENT_IDS if aid not in AGENT_REGISTRY]
    if missing:
        logger.error(
            f"[REGISTRY] CRITICAL: only {len(registered_ids)}/8 agents registered. "
            f"Missing: {missing}. Registered: {registered_ids}. "
            f"Look upward in this log for the [REGISTRY] Failed to register agent '<id>' "
            f"line(s) — that's the root cause for each missing agent."
        )
    else:
        logger.info(
            f"[REGISTRY] OK: 8/8 canonical agents registered: {registered_ids}"
        )
    return AGENT_REGISTRY


# Canonical 8-agent roster — single source of truth used by both the
# startup diagnostic above and the /jarvis/agents/diagnostic endpoint.
CANONICAL_AGENT_IDS = (
    "jarvis", "cortex", "sentinel", "pixel",
    "megaphone", "oracle", "taskmaster", "nexus",
)
