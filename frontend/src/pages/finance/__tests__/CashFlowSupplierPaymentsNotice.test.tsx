// ============================================================================
// IMS 2.0 - Cash Flow says so when supplier payments are left out
// ============================================================================
// Owner ruling 2026-10-01: supplier money is for ADMIN / SUPERADMIN /
// ACCOUNTANT only. GET /finance/cash-flow's org view therefore leaves the
// supplier payments out of "Total outflows" for anyone else, and says so with
// a flag (`vendor_payments_restricted`) -- never a figure. The dashboard used
// to keep only the four numbers, so a manager read a short outflow total as
// the whole truth. The flag must raise the same "this total leaves something
// out" notice the pay-head strip raises -- and only when it is set.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../../services/api/finance', () => ({
  financeApi: {
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
    exportTally: vi.fn(),
  },
}));

vi.mock('../../../context/AuthContext', () => ({
  // A store manager with no active shop: the org view of /cash-flow.
  useAuth: () => ({ user: { user_id: 'u-sm', roles: ['STORE_MANAGER'] } }),
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));

import FinanceDashboard from '../FinanceDashboard';
import { financeApi } from '../../../services/api/finance';

const api = financeApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const NOTICE = 'restricted-totals-notice';

const CASHFLOW = { period: 'This month', inflows: 300000, outflows: 24450, net_cash_flow: 275550 };

function primeApi(cashFlow: unknown) {
  api.getRevenue.mockResolvedValue({ total_revenue: 300000 });
  api.getPnl.mockResolvedValue({ revenue: 300000, total_expenses: 24450, expenses: {} });
  api.getGstSummary.mockResolvedValue({});
  api.getOutstanding.mockResolvedValue([]);
  api.getCashFlow.mockResolvedValue(cashFlow);
  api.getBudget.mockResolvedValue({ categories: {} });
  api.getVendorPayments.mockResolvedValue([]);
  api.getPeriodStatus.mockResolvedValue({ locked: false });
  api.getPnlByStore.mockResolvedValue({ stores: [] });
  api.getPnlByCategory.mockResolvedValue({ categories: [] });
  api.getGstReconciliation.mockResolvedValue({ entities: [] });
}

async function openCashFlowTab() {
  const user = userEvent.setup();
  render(<FinanceDashboard />);
  await waitFor(() => expect(api.getCashFlow).toHaveBeenCalled());
  await user.click(await screen.findByRole('button', { name: /cash flow/i }));
}

describe('FinanceDashboard cash flow - supplier payments left out are declared', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the notice when the server left supplier payments out of the outflows', async () => {
    primeApi({ ...CASHFLOW, vendor_payments_restricted: true });
    await openCashFlowTab();
    const notice = await screen.findByTestId(NOTICE);
    expect(notice.textContent).toMatch(/cash outflow figures/i);
    // A flag, never a figure: nothing about suppliers or amounts.
    expect(notice.textContent?.toLowerCase()).not.toMatch(/supplier|vendor|\d/);
  });

  it('shows NOTHING when the outflow total is whole', async () => {
    primeApi(CASHFLOW);
    await openCashFlowTab();
    // Positive control: the panel rendered, so "no notice" is a verdict.
    expect(await screen.findAllByText(/outflow/i)).not.toHaveLength(0);
    expect(screen.queryByTestId(NOTICE)).not.toBeInTheDocument();
  });

  it('never asks a store manager for per-supplier payables', async () => {
    primeApi({ ...CASHFLOW, vendor_payments_restricted: true });
    await openCashFlowTab();
    expect(api.getVendorPayments).not.toHaveBeenCalled();
  });
});
