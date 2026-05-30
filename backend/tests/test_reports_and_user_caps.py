"""
IMS 2.0 - Reports authz + role-aware user discount-cap default
==============================================================
Two least-privilege fixes from the deep-QA pass:

  1. UserCreate.discount_cap defaulted to a blanket 10.0 -> because the cap a
     user actually gets is max(role_baseline, stored_override) (services/
     role_caps.py::effective_discount_cap), that default ELEVATED zero-privilege
     roles: an ACCOUNTANT (baseline 0) created with the default ended up with an
     effective 10% discount power. Default is now None -> store the role baseline
     unless an admin deliberately supplies an override.

  2. /reports/inventory/valuation, /reports/staff/ranking and
     /reports/sales/by-salesperson were open to ANY authenticated user and took
     an arbitrary store_id (a cashier could pull another store's staff rankings /
     inventory value). They now require a management role and run through
     validate_store_access.
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import users as users_mod  # noqa: E402
from api.routers import reports as reports_mod  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


# --- 1. role-aware discount_cap default ---------------------------------


class _FakeUserRepo:
    def __init__(self):
        self.created = None

    def find_by_username(self, _u):
        return None

    def find_by_email(self, _e):
        return None

    def create(self, data):
        self.created = dict(data)
        out = dict(data)
        out["user_id"] = "U1"
        return out


def _create_user(monkeypatch, roles, **extra):
    repo = _FakeUserRepo()
    monkeypatch.setattr(users_mod, "get_user_repository", lambda: repo)
    payload = users_mod.UserCreate(
        username="newuser",
        email="new@example.com",
        password="password123",
        full_name="New User",
        roles=roles,
        store_ids=["S1"],
        **extra,
    )
    asyncio.run(users_mod.create_user(payload, current_user={"user_id": "adm", "roles": ["ADMIN"]}))
    return repo.created


def test_accountant_new_user_gets_zero_cap_not_ten(monkeypatch):
    created = _create_user(monkeypatch, ["ACCOUNTANT"])
    assert created["discount_cap"] == 0.0  # was silently 10 before the fix


def test_sales_cashier_new_user_gets_role_baseline_ten(monkeypatch):
    created = _create_user(monkeypatch, ["SALES_CASHIER"])
    assert created["discount_cap"] == 10.0


def test_store_manager_new_user_gets_twenty(monkeypatch):
    created = _create_user(monkeypatch, ["STORE_MANAGER"])
    assert created["discount_cap"] == 20.0


def test_explicit_override_is_respected(monkeypatch):
    created = _create_user(monkeypatch, ["ACCOUNTANT"], discount_cap=15)
    assert created["discount_cap"] == 15.0


# --- 2. reports role gate + store scope ---------------------------------


def _reports_client(user):
    app = FastAPI()
    app.include_router(reports_mod.router, prefix="/api/v1/reports")

    async def _u():
        return user

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


_CASHIER = {"user_id": "c", "roles": ["SALES_CASHIER"], "store_ids": ["S1"], "active_store_id": "S1"}
_MANAGER_S1 = {"user_id": "m", "roles": ["STORE_MANAGER"], "store_ids": ["S1"], "active_store_id": "S1"}


def test_cashier_blocked_from_inventory_valuation():
    assert _reports_client(_CASHIER).get("/api/v1/reports/inventory/valuation").status_code == 403


def test_cashier_blocked_from_staff_ranking():
    r = _reports_client(_CASHIER).get(
        "/api/v1/reports/staff/ranking?from_date=2026-05-01&to_date=2026-05-30"
    )
    assert r.status_code == 403


def test_cashier_blocked_from_sales_by_salesperson():
    r = _reports_client(_CASHIER).get(
        "/api/v1/reports/sales/by-salesperson?from_date=2026-05-01&to_date=2026-05-30"
    )
    assert r.status_code == 403


def test_manager_cannot_query_other_store_valuation():
    # role passes, but store scope rejects a store the manager doesn't own
    assert _reports_client(_MANAGER_S1).get(
        "/api/v1/reports/inventory/valuation?store_id=S2"
    ).status_code == 403


def test_manager_can_query_own_store_valuation():
    # role + scope both pass; no DB -> empty 200 (not a 403)
    assert _reports_client(_MANAGER_S1).get(
        "/api/v1/reports/inventory/valuation?store_id=S1"
    ).status_code == 200
