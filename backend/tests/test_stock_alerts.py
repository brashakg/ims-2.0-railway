"""
IMS 2.0 — unified stock-alerts classifier tests
================================================
Locks the logic behind GET /api/v1/inventory/alerts, which feeds
StockAlertsOverview.tsx (it used to render hardcoded mock alerts —
Vogue Cat Eye / Prada Baroque etc. — now removed).

The classifier `_build_stock_alert` is a pure function (no DB), so these
tests exercise the real decision logic directly: each product yields at
most ONE alert, chosen by priority
    REORDER_ALERT > LOW_STOCK > DEAD_STOCK > OVERSTOCK > FAST_MOVING.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers.inventory import (  # noqa: E402
    _build_stock_alert,
    _summarise_alert_stats,
    _empty_alert_stats,
)

NOW = datetime(2026, 5, 21, 12, 0, 0)


def _product(**overrides) -> dict:
    base = {
        "name": "Ray-Ban Aviator",
        "brand": "Ray-Ban",
        "category": "SUNGLASS",
        "barcode": "BC-001",
        "sku": "BC-001",
        "mrp": 8000.0,
        "offer_price": 8000.0,
        "cost_price": 4000.0,
        "stock_quantity": 10,
        "reorder_point": 0,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# REORDER_ALERT
# --------------------------------------------------------------------------


class TestReorderAlert:
    def test_out_of_stock_but_selling_is_critical_reorder(self):
        # 60 units in 30 days = 2/day, zero stock → CRITICAL reorder
        alert = _build_stock_alert(
            _product(stock_quantity=0),
            sold_30=60,
            last_sale=NOW - timedelta(days=1),
            now=NOW,
            dead_days=90,
            lead_time_days=14,
        )
        assert alert is not None
        assert alert["alertType"] == "REORDER_ALERT"
        assert alert["severity"] == "CRITICAL"
        assert alert["recommendedOrder"] > 0
        assert alert["costImpact"] == round(alert["recommendedOrder"] * 4000.0, 2)

    def test_runs_out_within_lead_time_is_reorder(self):
        # 2 units/day, 10 in stock → ~5 days cover, lead time 14 → reorder
        alert = _build_stock_alert(
            _product(stock_quantity=10),
            sold_30=60,
            last_sale=NOW - timedelta(days=1),
            now=NOW,
            dead_days=90,
            lead_time_days=14,
        )
        assert alert["alertType"] == "REORDER_ALERT"
        # 5 days > 14/2=7? no, 5 <= 7 → CRITICAL
        assert alert["severity"] == "CRITICAL"
        assert alert["projectedDaysToStockout"] == 5.0

    def test_explicit_reorder_point_triggers_even_with_cover(self):
        # slow sale but stock at/below an explicit reorder point
        alert = _build_stock_alert(
            _product(stock_quantity=3, reorder_point=5),
            sold_30=3,  # 0.1/day → 30 days cover, but below reorder point
            last_sale=NOW - timedelta(days=2),
            now=NOW,
            dead_days=90,
            lead_time_days=14,
        )
        assert alert["alertType"] == "REORDER_ALERT"


# --------------------------------------------------------------------------
# LOW_STOCK
# --------------------------------------------------------------------------


class TestLowStock:
    def test_getting_low_but_not_critical(self):
        # 1 unit/day, 12 in stock → 12 days cover. lead 7: >7 (not reorder)
        # but <=14 (lead*2) → LOW_STOCK
        alert = _build_stock_alert(
            _product(stock_quantity=12),
            sold_30=30,
            last_sale=NOW - timedelta(days=1),
            now=NOW,
            dead_days=90,
            lead_time_days=7,
        )
        assert alert["alertType"] == "LOW_STOCK"
        assert alert["severity"] == "MEDIUM"
        assert alert["recommendedOrder"] >= 1


# --------------------------------------------------------------------------
# DEAD_STOCK
# --------------------------------------------------------------------------


class TestDeadStock:
    def test_never_sold_with_stock_is_dead(self):
        alert = _build_stock_alert(
            _product(stock_quantity=10, cost_price=4000.0),
            sold_30=0,
            last_sale=None,
            now=NOW,
            dead_days=90,
            lead_time_days=14,
        )
        assert alert["alertType"] == "DEAD_STOCK"
        # 10 * 4000 = 40,000 → HIGH (>=20k, <50k)
        assert alert["severity"] == "HIGH"
        assert alert["costImpact"] == 40000.0
        assert "No recorded sales" in alert["actionRequired"]

    def test_no_sale_within_window_is_dead(self):
        alert = _build_stock_alert(
            _product(stock_quantity=2, cost_price=500.0),
            sold_30=0,
            last_sale=NOW - timedelta(days=120),  # > 90 day window
            now=NOW,
            dead_days=90,
            lead_time_days=14,
        )
        assert alert["alertType"] == "DEAD_STOCK"
        # 2 * 500 = 1000 → LOW (<5k)
        assert alert["severity"] == "LOW"
        assert alert["daysWithoutMovement"] == 120

    def test_high_value_dead_stock_is_critical(self):
        alert = _build_stock_alert(
            _product(stock_quantity=20, cost_price=5000.0),  # 100k tied up
            sold_30=0,
            last_sale=None,
            now=NOW,
            dead_days=90,
            lead_time_days=14,
        )
        assert alert["alertType"] == "DEAD_STOCK"
        assert alert["severity"] == "CRITICAL"

    def test_recent_sale_is_not_dead(self):
        # sold 40 days ago, no current velocity, healthy stock, within window
        alert = _build_stock_alert(
            _product(stock_quantity=10),
            sold_30=0,
            last_sale=NOW - timedelta(days=40),  # within 90-day window
            now=NOW,
            dead_days=90,
            lead_time_days=14,
        )
        # not dead (sold recently), not selling now (velocity 0) → no alert
        assert alert is None


# --------------------------------------------------------------------------
# OVERSTOCK / FAST_MOVING
# --------------------------------------------------------------------------


class TestOverstockAndFastMoving:
    def test_overstock_when_many_months_of_cover(self):
        # 1 unit/day = 30/month, 400 in stock → ~13 months → OVERSTOCK
        alert = _build_stock_alert(
            _product(stock_quantity=400, cost_price=100.0),
            sold_30=30,
            last_sale=NOW - timedelta(days=1),
            now=NOW,
            dead_days=90,
            lead_time_days=14,
        )
        assert alert["alertType"] == "OVERSTOCK"
        assert alert["severity"] == "MEDIUM"  # >=12 months
        assert alert["costImpact"] > 0

    def test_fast_moving_when_strong_seller_healthy_cover(self):
        # 1 unit/day, 100 in stock → ~3.3 months (not overstock), velocity>=0.5
        alert = _build_stock_alert(
            _product(stock_quantity=100),
            sold_30=30,
            last_sale=NOW - timedelta(days=1),
            now=NOW,
            dead_days=90,
            lead_time_days=14,
        )
        assert alert["alertType"] == "FAST_MOVING"
        assert alert["severity"] == "LOW"

    def test_slow_healthy_seller_yields_no_alert(self):
        # 0.1/day = 3/month, 30 in stock → 10 months? that's overstock.
        # Use 3 in stock, 3 sold → 1 month cover, velocity 0.1 (<0.5) → none
        alert = _build_stock_alert(
            _product(stock_quantity=3),
            sold_30=3,
            last_sale=NOW - timedelta(days=2),
            now=NOW,
            dead_days=90,
            lead_time_days=7,
        )
        # 3 units / 0.1 per day = 30 days cover > 14 (lead*2) so not low;
        # months = 3/(0.1*30)=1 so not overstock; velocity 0.1 < 0.5 → none
        assert alert is None


# --------------------------------------------------------------------------
# Priority ordering — a product matching multiple signals picks the top one
# --------------------------------------------------------------------------


class TestPriorityOrdering:
    def test_fast_seller_about_to_run_out_is_reorder_not_fast_moving(self):
        # high velocity AND low cover → REORDER wins over FAST_MOVING
        alert = _build_stock_alert(
            _product(stock_quantity=5),
            sold_30=90,  # 3/day
            last_sale=NOW - timedelta(days=1),
            now=NOW,
            dead_days=90,
            lead_time_days=14,
        )
        assert alert["alertType"] == "REORDER_ALERT"


# --------------------------------------------------------------------------
# Stats roll-up
# --------------------------------------------------------------------------


class TestStatsSummary:
    def test_empty_stats_shape(self):
        s = _empty_alert_stats()
        assert s == {
            "totalAlerts": 0,
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "deadStockValue": 0,
            "recommendedRestockValue": 0,
        }

    def test_summarise_counts_and_values(self):
        alerts = [
            {"severity": "CRITICAL", "alertType": "DEAD_STOCK", "costImpact": 100000},
            {"severity": "HIGH", "alertType": "REORDER_ALERT", "costImpact": 8000},
            {"severity": "MEDIUM", "alertType": "LOW_STOCK", "costImpact": 2000},
            {"severity": "LOW", "alertType": "FAST_MOVING", "costImpact": 0},
        ]
        s = _summarise_alert_stats(alerts)
        assert s["totalAlerts"] == 4
        assert s["critical"] == 1
        assert s["high"] == 1
        assert s["medium"] == 1
        assert s["low"] == 1
        assert s["deadStockValue"] == 100000
        # reorder + low_stock = 8000 + 2000
        assert s["recommendedRestockValue"] == 10000
