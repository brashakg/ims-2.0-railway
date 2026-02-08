# ============================================================================
# IMS 2.0 - Backend Test Configuration
# ============================================================================
# Pytest configuration and fixtures for backend testing

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime, timedelta
import os
import sys

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.main import app, get_db
from api.models import Base, User, Role, Store, Product, Customer, Order, Inventory
from api.schemas import UserCreate, ProductCreate, CustomerCreate, OrderCreate
from api.config import Settings
from api.utils.security import get_password_hash

# ============================================================================
# Test Database Configuration
# ============================================================================

# Use SQLite for tests (in-memory)
SQLALCHEMY_TEST_DATABASE_URL = "sqlite:///./test.db"

engine = create_engine(
    SQLALCHEMY_TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False
)

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    """Override database dependency for testing"""
    database = TestingSessionLocal()
    try:
        yield database
    finally:
        database.close()


# ============================================================================
# Pytest Fixtures
# ============================================================================

@pytest.fixture(scope="function")
def db_session():
    """Create a fresh database for each test"""
    Base.metadata.create_all(bind=engine)
    yield TestingSessionLocal()
    TestingSessionLocal.remove()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db_session):
    """Create test client with database override"""
    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def admin_token(client, db_session):
    """Create admin user and return auth token"""
    # Create admin role
    admin_role = Role(name="ADMIN", description="Administrator")
    db_session.add(admin_role)
    db_session.commit()

    # Create admin user
    admin_user = User(
        email="admin@test.local",
        username="admin",
        full_name="Admin User",
        hashed_password=get_password_hash("admin123"),
        is_active=True,
    )
    admin_user.roles.append(admin_role)
    db_session.add(admin_user)
    db_session.commit()

    # Get token
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.local", "password": "admin123"}
    )
    token = response.json()["data"]["token"]
    return token


@pytest.fixture(scope="function")
def manager_token(client, db_session):
    """Create manager user and return auth token"""
    # Create manager role
    manager_role = Role(name="STORE_MANAGER", description="Store Manager")
    db_session.add(manager_role)
    db_session.commit()

    # Create manager user
    manager_user = User(
        email="manager@test.local",
        username="manager",
        full_name="Manager User",
        hashed_password=get_password_hash("manager123"),
        is_active=True,
    )
    manager_user.roles.append(manager_role)
    db_session.add(manager_user)
    db_session.commit()

    # Get token
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "manager@test.local", "password": "manager123"}
    )
    token = response.json()["data"]["token"]
    return token


@pytest.fixture(scope="function")
def test_store(db_session):
    """Create test store"""
    store = Store(
        name="Test Store",
        location="Downtown",
        city="Test City",
        state="TS",
        zipcode="12345",
        phone="9876543210",
        email="store@test.local",
        manager_name="Store Manager",
        status="ACTIVE"
    )
    db_session.add(store)
    db_session.commit()
    return store


@pytest.fixture(scope="function")
def test_product(db_session):
    """Create test product"""
    product = Product(
        sku="TEST-SKU-001",
        name="Test Product",
        description="Test product description",
        category="FRAMES",
        brand="TestBrand",
        price=2500.00,
        quantity=100,
        status="ACTIVE"
    )
    db_session.add(product)
    db_session.commit()
    return product


@pytest.fixture(scope="function")
def test_customer(db_session):
    """Create test customer"""
    customer = Customer(
        first_name="John",
        last_name="Doe",
        email="john@test.local",
        phone="9876543210",
        address="123 Test St",
        city="Test City",
        state="TS",
        zipcode="12345",
        status="ACTIVE"
    )
    db_session.add(customer)
    db_session.commit()
    return customer


@pytest.fixture(scope="function")
def test_inventory(db_session, test_store, test_product):
    """Create test inventory"""
    inventory = Inventory(
        product_id=test_product.id,
        store_id=test_store.id,
        quantity=50,
        min_stock=10,
        max_stock=200,
        status="IN_STOCK"
    )
    db_session.add(inventory)
    db_session.commit()
    return inventory


@pytest.fixture(scope="function")
def test_order(db_session, test_customer, test_store):
    """Create test order"""
    order = Order(
        order_number=f"TEST-ORD-{datetime.now().timestamp()}",
        customer_id=test_customer.id,
        store_id=test_store.id,
        total_amount=2500.00,
        payment_status="PENDING",
        status="PENDING"
    )
    db_session.add(order)
    db_session.commit()
    return order


# ============================================================================
# Helper Functions
# ============================================================================

def get_auth_headers(token: str) -> dict:
    """Get authorization headers for API requests"""
    return {"Authorization": f"Bearer {token}"}


def assert_success_response(response, expected_status: int = 200):
    """Assert response is successful"""
    assert response.status_code == expected_status
    assert response.json()["success"] is True
    return response.json()["data"]


def assert_error_response(response, expected_status: int = 400):
    """Assert response has error"""
    assert response.status_code == expected_status
    assert response.json()["success"] is False


# ============================================================================
# Pytest Configuration
# ============================================================================

def pytest_configure(config):
    """Configure pytest markers"""
    config.addinivalue_line(
        "markers", "unit: mark test as a unit test"
    )
    config.addinivalue_line(
        "markers", "integration: mark test as an integration test"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow running"
    )
    config.addinivalue_line(
        "markers", "auth: mark test as authentication related"
    )
    config.addinivalue_line(
        "markers", "api: mark test as API endpoint related"
    )


# ============================================================================
# Test Settings
# ============================================================================

class TestSettings(Settings):
    """Override settings for testing"""
    DATABASE_URL = SQLALCHEMY_TEST_DATABASE_URL
    ENVIRONMENT = "test"
    DEBUG = True
    JWT_ALGORITHM = "HS256"
    JWT_EXPIRATION_HOURS = 24
    CORS_ORIGINS = ["http://localhost:3000", "http://localhost:5173"]
    LOG_LEVEL = "DEBUG"
