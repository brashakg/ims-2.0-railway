// ============================================================================
// A sign-out or account change in one tab locks every other tab (Codex R21)
// ============================================================================
// Two tabs on the same Customer 360: signing out in one left the other showing
// the customer's name, phone and spend under the old identity, and signing an
// INVESTOR in did not change that either. The tabs share localStorage, so the
// other tab now follows the auth keys through the browser's `storage` event:
// the token gone, or a different person's user, empties its screen and cache
// and reloads it. A token refresh for the SAME person (the api client rotates
// ims_token / ims_refresh_token while the user is active) changes nothing.
// Also (Codex R09): signing out drops the Ctrl-K recent lists.

import { act, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api', () => ({
  authApi: { login: vi.fn(), logout: vi.fn(), getProfile: vi.fn() },
}));
vi.mock('../../stores/posStore', () => ({
  usePOSStore: { getState: () => ({ clearAllOnLogout: () => {} }) },
}));

import { authApi } from '../../services/api';
import { AuthProvider, useAuth } from '../AuthContext';

let auth: ReturnType<typeof useAuth>;
function Probe() {
  auth = useAuth();
  return <span>{auth.user?.id ?? 'signed out'}</span>;
}

let mem: Map<string, string>;
const reload = vi.fn();
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
  reload.mockReset();
  vi.stubGlobal('location', { ...window.location, reload });
});

const userOf = (id: string) => ({ id, name: id, roles: ['ADMIN'], storeIds: ['BV-S1'], activeStoreId: 'BV-S1' });

async function signedInTab() {
  (authApi.login as unknown as ReturnType<typeof vi.fn>).mockImplementation(
    async ({ username }: { username: string }) => ({
      success: true, token: `tok-${username}`, refreshToken: `rt-${username}`, user: userOf(username),
    }),
  );
  (authApi.logout as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
  const qc = new QueryClient();
  render(
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </QueryClientProvider>,
  );
  await act(() => auth.login({ username: 'u-admin', password: 'x' }));
  qc.setQueryData(['customer360', 'C-1'], { name: 'AUDIT ONLY Customer', phone: '9000000002' });
  return qc;
}

// What the browser delivers to THIS tab when ANOTHER tab writes the key.
function otherTabWrites(key: string, value: string | null) {
  if (value === null) mem.delete(key);
  else mem.set(key, value);
  act(() => {
    window.dispatchEvent(new StorageEvent('storage', { key, newValue: value }));
  });
}

describe('cross-tab sign-out', () => {
  it('sign-out in another tab empties this tab', async () => {
    const qc = await signedInTab();
    otherTabWrites('ims_token', null);
    otherTabWrites('ims_refresh_token', null);
    otherTabWrites('ims_user', null);
    expect(screen.getByText('signed out')).toBeInTheDocument();
    expect(qc.getQueryCache().getAll()).toHaveLength(0);
    expect(reload).toHaveBeenCalled();
  });

  it('a different person signing in in another tab empties this tab', async () => {
    const qc = await signedInTab();
    otherTabWrites('ims_token', 'tok-u-inv');
    otherTabWrites('ims_user', JSON.stringify({ ...userOf('u-inv'), roles: ['INVESTOR'] }));
    expect(screen.getByText('signed out')).toBeInTheDocument();
    expect(qc.getQueryData(['customer360', 'C-1'])).toBeUndefined();
    expect(reload).toHaveBeenCalled();
  });

  it('a token refresh for the same person in another tab changes nothing', async () => {
    const qc = await signedInTab();
    otherTabWrites('ims_token', 'tok-u-admin-rotated');
    otherTabWrites('ims_refresh_token', 'rt-u-admin-rotated');
    otherTabWrites('ims_user', JSON.stringify({ ...userOf('u-admin'), storeIds: ['BV-S1', 'BV-S2'] }));
    expect(screen.getByText('u-admin')).toBeInTheDocument();
    expect(qc.getQueryData(['customer360', 'C-1'])).toBeDefined();
    expect(reload).not.toHaveBeenCalled();
  });
});

describe('sign-out drops the Ctrl-K recent lists (R09)', () => {
  it('every recent list, old global key included, is gone after sign-out', async () => {
    await signedInTab();
    mem.set('cmdk_recent_v1', '[{"type":"customer","id":"C-1","label":"AUDIT ONLY Customer","route":"/x"}]');
    mem.set('cmdk_recent_v1:u-admin:BV-S1:ADMIN', '[]');
    await act(() => auth.logout());
    expect([...mem.keys()].filter((k) => k.startsWith('cmdk_recent_v1'))).toEqual([]);
  });
});
