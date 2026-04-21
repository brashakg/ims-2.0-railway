"""
IMS 2.0 — Event Bus tests (Phase 6.2)
=======================================
Verifies the multi-worker event bus:

  - In-process fallback path works identically to pre-6.2 dispatch when
    REDIS_URL is unset (every handler fires, source is never echoed to
    itself).
  - MongoDB persistence: every publish() writes to agent_events.
  - Redis path: publish() calls redis.publish on the configured channel
    with a well-formed JSON envelope.
  - Listener loop: an inbound Redis message is dispatched to local
    handlers with event/payload/source.
  - Multi-worker semantics: a publish made with one worker_id reaches
    handlers on a separate bus instance (simulates worker B receiving
    worker A's event).

All tests run without a real Redis server — we use an in-memory fake.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import event_bus as event_bus_module
from agents.event_bus import EventBus, _resolve_redis_url


# ============================================================================
# Fakes
# ============================================================================


class FakeCollection:
    """Mongo collection stub that just remembers inserts."""
    def __init__(self):
        self.inserts: List[Dict[str, Any]] = []

    def insert_one(self, doc):
        self.inserts.append(doc)


class FakeDB:
    def __init__(self):
        self.collections: Dict[str, FakeCollection] = {}

    def get_collection(self, name: str) -> FakeCollection:
        if name not in self.collections:
            self.collections[name] = FakeCollection()
        return self.collections[name]


class FakePubSub:
    """
    Minimal async pubsub stub. Holds a queue of inbound messages that
    get_message() pops.
    """
    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self.subscribed_to: List[str] = []

    async def subscribe(self, channel: str):
        self.subscribed_to.append(channel)

    async def unsubscribe(self, *channels):
        pass

    async def aclose(self):
        pass

    async def get_message(self, ignore_subscribe_messages=True, timeout=1.0):
        try:
            msg = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            return msg
        except asyncio.TimeoutError:
            return None

    async def inject(self, data: str):
        """Test helper: push a message into the subscriber queue."""
        await self._queue.put({"type": "message", "data": data, "channel": "x"})


class FakeRedis:
    """Minimal async Redis stub wired to a single FakePubSub."""
    def __init__(self):
        self.publishes: List = []     # list of (channel, data)
        self._pubsub = FakePubSub()

    async def ping(self):
        return True

    def pubsub(self, ignore_subscribe_messages=False):
        return self._pubsub

    async def publish(self, channel: str, data: str):
        self.publishes.append((channel, data))
        # Simulate Redis echoing back to every subscriber including us
        await self._pubsub.inject(data)

    async def aclose(self):
        pass


# ============================================================================
# In-process fallback
# ============================================================================


@pytest.mark.asyncio
async def test_in_process_fallback_no_redis(monkeypatch):
    """
    With REDIS_URL unset, publish() should dispatch directly to handlers
    without touching Redis. This is the pre-6.2 behavior preserved.
    """
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_HOST", raising=False)

    received: List[tuple] = []

    async def handler(event, payload, source):
        received.append((event, payload, source))

    bus = EventBus(db=FakeDB())
    bus.register("stock.below_reorder", handler)
    await bus.start()
    assert bus.is_distributed is False  # no Redis

    await bus.publish("stock.below_reorder", {"sku": "BV-BOK-01"}, source="sentinel")

    assert received == [("stock.below_reorder", {"sku": "BV-BOK-01"}, "sentinel")]
    await bus.stop()


@pytest.mark.asyncio
async def test_source_agent_not_echoed_via_local_helper():
    """
    The registry's _deliver_to_subscribers skips the emitting agent so we
    don't feed our own event back. Verify by wiring a bus handler that
    simulates the registry's fan-out.
    """
    from agents.registry import _deliver_to_subscribers, AGENT_REGISTRY, _event_subscriptions

    # Clean state
    AGENT_REGISTRY.clear()
    _event_subscriptions.clear()

    calls: List[str] = []

    class DummyAgent:
        def __init__(self, aid):
            self.agent_id = aid

        async def on_event(self, event, payload):
            calls.append(self.agent_id)

    AGENT_REGISTRY["sentinel"] = DummyAgent("sentinel")
    AGENT_REGISTRY["taskmaster"] = DummyAgent("taskmaster")
    _event_subscriptions["stock.below_reorder"] = ["sentinel", "taskmaster"]

    await _deliver_to_subscribers("stock.below_reorder", {"sku": "X"}, source="sentinel")

    # sentinel emitted, should be skipped; taskmaster should receive
    assert calls == ["taskmaster"]

    AGENT_REGISTRY.clear()
    _event_subscriptions.clear()


# ============================================================================
# Mongo persistence
# ============================================================================


@pytest.mark.asyncio
async def test_publish_persists_to_agent_events(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_HOST", raising=False)

    db = FakeDB()
    bus = EventBus(db=db)
    await bus.start()
    await bus.publish("anomaly.detected", {"severity": "HIGH"}, source="oracle")
    await bus.stop()

    events = db.get_collection("agent_events").inserts
    assert len(events) == 1
    assert events[0]["event"] == "anomaly.detected"
    assert events[0]["source"] == "oracle"
    assert events[0]["payload"] == {"severity": "HIGH"}
    assert "emitted_at" in events[0]
    assert "emitted_dt" in events[0]
    assert events[0]["worker_id"] == bus.worker_id


@pytest.mark.asyncio
async def test_publish_persists_even_when_redis_fails(monkeypatch):
    """Mongo write is best-effort-but-always-attempted; Redis failure can't skip it."""
    monkeypatch.delenv("REDIS_URL", raising=False)

    db = FakeDB()
    bus = EventBus(db=db)
    # Simulate a broken redis: we set _redis manually to something that
    # raises on publish.
    class BrokenRedis:
        async def publish(self, *a, **k):
            raise RuntimeError("connection reset")

    bus._redis = BrokenRedis()
    bus._started = True  # pretend start() succeeded

    received = []

    async def handler(event, payload, source):
        received.append((event, payload))

    bus.register("x.event", handler)
    await bus.publish("x.event", {"k": 1}, source="t")

    # Event persisted
    assert len(db.get_collection("agent_events").inserts) == 1
    # Local fallback fired the handler
    assert received == [("x.event", {"k": 1})]


