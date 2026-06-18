"""
IMS 2.0 - ORACLE agent: customer PII must be scrubbed before the LLM call
========================================================================
Regression for SEC-1 (launch-hardening batch 1): the ORACLE agent path sent
unscrubbed customer PII (names/phone/email) to the Anthropic API because it
json.dumps-ed raw anomaly/context docs straight into the user prompt string
(the user/system strings are never scrubbed -- only the business_data kwarg is).

Fix: ORACLE now passes its data via business_data with scrub_level="customer",
so scrub_pii strips customer/patient PII keys before the request, and it no
longer embeds the customer name in the anomaly 'summary' free text.

These tests intercept llm_provider._call_anthropic to capture EXACTLY the
(system, user) payload that would hit the API, and assert customer PII is
absent from it.
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ["ANTHROPIC_API_KEY"] = "test-key-for-scrub-test"  # populate the registry

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import llm_provider  # noqa: E402
from agents.implementations.oracle import OracleAgent  # noqa: E402


_CUSTOMER_NAME = "Rajesh Kumar Verma"
_CUSTOMER_PHONE = "9876543210"
_CUSTOMER_EMAIL = "rajesh.verma@example.com"


def _install_capture(monkeypatch):
    """Patch the Anthropic transport so no real call is made and capture the
    full (system, user) payload. Returns a dict the test reads after the call."""
    captured = {}

    async def _fake_call_anthropic(system, user, m, history, max_tokens, timeout):
        captured["system"] = system
        captured["user"] = user
        # Return a benign JSON-shaped string so call_claude_json parses it.
        return '{"narrative": "ok", "recommendation": "do the thing"}'

    monkeypatch.setattr(llm_provider, "_call_anthropic", _fake_call_anthropic)
    return captured


def _assert_no_customer_pii(captured):
    blob = (captured.get("system", "") or "") + "\n" + (captured.get("user", "") or "")
    assert _CUSTOMER_NAME not in blob, "customer NAME leaked to the LLM payload"
    assert _CUSTOMER_PHONE not in blob, "customer PHONE leaked to the LLM payload"
    assert _CUSTOMER_EMAIL not in blob, "customer EMAIL leaked to the LLM payload"
    # The scrubbed payload must still carry the redaction marker so we know the
    # data actually flowed through scrub_pii (not just that it was dropped).
    assert "[redacted]" in blob


def test_enrich_with_claude_scrubs_customer_pii(monkeypatch):
    captured = _install_capture(monkeypatch)

    agent = OracleAgent()
    anomaly = {
        "kind": "vip_churn",
        "severity": "HIGH",
        "summary": "VIP customer C123 overdue by 90d (usual interval 45d)",
        "customer_id": "C123",
        "customer_name": _CUSTOMER_NAME,
        "customer_phone": _CUSTOMER_PHONE,
        "customer_email": _CUSTOMER_EMAIL,
    }

    narrative, rec = asyncio.run(agent._enrich_with_claude(anomaly))

    assert narrative == "ok"  # the fake transport responded -> call happened
    assert captured, "the LLM transport was never invoked"
    _assert_no_customer_pii(captured)


def test_run_on_demand_scrubs_customer_pii(monkeypatch):
    captured = _install_capture(monkeypatch)

    agent = OracleAgent()

    # Build a context that mimics what _build_context_for_query returns, with a
    # recent anomaly carrying customer PII keys.
    async def _fake_ctx(_query):
        return {
            "query": _query,
            "recent_anomalies": [
                {
                    "kind": "vip_churn",
                    "customer_id": "C123",
                    "customer_name": _CUSTOMER_NAME,
                    "customer_phone": _CUSTOMER_PHONE,
                    "customer_email": _CUSTOMER_EMAIL,
                }
            ],
            "revenue_last_7d": {"2026-06-18": 12345.0},
        }

    # Stub the anomalies collection so run() reaches the Claude path.
    class _Coll:
        def find(self, *a, **k):
            return self

        def sort(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return []

    monkeypatch.setattr(agent, "get_collection", lambda name: _Coll())
    monkeypatch.setattr(agent, "_build_context_for_query", _fake_ctx)

    from agents.base import AgentContext

    resp = asyncio.run(agent.run("why is revenue down?", AgentContext()))

    assert resp.success
    assert captured, "the LLM transport was never invoked"
    _assert_no_customer_pii(captured)
