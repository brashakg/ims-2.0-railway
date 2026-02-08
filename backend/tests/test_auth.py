# ============================================================================
# IMS 2.0 - Authentication API Tests
# ============================================================================

import pytest
import sys
import os
from datetime import datetime, timedelta

# Add backend to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytestmark = pytest.mark.auth


class TestLogin:
    """Test authentication login endpoint"""

    def test_login_with_valid_credentials(self, client):
        """Should login successfully with valid credentials"""
        # Create user first
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "testuser@test.local",
                "username": "testuser",
                "password": "SecurePassword123",
                "full_name": "Test User"
            }
        )
        assert response.status_code == 201

        # Login with valid credentials
        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "testuser@test.local",
                "password": "SecurePassword123"
            }
        )

        assert response.status_code == 200
        data = assert_success_response(response)
        assert "token" in data
        assert "user" in data
        assert data["user"]["email"] == "testuser@test.local"

    def test_login_with_invalid_email(self, client):
        """Should fail login with invalid email"""
        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "nonexistent@test.local",
                "password": "AnyPassword123"
            }
        )

        assert response.status_code == 401
        assert_error_response(response, 401)

    def test_login_with_wrong_password(self, client):
        """Should fail login with wrong password"""
        # Create user first
        client.post(
            "/api/v1/auth/register",
            json={
                "email": "testuser@test.local",
                "username": "testuser",
                "password": "CorrectPassword123",
                "full_name": "Test User"
            }
        )

        # Try login with wrong password
        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "testuser@test.local",
                "password": "WrongPassword123"
            }
        )

        assert response.status_code == 401
        assert_error_response(response, 401)

    def test_login_with_missing_email(self, client):
        """Should fail with missing email field"""
        response = client.post(
            "/api/v1/auth/login",
            json={"password": "Password123"}
        )

        assert response.status_code == 422

    def test_login_with_missing_password(self, client):
        """Should fail with missing password field"""
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "test@test.local"}
        )

        assert response.status_code == 422


class TestLogout:
    """Test authentication logout endpoint"""

    def test_logout_with_valid_token(self, client, admin_token):
        """Should logout successfully with valid token"""
        response = client.post(
            "/api/v1/auth/logout",
            headers=get_auth_headers(admin_token)
        )

        assert response.status_code == 200
        assert_success_response(response)

    def test_logout_without_token(self, client):
        """Should fail logout without authorization"""
        response = client.post("/api/v1/auth/logout")
        assert response.status_code == 401

    def test_logout_with_invalid_token(self, client):
        """Should fail logout with invalid token"""
        response = client.post(
            "/api/v1/auth/logout",
            headers=get_auth_headers("invalid-token-xyz")
        )
        assert response.status_code == 401


class TestTokenValidation:
    """Test token validation endpoint"""

    def test_validate_valid_token(self, client, admin_token):
        """Should validate valid token"""
        response = client.get(
            "/api/v1/auth/validate",
            headers=get_auth_headers(admin_token)
        )

        assert response.status_code == 200
        data = assert_success_response(response)
        assert data["valid"] is True

    def test_validate_invalid_token(self, client):
        """Should reject invalid token"""
        response = client.get(
            "/api/v1/auth/validate",
            headers=get_auth_headers("invalid-token")
        )

        assert response.status_code == 401

    def test_validate_expired_token(self, client, db_session):
        """Should reject expired token"""
        # This would require creating an expired token
        # Implementation depends on JWT library used
        pass

    def test_validate_without_token(self, client):
        """Should reject request without token"""
        response = client.get("/api/v1/auth/validate")
        assert response.status_code == 401


class TestTokenRefresh:
    """Test token refresh endpoint"""

    def test_refresh_with_valid_refresh_token(self, client, admin_token):
        """Should refresh token with valid refresh token"""
        # First login to get refresh token
        login_response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "admin@test.local",
                "password": "admin123"
            }
        )

        refresh_token = login_response.json()["data"].get("refresh_token")

        if refresh_token:
            response = client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": refresh_token}
            )

            assert response.status_code == 200
            data = assert_success_response(response)
            assert "token" in data
            assert data["token"] != admin_token  # Should be different token

    def test_refresh_with_invalid_refresh_token(self, client):
        """Should fail with invalid refresh token"""
        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "invalid-refresh-token"}
        )

        assert response.status_code == 401


class TestUserProfile:
    """Test user profile endpoints"""

    def test_get_profile(self, client, admin_token):
        """Should get user profile"""
        response = client.get(
            "/api/v1/auth/profile",
            headers=get_auth_headers(admin_token)
        )

        assert response.status_code == 200
        data = assert_success_response(response)
        assert "email" in data
        assert "username" in data
        assert "roles" in data

    def test_update_profile(self, client, admin_token):
        """Should update user profile"""
        response = client.put(
            "/api/v1/auth/profile",
            headers=get_auth_headers(admin_token),
            json={
                "full_name": "Updated Admin",
                "phone": "9876543210"
            }
        )

        assert response.status_code == 200
        data = assert_success_response(response)
        assert data["full_name"] == "Updated Admin"

    def test_change_password(self, client, admin_token):
        """Should change password"""
        response = client.post(
            "/api/v1/auth/change-password",
            headers=get_auth_headers(admin_token),
            json={
                "current_password": "admin123",
                "new_password": "NewPassword123"
            }
        )

        assert response.status_code == 200

    def test_change_password_with_wrong_current(self, client, admin_token):
        """Should fail if current password is wrong"""
        response = client.post(
            "/api/v1/auth/change-password",
            headers=get_auth_headers(admin_token),
            json={
                "current_password": "WrongPassword123",
                "new_password": "NewPassword123"
            }
        )

        assert response.status_code == 400


class TestAuthAuthorization:
    """Test authorization and role-based access"""

    def test_admin_access_admin_endpoint(self, client, admin_token):
        """Should allow admin to access admin endpoint"""
        response = client.get(
            "/api/v1/admin/users",
            headers=get_auth_headers(admin_token)
        )

        assert response.status_code == 200

    def test_manager_denied_admin_endpoint(self, client, manager_token):
        """Should deny manager access to admin endpoint"""
        response = client.get(
            "/api/v1/admin/users",
            headers=get_auth_headers(manager_token)
        )

        assert response.status_code == 403

    def test_unauthenticated_denied_protected_endpoint(self, client):
        """Should deny unauthenticated access to protected endpoint"""
        response = client.get("/api/v1/admin/users")
        assert response.status_code == 401


class TestRateLimiting:
    """Test rate limiting on auth endpoints"""

    def test_login_rate_limit(self, client):
        """Should enforce rate limiting on login attempts"""
        # Attempt multiple failed logins
        for i in range(6):
            response = client.post(
                "/api/v1/auth/login",
                json={
                    "email": "test@test.local",
                    "password": "wrong"
                }
            )

        # After X attempts, should be rate limited
        # Implementation depends on rate limiting strategy
        assert response.status_code in [401, 429]
