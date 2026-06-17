// ============================================================================
// IMS 2.0 - Idle auto-logout hook
// ============================================================================
// Enforces an idle session timeout. The timer math is factored into a PURE
// helper (computeIdleState) so it can be unit-tested deterministically without
// the DOM. The hook layers the side-effecting pieces on top: a cross-tab
// "last activity" timestamp in localStorage, passive activity listeners, a 1s
// tick, and a `storage` cross-tab sync.
//
// Cross-tab model: all tabs share `ims_last_activity` (epoch ms). Activity in
// ANY tab updates it; every tab's 1s tick reads it, so warning/logout decisions
// are consistent across tabs. A logout in one tab unmounts the watcher in the
// others (AppLayout stops rendering after auth clears), so timers are torn down.
//
// enabled === false makes the hook a COMPLETE no-op: no timers, no listeners.

import { useCallback, useEffect, useRef, useState } from 'react';

export const LAST_ACTIVITY_KEY = 'ims_last_activity';

// Write to localStorage at most this often, so a stream of mousemove/scroll
// events can't thrash the storage (and the cross-tab `storage` event).
const ACTIVITY_WRITE_THROTTLE_MS = 5_000;

// How often the watcher re-evaluates idle time.
const TICK_MS = 1_000;

const ACTIVITY_EVENTS = [
  'mousemove',
  'mousedown',
  'keydown',
  'scroll',
  'touchstart',
  'click',
  'wheel',
] as const;

export interface IdleState {
  /** True when within the warning window (idle but not yet expired). */
  warn: boolean;
  /** True when the idle threshold has been crossed (time to log out). */
  expired: boolean;
  /** Whole seconds remaining until logout (0 when expired). During the warning
   *  window this is <= warnSeconds; before the warning window it is the full
   *  time remaining (callers typically only display it while `warn`). */
  remainingSec: number;
}

/**
 * Pure idle-state calculator. Given how long the session has been idle and the
 * policy, returns whether to warn, whether expired, and seconds remaining.
 *
 * - enabled === false  -> never warns, never expires.
 * - warn window starts at  idleMs >= minutes*60_000 - warnSeconds*1000.
 * - expiry at             idleMs >= minutes*60_000.
 * remainingSec is ceil()'d so a 0.4s remainder still shows "1s" until it truly
 * hits zero (avoids flashing 0 for a full second before logout fires).
 */
export function computeIdleState(
  idleMs: number,
  minutes: number,
  warnSeconds: number,
  enabled: boolean,
): IdleState {
  if (!enabled) {
    return { warn: false, expired: false, remainingSec: 0 };
  }
  const timeoutMs = minutes * 60_000;
  const warnAtMs = timeoutMs - warnSeconds * 1_000;
  const safeIdle = idleMs > 0 ? idleMs : 0;

  if (safeIdle >= timeoutMs) {
    return { warn: false, expired: true, remainingSec: 0 };
  }
  const remainingSec = Math.max(0, Math.ceil((timeoutMs - safeIdle) / 1_000));
  const warn = safeIdle >= warnAtMs;
  return { warn, expired: false, remainingSec };
}

function _now(): number {
  return Date.now();
}

function _readLastActivity(): number {
  try {
    const raw = localStorage.getItem(LAST_ACTIVITY_KEY);
    const n = raw ? parseInt(raw, 10) : NaN;
    if (Number.isFinite(n) && n > 0) return n;
  } catch {
    /* ignore */
  }
  return _now();
}

function _writeLastActivity(ts: number): void {
  try {
    localStorage.setItem(LAST_ACTIVITY_KEY, String(ts));
  } catch {
    /* ignore quota / private-mode errors */
  }
}

export interface UseIdleLogoutOptions {
  enabled: boolean;
  minutes: number;
  warnSeconds: number;
  /** Called exactly once when the idle threshold is crossed. */
  onLogout: () => void;
}

export interface UseIdleLogoutResult {
  /** True while the warning modal should be shown. */
  warning: boolean;
  /** Whole seconds remaining until logout (valid while `warning`). */
  remainingSec: number;
  /** Record activity NOW (resets the idle clock). For "Stay signed in". */
  stayActive: () => void;
}

/**
 * Idle auto-logout hook. Wires activity tracking + a 1s tick + cross-tab sync
 * onto the pure computeIdleState calculator. Returns warning state, the live
 * countdown, and a stayActive() resetter for the modal's primary button.
 */
export function useIdleLogout(opts: UseIdleLogoutOptions): UseIdleLogoutResult {
  const { enabled, minutes, warnSeconds, onLogout } = opts;

  const [warning, setWarning] = useState(false);
  const [remainingSec, setRemainingSec] = useState(0);

  // Refs so the long-lived interval/listeners always see current values without
  // being re-created on every render.
  const onLogoutRef = useRef(onLogout);
  onLogoutRef.current = onLogout;
  const loggedOutRef = useRef(false);
  const lastWriteRef = useRef(0);

  const recordActivity = useCallback(() => {
    if (loggedOutRef.current) return;
    const now = _now();
    // Throttle the localStorage write (and the cross-tab storage event).
    if (now - lastWriteRef.current >= ACTIVITY_WRITE_THROTTLE_MS) {
      lastWriteRef.current = now;
      _writeLastActivity(now);
    }
    // Always clear the in-memory warning immediately on activity so the modal
    // dismisses without waiting for the next localStorage write window.
    setWarning(false);
  }, []);

  const stayActive = useCallback(() => {
    if (loggedOutRef.current) return;
    const now = _now();
    lastWriteRef.current = now;
    _writeLastActivity(now); // force-write (bypass throttle) for an explicit action
    setWarning(false);
  }, []);

  useEffect(() => {
    // Disabled -> COMPLETE no-op. No timers, no listeners, reset any state.
    if (!enabled) {
      setWarning(false);
      setRemainingSec(0);
      return;
    }

    loggedOutRef.current = false;
    lastWriteRef.current = 0;
    // Seed the activity clock on (re)mount so a fresh load starts the timer now.
    _writeLastActivity(_now());

    const handleActivity = () => recordActivity();

    ACTIVITY_EVENTS.forEach((evt) => {
      window.addEventListener(evt, handleActivity, { passive: true });
    });

    // Cross-tab: another tab's activity (or stayActive) updates the shared key.
    const handleStorage = (e: StorageEvent) => {
      if (e.key === LAST_ACTIVITY_KEY) {
        // Someone was active elsewhere -> clear our warning; the tick re-derives.
        if (!loggedOutRef.current) setWarning(false);
      }
    };
    window.addEventListener('storage', handleStorage);

    const tick = () => {
      if (loggedOutRef.current) return;
      const idleMs = _now() - _readLastActivity();
      const state = computeIdleState(idleMs, minutes, warnSeconds, enabled);
      if (state.expired) {
        loggedOutRef.current = true;
        setWarning(false);
        try {
          onLogoutRef.current();
        } catch {
          /* never let a logout handler error wedge the timer */
        }
        return;
      }
      setWarning(state.warn);
      setRemainingSec(state.remainingSec);
    };

    // Evaluate immediately, then on a steady 1s cadence.
    tick();
    const intervalId = window.setInterval(tick, TICK_MS);

    return () => {
      window.clearInterval(intervalId);
      ACTIVITY_EVENTS.forEach((evt) => {
        window.removeEventListener(evt, handleActivity);
      });
      window.removeEventListener('storage', handleStorage);
    };
  }, [enabled, minutes, warnSeconds, recordActivity]);

  return { warning, remainingSec, stayActive };
}
