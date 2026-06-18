"""
IMS 2.0 — inventory router stock-write gating
=============================================
Stock mutations (add stock, stock-count start/record/complete/scan, transfers,
serial create/update) had NO server-side role check — any authenticated user
could adjust stock by hitting the API directly. The write endpoints are now
gated to the inventory page's role set (ADMIN, AREA_MANAGER, STORE_MANAGER,
CATALOG_MANAGER, WORKSHOP_STAFF; SUPERADMIN auto-passes), which blocks the
non-inventory roles (SALES_STAFF, SALES_CASHIER, CASHIER, OPTOMETRIST,
ACCOUNTANT). Reads stay open so POS / search keep working.

FastAPI resolves the role dependency before body validation, so blocked roles
return 403 even without a request body — these tests send headers only.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(app):
    """Module-scoped client WITHOUT lifespan startup.

    Gating is enforced by a route dependency, so we don't need the app's
    startup (DB init, agent registry) — skipping it makes this matrix fast.
    Endpoints handle the no-DB path gracefully and the role gate runs first.
    """
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


# (method, path)
WRITES = [
    ("post", "/api/v1/inventory/stock/add"),
    ("post", "/api/v1/inventory/opening-stock/preview"),
    ("post", "/api/v1/inventory/opening-stock/commit"),
    ("post", "/api/v1/inventory/stock-count/start"),
    ("post", "/api/v1/inventory/stock-count/c1/items"),
    ("post", "/api/v1/inventory/stock-count/c1/complete"),
    ("post", "/api/v1/inventory/transfers"),
    # NOTE: the /inventory/transfers/{id}/send|receive stub endpoints were
    # REMOVED (dead stubs that returned fake success and moved no stock) -- the
    # real transfer ship/receive lives at /api/v1/transfers/{id}/ship|receive
    # (gated + tested in test_idor_transfers / test_transfer_stock_movement).
    ("post", "/api/v1/inventory/stock-count-scan"),
    ("post", "/api/v1/inventory/serials"),
    ("patch", "/api/v1/inventory/serials/s1"),
]

BLOCKED_ROLES = [["SALES_STAFF"], ["CASHIER"], ["OPTOMETRIST"], ["ACCOUNTANT"]]
ALLOWED_ROLES = [["STORE_MANAGER"], ["CATALOG_MANAGER"], ["WORKSHOP_STAFF"]]


class TestInventoryWriteGating:
    @pytest.mark.parametrize("method,path", WRITES)
    @pytest.mark.parametrize("roles", BLOCKED_ROLES)
    def test_non_inventory_roles_blocked(self, client, method, path, roles):
        resp = getattr(client, method)(path, headers=_headers(roles))
        assert resp.status_code == 403

    @pytest.mark.parametrize("method,path", WRITES)
    @pytest.mark.parametrize("roles", ALLOWED_ROLES)
    def test_inventory_roles_allowed(self, client, method, path, roles):
        resp = getattr(client, method)(path, headers=_headers(roles))
        assert resp.status_code != 403

    @pytest.mark.parametrize("method,path", WRITES)
    def test_superadmin_allowed(self, client, auth_headers, method, path):
        resp = getattr(client, method)(path, headers=auth_headers)
        assert resp.status_code != 403


class TestInventoryReadsStayOpen:
    def test_staff_can_read_stock(self, client, staff_headers):
        # Reads stay open — POS needs stock availability for all sales roles.
        assert client.get("/api/v1/inventory/stock", headers=staff_headers).status_code != 403

    def test_staff_can_list_serials(self, client, staff_headers):
        resp = client.get("/api/v1/inventory/serials", headers=staff_headers)
        assert resp.status_code != 403
