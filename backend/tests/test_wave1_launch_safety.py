"""
Wave-1 launch-safety tests:
  - SeededDatabaseConnection: in PRODUCTION a real-Mongo outage fails LOUD
    (is_connected False, db/get_collection None) instead of silently serving
    seed/mock data. Off production, the mock fallback is preserved.
  - online_stock_miss_summary: surfaces unresolved online oversell records.

In-memory only -- no real DB.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import connection as conn_mod  # noqa: E402
from api.services import online_sync_health as sh  # noqa: E402


class _FakeReal:
    def __init__(self, connected: bool):
        self.is_connected = connected

    @property
    def db(self):
        return {"__real__": True}

    def get_collection(self, name: str):
        return {"__real_coll__": name}


def _conn(monkeypatch, *, real_connected: bool, prod: bool):
    monkeypatch.setattr(conn_mod, "_is_production", lambda: prod)
    c = conn_mod.SeededDatabaseConnection()  # singleton
    c._real_db = _FakeReal(real_connected)
    return c


# --- _is_production detector ------------------------------------------------


def test_is_production_detects_railway(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "dep_123")
    assert conn_mod._is_production() is True


def test_is_production_detects_environment(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("RAILWAY_DEPLOYMENT_ID", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert conn_mod._is_production() is True


def test_is_production_false_local(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("RAILWAY_DEPLOYMENT_ID", raising=False)
    assert conn_mod._is_production() is False


# --- SeededDatabaseConnection gating ---------------------------------------


def test_real_connected_serves_real_regardless_of_env(monkeypatch):
    c = _conn(monkeypatch, real_connected=True, prod=True)
    assert c.is_connected is True
    assert c.db == {"__real__": True}
    assert c.get_collection("orders") == {"__real_coll__": "orders"}


def test_prod_outage_fails_loud_no_mock(monkeypatch):
    """PROD + real DB down -> is_connected False, db/get_collection None (no mock)."""
    c = _conn(monkeypatch, real_connected=False, prod=True)
    assert c.is_connected is False
    assert c.db is None
    assert c.get_collection("orders") is None


def test_offprod_outage_falls_back_to_mock(monkeypatch):
    """Local/test + real DB down -> mock fallback preserved (is_connected True,
    db + collection are real objects, not None)."""
    c = _conn(monkeypatch, real_connected=False, prod=False)
    assert c.is_connected is True
    assert c.db is not None
    assert c.get_collection("orders") is not None


# --- online_stock_miss_summary ---------------------------------------------


class _MissColl:
    def __init__(self, rows):
        self._rows = rows

    def _match(self, q):
        out = []
        for r in self._rows:
            ok = True
            for k, cond in q.items():
                v = r.get(k)
                if isinstance(cond, dict) and "$ne" in cond:
                    if v == cond["$ne"]:
                        ok = False
                elif not isinstance(cond, dict) and v != cond:
                    ok = False
            if ok:
                out.append(r)
        return out

    def count_documents(self, q):
        return len(self._match(q))

    def find(self, q, _proj=None):
        rows = self._match(q)

        class _Cur:
            def __init__(self, rs):
                self._rs = rs

            def limit(self, n):
                return self._rs[:n]

        return _Cur(rows)


class _MissDb:
    def __init__(self, rows):
        self._c = _MissColl(rows)

    def __getitem__(self, name):
        return self._c if name == "online_stock_miss" else _MissColl([])


def test_stock_miss_summary_counts_unresolved():
    db = _MissDb(
        [
            {"order_id": "1", "resolved": False, "reason": "under_claim"},
            {"order_id": "2", "reason": "exception"},  # no resolved key -> unresolved
            {"order_id": "3", "resolved": True, "reason": "under_claim"},
        ]
    )
    out = sh.online_stock_miss_summary(db)
    assert out["checked"] is True
    assert out["total"] == 3
    assert out["unresolved"] == 2
    assert len(out["recent"]) == 2


def test_stock_miss_summary_none_db():
    out = sh.online_stock_miss_summary(None)
    assert out["checked"] is False
    assert out["unresolved"] == 0
