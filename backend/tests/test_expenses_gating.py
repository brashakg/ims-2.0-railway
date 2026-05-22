"""
IMS 2.0 — expenses router role gating
=====================================
Approving / rejecting / disbursing / settling expenses and advances must be
restricted to finance roles (ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT;
SUPERADMIN auto-passes). Regular staff may still create / submit / list their
own expenses. These tests mount the router on a bare app and override the auth
dependency to assert the gate per endpoint without needing a database.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import expenses  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


def _client_as(roles):
    app = FastAPI()
    app.include_router(expenses.router, prefix="/expenses")

    async def _fake_user():
        return {
            "user_id": "u1",
            "full_name": "Test User",
            "active_store_id": "store-001",
            "roles": roles,
        }

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


# Endpoints that mutate approval/disbursement state — must be gated to finance.
GATED = [
    ("post", "/expenses/e1/approve", None),
    ("post", "/expenses/e1/reject", {"reason": "duplicate"}),
    ("post", "/expenses/e1/send-to-accountant", None),
    ("post", "/expenses/e1/mark-entered", None),
    ("get", "/expenses/to-enter", None),
    ("get", "/expenses/pending-approval", None),
    ("post", "/expenses/advances/a1/approve", None),
    ("post", "/expenses/advances/a1/disburse", {"reference": "TXN-1"}),
    ("post", "/expenses/advances/a1/settle", None),
]


class TestExpensesGating:
    @pytest.mark.parametrize("method,path,params", GATED)
    def test_sales_staff_blocked(self, method, path, params):
        client = _client_as(["SALES_STAFF"])
        resp = getattr(client, method)(path, params=params)
        assert resp.status_code == 403

    @pytest.mark.parametrize("method,path,params", GATED)
    def test_cashier_blocked(self, method, path, params):
        client = _client_as(["CASHIER"])
        resp = getattr(client, method)(path, params=params)
        assert resp.status_code == 403

    @pytest.mark.parametrize("method,path,params", GATED)
    def test_accountant_allowed(self, method, path, params):
        client = _client_as(["ACCOUNTANT"])
        resp = getattr(client, method)(path, params=params)
        assert resp.status_code != 403

    @pytest.mark.parametrize("method,path,params", GATED)
    def test_superadmin_allowed(self, method, path, params):
        client = _client_as(["SUPERADMIN"])
        resp = getattr(client, method)(path, params=params)
        assert resp.status_code != 403

    def test_staff_can_still_create_expense(self):
        client = _client_as(["SALES_STAFF"])
        resp = client.post(
            "/expenses",
            json={
                "category": "TRAVEL",
                "amount": 100.0,
                "description": "cab fare",
                "expense_date": "2026-05-21",
            },
        )
        assert resp.status_code != 403

    def test_staff_can_still_list_expenses(self):
        client = _client_as(["SALES_STAFF"])
        resp = client.get("/expenses")
        assert resp.status_code != 403

    def test_staff_can_still_submit_expense(self):
        client = _client_as(["SALES_STAFF"])
        resp = client.post("/expenses/e1/submit")
        assert resp.status_code != 403


class _FakeRepo:
    """Records the filter passed to find_many so we can assert ownership scope."""

    def __init__(self):
        self.last_filter = None

    def find_many(self, filter_dict, *args, **kwargs):
        self.last_filter = filter_dict
        return []


class TestExpenseOwnershipScope:
    """A normal user sees only their own expenses; admins see all."""

    def _client_with_repo(self, monkeypatch, roles, repo):
        app = FastAPI()
        app.include_router(expenses.router, prefix="/expenses")

        async def _fake_user():
            return {
                "user_id": "u1",
                "full_name": "Test User",
                "active_store_id": "store-001",
                "roles": roles,
            }

        app.dependency_overrides[get_current_user] = _fake_user
        monkeypatch.setattr(expenses, "get_expense_repository", lambda: repo)
        return TestClient(app)

    def test_non_admin_scoped_to_own(self, monkeypatch):
        repo = _FakeRepo()
        client = self._client_with_repo(monkeypatch, ["SALES_STAFF"], repo)
        resp = client.get("/expenses")
        assert resp.status_code == 200
        assert repo.last_filter == {"employee_id": "u1"}

    def test_admin_sees_all(self, monkeypatch):
        repo = _FakeRepo()
        client = self._client_with_repo(monkeypatch, ["ADMIN"], repo)
        resp = client.get("/expenses")
        assert resp.status_code == 200
        # No employee_id restriction for admins.
        assert "employee_id" not in (repo.last_filter or {})
