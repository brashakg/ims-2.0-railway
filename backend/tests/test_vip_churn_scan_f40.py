"""F40 ORACLE _scan_vip_churn orchestration (#40) -- T4/T5/T11/T12.

Stubs the orders.aggregate result (the pipeline correctness is standard Mongo,
validated against the live DB by the test session) and asserts the orchestration:
each VIP gets a vip_churn_risk subdoc, exactly one snapshot per store, HIGH-label
anomalies are returned for emit, and it is fail-soft when the DB is absent. No emoji.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

import pytest  # noqa: E402

NOW = datetime(2026, 6, 8, 22, 0, 0)


def _gaps_to_dates(last_ago, gaps):
    last = NOW - timedelta(days=last_ago)
    out, cur = [last], last
    for g in gaps:
        cur = cur - timedelta(days=g)
        out.append(cur)
    return out


class _Coll:
    def __init__(self, agg=None, docs=None):
        self._agg = agg or []
        self.docs = docs or {}
        self.updates = []   # (filter, update, upsert)

    def aggregate(self, pipeline):
        return list(self._agg)

    def find_one(self, filt, projection=None):
        return self.docs.get(filt.get("customer_id"))

    def update_one(self, filt, update, upsert=False):
        self.updates.append((filt, update, upsert))

        class _R:
            matched_count = 1
        return _R()


class _FakeDB:
    is_connected = True

    def __init__(self, colls):
        self._colls = colls

    def get_collection(self, name):
        return self._colls.get(name)


def _agent(fake_db, monkeypatch):
    import agents.implementations.oracle as oracle_mod
    monkeypatch.setattr(oracle_mod, "is_claude_available", lambda: False)  # skip Claude
    return oracle_mod.OracleAgent(db=fake_db)


def test_t4_t5_t11_scan_writes_subdocs_one_snapshot_returns_high(monkeypatch):
    # 3 VIPs (pipeline already filtered to LTV>=100k & count>=3), all in store S1:
    agg = [
        {"_id": "C1", "ltv": 200000, "count": 4, "dates": _gaps_to_dates(125, [30, 28, 32])},   # HIGH
        {"_id": "C2", "ltv": 150000, "count": 4, "dates": _gaps_to_dates(340, [300, 310, 330])}, # WATCH
        {"_id": "C3", "ltv": 150000, "count": 3, "dates": _gaps_to_dates(40, [60, 60])},          # NONE (not overdue)
    ]
    customers = _Coll(docs={
        "C1": {"name": "Asha", "primary_store_id": "S1"},
        "C2": {"name": "Ben", "primary_store_id": "S1"},
        "C3": {"name": "Cara", "primary_store_id": "S1"},
    })
    orders = _Coll(agg=agg)
    snaps = _Coll()
    fake_db = _FakeDB({"customers": customers, "orders": orders, "vip_churn_snapshots": snaps})
    agent = _agent(fake_db, monkeypatch)

    high = asyncio.run(agent._scan_vip_churn(NOW))

    # T5: each VIP got a vip_churn_risk subdoc with the right label.
    written = {f["customer_id"]: u["$set"]["vip_churn_risk"]
               for (f, u, _) in customers.updates if "vip_churn_risk" in u.get("$set", {})}
    assert written["C1"]["risk_label"] == "HIGH"
    assert written["C2"]["risk_label"] == "WATCH"
    assert written["C3"]["risk_label"] == "NONE"
    assert written["C1"]["usual_interval_days"] == 30

    # T4: exactly ONE snapshot per store, with correct counts.
    snap_upserts = [u for (f, u, up) in snaps.updates if up]
    assert len(snap_upserts) == 1
    snap = snap_upserts[0]["$set"]
    assert snap["store_id"] == "S1" and snap["vip_count"] == 3
    assert snap["watch_count"] == 1 and snap["high_risk_count"] == 1

    # HIGH-label anomaly returned for emit (C1 only).
    assert len(high) == 1 and high[0]["customer_id"] == "C1"
    assert high[0]["severity"] == "HIGH" and high[0]["kind"] == "vip_churn"


def test_t12_fail_soft_when_db_absent(monkeypatch):
    agent = _agent(_FakeDB({"customers": None, "orders": None}), monkeypatch)
    assert asyncio.run(agent._scan_vip_churn(NOW)) == []
