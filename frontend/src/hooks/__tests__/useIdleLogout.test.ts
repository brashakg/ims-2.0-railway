import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import {
  computeIdleState,
  useIdleLogout,
  LAST_ACTIVITY_KEY,
} from '../useIdleLogout';

// Pure timer math for the idle auto-logout hook. Deterministic: no DOM, no
// clocks -- we feed idleMs directly. minutes=15, warnSeconds=60 is the default
// policy; timeout = 900_000ms, warn window opens at 840_000ms.
const MIN = 15;
const WARN = 60;
const TIMEOUT_MS = MIN * 60_000; // 900_000
const WARN_AT_MS = TIMEOUT_MS - WARN * 1_000; // 840_000

describe('computeIdleState', () => {
  it('does not warn or expire while well within the active window', () => {
    const s = computeIdleState(0, MIN, WARN, true);
    expect(s.warn).toBe(false);
    expect(s.expired).toBe(false);
    expect(s.remainingSec).toBe(TIMEOUT_MS / 1000); // 900

    const mid = computeIdleState(500_000, MIN, WARN, true);
    expect(mid.warn).toBe(false);
    expect(mid.expired).toBe(false);
  });

  it('enters the warning window at exactly minutes - warnSeconds', () => {
    const justBefore = computeIdleState(WARN_AT_MS - 1, MIN, WARN, true);
    expect(justBefore.warn).toBe(false);
    expect(justBefore.expired).toBe(false);

    const atWarn = computeIdleState(WARN_AT_MS, MIN, WARN, true);
    expect(atWarn.warn).toBe(true);
    expect(atWarn.expired).toBe(false);
    // At the start of the warning window, ~warnSeconds remain.
    expect(atWarn.remainingSec).toBe(WARN);
  });

  it('counts down remaining seconds during the warning window', () => {
    // 30s into the warning window -> ~30s remaining.
    const s = computeIdleState(WARN_AT_MS + 30_000, MIN, WARN, true);
    expect(s.warn).toBe(true);
    expect(s.expired).toBe(false);
    expect(s.remainingSec).toBe(30);
  });

  it('expires exactly at the timeout (logout trigger)', () => {
    const justBefore = computeIdleState(TIMEOUT_MS - 1, MIN, WARN, true);
    expect(justBefore.expired).toBe(false);
    expect(justBefore.warn).toBe(true);

    const atTimeout = computeIdleState(TIMEOUT_MS, MIN, WARN, true);
    expect(atTimeout.expired).toBe(true);
    expect(atTimeout.warn).toBe(false);
    expect(atTimeout.remainingSec).toBe(0);

    const past = computeIdleState(TIMEOUT_MS + 60_000, MIN, WARN, true);
    expect(past.expired).toBe(true);
  });

  it('is a complete no-op when disabled (never warns, never expires)', () => {
    // Even far past the timeout, disabled => nothing fires.
    const a = computeIdleState(TIMEOUT_MS + 999_999, MIN, WARN, false);
    expect(a.warn).toBe(false);
    expect(a.expired).toBe(false);
    expect(a.remainingSec).toBe(0);

    const b = computeIdleState(0, MIN, WARN, false);
    expect(b.warn).toBe(false);
    expect(b.expired).toBe(false);
  });

  it('resetting idle (activity) clears warn/expired', () => {
    // Was deep in the warning window...
    const warned = computeIdleState(WARN_AT_MS + 10_000, MIN, WARN, true);
    expect(warned.warn).toBe(true);
    // ...activity resets idleMs to ~0 -> back to active, no warning.
    const reset = computeIdleState(0, MIN, WARN, true);
    expect(reset.warn).toBe(false);
    expect(reset.expired).toBe(false);
  });

  it('honours custom policies (e.g. 30 min / 120s warn)', () => {
    const m = 30;
    const w = 120;
    const timeout = m * 60_000; // 1_800_000
    const warnAt = timeout - w * 1_000; // 1_680_000
    expect(computeIdleState(warnAt - 1, m, w, true).warn).toBe(false);
    expect(computeIdleState(warnAt, m, w, true).warn).toBe(true);
    expect(computeIdleState(warnAt, m, w, true).remainingSec).toBe(w);
    expect(computeIdleState(timeout, m, w, true).expired).toBe(true);
  });

  it('treats negative idle as zero (clock skew guard)', () => {
    const s = computeIdleState(-5_000, MIN, WARN, true);
    expect(s.warn).toBe(false);
    expect(s.expired).toBe(false);
    expect(s.remainingSec).toBe(TIMEOUT_MS / 1000);
  });
});

// ---------------------------------------------------------------------------
// useIdleLogout hook — enforcement semantics (owner decision 2026-07-04:
// "popup shows but logout does not happen" -> expired means LOGOUT, hidden tabs
// included, and passive activity cannot dismiss an active warning).
// Uses fake timers; we drive idle by pinning the shared `ims_last_activity`
// key and advancing the system clock.
// ---------------------------------------------------------------------------

