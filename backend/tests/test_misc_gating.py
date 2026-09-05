"""
IMS 2.0 — misc router gating (clinical / settings / marketing)
==============================================================
Three world-writable surfaces hardened:

* clinical queue + eye-test writes (status/remove/start/complete) — gated
  to (ADMIN, STORE_MANAGER, OPTOMETRIST), mirroring the Clinical route.
  Queue ADD alone is also open to the sales roles (owner ruling 2026-09-06:
  the POS customer panel books eye tests); bare CASHIER stays out.
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


QUEUE_ADD = ("post", "/api/v1/clinical/queue")
QUEUE_ADD_BODY = {"storeId": "BV-TEST-01", "patientName": "Walk In", "customerPhone": "9999999999"}

CLINICAL_WRITES = [
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


class TestQueueAddSalesRoles:
    """Owner ruling 2026-09-06: sales staff may book an eye test (POST
    /clinical/queue) from the POS customer panel. Reverting the gate fails
    the first test; widening anything else fails the controls."""

    @pytest.mark.parametrize(
        "roles",
        [["SALES_STAFF"], ["SALES_CASHIER"], ["OPTOMETRIST"], ["STORE_MANAGER"], ["ADMIN"]],
    )
    def test_queue_add_allowed(self, client, roles):
        method, path = QUEUE_ADD
        resp = getattr(client, method)(path, headers=_headers(roles), json=QUEUE_ADD_BODY)
        assert resp.status_code == 200, resp.text
        assert resp.json()["patientName"] == "Walk In"

    def test_queue_add_blocked_for_bare_cashier(self, client):
        method, path = QUEUE_ADD
        resp = getattr(client, method)(path, headers=_headers(["CASHIER"]), json=QUEUE_ADD_BODY)
        assert resp.status_code == 403

    # CONTROL: the sales roles gained ONE door. Every other clinical write and
    # the prescription create still 403 them.
    @pytest.mark.parametrize("method,path", CLINICAL_WRITES)
    def test_sales_still_blocked_on_other_clinical_writes(self, client, method, path):
        assert getattr(client, method)(path, headers=_headers(["SALES_STAFF"])).status_code == 403

    @pytest.mark.parametrize("roles", [["SALES_STAFF"], ["SALES_CASHIER"]])
    def test_sales_still_cannot_create_prescription(self, client, roles):
        resp = client.post(
            "/api/v1/prescriptions",
            headers=_headers(roles),
            json={"customer_id": "C1", "patient_id": "P1", "sph_od": -1.0, "sph_os": -1.0},
        )
        assert resp.status_code == 403
        assert "clinical" in resp.json()["detail"].lower()

    def test_queue_add_broadens_no_grant_union(self):
        """The row stays on clinical:write rather than a carved key because that
        union ALREADY carries AUTHENTICATED (any actor may grant it) -- so adding
        the sales roles changes who-may-grant nothing. If this sentinel ever
        leaves the union, the queue row is then the broadener: carve a
        dedicated key (precedent products:qc) before merging."""
        from api.services.capabilities import capability_for, capability_roles

        assert capability_for("POST", "/api/v1/clinical/queue") == "clinical:write"
        assert "AUTHENTICATED" in capability_roles("clinical:write")


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
