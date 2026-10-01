// ============================================================================
// IMS 2.0 - Finance dashboard: supplier figures follow the dashboard's shop,
// and one status rule reads a supplier's balance (review r1 #12 #37 #38)
// ============================================================================
// Every other Finance panel reads the shop picked at the top of the screen
// (?store_id=activeStoreId). The supplier figures -- the Vendor Payments tab
// and the Outstanding tab's payment schedule -- used to ask for every shop
// with nothing on screen saying so, next to one shop's receivables.
//
// The balance is the supplier ledger's closing balance: below zero we paid
// ahead (an advance), zero is settled. Outstanding used to badge both
// 'Partial', the Vendor Payments tab 'Paid', and 'Vendors with Dues' counted
// every supplier on the list.
//
// Drives the REAL FinanceDashboard with the api modules mocked.

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

vi.mock('../../../services/api/stores', () => ({
  storeApi: { getStores: vi.fn() },
}));

let roles: string[] = ['ADMIN'];
let activeStoreId = 'BV-DHN-01';
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-1', roles, activeStoreId } }),
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));

import FinanceDashboard from '../FinanceDashboard';
import { financeApi } from '../../../services/api/finance';
import { storeApi } from '../../../services/api/stores';

const api = financeApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const getStores = storeApi.getStores as unknown as ReturnType<typeof vi.fn>;

// /finance/vendor-payments rows (the ledger's closing balance per supplier).
const SUPPLIERS = [
  { vendor_id: 'V-ESS', vendor_name: 'Essilor', balance: 4999, total_paid: 0 },
  { vendor_id: 'V-HOYA', vendor_name: 'Hoya Lens', balance: 2000, total_paid: 500 },
  { vendor_id: 'V-PUN', vendor_name: 'Pune Frames', balance: -1500.5, total_paid: 9000 },
  { vendor_id: 'V-DOR', vendor_name: 'Dormant Co', balance: 0, total_paid: 0 },
];

function primeApi() {
  api.getRevenue.mockResolvedValue({ total_revenue: 0 });
  api.getPnl.mockResolvedValue({ revenue: 0, expenses: {} });
  api.getGstSummary.mockResolvedValue({});
  api.getOutstanding.mockResolvedValue([]);
  api.getCashFlow.mockResolvedValue({});
  api.getBudget.mockResolvedValue({ categories: {} });
  api.getVendorPayments.mockResolvedValue(SUPPLIERS);
  api.getPeriodStatus.mockResolvedValue({ locked: false });
  api.getPnlByStore.mockResolvedValue({ stores: [] });
  api.getPnlByCategory.mockResolvedValue({ categories: [] });
  api.getGstReconciliation.mockResolvedValue({ entities: [] });
  getStores.mockResolvedValue({
    stores: [
      { store_id: 'BV-DHN-01', store_name: 'Better Vision Dhanbad' },
      { store_id: 'WO-PUN-01', store_name: 'WizOpt Pune' },
    ],
  });
}

async function openTab(name: RegExp) {
  const user = userEvent.setup();
  render(<FinanceDashboard />);
  await waitFor(() => expect(api.getPnl).toHaveBeenCalled());
  await user.click(await screen.findByRole('button', { name }));
}

/** The status badge on a supplier's row. */
function statusOf(vendor: string): string {
  const row = screen.getByText(vendor).closest('tr') as HTMLElement;
  const cells = within(row).getAllByRole('cell');
  return cells.map((c) => c.textContent || '').join('|');
}

describe('Finance dashboard - supplier figures follow the dashboard shop', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    roles = ['ADMIN'];
    activeStoreId = 'BV-DHN-01';
    primeApi();
  });

  it.each([['ADMIN'], ['ACCOUNTANT']])(
    '%s: asks for the shop every other panel reads, and says which shop it is',
    async (role) => {
      roles = [role];
      await openTab(/outstanding/i);
      expect(api.getVendorPayments).toHaveBeenCalledWith('BV-DHN-01');
      expect(api.getOutstanding).toHaveBeenCalledWith({ store_id: 'BV-DHN-01' });
      const head = (await screen.findByText(/vendor payment schedule/i)).parentElement as HTMLElement;
      expect(await within(head).findByText('Better Vision Dhanbad')).toBeInTheDocument();
      expect(within(head).getByText(/^Shop:/)).toBeInTheDocument();
    },
  );

  it('Vendor Payments tab names the same shop', async () => {
    await openTab(/vendor payments/i);
    expect(api.getVendorPayments).toHaveBeenCalledWith('BV-DHN-01');
    expect(await screen.findByText('Better Vision Dhanbad')).toBeInTheDocument();
  });

  it('no shop picked: every shop is asked for and labelled as such', async () => {
    activeStoreId = '';
    await openTab(/vendor payments/i);
    // No shop sent (getVendorPayments drops an empty id): every shop.
    expect(api.getVendorPayments).toHaveBeenCalled();
    expect(api.getVendorPayments.mock.calls.every(([sid]) => !sid)).toBe(true);
    expect(await screen.findByText('All shops')).toBeInTheDocument();
    expect(screen.queryByText(/^Shop:/)).toBeNull();
  });

  it('a shop missing from the store list is named by its id', async () => {
    activeStoreId = 'BV-NEW-09';
    await openTab(/vendor payments/i);
    await waitFor(() => expect(getStores).toHaveBeenCalled());
    expect(await screen.findByText('BV-NEW-09')).toBeInTheDocument();
  });
});

describe('Finance dashboard - one status rule for a supplier balance', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    roles = ['ADMIN'];
    activeStoreId = 'BV-DHN-01';
    primeApi();
  });

  it('Outstanding: advance, settled, part-paid and unpaid read as such', async () => {
    await openTab(/outstanding/i);
    await screen.findByText('Pune Frames');
    expect(statusOf('Pune Frames')).toMatch(/\|Advance$/);
    expect(statusOf('Dormant Co')).toMatch(/\|Settled$/);
    expect(statusOf('Hoya Lens')).toMatch(/\|Partial$/);
    expect(statusOf('Essilor')).toMatch(/\|Pending$/);
  });

  it('Vendor Payments: the same words, and only suppliers we owe have dues', async () => {
    await openTab(/vendor payments/i);
    await screen.findByText('Pune Frames');
    expect(statusOf('Pune Frames')).toMatch(/\|Advance\|/);
    expect(statusOf('Dormant Co')).toMatch(/\|Settled\|/);
    expect(statusOf('Hoya Lens')).toMatch(/\|Partial\|/);
    expect(statusOf('Essilor')).toMatch(/\|Pending\|/);
    const dues = screen.getByText('Vendors with Dues').parentElement as HTMLElement;
    expect(within(dues).getByText('2')).toBeInTheDocument();
  });
});
