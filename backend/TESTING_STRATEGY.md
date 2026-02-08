# IMS 2.0 - Backend Testing Strategy

## Phase 4: Testing & QA - Backend Implementation

This document outlines the comprehensive testing strategy for the IMS 2.0 backend API, targeting 85%+ code coverage and 10,000+ concurrent user support.

---

## 1. Testing Framework & Tools

### Unit Testing
- **Pytest**: Python testing framework
- **pytest-cov**: Coverage measurement
- **pytest-asyncio**: Async test support
- **pytest-mock**: Mocking utilities

### Integration Testing
- **TestClient**: FastAPI test client
- **SQLAlchemy**: In-memory SQLite for test database
- **Factory Boy**: Test data generation

### Performance Testing
- **Locust**: Load testing (alternative to k6)
- **pytest-benchmark**: Micro-benchmarking

### Code Quality
- **Pylint**: Code analysis
- **Black**: Code formatting
- **isort**: Import sorting
- **mypy**: Type checking

---

## 2. Test Structure

### Directory Layout

```
backend/
├── tests/
│   ├── __init__.py
│   ├── test_config.py              # Pytest configuration & fixtures
│   ├── test_auth.py                # Authentication tests
│   ├── test_products.py            # Product API tests
│   ├── test_customers.py           # Customer API tests
│   ├── test_orders.py              # Order API tests
│   ├── test_inventory.py           # Inventory API tests
│   ├── test_stores.py              # Store API tests
│   └── integration/
│       ├── test_order_flow.py      # Complete order workflow
│       ├── test_inventory_flow.py  # Inventory management flow
│       └── test_sync_flow.py       # Multi-store sync flow
├── pytest.ini                       # Pytest configuration
├── TESTING_STRATEGY.md              # This file
└── coverage.py                      # Coverage configuration
```

### Test Categories

1. **Unit Tests** (~60% of tests)
   - Test individual functions/methods in isolation
   - Mock external dependencies
   - Fast execution (< 1ms per test)

2. **Integration Tests** (~30% of tests)
   - Test API endpoints with database
   - Test multi-service interactions
   - Use in-memory SQLite

3. **End-to-End Tests** (~10% of tests)
   - Test complete user workflows
   - Test critical business processes
   - Slower but comprehensive

---

## 3. Installation & Setup

### Install Test Dependencies

```bash
pip install pytest pytest-cov pytest-asyncio pytest-mock \
  factory-boy faker locust python-multipart
```

### Configure Pytest

Create `backend/pytest.ini`:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
asyncio_mode = auto
markers =
    unit: Unit tests
    integration: Integration tests
    slow: Slow running tests
    auth: Authentication tests
    api: API endpoint tests
    db: Database tests
addopts = --strict-markers --tb=short
```

---

## 4. Fixture Structure

### Database Fixtures

```python
@pytest.fixture
def db_session():
    """Fresh database for each test"""
    # Setup
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()

    yield session

    # Teardown
    session.close()
    Base.metadata.drop_all(bind=engine)
```

### Authentication Fixtures

```python
@pytest.fixture
def admin_token(client, db_session):
    """Admin user with valid JWT token"""
    # Create admin user
    # Return auth token
    pass

@pytest.fixture
def manager_token(client, db_session):
    """Manager user with valid JWT token"""
    pass

@pytest.fixture
def staff_token(client, db_session):
    """Staff user with valid JWT token"""
    pass
```

### Data Fixtures

```python
@pytest.fixture
def test_product(db_session):
    """Create test product"""
    product = Product(
        sku="TEST-001",
        name="Test Product",
        category="FRAMES",
        price=2500
    )
    db_session.add(product)
    db_session.commit()
    return product

@pytest.fixture
def test_customer(db_session):
    """Create test customer"""
    pass

@pytest.fixture
def test_order(db_session, test_customer, test_product):
    """Create test order with items"""
    pass
```

---

## 5. Test Examples

### Unit Test Example

```python
@pytest.mark.unit
def test_password_hashing():
    """Test password hashing function"""
    password = "TestPassword123"
    hashed = get_password_hash(password)

    assert hashed != password
    assert verify_password(password, hashed)
    assert not verify_password("WrongPassword", hashed)
