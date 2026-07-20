"""
IMS 2.0 - Agent config idempotency + heartbeat tests
=====================================================
Covers the Jarvis-fleet fix:

  1. seed_configs() is idempotent -- repeated calls (mimicking N boot workers)
     leave EXACTLY one doc per agent_id, never duplicates.
  2. Reads are deterministic under RESIDUAL duplicates: get_config() and
     get_all_configs() prefer the enabled=True twin so a stray enabled=None/
     False duplicate can't silently keep an agent dark.
  3. ensure_indexes() fails LOUD (does not raise) when duplicates already exist.
  4. background_tick() writes an agent_heartbeats row on ok / skipped ticks so
     the Jarvis screen can show real liveness even for an idle/off agent.

Self-contained: a tiny in-memory FakeCollection emulates just the pymongo
surface these paths touch (no mongomock dependency, no real Mongo).
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.config import AgentConfigManager, DEFAULT_AGENT_CONFIGS
from agents.base import JarvisAgent, AgentType


# ---------------------------------------------------------------------------
# Minimal in-memory pymongo-ish collection
# ---------------------------------------------------------------------------


def _enabled_rank(v):
    # Mongo sort order for `enabled DESC`: true, false, null.
    if v is True:
        return 0
    if v is False:
        return 1
    return 2


def _apply_sort(docs, sort):
    def key(d):
        parts = []
        for field, _direction in sort:
            if field == "enabled":
                parts.append(_enabled_rank(d.get("enabled")))
            else:
                val = d.get(field)
                parts.append(val if val is not None else 0)
        return tuple(parts)

    return sorted(docs, key=key)


def _project(doc, projection):
    d = dict(doc)
    if projection and projection.get("_id") == 0:
        d.pop("_id", None)
    return d


class _Cursor:
    def __init__(self, docs, projection):
        self._docs = docs
        self._projection = projection

    def sort(self, spec):
        self._docs = _apply_sort(self._docs, spec)
        return self

    def __iter__(self):
        return iter(_project(d, self._projection) for d in self._docs)


class FakeCollection:
    def __init__(self):
        self.docs = []
        self._auto = 0
        self._unique_keys = set()

    # -- indexes --
    def create_index(self, key, unique=False, name=None):
        if unique:
            seen = set()
            for d in self.docs:
                v = d.get(key)
                if v in seen:
                    raise Exception(f"E11000 duplicate key on {key}={v!r}")
                seen.add(v)
            self._unique_keys.add(key)
        return name or key

    # -- helpers --
    def _match(self, doc, flt):
        return all(doc.get(k) == v for k, v in (flt or {}).items())

    def _unique_conflict(self, candidate, ignore=None):
        for key in self._unique_keys:
            for existing in self.docs:
                if existing is ignore:
                    continue
                if existing.get(key) == candidate.get(key):
                    return True
        return False

    # -- reads --
    def find(self, flt=None, projection=None):
        res = [d for d in self.docs if self._match(d, flt)]
        return _Cursor(res, projection)

    def find_one(self, flt=None, projection=None, sort=None):
        res = [d for d in self.docs if self._match(d, flt)]
        if sort:
            res = _apply_sort(res, sort)
        if not res:
            return None
        return _project(res[0], projection)

    # -- writes --
    def insert_one(self, doc):
        if "_id" not in doc:
            self._auto += 1
            doc["_id"] = self._auto
        if self._unique_conflict(doc, ignore=doc):
            raise Exception("E11000 duplicate key")
        self.docs.append(doc)

    def _apply_update(self, d, update, inserted):
        if "$set" in update:
            d.update(update["$set"])
        if inserted and "$setOnInsert" in update:
            for k, v in update["$setOnInsert"].items():
                d.setdefault(k, v)
        if "$inc" in update:
            for k, v in update["$inc"].items():
                d[k] = (d.get(k) or 0) + v

    def update_one(self, flt, update, upsert=False):
        matched = [d for d in self.docs if self._match(d, flt)]
        if matched:
            self._apply_update(matched[0], update, inserted=False)
            return
        if upsert:
            newdoc = dict(flt or {})
            self._apply_update(newdoc, update, inserted=True)
            self.insert_one(newdoc)


class FakeDB:
    def __init__(self):
        self._cols = {}

    def get_collection(self, name):
        return self._cols.setdefault(name, FakeCollection())


# ---------------------------------------------------------------------------
# 1 + 3. Idempotent seeding + unique index
# ---------------------------------------------------------------------------


def test_seed_configs_is_idempotent_no_duplicates():
    db = FakeDB()
    mgr = AgentConfigManager(db=db)

    # Four "workers" each seed -- must NOT create duplicates.
    for _ in range(4):
        mgr.seed_configs()

    col = db.get_collection("agent_config")
    counts = {}
    for d in col.docs:
        counts[d["agent_id"]] = counts.get(d["agent_id"], 0) + 1

    assert len(col.docs) == len(DEFAULT_AGENT_CONFIGS)
    assert all(c == 1 for c in counts.values()), counts


def test_seed_preserves_operator_enabled_state():
    """A later seed must NOT clobber an operator's enabled toggle
    ($setOnInsert only touches absent docs)."""
    db = FakeDB()
    mgr = AgentConfigManager(db=db)
    mgr.seed_configs()

    col = db.get_collection("agent_config")
    # Operator turns pixel ON (default ships False).
    col.update_one({"agent_id": "pixel"}, {"$set": {"enabled": True}})

    mgr.seed_configs()  # boot again
    pixel = col.find_one({"agent_id": "pixel"})
    assert pixel["enabled"] is True  # preserved, not reset to default False


def test_ensure_indexes_fails_soft_on_existing_duplicates(caplog):
    db = FakeDB()
    col = db.get_collection("agent_config")
    col.insert_one({"agent_id": "oracle", "enabled": True})
    col.insert_one({"agent_id": "oracle", "enabled": False})

    mgr = AgentConfigManager(db=db)
    # Must not raise even though the unique index can't build.
    mgr.ensure_indexes()


# ---------------------------------------------------------------------------
# 2. Deterministic reads under residual duplicates
# ---------------------------------------------------------------------------


def test_get_config_prefers_enabled_true_twin():
    db = FakeDB()
    col = db.get_collection("agent_config")
    # Simulate the live bug: duplicates with conflicting enabled values.
    col.insert_one({"agent_id": "sentinel", "enabled": None})
    col.insert_one({"agent_id": "sentinel", "enabled": False})
    col.insert_one({"agent_id": "sentinel", "enabled": True})

    mgr = AgentConfigManager(db=db)
    cfg = mgr.get_config("sentinel")
    assert cfg["enabled"] is True


def test_get_all_configs_collapses_duplicates():
    db = FakeDB()
    col = db.get_collection("agent_config")
    col.insert_one({"agent_id": "nexus", "enabled": None})
    col.insert_one({"agent_id": "nexus", "enabled": True})
    col.insert_one({"agent_id": "oracle", "enabled": False})

    mgr = AgentConfigManager(db=db)
    configs = mgr.get_all_configs()
    by_id = {}
    for c in configs:
        by_id.setdefault(c["agent_id"], []).append(c)

    assert len(by_id["nexus"]) == 1
    assert by_id["nexus"][0]["enabled"] is True  # enabled twin wins
    assert len(by_id["oracle"]) == 1


def test_default_enabled_states_match_intended_fleet():
    """The intended live fleet: everything on except PIXEL."""
    intended = {
        "jarvis": True, "cortex": True, "sentinel": True, "nexus": True,
        "oracle": True, "taskmaster": True, "megaphone": True, "pixel": False,
    }
    got = {c["agent_id"]: c["enabled"] for c in DEFAULT_AGENT_CONFIGS}
    assert got == intended


# ---------------------------------------------------------------------------
# 4. Heartbeats
# ---------------------------------------------------------------------------


class _ProbeAgent(JarvisAgent):
    agent_id = "probe"
    agent_name = "PROBE"
    agent_type = AgentType.MONITOR

    def __init__(self, db=None, boom=False):
        super().__init__(db=db)
        self._boom = boom

    async def _do_background_work(self):
        if self._boom:
            raise RuntimeError("kaboom")


@pytest.mark.asyncio
async def test_heartbeat_written_on_ok_tick():
    db = FakeDB()
    db.get_collection("agent_config").insert_one(
        {"agent_id": "probe", "enabled": True}
    )
    agent = _ProbeAgent(db=db)
    await agent.background_tick()

    hb = db.get_collection("agent_heartbeats").find_one({"agent_id": "probe"})
    assert hb is not None
    assert hb["last_tick_status"] == "ok"
    assert hb["tick_count"] == 1
    assert hb["last_tick_at"] is not None


@pytest.mark.asyncio
async def test_heartbeat_written_on_skipped_tick():
    db = FakeDB()
    db.get_collection("agent_config").insert_one(
        {"agent_id": "probe", "enabled": False}
    )
    agent = _ProbeAgent(db=db)
    await agent.background_tick()

    hb = db.get_collection("agent_heartbeats").find_one({"agent_id": "probe"})
    assert hb is not None
    assert hb["last_tick_status"] == "skipped"


@pytest.mark.asyncio
async def test_heartbeat_written_on_error_tick():
    db = FakeDB()
    db.get_collection("agent_config").insert_one(
        {"agent_id": "probe", "enabled": True}
    )
    agent = _ProbeAgent(db=db, boom=True)
    await agent.background_tick()

    hb = db.get_collection("agent_heartbeats").find_one({"agent_id": "probe"})
    assert hb is not None
    assert hb["last_tick_status"] == "error"
    assert "kaboom" in (hb["last_tick_error"] or "")
