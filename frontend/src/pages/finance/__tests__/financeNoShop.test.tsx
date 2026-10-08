// ============================================================================
// IMS 2.0 - Finance screens and a login with no shop (owner ruling R3)
// ============================================================================
// The server refuses a non-admin login with no shop (resolve_store_scope, 403
// 'Your login has no shop assigned ...'). Three screens met that refusal badly:
//   * Finance > Cash Flow tab showed the refused read as a month of Rs 0 (and,
//     before the server rule reached /finance/cash-flow, every shop's supplier
//     payments).
//   * Cash Flow & Payables and Cash Reconciliation reloaded without end: each
//     error toast handed every useToast() reader a new object, so a load
//     callback that lists `toast` in its deps ran again, toasted again ... --
//     hundreds of requests a second. The toast functions are now one stable
//     value (ToastContext), so a refusal is ONE request and ONE toast.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';

const NO_SHOP = 'Your login has no shop assigned - ask an admin to assign one.';
const REFUSED = { response: { status: 403, data: { detail: NO_SHOP } } };

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

describe('Finance > Cash Flow tab, a login with no shop', () => {
  it('reads the message, not a month of Rs 0 outflows', async () => {
    const user = userEvent.setup();
    withToasts(<FinanceDashboard />);
    await user.click(await screen.findByRole('button', { name: /cash flow/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(NO_SHOP);
    expect(screen.queryByText('Total Cash Outflows')).toBeNull();
  });

  it('an admin with no shop still gets the cash flow panel', async () => {
    auth.user = { id: 'u-a', roles: ['ADMIN'], activeRole: 'ADMIN', activeStoreId: '' };
    fin.getCashFlow.mockResolvedValue({ inflows: 0, outflows: 0, net_cash_flow: 0 });
    const user = userEvent.setup();
    withToasts(<FinanceDashboard />);
    await user.click(await screen.findByRole('button', { name: /cash flow/i }));
    expect(await screen.findByText('Total Cash Outflows')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

describe('Cash Flow & Payables, a login with no shop', () => {
  it('reads the message and sends none of its three reads', async () => {
    withToasts(<CashFlowPage />);
    expect(await screen.findByRole('alert')).toHaveTextContent(NO_SHOP);
    await settle();
    expect(ap.cashFlowApi.ownerDashboard).not.toHaveBeenCalled();
    expect(ap.cashFlowApi.forecast).not.toHaveBeenCalled();
    expect(ap.vendorApApi.apAging).not.toHaveBeenCalled();
  });

  it('a refused load (any reason) is asked once and toasted once, never re-run by its toast', async () => {
    auth.user = { id: 'u-p', roles: ['ACCOUNTANT'], activeRole: 'ACCOUNTANT', activeStoreId: 'WO-PUN-01' };
    withToasts(<CashFlowPage />);
    await screen.findByText(NO_SHOP);
    await settle();
    expect(ap.cashFlowApi.ownerDashboard).toHaveBeenCalledTimes(1);
    expect(screen.getAllByText(NO_SHOP)).toHaveLength(1);
    expect(screen.queryByText('Loading...')).toBeNull();
  });
});

describe('Cash Reconciliation, a store manager with no shop', () => {
  it('the refusal is one request and one toast, not a reload loop', async () => {
    auth.user = { id: 'u-sm', roles: ['STORE_MANAGER'], activeRole: 'STORE_MANAGER', activeStoreId: '' };
    withToasts(<CashReconciliationPage />);
    await screen.findByText(NO_SHOP);
    await settle();
    expect(recon.summary).toHaveBeenCalledTimes(1);
    expect(screen.getAllByText(NO_SHOP)).toHaveLength(1);
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
