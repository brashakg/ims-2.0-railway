// ============================================================================
// Settings > Users and Brand Master: a refused save keeps the whole draft
// (audit L1-F02)
// ============================================================================
// The audit filled a new user, got a real 422 for the e-mail, and the form
// came back with every field blank: saving raised the PAGE-wide loading flag,
// which unmounted the open form. Brand Master did the same. The form now
// stays mounted behind its own busy flag for every kind of refusal, a second
// press cannot send a second create, and success still closes and refreshes.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'me', roles: ['SUPERADMIN'], activeRole: 'SUPERADMIN', storeIds: ['S1'] } }),
}));
vi.mock('../../../components/permissions/PermissionDeltaEditor', () => ({ PermissionDeltaEditor: () => null }));
vi.mock('../../../components/permissions/UserPermissionsPanel', () => ({ UserPermissionsPanel: () => null }));

const createUser = vi.fn();
const getUsers = vi.fn();
const createBrand = vi.fn();
const getBrands = vi.fn();
vi.mock('../../../services/api', () => ({
  adminUserApi: {
    getUsers: () => getUsers(),
    createUser: (...a: unknown[]) => createUser(...a),
    updateUser: vi.fn(),
  },
  adminStoreApi: { getStores: () => Promise.resolve({ stores: [{ store_id: 'S1', store_name: 'Store A', store_code: 'A' }] }) },
  adminBrandApi: { getBrands: () => getBrands(), createBrand: (...a: unknown[]) => createBrand(...a) },
  adminDiscountApi: {},
}));

import { UserManagementSection } from '../SettingsAuth';
import { BrandSection } from '../SettingsStore';

const apiError = (status: number, message: string) => Object.assign(new Error(message), { status });

async function fillNewUser() {
  render(<UserManagementSection />);
  fireEvent.click(await screen.findByRole('button', { name: /add user/i }));
  fireEvent.change(screen.getByLabelText('Username *'), { target: { value: 'audit.user' } });
  fireEvent.change(screen.getByLabelText('Full Name *'), { target: { value: 'Audit User' } });
  fireEvent.change(screen.getByLabelText('Email *'), { target: { value: 'audit@example.invalid' } });
  fireEvent.change(screen.getByLabelText('Phone'), { target: { value: '9876543210' } });
  fireEvent.change(screen.getByLabelText('Password *'), { target: { value: 'Draft@Pass9' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'SALES STAFF' }));
}

function expectDraftIntact() {
  expect(screen.getByLabelText('Username *')).toHaveValue('audit.user');
  expect(screen.getByLabelText('Full Name *')).toHaveValue('Audit User');
  expect(screen.getByLabelText('Email *')).toHaveValue('audit@example.invalid');
  expect(screen.getByLabelText('Phone')).toHaveValue('9876543210');
  expect(screen.getByLabelText('Password *')).toHaveValue('Draft@Pass9');
  expect(screen.getByRole('checkbox', { name: 'SALES STAFF' })).toBeChecked();
}

beforeEach(() => {
  vi.clearAllMocks();
  getUsers.mockResolvedValue({ users: [] });
  getBrands.mockResolvedValue({ brands: [] });
});

describe('Add user: a refused save keeps the draft (L1-F02)', () => {
  it.each([
    ['validation 422', apiError(422, 'email: value is not a valid email address')],
    ['conflict 409', apiError(409, 'Username already exists')],
    ['forbidden 403', apiError(403, 'You cannot assign that role')],
    ['server 500', apiError(500, 'Internal Server Error')],
    ['network failure', new Error('Network Error')],
  ])('%s: every field is still there and the reason is shown', async (_name, err) => {
    createUser.mockRejectedValue(err);
    await fillNewUser();
    fireEvent.click(screen.getByRole('button', { name: 'Create User' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(err.message));
    expectDraftIntact();
    expect(screen.getByRole('button', { name: 'Create User' })).toBeEnabled();
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('a corrected retry creates exactly one user, then closes and refreshes the list', async () => {
    createUser.mockRejectedValueOnce(apiError(422, 'email: value is not a valid email address'));
    createUser.mockResolvedValue({ user_id: 'u-new' });
    await fillNewUser();
    fireEvent.click(screen.getByRole('button', { name: 'Create User' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText('Email *'), { target: { value: 'audit@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create User' }));

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('User created successfully'));
    expect(createUser).toHaveBeenCalledTimes(2); // one refused, one accepted
    expect(createUser.mock.calls[1][0]).toMatchObject({
      name: 'Audit User', email: 'audit@example.com', username: 'audit.user', password: 'Draft@Pass9', roles: ['SALES_STAFF'],
    });
    await waitFor(() => expect(screen.queryByLabelText('Username *')).toBeNull());
    expect(getUsers).toHaveBeenCalledTimes(2); // first load + refresh
  });

  it('a second press while the save is in flight does not send a second create', async () => {
    let finish: (v: unknown) => void = () => {};
    createUser.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    await fillNewUser();
    const save = screen.getByRole('button', { name: 'Create User' });
    fireEvent.click(save);
    fireEvent.click(save);

    const busy = await screen.findByRole('button', { name: 'Saving…' });
    expect(busy).toBeDisabled();
    expectDraftIntact(); // the form is still mounted while saving
    expect(createUser).toHaveBeenCalledTimes(1);
    finish({ user_id: 'u-new' });
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
  });
});

describe('Add brand: a refused save keeps the draft (L1-F02, brand door)', () => {
  it('keeps the name and code after a 422 and reports the reason', async () => {
    createBrand.mockRejectedValue(apiError(422, 'code: String should have at least 2 characters'));
    render(<MemoryRouter><BrandSection /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: 'Add Brand' }));
    fireEvent.change(screen.getByLabelText('Brand Name *'), { target: { value: 'Audit Brand' } });
    fireEvent.change(screen.getByLabelText('Brand Code *'), { target: { value: 'A' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create Brand' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('code: String should have at least 2 characters'));
    expect(screen.getByLabelText('Brand Name *')).toHaveValue('Audit Brand');
    expect(screen.getByLabelText('Brand Code *')).toHaveValue('A');
    expect(createBrand).toHaveBeenCalledTimes(1);
  });
});
