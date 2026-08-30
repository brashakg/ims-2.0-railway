"""
IMS 2.0 - Credit-delivery gate + handover record + collect-and-deliver
======================================================================
Owner rulings (POS Wave 4): delivering with a balance still due is a
credit decision — manager, or a manager-approved CREDIT_DELIVERY token
bound to the store AND the order; the counter records handover details on
the atomic claim; and collect-balance-and-deliver is ONE action that
delegates to the two existing doors (no second implementation).

Discriminating power: every test fails if its gate/persist/delegation is
reverted (403s become 200s; handover_record vanishes; the combined door
stops recording money).
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from tests.test_order_rx_hold_deliver_guard import (  # noqa: E402,F401
    wired_orders,
    _seed_order,
)


def _cashier_headers():
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": "test-cashier-9",
            "username": "cashier9",
            "roles": ["SALES_CASHIER"],
            "store_ids": ["BV-TEST-01"],
            "active_store_id": "BV-TEST-01",
        }
    )
    return {"Authorization": f"Bearer {token}"}


def _manager_headers():
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": "test-mgr-9",
            "username": "mgr9",
            "roles": ["STORE_MANAGER"],
            "store_ids": ["BV-TEST-01"],
            "active_store_id": "BV-TEST-01",
        }
    )
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Credit-delivery gate
# ---------------------------------------------------------------------------


def test_balance_due_delivery_403_for_cashier(client, wired_orders):
    _seed_order(
        wired_orders["order_repo"],
        amount_paid=400.0,
        balance_due=600.0,
        payment_status="PARTIAL",
    )
    r = client.post("/api/v1/orders/ord-hold-1/deliver", headers=_cashier_headers())
    assert r.status_code == 403, r.text
    assert "balance" in r.json()["detail"].lower()
    doc = wired_orders["order_repo"].find_by_id("ord-hold-1")
    assert doc["status"] == "READY"  # nothing moved


def test_balance_due_delivery_allowed_for_manager(client, wired_orders):
    _seed_order(
        wired_orders["order_repo"],
        amount_paid=400.0,
        balance_due=600.0,
        payment_status="PARTIAL",
    )
    r = client.post("/api/v1/orders/ord-hold-1/deliver", headers=_manager_headers())
    assert r.status_code == 200, r.text
    assert wired_orders["order_repo"].find_by_id("ord-hold-1")["status"] == "DELIVERED"


def test_settled_order_needs_no_gate(client, wired_orders):
    _seed_order(wired_orders["order_repo"])  # PAID, balance 0
    r = client.post("/api/v1/orders/ord-hold-1/deliver", headers=_cashier_headers())
    assert r.status_code == 200, r.text


def test_bad_token_still_403(client, wired_orders, monkeypatch):
    """A token the engine refuses (wrong order/store/expired) does not open
    the door."""
    from api.routers import orders as orders_module

    class _RefusingEngine:
        def __init__(self, **_kw):
            pass

        def consume_approval(self, **_kw):
            return {"ok": False, "error": "context_mismatch"}

    import api.services.approvals as approvals_module

    monkeypatch.setattr(approvals_module, "ApprovalEngine", _RefusingEngine)
    _seed_order(
        wired_orders["order_repo"],
        amount_paid=0.0,
        balance_due=1000.0,
        payment_status="PARTIAL",
    )
    r = client.post(
        "/api/v1/orders/ord-hold-1/deliver",
        json={"approval_token": "tok-x"},
        headers=_cashier_headers(),
    )
    assert r.status_code == 403


def test_valid_token_opens_the_door_and_is_bound(client, wired_orders, monkeypatch):
    """A consumed CREDIT_DELIVERY token delivers; the gate passes the store
    and order bindings to the engine (P1-2 pattern)."""
    seen = {}

    class _OkEngine:
        def __init__(self, **_kw):
            pass

        def consume_approval(self, **kw):
            seen.update(kw)
            return {"ok": True}

    import api.services.approvals as approvals_module

    monkeypatch.setattr(approvals_module, "ApprovalEngine", _OkEngine)
    _seed_order(
        wired_orders["order_repo"],
        amount_paid=0.0,
        balance_due=1000.0,
        payment_status="PARTIAL",
    )
    r = client.post(
        "/api/v1/orders/ord-hold-1/deliver",
        json={"approval_token": "tok-ok"},
        headers=_cashier_headers(),
    )
    assert r.status_code == 200, r.text
    assert seen["action_type"] == "CREDIT_DELIVERY"
    assert seen["expected_store_id"] == "BV-TEST-01"
    assert seen["expected_context"] == {"order_id": "ord-hold-1"}


# ---------------------------------------------------------------------------
# Handover record rides the atomic claim
# ---------------------------------------------------------------------------


def test_handover_record_persisted_on_claim(client, wired_orders):
    _seed_order(wired_orders["order_repo"])
    r = client.post(
        "/api/v1/orders/ord-hold-1/deliver",
        json={
            "handover": {
                "picked_up_by_name": "Ravi (brother)",
                "fit_check_done": True,
                "notes": "left temple adjusted at pickup",
            }
        },
        headers=_manager_headers(),
    )
    assert r.status_code == 200, r.text
    doc = wired_orders["order_repo"].find_by_id("ord-hold-1")
    rec = doc["handover_record"]
    assert rec["picked_up_by_name"] == "Ravi (brother)"
    assert rec["fit_check_done"] is True
    assert rec["recorded_by"] == "test-mgr-9"
    assert "picked_up_by_phone" not in rec  # only filled fields stored


def test_bodyless_deliver_still_works(client, wired_orders):
    """Backward compatibility: the FE posts /deliver with NO body today."""
    _seed_order(wired_orders["order_repo"])
    r = client.post("/api/v1/orders/ord-hold-1/deliver", headers=_manager_headers())
    assert r.status_code == 200, r.text
    doc = wired_orders["order_repo"].find_by_id("ord-hold-1")
    assert doc["status"] == "DELIVERED"
    assert "handover_record" not in doc


# ---------------------------------------------------------------------------
# Collect-and-deliver (delegation, not duplication)
# ---------------------------------------------------------------------------


def test_deliver_with_payment_collects_then_delivers(client, wired_orders):
    # amount_paid derives from the payments ROWS (the array is the truth),
    # so the earlier deposit must exist as a row, not just a header number.
    _seed_order(
        wired_orders["order_repo"],
        amount_paid=400.0,
        balance_due=600.0,
        payment_status="PARTIAL",
        payments=[{"payment_id": "p-dep", "method": "CASH", "amount": 400.0}],
    )
    r = client.post(
        "/api/v1/orders/ord-hold-1/deliver-with-payment",
        json={
            "payment": {"method": "CASH", "amount": 600.0},
            "handover": {"picked_up_by_name": "Self"},
        },
        headers=_manager_headers(),
    )
    assert r.status_code == 200, r.text
    doc = wired_orders["order_repo"].find_by_id("ord-hold-1")
    assert doc["status"] == "DELIVERED"
    assert doc["payment_status"] == "PAID"
    assert doc["balance_due"] == 0.0
    assert doc["handover_record"]["picked_up_by_name"] == "Self"


def test_deliver_with_payment_without_payment_just_delivers(client, wired_orders):
    _seed_order(wired_orders["order_repo"])
    r = client.post(
        "/api/v1/orders/ord-hold-1/deliver-with-payment",
        json={},
        headers=_manager_headers(),
    )
    assert r.status_code == 200, r.text
    assert wired_orders["order_repo"].find_by_id("ord-hold-1")["status"] == "DELIVERED"


def test_deliver_with_payment_cashier_partial_collect_still_gated(
    client, wired_orders
):
    """Collecting only part of the balance leaves money due — the credit
    gate must still fire for a cashier inside the combined door."""
    _seed_order(
        wired_orders["order_repo"],
        amount_paid=200.0,
        balance_due=800.0,
        payment_status="PARTIAL",
        payments=[{"payment_id": "p-dep2", "method": "CASH", "amount": 200.0}],
    )
    r = client.post(
        "/api/v1/orders/ord-hold-1/deliver-with-payment",
        json={"payment": {"method": "CASH", "amount": 300.0}},
        headers=_cashier_headers(),
    )
    assert r.status_code == 403, r.text
    doc = wired_orders["order_repo"].find_by_id("ord-hold-1")
    # money recorded (by design — non-atomic), delivery refused
    assert doc["amount_paid"] == 500.0
    assert doc["status"] == "READY"
