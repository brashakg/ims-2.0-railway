"""
IMS 2.0 — reports router financial-report gating
================================================
Financial reports (P&L by store/category, GST returns, outstanding payments,
discount analysis, expense-vs-revenue) had NO server-side role check — any
authenticated user could read company financials by hitting /reports/* directly.
The 8 financial endpoints are now gated to (ADMIN, AREA_MANAGER, STORE_MANAGER,
ACCOUNTANT; SUPERADMIN auto-passes), mirroring the frontend Reports route.

CRITICAL: /reports/dashboard and /reports/targets MUST stay open — the Hub
calls them for every role. These tests assert that explicitly.

FastAPI resolves the role dependency before query-param validation, so blocked
roles get 403 even without the required from_date/to_date/month params.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(app):
    """Module-scoped client without lifespan — gating is a route dependency."""
    return TestClient(app)


def _headers(roles):
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": "t-1",
            "username": "t",
            "roles": roles,
            "store_ids": ["BV-TEST-01"],
            "active_store_id": "BV-TEST-01",
        }
    )
    return {"Authorization": f"Bearer {token}"}


FINANCIAL = [
    "/api/v1/reports/finance/outstanding",
    "/api/v1/reports/finance/gst",
    "/api/v1/reports/profit/by-category",
    "/api/v1/reports/profit/by-store",
    "/api/v1/reports/discount/analysis",
    "/api/v1/reports/finance/expense-vs-revenue",
    "/api/v1/reports/gstr1",
]

# Owner ruling 2026-10-07 (R1): GSTR-3B adds up input tax from supplier bills
# (Table 4 ITC, RCM) -- the accounts roles only; managers keep the rest.
INPUT_TAX = ["/api/v1/reports/gstr3b", "/api/v1/reports/gstr3b/gstn-json"]

BLOCKED_ROLES = [["SALES_STAFF"], ["CASHIER"], ["OPTOMETRIST"]]
ALLOWED_ROLES = [["ACCOUNTANT"], ["STORE_MANAGER"], ["AREA_MANAGER"]]

# Hub uses these for ALL roles — they must never be gated.
OPEN = ["/api/v1/reports/dashboard", "/api/v1/reports/targets"]


class TestFinancialReportGating:
    @pytest.mark.parametrize("path", FINANCIAL)
    @pytest.mark.parametrize("roles", BLOCKED_ROLES)
    def test_non_finance_roles_blocked(self, client, path, roles):
        assert client.get(path, headers=_headers(roles)).status_code == 403

    @pytest.mark.parametrize("path", FINANCIAL)
    @pytest.mark.parametrize("roles", ALLOWED_ROLES)
    def test_finance_roles_allowed(self, client, path, roles):
        assert client.get(path, headers=_headers(roles)).status_code != 403

    @pytest.mark.parametrize("path", FINANCIAL)
    def test_superadmin_allowed(self, client, auth_headers, path):
        assert client.get(path, headers=auth_headers).status_code != 403


class TestInputTaxReportIsAccountsOnly:
    @pytest.mark.parametrize("path", INPUT_TAX)
    @pytest.mark.parametrize("roles", BLOCKED_ROLES + [["STORE_MANAGER"], ["AREA_MANAGER"]])
    def test_managers_and_counter_blocked(self, client, path, roles):
        assert client.get(path, headers=_headers(roles)).status_code == 403

    @pytest.mark.parametrize("path", INPUT_TAX)
    def test_accounts_allowed(self, client, auth_headers, path):
        assert client.get(path, headers=_headers(["ACCOUNTANT"])).status_code != 403
        assert client.get(path, headers=auth_headers).status_code != 403


class TestHubReportsStayOpen:
    @pytest.mark.parametrize("path", OPEN)
    @pytest.mark.parametrize(
        "roles", [["SALES_STAFF"], ["CASHIER"], ["OPTOMETRIST"], ["WORKSHOP_STAFF"]]
    )
    def test_dashboard_and_targets_open_for_all_roles(self, client, path, roles):
        # Regression guard: Hub depends on these for every role.
        assert client.get(path, headers=_headers(roles)).status_code != 403
