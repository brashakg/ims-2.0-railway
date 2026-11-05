// PR #1372 review (Codex 2026-11-05): the shop on screen must always be the
// shop the token acts for, for the person signed in, in every tab.
//  1. A slow switch answer that lands after sign-out / a new sign-in is thrown
//     away: it never replaces the next person's token.
//  2. Another tab of the SAME person that switched shop moves this tab too.
//  3. On load / refreshUser the server's shop wins over an old cached pick.
import { act, render, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api', () => ({
  authApi: { login: vi.fn(), logout: vi.fn(async () => {}), getProfile: vi.fn(), switchStore: vi.fn() },
}));
vi.mock('../../stores/posStore', () => ({
  usePOSStore: {
    getState: () => ({ clearAllOnLogout: () => {}, parkIfPending: () => 'not-pending', resetTransaction: () => {} }),
  },
}));

import { authApi } from '../../services/api';
import { AuthProvider, useAuth } from '../AuthContext';

let auth: ReturnType<typeof useAuth>;
const keep = (a: ReturnType<typeof useAuth>) => { auth = a; };
function Probe({ onAuth }: { onAuth: typeof keep }) {
  onAuth(useAuth());
  return null;
}
const fn = (f: unknown) => f as ReturnType<typeof vi.fn>;
const jwt = (user_id: string, shop: string, store_ids = ['S1', 'S2']) =>
  `h.${btoa(JSON.stringify({ user_id, active_store_id: shop, store_ids }))}.s`;
const person = (id: string, shop: string, extra: Record<string, unknown> = {}) => ({
  id, name: id, roles: ['STORE_MANAGER'], storeIds: ['S1', 'S2'], activeStoreId: shop, ...extra,
});
const cached = () => JSON.parse(localStorage.getItem('ims_user') || '{}');

let qc: QueryClient;
beforeEach(() => {
  const mem = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => mem.get(k) ?? null,
    setItem: (k: string, v: string) => void mem.set(k, String(v)),
    removeItem: (k: string) => void mem.delete(k),
    clear: () => mem.clear(),
  });
  fn(authApi.switchStore).mockReset();
  fn(authApi.getProfile).mockReset();
  qc = new QueryClient();
});

const mount = () =>
  render(<QueryClientProvider client={qc}><AuthProvider><Probe onAuth={keep} /></AuthProvider></QueryClientProvider>);
const loginAs = async (id: string, shop: string) => {
  fn(authApi.login).mockResolvedValueOnce({ success: true, token: jwt(id, shop), user: person(id, shop) });
  await act(() => auth.login({ username: id, password: 'x' }));
};
// A switch to S2 whose answer (for u1) is held back until release().
const heldSwitch = () => {
  let release!: () => void;
  fn(authApi.switchStore).mockReturnValueOnce(
    new Promise((r) => { release = () => r({ access_token: jwt('u1', 'S2'), active_store_id: 'S2' }); }),
  );
  let pending!: Promise<boolean>;
  act(() => { pending = auth.setActiveStore('S2'); });
  return {
    land: async () => { let ok: boolean | undefined; await act(async () => { release(); ok = await pending; }); return ok; },
  };
};

describe('a delayed switch answer never lands on the wrong session', () => {
  it('after sign-out and a DIFFERENT person signing in, it is discarded; their token is untouched', async () => {
    mount();
    await loginAs('u1', 'S1');
    const sw = heldSwitch();
    await act(() => auth.logout());
    await loginAs('u2', 'S1');
    expect(await sw.land()).toBe(false);
    expect(localStorage.getItem('ims_token')).toBe(jwt('u2', 'S1'));
    expect(auth.user?.id).toBe('u2');
    expect(auth.user?.activeStoreId).toBe('S1');
    expect(cached().activeStoreId).toBe('S1');
  });

  it('after sign-out and the SAME person signing in again, the old session answer is discarded', async () => {
    mount();
    await loginAs('u1', 'S1');
    const sw = heldSwitch();
    await act(() => auth.logout());
    await loginAs('u1', 'S1');
    expect(await sw.land()).toBe(false);
    expect(localStorage.getItem('ims_token')).toBe(jwt('u1', 'S1'));
    expect(auth.user?.activeStoreId).toBe('S1');
  });

  it('after sign-out alone, nothing is written back', async () => {
    mount();
    await loginAs('u1', 'S1');
    const sw = heldSwitch();
    await act(() => auth.logout());
    expect(await sw.land()).toBe(false);
    expect(localStorage.getItem('ims_token')).toBeNull();
    expect(localStorage.getItem('ims_user')).toBeNull();
  });

  it('when another tab signed a different person in meanwhile, the answer does not overwrite the shared token', async () => {
    mount();
    await loginAs('u1', 'S1');
    const sw = heldSwitch();
    localStorage.setItem('ims_token', jwt('u2', 'S1'));
    localStorage.setItem('ims_user', JSON.stringify(person('u2', 'S1')));
    expect(await sw.land()).toBe(false);
    expect(localStorage.getItem('ims_token')).toBe(jwt('u2', 'S1'));
  });

  it('positive control: the same session still switches when the answer is for this person', async () => {
    mount();
    await loginAs('u1', 'S1');
    const sw = heldSwitch();
    expect(await sw.land()).toBe(true);
    expect(localStorage.getItem('ims_token')).toBe(jwt('u1', 'S2'));
    expect(auth.user?.activeStoreId).toBe('S2');
  });
});

