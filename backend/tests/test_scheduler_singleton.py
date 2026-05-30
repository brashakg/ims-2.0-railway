"""
IMS 2.0 - Agent scheduler singleton guard tests
=================================================
Verifies BUG-2 fix: in a multi-worker deploy, only ONE process should run the
agent schedule. Two layers of guard:

  1. RUN_AGENT_SCHEDULER env gate (default "true"). "false" -> skip entirely.
  2. A Redis leader lock (SET key uuid NX EX TTL). Across N processes that all
     have the env gate on, exactly ONE acquires the lock and runs jobs; the
     rest skip.

Fail-soft: when Redis is not configured / unreachable, the scheduler RUNS
(preserving today's single-worker behavior) rather than silently scheduling
nothing.

No real Redis: a shared in-memory FakeRedis simulates SET NX semantics across
multiple AgentScheduler instances. No MongoDB: AgentScheduler(db=None) reads
DEFAULT_AGENT_CONFIGS.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents.scheduler as scheduler_mod
from agents.scheduler import AgentScheduler, _scheduler_enabled


# ---------------------------------------------------------------------------
# Fake Redis with SET NX EX semantics, shared across "workers"
# ---------------------------------------------------------------------------


class FakeRedisStore:
    """A single shared key-space several FakeRedis clients point at, so we can
    simulate multiple processes contending for one lock key."""

    def __init__(self):
        self.kv: dict[str, str] = {}


class FakeRedis:
    def __init__(self, store: FakeRedisStore):
        self._store = store
        self.closed = False

    def set(self, key, value, nx=False, ex=None):
        # Emulate SET key value NX: only set if absent; return True/None.
        if nx and key in self._store.kv:
            return None
        self._store.kv[key] = value
        return True

    def get(self, key):
        return self._store.kv.get(key)

    def expire(self, key, ttl):
        return key in self._store.kv

    def delete(self, key):
        self._store.kv.pop(key, None)
        return 1

    def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# Env gate
# ---------------------------------------------------------------------------


def test_scheduler_enabled_defaults_true(monkeypatch):
    monkeypatch.delenv("RUN_AGENT_SCHEDULER", raising=False)
    assert _scheduler_enabled() is True


@pytest.mark.parametrize("val", ["false", "False", "0", "no", "off", "  OFF  "])
def test_scheduler_enabled_false_values(monkeypatch, val):
    monkeypatch.setenv("RUN_AGENT_SCHEDULER", val)
    assert _scheduler_enabled() is False


@pytest.mark.parametrize("val", ["true", "1", "yes", "on", "anything"])
def test_scheduler_enabled_truthy_values(monkeypatch, val):
    monkeypatch.setenv("RUN_AGENT_SCHEDULER", val)
    assert _scheduler_enabled() is True


@pytest.mark.asyncio
async def test_start_skips_when_env_gate_false(monkeypatch):
    """RUN_AGENT_SCHEDULER=false -> start() schedules nothing and the
    underlying APScheduler is never started."""
    monkeypatch.setenv("RUN_AGENT_SCHEDULER", "false")
    sched = AgentScheduler(db=None)

    await sched.start(agents={})

    assert sched._running is False
    if sched._scheduler is not None:  # APScheduler available in this env
        assert sched._scheduler.running is False
        assert sched._scheduler.get_jobs() == []
    await sched.shutdown()  # must be safe even though we never started


# ---------------------------------------------------------------------------
# Fail-soft: no Redis configured -> run anyway
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runs_when_no_redis_configured(monkeypatch):
    """No REDIS_* env -> _make_redis_client returns None -> we still run
    (single-worker assumption preserved)."""
    monkeypatch.setenv("RUN_AGENT_SCHEDULER", "true")
    monkeypatch.setattr(scheduler_mod, "_make_redis_client", lambda: None)

    sched = AgentScheduler(db=None)
    acquired = await sched._acquire_leadership()

    assert acquired is True
    assert sched._is_leader is True
    assert sched._redis is None  # no client held when Redis absent
    await sched.shutdown()


@pytest.mark.asyncio
async def test_runs_when_redis_set_raises(monkeypatch):
    """Redis reachable but SET blows up -> fail-soft to running + warning."""
    monkeypatch.setenv("RUN_AGENT_SCHEDULER", "true")

    class BoomRedis(FakeRedis):
        def set(self, *a, **k):
            raise RuntimeError("redis down mid-op")

    store = FakeRedisStore()
    monkeypatch.setattr(scheduler_mod, "_make_redis_client",
                        lambda: BoomRedis(store))

    sched = AgentScheduler(db=None)
    acquired = await sched._acquire_leadership()

    assert acquired is True
    assert sched._is_leader is True
    await sched.shutdown()


# ---------------------------------------------------------------------------
# Leader election: only the first acquirer runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_first_acquirer_becomes_leader(monkeypatch):
    """Two schedulers share one fake Redis. The first SET NX wins; the second
    is denied the lock and must NOT run jobs."""
    monkeypatch.setenv("RUN_AGENT_SCHEDULER", "true")
    store = FakeRedisStore()
    monkeypatch.setattr(scheduler_mod, "_make_redis_client",
                        lambda: FakeRedis(store))

    sched_a = AgentScheduler(db=None)
    sched_b = AgentScheduler(db=None)

    a_won = await sched_a._acquire_leadership()
    b_won = await sched_b._acquire_leadership()

    assert a_won is True
    assert b_won is False
    assert sched_a._is_leader is True
    assert sched_b._is_leader is False
    # The key holds worker A's instance id.
    assert store.kv[scheduler_mod.SCHEDULER_LEADER_KEY] == sched_a._instance_id

    await sched_a.shutdown()
    await sched_b.shutdown()


@pytest.mark.asyncio
async def test_loser_start_schedules_no_jobs(monkeypatch):
    """End-to-end: the worker that loses the lock returns from start() with no
    APScheduler jobs and the scheduler not running."""
    monkeypatch.setenv("RUN_AGENT_SCHEDULER", "true")
    store = FakeRedisStore()
    # Pre-seed the lock as if another worker already owns it.
    store.kv[scheduler_mod.SCHEDULER_LEADER_KEY] = "some-other-worker"
    monkeypatch.setattr(scheduler_mod, "_make_redis_client",
                        lambda: FakeRedis(store))

    sched = AgentScheduler(db=None)
    await sched.start(agents={})

    assert sched._is_leader is False
    assert sched._running is False
    if sched._scheduler is not None:
        assert sched._scheduler.running is False
        assert sched._scheduler.get_jobs() == []
    await sched.shutdown()


@pytest.mark.asyncio
async def test_leader_releases_lock_on_shutdown(monkeypatch):
    """The leader deletes its key on shutdown so another worker can take over
    without waiting for the TTL."""
    monkeypatch.setenv("RUN_AGENT_SCHEDULER", "true")
    store = FakeRedisStore()
    monkeypatch.setattr(scheduler_mod, "_make_redis_client",
                        lambda: FakeRedis(store))

    sched = AgentScheduler(db=None)
    won = await sched._acquire_leadership()
    assert won is True
    assert scheduler_mod.SCHEDULER_LEADER_KEY in store.kv

    await sched._release_leadership()
    assert scheduler_mod.SCHEDULER_LEADER_KEY not in store.kv
    assert sched._is_leader is False


@pytest.mark.asyncio
async def test_release_does_not_delete_another_workers_lock(monkeypatch):
    """If our lock expired and another worker reclaimed it, release must NOT
    delete the new owner's key."""
    monkeypatch.setenv("RUN_AGENT_SCHEDULER", "true")
    store = FakeRedisStore()
    monkeypatch.setattr(scheduler_mod, "_make_redis_client",
                        lambda: FakeRedis(store))

    sched = AgentScheduler(db=None)
    await sched._acquire_leadership()

    # Simulate takeover: another worker now owns the key.
    store.kv[scheduler_mod.SCHEDULER_LEADER_KEY] = "new-owner-worker"

    await sched._release_leadership()
    # New owner's lock survives.
    assert store.kv[scheduler_mod.SCHEDULER_LEADER_KEY] == "new-owner-worker"
