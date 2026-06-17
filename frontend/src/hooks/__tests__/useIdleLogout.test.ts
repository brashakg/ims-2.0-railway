import { describe, it, expect } from 'vitest';
import { computeIdleState } from '../useIdleLogout';

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
