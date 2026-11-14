// ============================================================================
// Codex whole-app audit, batch b27 - L3-17, the Salary Setup screen half
// ============================================================================
// Owner ruling 2026-11-11: a gender field on the employee salary setup, filled
// by an admin, feeds the Professional Tax slab; while it is not set the old
// behaviour stays and the screen says so. Before this the form had no such
// field at all (and the server threw a posted gender away).
//
// The server half (the field is stored and reaches the payroll engine) is
// backend/tests/test_audit_b27_payroll_engine_gender.py. The Salary Sheet's
// empty month (L3-16) is in payrollSections.test.tsx, on the real route table.
//
// Every test drives the REAL page with only the API, auth and toast mocked.

import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

let currentUser = { id: 'admin-1', name: 'ZZ Admin', roles: ['ADMIN'], activeStoreId: 'ZZ-STORE' };
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: currentUser }),
}));

const toast = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));

const payrollApi = {
  listConfigs: vi.fn(),
  listPtSlabs: vi.fn(),
  createConfig: vi.fn(),
  updateConfig: vi.fn(),
  bulkConfigs: vi.fn(),
};
vi.mock('../../../services/api/payroll', () => ({
  payrollApi: new Proxy({}, { get: (_t, k: string) => (payrollApi as Record<string, unknown>)[k] }),
  grossOf: (c: { basic?: number; hra?: number }) => (c.basic || 0) + (c.hra || 0),
}));
vi.mock('../../../services/api/entities', () => ({
  entitiesApi: { list: vi.fn(async () => ({ entities: [], total: 0 })) },
}));
vi.mock('../../../services/api/stores', () => ({
  storeApi: { getStores: vi.fn(async () => ({ stores: [] })) },
}));

import { SalarySetupPage, parseSalaryCsv } from '../SalarySetupPage';

const NOT_SET_NOTE =
  'While gender is not set, Payroll Run charges Professional Tax on the general (male) slab, as before.';
const UNSET = { employee_id: 'EMP-UNSET', basic: 12000, hra: 3000 };
const FEMALE = { employee_id: 'EMP-F', basic: 12000, hra: 3000, gender: 'FEMALE' };
const MH = { state_code: 'MH', state_name: 'Maharashtra', basis: 'MONTHLY', gender_aware: true, slabs: [] };
const JH = { state_code: 'JH', state_name: 'Jharkhand', basis: 'ANNUAL', gender_aware: false, slabs: [] };

beforeEach(() => {
  vi.clearAllMocks();
  currentUser = { id: 'admin-1', name: 'ZZ Admin', roles: ['ADMIN'], activeStoreId: 'ZZ-STORE' };
  payrollApi.listConfigs.mockResolvedValue({ configs: [UNSET, FEMALE], total: 2 });
  payrollApi.listPtSlabs.mockResolvedValue({ pt_slabs: [MH, JH], total: 2, source: 'db' });
  payrollApi.createConfig.mockResolvedValue({ status: 'success', config_id: 'C-1' });
  payrollApi.updateConfig.mockResolvedValue({ status: 'success' });
  payrollApi.bulkConfigs.mockResolvedValue({ created: 0, updated: 1, total: 1 });
});

const genderSelect = () => screen.getByLabelText(/Gender \(for Professional Tax\)/) as HTMLSelectElement;

async function openEdit(employeeId: string) {
  render(<SalarySetupPage />);
  const row = (await screen.findByText(employeeId)).closest('tr') as HTMLElement;
  fireEvent.click(within(row).getByRole('button', { name: 'Edit' }));
  return row;
}

