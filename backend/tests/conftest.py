"""
IMS 2.0 - Test Configuration
==============================
Shared fixtures for backend tests.
Uses FastAPI TestClient with the real app but no external DB dependency.
"""

import pytest
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def app():
    """Create the FastAPI app for testing."""
    # Set test env vars before importing the app
    os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
    os.environ.setdefault("MONGODB_URI", "")  # empty = no DB
    from api.main import app as _app
    return _app


@pytest.fixture
def client(app):
    """TestClient that talks to the app without network."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers(client):
    """Get a valid JWT for an admin user by calling login.
    Falls back to creating a token directly if login requires a DB.
    """
    from api.routers.auth import create_access_token
    token = create_access_token({
        "user_id": "test-admin-001",
        "username": "testadmin",
        "roles": ["SUPERADMIN"],
        "store_ids": ["BV-TEST-01"],
        "active_store_id": "BV-TEST-01",
    })
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def staff_headers():
    """JWT for a regular sales staff user."""
    from api.routers.auth import create_access_token
    token = create_access_token({
        "user_id": "test-staff-001",
        "username": "teststaff",
        "roles": ["SALES_STAFF"],
        "store_ids": ["BV-TEST-01"],
        "active_store_id": "BV-TEST-01",
        "discount_cap": 10.0,
    })
    return {"Authorization": f"Bearer {token}"}