describe('another tab of the same person switched shop', () => {
  const otherTabSwitches = (shop: string) => {
    localStorage.setItem('ims_token', jwt('u1', shop));
    localStorage.setItem('ims_user', JSON.stringify(person('u1', shop)));
    act(() => { window.dispatchEvent(new StorageEvent('storage', { key: 'ims_token' })); });
  };

  it('this tab shows the shop the shared token acts for and drops its shop data', async () => {
    mount();
    await loginAs('u1', 'S1');
    qc.setQueryData(['stock', 'S1'], 7);
    otherTabSwitches('S2');
    expect(auth.user?.activeStoreId).toBe('S2');
    expect(cached().activeStoreId).toBe('S2');
    expect(qc.getQueryData(['stock', 'S1'])).toBeUndefined();
  });

  it('a cache the other tab left on the old shop is set to the token shop', async () => {
    mount();
    await loginAs('u1', 'S1');
    localStorage.setItem('ims_token', jwt('u1', 'S2'));
    act(() => { window.dispatchEvent(new StorageEvent('storage', { key: 'ims_token' })); });
    expect(auth.user?.activeStoreId).toBe('S2');
    expect(cached().activeStoreId).toBe('S2');
  });

  it('a token refresh on the same shop changes nothing and keeps the data', async () => {
    mount();
    await loginAs('u1', 'S1');
    qc.setQueryData(['stock', 'S1'], 7);
    otherTabSwitches('S1');
    expect(auth.user?.activeStoreId).toBe('S1');
    expect(qc.getQueryData(['stock', 'S1'])).toBe(7);
  });

  it('a different person in the token is left to the sign-out listener (#1379), never adopted as this shop', async () => {
    mount();
    await loginAs('u1', 'S1');
    localStorage.setItem('ims_token', jwt('u2', 'S2'));
    act(() => { window.dispatchEvent(new StorageEvent('storage', { key: 'ims_token' })); });
    expect(auth.user?.id).toBe('u1');
    expect(auth.user?.activeStoreId).toBe('S1');
  });
});

describe('the server shop wins over an old cached pick', () => {
  it('on load, a cache left on S2 with an S1 token is set back to S1', async () => {
    localStorage.setItem('ims_token', jwt('u1', 'S1'));
    localStorage.setItem('ims_user', JSON.stringify(person('u1', 'S2', { activeRole: 'STORE_MANAGER' })));
    fn(authApi.getProfile).mockResolvedValueOnce(person('u1', 'S1'));
    mount();
    await waitFor(() => expect(auth.isAuthenticated).toBe(true));
    expect(auth.user?.activeStoreId).toBe('S1');
    expect(cached().activeStoreId).toBe('S1');
    // the cached role pick is still restored
    expect(auth.user?.activeRole).toBe('STORE_MANAGER');
  });

  it('refreshUser takes the server shop, not the screen one', async () => {
    mount();
    fn(authApi.login).mockResolvedValueOnce({ success: true, token: jwt('u1', 'S1'), user: person('u1', 'S2') });
    await act(() => auth.login({ username: 'u1', password: 'x' }));
    fn(authApi.getProfile).mockResolvedValueOnce(person('u1', 'S1'));
    await act(() => auth.refreshUser());
    expect(auth.user?.activeStoreId).toBe('S1');
    expect(cached().activeStoreId).toBe('S1');
  });
});
