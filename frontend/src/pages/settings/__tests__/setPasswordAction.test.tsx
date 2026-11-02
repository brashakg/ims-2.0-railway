// ============================================================================
// Settings > Users: SUPERADMIN "Set password" action (owner ask 2026-11-01)
// ============================================================================
// Only a SUPERADMIN sees the action; Save stays disabled until the two fields
// match and reach 8 characters; Save posts to the set-password endpoint.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

let currentRoles: string[] = [];

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'me', roles: currentRoles, activeRole: currentRoles[0], storeIds: ['S1'] },
  }),
}));

const mockSetPassword = vi.fn();

vi.mock('../../../services/api', () => ({
  adminUserApi: {
    getUsers: () => Promise.resolve({
      users: [{ user_id: 'u-9', username: 'ravi', full_name: 'Ravi Kumar', roles: ['SALES_STAFF'], store_ids: ['S1'] }],
    }),
    setPassword: (...a: unknown[]) => mockSetPassword(...a),
    resetPassword: vi.fn(),
  },
  adminStoreApi: { getStores: () => Promise.resolve({ stores: [] }) },
}));

import { ToastProvider } from '../../../context/ToastContext';
import { UserManagementSection } from '../SettingsAuth';

const renderPage = () => render(<ToastProvider><UserManagementSection /></ToastProvider>);

beforeEach(() => {
  mockSetPassword.mockReset();
  mockSetPassword.mockResolvedValue({ user_id: 'u-9' });
});

describe('Set password action', () => {
  it('is shown only to a SUPERADMIN', async () => {
    currentRoles = ['ADMIN'];
    const { unmount } = renderPage();
    await screen.findByText('Ravi Kumar');
    expect(screen.queryByTitle('Set password')).toBeNull();
    unmount();

    currentRoles = ['SUPERADMIN'];
    renderPage();
    await screen.findByText('Ravi Kumar');
    expect(screen.getByTitle('Set password')).toBeTruthy();
  });

  it('keeps Save disabled until both fields match, then calls the API', async () => {
    currentRoles = ['SUPERADMIN'];
    renderPage();
    await screen.findByText('Ravi Kumar');
    fireEvent.click(screen.getByTitle('Set password'));

    const save = screen.getByRole('button', { name: 'Save password' }) as HTMLButtonElement;
    fireEvent.change(screen.getByLabelText('New password'), { target: { value: 'Brand@New9' } });
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'Brand@New8' } });
    expect(save.disabled).toBe(true);
    expect(screen.getByText("The two passwords don't match.")).toBeTruthy();

    fireEvent.change(screen.getByLabelText('New password'), { target: { value: 'short' } });
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'short' } });
    expect(save.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText('New password'), { target: { value: 'Brand@New9' } });
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'Brand@New9' } });
    expect(save.disabled).toBe(false);
    fireEvent.click(save);

    await waitFor(() => expect(mockSetPassword).toHaveBeenCalledWith('u-9', 'Brand@New9'));
    await waitFor(() => expect(screen.queryByLabelText('New password')).toBeNull());
  });
});