# ============================================================================
# Redis path with fake client
# ============================================================================


@pytest.mark.asyncio
async def test_redis_publish_shape_and_channel(monkeypatch):
    """
    With Redis plugged in, publish() should JSON-encode the message and
    send it to the configured channel. It should NOT directly dispatch
    locally (the listener loop handles that after Redis echoes the message).
    """
    monkeypatch.delenv("REDIS_URL", raising=False)
    fake = FakeRedis()
    bus = EventBus(db=FakeDB())
    # Bypass start() Redis connect — inject the fake directly
    bus._redis = fake
    bus._pubsub = fake._pubsub
    bus._running = True
    bus._started = True

    await bus.publish("stock.below_reorder", {"sku": "X", "qty": 2}, source="sentinel")

    assert len(fake.publishes) == 1
    channel, raw = fake.publishes[0]
    assert channel == "ims.agents.events"

    msg = json.loads(raw)
    assert msg["event"] == "stock.below_reorder"
    assert msg["payload"] == {"sku": "X", "qty": 2}
    assert msg["source"] == "sentinel"
    assert msg["worker_id"] == bus.worker_id
    assert "emitted_at" in msg


@pytest.mark.asyncio
async def test_listener_dispatches_inbound_redis_message(monkeypatch):
    """
    The listener loop reads messages from pubsub and dispatches them to
    local handlers. Simulates worker B receiving worker A's event.
    """
    monkeypatch.delenv("REDIS_URL", raising=False)
    fake = FakeRedis()
    bus = EventBus(db=FakeDB())
    bus._redis = fake
    bus._pubsub = fake._pubsub
    bus._running = True
    bus._started = True

    received: List[tuple] = []

    async def handler(event, payload, source):
        received.append((event, payload, source))

    bus.register("anomaly.detected", handler)

    # Manually start the listener (as start() would have)
    bus._listener_task = asyncio.create_task(bus._listen_loop())

    # Inject an inbound message from "another worker"
    other_worker_msg = json.dumps({
        "event": "anomaly.detected",
        "payload": {"severity": "CRITICAL"},
        "source": "oracle",
        "worker_id": "some-other-worker-uuid",
        "emitted_at": "2026-04-20T10:00:00Z",
    })
    await fake._pubsub.inject(other_worker_msg)

    # Wait briefly for the listener to dispatch
    for _ in range(20):
        if received:
            break
        await asyncio.sleep(0.05)

    assert received == [("anomaly.detected", {"severity": "CRITICAL"}, "oracle")]

    await bus.stop()