```

### Integration Test Example

```python
@pytest.mark.integration
def test_create_order_endpoint(client, admin_token, test_customer, test_product):
    """Test order creation via API"""
    response = client.post(
        "/api/v1/orders",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "customer_id": test_customer.id,
            "store_id": "store-1",
            "items": [
                {
                    "product_id": test_product.id,
                    "quantity": 2
                }
            ]
        }
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["customer_id"] == test_customer.id
    assert len(data["items"]) == 1
```

### End-to-End Test Example

```python
@pytest.mark.integration
class TestOrderWorkflow:
    """Test complete order workflow"""

    def test_create_order_and_process_payment(self, client, admin_token):
        """Test: create order → add items → process payment → confirm"""
        # Create order
        # Add items to order
        # Process payment
        # Confirm order
        # Verify status changes
        pass
```

---

## 6. Coverage Targets

### By Module

| Module | Line Coverage | Branch Coverage | Function Coverage |
|--------|---------------|-----------------|-------------------|
| Authentication | 95% | 90% | 95% |
| Products API | 85% | 80% | 90% |
| Orders API | 90% | 85% | 95% |
| Customers API | 85% | 80% | 90% |
| Inventory API | 85% | 80% | 90% |
| Reports API | 80% | 75% | 85% |
| Utilities | 90% | 85% | 90% |
| **Overall** | **85%** | **80%** | **90%** |

### Critical Paths (Must Have Tests)

1. **Authentication Flow**
   - Login ✓
   - Logout ✓
   - Token refresh ✓
   - Password reset ✓

2. **Order Processing**
   - Create order
   - Update order
   - Cancel order
   - Process payment

3. **Inventory Management**
   - Check stock
   - Update quantity
   - Transfer between stores
   - Track low stock

4. **Reporting**
   - Dashboard stats
   - Sales reports
   - Inventory reports
   - Customer analytics

---

## 7. Running Tests

### Run All Tests

```bash
# Run all tests
pytest

# Run with coverage report
pytest --cov=api --cov-report=html

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_auth.py

# Run specific test
pytest tests/test_auth.py::TestLogin::test_login_with_valid_credentials
```

### Run by Marker

```bash
# Run only unit tests
pytest -m unit

# Run only integration tests
pytest -m integration

# Run auth tests
pytest -m auth

# Skip slow tests
pytest -m "not slow"
```

### Generate Coverage Report

```bash
# HTML coverage report
pytest --cov=api --cov-report=html
open htmlcov/index.html

# Terminal report
pytest --cov=api --cov-report=term-missing

# JSON report
pytest --cov=api --cov-report=json
```

---

## 8. Best Practices

### Test Naming

```python
# Good: descriptive, follows pattern
def test_create_order_with_valid_items():
def test_product_price_must_be_positive():
def test_inventory_insufficient_raises_error():

# Avoid: vague, too generic
def test_order():
def test_error():
def test_api():
```

### Arrange-Act-Assert Pattern

```python
def test_create_product(client, admin_token):
    # Arrange
    product_data = {
        "sku": "TEST-001",
        "name": "Test Product",
        "price": 2500
    }

    # Act
    response = client.post(
        "/api/v1/products",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=product_data
    )

    # Assert
    assert response.status_code == 201
    assert response.json()["data"]["sku"] == "TEST-001"
```

### Testing Error Cases

```python
def test_create_order_with_invalid_customer(client, admin_token):
    """Test error handling"""
    response = client.post(
        "/api/v1/orders",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "customer_id": "invalid-id",
            "items": []
        }
    )

    assert response.status_code == 404
    assert response.json()["success"] is False
