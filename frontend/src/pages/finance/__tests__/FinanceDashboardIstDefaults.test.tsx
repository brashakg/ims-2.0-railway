// FE-1: the Finance dashboard opens on the IST financial year to date, and the
// period it checks / locks is the IST month of `to`. The box clock is UTC in CI;
// 20:00Z is 01:30 IST the next day (the night window).
import { it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-a', roles: ['ADMIN'], activeRole: 'ADMIN', activeStoreId: 'BV-DHN-01' } }),
}));
const fin = vi.hoisted(() => ({
  getRevenue: vi.fn(), getPnl: vi.fn(), getGstSummary: vi.fn(), getOutstanding: vi.fn(), getCashFlow: vi.fn(),
  getBudget: vi.fn(), getVendorPayments: vi.fn(), getPeriodStatus: vi.fn(), getPnlByStore: vi.fn(),
  getPnlByCategory: vi.fn(), getGstReconciliation: vi.fn(),
}));
vi.mock('../../../services/api/finance', () => ({ financeApi: fin }));
vi.mock('../../../services/api/vendorAp', () => ({
  cashFlowApi: { ownerDashboard: vi.fn(), forecast: vi.fn() }, vendorApApi: { apAging: vi.fn() },
}));
vi.mock('../../../services/api/cashReconciliation', () => ({ cashReconciliationApi: { summary: vi.fn(), signoff: vi.fn() } }));
vi.mock('../../../services/api/stores', () => ({ storeApi: { getStores: vi.fn(async () => ({ stores: [] })) } }));
vi.mock('../../../hooks/usePOSQueries', () => ({ useStores: () => ({ data: [] }) }));

import { ToastProvider } from '../../../context/ToastContext';
import FinanceDashboard from '../FinanceDashboard';

beforeEach(() => {
  for (const f of Object.values(fin)) f.mockResolvedValue({});
});
afterEach(() => vi.useRealTimers());

it.each([
  // [clock, from, to, period month, period year]
  ['2026-12-31T20:00:00Z', '2026-04-01', '2027-01-01', 1, 2027], // night: IST 1 Jan
  ['2026-12-31T09:00:00Z', '2026-04-01', '2026-12-31', 12, 2026], // day control
  ['2027-03-31T20:00:00Z', '2027-04-01', '2027-04-01', 4, 2027], // night: IST 1 Apr, new FY
  ['2027-03-31T09:00:00Z', '2026-04-01', '2027-03-31', 3, 2027], // day control, Jan-Mar is last FY
] as const)('at %s the dashboard reads %s..%s and period %i/%i', async (at, from, to, month, year) => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date(at));
  render(<ToastProvider><FinanceDashboard /></ToastProvider>);
  await waitFor(() => expect(fin.getPnl).toHaveBeenCalled());
  expect(fin.getPnl.mock.calls[0][0]).toMatchObject({ from_date: from, to_date: to });
  await waitFor(() => expect(fin.getPeriodStatus).toHaveBeenCalled());
  expect(fin.getPeriodStatus.mock.calls[0]).toEqual([month, year]);
});
