"""
IMS 2.0 - Nightly BVI catalog parity monitor tests
==================================================
Covers api/services/bvi_parity.py (the importable home of the parity oracle)
and SENTINEL's nightly hook:

  1. run_parity_snapshot -- comparison on fake data (counts, missing-SKU
     detection), fail-soft on missing PG url / PG down / missing Mongo, and
     the hard 25-item cap on stored sample lists.
  2. evaluate_drift -- missing SKUs and count-delta tolerance verdicts.
  3. store_parity_snapshot / prune_parity_snapshots -- persistence + the
     30-day retention trim on write.
  4. run_and_record_parity -- drift creates a SYSTEM task (deduped per IST
     day on re-run); a PG-down night stores an ok=False snapshot and files
     NO task; nothing ever raises.
  5. SENTINEL._maybe_run_bvi_parity -- env gate, the 02:30 IST wall-clock
     gate, once-per-IST-day dedupe (in-memory + restart-safe DB lookup), and
     scheduler safety when the service explodes.
  6. GET /api/v1/admin/online-store/parity -- the extended response now
     carries the bvi_nightly block (SUPERADMIN only, unchanged gate).

All service/agent tests use in-memory fakes (no real Mongo, no real
Postgres, no network) -- same style as test_bvi_drift_and_parity.py and
test_event_bus.py. No emojis.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("ECOMMERCE_DATABASE_URL", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import bvi_parity as bp  # noqa: E402
from api.services.bvi_parity import (  # noqa: E402
    BviSnapshot,
    evaluate_drift,
    latest_parity_snapshot,
    monitor_enabled,
    prune_parity_snapshots,
    run_and_record_parity,
    run_parity_snapshot,
    store_parity_snapshot,
)

IST = timezone(timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------------------
# Minimal in-memory fakes (get_collection-shaped, like agents/base.py expects)
# ---------------------------------------------------------------------------


def _matches(row: Dict[str, Any], query: Dict[str, Any]) -> bool:
    """Tiny matcher: equality, $lt, $in, $nin. Enough for this module."""
    for key, cond in (query or {}).items():
        val = row.get(key)
        if isinstance(cond, dict):
            if "$lt" in cond and not (val is not None and val < cond["$lt"]):
                return False
            if "$in" in cond and val not in cond["$in"]:
                return False
            if "$nin" in cond and val in cond["$nin"]:
                return False
        elif val != cond:
            return False
    return True


class _FakeCursor:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = list(rows)

    def sort(self, key_or_list, direction=None):
        if isinstance(key_or_list, list):
            key, direction = key_or_list[0]
        else:
            key = key_or_list
        self._rows.sort(
            key=lambda r: r.get(key) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=(direction == -1),
        )
        return self

    def skip(self, n):
        self._rows = self._rows[int(n):]
        return self

    def limit(self, n):
        if n:
            self._rows = self._rows[: int(n)]
        return self

    def __iter__(self):
        return iter(self._rows)


class _FakeDeleteResult:
    def __init__(self, n: int):
        self.deleted_count = n


class _FakeColl:
    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None):
        self.rows: List[Dict[str, Any]] = list(rows or [])

    def find(self, query=None, projection=None, *_a, **_k):
        return _FakeCursor([r for r in self.rows if _matches(r, query or {})])

    def find_one(self, query=None, projection=None, *_a, **_k):
        for r in self.rows:
            if _matches(r, query or {}):
                return r
        return None

    def count_documents(self, query=None):
        return sum(1 for r in self.rows if _matches(r, query or {}))

    def insert_one(self, doc):
        self.rows.append(doc)
        return doc

    def delete_many(self, query):
        keep = [r for r in self.rows if not _matches(r, query or {})]
        deleted = len(self.rows) - len(keep)
        self.rows = keep
        return _FakeDeleteResult(deleted)


class _FakeDb:
    def __init__(self, colls: Optional[Dict[str, _FakeColl]] = None):
        self._colls: Dict[str, _FakeColl] = dict(colls or {})

    def get_collection(self, name: str) -> _FakeColl:
        if name not in self._colls:
            self._colls[name] = _FakeColl()
        return self._colls[name]


class _FakePgConn:
    """Stands in for a psycopg2 connection; collect_bvi_snapshot is patched
    so nothing ever touches it beyond close()."""

    def close(self):
        pass


def _run(coro):
    return asyncio.run(coro)


def _wire_pg(monkeypatch, bvi_snapshot: BviSnapshot):
    """Point the service at a fake, reachable Postgres yielding `bvi_snapshot`."""
    monkeypatch.setenv("BVI_DATABASE_URL", "postgresql://fake-host/fake-db")
    monkeypatch.setattr(bp, "_pg_connect", lambda url: _FakePgConn())
    monkeypatch.setattr(bp, "collect_bvi_snapshot", lambda conn: bvi_snapshot)


def _ims_db_with_variants(skus_with_barcodes: Dict[str, str], products: int = 0):
    """A fake IMS Mongo db whose catalog_variants carry the given sku->barcode
    map and whose catalog_products has `products` docs."""
    return _FakeDb(
        {
            "catalog_variants": _FakeColl(
                [
                    {"sku": sku, **({"store_barcode": bc} if bc else {})}
                    for sku, bc in skus_with_barcodes.items()
                ]
            ),
            "catalog_products": _FakeColl([{"sku": f"P{i}"} for i in range(products)]),
            "ecom_collections": _FakeColl(),
            "ecom_menus": _FakeColl(),
            "customers": _FakeColl(),
            "online_orders": _FakeColl(),
            "products": _FakeColl(),
        }
    )


# ===========================================================================
# 1. run_parity_snapshot -- fail-soft + comparison on fake data
# ===========================================================================


def test_snapshot_no_pg_url_is_ok_false(monkeypatch):
    monkeypatch.delenv("BVI_DATABASE_URL", raising=False)
    monkeypatch.setenv("ECOMMERCE_DATABASE_URL", "")
    snap = run_parity_snapshot(_FakeDb())
    assert snap["ok"] is False
    assert "not configured" in snap["reason"]
    assert snap["report"] is None


def test_snapshot_pg_down_is_ok_false_no_raise(monkeypatch):
    monkeypatch.setenv("BVI_DATABASE_URL", "postgresql://unreachable/fake")
    monkeypatch.setattr(bp, "_pg_connect", lambda url: None)
    snap = run_parity_snapshot(_FakeDb())
    assert snap["ok"] is False
    assert snap["reason"] == "postgres unreachable"
    assert snap["report"] is None


def test_snapshot_none_db_is_ok_false():
    snap = run_parity_snapshot(None)
    assert snap["ok"] is False
    assert "mongo" in snap["reason"]


def test_snapshot_counts_and_missing_sku_detection(monkeypatch):
    """BVI has A,B,C; IMS only has A,B -> missing C, variant delta -1."""
    bvi = BviSnapshot(
        products=2,
        variants=3,
        variant_skus=["A", "B", "C"],
        variant_barcode_by_sku={"A": "111"},
    )
    _wire_pg(monkeypatch, bvi)
    db = _ims_db_with_variants({"A": "111", "B": ""}, products=2)

    snap = run_parity_snapshot(db)
    assert snap["ok"] is True
    report = snap["report"]
    assert report["counts"]["products"] == {
        "bvi": 2, "ims": 2, "delta": 0, "match": True,
    }
    assert report["counts"]["variants"]["delta"] == -1
    assert report["sku_diff"]["missing_count"] == 1
    assert report["sku_diff"]["sample_missing"] == ["C"]
    assert report["barcode_diff"]["mismatched_count"] == 0
    assert report["gate_pass"] is False


def test_snapshot_sample_lists_hard_capped_at_25(monkeypatch):
    """40 missing SKUs -> exact missing_count=40, sample capped at 25, and the
    full missing_in_ims list is NOT stored. A larger sample_limit request is
    clamped to the hard cap."""
    bvi = BviSnapshot(
        products=40,
        variants=40,
        variant_skus=[f"SKU-{i:03d}" for i in range(40)],
    )
    _wire_pg(monkeypatch, bvi)
    db = _ims_db_with_variants({})

    snap = run_parity_snapshot(db, sample_limit=500)
    assert snap["ok"] is True
    sd = snap["report"]["sku_diff"]
    assert sd["missing_count"] == 40
    assert len(sd["sample_missing"]) == 25
    assert "missing_in_ims" not in sd  # compact form only
    assert "mismatched" not in snap["report"]["barcode_diff"]


def test_snapshot_read_failure_is_ok_false(monkeypatch):
    """A -1 count (read error) must NOT fabricate parity numbers."""
    bvi = BviSnapshot(products=-1, variants=3, variant_skus=["A"])
    _wire_pg(monkeypatch, bvi)
    snap = run_parity_snapshot(_ims_db_with_variants({"A": ""}))
    assert snap["ok"] is False
    assert "count read failed" in snap["reason"]
    assert "bvi.products" in snap["reason"]


# ===========================================================================
# 2. evaluate_drift -- missing SKUs + count tolerance
# ===========================================================================


def _snap(missing=0, products_delta=0, variants_delta=0, ok=True):
    return {
        "ok": ok,
        "report": {
            "sku_diff": {"missing_count": missing},
            "counts": {
                "products": {"delta": products_delta},
                "variants": {"delta": variants_delta},
            },
        } if ok else None,
    }


def test_drift_on_missing_skus():
    verdict = evaluate_drift(_snap(missing=3))
    assert verdict["drift"] is True
    assert any("missing" in r for r in verdict["reasons"])


def test_no_drift_within_count_tolerance():
    # default tolerance is 5
    verdict = evaluate_drift(_snap(products_delta=3), tolerance=5)
    assert verdict["drift"] is False
    assert verdict["reasons"] == []


def test_drift_beyond_count_tolerance():
    verdict = evaluate_drift(_snap(products_delta=-7), tolerance=5)
    assert verdict["drift"] is True
    assert any("products" in r for r in verdict["reasons"])


def test_drift_variants_delta_also_checked():
    verdict = evaluate_drift(_snap(variants_delta=9), tolerance=5)
    assert verdict["drift"] is True
    assert any("variants" in r for r in verdict["reasons"])


def test_zero_tolerance_env_override(monkeypatch):
    monkeypatch.setenv("BVI_PARITY_COUNT_TOLERANCE", "0")
    verdict = evaluate_drift(_snap(products_delta=1))
    assert verdict["drift"] is True


def test_failed_snapshot_is_never_drift():
    verdict = evaluate_drift(_snap(ok=False))
    assert verdict["drift"] is False
    assert evaluate_drift(None)["drift"] is False


# ===========================================================================
# 3. persistence + 30-day retention trim
# ===========================================================================


def test_store_snapshot_stamps_and_persists():
    db = _FakeDb()
    snap = {"ok": True, "report": {"gate_pass": True}}
    assert store_parity_snapshot(db, snap) is True
    rows = db.get_collection("bvi_parity_snapshots").rows
    assert len(rows) == 1
    assert rows[0]["timestamp"] is not None
    assert rows[0]["ist_date"] == snap["ist_date"]  # caller sees the day key
    # latest_parity_snapshot returns it, _id stripped
    latest = latest_parity_snapshot(db)
    assert latest is not None
    assert latest["ok"] is True
    assert "_id" not in latest


def test_store_snapshot_trims_rows_older_than_30_days():
    old_ts = datetime.now(timezone.utc) - timedelta(days=40)
    fresh_ts = datetime.now(timezone.utc) - timedelta(days=2)
    db = _FakeDb(
        {
            "bvi_parity_snapshots": _FakeColl(
                [
                    {"ok": True, "timestamp": old_ts, "ist_date": "2026-05-25"},
                    {"ok": True, "timestamp": fresh_ts, "ist_date": "2026-07-02"},
                ]
            )
        }
    )
    assert store_parity_snapshot(db, {"ok": True, "report": None}) is True
    rows = db.get_collection("bvi_parity_snapshots").rows
    timestamps = [r["timestamp"] for r in rows]
    assert old_ts not in timestamps  # >30d row trimmed on write
    assert fresh_ts in timestamps
    assert len(rows) == 2  # fresh + the new one


def test_prune_and_store_fail_soft():
    assert prune_parity_snapshots(None) == 0
    assert store_parity_snapshot(None, {"ok": False}) is False
    assert latest_parity_snapshot(None) is None
    assert latest_parity_snapshot(_FakeDb()) is None  # empty collection


# ===========================================================================
# 4. run_and_record_parity -- drift task, dedupe, PG-down night
# ===========================================================================


def test_drift_creates_system_task_deduped_on_rerun(monkeypatch):
    bvi = BviSnapshot(
        products=3,
        variants=3,
        variant_skus=["A", "B", "C"],
    )
    _wire_pg(monkeypatch, bvi)
    monkeypatch.delenv("BVI_PARITY_COUNT_TOLERANCE", raising=False)
    db = _ims_db_with_variants({"A": ""}, products=3)  # B, C missing -> drift

    first = run_and_record_parity(db)
    assert first["ok"] is True
    assert first["drift"]["drift"] is True

    tasks = db.get_collection("tasks").rows
    assert len(tasks) == 1
    task = tasks[0]
    assert task["source"] == "SYSTEM"
    assert task["status"] == "OPEN"
    assert task["source_ref"] == f"bvi_parity_drift:{first['ist_date']}"
    assert "parity" in task["title"].lower()

    # Same night re-run: a second snapshot is stored, but the ACTIVE task
    # with the same source_ref dedupes -- still exactly one task.
    second = run_and_record_parity(db)
    assert second["drift"]["drift"] is True
    assert len(db.get_collection("bvi_parity_snapshots").rows) == 2
    assert len(db.get_collection("tasks").rows) == 1


def test_no_drift_creates_no_task(monkeypatch):
    bvi = BviSnapshot(products=1, variants=1, variant_skus=["A"])
    _wire_pg(monkeypatch, bvi)
    db = _ims_db_with_variants({"A": ""}, products=1)

    snap = run_and_record_parity(db)
    assert snap["ok"] is True
    assert snap["drift"]["drift"] is False
    assert db.get_collection("tasks").rows == []
    assert len(db.get_collection("bvi_parity_snapshots").rows) == 1


def test_pg_down_night_stores_ok_false_snapshot_and_no_task(monkeypatch):
    monkeypatch.setenv("BVI_DATABASE_URL", "postgresql://unreachable/fake")
    monkeypatch.setattr(bp, "_pg_connect", lambda url: None)
    db = _FakeDb()

    snap = run_and_record_parity(db)  # must NOT raise
    assert snap["ok"] is False
    assert snap["drift"]["drift"] is False
    stored = db.get_collection("bvi_parity_snapshots").rows
    assert len(stored) == 1
    assert stored[0]["ok"] is False
    assert db.get_collection("tasks").rows == []


def test_run_and_record_never_raises(monkeypatch):
    """Even an unexpected internal explosion returns an ok=False dict."""
    monkeypatch.setattr(
        bp, "run_parity_snapshot",
        lambda _db, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    out = run_and_record_parity(_FakeDb())
    assert out["ok"] is False
    assert "boom" in out["reason"]


# ===========================================================================
# 5. SENTINEL nightly hook -- env gate, wall-clock gate, per-day dedupe
# ===========================================================================


def _sentinel_with_recorder(monkeypatch, db=None):
    from agents.implementations.sentinel import SentinelAgent

    calls: List[Any] = []

    def _recorder(passed_db):
        calls.append(passed_db)
        return {"ok": True, "drift": {"drift": False}}

    monkeypatch.setattr(bp, "run_and_record_parity", _recorder)
    return SentinelAgent(db=db if db is not None else _FakeDb()), calls


def test_sentinel_skips_when_monitor_off(monkeypatch):
    monkeypatch.setenv("BVI_PARITY_MONITOR", "off")
    agent, calls = _sentinel_with_recorder(monkeypatch)
    _run(agent._maybe_run_bvi_parity(now=datetime(2026, 7, 5, 3, 0, tzinfo=IST)))
    assert calls == []


def test_sentinel_skips_before_0230_ist(monkeypatch):
    monkeypatch.delenv("BVI_PARITY_MONITOR", raising=False)
    agent, calls = _sentinel_with_recorder(monkeypatch)
    _run(agent._maybe_run_bvi_parity(now=datetime(2026, 7, 5, 1, 15, tzinfo=IST)))
    _run(agent._maybe_run_bvi_parity(now=datetime(2026, 7, 5, 2, 29, tzinfo=IST)))
    assert calls == []


def test_sentinel_runs_once_per_ist_day(monkeypatch):
    monkeypatch.delenv("BVI_PARITY_MONITOR", raising=False)
    agent, calls = _sentinel_with_recorder(monkeypatch)
    _run(agent._maybe_run_bvi_parity(now=datetime(2026, 7, 5, 2, 30, tzinfo=IST)))
    assert len(calls) == 1
    # later ticks the same IST day do nothing
    _run(agent._maybe_run_bvi_parity(now=datetime(2026, 7, 5, 2, 31, tzinfo=IST)))
    _run(agent._maybe_run_bvi_parity(now=datetime(2026, 7, 5, 23, 59, tzinfo=IST)))
    assert len(calls) == 1
    # the NEXT night runs again
    _run(agent._maybe_run_bvi_parity(now=datetime(2026, 7, 6, 2, 45, tzinfo=IST)))
    assert len(calls) == 2


def test_sentinel_dedupe_survives_restart_via_db(monkeypatch):
    """A snapshot already stored for today (by a pre-restart process) means
    the fresh agent instance must NOT re-run tonight's check."""
    monkeypatch.delenv("BVI_PARITY_MONITOR", raising=False)
    db = _FakeDb(
        {"bvi_parity_snapshots": _FakeColl([{"ist_date": "2026-07-05", "ok": True}])}
    )
    agent, calls = _sentinel_with_recorder(monkeypatch, db=db)
    _run(agent._maybe_run_bvi_parity(now=datetime(2026, 7, 5, 4, 0, tzinfo=IST)))
    assert calls == []
    assert agent._bvi_parity_last_ist_date == "2026-07-05"


