// ============================================================================
// IMS 2.0 - k6 read-only load test (INFRA-01)
// ============================================================================
// SAFE BY DEFAULT. Only ever point this at a DISPOSABLE local/test backend.
//
//   k6 run -e API_URL=http://127.0.0.1:18600/api/v1 \
//          -e K6_USERNAME=<synthetic user> -e K6_PASSWORD=<its password> \
//          k6-load-test.js
//
// - API_URL is REQUIRED (no default) and must be a loopback host
//   (localhost / 127.0.0.1 / [::1]). Any other host is refused unless
//   K6_ALLOW_REMOTE=yes AND the host is not a known production host;
//   production hosts are refused always.
// - Login uses the current contract: POST /auth/login {username, password}
//   -> {access_token}. Credentials come only from the environment.
// - READ-ONLY: no order/payment/stock writes of any kind.
// - Default load is 5 VUs for 1 minute; raise with K6_VUS / K6_DURATION.

import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate } from 'k6/metrics';

const errorRate = new Rate('errors');

const PRODUCTION_HOSTS = [
  'ims-20-railway-production.up.railway.app',
  'ims-2-0-railway.vercel.app',
  'app.uniparallel.com',
  'bettervision.in',
];
const LOOPBACK = ['localhost', '127.0.0.1', '[::1]'];

function hostOf(url) {
  const m = /^https?:\/\/([^/:]+|\[[^\]]+\])/i.exec(url || '');
  return m ? m[1].toLowerCase() : '';
}

const BASE_URL = __ENV.API_URL || '';
const HOST = hostOf(BASE_URL);
if (!HOST) {
  throw new Error('API_URL is required (e.g. http://127.0.0.1:18600/api/v1); there is no default target.');
}
if (PRODUCTION_HOSTS.some((h) => HOST === h || HOST.endsWith(`.${h}`))) {
  throw new Error(`Refusing to load-test production host ${HOST}.`);
}
if (!LOOPBACK.includes(HOST) && __ENV.K6_ALLOW_REMOTE !== 'yes') {
  throw new Error(`Refusing non-loopback host ${HOST}; set K6_ALLOW_REMOTE=yes only for a disposable test backend.`);
}
if (!__ENV.K6_USERNAME || !__ENV.K6_PASSWORD) {
  throw new Error('K6_USERNAME and K6_PASSWORD (a synthetic test user) are required.');
}

export const options = {
  vus: Number(__ENV.K6_VUS || 5),
  duration: __ENV.K6_DURATION || '1m',
  thresholds: {
    http_req_duration: ['p(95)<500', 'p(99)<1000'],
    http_req_failed: ['rate<0.1'],
    errors: ['rate<0.05'],
  },
};

export function setup() {
  const res = http.post(
    `${BASE_URL}/auth/login`,
    JSON.stringify({ username: __ENV.K6_USERNAME, password: __ENV.K6_PASSWORD }),
    { headers: { 'Content-Type': 'application/json' } },
  );
  const token = res.status === 200 ? res.json('access_token') : null;
  if (!token) {
    throw new Error(`Setup failed: login returned ${res.status}`);
  }
  return { token };
}

const READS = [
  ['products', '/products?page=1&limit=20'],
  ['product search', '/products?search=frame&page=1&limit=20'],
  ['customers', '/customers?page=1&limit=20'],
  ['orders', '/orders?page=1&limit=20'],
];

export default function (data) {
  const headers = { Authorization: `Bearer ${data.token}` };
  for (const [name, path] of READS) {
    group(name, function () {
      const res = http.get(`${BASE_URL}${path}`, { headers });
      errorRate.add(!check(res, { [`${name} 200`]: (r) => r.status === 200 }));
    });
    sleep(1);
  }
}
