"""
IMS 2.0 — SENTINEL agent tests
================================
Locks the operational contract for SENTINEL:

  1. Health score weighting is correct (DB 30%, API 20%, Frontend 15%,
     Agents 20%, Integrity 15%) with the unhealthy/degraded/unknown
     ladder applied per component.
  2. The /jarvis/agents/sentinel/health endpoint returns a stable shape
     even with no data (fail-soft empty envelope).
  3. The endpoint requires SUPERADMIN.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ----- health score weighting --------------------------------------------

class TestHealthScore:
    def _make_agent(self):
        """Build a SentinelAgent without invoking __init__'s db setup."""
        from agents.implementations.sentinel import SentinelAgent
        return SentinelAgent.__new__(SentinelAgent)

    def test_all_healthy_is_100(self):
        a = self._make_agent()
        score = a._compute_health_score({
            "database": {"status": "healthy"},
            "api": {"status": "healthy"},
            "frontend": {"status": "healthy"},
            "agents": {"status": "healthy", "total": 7, "healthy": 7, "unhealthy": 0},
            "data_integrity": {"status": "healthy", "issues": []},
        })
        assert score == 100

    def test_db_unhealthy_deducts_full_weight(self):
        a = self._make_agent()
        score = a._compute_health_score({
            "database": {"status": "unhealthy"},
            "api": {"status": "healthy"},
            "frontend": {"status": "healthy"},
            "agents": {"status": "healthy", "total": 7, "healthy": 7, "unhealthy": 0},
            "data_integrity": {"status": "healthy", "issues": []},
        })
        # DB weight is 30; everything else healthy
        assert score == 70

    def test_api_degraded_deducts_half(self):
        a = self._make_agent()
        score = a._compute_health_score({
            "database": {"status": "healthy"},
            "api": {"status": "degraded"},  # half of 20 = 10
            "frontend": {"status": "healthy"},
            "agents": {"status": "healthy", "total": 5, "healthy": 5, "unhealthy": 0},
            "data_integrity": {"status": "healthy", "issues": []},
        })
        assert score == 90

    def test_frontend_unknown_deducts_quarter(self):
        a = self._make_agent()
        score = a._compute_health_score({
            "database": {"status": "healthy"},
            "api": {"status": "healthy"},
            "frontend": {"status": "unknown"},  # quarter of 15 = 3
            "agents": {"status": "healthy", "total": 5, "healthy": 5, "unhealthy": 0},
            "data_integrity": {"status": "healthy", "issues": []},
        })
        assert score == 97

    def test_agents_ratio_deduction(self):
        a = self._make_agent()
        score = a._compute_health_score({
            "database": {"status": "healthy"},
            "api": {"status": "healthy"},
            "frontend": {"status": "healthy"},
            # 2/4 unhealthy → 50% of agent weight (20) = 10
            "agents": {"status": "degraded", "total": 4, "healthy": 2, "unhealthy": 2},
            "data_integrity": {"status": "healthy", "issues": []},
        })
        assert score == 90

    def test_integrity_issue_capped_at_weight(self):
        a = self._make_agent()
        # 10 issues × 5 = 50, capped at 15
        score = a._compute_health_score({
            "database": {"status": "healthy"},
            "api": {"status": "healthy"},
            "frontend": {"status": "healthy"},
            "agents": {"status": "healthy", "total": 5, "healthy": 5, "unhealthy": 0},
            "data_integrity": {
                "status": "degraded",
                "issues": [{"type": "ORPHAN", "severity": "MEDIUM"}] * 10,
            },
        })
        assert score == 85

    def test_score_never_below_zero(self):
        a = self._make_agent()
        score = a._compute_health_score({
            "database": {"status": "unhealthy"},     # -30
            "api": {"status": "unhealthy"},          # -20
            "frontend": {"status": "unhealthy"},     # -15
            "agents": {"status": "unhealthy", "total": 5, "healthy": 0, "unhealthy": 5},  # -20
            "data_integrity": {"status": "unhealthy", "issues": [{}] * 5},  # capped -15
        })
        assert score == 0  # not negative

    def test_missing_components_default_unknown(self):
        a = self._make_agent()
        score = a._compute_health_score({})
        # 4 components unknown (quarter weight each):
        # DB(30/4=7) + API(20/4=5) + Frontend(15/4=3) + Agents(0, total=0 → no deduction)
        # + Integrity(no issues → 0)
        # = 100 - 7 - 5 - 3 = 85
        assert 80 <= score <= 90


