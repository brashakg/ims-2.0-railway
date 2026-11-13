// Money review 24 of #1264: a store switch while a bill's press is pending
// parks that bill under ITS store (its key replays only there) and clears
// the till, so pressing again at the new store is never a second sale.
import { act, render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const parkIfPending = vi.fn((): 'parked' | 'not-pending' | null => 'parked');
const resetTransaction = vi.fn();
vi.mock('../../services/api', () => ({
  authApi: { login: vi.fn(), logout: vi.fn(), getProfile: vi.fn(), switchStore: vi.fn(async () => ({})) },
}));
vi.mock('../../stores/posStore', () => ({
  usePOSStore: { getState: () => ({ clearAllOnLogout: () => {}, parkIfPending, resetTransaction }) },
}));

import { authApi } from '../../services/api';
import { AuthProvider, useAuth } from '../AuthContext';

let auth: ReturnType<typeof useAuth>;
function Probe() {
  auth = useAuth();
  return null;
}

beforeEach(() => {
  const mem = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => mem.get(k) ?? null,
    setItem: (k: string, v: string) => void mem.set(k, String(v)),
    removeItem: (k: string) => void mem.delete(k),
    clear: () => mem.clear(),
  });
  parkIfPending.mockClear();
  resetTransaction.mockClear();
});

const signIn = async () => {
  (authApi.login as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
    success: true, token: 't',
    user: { id: 'u1', name: 'u1', roles: ['STORE_MANAGER'], storeIds: ['S1', 'S2'], activeStoreId: 'S1' },
  });
  render(<QueryClientProvider client={new QueryClient()}><AuthProvider><Probe /></AuthProvider></QueryClientProvider>);
  await act(() => auth.login({ username: 'u1', password: 'x' }));
};

describe('a store switch with a pending bill', () => {
  it('parks the pending bill and clears the till before the switch', async () => {
    await signIn();
    await act(() => auth.setActiveStore('S2'));
    expect(parkIfPending).toHaveBeenCalledWith('u1');
    expect(resetTransaction).toHaveBeenCalledTimes(1);
  });

  it('a bill with nothing pending is left as it is', async () => {
    parkIfPending.mockReturnValueOnce('not-pending');
    await signIn();
    await act(() => auth.setActiveStore('S2'));
    expect(resetTransaction).not.toHaveBeenCalled();
  });
});