describe('L3-17 Salary Setup: the admin fills the gender used for Professional Tax', () => {
  it('the list shows each employee\'s gender, and says what "not set" means', async () => {
    render(<SalarySetupPage />);
    const unset = (await screen.findByText('EMP-UNSET')).closest('tr') as HTMLElement;
    const female = screen.getByText('EMP-F').closest('tr') as HTMLElement;
    expect(within(unset).getByText('Not set')).toBeInTheDocument();
    expect(within(female).getByText('Female')).toBeInTheDocument();
    // Only the state whose slab is split by gender is named.
    expect(
      screen.getByText(`Maharashtra has a separate slab for women. ${NOT_SET_NOTE}`),
    ).toBeInTheDocument();
  });

  it('no gender-split slab configured: the slab note is not shown', async () => {
    payrollApi.listPtSlabs.mockResolvedValue({ pt_slabs: [JH], total: 1, source: 'db' });
    render(<SalarySetupPage />);
    await screen.findByText('EMP-UNSET');
    expect(screen.queryByText(/has a separate slab for women/)).not.toBeInTheDocument();
  });

  it('editing an employee with no gender: the field reads "Not set", says so, and saves Female', async () => {
    await openEdit('EMP-UNSET');
    expect(genderSelect().value).toBe('');
    expect(within(genderSelect()).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Not set', 'Male', 'Female',
    ]);
    expect(within(genderSelect().closest('label') as HTMLElement).getByText(NOT_SET_NOTE)).toBeInTheDocument();

    fireEvent.change(genderSelect(), { target: { value: 'FEMALE' } });
    fireEvent.click(screen.getByRole('button', { name: 'Update' }));
    await waitFor(() => expect(payrollApi.updateConfig).toHaveBeenCalledTimes(1));
    expect(payrollApi.updateConfig).toHaveBeenCalledWith(
      'EMP-UNSET',
      expect.objectContaining({ employee_id: 'EMP-UNSET', basic: 12000, gender: 'FEMALE' }),
    );
  });

  it('editing an employee whose gender is saved shows it, and it can be set back to "Not set"', async () => {
    await openEdit('EMP-F');
    expect(genderSelect().value).toBe('FEMALE');
    fireEvent.change(genderSelect(), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Update' }));
    await waitFor(() => expect(payrollApi.updateConfig).toHaveBeenCalledTimes(1));
    expect(payrollApi.updateConfig).toHaveBeenCalledWith('EMP-F', expect.objectContaining({ gender: '' }));
  });

  it('a new salary setup carries the gender the admin picked', async () => {
    render(<SalarySetupPage />);
    fireEvent.click(await screen.findByRole('button', { name: '+ Add salary' }));
    fireEvent.change(screen.getByLabelText('Employee ID *'), { target: { value: 'EMP-NEW' } });
    fireEvent.change(screen.getByLabelText('Basic *'), { target: { value: '15000' } });
    fireEvent.change(genderSelect(), { target: { value: 'MALE' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(payrollApi.createConfig).toHaveBeenCalledTimes(1));
    expect(payrollApi.createConfig).toHaveBeenCalledWith(
      expect.objectContaining({ employee_id: 'EMP-NEW', basic: 15000, gender: 'MALE' }),
    );
  });

  it('a non-admin gets no Edit button, so no gender field to fill', async () => {
    currentUser = { id: 'acc-1', name: 'ZZ Accountant', roles: ['ACCOUNTANT'], activeStoreId: 'ZZ-STORE' };
    render(<SalarySetupPage />);
    await screen.findByText('EMP-UNSET');
    expect(screen.queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '+ Add salary' })).not.toBeInTheDocument();
  });

  it('CSV import: a gender column is sent in the server\'s spelling, and a wrong value names its row', () => {
    expect(
      parseSalaryCsv('employee_id,basic,gender\nEMP-A,20000,female\nEMP-B,20000,\nEMP-C,20000,woman'),
    ).toEqual({
      rows: [
        { employee_id: 'EMP-A', basic: 20000, gender: 'FEMALE' },
        { employee_id: 'EMP-B', basic: 20000, gender: '' },
      ],
      skipped: [{ line: 4, employee_id: 'EMP-C', reason: 'gender must be MALE or FEMALE (or empty)' }],
    });
    // No gender column: nothing about gender is sent, so the saved one stays.
    expect(parseSalaryCsv('employee_id,basic\nEMP-A,20000').rows).toEqual([
      { employee_id: 'EMP-A', basic: 20000 },
    ]);
  });
});
