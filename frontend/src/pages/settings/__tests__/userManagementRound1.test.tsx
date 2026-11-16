// PR #1424, Codex round 1 (2026-11-15) + decision D70.
//  - P2 role standard: the editor said "Role standard: 0%" for an Admin (real
//    100) and for Accountant + Sales Staff (real 10): it read the highest role's
//    preset row. It now shows max over ALL roles from the server table.
//  - P2 all users: Settings > Users read one 50-row page; staff beyond it could
//    not be found. A failed read also showed "No users found".
//  - P2 Store Manager: Edit was offered and the server refused it (403).
//  - D70: a typed/legacy cap is kept on a role change unless the editor picks
//    the new standard; a role-standard cap follows the role; an untouched cap
//    is never re-sent (that would turn a role-standard cap into a typed one).
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

let currentRoles: string[] = [];
let listed: Record<string, unknown>[] | Error = [];
const updateUser = vi.fn();

const OPTIONS = {
  schema_version: 1,
  discount_cap_field: '__discount_cap__',
  grantable: [],
  role_deltas: {
    SALES_STAFF: { defaults: [], commonOverrides: [
      { key: '__discount_cap__', label: 'Maximum discount this person can give', type: 'number', default: 10, hard_floor_note: null },
    ] },
  },
  role_discount_caps: { SUPERADMIN: 100, ADMIN: 100, STORE_MANAGER: 20, ACCOUNTANT: 0, SALES_STAFF: 10 },
};

vi.mock('../../../context/AuthContext', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useAuth: () => ({
    user: { id: 'me', roles: currentRoles, activeRole: currentRoles[0], storeIds: ['S1'], activeStoreId: 'S1' },
    hasRole: () => true,
  }),
}));

vi.mock('../../../services/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  adminUserApi: {
    getUsers: () => (listed instanceof Error ? Promise.reject(listed) : Promise.resolve(listed)),
    createUser: vi.fn(),
    updateUser: (...a: unknown[]) => updateUser(...a),
    resetPassword: vi.fn(),
    setPassword: vi.fn(),
    getPermissionOptions: () => Promise.resolve(OPTIONS),
  },
  adminStoreApi: { getStores: () => Promise.resolve({ stores: [] }) },
}));

import { ToastProvider } from '../../../context/ToastContext';
import { UserManagementSection } from '../SettingsAuth';
import { PermissionDeltaEditor } from '../../../components/permissions/PermissionDeltaEditor';

const person = (over: Record<string, unknown>) => ({
  user_id: 'u1', username: 'ravi', full_name: 'Ravi Kumar', email: 'ravi@example.com',
  roles: ['SALES_STAFF'], store_ids: ['S1'], is_active: true, ...over,
});

beforeEach(() => {
  updateUser.mockReset();
  updateUser.mockResolvedValue({ user_id: 'u1' });
  listed = [];
});

const page = () => render(<ToastProvider><UserManagementSection /></ToastProvider>);

describe('role standard in the discount box', () => {
  const editor = (roles: string[], cap: number) => render(
    <PermissionDeltaEditor
      roles={roles} permissions={{}} onPermissionsChange={() => {}}
      discountCap={cap} onDiscountCapChange={() => {}}
      moduleAccess={{}} onModuleAccessChange={() => {}}
      options={OPTIONS} showModuleAccess={false}
    />,
  );

  it('Admin: 100, not 0', () => {
    editor(['ADMIN'], 100);
    expect(screen.getByText(/Role standard: 100%/)).toBeInTheDocument();
  });

  it('Accountant + Sales Staff: 10 (highest of all roles), not 0', () => {
    editor(['ACCOUNTANT', 'SALES_STAFF'], 0);
    expect(screen.getByText(/Role standard: 10%/)).toBeInTheDocument();
  });

  it('says nothing about a standard it does not know', () => {
    render(
      <PermissionDeltaEditor
        roles={['ADMIN']} permissions={{}} onPermissionsChange={() => {}}
        discountCap={100} onDiscountCapChange={() => {}}
        moduleAccess={{}} onModuleAccessChange={() => {}}
        options={{ ...OPTIONS, role_discount_caps: undefined }} showModuleAccess={false}
      />,
    );
    expect(screen.queryByText(/Role standard/)).toBeNull();
  });
});

