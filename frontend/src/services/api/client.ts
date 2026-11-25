// ============================================================================
// IMS 2.0 - API Client (shared axios instance & utilities)
// ============================================================================

import axios from 'axios';
import type { AxiosInstance, AxiosError, InternalAxiosRequestConfig } from 'axios';
import { _storedActivity, IDLE_LOGIN_PATH } from '../../hooks/useIdleLogout';
import { SIGNED_OUT_EVENT, dropPaletteRecents } from './signOutEvent';

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

// Methods that only READ: replaying one can never move money twice.
const SAFE_METHODS = ['get', 'head', 'options'];

// Check if error is retryable. Do NOT retry network/CORS errors - they won't
// be fixed by retrying. A 5xx is retried ONLY for a read: a write that failed
// with a 5xx may already have landed (PR #1264: an "unclear" store credit
// answers 503 "do NOT redo" -- an automatic replay redid it). A 429 means the
// server refused the request before doing anything, so any method retries.
export const isRetryableError = (error: AxiosError): boolean => {
  // CORS errors have no response - don't retry these
  if (!error.response) {
    return false;
  }
  // Rate limiting (429) - retry with backoff
  if (error.response.status === 429) {
    return true;
  }
  // Server errors (5xx) - these might be temporary; reads only
  if (error.response.status >= 500 && error.response.status < 600) {
    return SAFE_METHODS.includes(String(error.config?.method || 'get').toLowerCase());
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

/** A JWT's payload claims (NOT verified - the server does that), or null if
 *  unparseable. The one client-side reader of token claims. */
export function decodeJwtClaims(token: string | null | undefined): Record<string, any> | null {
  try {
    const part = (token || '').split('.')[1];
    if (!part) return null;
    const payload = JSON.parse(atob(part.replace(/-/g, '+').replace(/_/g, '/')));
    return payload && typeof payload === 'object' ? payload : null;
  } catch {
    return null;
  }
}

// A token with no chain (`sid`) that THIS tab refreshed while it was still the
// stored one -> the chain that refresh gave it. A missing `sid` proves nothing
// about continuity: only this recorded first upgrade does (#1372 round 4).
// ponytail: tab memory only; another tab holding a request of the same legacy
// token gets "not saved" instead of a replay (fail closed, deploy window only).
const legacyUpgrades = new Map<string, string>();

/** One sign-in? Same person on the same refresh chain (`sid`); a token that
 *  was rotated or moved shop stays in it, a new login is a new chain. An answer
 *  to a request of another sign-in must not touch this one (#1372 review). */
export function sameSession(a: string | null | undefined, b: string | null | undefined): boolean {
  if (a === b) return true;
  const [x, y] = [decodeJwtClaims(a), decodeJwtClaims(b)];
  if (!x || !y || x.user_id !== y.user_id) return false;
  if (x.sid && y.sid) return x.sid === y.sid;
  return (!!y.sid && legacyUpgrades.get(a!) === y.sid) || (!!x.sid && legacyUpgrades.get(b!) === x.sid);
}

/** The token a request really went out with. */
export function sentToken(config?: { headers?: unknown }): string {
  const header = (config?.headers as { Authorization?: unknown } | undefined)?.Authorization;
  return String(header ?? '').replace(/^Bearer /, '');
}

/** Exp claim of a JWT in epoch MILLISECONDS, or null if unparseable. */
export function decodeJwtExpMs(token: string): number | null {
  const exp = decodeJwtClaims(token)?.exp;
  return typeof exp === 'number' ? exp * 1000 : null;
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


// Single-flight: concurrent callers (proactive tick + several 401 retries)
// share ONE network refresh so the single-use rotating token isn't raced
// within this tab (cross-tab races are absorbed by the server's grace window).
let refreshInFlight: Promise<string | null> | null = null;
// Set when the server refused a refresh as idle (SESSION_IDLE): the sign-out
// then lands on /login?reason=idle like the browser's own idle check. Sticky
// until a refresh succeeds (a fresh login stores a new refresh token).
let refreshRefusedIdle = false;

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
  // The session ended idle: never re-present the dead chain, and never fall
  // back to the legacy access-token refresh, which would start a new chain.
  if (refreshRefusedIdle && !refresh) return null;
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
    // The stored token moved while this answer travelled (shop switch, another
    // tab's refresh, sign-out, a new sign-in): the access token here is older
    // than it and is dropped. Only the rotated refresh token of the SAME
    // sign-in is kept: the one it replaced is spent, and presenting that again
    // past the server's grace window revokes the whole chain.
    const current = localStorage.getItem(TOKEN_STORAGE_KEY);
    if (current !== access || localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY) !== refresh) {
      if (!current || !sameSession(current, newAccess)) return null;
      if (resp.data?.refresh_token) {
        localStorage.setItem(REFRESH_TOKEN_STORAGE_KEY, resp.data.refresh_token);
      }
      return current;
    }
    localStorage.setItem(TOKEN_STORAGE_KEY, newAccess);
    const sid = decodeJwtClaims(newAccess)?.sid;
    if (access && sid && !decodeJwtClaims(access)?.sid) legacyUpgrades.set(access, sid);
    if (resp.data?.refresh_token) {
      localStorage.setItem(REFRESH_TOKEN_STORAGE_KEY, resp.data.refresh_token);
    }
    syncCachedUserShops(newAccess);
    refreshRefusedIdle = false;
    return newAccess;
  } catch (e) {
    const detail = axios.isAxiosError(e) ? e.response?.data?.detail : undefined;
    // The refusal is about the sign-in that asked, not a later one.
    if (detail?.code === 'SESSION_IDLE' && sameSession(access, localStorage.getItem(TOKEN_STORAGE_KEY))) {
      refreshRefusedIdle = true;
      try {
        localStorage.removeItem(REFRESH_TOKEN_STORAGE_KEY);
      } catch {
        /* storage unavailable: performRefresh cannot read the token either */
      }
    }
    return null;
  }
}

