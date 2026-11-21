// PR #1424 round 2: a logged permission change that never landed (it lost the
// D70 compare-and-set and returned 409) is marked not_applied by the server.
// The timeline must say so and must not offer "Revert to this" for it.
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { UserPermissionsPanel } from '../UserPermissionsPanel';

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
        discount_cap: 15,
        history: [
          { log_id: 'abort', action: 'PERMISSIONS_UPDATE_ABORTED', actor_username: 'a', note: 'Not applied' },
          { log_id: 'phantom', action: 'PERMISSIONS_UPDATE', actor_username: 'a', not_applied: true },
          { log_id: 'real', action: 'PERMISSIONS_UPDATE', actor_username: 'b' },
        ],
      }),
  },
}));

describe('permission history: a change that never landed', () => {
  it('is labelled not applied and offers no Revert', async () => {
    render(<UserPermissionsPanel userId="u1" roles={['SALES_STAFF']} />);
    expect(await screen.findByText('Permissions changed (not applied)')).toBeTruthy();
    expect(screen.getByText('Change refused - nothing was applied')).toBeTruthy();
    // Revert stays only on the real change: never the phantom or its abort row.
    expect(screen.getAllByRole('button', { name: /Revert to this/ })).toHaveLength(1);
  });
});
