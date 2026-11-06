// PR #1372 review (Codex 2026-11-06): an answer belongs to the token it was
// asked with. The api client's own writers -- the refresh answer, the idle
// refusal, the 401 sign-out and replay -- must not reach across into a newer
// sign-in or undo a newer shop choice.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios';
import api, { refreshAccessToken, syncCachedUserShops } from '../client';
import { authApi } from '../auth';

const jwt = (user_id: string, shop: string, sid = 'chain-1', tag = '') =>
  `h.${btoa(JSON.stringify({ user_id, sid, active_store_id: shop, store_ids: ['S1', 'S2'], tag }))}.s`;
const ADMIN = jwt('admin', 'S1');
const INVESTOR = jwt('investor', 'S1', 'chain-2');

let mem: Map<string, string>;
beforeEach(() => {
  mem = new Map();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => mem.get(k) ?? null,
    setItem: (k: string, v: string) => void mem.set(k, String(v)),
    removeItem: (k: string) => void mem.delete(k),
    clear: () => mem.clear(),
    key: (i: number) => [...mem.keys()][i] ?? null,
    get length() { return mem.size; },
  });
  vi.stubGlobal('location', { ...window.location, href: '' });
  signIn('admin', ADMIN, 'r1');
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function signIn(id: string, token: string, refresh: string, shop = 'S1') {
  mem.set('ims_token', token);
  mem.set('ims_refresh_token', refresh);
  mem.set('ims_user', JSON.stringify({ id, activeStoreId: shop, storeIds: ['S1', 'S2'] }));
}
const cached = () => JSON.parse(mem.get('ims_user') || '{}');
// A refresh on the wire, answered when the test says.
function heldRefresh() {
  let answer!: (data: unknown) => void;
  let refuse!: (e: unknown) => void;
  vi.spyOn(axios, 'post').mockReturnValueOnce(
    new Promise((resolve, reject) => { answer = (data) => resolve({ data }); refuse = reject; }),
  );
  const result = refreshAccessToken();
  return { result, answer: (data: unknown) => answer(data), refuse: (e: unknown) => refuse(e) };
}
const failWith = (status: number, config: InternalAxiosRequestConfig) =>
  Object.assign(new Error(String(status)), {
    isAxiosError: true, config, response: { status, data: {}, headers: {}, config, statusText: '' },
  });
const ok = (config: InternalAxiosRequestConfig): AxiosResponse =>
  ({ data: { fine: true }, status: 200, statusText: '200', headers: {}, config });

describe('a refresh answer lands only on the token it was asked with', () => {
  it('old administrator answer after another person signed in: their whole session is untouched', async () => {
    const r = heldRefresh();
    signIn('investor', INVESTOR, 'r-investor');
    r.answer({ access_token: jwt('admin', 'S1', 'chain-1', 'new'), refresh_token: 'r2' });
    expect(await r.result).toBeNull();
    expect(mem.get('ims_token')).toBe(INVESTOR);
    expect(mem.get('ims_refresh_token')).toBe('r-investor');
    expect(cached().id).toBe('investor');
  });

  it('the same person signing in again is a new session: the old chain stays out of it', async () => {
    const r = heldRefresh();
    const again = jwt('admin', 'S1', 'chain-3');
    signIn('admin', again, 'r-again');
    r.answer({ access_token: jwt('admin', 'S1', 'chain-1', 'new'), refresh_token: 'r2' });
    expect(await r.result).toBeNull();
    expect(mem.get('ims_token')).toBe(again);
    expect(mem.get('ims_refresh_token')).toBe('r-again');
  });

  it('after sign-out alone nothing is recreated', async () => {
    const r = heldRefresh();
    mem.clear();
    r.answer({ access_token: jwt('admin', 'S1', 'chain-1', 'new'), refresh_token: 'r2' });
    expect(await r.result).toBeNull();
    expect(mem.size).toBe(0);
  });

  it('a refresh sent with the refresh token alone recreates nothing after sign-out', async () => {
    mem.delete('ims_token'); // the login page drops the token, not the refresh token
    const r = heldRefresh();
    mem.clear();
    r.answer({ access_token: jwt('admin', 'S1', 'chain-1', 'new'), refresh_token: 'r2' });
    expect(await r.result).toBeNull();
    expect(mem.size).toBe(0);
  });

  it('a shop switch that landed first keeps its shop; the chain keeps its rotated refresh token', async () => {
    const r = heldRefresh();
    const switched = jwt('admin', 'S2');
    mem.set('ims_token', switched);
    mem.set('ims_user', JSON.stringify({ ...cached(), activeStoreId: 'S2' }));
    r.answer({ access_token: jwt('admin', 'S1', 'chain-1', 'new'), refresh_token: 'r2' });
    expect(await r.result).toBe(switched); // a 401 replay uses the live token
    expect(mem.get('ims_token')).toBe(switched);
    expect(cached().activeStoreId).toBe('S2');
    // r1 is spent: losing r2 would trip the server's reuse canary after its
    // grace window and revoke the whole session.
    expect(mem.get('ims_refresh_token')).toBe('r2');
  });

  it('positive control: nothing moved, the new pair is stored', async () => {
    const r = heldRefresh();
    const fresh = jwt('admin', 'S1', 'chain-1', 'new');
    r.answer({ access_token: fresh, refresh_token: 'r2' });
    expect(await r.result).toBe(fresh);
    expect(mem.get('ims_token')).toBe(fresh);
    expect(mem.get('ims_refresh_token')).toBe('r2');
  });

  it('an idle refusal of the old session leaves the new person the refresh token they signed in with', async () => {
    const r = heldRefresh();
    signIn('investor', INVESTOR, 'r-investor');
    r.refuse(Object.assign(new Error('401'), {
      isAxiosError: true, response: { status: 401, data: { detail: { code: 'SESSION_IDLE' } } },
    }));
    expect(await r.result).toBeNull();
    expect(mem.get('ims_refresh_token')).toBe('r-investor');
  });
});

