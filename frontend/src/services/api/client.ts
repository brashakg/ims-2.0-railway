// ============================================================================
// IMS 2.0 - API Client (shared axios instance & utilities)
// ============================================================================

import axios from 'axios';
import type { AxiosInstance, AxiosError, InternalAxiosRequestConfig } from 'axios';
import { LAST_ACTIVITY_KEY } from '../../hooks/useIdleLogout';

const API_BASE_URL = import.meta.env.VITE_API_URL ||
  (import.meta.env.PROD ? 'https://ims-20-railway-production.up.railway.app/api/v1' : '/api/v1');

// API URL configured from environment

// Enforce HTTPS in production - convert any HTTP URLs to HTTPS
export function getSecureApiUrl(): string {
  let url = API_BASE_URL;
  if (import.meta.env.PROD && url.startsWith('http://')) {
    url = url.replace('http://', 'https://');
  }
  return url;
}

// Resolve a backend-relative asset path (e.g. "/api/v1/products/image/<id>")
// to an absolute URL on the API host. The deployed frontend lives on a
// DIFFERENT origin (Vercel) than the API (Railway): a relative <img src>
// resolves against the frontend origin, where the SPA catch-all rewrite
// serves index.html instead of the image — so every self-hosted image must
// be absolutized before it is rendered or stored. In dev the base URL is
// relative ("/api/v1", Vite proxy) and the path passes through unchanged.
// Absolute (http/https/data:) URLs pass through unchanged.
export function resolveApiAssetUrl(url: string): string {
  if (!url || !url.startsWith('/')) return url;
  const base = getSecureApiUrl();
  if (!/^https?:\/\//.test(base)) return url;
  return base.replace(/\/api\/v1\/?$/, '') + url;
}

// Retry configuration
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 1000;

// Helper function for delay
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

// Check if error is retryable (only 5xx server errors and rate limiting)
// Do NOT retry network/CORS errors - they won't be fixed by retrying
const isRetryableError = (error: AxiosError): boolean => {
  // CORS errors have no response - don't retry these
  if (!error.response) {
    return false;
  }
  // Server errors (5xx) - these might be temporary
  if (error.response.status >= 500 && error.response.status < 600) {
    return true;
  }
  // Rate limiting (429) - retry with backoff
  if (error.response.status === 429) {
    return true;
  }
  // Don't retry other errors (401, 403, 404, CORS, etc.)
  return false;
};

// Create axios instance with secure URL
const api: AxiosInstance = axios.create({
  baseURL: getSecureApiUrl(),
  timeout: 10000, // 10 second timeout per request (CORS issues usually fail fast)
  headers: {
    'Content-Type': 'application/json',
  },
});

// Retry-count tracking off the wire: keyed by config object so it never leaves
// the browser. Sending it as an `x-retry-count` header triggered a CORS preflight
// that the backend's allow_headers list didn't cover, causing every request to
// fail with "Network error" before even reaching the backend.
type RetryTrackedConfig = InternalAxiosRequestConfig & {
  __retryCount?: number;
  // 401 refresh-and-retry guard: exactly ONE refresh attempt per request.
  __retried401?: boolean;
};

// ── Token refresh (rotating refresh tokens, 2026-07 hardening) ─────────────
// Access tokens are now short-lived (45 min); the 8h session survives via a
// single-use rotating refresh token from /auth/login. Two triggers:
//   PROACTIVE: when the access token is within 5 min of expiry AND the user is
//   genuinely ACTIVE (see below) — so an active cashier never sees a 401.
//   REACTIVE: on a 401, refresh once and retry the request (covers laptop-wake,
//   throttled background tabs, races).
// CRITICAL idle-logout interplay: "active" is read from the SAME localStorage
// key useIdleLogout maintains from real user input (ims_last_activity). This
// module only READS that key and NEVER writes it, so the refresh loop cannot
// keep an unattended session alive — an idle user's token dies and the 15-min
// idle logout stays authoritative.

export const TOKEN_STORAGE_KEY = 'ims_token';
export const REFRESH_TOKEN_STORAGE_KEY = 'ims_refresh_token';

// Refresh when the access token has less than this long to live...
const PROACTIVE_REFRESH_WINDOW_MS = 5 * 60_000;
// ...but only if real user activity happened within this window. Must stay
// comfortably above useIdleLogout's 5s write throttle and well below the
// 15-min idle timeout (an idle user must NOT be refreshed).
const ACTIVITY_FRESHNESS_MS = 5 * 60_000;

/** Exp claim of a JWT in epoch MILLISECONDS, or null if unparseable. */
export function decodeJwtExpMs(token: string): number | null {
  try {
    const part = token.split('.')[1];
    if (!part) return null;
    const b64 = part.replace(/-/g, '+').replace(/_/g, '/');
    const payload = JSON.parse(atob(b64));
    return typeof payload.exp === 'number' ? payload.exp * 1000 : null;
  } catch {
    return null;
  }
}

/**
 * Pure decision helper (unit-tested): refresh proactively ONLY when the token
 * is inside the expiry window AND the user was recently active. An idle user
 * returns false no matter how close the token is to death — their token is
 * allowed to die so idle logout is never defeated by the refresh loop.
 */
export function shouldProactivelyRefresh(
  expMs: number | null,
  nowMs: number,
  lastActivityMs: number | null,
): boolean {
  if (expMs === null) return false;
  if (lastActivityMs === null || nowMs - lastActivityMs > ACTIVITY_FRESHNESS_MS) {
    return false; // idle -> let the token die (idle logout is authoritative)
  }
  return expMs - nowMs <= PROACTIVE_REFRESH_WINDOW_MS;
}

function readLastActivityMs(): number | null {
  try {
    const raw = localStorage.getItem(LAST_ACTIVITY_KEY);
    const n = raw ? parseInt(raw, 10) : NaN;
    return Number.isFinite(n) && n > 0 ? n : null;
  } catch {
    return null;
  }
}

// Single-flight: concurrent callers (proactive tick + several 401 retries)
// share ONE network refresh so the single-use rotating token isn't raced
// within this tab (cross-tab races are absorbed by the server's grace window).
let refreshInFlight: Promise<string | null> | null = null;

export function refreshAccessToken(): Promise<string | null> {
  if (!refreshInFlight) {
    refreshInFlight = performRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

async function performRefresh(): Promise<string | null> {
  let access: string | null = null;
  let refresh: string | null = null;
  try {
    access = localStorage.getItem(TOKEN_STORAGE_KEY);
    refresh = localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
  if (!access && !refresh) return null;
  const body: Record<string, string> = {};
  // Preferred: the rotating refresh token. The access token rides along for
  // claim continuity (active store), and doubles as the DEPRECATED legacy
  // refresh credential when no refresh token exists yet (deploy window).
  if (refresh) body.refresh_token = refresh;
  if (access) body.token = access;
  try {
    // Raw axios (NOT the `api` instance): must bypass the interceptors so a
    // failing refresh can never recurse into another refresh.
    const resp = await axios.post(`${getSecureApiUrl()}/auth/refresh`, body, {
      timeout: 10000,
      headers: { 'Content-Type': 'application/json' },
    });
    const newAccess: string | undefined = resp.data?.access_token;
    if (!newAccess) return null;
    localStorage.setItem(TOKEN_STORAGE_KEY, newAccess);
    if (resp.data?.refresh_token) {
      localStorage.setItem(REFRESH_TOKEN_STORAGE_KEY, resp.data.refresh_token);
    }
    return newAccess;
  } catch {
    return null;
  }
}

/** Fire-and-forget proactive check; cheap enough to run per-request + on a timer. */
function maybeProactiveRefresh(): void {
  let token: string | null = null;
  try {
    token = localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return;
  }
  if (!token) return;
  if (shouldProactivelyRefresh(decodeJwtExpMs(token), Date.now(), readLastActivityMs())) {
    void refreshAccessToken();
  }
}

// Steady 30s cadence covers active users who aren't currently firing API calls
// (e.g. building a POS cart). Guarded out of vitest so tests own their timers.
if (typeof window !== 'undefined' && import.meta.env.MODE !== 'test') {
  window.setInterval(maybeProactiveRefresh, 30_000);
}

// Request interceptor - add auth token (retry counter lives on the config object)
api.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = localStorage.getItem(TOKEN_STORAGE_KEY);
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    // Active user making requests near token expiry -> renew in the background
    // (fire-and-forget; never blocks or fails this request).
    maybeProactiveRefresh();
    return config;
  },
  (error) => Promise.reject(error)
);

// Handle final error after retries exhausted
const handleFinalError = (error: AxiosError<{ message?: string; detail?: string | Array<Record<string, unknown>> }>) => {
  if (error.response?.status === 401) {
    // Clear auth state on unauthorized (refresh already failed or was not
    // possible by the time we get here -> same logout flow as before).
    localStorage.removeItem(TOKEN_STORAGE_KEY);
    localStorage.removeItem(REFRESH_TOKEN_STORAGE_KEY);
    localStorage.removeItem('ims_user');
    window.location.href = '/login';
  }

  // Build user-friendly error message
  let message: string;

  if (!error.response) {
    // Network error
    message = 'Network error. Please check your internet connection and try again.';
  } else if (error.response.status >= 500) {
    message = 'Server error. Please try again in a moment.';
  } else {
    // Handle various API error formats (detail can be string or array)
    const rawDetail = error.response?.data?.detail;
    if (typeof rawDetail === 'string') {
      message = rawDetail;
    } else if (Array.isArray(rawDetail) && rawDetail.length > 0) {
      message = rawDetail.map((d: Record<string, unknown>) => (d.msg as string) || String(d)).join('. ');
    } else {
      message = error.response?.data?.message || error.message || 'An error occurred';
    }
  }

  return Promise.reject(new Error(message));
};

// ── Additive camelCase aliasing ─────────────────────────────────────────
// Many components are typed in camelCase (Product.offerPrice, Store.storeName)
// while the API returns snake_case. Rather than a destructive global rename
// (which would break the many components that read snake_case directly), we ADD
// camelCase aliases alongside the original keys. Non-destructive: snake readers
// keep working; camel-typed reads start resolving. Binary/blob responses (file
// downloads) are skipped, and the whole thing is wrapped so it can never break
// a response.
const _toCamel = (s: string): string =>
  s.replace(/_+([a-z0-9])/g, (_m, c: string) => c.toUpperCase());

function _isPlainObject(v: unknown): v is Record<string, unknown> {
  if (v === null || typeof v !== 'object') return false;
  const proto = Object.getPrototypeOf(v);
  return proto === Object.prototype || proto === null;
}

function addCamelAliases(value: any): any {
  if (Array.isArray(value)) return value.map(addCamelAliases);
  if (_isPlainObject(value)) {
    const out: Record<string, any> = {};
    for (const [k, v] of Object.entries(value)) {
      const cv = addCamelAliases(v);
      out[k] = cv;
      if (k.includes('_')) {
        const camel = _toCamel(k);
        if (camel !== k && !(camel in value)) out[camel] = cv;
      }
    }
    return out;
  }
  return value;
}

// Response interceptor - additive camelCase aliasing + error retry logic
api.interceptors.response.use(
  (response) => {
    try {
      const rt = response.config?.responseType;
      if (
        rt !== 'blob' &&
        rt !== 'arraybuffer' &&
        response.data &&
        typeof response.data === 'object'
      ) {
        response.data = addCamelAliases(response.data);
      }
    } catch {
      // Never let aliasing break a response.
    }
    return response;
  },
  async (error: AxiosError<{ message?: string; detail?: string | Array<Record<string, unknown>> }>) => {
    const config = error.config as RetryTrackedConfig | undefined;

    // Don't retry if no config
    if (!config) {
      return handleFinalError(error);
    }

    // Reactive refresh-and-retry, ONCE per request: an expired access token
    // (laptop wake, throttled tab that missed the proactive window) gets one
    // silent refresh + replay instead of a logout. Auth endpoints themselves
    // are excluded (a failed login/refresh/logout must never loop), and a
    // failed refresh falls through to handleFinalError -> logout as before.
    const status401 = error.response?.status === 401;
    const reqUrl = String(config.url || '');
    const isAuthEndpoint =
      reqUrl.includes('/auth/login') ||
      reqUrl.includes('/auth/refresh') ||
      reqUrl.includes('/auth/logout');
    if (status401 && !isAuthEndpoint && !config.__retried401) {
      config.__retried401 = true;
      const newToken = await refreshAccessToken();
      if (newToken) {
        if (config.headers) {
          config.headers.Authorization = `Bearer ${newToken}`;
        }
        return api.request(config);
      }
    }

    const retryCount = config.__retryCount ?? 0;

    // Check if we should retry
    if (isRetryableError(error) && retryCount < MAX_RETRIES) {
      config.__retryCount = retryCount + 1;

      // Exponential backoff: 1s, 2s, 4s
      const backoffDelay = RETRY_DELAY_MS * Math.pow(2, retryCount);
      await delay(backoffDelay);
      return api.request(config);
    }

    return handleFinalError(error);
  }
);

export default api;
