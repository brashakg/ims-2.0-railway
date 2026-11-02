// ============================================================================
// IMS 2.0 - the api client and the server-side idle limit
// ============================================================================
// 1. ONE parser: the client's proactive-refresh check reads the last-activity
//    stamp through useIdleLogout's exported _storedActivity, never its own copy
//    of the ims_last_activity parse rule.
// 2. A refresh the server refuses as idle (SESSION_IDLE) signs out to
//    /login?reason=idle, the same landing as the browser's own idle check.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import axios, { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios';

const stored = vi.hoisted(() => ({ value: null as number | null }));
vi.mock('../../../hooks/useIdleLogout', async (orig) => ({
  ...(await orig<typeof import('../../../hooks/useIdleLogout')>()),
  _storedActivity: () => stored.value,
}));

import api from '../client';

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: Record<string, unknown>) =>
    btoa(JSON.stringify(obj)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64(payload)}.signature`;
}

function respond(config: InternalAxiosRequestConfig, status: number, data: unknown): AxiosResponse {
  return { data, status, statusText: String(status), headers: {}, config };
}

let mem: Map<string, string>;
beforeEach(() => {
  mem = new Map();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => mem.get(k) ?? null,
    setItem: (k: string, v: string) => void mem.set(k, String(v)),
    removeItem: (k: string) => void mem.delete(k),
    clear: () => mem.clear(),
  });
  vi.stubGlobal('location', { ...window.location, href: '' });
  stored.value = null;
});
afterEach(() => vi.unstubAllGlobals());

describe('api client refresh check', () => {
  it('reads last activity through the shared _storedActivity parser', async () => {
    // Token 2 min from expiry; NO ims_last_activity in storage - only the shared
    // parser (mocked) says the user is active. A private copy would see null.
    mem.set('ims_token', fakeJwt({ exp: Math.floor(Date.now() / 1000) + 120 }));
    stored.value = Date.now();
    const post = vi.spyOn(axios, 'post').mockResolvedValue({ data: {} });

    await api.get('/x', { adapter: async (c) => respond(c, 200, {}) });

    expect(post).toHaveBeenCalledWith(expect.stringContaining('/auth/refresh'), expect.anything(), expect.anything());
  });

  it('a refresh refused as SESSION_IDLE lands on /login?reason=idle', async () => {
    mem.set('ims_token', fakeJwt({ exp: Math.floor(Date.now() / 1000) - 60 }));
    mem.set('ims_refresh_token', 'r');
    vi.spyOn(axios, 'post').mockRejectedValue(
      new AxiosError('401', '401', undefined, undefined, {
        data: { detail: { code: 'SESSION_IDLE', message: 'idle' } },
        status: 401,
        statusText: '401',
        headers: {},
        config: {} as InternalAxiosRequestConfig,
      }),
    );
    const unauthorized = async (c: InternalAxiosRequestConfig) => {
      throw new AxiosError('401', '401', c, undefined, respond(c, 401, { detail: 'expired' }));
    };

    await expect(api.get('/x', { adapter: unauthorized })).rejects.toBeTruthy();

    expect(window.location.href).toBe('/login?reason=idle');
    expect(mem.has('ims_refresh_token')).toBe(false);
  });

  it('an idle-refused refresh token is never presented again', async () => {
    // Proactive path: the access token is still valid when the server says
    // SESSION_IDLE. Re-presenting the dead token would trip the reuse canary
    // (plain-string 401) and lose the idle landing.
    mem.set('ims_token', fakeJwt({ exp: Math.floor(Date.now() / 1000) + 120 }));
    mem.set('ims_refresh_token', 'r');
    stored.value = Date.now();
    const refusal = (detail: unknown) =>
      new AxiosError('401', '401', undefined, undefined, {
        data: { detail },
        status: 401,
        statusText: '401',
        headers: {},
        config: {} as InternalAxiosRequestConfig,
      });
    const post = vi
      .spyOn(axios, 'post')
      .mockRejectedValueOnce(refusal({ code: 'SESSION_IDLE', message: 'idle' }))
      .mockRejectedValue(refusal('Refresh token already used; session revoked.'));

    await api.get('/x', { adapter: async (c) => respond(c, 200, {}) });
    await vi.waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 0));

    // The access token then dies: the 401 must not re-present 'r'.
    mem.set('ims_token', fakeJwt({ exp: Math.floor(Date.now() / 1000) - 60 }));
    const unauthorized = async (c: InternalAxiosRequestConfig) => {
      throw new AxiosError('401', '401', c, undefined, respond(c, 401, { detail: 'expired' }));
    };
    await expect(api.get('/y', { adapter: unauthorized })).rejects.toBeTruthy();

    expect(post).toHaveBeenCalledTimes(1);
    expect(window.location.href).toBe('/login?reason=idle');
  });
});
