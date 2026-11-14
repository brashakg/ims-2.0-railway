// L1-F03 + L1-F04 (audit 2026-11-04): the two staff-creation doors -- Settings
// > Users (User Management) and Staff Onboarding (the wizard) -- must share one
// rule for (a) the default discount authority and (b) the roles on offer.
//   L1-F03: User Management started every new person at a 10% discount
//   override (an Accountant became effectively 10%), and its Discount Cap box
//   showed `value || 10`, so a typed or stored 0 read as 10.
//   L1-F04: User Management offered no Cashier / Design Manager; the wizard
//   offered Cashier but no Design Manager.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

let currentRoles: string[] = [];
let listed: Record<string, unknown>[] = [];
const createUser = vi.fn();

vi.mock('../../../context/AuthContext', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useAuth: () => ({
    user: { id: 'me', roles: currentRoles, activeRole: currentRoles[0], storeIds: ['S1'], activeStoreId: 'S1' },
    hasRole: (want: string | string[]) =>
      currentRoles.includes('SUPERADMIN') || currentRoles.includes('ADMIN') || [want].flat().some((r) => currentRoles.includes(r)),
  }),
}));

vi.mock('../../../services/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  adminUserApi: {
    getUsers: () => Promise.resolve({ users: listed }),
    createUser: (...a: unknown[]) => createUser(...a),
    updateUser: vi.fn(),
    resetPassword: vi.fn(),
    setPassword: vi.fn(),
    getPermissionOptions: () => Promise.resolve({
      schema_version: 1,
      discount_cap_field: '__discount_cap__',
      grantable: [],
      role_deltas: {
        SALES_STAFF: { defaults: [], commonOverrides: [
          { key: '__discount_cap__', label: 'Maximum discount this person can give', type: 'number', default: 10, hard_floor_note: null },
        ] },
      },
    }),
  },
  adminStoreApi: { getStores: () => Promise.resolve({ stores: [] }) },
}));

import { ToastProvider } from '../../../context/ToastContext';
import { UserManagementSection } from '../SettingsAuth';
import SetupPage from '../SetupPage';

beforeEach(() => {
  createUser.mockReset();
  createUser.mockResolvedValue({ user_id: 'new-1' });
  listed = [];
});

const norm = (s: string) => s.trim().toLowerCase();

async function userManagementRoleLabels(role: string): Promise<string[]> {
  currentRoles = [role];
  const { unmount } = render(<ToastProvider><UserManagementSection /></ToastProvider>);
  fireEvent.click(await screen.findByRole('button', { name: /Add User/ }));
  const heading = screen.getByRole('heading', { name: 'Add New User' });
  const modal = heading.closest('div.bg-white') as HTMLElement;
  const rolesBlock = within(modal).getByText('Roles *').parentElement as HTMLElement;
  const labels = [...rolesBlock.querySelectorAll('label span')].map((s) => norm(s.textContent || ''));
  unmount();
  return labels;
}

async function wizardRoleLabels(role: string): Promise<string[]> {
  currentRoles = [role];
  const { unmount } = render(<ToastProvider><SetupPage /></ToastProvider>);
  fireEvent.click(screen.getByRole('button', { name: /Onboard Employee/ }));
  fireEvent.change(screen.getByPlaceholderText('e.g. Priya Sharma'), { target: { value: 'Priya Sharma' } });
  fireEvent.change(screen.getByPlaceholderText(/10-digit mobile/), { target: { value: '9876543210' } });
  fireEvent.change(screen.getByPlaceholderText('name@example.com'), { target: { value: 'priya@example.com' } });
  fireEvent.click(screen.getByRole('button', { name: /Continue/ }));
  await screen.findByText('What will they do?');
  const labels = [...document.querySelectorAll('button span.text-sm.font-semibold')].map((s) => norm(s.textContent || ''));
  unmount();
  return labels;
}

describe('L1-F04: both doors offer the same roles for the same actor', () => {
  it.each(['ADMIN', 'SUPERADMIN'])('%s sees one role list in User Management and Staff Onboarding', async (role) => {
    const um = await userManagementRoleLabels(role);
    const wiz = await wizardRoleLabels(role);
    expect(um).toContain('cashier');
    expect(um).toContain('design manager');
    expect(wiz).toContain('design manager');
    expect([...um].sort()).toEqual([...wiz].sort());
    // SUPERADMIN is never offered to an ADMIN by either door.
    if (role === 'ADMIN') expect(um.some((l) => l.startsWith('superadmin'))).toBe(false);
  });
});

describe('L1-F03: User Management creates with the role default, never a silent 10%', () => {
  it('a new Accountant is sent with NO discount override (server stores the role baseline, 0%)', async () => {
    currentRoles = ['SUPERADMIN'];
    render(<ToastProvider><UserManagementSection /></ToastProvider>);
    fireEvent.click(await screen.findByRole('button', { name: /Add User/ }));
    const modal = screen.getByRole('heading', { name: 'Add New User' }).closest('div.bg-white') as HTMLElement;
    const texts = modal.querySelectorAll('input[type="text"]');
    fireEvent.change(texts[0], { target: { value: 'acct1' } });
    fireEvent.change(texts[1], { target: { value: 'Asha Accountant' } });
    fireEvent.change(modal.querySelector('input[type="email"]')!, { target: { value: 'asha@example.com' } });
    fireEvent.change(modal.querySelector('input[type="password"]')!, { target: { value: 'Temp@1234' } });
    fireEvent.click(within(modal).getByText(/^accountant$/i).closest('label')!.querySelector('input')!);
    fireEvent.click(within(modal).getByRole('button', { name: 'Create User' }));
    await waitFor(() => expect(createUser).toHaveBeenCalledTimes(1));
    const sent = createUser.mock.calls[0][0] as Record<string, unknown>;
    expect(sent.roles).toEqual(['ACCOUNTANT']);
    expect(sent.discountCap).toBeUndefined();
  });

  it('a stored 0 shows as 0 in the list and in the edit form (not 10)', async () => {
    currentRoles = ['SUPERADMIN'];
    listed = [
      { user_id: 'u-1', username: 'ravi', full_name: 'Ravi Sales', roles: ['SALES_STAFF'], store_ids: ['S1'],
        discount_cap: 0, effective_discount_cap: 10 },
      { user_id: 'u-2', username: 'asha', full_name: 'Asha Books', roles: ['ACCOUNTANT'], store_ids: [],
        discount_cap: 0, effective_discount_cap: 0 },
    ];
    render(<ToastProvider><UserManagementSection /></ToastProvider>);
    const ashaRow = (await screen.findByText('Asha Books')).closest('tr')!;
    // The list states the cap actually enforced (server rule): Accountant 0%.
    expect(within(ashaRow).getByText('0%')).toBeTruthy();
    const raviRow = screen.getByText('Ravi Sales').closest('tr')!;
    expect(within(raviRow).getByText('10%')).toBeTruthy();

    fireEvent.click(within(raviRow).getByTitle('Edit user'));
    const modal = screen.getByRole('heading', { name: 'Edit User' }).closest('div.bg-white') as HTMLElement;
    await within(modal).findByText('Maximum discount this person can give');
    const caps = [...modal.querySelectorAll('input[type="number"]')] as HTMLInputElement[];
    expect(caps.map((c) => c.value)).toEqual(['0']);
  });
});
