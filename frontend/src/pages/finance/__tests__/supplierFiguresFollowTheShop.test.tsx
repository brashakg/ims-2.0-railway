// ============================================================================
// IMS 2.0 - Finance dashboard: supplier figures follow the dashboard's shop,
// and one status rule reads a supplier's balance (review r1 #12 #37 #38)
// ============================================================================
// Every Finance panel reads ONE shop scope -- the Purchase screens' (usePurchase
// Shop): an admin reads EVERY shop until he picks one in the header's Shop
// picker (owner ruling 2026-09-28 'admins see all stores'; round 4 P3: his
// topbar HQ shop used to narrow Vendor Payments to 'Shop: HQ, Rs 0 owed'),
// anyone else his own shop. The supplier figures say which.
//
// The balance is the supplier ledger's closing balance: below zero we paid
// ahead (an advance), zero is settled. Outstanding used to badge both
// 'Partial', the Vendor Payments tab 'Paid', and 'Vendors with Dues' counted
// every supplier on the list.
//
// Drives the REAL FinanceDashboard with the api modules mocked.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, renderHook, screen, waitFor, within } from '@testing-library/react';
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

// The one shop-name rule (purchaseShop.PurchaseShopName) reads the store list.
vi.mock('../../../hooks/usePOSQueries', () => ({
  useStores: () => ({
    data: [
      { store_id: 'BV-DHN-01', store_name: 'Better Vision Dhanbad' },
      { store_id: 'WO-PUN-01', store_name: 'WizOpt Pune' },
    ],
  }),
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
import { NO_SHOP_MESSAGE } from '../../../components/common/NoShopNotice';
import { usePurchaseShop } from '../../purchase/purchaseShop';

const api = financeApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

/** Clears the admin's shop pick (the shared Purchase / Finance filter). */
function clearPick() {
  const { result } = renderHook(() => usePurchaseShop());
  act(() => result.current.setShop(''));
}

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
    clearPick();
    roles = ['ADMIN'];
    activeStoreId = 'BV-HQ-01';
    primeApi();
  });

  // Round 4 P3: an admin whose topbar shop is HQ read 'Shop: HQ' and Rs 0
  // owed; the every-shop figure was nowhere on the screen.
  it.each([['ADMIN'], ['SUPERADMIN']])(
    '%s: every shop by default, never his topbar shop, and says so',
    async (role) => {
      roles = [role];
      await openTab(/outstanding/i);
      expect(api.getVendorPayments).toHaveBeenCalledWith(undefined);
      expect(api.getOutstanding).toHaveBeenCalledWith({ store_id: undefined });
      expect(api.getRevenue).toHaveBeenCalledWith({ period: 'month', store_id: undefined });
      const head = (await screen.findByText(/vendor payment schedule/i)).parentElement as HTMLElement;
      expect(within(head).getByText('All shops')).toBeInTheDocument();
      expect(screen.getByLabelText('Purchase shop')).toHaveValue('');
    },
  );

  it('an admin narrows every panel to one shop with the Shop picker', async () => {
    await openTab(/vendor payments/i);
    vi.clearAllMocks();
    primeApi();
    await userEvent.setup().selectOptions(screen.getByLabelText('Purchase shop'), 'WO-PUN-01');
    await waitFor(() => expect(api.getVendorPayments).toHaveBeenCalledWith('WO-PUN-01'));
    expect(api.getPnl).toHaveBeenCalledWith(expect.objectContaining({ store_id: 'WO-PUN-01' }));
    expect(await screen.findByText('WizOpt Pune', { selector: 'span' })).toBeInTheDocument();
  });

  it('an accountant reads his own shop, with no picker, and is told which', async () => {
    roles = ['ACCOUNTANT'];
    activeStoreId = 'BV-DHN-01';
    await openTab(/outstanding/i);
    expect(api.getVendorPayments).toHaveBeenCalledWith('BV-DHN-01');
    expect(api.getOutstanding).toHaveBeenCalledWith({ store_id: 'BV-DHN-01' });
    const head = (await screen.findByText(/vendor payment schedule/i)).parentElement as HTMLElement;
    expect(within(head).getByText('Better Vision Dhanbad')).toBeInTheDocument();
    expect(within(head).getByText(/^Shop:/)).toBeInTheDocument();
    expect(screen.queryByLabelText('Purchase shop')).toBeNull();
  });

  // Owner ruling 2026-10-12 (R4): the server gives a non-admin login with no
  // shop NO shop (empty reads). The dashboard used to print 'All shops' over
  // 'Total Payable Rs 0' -- we owe nobody. It now shows the one no-shop
  // notice in place of every panel and asks for no supplier figure at all.
  it('an accountant with no shop reads the message, not an empty all-shops figure', async () => {
    roles = ['ACCOUNTANT'];
    activeStoreId = '';
    render(<FinanceDashboard />);
    expect(await screen.findByText(NO_SHOP_MESSAGE)).toBeInTheDocument();
    expect(api.getVendorPayments).not.toHaveBeenCalled();
    expect(screen.queryByText('All shops')).toBeNull();
    expect(screen.queryByText('Total Payable')).toBeNull();
    expect(screen.queryByText(/vendor payment schedule/i)).toBeNull();
  });

  it('a shop missing from the store list is named by its id', async () => {
    roles = ['ACCOUNTANT'];
    activeStoreId = 'BV-NEW-09';
    await openTab(/outstanding/i);
    const head = (await screen.findByText(/vendor payment schedule/i)).parentElement as HTMLElement;
    expect(await within(head).findByText('BV-NEW-09')).toBeInTheDocument();
  });
});

describe('Finance dashboard - one status rule for a supplier balance', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clearPick();
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
