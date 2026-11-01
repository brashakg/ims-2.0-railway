// ============================================================================
// A reopened browser past the idle limit is signed out, not resumed
// ============================================================================
// Owner 2026-11-01: reopened Chrome after ~20 min and was still signed in,
// though auto-logout is 15 min. AuthContext's page-load path now checks the
// stored last-activity stamp BEFORE resuming the stored token: past the limit
// it auto-parks the POS cart and runs the normal logout instead.

import { act, render, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const policy = { enabled: true, minutes: 15, warnSeconds: 60 };
vi.mock('../../constants/sessionPolicy', () => ({ getSessionPolicy: () => policy }));
vi.mock('../../services/api', () => ({
  authApi: { login: vi.fn(), logout: vi.fn(), getProfile: vi.fn() },
}));
const park = vi.fn();
const clearPos = vi.fn();
vi.mock('../../stores/posStore', () => ({
  usePOSStore: { getState: () => ({ parkCurrentSale: park, clearAllOnLogout: clearPos }) },
}));

import { authApi } from '../../services/api';
import { AuthProvider, useAuth } from '../AuthContext';
import { LAST_ACTIVITY_KEY } from '../../hooks/useIdleLogout';

const mocked = (f: unknown) => f as ReturnType<typeof vi.fn>;
const USER = { id: 'u-1', name: 'Ravi', roles: ['SALES_STAFF'], storeIds: ['BV-S1'], activeStoreId: 'BV-S1' };
const MIN = 60_000;

const seen = {} as { auth: ReturnType<typeof useAuth> };
function Probe() {
  seen.auth = useAuth();
  return null;
}
function mount() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </QueryClientProvider>,
  );
}

let mem: Map<string, string>;
const replace = vi.fn();
beforeEach(() => {
  mem = new Map();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => mem.get(k) ?? null,
    setItem: (k: string, v: string) => void mem.set(k, String(v)),
    removeItem: (k: string) => void mem.delete(k),
    clear: () => mem.clear(),
  });
  vi.stubGlobal('location', { ...window.location, replace });
  policy.enabled = true;
  vi.clearAllMocks();
  mocked(authApi.logout).mockResolvedValue(undefined);
  mocked(authApi.getProfile).mockResolvedValue({ ...USER });
});
afterEach(() => vi.unstubAllGlobals());

function storedSession(lastActivityMsAgo: number | null) {
  mem.set('ims_token', 'h.p.s');
  mem.set('ims_refresh_token', 'r');
  mem.set('ims_user', JSON.stringify(USER));
  if (lastActivityMsAgo !== null) mem.set(LAST_ACTIVITY_KEY, String(Date.now() - lastActivityMsAgo));
}

describe('page load after the browser was closed', () => {
  it('idle 20 min (limit 15) -> parks the cart, logs out, lands on /login?reason=idle', async () => {
    storedSession(20 * MIN);
    mount();
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/login?reason=idle'));
    expect(authApi.getProfile).not.toHaveBeenCalled(); // never resumed
    expect(authApi.logout).toHaveBeenCalledTimes(1); // server revoke attempted
    expect(park).toHaveBeenCalledWith({ auto: true, heldBy: 'u-1' });
    // Parked BEFORE anything empties the cart, or the sale is lost.
    const parkedAt = park.mock.invocationCallOrder[0];
    expect(parkedAt).toBeLessThan(clearPos.mock.invocationCallOrder[0]);
    expect(parkedAt).toBeLessThan(mocked(authApi.logout).mock.invocationCallOrder[0]);
    expect(mem.get('ims_token')).toBeUndefined();
    expect(mem.get('ims_refresh_token')).toBeUndefined();
    expect(seen.auth.isAuthenticated).toBe(false);
  });

  it('idle 10 min (limit 15) -> resumes as before', async () => {
    storedSession(10 * MIN);
    mount();
    await waitFor(() => expect(seen.auth.isAuthenticated).toBe(true));
    expect(authApi.logout).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });

  it('no stamp stored -> resumes (nothing to measure)', async () => {
    storedSession(null);
    mount();
    await waitFor(() => expect(seen.auth.isAuthenticated).toBe(true));
    expect(authApi.logout).not.toHaveBeenCalled();
  });

  it('auto-logout disabled -> resumes even after hours', async () => {
    policy.enabled = false;
    storedSession(300 * MIN);
    mount();
    await waitFor(() => expect(seen.auth.isAuthenticated).toBe(true));
    expect(authApi.logout).not.toHaveBeenCalled();
  });

  it('a fresh login over an old stamp stays signed in on the next load', async () => {
    mem.set(LAST_ACTIVITY_KEY, String(Date.now() - 300 * MIN)); // last person's old stamp
    mocked(authApi.login).mockResolvedValue({ success: true, token: 'h.p.s', user: { ...USER } });
    const first = mount();
    await waitFor(() => expect(seen.auth.isLoading).toBe(false));
    await act(() => seen.auth.login({ username: 'u-1', password: 'x' }));
    first.unmount();

    mount(); // reload right after signing in
    await waitFor(() => expect(seen.auth.isAuthenticated).toBe(true));
    expect(authApi.logout).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });
});
