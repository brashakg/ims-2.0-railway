"""
IMS 2.0 — JARVIS honest fallback + no-fabricated-numbers regression tests
=========================================================================

AI-1 fix: When the LLM is unavailable JARVIS must NEVER fabricate numbers.
Two specific defects are covered:

  1. _format_predictions_response previously read ``data['sales_forecast']``
     which does not exist in the honest empty envelope returned by
     ``get_predictions()`` — any "predictions" intent query in template-
     fallback mode raised a KeyError, effectively crashing the response.

  2. generate_sales_response previously computed ``this_week * 0.9`` as a
     fabricated "last week" comparison when the live overview has no weekly
     revenue (``this_week`` is always 0 from _compute_overview_live).
     This is a SYSTEM_INTENT violation (no fabricated numbers).

SYSTEM_INTENT.md: "Fail loudly. Honest empty states over fabricated data."
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_jarvis():
    """Import + construct a Jarvis instance with the DB monkey-patched off."""
    import api.routers.jarvis as jmod
    # Prevent any real DB calls during unit tests
    jmod.DB_AVAILABLE = False
    jmod.get_db_collection = lambda name: None
    return jmod.Jarvis()


def _honest_predictions():
    """The envelope returned by JarvisAnalyticsEngine.get_predictions()."""
    return {
        "revenue_forecast": [],
        "demand_forecast": [],
        "stockout_predictions": [],
        "churn_predictions": [],
    }


def _zero_overview():
    """The all-zero fallback envelope from get_business_overview()."""
    return {
        "revenue": {
            "today": 0,
            "yesterday": 0,
            "this_week": 0,
            "this_month": 0,
            "last_month": 0,
            "growth_percentage": 0,
            "target": 0,
            "achievement_percent": 0,
            "trend": "stable",
        },
        "orders": {
            "today": 0,
            "pending": 0,
            "in_progress": 0,
            "ready_for_delivery": 0,
            "average_order_value": 0,
            "conversion_rate": 0,
        },
        "inventory": {
            "total_products": 0,
            "low_stock_items": 0,
            "out_of_stock": 0,
            "inventory_value": 0,
            "fast_moving_count": 0,
            "slow_moving_count": 0,
            "expiring_soon": 0,
            "turnover_rate": 0,
        },
        "customers": {
            "total": 0,
            "new_this_month": 0,
            "returning_rate": 0,
            "average_lifetime_value": 0,
            "nps_score": 0,
            "top_segment": "N/A",
        },
        "staff": {
            "total_employees": 0,
            "present_today": 0,
            "on_leave": 0,
            "top_performer": "N/A",
            "average_sales_per_staff": 0,
            "attendance_rate": 0,
        },
    }


# ---------------------------------------------------------------------------
# Test 1: predictions intent does NOT crash on the honest empty envelope
# ---------------------------------------------------------------------------

class TestPredictionsHonestEmptyState:
    def test_format_predictions_does_not_raise_on_honest_envelope(self):
        """KeyError must NOT be raised when predictions = {empty lists}."""
        j = _make_jarvis()
        # Must not raise KeyError / any exception
        result = j._format_predictions_response(_honest_predictions())
        assert isinstance(result, str)
        assert len(result) > 0

    def test_format_predictions_mentions_oracle_or_history(self):
        """The honest empty state should tell the user why no forecast is
        available — not silently return empty or fabricate numbers."""
        j = _make_jarvis()
        result = j._format_predictions_response(_honest_predictions())
        lower = result.lower()
        # Should mention ORACLE, history, or a similar honest explanation
        assert any(kw in lower for kw in ("oracle", "history", "forecast", "prediction", "agent")), (
            "Expected honest explanation about missing forecast data, got: " + result[:200]
        )

    def test_format_predictions_no_fabricated_currency_on_empty(self):
        """No currency amounts (Rs symbol or numeric) must appear for empty forecasts."""
        import re
        j = _make_jarvis()
        result = j._format_predictions_response(_honest_predictions())
        # No rupee amounts like 1,23,000 or 12.5 L
        assert not re.search(r"[0-9]+\.[0-9]+ (L|Cr|K)\b|Rs\s*[0-9]", result), (
            "Fabricated currency in empty-predictions response: " + result[:200]
        )

    def test_format_predictions_with_live_data_renders_normally(self):
        """When real forecast data exists the response should include it."""
        j = _make_jarvis()
        live_predictions = {
            "revenue_forecast": [{"period": "next_month", "amount": 500000}],
            "demand_forecast": [{"category": "Frames", "trend": "up"}],
            "stockout_predictions": [],
            "churn_predictions": [],
        }
        result = j._format_predictions_response(live_predictions)
        assert "Frames" in result or "500000" in result or "5.0L" in result or "next_month" in result


# ---------------------------------------------------------------------------
# Test 2: generate_sales_response does not fabricate this_week comparison
# ---------------------------------------------------------------------------

class TestSalesResponseNoFabricatedWeeklyComparison:
    def test_this_week_0_vs_fabricated_0_9(self):
        """With this_week=0, the old code computed 0*0.9=0 as 'last week'
        so the % was 0 — but the comparison value was wrong. More critically,
        if this_week>0, the old code would fabricate 90% of it as last week.
        The fix uses 0 as the comparison (honest: last_week not computed)."""
        from api.routers.jarvis import JarvisResponseGenerator

        rg = JarvisResponseGenerator()
        data = _zero_overview()
        entities = {"time_period": "this_week"}
        # Should not crash and should not contain a fabricated % that implies
        # last-week data we don't have
        result = rg.generate_sales_response(data, entities)
        assert isinstance(result, str)
        assert "this week" in result.lower()
        # Growth % from 0 vs 0 should be 0 (safe: no division because previous==0)
        # Confirm no positive growth claim when both are 0
        assert "+100" not in result

    def test_this_week_nonzero_no_fabricated_90pct(self):
        """If this_week has a real value, the old code would say 'up 11.1% vs
        last week' — last week was fabricated as 90% of current week.
        The fix sets previous=0 so change = 0 (honest: no historical data)."""
        from api.routers.jarvis import JarvisResponseGenerator

        rg = JarvisResponseGenerator()
        data = _zero_overview()
        data["revenue"]["this_week"] = 100000
        entities = {"time_period": "this_week"}
        result = rg.generate_sales_response(data, entities)
        # With previous=0 the change formula guard (if previous) skips the
        # division, so change==0 and no fabricated % appears
        assert isinstance(result, str)
        # The old bug would show "+11.1%" here (100000 vs 90000 fabricated)
        assert "+11.1" not in result

    def test_today_comparison_uses_real_yesterday(self):
        """Today vs yesterday uses real data — no fabrication here."""
        from api.routers.jarvis import JarvisResponseGenerator

        rg = JarvisResponseGenerator()
        data = _zero_overview()
        data["revenue"]["today"] = 80000
        data["revenue"]["yesterday"] = 60000
        entities = {"time_period": "today"}
        result = rg.generate_sales_response(data, entities)
        # 80k vs 60k = +33.3%
        assert "+33.3" in result


# ---------------------------------------------------------------------------
# Test 3: _generate_fallback_response returns valid dict for predictions intent
# ---------------------------------------------------------------------------

class TestGenerateFallbackResponsePredictionsIntent:
    def test_predictions_intent_fallback_returns_dict(self):
        """_generate_fallback_response must return a valid dict for
        'predictions' intent without raising (regression for AI-1 KeyError)."""
        j = _make_jarvis()
        business_data = {
            "overview": _zero_overview(),
            "sales_insights": {"top_selling_categories": [], "top_selling_products": [], "sales_by_store": [], "month_revenue": 0},
            "inventory_insights": {"critical_alerts": [], "reorder_recommendations": [], "slow_movers": [], "inventory_health_score": 0, "turnover_ratio": 0, "dead_stock_value": 0},
            "customer_insights": {"segments": [], "loyalty_metrics": {}, "churn_risk": [], "upcoming_eye_tests": []},
            "staff_insights": {"roster": [], "performance_ranking": [], "attendance_summary": {"total_staff": 0, "present_today": 0, "on_leave": 0, "present_rate": 0, "late_arrivals_today": 0}, "orders_per_staff": 0},
            "predictions": _honest_predictions(),
            "recommendations": [],
        }
        result = j._generate_fallback_response("What are your predictions?", "predictions", {}, business_data)
        assert isinstance(result, dict)
        assert "response" in result
        assert isinstance(result["response"], str)
        assert len(result["response"]) > 0
