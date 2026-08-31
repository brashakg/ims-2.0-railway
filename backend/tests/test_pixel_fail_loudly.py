"""
PIXEL must never tick in silence.
=================================

The bug these guard (verified against prod 2026-08-31):

    ui_audits total docs: 0   (kind=heartbeat: 0, kind=scheduled_audit: 0)

PIXEL was enabled, cron "0 2 * * *", and had written ZERO rows in its
entire life. The PageSpeed key in the Railway env was 57 characters and
Google answered ``400 "API key not valid"`` to all 9 routes. The old code
wrote its heartbeat row only when the key was MISSING -- a REJECTED key
counted as "available", so PIXEL took the real-audit path, hit
``All PageSpeed calls failed - no audit recorded`` and returned without
writing anything at all. Not even evidence of life.

Every test here asserts on WHAT LANDED IN ``ui_audits``, because that is
the only thing an operator can read the morning after. A test that only
asserted "the tick did not raise" would have passed against the bug --
the buggy tick did not raise either.

DISCRIMINATING POWER (measured by reverting the fix, see the PR body):
  * drop the ``if not page_results`` recording branch back to a bare
    ``return``  -> test_rejected_key_records_credentials_rejected,
                   test_transport_failure_records_all_calls_failed and
                   test_degraded_health_names_the_rejected_key all FAIL.
  * restore ``_is_pagespeed_available()`` gating the heartbeat write
                -> test_missing_key_records_no_credentials FAILS
                   (row kind/outcome are the old shape).
  * drop the ``_scrub`` calls
                -> test_api_key_never_reaches_mongo FAILS.

No secret values anywhere: the fake key below is an invented marker
string, only ever compared, never logged.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.implementations import pixel as pixel_mod  # noqa: E402
from tests.strict_fakes import StrictDB  # noqa: E402


# An invented non-secret marker shaped like the real prod failure: present,
# 57 chars, NOT a Google key. Never a real credential.
BAD_KEY = "MARKER-PAGESPEED-KEY-THAT-GOOGLE-REJECTS-0000000000000000"
GOOD_SHAPED_KEY = "AIza" + "M" * 35  # 39 chars, right shape, still fake

assert len(BAD_KEY) == 57
assert len(GOOD_SHAPED_KEY) == 39


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class _Resp:
    """Minimal httpx.Response stand-in: only what _audit_url actually reads."""

    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


GOOGLE_400 = {
    "error": {
        "code": 400,
        "message": "API key not valid. Please pass a valid API key.",
        "status": "INVALID_ARGUMENT",
    }
}


def _lighthouse_ok():
    return {
        "lighthouseResult": {
            "categories": {
                "performance": {"score": 0.71},
                "accessibility": {"score": 0.93, "auditRefs": [
                    {"id": "color-contrast", "weight": 7},
                ]},
                "best-practices": {"score": 1.0},
                "seo": {"score": 0.9},
            },
            "audits": {
                "color-contrast": {"score": 0, "title": "Contrast"},
                "largest-contentful-paint": {"numericValue": 2400},
                "cumulative-layout-shift": {"numericValue": 0.02},
                "total-blocking-time": {"numericValue": 120},
                "first-contentful-paint": {"numericValue": 900},
                "speed-index": {"numericValue": 1800},
            },
            "timing": {"total": 4321},
        }
    }


class _FakeClient:
    """Async context manager standing in for httpx.AsyncClient.

    ``responder`` is called with the request params so a test can assert the
    key really rode along, and it may RAISE to simulate a transport failure.
    """

    def __init__(self, responder):
        self._responder = responder
        self.calls = []

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, **kw):
        self.calls.append(dict(params or []))
        return self._responder(dict(params or []))


@pytest.fixture
def db():
    return StrictDB()


@pytest.fixture
def agent(db):
    return pixel_mod.PixelAgent(db=db)


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch):
    """PIXEL resolves the key through integration_config; stub that one door
    so no test can accidentally read a real environment/DB credential."""
    monkeypatch.delenv("PAGESPEED_API_KEY", raising=False)


def _use_key(monkeypatch, key: str):
    monkeypatch.setattr(pixel_mod, "_pagespeed_key", lambda: key)


def _use_http(monkeypatch, responder):
    client = _FakeClient(responder)
    monkeypatch.setattr(pixel_mod.httpx, "AsyncClient", client)
    return client


def _rows(db):
    return db.get_collection("ui_audits").docs


# ---------------------------------------------------------------------------
# 1. Missing key -> no_credentials  (and a row EXISTS)
# ---------------------------------------------------------------------------


def test_missing_key_records_no_credentials(agent, db, monkeypatch):
    _use_key(monkeypatch, "")

    asyncio.run(agent._do_background_work())

    rows = _rows(db)
    assert len(rows) == 1, "a tick with no key must still leave evidence of life"
    assert rows[0]["outcome"] == "no_credentials"
    assert rows[0]["kind"] == "run_failed"
    assert rows[0]["agent_id"] == "pixel"
    # The owner must be told what to DO, not just that it broke.
    assert "Settings -> Integrations" in rows[0]["next_step"]


def test_missing_key_makes_no_network_call(agent, monkeypatch):
    _use_key(monkeypatch, "")
    client = _use_http(monkeypatch, lambda p: _Resp(200, _lighthouse_ok()))

    asyncio.run(agent._do_background_work())

    assert client.calls == [], "no key means no outbound PageSpeed call"


# ---------------------------------------------------------------------------
# 2. Rejected key -> credentials_rejected  (THE prod bug)
# ---------------------------------------------------------------------------


def test_rejected_key_records_credentials_rejected(agent, db, monkeypatch):
    """A 57-char garbage key: every route 400s. Old code wrote NOTHING."""
    _use_key(monkeypatch, BAD_KEY)
    client = _use_http(monkeypatch, lambda p: _Resp(400, GOOGLE_400))

    asyncio.run(agent._do_background_work())

    assert len(client.calls) == len(pixel_mod.AUDIT_ROUTES), "all routes attempted"

    rows = _rows(db)
    assert len(rows) == 1, (
        "a rejected key produced silence before this fix -- the whole point"
    )
    row = rows[0]
    assert row["outcome"] == "credentials_rejected"
    assert row["kind"] == "run_failed"
    # Enough detail to diagnose from Mongo alone.
    assert len(row["routes_attempted"]) == len(pixel_mod.AUDIT_ROUTES)
    assert len(row["failures"]) == len(pixel_mod.AUDIT_ROUTES)
    assert row["failures"][0]["status"] == 400
    assert "API key not valid" in row["failures"][0]["error"]
    # The 57-char paste is flagged as not-Google-shaped.
    assert row["key_shape_ok"] is False
    assert "console.cloud.google.com" in row["next_step"]


def test_403_also_reads_as_credentials_rejected(agent, db, monkeypatch):
    """A key that exists but has the PageSpeed API disabled answers 403."""
    _use_key(monkeypatch, GOOD_SHAPED_KEY)
    _use_http(monkeypatch, lambda p: _Resp(403, {"error": {"message": "API disabled"}}))

    asyncio.run(agent._do_background_work())

    row = _rows(db)[0]
    assert row["outcome"] == "credentials_rejected"
    # Right shape, still rejected -- shape is advisory, the HTTP verdict rules.
    assert row["key_shape_ok"] is True


# ---------------------------------------------------------------------------
# 3. Transport failure -> all_calls_failed  (NOT a credential problem)
# ---------------------------------------------------------------------------


def test_transport_failure_records_all_calls_failed(agent, db, monkeypatch):
    def _boom(params):
        raise pixel_mod.httpx.ConnectError("connection refused")

    _use_key(monkeypatch, GOOD_SHAPED_KEY)
    _use_http(monkeypatch, _boom)

    asyncio.run(agent._do_background_work())

    row = _rows(db)[0]
    assert row["outcome"] == "all_calls_failed", (
        "a dead network must not be blamed on the key"
    )
    assert row["failures"][0]["status"] is None
    assert "ConnectError" in row["failures"][0]["error"]


def test_timeout_records_all_calls_failed(agent, db, monkeypatch):
    def _slow(params):
        raise pixel_mod.httpx.ReadTimeout("timed out")

    _use_key(monkeypatch, GOOD_SHAPED_KEY)
    _use_http(monkeypatch, _slow)

    asyncio.run(agent._do_background_work())

    row = _rows(db)[0]
    assert row["outcome"] == "all_calls_failed"
    assert row["failures"][0]["error"] == "timeout"


# ---------------------------------------------------------------------------
# 4. Working key -> a real audit still lands
# ---------------------------------------------------------------------------


def test_successful_run_records_the_audit(agent, db, monkeypatch):
    _use_key(monkeypatch, GOOD_SHAPED_KEY)
    _use_http(monkeypatch, lambda p: _Resp(200, _lighthouse_ok()))

    asyncio.run(agent._do_background_work())

    rows = _rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row["outcome"] == "ok"
    # kind stays "scheduled_audit" -- the Jarvis history reader filters on it.
    assert row["kind"] == "scheduled_audit"
    assert row["summary"]["pages_audited"] == len(pixel_mod.AUDIT_ROUTES)
    assert row["summary"]["overall_min_perf"] == 0.71
    assert row["summary"]["overall_min_a11y"] == 0.93
    assert len(row["pages"]) == len(pixel_mod.AUDIT_ROUTES)
    assert row["failures"] == []


def test_partial_failure_still_records_the_routes_that_failed(agent, db, monkeypatch):
    """One bad route must not be silently dropped from an otherwise-ok run."""
    def _one_bad(params):
        if params.get("url", "").endswith("/settings"):
            return _Resp(500, None, text="upstream boom")
        return _Resp(200, _lighthouse_ok())

    _use_key(monkeypatch, GOOD_SHAPED_KEY)
    _use_http(monkeypatch, _one_bad)

    asyncio.run(agent._do_background_work())

    row = _rows(db)[0]
    assert row["outcome"] == "ok"
    assert row["summary"]["pages_audited"] == len(pixel_mod.AUDIT_ROUTES) - 1
    assert [f["url"] for f in row["failures"]] == [
        pixel_mod.FRONTEND_BASE_URL.rstrip("/") + "/settings"
    ]
    assert row["failures"][0]["status"] == 500


# ---------------------------------------------------------------------------
# 5. The key must never reach Mongo or a log line
# ---------------------------------------------------------------------------


def test_api_key_never_reaches_mongo(agent, db, monkeypatch, caplog):
    """Google echoing the request URL back must not persist the credential."""
    def _echoes_the_key(params):
        # Worst realistic case: the error body quotes the whole request URL.
        return _Resp(
            400,
            {"error": {"message": f"bad request for key={params['key']} - rejected"}},
        )

    _use_key(monkeypatch, BAD_KEY)
    _use_http(monkeypatch, _echoes_the_key)

    with caplog.at_level("WARNING"):
        asyncio.run(agent._do_background_work())

    stored = repr(_rows(db))
    assert BAD_KEY not in stored, "the API key was written into ui_audits"
    assert "***" in stored, "the key should be redacted, not just absent"
    assert BAD_KEY not in caplog.text, "the API key was written to a log line"


def test_transport_exception_carrying_the_key_is_scrubbed(agent, db, monkeypatch):
    """httpx exceptions can carry the request URL -- and the key rides in it."""
    def _boom(params):
        raise pixel_mod.httpx.ConnectError(
            f"failed connecting to {pixel_mod.PAGESPEED_URL}?key={params['key']}"
        )

    _use_key(monkeypatch, BAD_KEY)
    _use_http(monkeypatch, _boom)

    asyncio.run(agent._do_background_work())

    assert BAD_KEY not in repr(_rows(db))


# ---------------------------------------------------------------------------
# 6. health_check() -- the owner-facing surface
# ---------------------------------------------------------------------------


def test_never_run_reads_as_degraded(agent):
    """PIXEL's actual prod state: enabled, scheduled, zero rows."""
    health = asyncio.run(agent.health_check())
    assert health["health"] == "degraded"
    assert "never recorded" in health["last_error"]


