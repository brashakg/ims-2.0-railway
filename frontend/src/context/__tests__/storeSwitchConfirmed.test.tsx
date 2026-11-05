// R41 (Codex audit 2026-11-04): a failed store switch left the screen and the
// cached user on store B while the token still acted for store A. The screen,
// the cache and the token must always name ONE shop: the switch is applied
// only after the server re-issued the token, and an older answer that arrives
// after a newer switch is ignored.
import { act, render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api', () => ({
  authApi: { login: vi.fn(), logout: vi.fn(), getProfile: vi.fn(), switchStore: vi.fn() },
}));
vi.mock('../../stores/posStore', () => ({
  usePOSStore: {
    getState: () => ({ clearAllOnLogout: () => {}, parkIfPending: () => 'not-pending', resetTransaction: () => {} }),
  },
}));

import { authApi } from '../../services/api';
import { AuthProvider, useAuth } from '../AuthContext';

let auth: ReturnType<typeof useAuth>;
function Probe() {
  auth = useAuth();
  return null;
}
const switchStore = authApi.switchStore as unknown as ReturnType<typeof vi.fn>;
// A server-issued token names its person and shop (R41 review: a switch answer
// is applied only to the person who asked).
const jwt = (shop: string) => `h.${btoa(JSON.stringify({ user_id: 'u1', active_store_id: shop, store_ids: ['S1', 'S2', 'S3'] }))}.s`;
const cachedShop = () => JSON.parse(localStorage.getItem('ims_user') || '{}').activeStoreId;

beforeEach(() => {
  const mem = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => mem.get(k) ?? null,
    setItem: (k: string, v: string) => void mem.set(k, String(v)),
    removeItem: (k: string) => void mem.delete(k),
    clear: () => mem.clear(),
  });
  switchStore.mockReset();
});

const signIn = async () => {
  (authApi.login as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
    success: true, token: jwt('S1'),
    user: { id: 'u1', name: 'u1', roles: ['STORE_MANAGER'], storeIds: ['S1', 'S2', 'S3'], activeStoreId: 'S1' },
  });
  render(<QueryClientProvider client={new QueryClient()}><AuthProvider><Probe /></AuthProvider></QueryClientProvider>);
  await act(() => auth.login({ username: 'u1', password: 'x' }));
  localStorage.setItem('ims_token', jwt('S1'));
};

describe('a store switch moves the screen only when the server confirms it', () => {
  it('a failed switch (503) leaves screen, cache and token on the old shop', async () => {
    await signIn();
    switchStore.mockRejectedValueOnce(Object.assign(new Error('503'), { response: { status: 503 } }));
    let ok: boolean | undefined;
    await act(async () => { ok = await auth.setActiveStore('S2'); });
    expect(ok).toBe(false);
    expect(auth.user?.activeStoreId).toBe('S1');
    expect(cachedShop()).toBe('S1');
    expect(localStorage.getItem('ims_token')).toBe(jwt('S1'));
  });

  it('while the switch is pending nothing has moved yet', async () => {
    await signIn();
    let resolve!: (v: unknown) => void;
    switchStore.mockReturnValueOnce(new Promise((r) => { resolve = r; }));
    let pending!: Promise<boolean>;
    act(() => { pending = auth.setActiveStore('S2'); });
    expect(auth.user?.activeStoreId).toBe('S1');
    expect(cachedShop()).toBe('S1');
    await act(async () => { resolve({ access_token: jwt('S2'), active_store_id: 'S2' }); await pending; });
    expect(auth.user?.activeStoreId).toBe('S2');
    expect(cachedShop()).toBe('S2');
    expect(localStorage.getItem('ims_token')).toBe(jwt('S2'));
  });

  it('a confirmed switch moves screen, cache and token together, to the shop the server named', async () => {
    await signIn();
    switchStore.mockResolvedValueOnce({ access_token: jwt('S2'), active_store_id: 'S2' });
    let ok: boolean | undefined;
    await act(async () => { ok = await auth.setActiveStore('S2'); });
    expect(ok).toBe(true);
    expect(auth.user?.activeStoreId).toBe('S2');
    expect(cachedShop()).toBe('S2');
    expect(localStorage.getItem('ims_token')).toBe(jwt('S2'));
  });

  it('an older answer arriving after a newer switch is ignored', async () => {
    await signIn();
    let slowB!: (v: unknown) => void;
    switchStore.mockReturnValueOnce(new Promise((r) => { slowB = r; }));
    switchStore.mockResolvedValueOnce({ access_token: jwt('S3'), active_store_id: 'S3' });
    let first!: Promise<boolean>;
    act(() => { first = auth.setActiveStore('S2'); });
    await act(async () => { await auth.setActiveStore('S3'); });
    let firstOk: boolean | undefined;
    await act(async () => { slowB({ access_token: jwt('S2'), active_store_id: 'S2' }); firstOk = await first; });
    expect(firstOk).toBe(false);
    expect(auth.user?.activeStoreId).toBe('S3');
    expect(cachedShop()).toBe('S3');
    expect(localStorage.getItem('ims_token')).toBe(jwt('S3'));
  });

  it('a shop the user is not given is refused without a request', async () => {
    await signIn();
    let ok: boolean | undefined;
    await act(async () => { ok = await auth.setActiveStore('S9'); });
    expect(ok).toBe(false);
    expect(switchStore).not.toHaveBeenCalled();
    expect(auth.user?.activeStoreId).toBe('S1');
  });
});
