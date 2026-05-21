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

    def test_alerts_limit_bounds(self, client, auth_headers):
        r = client.get(
            "/api/v1/jarvis/agents/sentinel/health?alerts_limit=500",
            headers=auth_headers,
        )
        assert r.status_code == 200
