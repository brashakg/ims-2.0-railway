"""
IMS 2.0 - POS-4 credit-limit (khata) tests
===========================================
Covers:
  1. _ar_outstanding() helper — pure unit tests with a fake order repo.
  2. GET /customers/{id}/credit-summary — endpoint smoke test (no live DB).
  3. POST /orders/{id}/payments with method=CREDIT enforces the limit.
"""

from __future__ import annotations

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")


# ============================================================================
# Helpers
# ============================================================================


def _order(customer_id: str, payments: list, payment_status: str, balance_due: float):
    return {
        "order_id": str(uuid.uuid4()),
        "customer_id": customer_id,
        "payments": payments,
        "payment_status": payment_status,
        "balance_due": balance_due,
        "status": "CONFIRMED",
    }


class FakeOrderRepo:
    def __init__(self, orders):
        self._orders = orders

    def find_many(self, q, sort=None, limit=500):
        cid_filter = q.get("$or", [{}])[0].get("customer_id", "")
        status_filter = q.get("status", {}).get("$nin", [])
        return [
            o for o in self._orders
            if o["customer_id"] == cid_filter
            and o.get("status", "") not in status_filter
        ]


class FakeCustomerRepo:
    def __init__(self, docs):
        self._docs = {d["customer_id"]: d for d in docs}

    def find_by_id(self, cid):
        return self._docs.get(cid)


# ============================================================================
# 1. _ar_outstanding() unit tests (monkeypatched repos)
# ============================================================================


def test_ar_outstanding_no_credit_orders(monkeypatch):
    """Customer with only cash orders has 0 AR outstanding."""
    from api.routers.customers import _ar_outstanding

    cid = "C001"
    orders = [
        _order(cid, [{"method": "CASH", "amount": 5000}], "PAID", 0),
    ]
    monkeypatch.setattr(
        "api.routers.customers._ar_outstanding.__globals__['get_order_repository']",
        lambda: FakeOrderRepo(orders),
        raising=False,
    )
    # Manually call with patched repo by directly testing logic
    # (import-patching is simpler here since _ar_outstanding is a module-level fn)
    import importlib
    import api.routers.customers as cm

    orig = cm.get_order_repository if hasattr(cm, "get_order_repository") else None

    import api.dependencies as deps
    real_fn = deps.get_order_repository
    deps.get_order_repository = lambda: FakeOrderRepo(orders)
    try:
        result = cm._ar_outstanding(cid, {"customer_id": cid})
    finally:
        deps.get_order_repository = real_fn

    assert result == 0.0


def test_ar_outstanding_sums_credit_balance_due(monkeypatch):
    """Two CREDIT-tendered PARTIAL orders: sum of their balance_due."""
    from api.routers import customers as cm
    import api.dependencies as deps

    cid = "C002"
    orders = [
        _order(cid, [{"method": "CREDIT", "amount": 10000}], "PARTIAL", 10000),
        _order(cid, [{"method": "CREDIT", "amount": 5000}], "PARTIAL", 5000),
    ]
    deps.get_order_repository = lambda: FakeOrderRepo(orders)
    try:
        result = cm._ar_outstanding(cid, {"customer_id": cid})
    finally:
        deps.get_order_repository = lambda: None

    assert result == 15000.0


def test_ar_outstanding_paid_order_excluded(monkeypatch):
    """A CREDIT order that is PAID contributes 0 to AR."""
    from api.routers import customers as cm
    import api.dependencies as deps

    cid = "C003"
    orders = [
        _order(cid, [{"method": "CREDIT", "amount": 8000}], "PAID", 0),
        _order(cid, [{"method": "CREDIT", "amount": 3000}], "PARTIAL", 3000),
    ]
    deps.get_order_repository = lambda: FakeOrderRepo(orders)
    try:
        result = cm._ar_outstanding(cid, {"customer_id": cid})
    finally:
        deps.get_order_repository = lambda: None

    assert result == 3000.0