describe('Settings > Users list', () => {
  it('a failed read says so and offers Retry, never "No users found"', async () => {
    currentRoles = ['SUPERADMIN'];
    listed = new Error('Network Error');
    page();
    expect(await screen.findByRole('alert')).toHaveTextContent(/Could not load the staff list/);
    expect(screen.queryByText(/No users found/)).toBeNull();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  it('finds a person who is past the first 50', async () => {
    currentRoles = ['SUPERADMIN'];
    listed = Array.from({ length: 83 }, (_, i) =>
      person({ user_id: `u${i}`, username: `c1424_cap_${i}`, full_name: `Cap ${i}`, email: `c${i}@example.com` }));
    page();
    fireEvent.change(await screen.findByLabelText('Search users'), { target: { value: 'c1424_cap_80' } });
    expect(await screen.findByText('Cap 80')).toBeInTheDocument();
  });

  it('a Store Manager is not offered Edit / permissions / reset / delete (server: Admin only)', async () => {
    currentRoles = ['STORE_MANAGER'];
    listed = [person({ roles: ['ACCOUNTANT'] })];
    page();
    expect(await screen.findByText('Ravi Kumar')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Edit user/i })).toBeNull();
    expect(screen.queryByTitle('Edit user')).toBeNull();
    expect(screen.queryByTitle('Customize permissions')).toBeNull();
    expect(screen.queryByTitle(/Reset password/)).toBeNull();
    expect(screen.queryByTitle('Delete user')).toBeNull();
  });
});

describe('D70: discount cap on a role change', () => {
  async function editRoleToAccountant(over: Record<string, unknown>) {
    currentRoles = ['SUPERADMIN'];
    listed = [person(over)];
    page();
    fireEvent.click(await screen.findByTitle('Edit user'));
    const modal = screen.getByRole('heading', { name: /Edit User/i }).closest('div.bg-white') as HTMLElement;
    const roles = within(modal).getByText('Roles *').parentElement as HTMLElement;
    fireEvent.click(within(roles).getByRole('checkbox', { name: 'Sales Staff' }));
    fireEvent.click(within(roles).getByRole('checkbox', { name: 'Accountant' }));
    return modal;
  }

  it('a typed cap: asks, keeps by default, and can switch to the standard', async () => {
    const modal = await editRoleToAccountant({ discount_cap: 15, discount_cap_is_role_default: false, effective_discount_cap: 15 });
    expect(await within(modal).findByText(/discount cap is 15%/)).toBeInTheDocument();
    const keep = within(modal).getByRole('radio', { name: /Keep 15%/ });
    expect(keep).toBeChecked();
    fireEvent.click(within(modal).getByRole('radio', { name: /Use the new role's standard \(0%\)/ }));
    fireEvent.click(within(modal).getByRole('button', { name: 'Update User' }));
    await waitFor(() => expect(updateUser).toHaveBeenCalledTimes(1));
    const sent = updateUser.mock.calls[0][1];
    expect(sent.roles).toEqual(['ACCOUNTANT']);
    expect(sent.discountCapFollowRole).toBe(true);
    expect(sent.discountCap).toBeUndefined();
  });

  it('a typed cap kept: nothing about the cap is sent, so the server keeps it', async () => {
    const modal = await editRoleToAccountant({ discount_cap: 15, discount_cap_is_role_default: false });
    await within(modal).findByRole('radio', { name: /Keep 15%/ });
    fireEvent.click(within(modal).getByRole('button', { name: 'Update User' }));
    await waitFor(() => expect(updateUser).toHaveBeenCalledTimes(1));
    const sent = updateUser.mock.calls[0][1];
    expect(sent.discountCapFollowRole).toBe(false);
    expect(sent.discountCap).toBeUndefined();
  });

  it('a role-standard cap: says it follows the role, and the old value is not re-sent', async () => {
    const modal = await editRoleToAccountant({ discount_cap: 10, discount_cap_is_role_default: true });
    expect(await within(modal).findByText(/follows the new role: it becomes 0%/)).toBeInTheDocument();
    expect(within(modal).queryByRole('radio')).toBeNull();
    fireEvent.click(within(modal).getByRole('button', { name: 'Update User' }));
    await waitFor(() => expect(updateUser).toHaveBeenCalledTimes(1));
    expect(updateUser.mock.calls[0][1].discountCap).toBeUndefined();
  });
});