def test_sentinel_hook_never_raises_when_service_explodes(monkeypatch):
    """Scheduler safety: an exploding service is swallowed with a warning."""
    from agents.implementations.sentinel import SentinelAgent

    def _boom(_db):
        raise RuntimeError("simulated parity explosion")

    monkeypatch.delenv("BVI_PARITY_MONITOR", raising=False)
    monkeypatch.setattr(bp, "run_and_record_parity", _boom)
    agent = SentinelAgent(db=_FakeDb())
    # Must not raise even though the underlying call does.
    _run(agent._maybe_run_bvi_parity(now=datetime(2026, 7, 5, 3, 0, tzinfo=IST)))
    # And it must not retry every tick for the rest of the night.
    assert agent._bvi_parity_last_ist_date == "2026-07-05"


def test_monitor_enabled_env_values(monkeypatch):
    monkeypatch.delenv("BVI_PARITY_MONITOR", raising=False)
    assert monitor_enabled() is True  # default on
    for off in ("off", "0", "false", "no", "OFF"):
        monkeypatch.setenv("BVI_PARITY_MONITOR", off)
        assert monitor_enabled() is False
    monkeypatch.setenv("BVI_PARITY_MONITOR", "on")
    assert monitor_enabled() is True


# ===========================================================================
# 6. Endpoint shape -- GET /api/v1/admin/online-store/parity (extended)
# ===========================================================================