describe('the cached shops follow only the cached person', () => {
  it('another person\'s token never rewrites the cached shops', () => {
    syncCachedUserShops(jwt('investor', 'S2'));
    expect(cached()).toMatchObject({ id: 'admin', activeStoreId: 'S1' });
  });
});

describe('an error answer to a request of an older sign-in', () => {
  // A request on the wire as admin; the test signs someone else in, then fails it.
  const adminRequest = (status: number, method: 'get' | 'post' = 'get') => {
    let fail!: () => void;
    const adapter = vi.fn((config: InternalAxiosRequestConfig) =>
      new Promise<AxiosResponse>((_, reject) => { fail = () => reject(failWith(status, config)); }));
    const outcome = api.request({ url: '/x', method, adapter }).then(() => 'ok', () => 'refused');
    return { adapter, outcome, fail: () => fail() };
  };

  it('a 401 neither refreshes, replays as the new person, nor signs them out', async () => {
    const post = vi.spyOn(axios, 'post');
    const q = adminRequest(401, 'post');
    await vi.waitFor(() => expect(q.adapter).toHaveBeenCalled());
    signIn('investor', INVESTOR, 'r-investor');
    q.fail();
    expect(await q.outcome).toBe('refused');
    expect(post).not.toHaveBeenCalled();
    expect(q.adapter).toHaveBeenCalledTimes(1);
    expect(mem.get('ims_token')).toBe(INVESTOR);
    expect(cached().id).toBe('investor');
    expect(location.href).toBe('');
  });

  it('a 429 write is not replayed as the new person', async () => {
    const q = adminRequest(429, 'post');
    await vi.waitFor(() => expect(q.adapter).toHaveBeenCalled());
    signIn('investor', INVESTOR, 'r-investor');
    q.fail();
    expect(await q.outcome).toBe('refused');
    expect(q.adapter).toHaveBeenCalledTimes(1);
  });

  it('a sign-in landing during the 401\'s own refresh is not signed out either', async () => {
    let answer!: (data: unknown) => void;
    vi.spyOn(axios, 'post').mockReturnValueOnce(new Promise((resolve) => { answer = (data) => resolve({ data }); }));
    const q = adminRequest(401);
    await vi.waitFor(() => expect(q.adapter).toHaveBeenCalled());
    q.fail();
    await vi.waitFor(() => expect(axios.post).toHaveBeenCalled());
    signIn('investor', INVESTOR, 'r-investor');
    answer({ access_token: jwt('admin', 'S1', 'chain-1', 'new'), refresh_token: 'r2' });
    expect(await q.outcome).toBe('refused');
    expect(mem.get('ims_token')).toBe(INVESTOR);
    expect(location.href).toBe('');
  });

  it('positive control: same sign-in, token rotated by another tab - refresh and replay as before', async () => {
    const fresh = jwt('admin', 'S1', 'chain-1', 'fresh');
    vi.spyOn(axios, 'post').mockResolvedValueOnce({ data: { access_token: fresh, refresh_token: 'r3' } });
    let fail!: () => void;
    const adapter = vi.fn((config: InternalAxiosRequestConfig) =>
      adapter.mock.calls.length === 1
        ? new Promise<AxiosResponse>((_, reject) => { fail = () => reject(failWith(401, config)); })
        : Promise.resolve(ok(config)));
    const done = api.get('/x', { adapter });
    await vi.waitFor(() => expect(adapter).toHaveBeenCalled());
    mem.set('ims_token', jwt('admin', 'S1', 'chain-1', 'other-tab'));
    fail();
    expect((await done).data).toMatchObject({ fine: true });
    expect(adapter).toHaveBeenCalledTimes(2);
  });

  it('positive control: the session\'s own dead token still signs out', async () => {
    vi.spyOn(axios, 'post').mockRejectedValueOnce(new Error('refused'));
    const adapter = (config: InternalAxiosRequestConfig) => Promise.reject(failWith(401, config));
    await expect(api.get('/x', { adapter })).rejects.toBeTruthy();
    expect(mem.get('ims_token')).toBeUndefined();
    expect(location.href).toBe('/login');
  });
});

describe('switchStore names the token that really carried it', () => {
  it('a switch replayed after a 401 refresh reports the refreshed token, not the dead one', async () => {
    const fresh = jwt('admin', 'S1', 'chain-1', 'fresh');
    vi.spyOn(axios, 'post').mockResolvedValueOnce({ data: { access_token: fresh, refresh_token: 'r2' } });
    const adapter = vi.fn((config: InternalAxiosRequestConfig) =>
      adapter.mock.calls.length === 1
        ? Promise.reject(failWith(401, config))
        : Promise.resolve({ ...ok(config), data: { access_token: jwt('admin', 'S2'), active_store_id: 'S2' } }));
    const realAdapter = api.defaults.adapter;
    api.defaults.adapter = adapter;
    try {
      const answer = await authApi.switchStore('S2');
      expect(answer.sent_with).toBe(fresh);
    } finally {
      api.defaults.adapter = realAdapter;
    }
  });
});
