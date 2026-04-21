"""
IMS 2.0 - Agent Event Bus (Phase 6.2)
=======================================
Cross-worker durable event dispatch for the Jarvis agent ecosystem.

Why this exists
---------------
Before Phase 6.2, `registry.dispatch_event` walked an in-process dict of
subscribers and called each agent's `on_event()` directly. This works in
a single-process dev run, but Railway runs 4 uvicorn workers — when
SENTINEL in worker A emits `stock.below_reorder`, TASKMASTER in worker B
never sees it because worker B's registry is a separate Python object.

Design
------
Redis pub/sub on a single channel (`ims.agents.events`), with every
message echoed back to every worker including the sender. Each worker
dispatches locally based on its own `_event_subscriptions` map.

Additionally, every event is persisted to the MongoDB `agent_events`
collection so:
  - We get a durable audit trail of what agents emitted and when
  - The activity feed can query the collection instead of racing the bus
  - We can cold-replay if needed (not automatic in Phase 6.2)

Fail-soft
---------
- REDIS_URL unset  -> warn once, fall back to in-process dispatch
                     (identical semantics to pre-6.2 behavior)
- Redis publish failure -> log + fall back to local dispatch for just
                            that message
- MongoDB unavailable -> skip persistence, dispatch still happens
- Handler raises -> logged, other handlers still run

This preserves the pre-6.2 contract: a dev machine without Redis or
Mongo still boots, agents still tick, events still fire locally.

Env vars
--------
- REDIS_URL          (preferred) e.g. redis://default:password@host:port
- REDIS_HOST/PORT/PASSWORD/DB  (legacy, matches api/services/cache.py)
- AGENT_EVENT_CHANNEL  default `ims.agents.events`
"""

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union
import asyncio
import json
import logging
import os
import uuid

logger = logging.getLogger(__name__)


# Event handlers can be sync or async. The bus awaits the awaitable form.
EventHandler = Callable[[str, Dict[str, Any], str], Union[Awaitable[None], None]]


# Single channel for all agent events. Keep this shared — Redis pub/sub
# matches channel names exactly and fanning out message bodies locally
# is cheaper than juggling N subscriptions.
DEFAULT_CHANNEL = "ims.agents.events"


def _resolve_redis_url() -> Optional[str]:
    """Match cache.py's URL/host/port/password/db convention."""
    url = os.getenv("REDIS_URL")
    if url:
        return url
    host = os.getenv("REDIS_HOST")
    if not host:
        return None
    port = os.getenv("REDIS_PORT", "6379")
    password = os.getenv("REDIS_PASSWORD")
    db = os.getenv("REDIS_DB", "0")
    auth = f":{password}@" if password else ""
    return f"redis://{auth}{host}:{port}/{db}"


