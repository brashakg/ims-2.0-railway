// A forced sign-out (a 401 the client cannot refresh) is a sign-out door of its
// own, outside AuthContext.logout: it too must drop the Ctrl-K recent lists,
// which hold customer names and phones (Codex R09).
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { InternalAxiosRequestConfig } from 'axios';
import api from '../client';

let mem: Map<string, string>;
beforeEach(() => {
  mem = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => mem.get(k) ?? null,
    setItem: (k: string, v: string) => void mem.set(k, String(v)),
    removeItem: (k: string) => void mem.delete(k),
    clear: () => mem.clear(),
    key: (i: number) => [...mem.keys()][i] ?? null,
    get length() { return mem.size; },
  });
  vi.stubGlobal('location', { ...window.location, href: '' });
});

describe('forced 401 sign-out', () => {
  it('drops every Ctrl-K recent list with the tokens', async () => {
    mem.set('ims_user', JSON.stringify({ id: 'u-admin', activeStoreId: 'BV-S1' }));
    mem.set('cmdk_recent_v1:u-admin:BV-S1:ADMIN', '[{"type":"customer","id":"C-1","label":"AUDIT ONLY Customer"}]');
    mem.set('cmdk_recent_v1', '[]');
    mem.set('unrelated', 'kept');
    // No token in storage, so no refresh is possible: the 401 is final.
    const adapter = async (config: InternalAxiosRequestConfig) =>
      Promise.reject(Object.assign(new Error('401'), {
        isAxiosError: true, config, response: { status: 401, data: {}, headers: {}, config, statusText: '' },
      }));
    await expect(api.get('/customers/C-1', { adapter })).rejects.toBeTruthy();
    expect([...mem.keys()].filter((k) => k.startsWith('cmdk_recent_v1'))).toEqual([]);
    expect(mem.get('unrelated')).toBe('kept');
  });
});
