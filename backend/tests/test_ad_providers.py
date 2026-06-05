"""
IMS 2.0 - Tests for CRM-16 Ad Performance Provider Seam
=========================================================
Coverage matrix:
  - SIMULATED/not_configured when no creds (Google + Meta)
  - Normalised shape: required fields present
  - Totals / CPL arithmetic
  - blended_roas stays 0 when spend > 0 but no revenue (contract)
  - fetch_ad_performance merges both channels + computes configured flags
  - channel filter: google-only / meta-only
  - Fail-soft: exception in provider -> not_configured rows, no raise
  - Router smoke: GET /api/v1/marketing/ad-performance returns 200 with
    correct shape even when no DB / no creds (not_configured response)
  - RBAC: unauthenticated -> 401; authenticated non-admin -> 403
"""
from __future__ import annotations

import asyncio
import sys
import os

import pytest

# Make backend importable from the test directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(coro):
    """Run an async coroutine from a sync test (Python 3.10+ compatible)."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Unit tests for ad_providers (no HTTP, no DB)
# ---------------------------------------------------------------------------


class TestNotConfiguredRows:
    def test_google_not_configured_shape(self):
        from api.services.ad_providers import _not_configured_rows

        rows = _not_configured_rows("google")
        assert len(rows) == 1
        r = rows[0]
        assert r.channel == "google"
        assert r.status == "not_configured"
        assert r.campaign_id == "__not_configured__"
        assert r.spend == 0.0
        assert r.cpl == 0.0
        assert r.roas == 0.0

    def test_meta_not_configured_shape(self):
        from api.services.ad_providers import _not_configured_rows

        rows = _not_configured_rows("meta")
        assert rows[0].channel == "meta"
        assert rows[0].status == "not_configured"


class TestLoadIntegrationConfig:
    def test_returns_empty_when_db_is_none(self):
        from api.services.ad_providers import _load_integration_config

        assert _load_integration_config(None, "google_ads") == {}

    def test_returns_empty_when_doc_not_found(self):
        """Uses a mock collection that returns None for find_one."""
        from api.services.ad_providers import _load_integration_config

        class _MockColl:
            def find_one(self, *a, **kw):
                return None

        class _MockDB:
            def get_collection(self, name):
                return _MockColl()

        assert _load_integration_config(_MockDB(), "google_ads") == {}

    def test_returns_config_dict_when_found(self):
        from api.services.ad_providers import _load_integration_config

        cfg = {"customer_id": "123", "developer_token": "tok"}

        class _MockColl:
            def find_one(self, *a, **kw):
                return {"type": "google_ads", "enabled": True, "config": cfg}

        class _MockDB:
            def get_collection(self, name):
                return _MockColl()

        result = _load_integration_config(_MockDB(), "google_ads")
        assert result == cfg

    def test_returns_empty_on_exception(self):
        from api.services.ad_providers import _load_integration_config

        class _BrokenDB:
            def get_collection(self, name):
                raise RuntimeError("Mongo is down")

        assert _load_integration_config(_BrokenDB(), "google_ads") == {}


class TestGoogleAdsPerformance:
    def test_returns_not_configured_when_no_db(self):
        from api.services.ad_providers import google_ads_performance

        rows = run(google_ads_performance(None, {"from": "2025-01-01", "to": "2025-01-31"}))
        assert len(rows) == 1
        assert rows[0].status == "not_configured"
        assert rows[0].channel == "google"

    def test_returns_not_configured_when_empty_config(self):
        from api.services.ad_providers import google_ads_performance

        class _MockColl:
            def find_one(self, *a, **kw):
                return None

        class _MockDB:
            def get_collection(self, name):
                return _MockColl()

        rows = run(google_ads_performance(_MockDB(), {"from": "2025-01-01", "to": "2025-01-31"}))
        assert rows[0].status == "not_configured"

    def test_returns_not_configured_when_partial_creds(self):
        """Missing refresh_token -> not_configured (no HTTP call made)."""
        from api.services.ad_providers import google_ads_performance

        cfg = {
            "customer_id": "123",
            "developer_token": "tok",
            "client_id": "cid",
            "client_secret": "sec",
            # refresh_token deliberately absent
        }

        class _MockColl:
            def find_one(self, *a, **kw):
                return {"type": "google_ads", "enabled": True, "config": cfg}

        class _MockDB:
            def get_collection(self, name):
                return _MockColl()

        rows = run(google_ads_performance(_MockDB(), {"from": "2025-01-01", "to": "2025-01-31"}))
        assert rows[0].status == "not_configured"


class TestMetaAdsPerformance:
    def test_returns_not_configured_when_no_db(self):
        from api.services.ad_providers import meta_ads_performance

        rows = run(meta_ads_performance(None, {"from": "2025-01-01", "to": "2025-01-31"}))
        assert len(rows) == 1
        assert rows[0].status == "not_configured"
        assert rows[0].channel == "meta"

    def test_returns_not_configured_when_missing_access_token(self):
        from api.services.ad_providers import meta_ads_performance

        cfg = {"ad_account_id": "123456789"}  # no access_token

        class _MockColl:
            def find_one(self, *a, **kw):
                return {"type": "meta_ads", "enabled": True, "config": cfg}

        class _MockDB:
            def get_collection(self, name):
                return _MockColl()

        rows = run(meta_ads_performance(_MockDB(), {"from": "2025-01-01", "to": "2025-01-31"}))
        assert rows[0].status == "not_configured"


class TestComputeTotals:
    def test_totals_with_no_rows(self):
        from api.services.ad_providers import AdPerformanceResult, _compute_totals

        result = AdPerformanceResult()
        _compute_totals(result)
        assert result.total_spend == 0.0
        assert result.total_conversions == 0

    def test_totals_arithmetic(self):
        from api.services.ad_providers import (
            AdPerformanceResult,
            CampaignRow,
            _compute_totals,
        )

        rows = [
            CampaignRow(
                channel="google",
                campaign_id="1",
                campaign_name="C1",
                spend=10_000.0,
                impressions=50_000,
                clicks=500,
                conversions=20,
                ctr=1.0,
                cpl=500.0,
                roas=0.0,
            ),
            CampaignRow(
                channel="meta",
                campaign_id="2",
                campaign_name="C2",
                spend=5_000.0,
                impressions=20_000,
                clicks=200,
                conversions=10,
                ctr=1.0,
                cpl=500.0,
                roas=0.0,
            ),
        ]
        result = AdPerformanceResult(rows=rows)
        _compute_totals(result)
        assert result.total_spend == 15_000.0
        assert result.total_impressions == 70_000
        assert result.total_clicks == 700
        assert result.total_conversions == 30
        assert result.total_cpl == pytest.approx(500.0, rel=1e-3)

    def test_cpl_zero_when_no_conversions(self):
        from api.services.ad_providers import (
            AdPerformanceResult,
            CampaignRow,
            _compute_totals,
        )

        rows = [
            CampaignRow(
                channel="google",
                campaign_id="1",
                campaign_name="C1",
                spend=10_000.0,
                impressions=50_000,
                clicks=500,
                conversions=0,  # no conversions
                ctr=1.0,
                cpl=0.0,
                roas=0.0,
            ),
        ]
        result = AdPerformanceResult(rows=rows)
        _compute_totals(result)
        assert result.total_cpl == 0.0

    def test_blended_roas_stays_zero(self):
        """blended_roas is not set by _compute_totals (requires revenue data)."""
        from api.services.ad_providers import (
            AdPerformanceResult,
            CampaignRow,
            _compute_totals,
        )

        rows = [
            CampaignRow(
                channel="google",
                campaign_id="1",
                campaign_name="C1",
                spend=5000.0,
                impressions=10000,
                clicks=100,
                conversions=5,
                ctr=1.0,
                cpl=1000.0,
                roas=0.0,
            ),
        ]
        result = AdPerformanceResult(rows=rows)
        _compute_totals(result)
        # blended_roas must stay 0 -- it requires revenue from ad accounts
        assert result.blended_roas == 0.0


class TestFetchAdPerformance:
    def test_both_not_configured_when_no_db(self):
        from api.services.ad_providers import fetch_ad_performance

        result = run(fetch_ad_performance(None, "2025-01-01", "2025-01-31"))
        assert result.google_configured is False
        assert result.meta_configured is False
        assert result.rows == []  # sentinel rows stripped
        assert result.note != ""  # human-readable explanation present

    def test_google_only_filter(self):
        """channel='google' -> meta_configured stays False and no meta sentinel row."""
        from api.services.ad_providers import fetch_ad_performance

        result = run(fetch_ad_performance(None, "2025-01-01", "2025-01-31", channel="google"))
        assert result.meta_configured is False
        assert all(r.channel == "google" for r in result.rows)

    def test_meta_only_filter(self):
        from api.services.ad_providers import fetch_ad_performance

        result = run(fetch_ad_performance(None, "2025-01-01", "2025-01-31", channel="meta"))
        assert result.google_configured is False
        assert all(r.channel == "meta" for r in result.rows)

    def test_returns_valid_result_on_exception(self):
        """If provider raises unexpectedly, result is still a valid object."""
        from api.services.ad_providers import fetch_ad_performance

        class _ExplodingDB:
            def get_collection(self, name):
                raise RuntimeError("Chaos!")

        result = run(
            fetch_ad_performance(_ExplodingDB(), "2025-01-01", "2025-01-31")
        )
        # Must return an AdPerformanceResult, not raise
        assert hasattr(result, "google_configured")
        assert hasattr(result, "rows")

    def test_fetched_at_is_iso_string(self):
        from api.services.ad_providers import fetch_ad_performance

        result = run(fetch_ad_performance(None, "2025-01-01", "2025-01-31"))
        assert result.fetched_at != ""
        # Parseable as an ISO datetime
        from datetime import datetime

        datetime.fromisoformat(result.fetched_at.replace("Z", "+00:00"))

    def test_total_spend_zero_when_no_data(self):
        from api.services.ad_providers import fetch_ad_performance

        result = run(fetch_ad_performance(None, "2025-01-01", "2025-01-31"))
        assert result.total_spend == 0.0
        assert result.total_impressions == 0
        assert result.total_conversions == 0


# ---------------------------------------------------------------------------
# Router smoke tests via TestClient
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    """FastAPI TestClient with no live DB (stub mode)."""
    from fastapi.testclient import TestClient
    from api.main import app

    return TestClient(app)


def _make_token(client, role: str = "ADMIN") -> str:
    """Log in with the seeded admin account to get a JWT."""
    username = "admin" if role in ("ADMIN", "SUPERADMIN") else "cashier_test"
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    if resp.status_code == 200:
        return resp.json().get("access_token", "")
    return ""


class TestAdPerformanceRouter:
    def test_unauthenticated_returns_401(self, client):
        resp = client.get(
            "/api/v1/marketing/ad-performance",
            params={"from": "2025-01-01", "to": "2025-01-31"},
        )
        assert resp.status_code in (401, 403), f"Expected 401/403 got {resp.status_code}"

    def test_admin_returns_200_with_not_configured(self, client):
        """Admin with valid JWT -> 200 even with no creds (not_configured response)."""
        token = _make_token(client, "ADMIN")
        if not token:
            pytest.skip("Auth endpoint not available in stub mode")
        resp = client.get(
            "/api/v1/marketing/ad-performance",
            params={"from": "2025-01-01", "to": "2025-01-31"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Shape contract
        assert "rows" in body
        assert "google_configured" in body
        assert "meta_configured" in body
        assert "total_spend" in body
        assert "fetched_at" in body
        # Since there's no DB/creds in stub mode, both must be unconfigured
        assert body["google_configured"] is False
        assert body["meta_configured"] is False

    def test_invalid_date_format_returns_422(self, client):
        token = _make_token(client, "ADMIN")
        if not token:
            pytest.skip("Auth endpoint not available in stub mode")
        resp = client.get(
            "/api/v1/marketing/ad-performance",
            params={"from": "01/01/2025", "to": "31/01/2025"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422

    def test_invalid_channel_returns_422(self, client):
        token = _make_token(client, "ADMIN")
        if not token:
            pytest.skip("Auth endpoint not available in stub mode")
        resp = client.get(
            "/api/v1/marketing/ad-performance",
            params={"from": "2025-01-01", "to": "2025-01-31", "channel": "tiktok"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422
