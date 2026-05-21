"""
IMS 2.0 — hr + payroll router role gating
=========================================
The HR router was world-readable: any authenticated user could read leaves /
attendance / payroll lists and call approve-leave / generate-payroll directly,
even though the frontend `hr` and `hr/payroll` routes are restricted to
finance/management roles. The router is now gated to mirror those guards.

payroll's create-salary-config is admin-only and previously used a manual
`if "ADMIN" not in roles` check that wrongly rejected SUPERADMIN — now fixed
via require_roles("ADMIN") (SUPERADMIN auto-passes).

These run end-to-end against the real app via the conftest fixtures.
"""

from __future__ import annotations


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


class TestHrRouterGating:
    def test_staff_blocked_on_leaves(self, client, staff_headers):
        assert client.get("/api/v1/hr/leaves", headers=staff_headers).status_code == 403

    def test_staff_blocked_on_attendance(self, client, staff_headers):
        resp = client.get("/api/v1/hr/attendance", headers=staff_headers)
        assert resp.status_code == 403

    def test_staff_blocked_on_payroll_list(self, client, staff_headers):
        resp = client.get("/api/v1/hr/payroll", headers=staff_headers)
        assert resp.status_code == 403

    def test_accountant_allowed_on_leaves(self, client):
        resp = client.get("/api/v1/hr/leaves", headers=_headers(["ACCOUNTANT"]))
        assert resp.status_code != 403

    def test_store_manager_allowed_on_attendance(self, client):
        resp = client.get("/api/v1/hr/attendance", headers=_headers(["STORE_MANAGER"]))
        assert resp.status_code != 403

    def test_superadmin_allowed_on_payroll_list(self, client, auth_headers):
        resp = client.get("/api/v1/hr/payroll", headers=auth_headers)
        assert resp.status_code != 403

    def test_unauthenticated_rejected(self, client):
        resp = client.get("/api/v1/hr/leaves")
        assert resp.status_code in (401, 403)


_CONFIG_BODY = {"employee_id": "emp-test-1", "basic_salary": 25000.0}


class TestPayrollConfigAdminOnly:
    def test_accountant_blocked(self, client):
        # ACCOUNTANT passes the router finance gate but config is ADMIN-only.
        resp = client.post(
            "/api/v1/payroll/config", headers=_headers(["ACCOUNTANT"]), json=_CONFIG_BODY
        )
        assert resp.status_code == 403

    def test_store_manager_blocked(self, client):
        resp = client.post(
            "/api/v1/payroll/config",
            headers=_headers(["STORE_MANAGER"]),
            json=_CONFIG_BODY,
        )
        assert resp.status_code == 403

    def test_admin_allowed(self, client):
        resp = client.post(
            "/api/v1/payroll/config", headers=_headers(["ADMIN"]), json=_CONFIG_BODY
        )
        assert resp.status_code != 403

    def test_superadmin_allowed(self, client, auth_headers):
        # Regression: SUPERADMIN was previously rejected by the manual check.
        resp = client.post(
            "/api/v1/payroll/config", headers=auth_headers, json=_CONFIG_BODY
        )
        assert resp.status_code != 403

    def test_sales_staff_blocked(self, client, staff_headers):
        resp = client.post(
            "/api/v1/payroll/config", headers=staff_headers, json=_CONFIG_BODY
        )
        assert resp.status_code == 403
