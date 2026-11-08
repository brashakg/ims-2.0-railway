// PR #1372 review (Codex 2026-11-06): an answer belongs to the token it was
// asked with. The api client's own writers -- the refresh answer, the idle
// refusal, the 401 sign-out and replay -- must not reach across into a newer
// sign-in or undo a newer shop choice.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios';
import api, {
  REPLAY_REFUSED_MESSAGE, WRITE_UNCONFIRMED_MESSAGE, refreshAccessToken, sameSession, syncCachedUserShops,
} from '../client';
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

// PR #1372 round 3 (Codex): a replay goes out only for the person, sign-in and
// shop that first sent the request. Both reproduced as a stored task on the
// wrong shop / under the wrong person; the wait is where the world moves.
const S2 = jwt('admin', 'S2');
const refusal = (e: { status?: number; message?: string }) => ({ status: e.status, message: e.message });
const REFUSED = (status: number) => ({ status, message: REPLAY_REFUSED_MESSAGE });
const sentAs = (adapter: { mock: { calls: unknown[][] } }, call: number) =>
  String((adapter.mock.calls[call][0] as InternalAxiosRequestConfig).headers.Authorization).replace('Bearer ', '');
// The answers a request gets, in order; after them it succeeds.
const answering = (...statuses: number[]) => {
  const adapter = vi.fn((config: InternalAxiosRequestConfig) => {
    const status = statuses[adapter.mock.calls.length - 1];
    return status ? Promise.reject(failWith(status, config)) : Promise.resolve(ok(config));
  });
  return adapter;
};
// The client's backoff, cut short: `during` runs in the wait, as the world moving.
const duringBackoff = (during: (ms: number) => void) =>
  vi.spyOn(globalThis, 'setTimeout').mockImplementation(((run: () => void, ms: number) => {
    during(ms);
    run();
    return 0;
  }) as never);

describe('a 401 replay never moves a request into another shop', () => {
  it('a 401 answered after the switch: no refresh, no replay, nobody signed out, and it says so', async () => {
    const post = vi.spyOn(axios, 'post').mockResolvedValueOnce({ data: { access_token: S2, refresh_token: 'r2' } });
    let fail!: () => void;
    const adapter = vi.fn((config: InternalAxiosRequestConfig) =>
      adapter.mock.calls.length === 1
        ? new Promise<AxiosResponse>((_, reject) => { fail = () => reject(failWith(401, config)); })
        : Promise.resolve(ok(config)));
    const done = api.post('/tasks', {}, { adapter }).catch(refusal);
    await vi.waitFor(() => expect(adapter).toHaveBeenCalled());
    mem.set('ims_token', S2);
    fail();
    expect(await done).toEqual(REFUSED(401));
    expect(adapter).toHaveBeenCalledTimes(1);
    expect(post).not.toHaveBeenCalled();
    expect(mem.get('ims_token')).toBe(S2);
    expect(location.href).toBe('');
  });

  it('a switch landing during the 401\'s own refresh: the chain keeps its new refresh token, the request stays unsent', async () => {
    let answer!: (data: unknown) => void;
    vi.spyOn(axios, 'post').mockReturnValueOnce(new Promise((resolve) => { answer = (data) => resolve({ data }); }));
    const adapter = answering(401);
    const done = api.post('/tasks', {}, { adapter }).catch(refusal);
    await vi.waitFor(() => expect(axios.post).toHaveBeenCalled());
    mem.set('ims_token', S2);
    answer({ access_token: jwt('admin', 'S1', 'chain-1', 'new'), refresh_token: 'r2' });
    expect(await done).toEqual(REFUSED(401));
    expect(adapter).toHaveBeenCalledTimes(1);
    expect(mem.get('ims_token')).toBe(S2);
    expect(mem.get('ims_refresh_token')).toBe('r2');
  });

  it('positive control: there and back to the first shop, the request is still that shop\'s', async () => {
    const back = jwt('admin', 'S1', 'chain-1', 'back');
    const fresh = jwt('admin', 'S1', 'chain-1', 'fresh');
    vi.spyOn(axios, 'post').mockResolvedValueOnce({ data: { access_token: fresh, refresh_token: 'r2' } });
    let fail!: () => void;
    const adapter = vi.fn((config: InternalAxiosRequestConfig) =>
      adapter.mock.calls.length === 1
        ? new Promise<AxiosResponse>((_, reject) => { fail = () => reject(failWith(401, config)); })
        : Promise.resolve(ok(config)));
    const done = api.post('/tasks', {}, { adapter });
    await vi.waitFor(() => expect(adapter).toHaveBeenCalled());
    mem.set('ims_token', back);
    fail();
    expect((await done).status).toBe(200);
    expect(adapter).toHaveBeenCalledTimes(2);
    expect(sentAs(adapter, 1)).toBe(fresh);
  });

  it('positive control: an HQ login with no shop refreshes and replays', async () => {
    const hq = (tag: string) => `h.${btoa(JSON.stringify({ user_id: 'hq', sid: 'c', active_store_id: null, tag }))}.s`;
    const noShopClaim = `h.${btoa(JSON.stringify({ user_id: 'hq', sid: 'c' }))}.s`;
    signIn('hq', hq('old'), 'r1', '');
    vi.spyOn(axios, 'post').mockResolvedValueOnce({ data: { access_token: noShopClaim, refresh_token: 'r2' } });
    const adapter = answering(401);
    expect((await api.get('/x', { adapter })).status).toBe(200);
    expect(adapter).toHaveBeenCalledTimes(2);
  });

  it('positive control: a sign-in with no chain yet takes its first one on refresh, and the replay goes', async () => {
    // The server's refresh-store hiccup at login leaves no refresh token: the
    // legacy refresh mints a chain, so `sid` appears where there was none.
    const noChain = `h.${btoa(JSON.stringify({ user_id: 'admin', active_store_id: 'S1' }))}.s`;
    const chained = jwt('admin', 'S1', 'chain-9');
    mem.set('ims_token', noChain);
    mem.delete('ims_refresh_token');
    vi.spyOn(axios, 'post').mockResolvedValueOnce({ data: { access_token: chained, refresh_token: 'r9' } });
    const adapter = answering(401);
    expect((await api.post('/tasks', {}, { adapter })).status).toBe(200);
    expect(sentAs(adapter, 1)).toBe(chained);
  });
});

