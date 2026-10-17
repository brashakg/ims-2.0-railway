// ============================================================================
// IMS 2.0 - a token refresh re-reads the login's shops (round 4, item 10)
// ============================================================================
// The server scopes every read by the token's claims. An AREA_MANAGER with no
// shop who logged in before the no-shop fix kept BV-BOK-01 in the cached user
// after /auth/refresh dropped it from his token: the no-shop gate (hasNoShop)
// read the cache and stayed open, and every Purchase list answered 403 'No
// access to store BV-BOK-01' instead of the plain no-shop message. The token
// is the one source now: a refresh rewrites the cached shops from its claims.

// This Node/jsdom runner ships a partial localStorage (no clear/setItem): the
// repo's Map-backed stand-in (see HeldBillsScoping.test.ts).
(() => {
  const m = new Map<string, string>();
  const ls = {
    getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
    setItem: (k: string, v: string) => { m.set(k, String(v)); },
    removeItem: (k: string) => { m.delete(k); },
    clear: () => { m.clear(); },
    key: (i: number) => Array.from(m.keys())[i] ?? null,
    get length() { return m.size; },
  };
  Object.defineProperty(globalThis, 'localStorage', { value: ls, configurable: true, writable: true });
})();

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import axios from 'axios';
import { refreshAccessToken, USER_SHOPS_SYNCED_EVENT } from '../client';
import { hasNoShop } from '../../../components/common/NoShopNotice';

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: Record<string, unknown>) =>
    btoa(JSON.stringify(obj)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64(payload)}.signature`;
}

const cached = () => JSON.parse(localStorage.getItem('ims_user') || '{}');

function refreshTo(claims: Record<string, unknown>) {
  vi.spyOn(axios, 'post').mockResolvedValue({ data: { access_token: fakeJwt(claims), refresh_token: 'rt-2' } });
  return refreshAccessToken();
}

describe('a token refresh rewrites the cached shops from the new token', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('ims_token', fakeJwt({ user_id: 'am', active_store_id: 'BV-BOK-01', store_ids: [] }));
    localStorage.setItem('ims_refresh_token', 'rt-1');
  });
  afterEach(() => vi.restoreAllMocks());

  it('a shop-less area manager loses the shop an old login gave him, and reads the no-shop message', async () => {
    localStorage.setItem(
      'ims_user',
      JSON.stringify({ id: 'am', roles: ['AREA_MANAGER'], activeRole: 'AREA_MANAGER', activeStoreId: 'BV-BOK-01', storeIds: [] }),
    );
    expect(hasNoShop(cached())).toBe(false); // the stale cache: the gate stays open
    const heard = vi.fn();
    window.addEventListener(USER_SHOPS_SYNCED_EVENT, heard);
    await refreshTo({ user_id: 'am', roles: ['AREA_MANAGER'], active_store_id: null, store_ids: [] });
    window.removeEventListener(USER_SHOPS_SYNCED_EVENT, heard);

    expect(cached()).toMatchObject({ activeStoreId: '', storeIds: [], activeRole: 'AREA_MANAGER' });
    expect(hasNoShop(cached())).toBe(true);
    expect(heard).toHaveBeenCalledTimes(1);
  });

  it('a login that holds a shop keeps it', async () => {
    localStorage.setItem(
      'ims_user',
      JSON.stringify({ id: 'sm', roles: ['STORE_MANAGER'], activeStoreId: 'BV-DHN-01', storeIds: ['BV-DHN-01'] }),
    );
    const heard = vi.fn();
    window.addEventListener(USER_SHOPS_SYNCED_EVENT, heard);
    await refreshTo({ user_id: 'sm', roles: ['STORE_MANAGER'], active_store_id: 'BV-DHN-01', store_ids: ['BV-DHN-01'] });
    window.removeEventListener(USER_SHOPS_SYNCED_EVENT, heard);
    expect(cached()).toMatchObject({ activeStoreId: 'BV-DHN-01', storeIds: ['BV-DHN-01'] });
    expect(hasNoShop(cached())).toBe(false);
    expect(heard).not.toHaveBeenCalled(); // nothing changed, nothing re-renders
  });
});
