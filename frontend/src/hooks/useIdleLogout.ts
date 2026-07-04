// ============================================================================
// IMS 2.0 - Idle auto-logout hook
// ============================================================================
// Enforces an idle session timeout. The timer math is factored into a PURE
// helper (computeIdleState) so it can be unit-tested deterministically without
// the DOM. The hook layers the side-effecting pieces on top: a cross-tab
// "last activity" timestamp in localStorage, passive activity listeners, a 1s
// tick, a `storage` cross-tab sync, and tab-visibility tracking.
//
// Cross-tab model: all tabs share `ims_last_activity` (epoch ms). Activity in
// ANY tab updates it; every tab's 1s tick reads it, so warning/logout decisions
// are consistent across tabs. A logout in one tab unmounts the watcher in the
// others (AppLayout stops rendering after auth clears), so timers are torn down.
//
// ENFORCEMENT MODEL (owner decision 2026-07-04 — "popup shows but logout does
// not happen"): an unattended terminal MUST actually sign out.
//   1. EXPIRED means LOGOUT. Once idle crosses the timeout, logout fires on the
//      next tick — even if the tab is hidden (throttled hidden-tab ticks still
//      arrive ~1/min, and visibilitychange re-ticks immediately on return).
//      The previous design deferred logout for hidden tabs and restarted a
//      fresh warning countdown on return; in practice that made auto-logout
//      untriggerable (walk away -> hidden -> no logout; come back -> fresh
//      countdown -> first mouse-move cancels). The POS cart is AUTO-PARKED by
//      the watcher before logout, so a missed warning costs only a re-login.
//   2. Once the warning modal is up, PASSIVE activity (mousemove/scroll/keys)
//      in THIS tab no longer cancels it — only the explicit "Stay signed in"
//      button (stayActive) does. Otherwise anyone could keep a session alive
//      by wiggling the mouse at an unattended terminal.
//   3. REAL activity in ANOTHER tab (shared `ims_last_activity` write) still
//      cancels the warning everywhere: the person is genuinely working in this
//      browser profile, and logging out one tab would kill the shared token
//      under their feet.
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
 * Idle auto-logout hook. Wires activity tracking + a 1s tick + cross-tab sync +
 * tab-visibility tracking onto the pure computeIdleState calculator. Returns
 * warning state, the live countdown, and a stayActive() resetter for the
 * modal's primary button.
 *
 * Enforcement (see file header): expiry ALWAYS logs out — hidden tabs included —
 * and once the warning is showing, only stayActive() (the modal button) or real
 * activity in ANOTHER tab cancels it. Passive same-tab activity is ignored
 * while the warning is up.
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
  // Mirror of `warning` for the activity listeners: while the modal is up,
  // passive activity must NOT reset the clock or dismiss it (only the explicit
  // stayActive button, or another tab's activity, may).
  const warningRef = useRef(false);

  const recordActivity = useCallback(() => {
    if (loggedOutRef.current) return;
    // Warning showing -> passive activity is deliberately IGNORED. Mouse
    // wiggling at an unattended terminal must not keep the session alive; the
    // user has to click "Stay signed in".
    if (warningRef.current) return;
    const now = _now();
    // Throttle the localStorage write (and the cross-tab storage event).
    if (now - lastWriteRef.current >= ACTIVITY_WRITE_THROTTLE_MS) {
      lastWriteRef.current = now;
      _writeLastActivity(now);
    }
  }, []);

  const stayActive = useCallback(() => {
    if (loggedOutRef.current) return;
    const now = _now();
    lastWriteRef.current = now;
    _writeLastActivity(now); // force-write (bypass throttle) for an explicit action
    warningRef.current = false;
    setWarning(false);
  }, []);

  useEffect(() => {
    // Disabled -> COMPLETE no-op. No timers, no listeners, reset any state.
    if (!enabled) {
      warningRef.current = false;
      setWarning(false);
      setRemainingSec(0);
      return;
    }

    loggedOutRef.current = false;
    lastWriteRef.current = 0;
    warningRef.current = false;
    // Seed the activity clock on (re)mount so a fresh load starts the timer now.
    _writeLastActivity(_now());

    const handleActivity = () => recordActivity();

    ACTIVITY_EVENTS.forEach((evt) => {
      window.addEventListener(evt, handleActivity, { passive: true });
    });

    // Cross-tab: another tab's activity (or stayActive) updates the shared key.
    // That is REAL use of this browser profile -> cancel our warning; the next
    // tick re-derives the (now small) idle time.
    const handleStorage = (e: StorageEvent) => {
      if (e.key === LAST_ACTIVITY_KEY) {
        if (!loggedOutRef.current) {
          warningRef.current = false;
          setWarning(false);
        }
      }
    };
    window.addEventListener('storage', handleStorage);

    // Core decision: expired -> logout, exactly once. No hidden-tab deferral —
    // an unattended terminal must sign out (the watcher auto-parks the POS cart
    // first, so nothing is lost). Hidden tabs tick throttled (~1/min) which is
    // fine: logout lands within a minute of expiry, and visibilitychange ticks
    // immediately when the user returns.
    const tick = () => {
      if (loggedOutRef.current) return;
      const now = _now();
      const idleMs = now - _readLastActivity();
      const state = computeIdleState(idleMs, minutes, warnSeconds, enabled);

      if (state.expired) {
        loggedOutRef.current = true;
        warningRef.current = false;
        setWarning(false);
        try {
          onLogoutRef.current();
        } catch {
          /* never let a logout handler error wedge the timer */
        }
        return;
      }

      if (state.warn) {
        warningRef.current = true;
        setWarning(true);
        setRemainingSec(state.remainingSec);
        return;
      }

      // Active: not warning, not expired.
      warningRef.current = false;
      setWarning(false);
      setRemainingSec(state.remainingSec);
    };

    // When the tab becomes visible/focused again, re-evaluate immediately rather
    // than waiting up to TICK_MS — a tab returned to after expiry logs out at
    // once instead of lingering.
    const handleVisibility = () => {
      if (loggedOutRef.current) return;
      tick();
    };
    document.addEventListener('visibilitychange', handleVisibility);
    // focus/blur as a belt-and-suspenders signal (some browsers fire these more
    // reliably than visibilitychange when alt-tabbing between apps).
    window.addEventListener('focus', handleVisibility);

    // Evaluate immediately, then on a steady 1s cadence.
    tick();
    const intervalId = window.setInterval(tick, TICK_MS);

    return () => {
      window.clearInterval(intervalId);
      ACTIVITY_EVENTS.forEach((evt) => {
        window.removeEventListener(evt, handleActivity);
      });
      window.removeEventListener('storage', handleStorage);
      document.removeEventListener('visibilitychange', handleVisibility);
      window.removeEventListener('focus', handleVisibility);
    };
  }, [enabled, minutes, warnSeconds, recordActivity]);

  return { warning, remainingSec, stayActive };
}