class EventBus:
    """
    Async event bus with Redis pub/sub backend + Mongo audit log.

    Typical lifecycle:
        bus = EventBus(db=db)
        await bus.start()           # connects to Redis, spawns listener task
        bus.register("stock.below_reorder", taskmaster_handler)
        await bus.publish("stock.below_reorder", {"sku": "X"}, source="sentinel")
        ...
        await bus.stop()            # cancels listener, closes pubsub

    The bus is designed so `register` + `publish` can be called safely
    before `start()` (they just run in in-process mode until the listener
    comes up). This matches how the registry wires itself in
    `initialize_registry` before the FastAPI lifespan calls `bus.start()`.
    """

    def __init__(self, db=None, redis_url: Optional[str] = None,
                 channel: Optional[str] = None):
        self._db = db
        self._redis_url = redis_url or _resolve_redis_url()
        self._channel = channel or os.getenv("AGENT_EVENT_CHANNEL", DEFAULT_CHANNEL)
        self._redis = None              # redis.asyncio.Redis
        self._pubsub = None             # redis.asyncio.client.PubSub
        self._listener_task: Optional[asyncio.Task] = None
        self._handlers: Dict[str, List[EventHandler]] = {}
        self._worker_id = str(uuid.uuid4())
        self._running = False
        self._started = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def worker_id(self) -> str:
        """Unique id for this worker process. Useful for log/debug correlation."""
        return self._worker_id

    @property
    def is_distributed(self) -> bool:
        """True if Redis is connected and events fan out across workers."""
        return self._redis is not None

    def register(self, event: str, handler: EventHandler) -> None:
        """
        Register a local handler for `event`. Safe to call at any time;
        does not require the bus to be started.
        """
        self._handlers.setdefault(event, []).append(handler)

    async def start(self) -> None:
        """
        Connect to Redis (if configured) and start the background listener.
        Idempotent - subsequent calls are a no-op.
        """
        if self._started:
            return
        self._started = True

        if not self._redis_url:
            logger.warning(
                "[EVENT_BUS] REDIS_URL not set - running in IN-PROCESS mode. "
                "Events emitted in worker A will NOT reach subscribers in "
                "worker B on a multi-worker deploy."
            )
            self._running = True
            return

        try:
            # redis.asyncio ships with redis-py 4.2+; we require redis==5.0.7
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                health_check_interval=30,
            )
            # Ping to force connect errors to surface here, not on first publish
            await self._redis.ping()

            self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
            await self._pubsub.subscribe(self._channel)

            self._running = True
            self._listener_task = asyncio.create_task(
                self._listen_loop(), name=f"event_bus.listener.{self._worker_id[:8]}"
            )
            logger.info(
                f"[EVENT_BUS] Connected to Redis, listening on '{self._channel}' "
                f"(worker={self._worker_id[:8]})"
            )
        except Exception as e:
            logger.warning(
                f"[EVENT_BUS] Redis connect failed ({e}) - falling back to "
                f"IN-PROCESS mode. Multi-worker events will be lost."
            )
            self._redis = None
            self._pubsub = None
            self._running = True

    async def stop(self) -> None:
        """Cancel the listener task and close Redis connections."""
        self._running = False

        if self._listener_task is not None:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except (asyncio.CancelledError, Exception):
                pass
            self._listener_task = None

        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe(self._channel)
                await self._pubsub.aclose()
            except Exception:
                pass
            self._pubsub = None

        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None

        self._started = False
        logger.info("[EVENT_BUS] Stopped")

    async def publish(self, event: str, payload: Dict[str, Any],
                      source: str = "") -> None:
        """
        Persist the event to MongoDB and publish to Redis. If Redis is
        unavailable, dispatches locally so the emitting worker's subscribers
        still fire (matching pre-6.2 behavior).
        """
        message = {
            "event": event,
            "payload": payload,
            "source": source,
            "emitted_at": datetime.now(timezone.utc).isoformat(),
            "worker_id": self._worker_id,
        }

        # 1. Persist for audit / activity-feed / cold-replay.
        self._persist(message)

        # 2. Distribute. When Redis is up every worker (including us) sees
        #    the event via the listener loop, so we DO NOT dispatch locally
        #    here -- doing both would double-fire subscribers.
        if self._redis is not None:
            try:
                await self._redis.publish(
                    self._channel, json.dumps(message, default=str)
                )
                return
            except Exception as e:
                logger.warning(
                    f"[EVENT_BUS] Redis publish failed for {event} ({e}) - "
                    f"falling back to local dispatch for this message"
                )

        # 3. In-process fallback. Only reached when Redis is absent/broken.
        await self._dispatch_local(event, payload, source)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _persist(self, message: Dict[str, Any]) -> None:
        """Best-effort write to the agent_events collection. Never raises."""
        if self._db is None:
            return
        try:
            col = self._db.get_collection("agent_events")
            col.insert_one({
                **message,
                # Also store as a native datetime for range queries; the
                # isoformat string stays as the canonical wire format.
                "emitted_dt": datetime.now(timezone.utc),
            })
        except Exception as e:
            logger.debug(f"[EVENT_BUS] persist failed (non-fatal): {e}")

    async def _listen_loop(self) -> None:
        """
        Background task: reads messages off the Redis pubsub and dispatches
        to local handlers. One task per worker.
        """
        assert self._pubsub is not None
        while self._running:
            try:
                msg = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if msg is None:
                    continue
                if msg.get("type") != "message":
                    continue
                try:
                    data = json.loads(msg["data"])
                except Exception as e:
                    logger.warning(f"[EVENT_BUS] Bad pubsub payload: {e}")
                    continue

                event = data.get("event") or ""
                payload = data.get("payload") or {}
                source = data.get("source") or ""
                await self._dispatch_local(event, payload, source)

            except asyncio.CancelledError:
                break
            except Exception as e:
                # Keep the listener alive; transient errors (conn reset, slow
                # handler) shouldn't tear down cross-worker eventing.
                logger.warning(f"[EVENT_BUS] Listener error (continuing): {e}")
                await asyncio.sleep(0.5)

    async def _dispatch_local(self, event: str, payload: Dict[str, Any],
                              source: str) -> None:
        """Call every registered handler for this event. Failures are logged."""
        handlers = list(self._handlers.get(event, []))
        for h in handlers:
            try:
                result = h(event, payload, source)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(
                    f"[EVENT_BUS] Handler for {event} raised: {e}",
                    exc_info=True,
                )


# ============================================================================
# Global singleton
# ============================================================================
# The registry wires subscriptions at import time (before lifespan runs), so
# we need a module-level bus instance it can call register() on.

_bus: Optional[EventBus] = None


def get_event_bus(db=None) -> EventBus:
    """
    Return the process-wide event bus, creating it on first call.
    `db` is only used for the initial construction.
    """
    global _bus
    if _bus is None:
        _bus = EventBus(db=db)
    elif db is not None and _bus._db is None:
        # Allow late binding of DB after first import
        _bus._db = db
    return _bus


def reset_event_bus_for_tests() -> None:
    """Drop the singleton - only used by tests that need a fresh instance."""
    global _bus
    _bus = None
