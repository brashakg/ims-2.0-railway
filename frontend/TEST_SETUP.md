# IMS 2.0 - Testing Infrastructure Setup Guide

## Phase 4: Testing & QA - Complete Implementation

This document describes the comprehensive testing infrastructure set up for the IMS 2.0 application, with target coverage of 85%+ across all modules.

---

## 1. Testing Stack Overview

### Unit Testing Framework
- **Jest**: Test runner and assertion library
- **React Testing Library**: React component testing utilities
- **@testing-library/user-event**: User interaction simulation

### Integration Testing
- Custom integration test suite for critical user flows
- Mock API responses and authentication state

### Load Testing
- **k6**: Open-source load testing tool
- Performance benchmarking against 10,000+ concurrent users

### Security Testing
- npm audit for dependency vulnerabilities
- OWASP ZAP for security scanning
- TypeScript strict mode for type safety

---

## 2. Installation & Setup

### Add Test Dependencies

```bash
npm install --save-dev \
  @testing-library/react \
  @testing-library/jest-dom \
  @testing-library/user-event \
  jest \
  ts-jest \
  @types/jest \
  identity-obj-proxy \
  jest-environment-jsdom
```

### Load Testing Tools

```bash
# Install k6 (load testing)
# On macOS: brew install k6
# On Ubuntu: sudo apt-get install k6
# Or download from: https://k6.io/docs/getting-started/installation/
```

### Security Testing Tools

```bash
# Install OWASP ZAP (security scanning)
# Download from: https://www.zaproxy.org/download/

# Use npm audit for dependency scanning
npm audit

# Optional: Snyk for vulnerability scanning
npm install -g snyk
snyk test
```

---

## 3. Test Files Structure

```
frontend/
├── jest.config.js                          # Jest configuration
├── src/
│   ├── setupTests.ts                       # Jest setup file
│   ├── utils/
│   │   ├── test-utils.ts                   # Testing utilities & helpers
│   │   └── test-fixtures.ts                # Mock data & fixtures
│   ├── components/
│   │   └── common/
│   │       └── __tests__/
│   │           └── BaseModal.test.tsx      # Component unit tests
│   ├── hooks/
│   │   └── __tests__/
│   │       └── useApiQuery.test.tsx        # Hook unit tests
│   └── __tests__/
│       └── integration/
│           └── auth.integration.test.tsx   # Integration tests
├── k6-load-test.js                         # Load testing script
└── TEST_SETUP.md                           # This file
```

---

## 4. NPM Scripts

Add these scripts to `package.json`:

```json
{
  "scripts": {
    "test": "jest",
    "test:watch": "jest --watch",
    "test:coverage": "jest --coverage",
    "test:unit": "jest --testPathPattern='(?<!integration)'",
    "test:integration": "jest --testPathPattern='integration'",
    "test:coverage:report": "jest --coverage && open coverage/lcov-report/index.html",
    "lint:security": "npm audit && snyk test",
    "load:test": "k6 run k6-load-test.js",
    "load:test:staging": "k6 run k6-load-test.js --vus 100 --duration 5m",
    "load:test:production": "k6 run k6-load-test.js --vus 1000 --duration 10m"
  }
}
```

---

## 5. Running Tests

### Unit Tests

```bash
# Run all tests
npm test

# Run in watch mode (for development)
npm run test:watch

# Run only unit tests (excluding integration)
npm run test:unit

# Generate coverage report
npm run test:coverage
```

### Integration Tests

```bash
# Run integration tests
npm run test:integration

# Run specific integration test file
npm test -- auth.integration.test.tsx
```

### Load Testing

```bash
# Run load test with default settings (1000 VUs, 15 minutes)
npm run load:test

# Run staging load test (100 VUs, 5 minutes)
npm run load:test:staging

# Run production load test (1000 VUs, 10 minutes)
npm run load:test:production

# Custom configuration
k6 run k6-load-test.js --vus 500 --duration 10m
```

### Security Testing

```bash
# Check for vulnerable dependencies
npm audit

# Detailed vulnerability report
npm audit --json

# Fix vulnerabilities (with caution)
npm audit fix

# Third-party vulnerability scanner (optional)
snyk test
snyk monitor
```

---

## 6. Coverage Targets

### Global Coverage Thresholds

- **Branches**: 75%
- **Functions**: 85%
- **Lines**: 85%
- **Statements**: 85%

### Component Coverage (Higher Bar)

- **Branches**: 80%
- **Functions**: 90%
- **Lines**: 90%
- **Statements**: 90%

### Hooks Coverage (Highest Bar)

- **Branches**: 85%
- **Functions**: 90%
- **Lines**: 90%
- **Statements**: 90%

### Services Coverage

- **Branches**: 80%
- **Functions**: 85%
- **Lines**: 85%
- **Statements**: 85%

---

## 7. Test Utilities Reference

### renderWithProviders

```typescript
const { getByText, queryClient } = renderWithProviders(
  <MyComponent />,
  {
    authState: { user: mockUsers.admin },
    queryClient: customQueryClient,
  }
);
```

### Mock Data Fixtures

```typescript
// Pre-defined fixtures
mockUsers.admin
mockUsers.manager
mockUsers.staff

mockProducts.frame1
mockProducts.lens1
mockProducts.outOfStock

mockCustomers.regular
mockCustomers.vip
mockCustomers.new

mockOrders.pending
mockOrders.completed
mockOrders.cancelled

// Factory functions for custom data
createMockUser({ email: 'custom@test.com' })
createMockProduct({ name: 'Custom Product', price: 5000 })
createMockOrder({ status: 'SHIPPED' })
```

