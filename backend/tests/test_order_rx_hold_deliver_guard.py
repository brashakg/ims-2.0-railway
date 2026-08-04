"""
IMS 2.0 - Rx FLAG-AND-HOLD delivery guard (enforcement half)
============================================================
PR #947 shipped the VISIBLE + RELEASABLE half of the online Rx flag-and-hold
policy (the hold chip + the ADMIN/SUPERADMIN clear-rx-hold route) and deferred
the ENFORCEMENT half. This locks it in:

An online spectacle-lens order booked without a valid prescription is stamped
``rx_pending`` + ``fulfillment_hold`` at ingest (owner decision 2026-06-30).
Staff must NOT be able to advance such an order to READY / DELIVERED until the
hold is cleared (which sets both flags back to False).

Covered here (the two order-status transition endpoints in orders.py):
  * POST /orders/{id}/ready    on a held order -> 400, status unchanged
  * POST /orders/{id}/deliver  on a held order -> 400, status unchanged
  * a NON-held order advances normally (no false block)
  * a CLEARED-hold order advances normally
  * the predicate is an OR: fulfillment_hold alone (no rx_pending) still blocks

The shipment-book guard is covered in test_shipping.py.

Uses the in-memory FakeDB harness (same as test_orders_hardening) so no live DB
is required.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")


@pytest.fixture
def wired_orders(monkeypatch):
    """Wire an in-memory OrderRepository into the orders router."""
    from tests.test_walkouts import FakeDB
    from api.routers import orders as orders_module
    from database.repositories.order_repository import OrderRepository

    fake_db = FakeDB()
    order_repo = OrderRepository(fake_db.get_collection("orders"))

    monkeypatch.setattr(orders_module, "get_order_repository", lambda: order_repo)

    return {"db": fake_db, "order_repo": order_repo}


def _seed_order(order_repo, **overrides):
    """Persist a READY, PAID order (1 FRAME @ 1000, no hold by default)."""
    doc = {
        "order_id": "ord-hold-1",
        "order_number": "ORD-BOK01-2026-HOLD01",
        "store_id": "BV-TEST-01",
        "customer_id": "cust-x",
        "customer_name": "Test Customer",
        "status": "READY",
        "items": [
            {
                "item_id": "line-1",
                "item_type": "SPECTACLE_LENS",
                "category": "SPECTACLE_LENS",
                "product_name": "Rx Lens",
                "quantity": 1,
                "unit_price": 1000.0,
                "item_total": 1000.0,
            }
        ],
        "grand_total": 1000.0,
        "amount_paid": 1000.0,
        "balance_due": 0.0,
        "payment_status": "PAID",
    }
    doc.update(overrides)
    order_repo.create(doc)
    return doc


# ---------------------------------------------------------------------------
# DELIVER guard
# ---------------------------------------------------------------------------


def test_deliver_blocked_on_active_rx_hold(client, auth_headers, wired_orders):
    """A held (rx_pending + fulfillment_hold) READY order cannot be delivered."""
    _seed_order(
        wired_orders["order_repo"],
        status="READY",
        rx_pending=True,
        fulfillment_hold=True,
    )
    resp = client.post(
        "/api/v1/orders/ord-hold-1/deliver", headers=auth_headers
    )
    assert resp.status_code == 400, resp.text
    assert "rx hold" in resp.text.lower()
    # The order was NOT advanced.
    assert wired_orders["order_repo"].find_by_id("ord-hold-1")["status"] == "READY"


def test_deliver_blocked_on_fulfillment_hold_only(
    client, auth_headers, wired_orders
):
    """The predicate is an OR: fulfillment_hold alone (rx_pending absent) still
    blocks delivery."""
    _seed_order(
        wired_orders["order_repo"], status="READY", fulfillment_hold=True
    )
    resp = client.post(
        "/api/v1/orders/ord-hold-1/deliver", headers=auth_headers
    )
    assert resp.status_code == 400, resp.text
    assert "rx hold" in resp.text.lower()


def test_deliver_allowed_when_not_held(client, auth_headers, wired_orders):
    """A normal (never-held) READY, PAID order delivers as before."""
    _seed_order(wired_orders["order_repo"], status="READY")
    resp = client.post(
        "/api/v1/orders/ord-hold-1/deliver", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "DELIVERED"


def test_deliver_allowed_after_hold_cleared(client, auth_headers, wired_orders):
    """Once the hold is cleared (both flags False), delivery proceeds -- proving
    clearing the hold releases the order."""
    _seed_order(
        wired_orders["order_repo"],
        status="READY",
        rx_pending=False,
        fulfillment_hold=False,
        rx_hold_cleared=True,
    )
    resp = client.post(
        "/api/v1/orders/ord-hold-1/deliver", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "DELIVERED"


# ---------------------------------------------------------------------------
# READY guard
# ---------------------------------------------------------------------------


def test_ready_blocked_on_active_rx_hold(client, auth_headers, wired_orders):
    """A held CONFIRMED order cannot be marked READY (the transition itself is
    valid CONFIRMED->READY, so this proves the HOLD is what blocks it)."""
    _seed_order(
        wired_orders["order_repo"],
        status="CONFIRMED",
        rx_pending=True,
        fulfillment_hold=True,
    )
    resp = client.post(
        "/api/v1/orders/ord-hold-1/ready", headers=auth_headers
    )
    assert resp.status_code == 400, resp.text
    assert "rx hold" in resp.text.lower()
    assert (
        wired_orders["order_repo"].find_by_id("ord-hold-1")["status"] == "CONFIRMED"
    )


def test_ready_allowed_when_not_held(client, auth_headers, wired_orders):
    """A normal CONFIRMED order advances to READY as before."""
    _seed_order(wired_orders["order_repo"], status="CONFIRMED")
    resp = client.post(
        "/api/v1/orders/ord-hold-1/ready", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "READY"


def test_ready_allowed_after_hold_cleared(client, auth_headers, wired_orders):
    """A cleared-hold CONFIRMED order advances to READY."""
    _seed_order(
        wired_orders["order_repo"],
        status="CONFIRMED",
        rx_pending=False,
        fulfillment_hold=False,
        rx_hold_cleared=True,
    )
    resp = client.post(
        "/api/v1/orders/ord-hold-1/ready", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "READY"


# ---------------------------------------------------------------------------
# Predicate unit tests (pure, no DB)
# ---------------------------------------------------------------------------


def test_predicate_helpers():
    from api.routers.orders import order_has_active_rx_hold, assert_no_active_rx_hold

    assert order_has_active_rx_hold({"rx_pending": True}) is True
    assert order_has_active_rx_hold({"fulfillment_hold": True}) is True
    assert order_has_active_rx_hold({"rx_pending": True, "fulfillment_hold": True}) is True
    assert order_has_active_rx_hold({"rx_pending": False, "fulfillment_hold": False}) is False
    assert order_has_active_rx_hold({}) is False
    assert order_has_active_rx_hold(None) is False

    # assert_ variant raises only when held.
    assert_no_active_rx_hold({"rx_pending": False})  # no raise
    with pytest.raises(Exception):
        assert_no_active_rx_hold({"fulfillment_hold": True})
