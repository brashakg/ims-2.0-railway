"""
IMS 2.0 — Agents diagnostic endpoint + OpenAPI schema tests (Phase 6.5b/c)
============================================================================
Covers:
  - /api/v1/jarvis/agents — response_model schema actually serializes the
    8-agent roster cleanly through Pydantic (catches silent shape drift).
  - /api/v1/jarvis/agents/diagnostic — returns canonical/registered/
    configured arrays + missing diff + worker_id + as_of.
  - Both endpoints are SUPERADMIN-gated (404 for non-superadmin).
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# /jarvis/agents — response_model + auth
# ---------------------------------------------------------------------------


class TestListAgentsAuth:
    def test_unauth_returns_404(self, client):
        """SUPERADMIN-only endpoints return 404 (not 401/403) so non-admins
        can't even tell the URL exists. Verified for the list endpoint."""
        resp = client.get("/api/v1/jarvis/agents")
        assert resp.status_code in (401, 404)

    def test_non_superadmin_returns_404(self, client, staff_headers):
        """A logged-in SALES_STAFF should still see 404 on the agents list."""
        resp = client.get("/api/v1/jarvis/agents", headers=staff_headers)
        assert resp.status_code == 404

    def test_superadmin_gets_envelope(self, client, auth_headers):
        """SUPERADMIN sees the full envelope shape required by the schema."""
        resp = client.get("/api/v1/jarvis/agents", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        # ListAgentsResponse top-level shape
        assert "agents" in body
        assert "total" in body
        assert "enabled_count" in body
        assert isinstance(body["agents"], list)
        # Each agent row has the AgentStatusResponse fields
        if body["agents"]:
            row = body["agents"][0]
            for required in ("agent_id", "agent_name", "enabled", "toggleable",
                             "status", "schedule_type", "schedule_value",
                             "capabilities"):
                assert required in row, f"missing field {required} in agent row"

    def test_no_legacy_route_shadowing(self, client, auth_headers):
        """
        Phase 6.5b regression: a legacy `GET /agents` handler in
        `routers/jarvis.py` used to shadow the canonical agents.py
        endpoint because jarvis_router was mounted first. That returned
        either 5 stale agents from `core.subagents.AGENT_REGISTRY` OR
        `{"agents": [], "error": "Subagent module not available"}`.
        Either of those would re-break the Jarvis page. This test fails
        if anyone re-introduces the duplicate route.
        """
        resp = client.get("/api/v1/jarvis/agents", headers=auth_headers)
        body = resp.json()
        # The legacy shape had `error` at the top level + missing fields.
        assert "error" not in body, (
            "Looks like the legacy /agents route in jarvis.py is shadowing "
            "the canonical one in agents.py again. Delete the duplicate."
        )
        # The legacy shape lacked `total` and `enabled_count`.
        assert "total" in body
        assert "enabled_count" in body


# ---------------------------------------------------------------------------
# /jarvis/agents/diagnostic — Phase 6.5c
# ---------------------------------------------------------------------------


class TestDiagnosticAuth:
    def test_unauth_returns_404(self, client):
        resp = client.get("/api/v1/jarvis/agents/diagnostic")
        assert resp.status_code in (401, 404)

    def test_non_superadmin_returns_404(self, client, staff_headers):
        resp = client.get(
            "/api/v1/jarvis/agents/diagnostic", headers=staff_headers,
        )
        assert resp.status_code == 404


def test_diagnostic_returns_canonical_eight(client, auth_headers):
    """Canonical list must always be the 8 expected agents."""
    resp = client.get(
        "/api/v1/jarvis/agents/diagnostic", headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["canonical"]) == {
        "jarvis", "cortex", "sentinel", "pixel",
        "megaphone", "oracle", "taskmaster", "nexus",
    }


def test_diagnostic_envelope_shape(client, auth_headers):
    """Every documented field present, types correct."""
    resp = client.get(
        "/api/v1/jarvis/agents/diagnostic", headers=auth_headers,
    )
    body = resp.json()
    for key in ("canonical", "registered", "configured",
                "missing_from_registry", "missing_from_config",
                "worker_id", "as_of"):
        assert key in body, f"diagnostic missing field {key}"
    assert isinstance(body["canonical"], list)
    assert isinstance(body["registered"], list)
    assert isinstance(body["configured"], list)
    assert isinstance(body["missing_from_registry"], list)
    assert isinstance(body["missing_from_config"], list)
    assert isinstance(body["worker_id"], str)
    assert isinstance(body["as_of"], str)


def test_diagnostic_missing_diff_is_consistent(client, auth_headers):
    """missing_from_registry == canonical \\ registered (set difference)."""
    resp = client.get(
        "/api/v1/jarvis/agents/diagnostic", headers=auth_headers,
    )
    body = resp.json()
    expected_missing = sorted(set(body["canonical"]) - set(body["registered"]))
    actual_missing = sorted(body["missing_from_registry"])
    assert actual_missing == expected_missing, (
        f"missing_from_registry mismatch: server returned {actual_missing} "
        f"but math says {expected_missing}"
    )
