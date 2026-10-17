// ============================================================================
// IMS 2.0 - per-vendor payables are an accounts read (F60, 2026-09-28)
// ============================================================================
// GET /finance/vendor-payments now answers ADMIN / ACCOUNTANT only - the same
// answer as the vendor ledger and /vendors/ap-aging. The Finance dashboard is
// also open to STORE_MANAGER / AREA_MANAGER, so for them it must not call the
// endpoint (a 403 swallowed into an empty list reads as "we owe nobody") and
// must not show the Vendor Payments tab or the payment schedule.
// Both directions: an accounts reader still gets all of it. The same rule
// (owner ruling 2026-10-12) holds the P&L by category's COGS and gross profit.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
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

let roles: string[] = ['STORE_MANAGER'];
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { user_id: 'u-1', roles, activeStoreId: 'ZZ-SOLO' } }),
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));

import FinanceDashboard from '../FinanceDashboard';
import { financeApi } from '../../../services/api/finance';

const api = financeApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

function primeApi() {
  api.getRevenue.mockResolvedValue({ total_revenue: 0 });
  api.getPnl.mockResolvedValue({ revenue: 0, expenses: {} });
  api.getGstSummary.mockResolvedValue({});
  api.getOutstanding.mockResolvedValue([]);
  api.getCashFlow.mockResolvedValue({});
  api.getBudget.mockResolvedValue({ categories: {} });
  api.getVendorPayments.mockResolvedValue([
    { vendor_id: 'V1', vendor_name: 'Acme Optics', balance: 1111.11 },
  ]);
  api.getPeriodStatus.mockResolvedValue({ locked: false });
  api.getPnlByStore.mockResolvedValue({ stores: [] });
  api.getPnlByCategory.mockResolvedValue({ categories: [] });
  api.getGstReconciliation.mockResolvedValue({ entities: [] });
}

async function openOutstanding() {
  const user = userEvent.setup();
  render(<FinanceDashboard />);
  await waitFor(() => expect(api.getPnl).toHaveBeenCalled());
  await user.click(await screen.findByRole('button', { name: /outstanding/i }));
}

describe('FinanceDashboard - vendor payables follow the accounts rule', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    primeApi();
  });

  it.each([['STORE_MANAGER'], ['AREA_MANAGER']])(
    '%s: no payables call, no Vendor Payments tab, no schedule',
    async (role) => {
      roles = [role];
      await openOutstanding();
      expect(api.getVendorPayments).not.toHaveBeenCalled();
      expect(screen.queryByRole('button', { name: /vendor payments/i })).toBeNull();
      expect(screen.queryByText(/vendor payment schedule/i)).toBeNull();
    },
  );

  it('withheld purchase costs do not turn revenue into fabricated gross profit', async () => {
    roles = ['STORE_MANAGER'];
    api.getPnl.mockResolvedValue({ revenue: 9999, expenses: {} });
    render(<FinanceDashboard />);
    await waitFor(() => expect(screen.getByText('Total Revenue').parentElement).toHaveTextContent('9,999'));
    for (const label of screen.getAllByText('Gross Profit')) {
      expect(label.parentElement).toHaveTextContent('—');
      expect(label.parentElement).not.toHaveTextContent('9,999');
    }
    expect(screen.getByText('Cost of Goods Sold').parentElement).toHaveTextContent('—');
  });

  it.each([['ACCOUNTANT'], ['ADMIN'], ['SUPERADMIN']])(
    '%s: still sees the tab and the schedule',
    async (role) => {
      roles = [role];
      await openOutstanding();
      expect(api.getVendorPayments).toHaveBeenCalled();
      expect(screen.getByRole('button', { name: /vendor payments/i })).toBeInTheDocument();
      expect(await screen.findByText(/vendor payment schedule/i)).toBeInTheDocument();
      expect(await screen.findByText('Acme Optics')).toBeInTheDocument();
    },
  );
});

// Owner ruling 2026-10-12: cost of goods and gross profit are worked out from
// what we pay. The default Revenue & P&L tab shows a manager revenue by
// category only - even if a cost figure ever reached the browser.
describe('FinanceDashboard - P&L by category follows the accounts rule', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    primeApi();
    api.getPnlByCategory.mockResolvedValue({
      categories: [{ category: 'FRAME', revenue: 9999, cogs: 4321.77, gross_profit: 5677.23 }],
    });
  });

  it.each([['STORE_MANAGER'], ['AREA_MANAGER']])(
    '%s: revenue by category, no COGS or gross profit',
    async (role) => {
      roles = [role];
      render(<FinanceDashboard />);
      const table = within((await screen.findByText('Revenue by category')).parentElement!);
      expect(table.getByText('₹9,999')).toBeInTheDocument();
      expect(table.queryByText('COGS')).toBeNull();
      expect(table.queryByText('Gross profit')).toBeNull();
      expect(screen.queryByText('₹4,322')).toBeNull();
      expect(screen.queryByText('₹5,677')).toBeNull();
    },
  );

  it.each([['ACCOUNTANT'], ['ADMIN'], ['SUPERADMIN']])(
    '%s: the full P&L by category',
    async (role) => {
      roles = [role];
      render(<FinanceDashboard />);
      const table = within((await screen.findByText('P&L by category')).parentElement!);
      expect(table.getByText('COGS')).toBeInTheDocument();
      expect(table.getByText('Gross profit')).toBeInTheDocument();
      expect(screen.getByText('₹4,322')).toBeInTheDocument();
      expect(screen.getByText('₹5,677')).toBeInTheDocument();
    },
  );
});
