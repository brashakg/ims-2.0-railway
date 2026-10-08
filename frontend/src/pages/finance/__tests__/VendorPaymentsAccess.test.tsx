// ============================================================================
// IMS 2.0 - per-vendor payables are an accounts read (F60, 2026-09-28)
// ============================================================================
// GET /finance/vendor-payments now answers ADMIN / ACCOUNTANT only - the same
// answer as the vendor ledger and /vendors/ap-aging. The Finance dashboard is
// also open to STORE_MANAGER / AREA_MANAGER, so for them it must not call the
// endpoint (a 403 swallowed into an empty list reads as "we owe nobody") and
// must not show the Vendor Payments tab or the payment schedule.
// Both directions: an accounts reader still gets all of it.

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