# ----- endpoint shape ----------------------------------------------------

class TestSentinelEndpoint:
    def test_requires_auth(self, client):
        r = client.get("/api/v1/jarvis/agents/sentinel/health")
        assert r.status_code in (401, 404)  # 404 if path under SUPERADMIN guard

    def test_no_db_empty_envelope(self, client, auth_headers):
        r = client.get("/api/v1/jarvis/agents/sentinel/health", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert "latest" in body
        assert "history" in body
        assert "alerts" in body
        assert "history_count" in body
        assert "as_of" in body
        assert isinstance(body["history"], list)
        assert isinstance(body["alerts"], list)
        # Empty DB → no data
        assert body["latest"] is None
        assert body["history_count"] == 0

    def test_history_limit_bounds(self, client, auth_headers):
        # 1000 should be clamped to 240, no 422
        r = client.get(
            "/api/v1/jarvis/agents/sentinel/health?history_limit=1000",
            headers=auth_headers,
        )
        assert r.status_code == 200

    @pytest.mark.parametrize("broken", ["health_checks", "alert_history"])
    def test_unreadable_store_is_503_not_empty(self, client, auth_headers, monkeypatch, broken):
        """A failed read must not answer 200 'no checks / no alerts' (a false all-clear)."""
        import mongomock
        from api.routers import jarvis
        from pymongo.errors import AutoReconnect

        db = mongomock.MongoClient()["sentinel_read"]
        good = {"health_checks": db["health_checks"], "alert_history": db["alert_history"]}

        class Broken:
            def find(self, *a, **k):
                raise AutoReconnect("injected read failure")

        monkeypatch.setattr(jarvis, "get_db_collection", lambda n: Broken() if n == broken else good.get(n))
        r = client.get("/api/v1/jarvis/agents/sentinel/health", headers=auth_headers)
        assert r.status_code == 503

    def test_alerts_limit_bounds(self, client, auth_headers):
        r = client.get(
            "/api/v1/jarvis/agents/sentinel/health?alerts_limit=500",
            headers=auth_headers,
        )
        assert r.status_code == 200


class TestPymongoTruthTestBugFixed:
    """REGRESSION: pymongo Collection / Database objects raise on `bool()`
    (NotImplementedError "Collection objects do not implement truth value
    testing"). The agent code originally used `if col:` and `if self.db:`
    everywhere — every one of those silently raised, the surrounding
    try/except swallowed it, and writes to `health_checks`, `alert_history`,
    `agent_audit_log` etc. were skipped.

    Symptom in prod: SENTINEL ran successfully but `health_checks` stayed
    empty; PIXEL's run_count never incremented past 0.

    Fix: every check uses explicit `is None` / `is not None`. This test
    scans the agent tree to make sure nobody re-introduces the pattern.
    """

    def test_no_implicit_truth_test_on_db_or_col_in_agents(self):
        """Widened in v2 (May 2026) — earlier version only caught bare
        `col` and `coll`. It missed `users_col`, `orders_col`, `cust_col`
        etc. that the SENTINEL `_check_db_health` actually used. That left
        the SENTINEL "database unhealthy" prod bug live for several days
        before the user reported it on the UI card. This wider regex
        catches ANY variable name ending in `_col`, `_coll`, `_collection`
        or starting with `coll`, plus `self.db` / `self._db`."""
        import os
        import re

        agents_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "agents",
        )
        offenders = []
        # Match: identifier ending in _col / _coll / _collection, OR bare
        # col / coll, OR self.db / self._db. Also catches `if not <var>:`.
        bad = re.compile(
            r"if\s+(not\s+)?"
            r"(\w*_col(?:l(?:ection)?)?|col|coll|self\.db|self\._db)"
            r"\s*(:|and\b|or\b|\))",
        )
        for root, _, files in os.walk(agents_dir):
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(root, f)
                with open(path, encoding="utf-8") as fh:
                    for lineno, line in enumerate(fh, 1):
                        # Skip explicit "is None" / "is not None" — those are fine
                        if "is None" in line or "is not None" in line:
                            continue
                        if bad.search(line):
                            offenders.append(f"{path}:{lineno}: {line.strip()}")
        assert not offenders, (
            "Implicit truth-test on Mongo Collection/Database — pymongo raises "
            "on bool(). Use `is None` / `is not None` instead.\n"
            + "\n".join(offenders)
        )

    def test_widened_regex_catches_prefixed_names(self):
        """Standalone sanity check for the v2 regex — confirms it catches
        prefixed names like `users_col` that the v1 regex missed."""
        import re
        bad = re.compile(
            r"if\s+(not\s+)?"
            r"(\w*_col(?:l(?:ection)?)?|col|coll|self\.db|self\._db)"
            r"\s*(:|and\b|or\b|\))",
        )
        # All these MUST match (they're all the bug pattern):
        for line in [
            "if col:",
            "if not col:",
            "if coll:",
            "if users_col:",
            "if orders_col:",
            "if cust_col:",
            "if my_collection:",
            "if not user_collection:",
            "if self.db:",
            "if self._db:",
            "if orders_col and customers_col:",
            "if not cust_col and users_col:",
            "if users_col or orders_col:",
        ]:
            assert bad.search(line), f"v2 regex should match: {line!r}"
        # And these must NOT (correct patterns):
        for line in [
            "if col is None:",
            "if col is not None:",
            "if users_col is not None:",
            "if self.db is not None:",
            "if isinstance(col, Collection):",
        ]:
            # The regex MAY still match these on the `if X:` prefix, but
            # the "is None" / "is not None" guard in the main test skips
            # them. Just confirm the test's guard works:
            assert ("is None" in line or "is not None" in line or
                    not bad.search(line)), f"v2 false-positive on: {line!r}"