### Form Input Helpers

```typescript
fillInputField(container, 'Email', 'test@example.com');
fillSelectField(container, 'Role', 'ADMIN');
submitForm(container);
```

### Event Simulation

```typescript
simulateKeyPress(element, 'Escape');
simulateChange(inputElement, 'new value');
simulateClick(buttonElement);
```

### API Response Mocking

```typescript
const mockResponse = createMockApiResponse({ id: 1, name: 'Product' });
const mockError = createMockApiError('API Error', 500);
```

---

## 8. Load Testing Configuration

### K6 Test Stages

The load test simulates realistic traffic patterns:

1. **Ramp-up (30s)**: 0 → 100 users
2. **Ramp-up (2m)**: 100 → 500 users
3. **Ramp-up (5m)**: 500 → 1000 users
4. **Sustained (5m)**: 1000 users constant
5. **Ramp-down (2m)**: 1000 → 500 users
6. **Ramp-down (1m)**: 500 → 0 users

**Total Duration**: ~16 minutes

### Performance Thresholds

- **95th percentile response time**: < 500ms
- **99th percentile response time**: < 1000ms
- **Error rate**: < 10%

### Custom Metrics

- `errors`: Custom error tracking
- `login_duration`: Authentication endpoint performance
- `products_fetch_duration`: Product API performance
- `order_creation_duration`: Order creation endpoint
- `total_requests`: Total API calls made
- `active_connections`: Concurrent active connections

### Load Test Endpoints Covered

1. **Authentication**
   - POST /auth/login
   - GET /auth/validate
   - POST /auth/refresh
   - POST /auth/logout

2. **Products**
   - GET /products (list with pagination)
   - GET /products (search)

3. **Customers**
   - GET /customers (list)

4. **Orders**
   - GET /orders (list)
   - POST /orders (create)

5. **Dashboard**
   - GET /reports/dashboard-stats

6. **Inventory**
   - GET /inventory (list)

---

## 9. CI/CD Integration

### GitHub Actions Example

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Setup Node.js
        uses: actions/setup-node@v3
        with:
          node-version: '18'

      - name: Install dependencies
        run: npm ci

      - name: Run unit tests
        run: npm run test:unit

      - name: Run integration tests
        run: npm run test:integration

      - name: Generate coverage report
        run: npm run test:coverage

      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v3
        with:
          files: ./coverage/lcov.info
```

---

## 10. Best Practices

### Writing Tests

1. **Descriptive Test Names**: Use clear, specific names
   ```typescript
   it('should disable submit button when form has errors')
   it('should show error message when login fails with 401')
   ```

2. **Arrange-Act-Assert Pattern**
   ```typescript
   // Arrange
   const mockFn = jest.fn();

   // Act
   userEvent.click(button);

   // Assert
   expect(mockFn).toHaveBeenCalled();
   ```

3. **Mock External Dependencies**
   ```typescript
   jest.mock('axios');
   jest.mock('../api/products');
   ```

4. **Use Fixtures for Consistency**
   ```typescript
   const user = createMockUser();
   const product = createMockProduct();
   ```

5. **Test User Behavior, Not Implementation**
   ```typescript
   // Good: tests user action
   await userEvent.click(screen.getByRole('button', { name: /save/i }));

   // Avoid: tests internal state
   expect(component.state.isLoading).toBe(true);
   ```

### Coverage Strategy

1. **Aim for behavior coverage, not line coverage**
2. **Test critical user flows end-to-end**
3. **Test error cases and edge conditions**
4. **Focus on complex business logic**
5. **Reduce mocking of internal components**

### Load Testing

1. **Use realistic data volumes**
2. **Include thinking time between requests**
3. **Test peak traffic scenarios**
4. **Monitor resource utilization (CPU, memory)**
5. **Run tests in staging before production**

---

## 11. Troubleshooting

### Common Issues

**Issue**: `Cannot find module '@testing-library/react'`
```bash
npm install @testing-library/react --save-dev
```

**Issue**: `localStorage is not defined`
- Already mocked in setupTests.ts, ensure file is loaded

**Issue**: K6 script fails to authenticate
- Verify BASE_URL and credentials in k6-load-test.js
- Check if API is accessible and running

**Issue**: Tests timeout
- Increase timeout: `jest.setTimeout(10000)`
- Check for unresolved promises

**Issue**: Coverage report incomplete
- Run: `npm run test:coverage`
- Check collectCoverageFrom patterns in jest.config.js

---

## 12. Next Steps

After Phase 4 (Testing & QA) is complete:

1. **Phase 5: DevOps & Infrastructure**
   - GitHub Actions CI/CD pipelines
   - Terraform Infrastructure as Code
   - Backup and disaster recovery

2. **Phase 6: Monitoring & Analytics**
   - Real-time dashboards
   - Performance metrics
   - User analytics

3. **Phase 7: Security Hardening**
   - 2FA implementation
   - RBAC enhancements
   - Audit logging

4. **Phase 8: Documentation & Training**
   - Developer documentation
   - Operations manual
   - Training program

---

## 13. Resources

- [Jest Documentation](https://jestjs.io/)
- [React Testing Library](https://testing-library.com/react)
- [K6 Documentation](https://k6.io/docs/)
- [OWASP ZAP](https://www.zaproxy.org/)
- [npm Audit](https://docs.npmjs.com/cli/v8/commands/npm-audit)

---

**Last Updated**: February 8, 2026
**Coverage Target**: 85%+
**Load Test Target**: 10,000+ concurrent users
**Performance Target**: P95 < 500ms, P99 < 1000ms
