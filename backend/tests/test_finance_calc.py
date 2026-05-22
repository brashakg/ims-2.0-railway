"""
Finance correctness tests (Phase 1)
===================================
Pure checks of the COGS computation and order-total tolerance that back the
finance correctness pass. (The field/case fixes themselves are exercised by
the live aggregations against CI's Mongo.)
"""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from api.routers.finance import compute_cogs, _order_total  # noqa: E402


def test_cogs_from_real_cost():
    orders = [{"items": [{"product_id": "P1", "quantity": 2}, {"product_id": "P2", "quantity": 1}]}]
    cost = {"P1": 100.0, "P2": 250.0}
    assert compute_cogs(orders, cost) == 450.0  # 2*100 + 1*250


def test_cogs_fallback_when_cost_unknown():
    orders = [{"items": [{"product_id": "PX", "quantity": 1, "total": 1000}]}]
    assert compute_cogs(orders, {}, fallback_rate=0.6) == 600.0  # 60% of line total
    assert compute_cogs(orders, {}) == 0.0  # no fallback -> 0 (not counted)


def test_cogs_keyed_by_objectid():
    orders = [{"items": [{"product_id": "6a0ebf45fbd46b17c0b52ffc", "quantity": 3}]}]
    cost = {"6a0ebf45fbd46b17c0b52ffc": 50.0}
    assert compute_cogs(orders, cost) == 150.0


def test_cogs_empty():
    assert compute_cogs([], {}) == 0.0
    assert compute_cogs([{"items": []}], {"P1": 10}) == 0.0


def test_order_total_prefers_grand_total():
    assert _order_total({"grand_total": 500, "total": 999}) == 500.0
    assert _order_total({"total": 250}) == 250.0   # legacy fallback
    assert _order_total({}) == 0.0
