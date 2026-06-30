// ============================================================================
// IMS 2.0 - "New version available" update check
// ============================================================================
// Detects when a NEWER frontend deploy is live than the one running in this
// tab, so we can prompt the user to refresh. Background:
//
// The SPA is served stale-after-deploy if the HTML (which points at the hashed
// JS bundle) is edge-cached. vercel.json now sends no-cache on all HTML/route
// responses AND on /version.json, so a fresh deploy is visible immediately on
// the next request. This hook turns "the user has the page open" into a refresh
// prompt without forcing a reload out from under them.
//
// Mechanism: the running bundle is stamped with __APP_BUILD_ID__ at build time
// (vite `define`). The same id is written to dist/version.json. On an interval
// and whenever the tab regains focus / becomes visible, we fetch /version.json
// with {cache:'no-store'} and compare. If the served build differs from the
// running build, an update is available.
//
// Contract: FAIL-SOFT. Any network/parse error is swallowed -> no banner, never
// throws, never crashes the app. The check is also a no-op when the running
// build id is empty/unknown (can't meaningfully compare).
//
// The hour-zero deploy where version.json doesn't exist yet returns 404/HTML;
// fetchServedBuild treats a non-OK / non-JSON response as "unknown" -> no false
// positive.

import { useCallback, useEffect, useRef, useState } from 'react';

/** How often to poll /version.json while the tab is open. */
export const UPDATE_CHECK_INTERVAL_MS = 60_000;

/** The build id baked into THIS running bundle (vite define). */
export function runningBuildId(): string {
  try {
    // __APP_BUILD_ID__ is replaced at build time. In a raw test/dev context
    // where it isn't defined, guard against a ReferenceError.
    return typeof __APP_BUILD_ID__ === 'string' ? __APP_BUILD_ID__ : '';
  } catch {
    return '';
  }
}

/**
 * Pure decision: should we show the "update available" banner?
 * True only when BOTH ids are known (non-empty) AND they differ. An unknown
 * served build (fetch failed / not yet deployed) or unknown running build
 * yields false -> no false positives.
 */
export function isUpdateAvailable(running: string, served: string | null): boolean {
  if (!running) return false;
  if (!served) return false;
  return running !== served;
}

/**
 * Fetch the currently-served build id from /version.json. Returns null on any
 * failure (network, non-OK, non-JSON, missing field) so callers treat it as
 * "unknown" rather than crashing. Always bypasses the cache.
 */
export async function fetchServedBuild(
  signal?: AbortSignal,
): Promise<string | null> {
  try {
    const res = await fetch('/version.json', { cache: 'no-store', signal });
    if (!res || !res.ok) return null;
    const data = await res.json();
    const build = data && typeof data.build === 'string' ? data.build : null;
    return build || null;
  } catch {
    return null;
  }
}

export interface UseUpdateAvailableOptions {
  /** Override the poll interval (ms). Defaults to UPDATE_CHECK_INTERVAL_MS. */
  intervalMs?: number;
  /** Disable the check entirely (no timers, no fetches). Default enabled. */
  enabled?: boolean;
}

/**
 * Returns whether a newer build is available. Polls /version.json on an
 * interval and on tab focus/visibility. Fail-soft throughout: a failed check
 * leaves the flag unchanged and never throws.
 */
export function useUpdateAvailable(
  opts: UseUpdateAvailableOptions = {},
): { updateAvailable: boolean } {
  const { intervalMs = UPDATE_CHECK_INTERVAL_MS, enabled = true } = opts;
  const [updateAvailable, setUpdateAvailable] = useState(false);

  // Stable across renders; the running id never changes within a session.
  const runningRef = useRef(runningBuildId());

  const check = useCallback(async (signal?: AbortSignal) => {
    const running = runningRef.current;
    if (!running) return; // can't compare without a known running build
    const served = await fetchServedBuild(signal);
    if (signal?.aborted) return;
    if (isUpdateAvailable(running, served)) {
      setUpdateAvailable(true); // sticky: once true, stays true until reload
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();

    // Initial check shortly after mount (don't block first paint).
    void check(controller.signal);

    const id = window.setInterval(() => {
      void check(controller.signal);
    }, intervalMs);

    // Re-check when the user returns to the tab — the most likely moment a new
    // deploy happened while they were away.
    const onFocus = () => void check(controller.signal);
    const onVisibility = () => {
      if (typeof document !== 'undefined' && !document.hidden) {
        void check(controller.signal);
      }
    };
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisibility);

    return () => {
      controller.abort();
      window.clearInterval(id);
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [enabled, intervalMs, check]);

  return { updateAvailable };
}