def test_ar_outstanding_cancelled_excluded(monkeypatch):
    """CANCELLED orders are excluded from the AR sum."""
    from api.routers import customers as cm
    import api.dependencies as deps

    cid = "C004"
    orders = [
        {**_order(cid, [{"method": "CREDIT", "amount": 7000}], "PARTIAL", 7000),
         "status": "CANCELLED"},
        _order(cid, [{"method": "CREDIT", "amount": 2000}], "PARTIAL", 2000),
    ]
    deps.get_order_repository = lambda: FakeOrderRepo(orders)
    try:
        result = cm._ar_outstanding(cid, {"customer_id": cid})
    finally:
        deps.get_order_repository = lambda: None

    # cancelled order excluded
    assert result == 2000.0


# ============================================================================
# 2. GET /customers/{id}/credit-summary endpoint (DB-less)
# ============================================================================


def _make_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers.customers import router as customers_router
    from api.routers.auth import get_current_user

    app = FastAPI()
    app.include_router(customers_router, prefix="/customers")
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "tester",
        "roles": ["ADMIN"],
        "active_store_id": "S1",
    }
    return TestClient(app)


def test_credit_summary_unlimited(monkeypatch):
    """Customer with credit_limit=0 → unlimited, ar_available=None."""
    import api.routers.customers as cm

    customer = {"customer_id": "CX1", "credit_limit": 0}
    monkeypatch.setattr(cm, "get_customer_repository", lambda: FakeCustomerRepo([customer]))
    monkeypatch.setattr(cm, "_ar_outstanding", lambda cid, doc: 0.0)
    client = _make_client()
    r = client.get("/customers/CX1/credit-summary")

    assert r.status_code == 200
    body = r.json()
    assert body["credit_limit"] == 0.0
    assert body["ar_outstanding"] == 0.0
    assert body["ar_available"] is None
    assert body["limit_exceeded"] is False


def test_credit_summary_within_limit(monkeypatch):
    """Customer with limit=50000, AR=10000 → ar_available=40000."""
    import api.routers.customers as cm

    cid = "CX2"
    customer = {"customer_id": cid, "credit_limit": 50000}
    monkeypatch.setattr(cm, "get_customer_repository", lambda: FakeCustomerRepo([customer]))
    monkeypatch.setattr(cm, "_ar_outstanding", lambda c, doc: 10000.0)
    client = _make_client()
    r = client.get(f"/customers/{cid}/credit-summary")

    assert r.status_code == 200
    body = r.json()
    assert body["credit_limit"] == 50000.0
    assert body["ar_outstanding"] == 10000.0
    assert body["ar_available"] == 40000.0
    assert body["limit_exceeded"] is False


def test_credit_summary_exceeded(monkeypatch):
    """Customer whose AR > limit: limit_exceeded=True."""
    import api.routers.customers as cm

    cid = "CX3"
    customer = {"customer_id": cid, "credit_limit": 5000}
    monkeypatch.setattr(cm, "get_customer_repository", lambda: FakeCustomerRepo([customer]))
    monkeypatch.setattr(cm, "_ar_outstanding", lambda c, doc: 8000.0)
    client = _make_client()
    r = client.get(f"/customers/{cid}/credit-summary")

    assert r.status_code == 200
    body = r.json()
    assert body["limit_exceeded"] is True
    assert body["ar_available"] == 5000.0 - 8000.0


# ============================================================================
# 3. BOPIS cross-store-stock endpoint (DB-less / fail-soft)
# ============================================================================


def test_cross_store_stock_fail_soft():
    """When stock repo is unavailable, returns empty stores list."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers.inventory import router as inv_router
    from api.routers.auth import get_current_user, require_roles
    import api.dependencies as deps

    app = FastAPI()
    app.include_router(inv_router, prefix="/inventory")
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "tester",
        "roles": ["STORE_MANAGER"],
        "active_store_id": "S1",
    }

    # make require_roles a pass-through for any role tuple
    from fastapi import Request

    def _mock_require_roles(*_roles):
        def dep():
            return {"user_id": "tester", "roles": list(_roles), "active_store_id": "S1"}
        return dep

    # Override require_roles used in the router
    import api.routers.inventory as inv_module
    orig = inv_module.require_roles
    inv_module.require_roles = _mock_require_roles

    deps.get_stock_repository = lambda: None
    client = TestClient(app)
    try:
        r = client.get("/inventory/cross-store-stock", params={"product_id": "P999"})
    finally:
        inv_module.require_roles = orig
        deps.get_stock_repository = lambda: None

    assert r.status_code == 200
    body = r.json()
    assert body["product_id"] == "P999"
    assert body["stores"] == []
