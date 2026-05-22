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

from api.routers.finance import (  # noqa: E402
    compute_cogs,
    _order_total,
    gst_reconciliation,
    pnl_by_category,
)


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


def test_gst_reconciliation_groups_by_entity():
    orders = [
        {"store_id": "S1", "tax_amount": 100},
        {"store_id": "S2", "tax_amount": 50},
        {"store_id": "S1", "tax_total": 20},  # tax_total fallback
    ]
    purchases = [
        {"delivery_store_id": "S1", "tax_amount": 30},
        {"store_id": "S2", "tax_amount": 10},
    ]
    s2e = {"S1": "E1", "S2": "E2"}
    r = gst_reconciliation(orders, purchases, s2e, {"E1": "Entity One", "E2": "Entity Two"})
    assert r["total_collected"] == 170.0       # 100 + 50 + 20
    assert r["total_input_credit"] == 40.0     # 30 + 10
    assert r["total_net_payable"] == 130.0
    e1 = next(e for e in r["entities"] if e["entity_id"] == "E1")
    assert e1["entity_name"] == "Entity One"
    assert e1["gst_collected"] == 120.0        # 100 + 20
    assert e1["cgst"] == 60.0 and e1["sgst"] == 60.0
    assert e1["input_credit"] == 30.0
    assert e1["net_payable"] == 90.0


def test_gst_reconciliation_unassigned_store():
    r = gst_reconciliation([{"store_id": "SX", "tax_amount": 18}], [], {}, {})
    assert r["entities"][0]["entity_id"] == "_unassigned"
    assert r["total_collected"] == 18.0


def test_pnl_by_category():
    orders = [
        {"items": [
            {"item_type": "FRAME", "product_id": "P1", "quantity": 2, "total": 1000},
            {"item_type": "LENS", "product_id": "P2", "quantity": 1, "total": 500},
        ]},
        {"items": [{"item_type": "FRAME", "product_id": "P1", "quantity": 1, "total": 500}]},
    ]
    cats = pnl_by_category(orders, {"P1": 200.0, "P2": 150.0})
    frame = next(c for c in cats if c["category"] == "FRAME")
    assert frame["revenue"] == 1500.0   # 1000 + 500
    assert frame["cogs"] == 600.0       # 3 * 200
    assert frame["gross_profit"] == 900.0
    lens = next(c for c in cats if c["category"] == "LENS")
    assert lens["revenue"] == 500.0 and lens["cogs"] == 150.0
    assert cats[0]["category"] == "FRAME"  # sorted by revenue desc
