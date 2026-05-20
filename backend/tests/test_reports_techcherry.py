"""
IMS 2.0 — TechCherry R1 reports tests (Phase R1)
=================================================
Verifies the 4 net-new analytics endpoints introduced in
docs/TECHCHERRY_PORT_SCOPE.md §5:

  GET /reports/walkouts/footfall-audit
  GET /reports/sales/price-bands
  GET /reports/sales/lens-deep-dive
  GET /reports/sales/seasonality

Each gets:
  - auth gate (401 without token)
  - empty-DB envelope (returns zeroed structures, never raises)
  - shape contract (keys present, types correct)

Plus a few pure-function tests for the helpers (FY math, price band
bucketing) since those are the only places real logic-bugs can hide.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# Pure-function tests (no Mongo needed)
# ============================================================================


class TestHelpers:
    def test_fy_of_april_is_new_fy(self):
        from api.routers.reports import _fy_of
        assert _fy_of(datetime(2026, 4, 1)) == "FY26-27"
        assert _fy_of(datetime(2026, 12, 31)) == "FY26-27"

    def test_fy_of_january_is_previous_fy(self):
        from api.routers.reports import _fy_of
        assert _fy_of(datetime(2026, 1, 15)) == "FY25-26"
        assert _fy_of(datetime(2026, 3, 31)) == "FY25-26"

    def test_fy_of_century_rollover_pads_zeros(self):
        from api.routers.reports import _fy_of
        # FY99-00 and FY00-01 should pad correctly
        assert _fy_of(datetime(1999, 5, 1)) == "FY99-00"
        assert _fy_of(datetime(2000, 5, 1)) == "FY00-01"

    def test_price_band_below_1k(self):
        from api.routers.reports import _price_band_of
        assert _price_band_of(0) == "<1K"
        assert _price_band_of(999) == "<1K"

    def test_price_band_boundaries_low_inclusive(self):
        from api.routers.reports import _price_band_of
        # Each band is [lo, hi). 1000 → 1K-2.5K (not <1K).
        assert _price_band_of(1000) == "1K-2.5K"
        assert _price_band_of(2499.99) == "1K-2.5K"
        assert _price_band_of(2500) == "2.5K-5K"

    def test_price_band_above_top(self):
        from api.routers.reports import _price_band_of
        assert _price_band_of(150000) == "1.5L+"
        assert _price_band_of(10_000_000) == "1.5L+"

    def test_price_band_negative_falls_through(self):
        # Returns/refunds may come in as negative. Should not raise.
        from api.routers.reports import _price_band_of
        # Negative doesn't match any range; the fallback returns "1.5L+"
        # (this is by design — returns are summed separately, not band'd)
        result = _price_band_of(-500)
        assert result in ("<1K", "1.5L+")  # impl can choose


# ============================================================================
# HTTP-level tests via TestClient (cover the four endpoints)
# ============================================================================


class TestFootfallAuditEndpoint:
    def test_requires_auth(self, client):
        r = client.get("/api/v1/reports/walkouts/footfall-audit")
        assert r.status_code == 401

    def test_empty_envelope_when_db_absent(self, client, auth_headers):
        r = client.get(
            "/api/v1/reports/walkouts/footfall-audit",
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["store_id"] == "BV-TEST-01"
        assert isinstance(body["months"], list)
        assert "rolling" in body
        rolling = body["rolling"]
        for k in [
            "walkins_total", "walkouts_total", "walkouts_converted",
            "orders_total", "hidden_sales", "hidden_sales_pct",
            "staff_reported_conversion_pct", "true_conversion_pct",
        ]:
            assert k in rolling

    def test_months_back_query_bounds(self, client, auth_headers):
        r = client.get(
            "/api/v1/reports/walkouts/footfall-audit?months_back=0",
            headers=auth_headers,
        )
        assert r.status_code == 422
        r = client.get(
            "/api/v1/reports/walkouts/footfall-audit?months_back=37",
            headers=auth_headers,
        )
        assert r.status_code == 422


class TestPriceBandsEndpoint:
    def test_requires_auth(self, client):
        r = client.get("/api/v1/reports/sales/price-bands")
        assert r.status_code == 401

    def test_returns_all_11_bands(self, client, auth_headers):
        r = client.get(
            "/api/v1/reports/sales/price-bands",
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["store_id"] == "BV-TEST-01"
        # Exactly 11 named bands, in order
        assert body["bands"] == [
            "<1K", "1K-2.5K", "2.5K-5K", "5K-10K", "10K-15K", "15K-20K",
            "20K-30K", "30K-50K", "50K-75K", "75K-1.5L", "1.5L+",
        ]

    def test_movement_summary_present(self, client, auth_headers):
        r = client.get(
            "/api/v1/reports/sales/price-bands",
            headers=auth_headers,
        )
        body = r.json()
        ms = body["movement_summary"]
        for k in ("premiumized_pct", "stable_pct", "downgraded_pct", "compared_customers"):
            assert k in ms

    def test_fy_count_bounds(self, client, auth_headers):
        r = client.get(
            "/api/v1/reports/sales/price-bands?fy_count=0",
            headers=auth_headers,
        )
        assert r.status_code == 422
        r = client.get(
            "/api/v1/reports/sales/price-bands?fy_count=11",
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_trend_bands_bounds(self, client, auth_headers):
        r = client.get(
            "/api/v1/reports/sales/price-bands?trend_bands=12",
            headers=auth_headers,
        )
        assert r.status_code == 422


class TestLensDeepDiveEndpoint:
    def test_requires_auth(self, client):
        r = client.get("/api/v1/reports/sales/lens-deep-dive")
        assert r.status_code == 401

    def test_empty_envelope_shape(self, client, auth_headers):
        r = client.get(
            "/api/v1/reports/sales/lens-deep-dive",
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        totals = body["totals"]
        for k in (
            "lens_units", "lens_revenue", "atv",
            "contact_lens_units", "contact_lens_revenue",
        ):
            assert k in totals
        for k in ("by_brand", "by_type", "by_coating", "by_refractive_index"):
            assert isinstance(body[k], list)
        assert "parse_rate" in body
        assert "metadata_pending" in body

    def test_months_back_default(self, client, auth_headers):
        r = client.get(
            "/api/v1/reports/sales/lens-deep-dive",
            headers=auth_headers,
        )
        body = r.json()
        # Default months_back=12 → period spans roughly a year
        # Just sanity-check the period dates are present and parsable.
        assert "period_start" in body
        assert "period_end" in body


class TestSeasonalityEndpoint:
    def test_requires_auth(self, client):
        r = client.get("/api/v1/reports/sales/seasonality")
        assert r.status_code == 401

    def test_returns_all_7_days_and_12_months(self, client, auth_headers):
        r = client.get(
            "/api/v1/reports/sales/seasonality",
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["day_of_week"]) == 7
        assert len(body["month_of_year"]) == 12
        dow_names = [d["dow"] for d in body["day_of_week"]]
        assert dow_names == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        month_names = [d["month"] for d in body["month_of_year"]]
        assert month_names == [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        ]

    def test_peak_trough_keys_present_even_if_null(self, client, auth_headers):
        r = client.get(
            "/api/v1/reports/sales/seasonality",
            headers=auth_headers,
        )
        body = r.json()
        # In empty-DB mode all revenues are 0, so peak/trough come back as null
        for k in ("peak_dow", "trough_dow", "peak_month", "trough_month"):
            assert k in body
            assert body[k] is None
        assert body["peak_dow_lift_pct"] == 0.0
        assert body["total_orders"] == 0

    def test_years_back_bounds(self, client, auth_headers):
        r = client.get(
            "/api/v1/reports/sales/seasonality?years_back=0",
            headers=auth_headers,
        )
        assert r.status_code == 422
        r = client.get(
            "/api/v1/reports/sales/seasonality?years_back=11",
            headers=auth_headers,
        )
        assert r.status_code == 422