/** Fired after syncCachedUserShops rewrites the cached user; AuthContext
 *  re-reads it so the screen follows at once. */
export const USER_SHOPS_SYNCED_EVENT = 'ims:user-shops-synced';

/** The token is the one source of a login's shops: the server scopes every
 *  read by its claims. After a refresh the cached user (ims_user) takes its
 *  active shop and assigned shops from the new token, so a login whose shop
 *  the server dropped -- an area manager an older login defaulted to a shop
 *  he was never given -- is shown the no-shop message (hasNoShop), not a
 *  screen of 403s for a shop the client still remembered. */
export function syncCachedUserShops(token: string): void {
  try {
    const claims = decodeJwtClaims(token);
    if (!claims) return;
    const raw = localStorage.getItem('ims_user');
    if (!raw) return;
    const user = JSON.parse(raw);
    if (claims.user_id !== user.id) return; // another person's shops are not this cache's
    const activeStoreId = claims.active_store_id || '';
    const storeIds = Array.isArray(claims.store_ids) ? claims.store_ids : [];
    // Unchanged shops (nearly every refresh): nothing re-renders.
    if (user.activeStoreId === activeStoreId && JSON.stringify(user.storeIds) === JSON.stringify(storeIds)) return;
    localStorage.setItem('ims_user', JSON.stringify({ ...user, activeStoreId, storeIds }));
    window.dispatchEvent(new Event(USER_SHOPS_SYNCED_EVENT));
  } catch {
    /* an unreadable token or cache changes nothing */
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
  if (shouldProactivelyRefresh(decodeJwtExpMs(token), Date.now(), _storedActivity())) {
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

// P0-3 (launch gate): the ONE error shape this client delivers. The
// interceptor has always rejected with a bare `new Error(message)` — the
// axios `.response` never leaves this file — yet screens kept reading
// `err.response.data.detail`, so every structured recovery branch
// (EXPRESS_PARTIAL's banner, ATTACHMENT_* re-prompt) was dead and staff saw
// a generic "try again" — the retry that mints a delivery's stock twice.
// ApiError keeps the user-facing `message` exactly as before and ADDS the
// structured fields alongside it: `status`, the backend's `code`, and the
// raw `detail`. Components key recovery flows off THESE (the
// DuplicateProductError pattern), never off `.response`.
export class ApiError extends Error {
  status?: number;
  code?: string;
  detail?: unknown;
  constructor(
    message: string,
    opts: { status?: number; code?: string; detail?: unknown } = {},
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = opts.status;
    this.code = opts.code;
    this.detail = opts.detail;
  }
}

export const PUSH_TIMEOUT_MESSAGE =
  'The push is still running on the server. Refresh in a minute to see the result; do not press again.';

// The Catalog page's permanent delete keeps running on the server after the
// request times out (it goes one product at a time, Shopify first).
export const PURGE_TIMEOUT_MESSAGE =
  'The permanent delete is still running on the server. Refresh in a minute to see the result; do not press again.';

// axios timeout = ECONNABORTED (ETIMEDOUT only under clarifyTimeoutError);
// a plain network failure is ERR_NETWORK and keeps the generic message.
const isTimeout = (error: AxiosError): boolean =>
  error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT';
const isPushTimeout = (error: AxiosError): boolean =>
  isTimeout(error) && String(error.config?.url || '').includes('/online-store/push/');
const isPurgeTimeout = (error: AxiosError): boolean =>
  isTimeout(error) && String(error.config?.url || '').includes('/catalog/products/bulk-purge');

// A write (anything but GET/HEAD/OPTIONS) whose answer was lost -- a 5xx that
// is not a deliberate 503 stop, or no response at all -- may already have
// landed: "try again" is the second press that does it twice (money review 12).
export const WRITE_UNCONFIRMED_MESSAGE =
  'The server did not confirm this - it may already be saved. Reload and check before pressing again.';
// POSTs that only READ (a price quote) or sign in: nothing to have "saved".
const READ_LIKE_POSTS = [/\/auth\//, /\/orders\/quote(\?|$)/, /\/returns\/quote(\?|$)/];
const isWrite = (error: AxiosError): boolean =>
  !SAFE_METHODS.includes(String(error.config?.method || 'get').toLowerCase())
  && !READ_LIKE_POSTS.some((re) => re.test(String(error.config?.url || '')));

// Pure transform: axios error -> the ApiError components actually receive.
// Exported so tests reject fixtures through the REAL production transform
// instead of hand-built axios shapes the client never delivers (the
// hollow-double class: green tests over a dead code path).
export function buildApiError(
  error: AxiosError<{ message?: string; detail?: string | Array<Record<string, unknown>> }>,
): ApiError {
  // Build user-friendly error message
  let message: string;

  if (!error.response) {
    // A timed-out /online-store/push/* request is NOT a dead connection: the
    // server keeps running the sweep (prod 2026-09-05: 10 products processed in
    // ~11 s behind a red "check your internet" toast). Telling the operator to
    // "try again" is the second press that pushes twice.
    message = isPushTimeout(error)
      ? PUSH_TIMEOUT_MESSAGE
      : isPurgeTimeout(error)
        ? PURGE_TIMEOUT_MESSAGE
        : isWrite(error)
          ? WRITE_UNCONFIRMED_MESSAGE
          : 'Network error. Please check your internet connection and try again.';
  } else if (error.response.status >= 500) {
    // 503 is the only 5xx this API raises DELIBERATELY: it is a safety stop, not
    // a crash. Goods-receipt acceptance uses it to say "N units were received
    // before we stopped — the units already received are safe and will not be
    // counted twice", which is the sentence that stops a clerk recounting a
    // half-received carton and hand-adding the difference (a double mint that
    // sits outside every server-side guard). So a STRING detail on a 503
    // survives; every other 5xx keeps the generic text so no internal error
    // message can leak to a user.
    // A structured 503 ({code, message} -- e.g. CATALOGUE_UNREADABLE, "could
    // not check the catalogue, so nothing was created") is the same deliberate
    // stop: its message survives too, never "Server error" (R2-18).
    const rawStop = error.response.status === 503 ? error.response?.data?.detail : undefined;
    const safeStopDetail =
      rawStop && typeof rawStop === 'object' && !Array.isArray(rawStop)
        ? (rawStop as unknown as { message?: unknown }).message
        : rawStop;
    message =
      typeof safeStopDetail === 'string' && safeStopDetail.trim()
        ? safeStopDetail
        : isWrite(error)
          ? WRITE_UNCONFIRMED_MESSAGE
          : 'Server error. Please try again in a moment.';
  } else {
    // Handle various API error formats (detail can be string or array)
    const rawDetail = error.response?.data?.detail;
    if (typeof rawDetail === 'string') {
      message = rawDetail;
    } else if (Array.isArray(rawDetail) && rawDetail.length > 0) {
      message = rawDetail.map((d: Record<string, unknown>) => (d.msg as string) || String(d)).join('. ');
    } else if (
      rawDetail &&
      typeof rawDetail === 'object' &&
      !Array.isArray(rawDetail) &&
      typeof (rawDetail as unknown as { message?: unknown }).message === 'string'
    ) {
      // Structured detail: FastAPI nests everything under `detail`, so a dict
      // detail used to fall through to axios's bare "Request failed with status
      // code 409". Goods-receipt express deliberately sends recovery guidance
      // that way for a receipt which may already hold real stock.
      message = (rawDetail as unknown as { message: string }).message;
    } else {
      message = error.response?.data?.message || error.message || 'An error occurred';
    }
  }

  const status = error.response?.status;
  // Structured fields ride along for 4xx only: the 5xx generic-text rule
  // above exists so no internal error can leak to a user, and stripping the
  // payload keeps a component from rendering what the message rule hid.
  const rawDetail = status && status < 500 ? error.response?.data?.detail : undefined;
  const code =
    rawDetail &&
    typeof rawDetail === 'object' &&
    !Array.isArray(rawDetail) &&
    typeof (rawDetail as unknown as { code?: unknown }).code === 'string'
      ? (rawDetail as unknown as { code: string }).code
      : undefined;
  return new ApiError(message, { status, code, detail: rawDetail });
}

// The answer to a request sent under another sign-in or another shop, or
// signed out: it says nothing about the session in storage now, so it never
// refreshes, replays or signs that person out. The caller just gets its error.
const fromOlderContext = (config?: { headers?: unknown }): boolean => {
  const sent = sentToken(config);
  const now = localStorage.getItem(TOKEN_STORAGE_KEY) || '';
  return !sameSession(sent, now)
    || (decodeJwtClaims(sent)?.active_store_id || '') !== (decodeJwtClaims(now)?.active_store_id || '');
};

// Only a read or a write the server refused (401, 429) is ever replayed, so
// "not saved" is the truth. Its status stays: the till reads a 4xx as refused.
export const REPLAY_REFUSED_MESSAGE =
  'Not saved: the shop or sign-in changed while this was waiting. Check the shop and try again.';
const refuseReplay = (error: AxiosError) =>
  Promise.reject(new ApiError(REPLAY_REFUSED_MESSAGE, { status: error.response?.status }));

// THE way a request goes out again. Everything waited on before it (a refresh,
// a backoff) is time for a shop switch or the next person's sign-in, so the
// check sits here, after the wait; the request interceptor then stamps the
// same token this just compared.
const replay = (config: RetryTrackedConfig, error: AxiosError) =>
  fromOlderContext(config) ? refuseReplay(error) : api.request(config);

// Handle final error after retries exhausted
const handleFinalError = (error: AxiosError<{ message?: string; detail?: string | Array<Record<string, unknown>> }>) => {
  // A refused sign-in (wrong password) ends no one's session (owner 2026-11-07).
  const signInRefused = String(error.config?.url || '').includes('/auth/login');
  if (error.response?.status === 401 && !signInRefused && !fromOlderContext(error.config)) {
    // Clear auth state on unauthorized (refresh already failed or was not
    // possible by the time we get here -> same logout flow as before).
    // The POS goes through THE one sign-out step (a pending bill is parked,
    // then the draft cleared) -- by event, as the store cannot be imported
    // here without an import cycle (money review 23).
    try {
      const u = JSON.parse(localStorage.getItem('ims_user') || 'null');
      window.dispatchEvent(new CustomEvent(SIGNED_OUT_EVENT, { detail: { userId: u?.id, storeId: u?.activeStoreId } }));
    } catch { /* never block the sign-out */ }
    localStorage.removeItem(TOKEN_STORAGE_KEY);
    localStorage.removeItem(REFRESH_TOKEN_STORAGE_KEY);
    localStorage.removeItem('ims_user');
    dropPaletteRecents();
    window.location.href = refreshRefusedIdle ? IDLE_LOGIN_PATH : '/login';
  }

  return Promise.reject(buildApiError(error));
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

// A product's `attributes` is a DICTIONARY (its keys are data: brand_name,
// model_no, a custom field), not a transport shape -- aliased, every editor and
// drawer showed Brand Name beside BrandName and the form saved the aliases back
// (CAT-07). Its keys pass through exactly as stored.
const _DICTIONARY_KEYS = new Set(['attributes']);

export function addCamelAliases(value: any): any {
  if (Array.isArray(value)) return value.map(addCamelAliases);
  if (_isPlainObject(value)) {
    const out: Record<string, any> = {};
    for (const [k, v] of Object.entries(value)) {
      const cv = _DICTIONARY_KEYS.has(k) && _isPlainObject(v) ? v : addCamelAliases(v);
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
    const refreshable = status401 && !isAuthEndpoint && !config.__retried401;

    if (fromOlderContext(config)) {
      return refreshable || isRetryableError(error) ? refuseReplay(error) : Promise.reject(buildApiError(error));
    }

    if (refreshable) {
      config.__retried401 = true;
      if (await refreshAccessToken()) return replay(config, error);
    }

    const retryCount = config.__retryCount ?? 0;

    // Check if we should retry
    if (isRetryableError(error) && retryCount < MAX_RETRIES) {
      config.__retryCount = retryCount + 1;

      // Exponential backoff: 1s, 2s, 4s
      const backoffDelay = RETRY_DELAY_MS * Math.pow(2, retryCount);
      await delay(backoffDelay);
      return replay(config, error);
    }

    return handleFinalError(error);
  }
);

export default api;