_PARITY_EP = "/api/v1/admin/online-store/parity"


def _token(roles):
    from api.routers.auth import create_access_token

    return {
        "Authorization": "Bearer "
        + create_access_token(
            {
                "user_id": f"parmon-{'-'.join(roles).lower()}",
                "username": "parmon-tester",
                "roles": roles,
                "store_ids": ["BV-TEST-01"],
                "active_store_id": "BV-TEST-01",
            }
        )
    }


def test_parity_endpoint_includes_bvi_nightly_block(client):
    r = client.get(_PARITY_EP, headers=_token(["SUPERADMIN"]))
    assert r.status_code == 200, r.text
    body = r.json()
    # pre-existing blocks unchanged
    assert "parity" in body
    assert "uploads_audit" in body
    # new nightly-monitor block
    assert "bvi_nightly" in body
    nightly = body["bvi_nightly"]
    assert set(nightly.keys()) == {"monitor_enabled", "latest"}
    assert isinstance(nightly["monitor_enabled"], bool)
    # no nightly run has stored a snapshot in this test env
    assert nightly["latest"] is None or isinstance(nightly["latest"], dict)


def test_parity_endpoint_still_superadmin_only(client):
    r = client.get(_PARITY_EP, headers=_token(["ADMIN"]))
    assert r.status_code == 403
