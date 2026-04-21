"""
IMS 2.0 - Observability module tests
======================================
Unit tests for backend/observability.py - verify the fail-soft contract:

  - Slack helpers no-op when SLACK_WEBHOOK_URL is unset
  - Severity threshold gating works (SLACK_ALERT_SEVERITY)
  - Payload shape is correct
  - Sentry helpers no-op when sentry_sdk is absent or DSN unset
  - agent_tick_span context manager works even without Sentry

None of these tests hit the network. Slack POST is mocked via
httpx.MockTransport.
"""

import os
import sys
import pytest
import httpx

# Add backend to path (conftest does this at session scope too)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import observability


# ============================================================================
# Slack payload & gating
# ============================================================================


def test_slack_payload_shape():
    payload = observability._build_slack_payload(
        severity="CRITICAL",
        title="Sales anomaly detected",
        body="Today's revenue is 62% below baseline",
        metadata={"delta_pct": -62.0, "today_total": 48000},
    )
    assert "attachments" in payload
    att = payload["attachments"][0]
    assert att["color"] == "#dc2626"  # critical = red-600
    assert att["title"].startswith("[CRITICAL]")
    assert "Today's revenue" in att["text"]
    # metadata becomes fields
    titles = {f["title"] for f in att["fields"]}
    assert "delta_pct" in titles
    assert "today_total" in titles


def test_slack_gating_defaults_to_critical_only(monkeypatch):
    monkeypatch.delenv("SLACK_ALERT_SEVERITY", raising=False)
    assert observability._should_alert("CRITICAL") is True
    assert observability._should_alert("HIGH") is False
    assert observability._should_alert("MEDIUM") is False
    assert observability._should_alert("LOW") is False


def test_slack_gating_honors_high_threshold(monkeypatch):
    monkeypatch.setenv("SLACK_ALERT_SEVERITY", "HIGH")
    assert observability._should_alert("CRITICAL") is True
    assert observability._should_alert("HIGH") is True
    assert observability._should_alert("MEDIUM") is False


def test_slack_gating_unknown_severity_is_suppressed(monkeypatch):
    monkeypatch.setenv("SLACK_ALERT_SEVERITY", "CRITICAL")
    # Unknown severity ranks as 0, threshold is CRITICAL (4), so it shouldn't alert.
    assert observability._should_alert("INFO") is False


@pytest.mark.asyncio
async def test_notify_slack_noop_when_unset(monkeypatch):
    """With SLACK_WEBHOOK_URL missing, notify_slack returns False without error."""
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    ok = await observability.notify_slack("CRITICAL", "title", "body", {"k": "v"})
    assert ok is False


@pytest.mark.asyncio
async def test_notify_slack_gating_skips_below_threshold(monkeypatch):
    """With threshold=CRITICAL, a MEDIUM alert should skip the POST."""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.example/abc")
    monkeypatch.setenv("SLACK_ALERT_SEVERITY", "CRITICAL")
    ok = await observability.notify_slack("MEDIUM", "title", "body", None)
    assert ok is False


@pytest.mark.asyncio
async def test_notify_slack_post_success(monkeypatch):
    """Real POST path with a mocked transport - verify payload + returns True on 200."""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.example/abc")
    monkeypatch.setenv("SLACK_ALERT_SEVERITY", "CRITICAL")

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(200, text="ok")

    transport = httpx.MockTransport(handler)

    # Monkey-patch httpx.AsyncClient to use our transport.
    original_client = httpx.AsyncClient

    class TestClient(original_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", TestClient)

    ok = await observability.notify_slack(
        "CRITICAL", "Sales anomaly", "Body", {"k": "v"}
    )
    assert ok is True
    assert captured["url"] == "https://hooks.slack.example/abc"
    assert "[CRITICAL] Sales anomaly" in captured["body"]


@pytest.mark.asyncio
async def test_notify_slack_swallows_errors(monkeypatch):
    """If the webhook returns 500 or times out, notify_slack returns False - never raises."""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.example/abc")
    monkeypatch.setenv("SLACK_ALERT_SEVERITY", "CRITICAL")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="slack down")

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    class TestClient(original_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", TestClient)

    ok = await observability.notify_slack("CRITICAL", "t", "b", None)
    assert ok is False


# ============================================================================
# Sentry no-op behavior
# ============================================================================


def test_sentry_sdk_returns_none_when_dsn_unset(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert observability._sentry_sdk() is None


def test_capture_agent_error_noop_without_sentry(monkeypatch):
    """capture_agent_error must not raise when Sentry is absent."""
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    # Should be a silent no-op
    observability.capture_agent_error("oracle", RuntimeError("boom"), extra={"x": 1})


@pytest.mark.asyncio
async def test_agent_tick_span_works_without_sentry(monkeypatch):
    """agent_tick_span yields None cleanly when Sentry isn't configured."""
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    async with observability.agent_tick_span("oracle", "background") as txn:
        assert txn is None


@pytest.mark.asyncio
async def test_agent_tick_span_propagates_exceptions(monkeypatch):
    """Exceptions raised inside the span must propagate to the caller."""
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    with pytest.raises(ValueError):
        async with observability.agent_tick_span("oracle", "background"):
            raise ValueError("test error")


def test_is_slack_configured(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    assert observability.is_slack_configured() is False
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.example/x")
    assert observability.is_slack_configured() is True
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "   ")
    assert observability.is_slack_configured() is False