```

### Using Fixtures

```python
# Good: use fixtures for data consistency
def test_order_listing(client, admin_token, test_order):
    response = client.get(
        "/api/v1/orders",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert test_order.id in [item["id"] for item in response.json()["data"]["items"]]

# Avoid: creating data inline
def test_order_listing_inline(client, admin_token):
    # Create order directly...
    pass
```

---

## 9. CI/CD Integration

### GitHub Actions Example

```yaml
name: Backend Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_PASSWORD: postgres
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
      - uses: actions/checkout@v3

      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install -r requirements-dev.txt

      - name: Run unit tests
        run: pytest -m unit --cov=api --cov-report=term

      - name: Run integration tests
        run: pytest -m integration --cov=api --cov-report=term

      - name: Upload coverage
        uses: codecov/codecov-action@v3
```

---

## 10. Load Testing with Locust

### Create `backend/locustfile.py`

```python
from locust import HttpUser, task, between
from datetime import datetime

class APIUser(HttpUser):
    wait_time = between(1, 3)
    token = ""

    def on_start(self):
        """Login before tests"""
        response = self.client.post(
            "/api/v1/auth/login",
            json={"email": "test@test.local", "password": "password"}
        )
        self.token = response.json()["data"]["token"]

    @task(3)
    def list_products(self):
        """List products (3 tasks)"""
        self.client.get(
            "/api/v1/products?page=1&limit=20",
            headers={"Authorization": f"Bearer {self.token}"}
        )

    @task(2)
    def search_products(self):
        """Search products (2 tasks)"""
        self.client.get(
            "/api/v1/products?search=frame&page=1",
            headers={"Authorization": f"Bearer {self.token}"}
        )

    @task(1)
    def create_order(self):
        """Create order (1 task)"""
        self.client.post(
            "/api/v1/orders",
            headers={"Authorization": f"Bearer {self.token}"},
            json={
                "customer_id": "cust-1",
                "items": [{"product_id": "prod-1", "quantity": 1}]
            }
        )
```

### Run Load Test

```bash
# Run load test with UI
locust -f locustfile.py --host=http://localhost:8000

# Run headless
locust -f locustfile.py --host=http://localhost:8000 \
  --users 1000 --spawn-rate 100 --run-time 10m --headless
```

---

## 11. Performance Benchmarks

### Target Metrics

| Endpoint | Response Time (P95) | Response Time (P99) | Throughput |
|----------|-------------------|-------------------|-----------|
| GET /products | < 100ms | < 200ms | 5,000+ req/s |
| GET /orders | < 150ms | < 300ms | 3,000+ req/s |
| POST /orders | < 200ms | < 400ms | 1,000+ req/s |
| GET /dashboard/stats | < 100ms | < 200ms | 5,000+ req/s |
| GET /inventory | < 100ms | < 200ms | 5,000+ req/s |

### Benchmark Test

```python
@pytest.mark.benchmark
def test_product_list_performance(benchmark, client, admin_token):
    """Benchmark product list endpoint"""
    def list_products():
        client.get(
            "/api/v1/products?page=1&limit=20",
            headers={"Authorization": f"Bearer {admin_token}"}
        )

    result = benchmark(list_products)
    assert result.stats.mean < 0.1  # < 100ms
```

---

## 12. Troubleshooting

### Common Issues

**Issue**: `IndentationError in test file`
```bash
python -m py_compile tests/test_auth.py
```

**Issue**: `Fixture not found`
- Ensure `test_config.py` is in `tests/` directory
- Fixture is in conftest.py or test_config.py

**Issue**: `Tests failing due to database state`
```bash
# Clear test database
rm test.db

# Use fresh database for each test
```

**Issue**: `Import errors in tests`
```bash
# Ensure api module is importable
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
pytest tests/
```

---

## 13. Next Steps

After Phase 4 Testing & QA:

1. **Phase 5**: DevOps & Infrastructure
   - CI/CD pipelines
   - Automated deployments
   - Infrastructure as Code

2. **Phase 6**: Monitoring & Analytics
   - Real-time dashboards
   - Performance monitoring
   - User analytics

3. **Phase 7**: Security Hardening
   - Advanced authentication
   - Encryption at rest
   - Audit logging

4. **Phase 8**: Documentation & Training
   - API documentation
   - Developer guides
   - Operations manual

---

## 14. Resources

- [Pytest Documentation](https://docs.pytest.org/)
- [FastAPI Testing](https://fastapi.tiangolo.com/advanced/testing-dependencies/)
- [Locust Documentation](https://locust.io/)
- [Coverage.py](https://coverage.readthedocs.io/)
- [pytest-cov](https://pytest-cov.readthedocs.io/)

---

**Last Updated**: February 8, 2026
**Coverage Target**: 85%+ across all modules
**Load Test Target**: 10,000+ concurrent users
**Performance Target**: P95 < 200ms, P99 < 500ms
