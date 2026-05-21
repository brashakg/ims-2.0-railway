"""
IMS 2.0 — misc router gating (clinical / settings / marketing)
==============================================================
Three world-writable surfaces hardened:

* clinical queue + eye-test writes (add/status/remove/start/complete) — gated
  to (ADMIN, STORE_MANAGER, OPTOMETRIST), mirroring the Clinical route.
* settings notification-provider config (holds SMS/WhatsApp API credentials) —
  gated to ADMIN only, mirroring the SettingsPage Notifications tab guard.
* marketing bulk notification fan-out (mass customer messaging) — gated to
  (ADMIN, AREA_MANAGER, STORE_MANAGER).

SUPERADMIN auto-passes everywhere. FastAPI resolves the role dependency before
body/param validation, so blocked roles return 403 without a request body.
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


CLINICAL_WRITES = [
    ("post", "/api/v1/clinical/queue"),
    ("patch", "/api/v1/clinical/queue/q1/status"),
    ("delete", "/api/v1/clinical/queue/q1"),
    ("post", "/api/v1/clinical/queue/q1/start-test"),
    ("post", "/api/v1/clinical/tests/t1/complete"),
]


class TestClinicalWriteGating:
    @pytest.mark.parametrize("method,path", CLINICAL_WRITES)
    @pytest.mark.parametrize("roles", [["SALES_STAFF"], ["CASHIER"]])
    def test_non_clinical_roles_blocked(self, client, method, path, roles):
        assert getattr(client, method)(path, headers=_headers(roles)).status_code == 403

    @pytest.mark.parametrize("method,path", CLINICAL_WRITES)
    @pytest.mark.parametrize("roles", [["OPTOMETRIST"], ["STORE_MANAGER"]])
    def test_clinical_roles_allowed(self, client, method, path, roles):
        assert getattr(client, method)(path, headers=_headers(roles)).status_code != 403

    @pytest.mark.parametrize("method,path", CLINICAL_WRITES)
    def test_superadmin_allowed(self, client, auth_headers, method, path):
        assert getattr(client, method)(path, headers=auth_headers).status_code != 403


class TestNotificationProvidersAdminOnly:
    PATH = "/api/v1/settings/notifications/providers"

    @pytest.mark.parametrize("roles", [["STORE_MANAGER"], ["ACCOUNTANT"], ["SALES_STAFF"]])
    def test_non_admin_blocked_read(self, client, roles):
        assert client.get(self.PATH, headers=_headers(roles)).status_code == 403

    @pytest.mark.parametrize("roles", [["STORE_MANAGER"], ["CATALOG_MANAGER"]])
    def test_non_admin_blocked_write(self, client, roles):
        assert client.put(self.PATH, headers=_headers(roles), json={}).status_code == 403

    def test_admin_allowed_read(self, client):
        assert client.get(self.PATH, headers=_headers(["ADMIN"])).status_code != 403

    def test_superadmin_allowed_write(self, client, auth_headers):
        assert client.put(self.PATH, headers=auth_headers, json={}).status_code != 403


class TestMarketingBulkSendGating:
    PATH = "/api/v1/marketing/notifications/send-bulk"

    @pytest.mark.parametrize("roles", [["SALES_STAFF"], ["OPTOMETRIST"], ["CASHIER"]])
    def test_non_manager_blocked(self, client, roles):
        assert client.post(self.PATH, headers=_headers(roles)).status_code == 403

    @pytest.mark.parametrize("roles", [["STORE_MANAGER"], ["AREA_MANAGER"], ["ADMIN"]])
    def test_manager_allowed(self, client, roles):
        assert client.post(self.PATH, headers=_headers(roles)).status_code != 403

    def test_superadmin_allowed(self, client, auth_headers):
        assert client.post(self.PATH, headers=auth_headers).status_code != 403
