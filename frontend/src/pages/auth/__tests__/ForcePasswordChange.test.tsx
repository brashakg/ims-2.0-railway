// ============================================================================
// IMS 2.0 - First-password change resumes the normal store-selection step
// ----------------------------------------------------------------------------
// Codex 11-05 (PR #1371, R25): sign-in skips the picker while the password is
// temporary, and nothing brought it back after the change, so a two-shop Area
// Manager landed straight in the hub on the default shop. After a successful
// change the SAME "needs store selection" decision as sign-in must run.
// ============================================================================

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/react';

const navigateSpy = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const orig = await importOriginal<typeof import('react-router-dom')>();
  return { ...orig, useNavigate: () => navigateSpy, useLocation: () => ({ pathname: '/pos/new' }) };
});

const refreshUser = vi.fn(() => Promise.resolve());
const logout = vi.fn(() => Promise.resolve());
let mockUser: Record<string, unknown>;
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: mockUser, refreshUser, logout }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}));

const changePassword = vi.fn();
vi.mock('../../../services/api/auth', () => ({
  authApi: { changePassword: (...a: unknown[]) => changePassword(...a) },
}));
const getStores = vi.fn();
vi.mock('../../../services/api', () => ({
  storeApi: { getStores: (...a: unknown[]) => getStores(...a) },
}));

import { ForcePasswordChange } from '../ForcePasswordChange';

const user = (over: Record<string, unknown>) => ({
  id: 'u1', name: 'New Hire', mustChangePassword: true, activeStoreId: 'AUDTA', ...over,
});

async function changeIt() {
  const { container } = render(<ForcePasswordChange />);
  const set = (sel: string, v: string) =>
    fireEvent.change(container.querySelector(sel)!, { target: { value: v } });
  set('#current-password', 'Temp@1234');
  set('#new-password', 'Brand@New9');
  set('#confirm-password', 'Brand@New9');
  fireEvent.submit(container.querySelector('form')!);
  await waitFor(() => expect(refreshUser).toHaveBeenCalled());
}

const toPicker = ['/select-store', { state: { from: '/pos/new' }, replace: true }];

beforeEach(() => {
  navigateSpy.mockReset();
  refreshUser.mockClear();
  getStores.mockReset();
  changePassword.mockReset().mockResolvedValue({ message: 'Password changed successfully' });
});

describe('ForcePasswordChange -> store selection', () => {
  it('sends a two-shop Area Manager to the picker, keeping the destination', async () => {
    mockUser = user({ roles: ['AREA_MANAGER'], storeIds: ['AUDTA', 'AUDTB'] });
    await changeIt();
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith(...toPicker));
  });

  it('sends an Admin to the picker when the org has more than one shop', async () => {
    mockUser = user({ roles: ['ADMIN'], storeIds: ['AUDTA'] });
    getStores.mockResolvedValue({ stores: [{ store_id: 'AUDTA' }, { store_id: 'AUDTB' }] });
    await changeIt();
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith(...toPicker));
  });

  it('lets a single-shop user continue straight in', async () => {
    mockUser = user({ roles: ['SALES_STAFF'], storeIds: ['AUDTA'] });
    await changeIt();
    await new Promise((r) => setTimeout(r, 0));
    expect(navigateSpy).not.toHaveBeenCalled();
  });

  it('lets an Admin of a one-shop org continue straight in', async () => {
    mockUser = user({ roles: ['ADMIN'], storeIds: [] });
    getStores.mockResolvedValue({ stores: [{ store_id: 'AUDTA' }] });
    await changeIt();
    await new Promise((r) => setTimeout(r, 0));
    expect(navigateSpy).not.toHaveBeenCalled();
  });

  it('a failed change goes nowhere', async () => {
    mockUser = user({ roles: ['AREA_MANAGER'], storeIds: ['AUDTA', 'AUDTB'] });
    changePassword.mockRejectedValue(new Error('Current password is incorrect'));
    const { container, findByRole } = render(<ForcePasswordChange />);
    const set = (sel: string, v: string) =>
      fireEvent.change(container.querySelector(sel)!, { target: { value: v } });
    set('#current-password', 'Wrong@1234');
    set('#new-password', 'Brand@New9');
    set('#confirm-password', 'Brand@New9');
    fireEvent.submit(container.querySelector('form')!);
    await findByRole('alert');
    expect(refreshUser).not.toHaveBeenCalled();
    expect(navigateSpy).not.toHaveBeenCalled();
  });
});