const BASE = 1_700_000_000_000; // fixed epoch anchor for determinism

/**
 * Deterministic in-memory localStorage. The vitest runner is launched with a
 * defective `--localstorage-file` shim in this environment (clear/removeItem are
 * missing), and the hook's reads/writes are try/catch-wrapped so a broken store
 * would silently make idle always read as 0. We install a clean Storage-like
 * mock so the hook and the test helpers share one working store.
 */
function installMemoryLocalStorage() {
  const store = new Map<string, string>();
  const mock: Storage = {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    removeItem: (k: string) => store.delete(k),
    setItem: (k: string, v: string) => store.set(k, String(v)),
  };
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: mock,
  });
}

/** Force document.hidden (jsdom default is false / visible). */
function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', {
    configurable: true,
    get: () => hidden,
  });
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => (hidden ? 'hidden' : 'visible'),
  });
}

/** Pin the cross-tab idle clock to `ts` ms (when the user was last active). */
function setLastActivity(ts: number) {
  localStorage.setItem(LAST_ACTIVITY_KEY, String(ts));
}

/** Move the fake system clock to absolute `t` and run exactly one 1s tick. */
function tickAt(t: number) {
  vi.setSystemTime(t);
  act(() => {
    vi.advanceTimersByTime(1_000);
  });
}

/** Simulate passive user activity (mousemove) in THIS tab. */
function moveMouse() {
  act(() => {
    window.dispatchEvent(new Event('mousemove'));
  });
}

/** Simulate REAL activity in ANOTHER tab: the shared key is updated there and a
 *  cross-tab `storage` event reaches this tab. */
function otherTabActivity(ts: number) {
  setLastActivity(ts);
  act(() => {
    let evt: Event;
    try {
      evt = new StorageEvent('storage', { key: LAST_ACTIVITY_KEY, newValue: String(ts) });
    } catch {
      evt = new Event('storage');
      Object.defineProperty(evt, 'key', { value: LAST_ACTIVITY_KEY });
    }
    window.dispatchEvent(evt);
  });
}

