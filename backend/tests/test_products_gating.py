"""
IMS 2.0 — products router catalog-write gating
==============================================
Creating / updating catalog products had NO server-side role check — any
authenticated user could add or edit products (and their pricing) by hitting
the API directly, despite the frontend `catalog/add` route being restricted
to catalog managers. create_product + update_product are now gated to
(ADMIN, CATALOG_MANAGER); SUPERADMIN auto-passes. Reads stay open so POS /
search keep working for all roles.

End-to-end via the conftest TestClient fixtures.
"""

from __future__ import annotations

import pytest


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


_PRODUCT_BODY = {
    "sku": "SKU-T1",
    "category": "FRAME",
    "brand": "Acme",
    "model": "M1",
    "mrp": 1000.0,
    "offer_price": 900.0,
}
_UPDATE_BODY = {"brand": "NewBrand", "mrp": 1200.0}


class TestProductWriteGating:
    def test_sales_staff_blocked_create(self, client, staff_headers):
        resp = client.post("/api/v1/products", headers=staff_headers, json=_PRODUCT_BODY)
        assert resp.status_code == 403

    def test_store_manager_blocked_create(self, client):
        # Catalog is centrally managed — STORE_MANAGER is intentionally excluded.
        resp = client.post(
            "/api/v1/products", headers=_headers(["STORE_MANAGER"]), json=_PRODUCT_BODY
        )
        assert resp.status_code == 403

    def test_catalog_manager_allowed_create(self, client):
        resp = client.post(
            "/api/v1/products", headers=_headers(["CATALOG_MANAGER"]), json=_PRODUCT_BODY
        )
        assert resp.status_code != 403

    def test_admin_allowed_create(self, client):
        resp = client.post(
            "/api/v1/products", headers=_headers(["ADMIN"]), json=_PRODUCT_BODY
        )
        assert resp.status_code != 403

    def test_superadmin_allowed_create(self, client, auth_headers):
        resp = client.post("/api/v1/products", headers=auth_headers, json=_PRODUCT_BODY)
        assert resp.status_code != 403

    def test_sales_staff_blocked_update(self, client, staff_headers):
        resp = client.put(
            "/api/v1/products/p1", headers=staff_headers, json=_UPDATE_BODY
        )
        assert resp.status_code == 403

    def test_catalog_manager_allowed_update(self, client):
        resp = client.put(
            "/api/v1/products/p1", headers=_headers(["CATALOG_MANAGER"]), json=_UPDATE_BODY
        )
        assert resp.status_code != 403


class TestProductReadsStayOpen:
    def test_staff_can_list_products(self, client, staff_headers):
        assert client.get("/api/v1/products", headers=staff_headers).status_code != 403

    def test_staff_can_read_categories(self, client, staff_headers):
        resp = client.get("/api/v1/products/categories/list", headers=staff_headers)
        assert resp.status_code != 403
