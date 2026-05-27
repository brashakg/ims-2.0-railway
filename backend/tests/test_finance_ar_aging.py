"""
Tests for:
  * AR aging by DUE date (was created_at) -- mis-labeled NET-60 customer
    as overdue at 31 days previously.
  * COGS-freeze on orders.items.cost_at_sale -- snapshot must be preferred
    over the current products.cost_price so historical P&L doesn't drift.
"""

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Importing api.routers.finance triggers the routers __init__ which loads
# api.routers.auth which insists on JWT_SECRET_KEY at module import time.
# Set both env vars here so this test file is self-sufficient even when run
# outside the standard `app` fixture (CI also exports these).
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from api.routers.finance import (  # noqa: E402
    _ar_due_date,
    _ar_days_overdue,
    _item_cost,
    compute_cogs,
    pnl_by_category,
)


# ---------- AR aging by due_date ----------


def test_ar_due_date_uses_customer_terms():
    """due_date = created_at + customer credit_terms_days (NET-45)."""
    order = {
        "created_at": "2026-04-01",
        "customer_id": "cust-1",
    }
    due = _ar_due_date(order, {"cust-1": 45})
    assert due == datetime(2026, 4, 1) + timedelta(days=45)


def test_ar_due_date_defaults_to_30_when_customer_missing():
    """Customer not in terms map -> default 30 days."""
    order = {"created_at": "2026-04-01", "customer_id": "ghost"}
    due = _ar_due_date(order, {})
    assert due == datetime(2026, 5, 1)


def test_ar_due_date_respects_per_order_override():
    """If a customer doc isn't in the map but the order carries
    payment_terms_days, use the order's value."""
    order = {
        "created_at": "2026-04-01",
        "customer_id": "ghost",
        "payment_terms_days": 60,
    }
    due = _ar_due_date(order, {})
    assert due == datetime(2026, 4, 1) + timedelta(days=60)


def test_ar_days_overdue_current_when_within_terms():
    """NET-60, 25 days old -> NOT overdue (was 25 'days_overdue' pre-fix)."""
    order = {"created_at": "2026-04-01", "customer_id": "c"}
    now = datetime(2026, 4, 26)  # 25 days after created
    due = _ar_due_date(order, {"c": 60})
    assert _ar_days_overdue(now, due) <= 0


def test_ar_days_overdue_past_due():
    """NET-30, 45 days old -> 15 days overdue (NOT 45)."""
    order = {"created_at": "2026-04-01", "customer_id": "c"}
    now = datetime(2026, 5, 16)
    due = _ar_due_date(order, {"c": 30})
    assert _ar_days_overdue(now, due) == 15


def test_ar_days_overdue_none_due_treated_as_current():
    """Unparseable created_at -> no due_date -> current (not overdue)."""
    assert _ar_days_overdue(datetime.utcnow(), None) == 0


# ---------- COGS-freeze on cost_at_sale ----------


def test_item_cost_prefers_snapshot():
    """When item.cost_at_sale is set, use it -- ignore live cost_by_product."""
    it = {"product_id": "p1", "cost_at_sale": 100.0}
    assert _item_cost(it, {"p1": 999.0}) == 100.0


def test_item_cost_falls_back_to_live_when_snapshot_missing():
    """No snapshot -> use the live cost_by_product (historical orders)."""
    it = {"product_id": "p1"}
    assert _item_cost(it, {"p1": 999.0}) == 999.0


def test_item_cost_returns_none_when_both_missing():
    """No snapshot, no live cost -> None (caller can apply fallback rate)."""
    it = {"product_id": "p-unknown"}
    assert _item_cost(it, {}) is None


def test_compute_cogs_historical_doesnt_drift_when_live_cost_edited():
    """Two orders for the same product. The historical one has cost_at_sale=80
    snapshotted; live cost_price is now 200. Historical COGS must stay 80,
    not jump to 200."""
    historical_order = {
        "items": [{"product_id": "p1", "quantity": 1, "cost_at_sale": 80.0}],
    }
    # An older order without the snapshot field uses the live price.
    new_order = {
        "items": [{"product_id": "p1", "quantity": 1}],
    }
    # Live cost_price has since been edited up.
    cost_by_product = {"p1": 200.0}
    cogs_hist = compute_cogs([historical_order], cost_by_product)
    cogs_new = compute_cogs([new_order], cost_by_product)
    assert cogs_hist == 80.0     # snapshot wins -- no drift
    assert cogs_new == 200.0     # falls back to live


def test_compute_cogs_zero_when_snapshot_zero_not_none():
    """Snapshot of 0 (free item / promo) is a valid value, not 'unknown'."""
    order = {
        "items": [{"product_id": "p1", "quantity": 2, "cost_at_sale": 0.0}],
    }
    assert compute_cogs([order], {"p1": 50.0}) == 0.0


def test_pnl_by_category_prefers_snapshot():
    """pnl_by_category respects cost_at_sale too -- category-level P&L
    should match the order-level snapshot, not the live price."""
    orders = [
        {
            "items": [
                {
                    "product_id": "p1",
                    "quantity": 1,
                    "total": 500,
                    "item_type": "FRAME",
                    "cost_at_sale": 100.0,
                }
            ]
        }
    ]
    cats = pnl_by_category(orders, {"p1": 999.0})  # live price ignored
    assert cats[0]["cogs"] == 100.0
    assert cats[0]["revenue"] == 500.0
