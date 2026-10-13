// ============================================================================
// Signing out empties the app's cache of server data (shared tablets)
// ============================================================================
// The app keeps ONE React Query cache for the life of the page, with a
// 5-minute staleTime (App.tsx), and most sign-outs (idle, store picker, forced
// password change) do not reload the page. So a cached list whose key does not
// name the person - most screens' keys do not - was served to whoever signed
// in next. AuthContext.logout, the door every sign-out goes through, now
// clears the cache. Take the clear out and this test goes red.

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

// The runner's localStorage shim lacks methods (see IdleLogoutWatcher.test).
beforeEach(() => {
  const mem = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => mem.get(k) ?? null,
    setItem: (k: string, v: string) => void mem.set(k, String(v)),
    removeItem: (k: string) => void mem.delete(k),
    clear: () => mem.clear(),
  });
});

describe('sign-out on a shared tablet', () => {
  it("the next person never gets the last person's cached task list", async () => {
    (authApi.login as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      async ({ username }: { username: string }) => ({
        success: true,
        token: `tok-${username}`,
        user: { id: username, name: username, roles: ['STORE_MANAGER'], storeIds: ['BV-S1'], activeStoreId: 'BV-S1' },
      }),
    );
    (authApi.logout as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
    const qc = new QueryClient({ defaultOptions: { queries: { staleTime: 5 * 60 * 1000 } } });
    render(
      <QueryClientProvider client={qc}>
        <AuthProvider>
          <Probe />
        </AuthProvider>
      </QueryClientProvider>,
    );

    await act(() => auth.login({ username: 'u-mgr', password: 'x' }));
    // The manager's team list, cached under a key that does not name them.
    const key = ['tasks', 'list', 'team'];
    qc.setQueryData(key, { tasks: [{ title: "Till short by Rs 4,200 - Ravi's shift" }] });

    await act(() => auth.logout());
    await act(() => auth.login({ username: 'u-sales', password: 'x' }));

    expect(screen.getByText('u-sales')).toBeInTheDocument();
    expect(qc.getQueryData(key)).toBeUndefined();
    expect(qc.getQueryCache().getAll()).toHaveLength(0);
  });
});