describe('useIdleLogout (enforcement)', () => {
  beforeEach(() => {
    installMemoryLocalStorage();
    vi.useFakeTimers();
    vi.setSystemTime(BASE);
    setHidden(false);
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    localStorage.clear();
  });

  it('(a) expiry logs out even when a sparse/throttled tick skipped the warn window', () => {
    const onLogout = vi.fn();
    renderHook(() =>
      useIdleLogout({ enabled: true, minutes: MIN, warnSeconds: WARN, onLogout }),
    );

    // Pin last-activity to the anchor; the hook also seeded it on mount, so
    // overwrite AFTER mount to simulate the user having been idle since BASE.
    setLastActivity(BASE);

    // Throttled tab: previous tick before the warn window, next tick past the
    // timeout — the warning was never rendered. The terminal is unattended:
    // logout must STILL fire (the watcher auto-parks the POS cart first).
    tickAt(BASE + 835_000); // 835s < 840s warn start
    expect(onLogout).not.toHaveBeenCalled();

    tickAt(BASE + 901_000); // past the 900s timeout
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it('(b) full visible flow: warn at minutes-warnSeconds, countdown, logout at the timeout', () => {
    const onLogout = vi.fn();
    const { result } = renderHook(() =>
      useIdleLogout({ enabled: true, minutes: MIN, warnSeconds: WARN, onLogout }),
    );
    setLastActivity(BASE);

    // tickAt(t) advances 1s past t before the tick evaluates, so target t-1s to
    // observe the exact boundary values.
    tickAt(BASE + WARN_AT_MS - 1_000); // evaluates at 840s -> warning opens
    expect(result.current.warning).toBe(true);
    expect(result.current.remainingSec).toBe(WARN);
    expect(onLogout).not.toHaveBeenCalled();

    tickAt(BASE + WARN_AT_MS + 29_000); // evaluates at +30s of countdown
    expect(result.current.warning).toBe(true);
    expect(result.current.remainingSec).toBe(30);
    expect(onLogout).not.toHaveBeenCalled();

    tickAt(BASE + TIMEOUT_MS + 1_000); // past the timeout -> logout, once
    expect(onLogout).toHaveBeenCalledTimes(1);
    expect(result.current.warning).toBe(false);

    // No repeat fire on later ticks.
    tickAt(BASE + TIMEOUT_MS + 30_000);
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it('(c) a HIDDEN tab past the timeout logs out (unattended terminal must sign out)', () => {
    const onLogout = vi.fn();
    renderHook(() =>
      useIdleLogout({ enabled: true, minutes: MIN, warnSeconds: WARN, onLogout }),
    );
    setLastActivity(BASE);
    setHidden(true);

    tickAt(BASE + 1_000_000); // far past the 900s timeout, tab hidden
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it('(c2) returning to a tab that expired while hidden logs out immediately on visibilitychange', () => {
    const onLogout = vi.fn();
    renderHook(() =>
      useIdleLogout({ enabled: true, minutes: MIN, warnSeconds: WARN, onLogout }),
    );
    setLastActivity(BASE);
    setHidden(true);

    // Simulate heavy throttling: NO interval tick fires while hidden. The user
    // returns long past expiry -> the visibilitychange re-tick logs out at once.
    vi.setSystemTime(BASE + 1_500_000);
    act(() => {
      setHidden(false);
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it('(d) enabled=false is a complete no-op (no timers, no logout, no warning)', () => {
    const onLogout = vi.fn();
    const setIntervalSpy = vi.spyOn(window, 'setInterval');
    const addEventSpy = vi.spyOn(window, 'addEventListener');
    const { result } = renderHook(() =>
      useIdleLogout({ enabled: false, minutes: MIN, warnSeconds: WARN, onLogout }),
    );
    setLastActivity(BASE);

    // Even far past any timeout, nothing fires and no warning shows.
    tickAt(BASE + 5_000_000);
    tickAt(BASE + 9_000_000);

    expect(onLogout).not.toHaveBeenCalled();
    expect(result.current.warning).toBe(false);
    expect(result.current.remainingSec).toBe(0);
    // No idle interval and no activity/visibility listeners were registered.
    expect(setIntervalSpy).not.toHaveBeenCalled();
    expect(addEventSpy).not.toHaveBeenCalled();
  });

  it('(e) passive activity while the warning is up does NOT cancel it — logout still fires', () => {
    const onLogout = vi.fn();
    const { result } = renderHook(() =>
      useIdleLogout({ enabled: true, minutes: MIN, warnSeconds: WARN, onLogout }),
    );
    setLastActivity(BASE);

    tickAt(BASE + 850_000); // inside the warning window
    expect(result.current.warning).toBe(true);

    // Someone wiggles the mouse at the unattended terminal.
    vi.setSystemTime(BASE + 855_000);
    moveMouse();

    // Warning still up, idle clock NOT reset.
    tickAt(BASE + 856_000);
    expect(result.current.warning).toBe(true);
    expect(localStorage.getItem(LAST_ACTIVITY_KEY)).toBe(String(BASE));

    // The countdown completes -> logout fires.
    tickAt(BASE + 901_000);
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it('(e2) passive activity BEFORE the warning resets the idle clock as usual', () => {
    const onLogout = vi.fn();
    const { result } = renderHook(() =>
      useIdleLogout({ enabled: true, minutes: MIN, warnSeconds: WARN, onLogout }),
    );
    setLastActivity(BASE);

    // Active use at 800s idle (before the 840s warn window).
    vi.setSystemTime(BASE + 800_000);
    moveMouse();
    expect(localStorage.getItem(LAST_ACTIVITY_KEY)).toBe(String(BASE + 800_000));

    // A time that WOULD have warned off the old clock is now well within the
    // active window off the fresh clock.
    tickAt(BASE + 850_000); // idle = 50s
    expect(result.current.warning).toBe(false);
    expect(onLogout).not.toHaveBeenCalled();
  });

  it('(f) REAL activity in another tab (storage event) cancels the warning', () => {
    const onLogout = vi.fn();
    const { result } = renderHook(() =>
      useIdleLogout({ enabled: true, minutes: MIN, warnSeconds: WARN, onLogout }),
    );
    setLastActivity(BASE);

    tickAt(BASE + 850_000); // warning up
    expect(result.current.warning).toBe(true);

    // The user is genuinely working in another tab of this browser profile.
    otherTabActivity(BASE + 851_000);
    expect(result.current.warning).toBe(false);

    // Next tick re-derives a small idle -> no warning, no logout.
    tickAt(BASE + 852_000);
    expect(result.current.warning).toBe(false);
    expect(onLogout).not.toHaveBeenCalled();
  });

  it('stayActive (the modal button) cancels the warning and resets the idle clock', () => {
    const onLogout = vi.fn();
    const { result } = renderHook(() =>
      useIdleLogout({ enabled: true, minutes: MIN, warnSeconds: WARN, onLogout }),
    );
    setLastActivity(BASE);

    // Enter warning.
    tickAt(BASE + 850_000);
    expect(result.current.warning).toBe(true);

    // "Stay signed in" -> writes fresh activity + clears warning.
    act(() => {
      result.current.stayActive();
    });
    expect(result.current.warning).toBe(false);

    // The hook wrote a fresh last-activity (≈ now). A subsequent tick that is
    // still well within the active window keeps us logged in.
    tickAt(BASE + 851_000);
    expect(onLogout).not.toHaveBeenCalled();
    expect(result.current.warning).toBe(false);
  });
});