describe('a request waiting out its backoff is checked again when the wait ends', () => {
  it('the next person signed in during the wait: the old request never goes out as them', async () => {
    duringBackoff(() => signIn('investor', INVESTOR, 'r-investor'));
    const adapter = answering(429);
    expect(await api.post('/tasks', {}, { adapter }).catch(refusal)).toEqual(REFUSED(429));
    expect(adapter).toHaveBeenCalledTimes(1);
    expect(mem.get('ims_token')).toBe(INVESTOR);
    expect(cached().id).toBe('investor');
    expect(location.href).toBe('');
  });

  it('the third wait counts too (the natural 1s, 2s, 4s backoff)', async () => {
    duringBackoff((ms) => { if (ms === 4000) signIn('investor', INVESTOR, 'r-investor'); });
    const adapter = answering(429, 429, 429);
    expect(await api.post('/tasks', {}, { adapter }).catch(refusal)).toEqual(REFUSED(429));
    expect(adapter).toHaveBeenCalledTimes(3);
    expect(sentAs(adapter, 2)).toBe(ADMIN);
  });

  it('sign-out alone during the wait: the dead token is not sent again', async () => {
    duringBackoff(() => mem.clear());
    const adapter = answering(429);
    expect(await api.post('/tasks', {}, { adapter }).catch(refusal)).toEqual(REFUSED(429));
    expect(adapter).toHaveBeenCalledTimes(1);
    expect(mem.size).toBe(0);
  });

  it('a shop switch during the wait: refused, on a write and on a read being retried', async () => {
    duringBackoff(() => mem.set('ims_token', S2));
    const write = answering(429);
    expect(await api.post('/tasks', {}, { adapter: write }).catch(refusal)).toEqual(REFUSED(429));
    expect(write).toHaveBeenCalledTimes(1);
    mem.set('ims_token', ADMIN);
    const read = answering(503);
    expect(await api.get('/x', { adapter: read }).catch(refusal)).toEqual(REFUSED(503));
    expect(read).toHaveBeenCalledTimes(1);
  });

  it('a request sent signed out is not carried by whoever signs in during its wait', async () => {
    mem.clear();
    duringBackoff(() => signIn('investor', INVESTOR, 'r-investor'));
    const adapter = answering(429);
    expect(await api.post('/x', {}, { adapter }).catch(refusal)).toEqual(REFUSED(429));
    expect(adapter).toHaveBeenCalledTimes(1);
  });

  it('positive control: same person and shop, token rotated in the wait - it retries once, on the new token', async () => {
    const fresh = jwt('admin', 'S1', 'chain-1', 'fresh');
    duringBackoff(() => mem.set('ims_token', fresh));
    const adapter = answering(429);
    expect((await api.post('/tasks', {}, { adapter })).status).toBe(200);
    expect(adapter).toHaveBeenCalledTimes(2);
    expect(sentAs(adapter, 1)).toBe(fresh);
  });

  it('positive control: a sign-in in its own backoff still signs in', async () => {
    mem.clear();
    duringBackoff(() => undefined);
    const adapter = answering(429);
    expect((await api.post('/auth/login', {}, { adapter })).status).toBe(200);
    expect(adapter).toHaveBeenCalledTimes(2);
  });

  it('a 5xx on a write from the old shop keeps its "may already be saved" warning', async () => {
    let fail!: () => void;
    const adapter = vi.fn((config: InternalAxiosRequestConfig) =>
      new Promise<AxiosResponse>((_, reject) => { fail = () => reject(failWith(500, config)); }));
    const done = api.post('/tasks', {}, { adapter }).catch(refusal);
    await vi.waitFor(() => expect(adapter).toHaveBeenCalled());
    mem.set('ims_token', S2);
    fail();
    expect(await done).toEqual({ status: 500, message: WRITE_UNCONFIRMED_MESSAGE });
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

// PR #1372 round 4 (Codex 2026-11-07): a token with no `sid` (a sign-in from
// before refresh chains, or a login whose chain store hiccuped) is the same
// sign-in only as the chain THIS tab's refresh of it produced -- never any
// later chain of the same person, e.g. after a sign-out and a fresh sign-in.
const noChain = (tag = '') => `h.${btoa(JSON.stringify({ user_id: 'admin', active_store_id: 'S1', tag }))}.s`;
const signInAgain = (sid: string) => { mem.clear(); signIn('admin', jwt('admin', 'S1', sid), `r-${sid}`); };

describe('a token with no chain is not proof of the same sign-in', () => {
  it('an old 429 write waiting out its backoff is refused after sign-out and the same person signing in again', async () => {
    mem.set('ims_token', noChain('old'));
    mem.delete('ims_refresh_token');
    duringBackoff(() => signInAgain('fresh-chain'));
    const adapter = answering(429);
    expect(await api.post('/tasks', {}, { adapter }).catch(refusal)).toEqual(REFUSED(429));
    expect(adapter).toHaveBeenCalledTimes(1);
    expect(mem.get('ims_token')).toBe(jwt('admin', 'S1', 'fresh-chain'));
    expect(mem.get('ims_refresh_token')).toBe('r-fresh-chain');
  });

  it('a late idle refusal of the old sign-in leaves the fresh sign-in its refresh token', async () => {
    mem.set('ims_token', noChain('old'));
    mem.set('ims_refresh_token', 'r-old');
    const r = heldRefresh();
    signInAgain('brand-new-chain');
    r.refuse(Object.assign(new Error('401'), {
      isAxiosError: true, response: { status: 401, data: { detail: { code: 'SESSION_IDLE' } } },
    }));
    expect(await r.result).toBeNull();
    expect(mem.get('ims_token')).toBe(jwt('admin', 'S1', 'brand-new-chain'));
    expect(mem.get('ims_refresh_token')).toBe('r-brand-new-chain');
  });

  it('positive control: the idle refusal of the sign-in still stored drops its refresh token', async () => {
    mem.set('ims_token', noChain('still'));
    mem.set('ims_refresh_token', 'r-still');
    const r = heldRefresh();
    r.refuse(Object.assign(new Error('401'), {
      isAxiosError: true, response: { status: 401, data: { detail: { code: 'SESSION_IDLE' } } },
    }));
    expect(await r.result).toBeNull();
    expect(mem.has('ims_refresh_token')).toBe(false);
  });

  it('only the chain this tab upgraded the token into continues it', async () => {
    const legacy = noChain('upgrade');
    mem.set('ims_token', legacy);
    // A refresh credential beside it (an idle refusal earlier in this file is
    // sticky for a sign-in without one); Codex's no-refresh-token shape is
    // rerun verbatim from its evidence.
    mem.set('ims_refresh_token', 'r-legacy');
    const upgraded = jwt('admin', 'S1', 'chain-up');
    vi.spyOn(axios, 'post').mockResolvedValueOnce({ data: { access_token: upgraded, refresh_token: 'r-up' } });
    expect(await refreshAccessToken()).toBe(upgraded);
    expect(sameSession(legacy, upgraded)).toBe(true);
    expect(sameSession(upgraded, legacy)).toBe(true);
    expect(sameSession(legacy, jwt('admin', 'S1', 'chain-up', 'shop-moved'))).toBe(true);
    expect(sameSession(legacy, jwt('admin', 'S1', 'fresh-chain'))).toBe(false); // sign-out, sign-in again
    expect(sameSession(legacy, noChain('another-chainless-login'))).toBe(false);
    expect(sameSession(noChain('never-refreshed'), upgraded)).toBe(false);
  });

  it('positive control: a request waiting out its backoff while this tab upgrades the token still goes, once', async () => {
    const legacy = noChain('in-wait');
    mem.set('ims_token', legacy);
    mem.set('ims_refresh_token', 'r-legacy');
    const upgraded = jwt('admin', 'S1', 'chain-wait');
    vi.spyOn(axios, 'post').mockResolvedValueOnce({ data: { access_token: upgraded, refresh_token: 'r-wait' } });
    await refreshAccessToken(); // the proactive refresh, as if it ran during the wait
    mem.set('ims_token', legacy); // the request below was sent with the old token
    const adapter = answering(429);
    duringBackoff(() => mem.set('ims_token', upgraded));
    expect((await api.post('/tasks', {}, { adapter })).status).toBe(200);
    expect(adapter).toHaveBeenCalledTimes(2);
    expect(sentAs(adapter, 0)).toBe(legacy);
    expect(sentAs(adapter, 1)).toBe(upgraded);
  });
});
