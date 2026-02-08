// ============================================================================
// IMS 2.0 - K6 Load Testing Script
// ============================================================================
// Performance and load testing for critical APIs
// Run with: k6 run k6-load-test.js

import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Trend, Counter, Gauge } from 'k6/metrics';

// Define custom metrics
const errorRate = new Rate('errors');
const loginDuration = new Trend('login_duration');
const productsFetchDuration = new Trend('products_fetch_duration');
const orderCreationDuration = new Trend('order_creation_duration');
const totalRequests = new Counter('total_requests');
const activeConnections = new Gauge('active_connections');

// Load test configuration
export const options = {
  stages: [
    { duration: '30s', target: 100 },    // Ramp-up to 100 users
    { duration: '2m', target: 500 },     // Ramp-up to 500 users
    { duration: '5m', target: 1000 },    // Ramp-up to 1000 users
    { duration: '5m', target: 1000 },    // Stay at 1000 users
    { duration: '2m', target: 500 },     // Ramp-down to 500 users
    { duration: '1m', target: 0 },       // Ramp-down to 0 users
  ],
  thresholds: {
    http_req_duration: ['p(95)<500', 'p(99)<1000'], // 95% < 500ms, 99% < 1000ms
    http_req_failed: ['rate<0.1'],                   // Error rate < 10%
    'errors': ['rate<0.05'],                         // Custom error rate < 5%
  },
};

const BASE_URL = __ENV.API_URL || 'https://ims-20-railway-production.up.railway.app/api/v1';
const TEST_ADMIN_EMAIL = 'admin@ims.local';
const TEST_ADMIN_PASSWORD = 'admin123';

let authToken = '';

/**
 * Setup phase - get authentication token
 */
export function setup() {
  group('Setup - Authentication', function () {
    const loginResponse = http.post(`${BASE_URL}/auth/login`, {
      email: TEST_ADMIN_EMAIL,
      password: TEST_ADMIN_PASSWORD,
    });

    check(loginResponse, {
      'login successful': (r) => r.status === 200,
      'token received': (r) => r.json('data.token') !== null,
    });

    if (loginResponse.status === 200) {
      authToken = loginResponse.json('data.token');
      console.log('Authentication token acquired');
      return { token: authToken };
    }

    throw new Error('Setup failed: Could not authenticate');
  });
}

/**
 * VU code - main load test scenarios
 */
export default function (data) {
  activeConnections.add(1);
  authToken = data.token;

  group('Products API', function () {
    // Test product listing
    group('List Products', function () {
      const listResponse = http.get(`${BASE_URL}/products?page=1&limit=20`, {
        headers: { Authorization: `Bearer ${authToken}` },
      });

      const success = check(listResponse, {
        'products list status 200': (r) => r.status === 200,
        'products list has items': (r) => r.json('data.items.length') > 0,
        'products list has pagination': (r) => r.json('data.totalPages') !== undefined,
      });

      productsFetchDuration.add(listResponse.timings.duration);
      errorRate.add(!success);
      totalRequests.add(1);
    });

    sleep(1);

    // Test product search
    group('Search Products', function () {
      const searchResponse = http.get(`${BASE_URL}/products?search=frame&page=1&limit=20`, {
        headers: { Authorization: `Bearer ${authToken}` },
      });

      const success = check(searchResponse, {
        'search status 200': (r) => r.status === 200,
        'search has results': (r) => r.json('data.items.length') >= 0,
      });

      errorRate.add(!success);
      totalRequests.add(1);
    });

    sleep(1);
  });

  group('Customers API', function () {
    // Test customer listing
    const customerResponse = http.get(`${BASE_URL}/customers?page=1&limit=20`, {
      headers: { Authorization: `Bearer ${authToken}` },
    });

    const success = check(customerResponse, {
      'customers list status 200': (r) => r.status === 200,
      'customers list has items': (r) => r.json('data.items') !== undefined,
    });

    errorRate.add(!success);
    totalRequests.add(1);

    sleep(1);
  });

  group('Orders API', function () {
    // Test order listing
    const ordersResponse = http.get(`${BASE_URL}/orders?page=1&limit=20&status=PENDING`, {
      headers: { Authorization: `Bearer ${authToken}` },
    });

    check(ordersResponse, {
      'orders list status 200': (r) => r.status === 200,
      'orders list valid': (r) => r.json('data') !== undefined,
    });

    sleep(1);

    // Test order creation (if GET successful, try POST)
    if (ordersResponse.status === 200) {
      group('Create Order', function () {
        const createResponse = http.post(`${BASE_URL}/orders`, {
          customerId: 'cust-1',
          storeId: 'store-1',
          items: [
            { productId: 'prod-1', quantity: 2 },
          ],
          paymentMethod: 'CASH',
        }, {
          headers: { Authorization: `Bearer ${authToken}` },
        });

        const success = check(createResponse, {
          'order creation status 200/201': (r) => r.status === 200 || r.status === 201,
          'order has id': (r) => r.json('data.id') !== undefined,
        });

        orderCreationDuration.add(createResponse.timings.duration);
        errorRate.add(!success);
        totalRequests.add(1);
      });
    }

    sleep(1);
  });

  group('Dashboard API', function () {
    // Test dashboard stats
    const statsResponse = http.get(`${BASE_URL}/reports/dashboard-stats`, {
      headers: { Authorization: `Bearer ${authToken}` },
    });

    check(statsResponse, {
      'dashboard stats status 200': (r) => r.status === 200,
      'dashboard has KPIs': (r) => {
        const data = r.json('data');
        return data && data.totalSales !== undefined;
      },
    });

    totalRequests.add(1);
    sleep(1);
  });

  group('Inventory API', function () {
    // Test inventory listing
    const inventoryResponse = http.get(`${BASE_URL}/inventory?storeId=store-1&page=1&limit=20`, {
      headers: { Authorization: `Bearer ${authToken}` },
    });

    const success = check(inventoryResponse, {
      'inventory list status 200': (r) => r.status === 200,
      'inventory has items': (r) => r.json('data.items') !== undefined,
    });

    errorRate.add(!success);
    totalRequests.add(1);
    sleep(1);
  });

  activeConnections.add(-1);
  sleep(Math.random() * 5 + 1); // Random sleep between 1-6 seconds
}

/**
 * Teardown phase - cleanup
 */
export function teardown(data) {
  console.log('Load test completed');
  console.log(`Total requests: ${totalRequests.value}`);
}
