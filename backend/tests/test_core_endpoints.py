"""
IMS 2.0 - Core Endpoint Tests
================================
Smoke tests for critical API endpoints: health, auth, orders, prescriptions.
These tests run against the app with no DB — they verify routing, validation,
auth guards, and error handling.
"""

import pytest


class TestHealthCheck:
    """Health endpoint should always respond."""

    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "database" in data

    def test_root_returns_welcome(self, client):
        resp = client.get("/")
        assert resp.status_code == 200


class TestAuthEndpoints:
    """Authentication: login, logout, me, token refresh."""

    def test_login_missing_fields_returns_422(self, client):
        resp = client.post("/api/v1/auth/login", json={})
        assert resp.status_code == 422

    def test_login_short_password_returns_422(self, client):
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "ab"},
        )
        assert resp.status_code == 422

    def test_me_without_token_returns_401(self, client):
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    def test_me_with_valid_token(self, client, auth_headers):
        resp = client.get("/api/v1/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "testadmin"
        assert "SUPERADMIN" in data["roles"]

    def test_logout_revokes_token(self, client):
        """After logout, the same token should be rejected."""
        from api.routers.auth import create_access_token
        token = create_access_token({
            "user_id": "logout-test",
            "username": "logoutuser",
            "roles": ["SALES_STAFF"],
            "store_ids": ["BV-TEST-01"],
            "active_store_id": "BV-TEST-01",
        })
        headers = {"Authorization": f"Bearer {token}"}

        # Token works before logout
        resp = client.get("/api/v1/auth/me", headers=headers)
        assert resp.status_code == 200

        # Logout
        resp = client.post("/api/v1/auth/logout", headers=headers)
        assert resp.status_code == 200

        # Token should now be rejected
        resp = client.get("/api/v1/auth/me", headers=headers)
        assert resp.status_code == 401

    def test_refresh_with_valid_token(self, client, auth_headers):
        token = auth_headers["Authorization"].replace("Bearer ", "")
        resp = client.post("/api/v1/auth/refresh", json={"token": token})
        assert resp.status_code == 200
        assert "access_token" in resp.json()


class TestOrderEndpoints:
    """Orders: creation validation, auth guards."""

    def test_create_order_requires_auth(self, client):
        resp = client.post("/api/v1/orders", json={})
        assert resp.status_code == 401

    def test_create_order_empty_items_rejected(self, client, auth_headers):
        resp = client.post(
            "/api/v1/orders",
            json={"customer_id": "cust-1", "items": []},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "at least one item" in resp.json()["detail"]

    def test_create_order_exceeds_cart_limit(self, client, auth_headers):
        """Cart limit is 15 items."""
        items = [
            {"product_id": f"prod-{i}", "unit_price": 100, "quantity": 1, "item_type": "FRAME"}
            for i in range(16)
        ]
        resp = client.post(
            "/api/v1/orders",
            json={"customer_id": "cust-1", "items": items},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "15" in resp.json()["detail"]


class TestPrescriptionEndpoints:
    """Prescriptions: role guards, Rx validation."""

    def test_create_rx_requires_auth(self, client):
        resp = client.post("/api/v1/prescriptions", json={})
        assert resp.status_code == 401

    def test_create_rx_blocked_for_sales_staff(self, client, staff_headers):
        """SALES_STAFF should not be able to create prescriptions.

        EyeData fields (sph/cyl/add) are strings, not floats — Rx values
        carry sign prefixes like '-2.00' that need to round-trip exactly
        through the DB. Pydantic v2 doesn't coerce float → str, so passing
        numeric JSON would 422 before the role gate ever runs.
        """
        resp = client.post(
            "/api/v1/prescriptions",
            json={
                "customer_id": "cust-1",
                "patient_id": "pat-1",
                "source": "TESTED_AT_STORE",
                "right_eye": {"sph": "-2.00", "cyl": "-0.50", "axis": 90},
                "left_eye": {"sph": "-1.50", "cyl": "-0.75", "axis": 85},
            },
            headers=staff_headers,
        )
        # Should be 403 because SALES_STAFF lacks clinical access
        assert resp.status_code == 403
        assert "clinical" in resp.json()["detail"].lower()


class TestSecurityHeaders:
    """Every response should include security headers."""

    def test_health_has_security_headers(self, client):
        resp = client.get("/health")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert "strict-origin" in resp.headers.get("Referrer-Policy", "")

    def test_error_response_does_not_leak_details(self, client):
        """500 errors should not expose internal stack traces."""
        resp = client.get("/health")  # this won't 500, but check the pattern
        # Hit a non-existent endpoint
        resp = client.get("/api/v1/nonexistent")
        assert resp.status_code in (404, 405)
        body = resp.json()
        # Should not contain Python traceback indicators
        assert "Traceback" not in str(body)
        assert "File \"/" not in str(body)
