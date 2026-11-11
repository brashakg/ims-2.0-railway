// ============================================================================
// Decision D15 (L3-02 follow-up) - Payroll Run: an admin can discard a draft
// ============================================================================
// A month cannot be locked while a payroll row is a draft, and a draft whose
// salary setup was switched off cannot be rerun: the only way out used to be
// approving a figure known to be wrong. Each saved DRAFT row now carries one
// small "Discard draft" button (administrators only, with a confirm).
//
// The server half (drafts only, never a locked month, the audit-log row) is
// backend/tests/test_audit_b27_payroll_discard_draft.py.
//
// Every test drives the REAL page with only the API, auth and toast mocked.

import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

let currentUser = { id: 'admin-1', name: 'ZZ Admin', roles: ['ADMIN'], activeStoreId: 'ZZ-STORE' };
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: currentUser }),
}));

const toast = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));

const payrollApi = {
  listConfigs: vi.fn(),
  listRunRows: vi.fn(),
  getSummary: vi.fn(),
  discardDraft: vi.fn(),
};
vi.mock('../../../services/api/payroll', () => ({
  payrollApi: new Proxy({}, { get: (_t, k: string) => (payrollApi as Record<string, unknown>)[k] }),
  grossOf: (c: { basic?: number; hra?: number }) => (c.basic || 0) + (c.hra || 0),
}));
vi.mock('../../../services/api/entities', () => ({
  entitiesApi: { list: vi.fn(async () => ({ entities: [], total: 0 })) },
}));

import { PayrollRunPage } from '../PayrollRunPage';

const row = (employee_id: string, status: string, saved = true) => ({
  ...(saved ? { payroll_id: `PR-${employee_id}` } : {}),
  employee_id, employee_name: `ZZ ${employee_id}`, status, net_salary: 17750, deductions: 2250,
});
const ROWS = [row('EMP-D', 'DRAFT'), row('EMP-A', 'APPROVED'), row('EMP-P', 'PAID')];
const now = new Date();
const PERIOD = { month: now.getMonth() + 1, year: now.getFullYear() };

let confirm: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.clearAllMocks();
  currentUser = { id: 'admin-1', name: 'ZZ Admin', roles: ['ADMIN'], activeStoreId: 'ZZ-STORE' };
  payrollApi.listConfigs.mockResolvedValue({ configs: [], total: 0 });
  payrollApi.listRunRows.mockResolvedValue({ rows: ROWS, total: ROWS.length, totals: {} });
  payrollApi.getSummary.mockResolvedValue({ summary: null });
  payrollApi.discardDraft.mockResolvedValue({ status: 'success', discarded: 1 });
  confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
});

afterEach(() => {
  vi.restoreAllMocks();
});

async function registerRow(name: string) {
  render(<PayrollRunPage />);
  return (await screen.findByText(name)).closest('tr') as HTMLElement;
}
const discardIn = (tr: HTMLElement) => within(tr).queryByRole('button', { name: 'Discard draft' });

describe('D15 Payroll Run: discard a payroll draft', () => {
  it('only a saved DRAFT row offers it: not an approved or paid one', async () => {
    const draft = await registerRow('ZZ EMP-D');
    expect(discardIn(draft)).toBeInTheDocument();
    for (const name of ['ZZ EMP-A', 'ZZ EMP-P']) {
      expect(discardIn(screen.getByText(name).closest('tr') as HTMLElement)).toBeNull();
    }
  });

  it('a preview row (never saved) has nothing to discard', async () => {
    const rows = [row('EMP-D', 'DRAFT', false)];
    payrollApi.listRunRows.mockResolvedValue({ rows, total: 1, totals: {} });
    expect(discardIn(await registerRow('ZZ EMP-D'))).toBeNull();
  });

  it.each(['ACCOUNTANT', 'STORE_MANAGER'])('%s is not offered it', async (role) => {
    currentUser = { ...currentUser, roles: [role] };
    expect(discardIn(await registerRow('ZZ EMP-D'))).toBeNull();
  });

  it('asks first, naming the employee and the figure; "Cancel" sends nothing', async () => {
    confirm.mockReturnValue(false);
    fireEvent.click(discardIn(await registerRow('ZZ EMP-D')) as HTMLElement);
    expect(confirm).toHaveBeenCalledTimes(1);
    const question = String(confirm.mock.calls[0][0]);
    expect(question).toContain('ZZ EMP-D');
    expect(question).toContain('₹17,750');
    expect(payrollApi.discardDraft).not.toHaveBeenCalled();
  });

  it('on "OK" discards that one employee for the month on screen and reloads the register', async () => {
    fireEvent.click(discardIn(await registerRow('ZZ EMP-D')) as HTMLElement);
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Payroll draft of ZZ EMP-D discarded'));
    expect(payrollApi.discardDraft.mock.calls).toEqual([[{ ...PERIOD, employee_id: 'EMP-D' }]]);
    expect(payrollApi.listRunRows).toHaveBeenCalledTimes(2);
  });

  it('shows the server\'s reason when it refuses (a locked month)', async () => {
    payrollApi.discardDraft.mockRejectedValue(
      new Error('Accounting period 10/2026 is locked; cannot post to a closed month.'),
    );
    fireEvent.click(discardIn(await registerRow('ZZ EMP-D')) as HTMLElement);
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        'Accounting period 10/2026 is locked; cannot post to a closed month.',
      ),
    );
    expect(toast.success).not.toHaveBeenCalled();
  });
});