def test_degraded_health_names_the_rejected_key(agent, db, monkeypatch):
    _use_key(monkeypatch, BAD_KEY)
    _use_http(monkeypatch, lambda p: _Resp(400, GOOGLE_400))
    asyncio.run(agent._do_background_work())

    health = asyncio.run(agent.health_check())
    assert health["health"] == "degraded"
    assert health["last_outcome"] == "credentials_rejected"
    assert "rejected by Google" in health["last_error"]
    assert "API key not valid" in health["last_error"]
    assert BAD_KEY not in repr(health)


def test_successful_run_reads_as_healthy(agent, db, monkeypatch):
    _use_key(monkeypatch, GOOD_SHAPED_KEY)
    _use_http(monkeypatch, lambda p: _Resp(200, _lighthouse_ok()))
    asyncio.run(agent._do_background_work())

    health = asyncio.run(agent.health_check())
    assert health["health"] == "healthy"
    assert health["last_outcome"] == "ok"
    assert health.get("last_error") is None


def test_health_reads_the_LATEST_row_not_the_first(agent, db, monkeypatch):
    """A recovery must clear the degraded flag; a fresh break must set it."""
    _use_key(monkeypatch, GOOD_SHAPED_KEY)
    _use_http(monkeypatch, lambda p: _Resp(200, _lighthouse_ok()))
    asyncio.run(agent._do_background_work())
    assert asyncio.run(agent.health_check())["health"] == "healthy"

    _use_http(monkeypatch, lambda p: _Resp(400, GOOGLE_400))
    asyncio.run(agent._do_background_work())

    health = asyncio.run(agent.health_check())
    assert health["health"] == "degraded"
    assert health["last_outcome"] == "credentials_rejected"