# ----- INFRA-05: incident lifecycle, no duplicate alerts per tick ----------

class TestDegradedIncidentLifecycle:
    """Identical degraded ticks must not insert one alert per tick."""

    def _agent(self, db):
        from unittest.mock import AsyncMock
        from agents.implementations.sentinel import SentinelAgent
        a = SentinelAgent(db=db)
        a._run_count = 1  # skip the every-10th integrity probe
        self.state = {
            "database": {"status": "unhealthy"},
            "api": {"status": "unhealthy"},
        }
        healthy = {"status": "healthy"}
        a._check_db_health = AsyncMock(side_effect=lambda: self.state["database"])
        a._check_api_health = AsyncMock(side_effect=lambda: self.state["api"])
        a._check_frontend_health = AsyncMock(return_value=healthy)
        a._check_agent_health = AsyncMock(return_value={"status": "healthy", "total": 1, "unhealthy": 0})
        a._maybe_daily_stock_parity = AsyncMock()
        self.emitted = []

        async def emit(event, payload):
            self.emitted.append(event)
        a.emit_event = emit
        return a

    @pytest.mark.asyncio
    async def test_open_change_reminder_recover(self):
        import mongomock
        from datetime import timedelta
        db = mongomock.MongoClient()["sentinel_infra05"]
        a = self._agent(db)
        alerts = db["alert_history"]


        await a._do_background_work()
        await a._do_background_work()
        assert alerts.count_documents({"domain": "system"}) == 1, "identical tick duplicated the alert"
        assert db["health_checks"].count_documents({}) == 2  # every tick still snapshotted
        assert self.emitted == ["system.degraded"]
        first = alerts.find_one({"domain": "system"})
        assert first["details"]["incident"] == "opened"

        # Meaningful change (failing set) -> a new alert.
        self.state["api"] = {"status": "healthy"}
        self.state["database"] = {"status": "unhealthy"}
        a._check_frontend_health.return_value = {"status": "unhealthy"}
        await a._do_background_work()
        await a._do_background_work()
        assert alerts.count_documents({"domain": "system"}) == 2
        latest = alerts.find_one({"domain": "system"}, sort=[("timestamp", -1), ("_id", -1)])
        assert latest["details"]["incident"] == "changed"

        # Unchanged for over an hour -> exactly one reminder.
        for row in list(alerts.find({"domain": "system"})):
            alerts.update_one({"_id": row["_id"]}, {"$set": {"timestamp": row["timestamp"] - timedelta(hours=2)}})
        await a._do_background_work()
        await a._do_background_work()
        assert alerts.count_documents({"domain": "system"}) == 3
        latest = alerts.find_one({"domain": "system"}, sort=[("timestamp", -1), ("_id", -1)])
        assert latest["details"]["incident"] == "reminder"

        # Recovery: stamped on the incident row, no new alert, history kept.
        self.state["database"] = {"status": "healthy"}
        a._check_frontend_health.return_value = {"status": "healthy"}
        await a._do_background_work()
        await a._do_background_work()
        assert alerts.count_documents({"domain": "system"}) == 3
        assert alerts.count_documents({"domain": "system", "recovered_at": {"$exists": True}}) == 1
        assert alerts.find_one({"_id": latest["_id"]})["recovered_score"] == a._health_score
        assert alerts.count_documents({"acknowledged": False}) == 3  # not auto-acknowledged

        # A new drop after recovery opens a new incident.
        self.state["database"] = {"status": "unhealthy"}
        self.state["api"] = {"status": "unhealthy"}
        await a._do_background_work()
        assert alerts.count_documents({"domain": "system"}) == 4
        latest = alerts.find_one({"domain": "system"}, sort=[("timestamp", -1), ("_id", -1)])
        assert latest["details"]["incident"] == "opened"

    @pytest.mark.asyncio
    async def test_unreadable_store_falls_back_to_memory(self):
        """No DB at all: the in-memory tail still suppresses repeats."""
        a = self._agent(None)
        a.get_collection = lambda name: None
        await a._do_background_work()
        await a._do_background_work()
        assert len([x for x in a._alerts if x["domain"] == "system"]) == 1


