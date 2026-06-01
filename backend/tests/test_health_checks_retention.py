"""
IMS 2.0 - health_checks retention (SENTINEL telemetry self-caps)
================================================================
The `health_checks` collection gets one row per ~60s SENTINEL tick and would
grow UNBOUNDED (it was the largest collection in prod + the main driver of disk
growth on a small Mongo volume). It is now bounded two ways:

  1. A TTL index (expireAfterSeconds=14d) declared in schemas.INDEXES and built
     by connection.ensure_indexes -- the server-side auto-expiry. (The build
     needs >=500MB free disk, so on a small/full volume it is deferred.)
  2. A DEFENSIVE in-tick prune (sentinel.prune_health_checks) that deletes rows
     older than the retention window on every tick -- this is what actually caps
     growth TODAY, with or without the TTL index.

These are pure-function tests of (2) + a declaration assertion for (1); no mongo
required, so they run in every environment.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.implementations.sentinel import (  # noqa: E402
    HEALTH_CHECK_RETENTION_DAYS,
    prune_health_checks,
)


class _FakeCol:
    """Minimal fake supporting delete_many({'timestamp': {'$lt': cutoff}})."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def delete_many(self, query):
        self.calls.append(query)
        lt = query["timestamp"]["$lt"]
        keep = [r for r in self.rows if not (r["timestamp"] < lt)]
        deleted = len(self.rows) - len(keep)
        self.rows = keep

        class _Res:
            deleted_count = deleted

        return _Res()


def _rows(now):
    return [
        {"timestamp": now - timedelta(days=40)},          # old
        {"timestamp": now - timedelta(days=15)},          # old (>14d)
        {"timestamp": now - timedelta(days=14, hours=1)}, # old (just over)
        {"timestamp": now - timedelta(days=13, hours=23)},# keep (just under)
        {"timestamp": now - timedelta(hours=1)},          # keep
    ]


def test_default_retention_is_14_days():
    assert HEALTH_CHECK_RETENTION_DAYS == 14


def test_prune_deletes_old_keeps_recent():
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    col = _FakeCol(_rows(now))
    deleted = prune_health_checks(col, now=now)
    assert deleted == 3
    assert len(col.rows) == 2
    cutoff = now - timedelta(days=14)
    assert all(r["timestamp"] >= cutoff for r in col.rows)
    # queried on the timestamp field with a $lt bound
    assert col.calls and "timestamp" in col.calls[0] and "$lt" in col.calls[0]["timestamp"]


def test_prune_custom_retention():
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    col = _FakeCol([
        {"timestamp": now - timedelta(days=8)},  # old for 7d window
        {"timestamp": now - timedelta(days=2)},  # keep
    ])
    assert prune_health_checks(col, retention_days=7, now=now) == 1
    assert len(col.rows) == 1


def test_prune_none_collection_is_zero():
    assert prune_health_checks(None) == 0


def test_prune_is_failsoft_on_error():
    class _BadCol:
        def delete_many(self, query):
            raise RuntimeError("mongo unavailable")

    assert prune_health_checks(_BadCol(), now=datetime.now(timezone.utc)) == 0


def test_prune_nothing_to_delete_returns_zero():
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    col = _FakeCol([{"timestamp": now - timedelta(hours=2)}])
    assert prune_health_checks(col, now=now) == 0
    assert len(col.rows) == 1


def test_ttl_index_declared_in_schemas():
    from database.schemas import INDEXES

    hc = INDEXES.get("health_checks")
    assert hc, "health_checks TTL index must be declared in schemas.INDEXES"
    ttl = [i for i in hc if "expireAfterSeconds" in i]
    assert ttl, "expected a TTL index spec"
    assert ttl[0]["expireAfterSeconds"] == 14 * 24 * 60 * 60
    assert ttl[0]["keys"] == [("timestamp", 1)]
