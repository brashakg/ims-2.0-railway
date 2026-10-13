// ============================================================================
// IMS 2.0 - Finance screens and a login with no shop (owner ruling R4)
// ============================================================================
// Owner ruling 2026-10-12 (R4): a non-admin login with no shop sees NO shop --
// the server scopes it to no shop (empty reads; naming a shop is a 403 worded
// NO_SHOP_MESSAGE). The screens say why through the ONE no-shop rule
// (components/common/NoShopNotice):
//   * the Finance dashboard shows NoShopNotice in place of its panels and
//     sends none of its reads (budgets and the period lock are not shop-scoped,
//     so even they would hand it organisation data);
//   * Cash Flow & Payables is gated at its route (routes/__tests__/
//     financeCashFlowNoShop.test.tsx).
// And a refused load reloaded without end on Cash Flow & Payables and Cash
// Reconciliation: each error toast handed every useToast() reader a new
// object, so a load callback that lists `toast` in its deps ran again, toasted
// again ... -- hundreds of requests a second. The toast functions are now one
// stable value (ToastContext), so a refusal is ONE request and ONE toast.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';

// Any refusal the server words: these suites are about how a screen meets it.
const REFUSAL = 'No access to store BV-DHN-01';
const REFUSED = { response: { status: 403, data: { detail: REFUSAL } } };

const auth = vi.hoisted(() => ({
  user: { id: 'u-1', roles: ['ACCOUNTANT'], activeRole: 'ACCOUNTANT', activeStoreId: '' } as {
    id: string;
    roles: string[];
    activeRole: string;
    activeStoreId: string;
  },
}));
vi.mock('../../../context/AuthContext', () => ({ useAuth: () => ({ user: auth.user }) }));

const fin = vi.hoisted(() => ({
  getRevenue: vi.fn(),
  getPnl: vi.fn(),
  getGstSummary: vi.fn(),
  getOutstanding: vi.fn(),
  getCashFlow: vi.fn(),
  getBudget: vi.fn(),
  getVendorPayments: vi.fn(),
  getPeriodStatus: vi.fn(),
  getPnlByStore: vi.fn(),
  getPnlByCategory: vi.fn(),
  getGstReconciliation: vi.fn(),
}));
vi.mock('../../../services/api/finance', () => ({ financeApi: fin }));

const ap = vi.hoisted(() => ({
  cashFlowApi: { ownerDashboard: vi.fn(), forecast: vi.fn() },
  vendorApApi: { apAging: vi.fn() },
}));
vi.mock('../../../services/api/vendorAp', () => ap);

const recon = vi.hoisted(() => ({ summary: vi.fn(), signoff: vi.fn() }));
vi.mock('../../../services/api/cashReconciliation', () => ({ cashReconciliationApi: recon }));
vi.mock('../../../services/api/stores', () => ({ storeApi: { getStores: vi.fn(async () => ({ stores: [] })) } }));
vi.mock('../../../hooks/usePOSQueries', () => ({ useStores: () => ({ data: [] }) }));

import { ToastProvider } from '../../../context/ToastContext';
import { NO_SHOP_MESSAGE } from '../../../components/common/NoShopNotice';
import FinanceDashboard from '../FinanceDashboard';
import CashFlowPage from '../CashFlowPage';
import CashReconciliationPage from '../CashReconciliationPage';

/** The REAL toast provider: a mocked useToast hands back a fresh object per
 *  render and would hide (or fake) the loop this file is about. */
const withToasts = (ui: ReactNode) => render(<ToastProvider>{ui}</ToastProvider>);

/** Let any re-run loop spin: a few hundred ms is thousands of renders. */
const settle = () => new Promise((r) => setTimeout(r, 300));

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = { id: 'u-1', roles: ['ACCOUNTANT'], activeRole: 'ACCOUNTANT', activeStoreId: '' };
  for (const f of Object.values(fin)) f.mockRejectedValue(REFUSED);
  fin.getBudget.mockResolvedValue({ categories: {} });
  ap.cashFlowApi.ownerDashboard.mockRejectedValue(REFUSED);
  ap.cashFlowApi.forecast.mockRejectedValue(REFUSED);
  ap.vendorApApi.apAging.mockRejectedValue(REFUSED);
  recon.summary.mockRejectedValue(REFUSED);
});

describe('Finance dashboard, a login with no shop', () => {
  it('reads the message in place of the panels, and sends none of its reads', async () => {
    withToasts(<FinanceDashboard />);
    expect(await screen.findByText(NO_SHOP_MESSAGE)).toBeInTheDocument();
    await settle();
    for (const f of Object.values(fin)) expect(f).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /cash flow/i })).toBeNull();
    expect(screen.queryByText('Total Cash Outflows')).toBeNull();
  });

  it('an admin with no shop still gets the cash flow panel', async () => {
    auth.user = { id: 'u-a', roles: ['ADMIN'], activeRole: 'ADMIN', activeStoreId: '' };
    fin.getCashFlow.mockResolvedValue({ inflows: 0, outflows: 0, net_cash_flow: 0 });
    const user = userEvent.setup();
    withToasts(<FinanceDashboard />);
    await user.click(await screen.findByRole('button', { name: /cash flow/i }));
    expect(await screen.findByText('Total Cash Outflows')).toBeInTheDocument();
    expect(screen.queryByText(NO_SHOP_MESSAGE)).toBeNull();
  });
});

describe('Cash Flow & Payables, a refused load', () => {
  it('is asked once and toasted once, never re-run by its toast', async () => {
    auth.user = { id: 'u-p', roles: ['ACCOUNTANT'], activeRole: 'ACCOUNTANT', activeStoreId: 'WO-PUN-01' };
    withToasts(<CashFlowPage />);
    await screen.findByText(REFUSAL);
    await settle();
    expect(ap.cashFlowApi.ownerDashboard).toHaveBeenCalledTimes(1);
    expect(screen.getAllByText(REFUSAL)).toHaveLength(1);
    expect(screen.queryByText('Loading...')).toBeNull();
  });
});

describe('Cash Reconciliation, a refused load', () => {
  it('the refusal is one request and one toast, not a reload loop', async () => {
    auth.user = { id: 'u-sm', roles: ['STORE_MANAGER'], activeRole: 'STORE_MANAGER', activeStoreId: '' };
    withToasts(<CashReconciliationPage />);
    await screen.findByText(REFUSAL);
    await settle();
    expect(recon.summary).toHaveBeenCalledTimes(1);
    expect(screen.getAllByText(REFUSAL)).toHaveLength(1);
  });
});

describe('the toast functions are one value', () => {
  it('a toast does not hand its readers a new object', async () => {
    const { useToast } = await import('../../../context/ToastContext');
    const seen = new Set<unknown>();
    function Reader() {
      const toast = useToast();
      seen.add(toast);
      return <button onClick={() => toast.error('boom')}>raise</button>;
    }
    withToasts(<Reader />);
    await userEvent.setup().click(screen.getByRole('button', { name: 'raise' }));
    await waitFor(() => expect(screen.getByText('boom')).toBeInTheDocument());
    expect(seen.size).toBe(1);
  });
});