# ----- INFRA-05 round 2 (Codex #1432): alert store faults -------------------
# Codex reproduced on native Mongo: (a) a failed alert_history read after a
# recovery fell back to a stale memory row and hid a fresh outage, (b) a failed
# recovery write did the same, (c) a failed insert re-emitted system.degraded on
# every tick and the next healthy insert opened yet another incident. Each case
# runs on mongomock AND on a real Mongo (CI's mongo:7.0 service via MONGODB_URL,
# or a local loopback MONGO_HOST/MONGO_PORT/MONGO_DATABASE). Every collection
# the agent touches is redirected to a throw-away prefixed name that is dropped
# afterwards, so no existing row of a real database is read or written.


def _native_db():
    import uuid
    from pymongo import MongoClient
    url = os.getenv("MONGODB_URL")
    name = f"sentinel_infra05_{uuid.uuid4().hex[:8]}"
    if not url and os.getenv("MONGO_HOST") and os.getenv("MONGO_DATABASE"):
        if os.environ["MONGO_HOST"] not in ("127.0.0.1", "localhost"):
            pytest.skip("native sentinel tests run on loopback Mongo only")
        url = f"mongodb://{os.environ['MONGO_HOST']}:{os.getenv('MONGO_PORT', '27017')}"
        name = os.environ["MONGO_DATABASE"]
    if not url:
        pytest.skip("no real Mongo configured (CI sets MONGODB_URL)")
    client = MongoClient(url, serverSelectionTimeoutMS=1500)
    try:
        client.admin.command("ping")
    except Exception:  # noqa: BLE001
        pytest.skip("real Mongo not reachable")
    return client[name]


class _Flaky:
    """Collection proxy: methods named in `fail` raise AutoReconnect; with
    `after` the write lands first and only the acknowledgement is lost."""

    def __init__(self, col):
        self._col = col
        self.fail = set()
        self.after = False

    def __getattr__(self, name):
        real = getattr(self._col, name)
        if name not in self.fail:
            return real
        from pymongo.errors import AutoReconnect

        def boom(*a, **k):
            if self.after:
                real(*a, **k)
            raise AutoReconnect(f"injected {name}")
        return boom


@pytest.fixture(params=["mongomock", "native"])
def alert_store(request):
    import uuid
    if request.param == "mongomock":
        import mongomock
        db = mongomock.MongoClient()["sentinel_infra05_mock"]
    else:
        db = _native_db()
    prefix = f"t1432_{uuid.uuid4().hex[:8]}_"
    flaky = _Flaky(db[prefix + "alert_history"])
    yield db, prefix, flaky
    for name in db.list_collection_names():
        if name.startswith(prefix):
            db.drop_collection(name)


