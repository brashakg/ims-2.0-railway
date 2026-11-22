// D93 (#1424): the permissions panel saves a cap without roles, so it must
// tell the server which roles it was showing; the server answers 409 if they
// changed meanwhile. Save and Revert both carry them.
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { UserPermissionsPanel } from '../UserPermissionsPanel';

const updateUser = vi.fn(() => Promise.resolve({}));
const revertUserPermissions = vi.fn(() => Promise.resolve({}));

vi.mock('../PermissionDeltaEditor', () => ({ PermissionDeltaEditor: () => null }));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}));
vi.mock('../../../services/api', () => ({
  adminUserApi: {
    getUserPermissions: () =>
      Promise.resolve({
        permissions: {},
        module_access: {},
        discount_cap: 10,
        history: [{ log_id: 'real', action: 'PERMISSIONS_UPDATE', actor_username: 'b' }],
      }),
    updateUser: (...a: unknown[]) => updateUser(...(a as [])),
    revertUserPermissions: (...a: unknown[]) => revertUserPermissions(...(a as [])),
  },
}));

describe('permissions panel: held to the roles on screen', () => {
  it('sends the roles it showed on Save and on Revert', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<UserPermissionsPanel userId="u1" roles={['SALES_STAFF']} />);
    fireEvent.click(await screen.findByRole('button', { name: /Save permissions/ }));
    await waitFor(() => expect(updateUser).toHaveBeenCalled());
    expect(updateUser.mock.calls[0]).toEqual([
      'u1',
      expect.objectContaining({ expectedRoles: ['SALES_STAFF'] }),
    ]);
    fireEvent.click(await screen.findByRole('button', { name: /Revert to this/ }));
    await waitFor(() => expect(revertUserPermissions).toHaveBeenCalled());
    expect(revertUserPermissions.mock.calls[0]).toEqual(['u1', 'real', ['SALES_STAFF']]);
  });
});
