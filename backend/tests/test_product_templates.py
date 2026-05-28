"""
IMS 2.0 - product templates router (Phase C of product-add redesign, #143)
===========================================================================
Covers the /api/v1/product-templates CRUD surface:
  - role gating mirrors products.py _CATALOG_ROLES (ADMIN / CATALOG_MANAGER;
    SUPERADMIN auto-passes). Other roles get 403.
  - payload-size guard (422) and blank-name guard.
  - delete-permission shape.

The MongoDB-backed happy path (insert + read-back) only runs when a DB is
reachable; locally the app runs in stub mode (no DB) so those writes return 503
and are skipped. On CI (mongo:7.0) the full round-trip exercises the collection.

End-to-end via the conftest TestClient fixtures.
"""

from __future__ import annotations


def _headers(roles):
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": "tpl-user-1",
            "username": "tpluser",
            "roles": roles,
            "store_ids": ["BV-TEST-01"],
            "active_store_id": "BV-TEST-01",
        }
    )
    return {"Authorization": f"Bearer {token}"}


_TEMPLATE_BODY = {
    "name": "Ray-Ban Aviator base",
    "category": "SG",
    "payload": {
        "category": "SG",
        "attributes": {"brand_name": "Ray-Ban", "model_no": "RB3025"},
        "gstRate": "18",
        "mrp": "5000",
        "discountCategory": "PREMIUM",
    },
}


# ---------------------------------------------------------------------------
# Role gating
# ---------------------------------------------------------------------------
class TestTemplateGating:
    def test_sales_staff_blocked_list(self, client, staff_headers):
        resp = client.get("/api/v1/product-templates", headers=staff_headers)
        assert resp.status_code == 403

    def test_sales_staff_blocked_create(self, client, staff_headers):
        resp = client.post(
            "/api/v1/product-templates", headers=staff_headers, json=_TEMPLATE_BODY
        )
        assert resp.status_code == 403

    def test_store_manager_blocked_create(self, client):
        # Catalog is centrally managed - STORE_MANAGER is intentionally excluded.
        resp = client.post(
            "/api/v1/product-templates",
            headers=_headers(["STORE_MANAGER"]),
            json=_TEMPLATE_BODY,
        )
        assert resp.status_code == 403

    def test_sales_staff_blocked_delete(self, client, staff_headers):
        resp = client.delete("/api/v1/product-templates/nope", headers=staff_headers)
        assert resp.status_code == 403

    def test_catalog_manager_allowed_list(self, client):
        resp = client.get(
            "/api/v1/product-templates", headers=_headers(["CATALOG_MANAGER"])
        )
        assert resp.status_code != 403

    def test_admin_allowed_list(self, client):
        resp = client.get("/api/v1/product-templates", headers=_headers(["ADMIN"]))
        assert resp.status_code != 403

    def test_superadmin_allowed_create(self, client, auth_headers):
        # SUPERADMIN auto-passes the role gate (may 503 if no DB; never 403).
        resp = client.post(
            "/api/v1/product-templates", headers=auth_headers, json=_TEMPLATE_BODY
        )
        assert resp.status_code != 403


# ---------------------------------------------------------------------------
# List shape (works in stub mode too - returns empty envelope)
# ---------------------------------------------------------------------------
class TestTemplateListShape:
    def test_list_returns_envelope(self, client, auth_headers):
        resp = client.get("/api/v1/product-templates", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "templates" in data
        assert "total" in data
        assert isinstance(data["templates"], list)


# ---------------------------------------------------------------------------
# Validation guards. When a DB is present these assert the 422s; in stub mode
# the create returns 503 before validation, so we skip the assertion.
# ---------------------------------------------------------------------------
class TestTemplateValidation:
    def test_blank_name_rejected_or_no_db(self, client, auth_headers):
        body = dict(_TEMPLATE_BODY, name="   ")
        resp = client.post("/api/v1/product-templates", headers=auth_headers, json=body)
        # pydantic min_length=1 trims nothing, but the explicit strip-check makes
        # an all-whitespace name a 422. No DB -> 503. Either way, not created.
        assert resp.status_code in (422, 503)

    def test_oversized_payload_rejected_or_no_db(self, client, auth_headers):
        big = {f"k{i}": str(i) for i in range(200)}
        body = dict(_TEMPLATE_BODY, payload=big)
        resp = client.post("/api/v1/product-templates", headers=auth_headers, json=body)
        assert resp.status_code in (422, 503)

    def test_missing_payload_is_422(self, client, auth_headers):
        # `payload` is required by the schema -> pydantic 422 regardless of DB.
        resp = client.post(
            "/api/v1/product-templates",
            headers=auth_headers,
            json={"name": "no payload"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Full round-trip - only meaningful with a DB (CI mongo:7.0). Skipped in stub.
# ---------------------------------------------------------------------------
class TestTemplateRoundTrip:
    def test_create_list_delete_when_db_present(self, client, auth_headers):
        create = client.post(
            "/api/v1/product-templates", headers=auth_headers, json=_TEMPLATE_BODY
        )
        if create.status_code == 503:
            # No DB in this environment (local stub mode) - nothing to round-trip.
            return
        assert create.status_code == 201
        created = create.json()
        tid = created["template_id"]
        assert created["name"] == _TEMPLATE_BODY["name"]
        assert created["category"] == "SG"
        assert created["payload"]["attributes"]["brand_name"] == "Ray-Ban"

        # It should now appear in the list.
        listing = client.get("/api/v1/product-templates", headers=auth_headers).json()
        assert any(t["template_id"] == tid for t in listing["templates"])

        # Owner can delete it.
        delete = client.delete(f"/api/v1/product-templates/{tid}", headers=auth_headers)
        assert delete.status_code == 200
        assert delete.json()["deleted"] is True

        # Deleting a missing template is a 404.
        missing = client.delete(
            f"/api/v1/product-templates/{tid}", headers=auth_headers
        )
        assert missing.status_code == 404
