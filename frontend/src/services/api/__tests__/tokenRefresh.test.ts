// ============================================================================
// IMS 2.0 - token refresh decision helpers (client.ts)
// ============================================================================
// The load-bearing logic for the 2026-07 token hardening on the client is the
// PURE decision function shouldProactivelyRefresh: it must renew the access
// token for an ACTIVE user approaching expiry, and it must NEVER refresh for
// an idle user -- otherwise the refresh loop would defeat the 15-min idle
// logout (the idle user's token has to be allowed to die). decodeJwtExpMs is
// the exp parser it feeds on. Both are deterministic: no network, no timers.

import { describe, it, expect } from 'vitest';
import { decodeJwtExpMs, shouldProactivelyRefresh } from '../client';

const MIN = 60_000;

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: Record<string, unknown>) =>
    btoa(JSON.stringify(obj)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64(payload)}.signature`;
}

describe('decodeJwtExpMs', () => {
  it('extracts exp (epoch seconds) as milliseconds', () => {
    expect(decodeJwtExpMs(fakeJwt({ exp: 1_800_000_000, user_id: 'u1' }))).toBe(
      1_800_000_000_000,
    );
  });

  it('returns null for a token without exp', () => {
    expect(decodeJwtExpMs(fakeJwt({ user_id: 'u1' }))).toBeNull();
  });

  it('returns null for garbage tokens instead of throwing', () => {
    expect(decodeJwtExpMs('not-a-jwt')).toBeNull();
    expect(decodeJwtExpMs('')).toBeNull();
    expect(decodeJwtExpMs('a.%%%%.c')).toBeNull();
  });
});

describe('shouldProactivelyRefresh', () => {
  const now = 1_800_000_000_000;

  it('refreshes an ACTIVE user whose token is inside the 5-min window', () => {
    // Token expires in 3 min; user active 10s ago.
    expect(shouldProactivelyRefresh(now + 3 * MIN, now, now - 10_000)).toBe(true);
  });

  it('refreshes an ACTIVE user whose token already expired (wake-from-sleep)', () => {
    expect(shouldProactivelyRefresh(now - 1 * MIN, now, now - 10_000)).toBe(true);
  });

  it('does NOT refresh far from expiry even when active', () => {
    // Token expires in 40 min -> nothing to do.
    expect(shouldProactivelyRefresh(now + 40 * MIN, now, now - 10_000)).toBe(false);
  });

  it('does NOT refresh an IDLE user -- the token must be allowed to die', () => {
    // Token expires in 3 min but last activity was 6 min ago (> freshness
    // window). Refreshing here would defeat idle logout.
    expect(shouldProactivelyRefresh(now + 3 * MIN, now, now - 6 * MIN)).toBe(false);
  });

  it('boundary: activity exactly at the freshness limit still counts as active', () => {
    expect(shouldProactivelyRefresh(now + 3 * MIN, now, now - 5 * MIN)).toBe(true);
    expect(shouldProactivelyRefresh(now + 3 * MIN, now, now - 5 * MIN - 1)).toBe(false);
  });

  it('does NOT refresh with no activity signal at all', () => {
    // No ims_last_activity (e.g. idle-logout disabled tab that never wrote it,
    // or storage unavailable): fail toward NOT refreshing.
    expect(shouldProactivelyRefresh(now + 3 * MIN, now, null)).toBe(false);
  });

  it('does NOT refresh when the token exp is unparseable', () => {
    expect(shouldProactivelyRefresh(null, now, now - 10_000)).toBe(false);
  });
});
