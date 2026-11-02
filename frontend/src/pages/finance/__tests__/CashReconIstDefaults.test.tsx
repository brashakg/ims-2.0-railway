// FE-6: cash reconciliation opens on the IST month to date.
import { it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-sm', roles: ['STORE_MANAGER'], activeRole: 'STORE_MANAGER', activeStoreId: 'BV-DHN-01' } }),
}));
const recon = vi.hoisted(() => ({ summary: vi.fn(), signoff: vi.fn() }));
vi.mock('../../../services/api/cashReconciliation', () => ({ cashReconciliationApi: recon }));
vi.mock('../../../services/api/stores', () => ({ storeApi: { getStores: vi.fn(async () => ({ stores: [] })) } }));
vi.mock('../../../hooks/usePOSQueries', () => ({ useStores: () => ({ data: [] }) }));

import { ToastProvider } from '../../../context/ToastContext';
import CashReconciliationPage from '../CashReconciliationPage';

beforeEach(() => {
  recon.summary.mockResolvedValue({ rows: [], totals: {} });
});
afterEach(() => vi.useRealTimers());

it.each([
  ['2026-12-31T20:00:00Z', '2027-01-01', '2027-01-01'], // night: IST 1 Jan
  ['2026-12-31T09:00:00Z', '2026-12-01', '2026-12-31'], // day control
  ['2027-03-31T20:00:00Z', '2027-04-01', '2027-04-01'], // night: IST 1 Apr
])('at %s the default range is %s..%s', async (at, from, to) => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date(at));
  render(<ToastProvider><CashReconciliationPage /></ToastProvider>);
  await waitFor(() => expect(recon.summary).toHaveBeenCalled());
  expect(recon.summary.mock.calls[0][0]).toMatchObject({ from, to });
});