class TestAlertStoreFaults:
    def _agent(self, store):
        from unittest.mock import AsyncMock
        from agents.implementations.sentinel import SentinelAgent
        db, prefix, flaky = store
        a = SentinelAgent(db=db)
        a.get_collection = lambda n: flaky if n == "alert_history" else db[prefix + n]
        a._run_count = 1
        a._check_frontend_health = AsyncMock(return_value={"status": "healthy"})
        a._check_agent_health = AsyncMock(return_value={"status": "healthy", "total": 1, "unhealthy": 0})
        a._maybe_daily_stock_parity = AsyncMock()
        a.state = "unhealthy"
        a._check_db_health = AsyncMock(side_effect=lambda: {"status": a.state})
        a._check_api_health = AsyncMock(side_effect=lambda: {"status": a.state})
        a.events = []

        async def emit(event, payload):
            a.events.append(payload["incident"])
        a.emit_event = emit
        return a

    async def _tick(self, a, healthy=False):
        a.state = "healthy" if healthy else "unhealthy"
        await a._do_background_work()

    @staticmethod
    def _rows(store):
        return list(store[2]._col.find({"domain": "system"}).sort([("timestamp", 1), ("_id", 1)]))

    async def test_read_failure_after_recovery_still_alerts_new_outage(self, alert_store):
        a = self._agent(alert_store)
        await self._tick(a)
        await self._tick(a, healthy=True)
        alert_store[2].fail = {"find_one", "find"}
        await self._tick(a)
        rows = self._rows(alert_store)
        assert a.events == ["opened", "opened"], "fresh outage after recovery was suppressed"
        assert len(rows) == 2 and rows[0].get("recovered_at") and not rows[1].get("recovered_at")

    async def test_failed_recovery_write_still_alerts_and_is_retried(self, alert_store):
        a = self._agent(alert_store)
        await self._tick(a)
        alert_store[2].fail = {"update_one"}
        await self._tick(a, healthy=True)
        alert_store[2].fail = set()
        await self._tick(a)
        rows = self._rows(alert_store)
        assert a.events == ["opened", "opened"], "fresh outage after unsaved recovery was suppressed"
        assert len(rows) == 2
        assert rows[0].get("recovered_at") and rows[0]["recovered_score"] == 100, "recovery never persisted"
        assert not rows[1].get("recovered_at")

    async def test_insert_failure_one_event_and_persisted_once_later(self, alert_store):
        a = self._agent(alert_store)
        alert_store[2].fail = {"insert_one"}
        await self._tick(a)
        await self._tick(a)
        assert a.events == ["opened"], "identical ticks re-emitted while the insert failed"
        assert self._rows(alert_store) == []
        alert_store[2].fail = set()
        await self._tick(a)
        rows = self._rows(alert_store)
        assert a.events == ["opened"]
        assert len(rows) == 1 and rows[0]["details"]["incident"] == "opened"

    async def test_lost_insert_acknowledgement_is_not_duplicated(self, alert_store):
        a = self._agent(alert_store)
        alert_store[2].fail, alert_store[2].after = {"insert_one"}, True
        await self._tick(a)
        alert_store[2].fail, alert_store[2].after = set(), False
        await self._tick(a)
        await self._tick(a)
        assert a.events == ["opened"]
        assert len(self._rows(alert_store)) == 1
        assert not getattr(a, "_pending_alert_writes", [])

    async def test_recovery_while_insert_pending_persists_both_in_order(self, alert_store):
        a = self._agent(alert_store)
        alert_store[2].fail = {"insert_one", "update_one"}
        await self._tick(a)
        await self._tick(a, healthy=True)
        alert_store[2].fail = set()
        await self._tick(a, healthy=True)
        rows = self._rows(alert_store)
        assert a.events == ["opened"]
        assert len(rows) == 1 and rows[0].get("recovered_at")

    async def test_other_alerts_are_persisted_after_insert_recovers(self, alert_store):
        a = self._agent(alert_store)
        alert_store[2].fail = {"insert_one"}
        await a.on_event("agent.error", {"agent_id": "pixel", "error": "boom"})
        alert_store[2].fail = set()
        await self._tick(a, healthy=True)
        assert alert_store[2]._col.count_documents({"domain": "agent"}) == 1

    async def test_restart_reads_open_incident_and_stays_quiet(self, alert_store):
        a = self._agent(alert_store)
        await self._tick(a)
        b = self._agent(alert_store)
        await self._tick(b)
        assert b.events == [], "restart re-alerted an unchanged open incident"
        assert len(self._rows(alert_store)) == 1

    async def test_restart_with_unreadable_store_alerts_rather_than_hides(self, alert_store):
        a = self._agent(alert_store)
        await self._tick(a)
        b = self._agent(alert_store)
        alert_store[2].fail = {"find_one", "find"}
        await self._tick(b)
        assert b.events == ["opened"]
