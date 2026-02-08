# ============================================================================
# IMS 2.0 - Pytest Configuration & Fixtures
# ============================================================================

import pytest
import os
import sys
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from pymongo import MongoClient

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.main import app
from api.schemas import UserCreate, LoginRequest

# ============================================================================
# Test Database Configuration
# ============================================================================

MONGODB_TEST_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017/ims_test")


@pytest.fixture(scope="session")
def mongodb_client():
    """Create MongoDB client for testing"""
    client = MongoClient(MONGODB_TEST_URL)
    yield client
    # Cleanup: drop test database after all tests
    client.drop_database("ims_test")


@pytest.fixture(scope="function")
def mongodb(mongodb_client):
    """Get MongoDB database for each test"""
    db = mongodb_client.ims_test
    # Clear collections before each test
    for collection_name in db.list_collection_names():
        db[collection_name].delete_many({})
    yield db


@pytest.fixture(scope="function")
def client():
    """Create FastAPI test client"""
    return TestClient(app)


@pytest.fixture(scope="function")
def admin_user():
    """Create test admin user data"""
    return {
        "username": "admin",
        "email": "admin@test.com",
        "password": "testpass123",
        "full_name": "Test Admin",
        "roles": ["ADMIN"],
    }


@pytest.fixture(scope="function")
def admin_token(client, admin_user):
    """Get admin authentication token"""
    # Note: In a real test, you'd create the user first
    # For now, this is a placeholder
    return "test-admin-token"


@pytest.fixture(scope="function")
def manager_token(client):
    """Get manager authentication token"""
    return "test-manager-token"


def get_auth_headers(token: str) -> dict:
    """Helper to get authorization headers"""
    return {"Authorization": f"Bearer {token}"}


def assert_success_response(response, status_code: int = 200):
    """Assert response is successful"""
    assert response.status_code == status_code
    assert response.json().get("success") or response.status_code in [200, 201]


def assert_error_response(response, expected_status: int = 400):
    """Assert response is an error"""
    assert response.status_code == expected_status


# ============================================================================
# Pytest Markers
# ============================================================================

def pytest_configure(config):
    """Register custom markers"""
    config.addinivalue_line("markers", "unit: unit tests")
    config.addinivalue_line("markers", "integration: integration tests")
    config.addinivalue_line("markers", "slow: slow tests")
    config.addinivalue_line("markers", "auth: authentication tests")
