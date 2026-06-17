// ============================================================================
// IMS 2.0 - Idle auto-logout session policy (runtime reader)
// ============================================================================
// Mirrors the constants/gstRuntime.ts loadPricingMode pattern: the policy lives
// on the backend (the system_settings singleton) and is served on the PUBLIC
// /health endpoint so EVERY authenticated user -- including SALES_STAFF -- can
// read it without an admin-gated call. We read it at RUNTIME (Vite bakes
// build-time env, so a config change wouldn't reach a pre-built FE otherwise).
//
// Fail-soft: until /health answers (or if the fetch fails) getSessionPolicy()
// returns the last-known policy from localStorage, falling back to the defaults
// { enabled:true, minutes:15, warnSeconds:60 } -- identical to the backend
// defaults so behaviour is consistent before the network round-trips.

import api from '../services/api/client';

export interface SessionPolicy {
  enabled: boolean;
  minutes: number;
  warnSeconds: number;
}

const DEFAULT_POLICY: SessionPolicy = { enabled: true, minutes: 15, warnSeconds: 60 };

const LS_KEY = 'ims_session_policy';

function _sanitize(raw: unknown): SessionPolicy {
  const p = (raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {});
  const minutes = Number(p.minutes);
  const warnSeconds = Number(p.warnSeconds);
  return {
    enabled: p.enabled === undefined ? DEFAULT_POLICY.enabled : Boolean(p.enabled),
    minutes: Number.isFinite(minutes) && minutes > 0 ? minutes : DEFAULT_POLICY.minutes,
    warnSeconds:
      Number.isFinite(warnSeconds) && warnSeconds > 0 ? warnSeconds : DEFAULT_POLICY.warnSeconds,
  };
}

// Initialise from the last-known localStorage snapshot so a reload BEFORE
// /health answers still has the most recent policy (not a stale build constant).
function _readInitial(): SessionPolicy {
  try {
    const cached = localStorage.getItem(LS_KEY);
    if (cached) return _sanitize(JSON.parse(cached));
  } catch {
    /* ignore parse / private-mode errors */
  }
  return { ...DEFAULT_POLICY };
}

let _policy: SessionPolicy = _readInitial();

/** Fetch the active idle auto-logout policy from the backend `/health`. Safe to
 *  call repeatedly; NEVER throws; keeps the prior value (last-known or default)
 *  on error. Mirrors the policy into localStorage for the next cold load. */
export async function loadSessionPolicy(): Promise<void> {
  try {
    const res = await api.get('/health');
    const al = res.data?.auto_logout;
    if (al && typeof al === 'object') {
      _policy = _sanitize({
        enabled: al.enabled,
        minutes: al.minutes,
        // Backend serves snake_case `warn_seconds`; the api client also adds a
        // camelCase `warnSeconds` alias, so accept either.
        warnSeconds: al.warn_seconds ?? al.warnSeconds,
      });
      try {
        localStorage.setItem(LS_KEY, JSON.stringify(_policy));
      } catch {
        /* ignore quota / private-mode errors */
      }
    }
  } catch {
    /* keep current value (last-known from localStorage, or defaults) */
  }
}

/** Synchronous accessor for the current idle auto-logout policy. */
export function getSessionPolicy(): SessionPolicy {
  return _policy;
}