@pytest.mark.asyncio
async def test_cross_worker_simulation(monkeypatch):
    """
    Two independent EventBus instances share the same fake Redis. When
    bus_a publishes, bus_b's handler fires — the key multi-worker property.
    """
    monkeypatch.delenv("REDIS_URL", raising=False)

    fake = FakeRedis()

    # Both buses share the same pubsub. In real Redis each client would
    # have its own subscription, but the FakeRedis's single pubsub queue
    # simulates the broadcast behavior from the receiving side.
    bus_a = EventBus(db=FakeDB())
    bus_b = EventBus(db=FakeDB())
    for bus in (bus_a, bus_b):
        bus._redis = fake
        bus._pubsub = fake._pubsub
        bus._running = True
        bus._started = True

    received_on_b: List[tuple] = []

    async def b_handler(event, payload, source):
        received_on_b.append((event, payload, source))

    bus_b.register("stock.below_reorder", b_handler)
    bus_b._listener_task = asyncio.create_task(bus_b._listen_loop())

    # A publishes; since they share pubsub, B's listener sees it.
    await bus_a.publish("stock.below_reorder", {"sku": "BV-BOK-01"}, source="sentinel")

    for _ in range(30):
        if received_on_b:
            break
        await asyncio.sleep(0.05)

    assert received_on_b == [("stock.below_reorder", {"sku": "BV-BOK-01"}, "sentinel")]

    await bus_b.stop()


# ============================================================================
# URL resolution
# ============================================================================


def test_resolve_redis_url_prefers_url(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://u:p@h:1234/2")
    monkeypatch.setenv("REDIS_HOST", "other")
    assert _resolve_redis_url() == "redis://u:p@h:1234/2"


def test_resolve_redis_url_from_host_password(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("REDIS_HOST", "redis.internal")
    monkeypatch.setenv("REDIS_PORT", "6380")
    monkeypatch.setenv("REDIS_PASSWORD", "secret")
    monkeypatch.setenv("REDIS_DB", "3")
    assert _resolve_redis_url() == "redis://:secret@redis.internal:6380/3"


def test_resolve_redis_url_no_config(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_HOST", raising=False)
    assert _resolve_redis_url() is None


# ============================================================================
# Handler failure isolation
# ============================================================================


@pytest.mark.asyncio
async def test_one_failing_handler_does_not_block_others(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)

    calls: List[str] = []

    async def bad(event, payload, source):
        calls.append("bad-before")
        raise RuntimeError("handler crash")

    async def good(event, payload, source):
        calls.append("good")

    bus = EventBus(db=FakeDB())
    bus.register("x", bad)
    bus.register("x", good)
    await bus.start()
    await bus.publish("x", {}, source="t")
    await bus.stop()

    assert "bad-before" in calls
    assert "good" in calls
