// L3-02 (audit b09): locking a month whose payroll has not been run for
// someone would lose that month's wages for good (a saving run answers 423 in
// a locked month). The server answers 409 PAYROLL_NOT_RUN; the Finance screen
// asks, and only an explicit "OK" sends the lock again with without_payroll.
import { it, expect, vi, beforeEach, afterEach, type MockInstance } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-a', roles: ['ADMIN'], activeRole: 'ADMIN', activeStoreId: 'BV-DHN-01' } }),
}));
const fin = vi.hoisted(() => ({
  getRevenue: vi.fn(), getPnl: vi.fn(), getGstSummary: vi.fn(), getOutstanding: vi.fn(), getCashFlow: vi.fn(),
  getBudget: vi.fn(), getVendorPayments: vi.fn(), getPeriodStatus: vi.fn(), getPnlByStore: vi.fn(),
  getPnlByCategory: vi.fn(), getGstReconciliation: vi.fn(), lockPeriod: vi.fn(),
}));
vi.mock('../../../services/api/finance', () => ({ financeApi: fin }));
vi.mock('../../../services/api/vendorAp', () => ({
  cashFlowApi: { ownerDashboard: vi.fn(), forecast: vi.fn() }, vendorApApi: { apAging: vi.fn() },
}));
vi.mock('../../../services/api/cashReconciliation', () => ({ cashReconciliationApi: { summary: vi.fn(), signoff: vi.fn() } }));
vi.mock('../../../services/api/stores', () => ({ storeApi: { getStores: vi.fn(async () => ({ stores: [] })) } }));
vi.mock('../../../hooks/usePOSQueries', () => ({ useStores: () => ({ data: [] }) }));

import { ToastProvider } from '../../../context/ToastContext';
import { ApiError } from '../../../services/api/client';
import FinanceDashboard from '../FinanceDashboard';

const NOT_RUN = 'Payroll for 11/2026 has not been run for 2 employee(s) (shop: BV-DHN-01).';
const notRun = () => new ApiError(NOT_RUN, { status: 409, code: 'PAYROLL_NOT_RUN' });
// restoreMocks is on, so the spy is made afresh for each test.
let confirm: MockInstance<typeof window.confirm>;

beforeEach(() => {
  for (const f of Object.values(fin)) f.mockReset().mockResolvedValue({});
  confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-11-12T09:00:00Z'));
});
afterEach(() => vi.useRealTimers());

async function pressLock() {
  render(<ToastProvider><FinanceDashboard /></ToastProvider>);
  fireEvent.click(await screen.findByText('Period Management'));
  fireEvent.click(await screen.findByText('Lock Period'));
  await waitFor(() => expect(fin.lockPeriod).toHaveBeenCalled());
}

it('asks before locking a month whose payroll was not run, and locks only on OK', async () => {
  fin.lockPeriod.mockRejectedValueOnce(notRun());
  confirm.mockReturnValue(true);
  await pressLock();
  await waitFor(() => expect(fin.lockPeriod).toHaveBeenCalledTimes(2));
  expect(confirm.mock.calls[0][0]).toContain(NOT_RUN);
  expect(fin.lockPeriod.mock.calls).toEqual([[11, 2026, false], [11, 2026, true]]);
  expect(await screen.findByText('Locked')).toBeTruthy();
});

it('does not lock when the admin says no, and shows why', async () => {
  fin.lockPeriod.mockRejectedValueOnce(notRun());
  confirm.mockReturnValue(false);
  await pressLock();
  expect(await screen.findByText(NOT_RUN)).toBeTruthy();
  expect(fin.lockPeriod).toHaveBeenCalledTimes(1);
  expect(screen.getByText('Open')).toBeTruthy();
});

it('a draft payroll refusal is never offered "lock anyway"', async () => {
  const draft = 'Payroll for 11/2026 is still a draft for 1 employee(s) (shop: BV-DHN-01).';
  fin.lockPeriod.mockRejectedValueOnce(new ApiError(draft, { status: 409 }));
  await pressLock();
  expect(await screen.findByText(draft)).toBeTruthy();
  expect(confirm).not.toHaveBeenCalled();
  expect(fin.lockPeriod).toHaveBeenCalledTimes(1);
});