# ---------------------------------------------------------------------------
# 7. The shape check is ADVISORY, never a gate
# ---------------------------------------------------------------------------


def test_oddly_shaped_key_is_still_sent_to_google(agent, monkeypatch):
    """A key Google would accept must never be locked out client-side."""
    _use_key(monkeypatch, BAD_KEY)
    client = _use_http(monkeypatch, lambda p: _Resp(200, _lighthouse_ok()))

    asyncio.run(agent._do_background_work())

    assert len(client.calls) == len(pixel_mod.AUDIT_ROUTES)
    assert client.calls[0]["key"] == BAD_KEY


def test_key_shape_helper():
    assert pixel_mod._key_shape_ok(GOOD_SHAPED_KEY) is True
    assert pixel_mod._key_shape_ok(BAD_KEY) is False
    assert pixel_mod._key_shape_ok("") is False


# ---------------------------------------------------------------------------
# 8. The whole scheduled tick (background_tick, not the inner method)
# ---------------------------------------------------------------------------


def test_scheduled_tick_end_to_end_leaves_a_row(agent, db, monkeypatch):
    """Drive the REAL scheduler entrypoint, not just _do_background_work."""
    db.seed("agent_config", [{"agent_id": "pixel", "enabled": True}])
    _use_key(monkeypatch, BAD_KEY)
    _use_http(monkeypatch, lambda p: _Resp(400, GOOGLE_400))

    asyncio.run(agent.background_tick())

    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "credentials_rejected"
