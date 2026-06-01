"""
IMS 2.0 - Privilege-gap fixes (from the RBAC policy-matrix audit)
================================================================
Endpoints that mint customer money (loyalty / store credit), send outbound
messages (WhatsApp/SMS spend), or mutate store configuration were reachable by
ANY authenticated user (only get_current_user). They are now gated:
  - loyalty/add + store-credit/add -> _CREDIT_ROLES (matches issue/redeem siblings)
  - marketing notifications/send   -> _BULK_SEND_ROLES (matches send-bulk)
  - store categories enable/disable -> HQ only (Admin/Superadmin) per SYSTEM_INTENT 11
All gates fire before any DB access.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import customers, marketing, stores  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


def _client(router, prefix, roles):
    app = FastAPI()
    app.include_router(router, prefix=prefix)

    async def _u():
        return {"user_id": "u1", "full_name": "T", "roles": roles,
                "store_ids": ["S1"], "active_store_id": "S1"}

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


_NOTIF = {"customer_id": "C1", "customer_phone": "9000000000",
          "customer_name": "X", "template_id": "t1"}


# --- customer money: loyalty + store credit (_CREDIT_ROLES) -------------

def test_loyalty_add_denied_for_sales_staff():
    assert _client(customers.router, "/api/v1/customers", ["SALES_STAFF"]).post(
        "/api/v1/customers/C1/loyalty/add", params={"points": 10}).status_code == 403


def test_loyalty_add_allowed_for_manager():
    assert _client(customers.router, "/api/v1/customers", ["STORE_MANAGER"]).post(
        "/api/v1/customers/C1/loyalty/add", params={"points": 10}).status_code != 403


def test_store_credit_add_denied_for_sales_staff():
    assert _client(customers.router, "/api/v1/customers", ["SALES_STAFF"]).post(
        "/api/v1/customers/C1/store-credit/add", params={"amount": 100}).status_code == 403


def test_store_credit_add_allowed_for_accountant():
    assert _client(customers.router, "/api/v1/customers", ["ACCOUNTANT"]).post(
        "/api/v1/customers/C1/store-credit/add", params={"amount": 100}).status_code != 403


# --- marketing outbound send (_BULK_SEND_ROLES) ------------------------

def test_marketing_send_denied_for_sales_staff():
    assert _client(marketing.router, "/api/v1/marketing", ["SALES_STAFF"]).post(
        "/api/v1/marketing/notifications/send", json=_NOTIF).status_code == 403


def test_marketing_send_allowed_for_manager(monkeypatch):
    async def _fake_send(**_kwargs):
        return {"status": "queued"}

    monkeypatch.setattr(marketing, "send_notification", _fake_send)
    monkeypatch.setattr(marketing, "_check_notification_rate", lambda *_a, **_k: None)
    # This asserts the ROLE gate allows a manager; keep it time-independent by
    # forcing the promo quiet-hours window (council S18) open. The window itself
    # is tested in test_marketing_quiet_hours.py.
    from agents import quiet_hours as _qh

    monkeypatch.setattr(_qh, "in_quiet_hours", lambda now=None: False)
    r = _client(marketing.router, "/api/v1/marketing", ["STORE_MANAGER"]).post(
        "/api/v1/marketing/notifications/send", json=_NOTIF)
    assert r.status_code == 200


# --- store config: categories are HQ-only (SYSTEM_INTENT 11) -----------

def test_enable_category_denied_for_store_manager():
    # store config is HQ-only -> even a STORE_MANAGER is blocked
    assert _client(stores.router, "/api/v1/stores", ["STORE_MANAGER"]).post(
        "/api/v1/stores/S1/categories/FRAME").status_code == 403


def test_enable_category_allowed_for_admin():
    assert _client(stores.router, "/api/v1/stores", ["ADMIN"]).post(
        "/api/v1/stores/S1/categories/FRAME").status_code != 403


def test_disable_category_denied_for_store_manager():
    assert _client(stores.router, "/api/v1/stores", ["STORE_MANAGER"]).delete(
        "/api/v1/stores/S1/categories/FRAME").status_code == 403
