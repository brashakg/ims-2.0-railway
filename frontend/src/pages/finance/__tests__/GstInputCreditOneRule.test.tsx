// ============================================================================
// IMS 2.0 - input credit, one rule (owner + CA 2026-10-18)
// ============================================================================
// * The Finance GST table is one row per GSTIN, read off its GSTR-3B; a GSTIN
//   that also files for shops outside the login's view shows no cash figure.
// * A services bill on the Cash Flow screen carries the accountant's section
//   17(5) 'credit not claimable' tick, sent as itc_blocked; a goods bill has
//   none. The supplier's services bills can be ticked / unticked later, and a
//   locked month's refusal is shown in the server's words.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }));

vi.mock('../../../services/api/finance', () => ({
  financeApi: {
    getRevenue: vi.fn(), getPnl: vi.fn(), getGstSummary: vi.fn(), getOutstanding: vi.fn(),
    getCashFlow: vi.fn(), getBudget: vi.fn(), getVendorPayments: vi.fn(), getPeriodStatus: vi.fn(),
    getPnlByStore: vi.fn(), getPnlByCategory: vi.fn(), getGstReconciliation: vi.fn(), exportTally: vi.fn(),
  },
}));
const apis = vi.hoisted(() => ({
  cashFlowApi: { ownerDashboard: vi.fn(), forecast: vi.fn() },
  vendorApApi: {
    apAging: vi.fn(), ledger: vi.fn(), listBills: vi.fn(), createBill: vi.fn(), setItcBlocked: vi.fn(),
    createPayment: vi.fn(), createDebitNote: vi.fn(), listReceipts: vi.fn(), listPayments: vi.fn(),
  },
}));
vi.mock('../../../services/api/vendorAp', () => apis);

const roles = ['ACCOUNTANT'];
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-acct', user_id: 'u-acct', roles, activeRole: 'ACCOUNTANT', activeStoreId: 'BV-DHN-01', storeIds: ['BV-DHN-01'] },
    isAuthenticated: true, isLoading: false,
    hasRole: (want: string | string[]) => (Array.isArray(want) ? want : [want]).some((r) => roles.includes(r)),
    hasPermission: () => true, hasModuleAccess: () => true,
  }),
}));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../../hooks/usePOSQueries', () => ({ useStores: () => ({ data: [{ store_id: 'BV-DHN-01', store_name: 'Dhanbad' }] }) }));

import FinanceDashboard from '../FinanceDashboard';
import CashFlowPage from '../CashFlowPage';
import { financeApi } from '../../../services/api/finance';

const fin = financeApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const DASH = {
  as_of: '2026-10-01', store_id: null, receivables: { total: 0, buckets: {}, overdue: 0 },
  payables: { total: 0, buckets: {}, overdue: 0, due_7d: 0, due_30d: 0, unallocated_credits: 0 },
  net_position: 0, this_month: { revenue: 0, expenses: 0, vendor_payments: 0, net_cash_flow: 0 }, alerts: [],
};
const FORECAST = {
  opening_cash: 0, as_of: '2026-10-01', horizon_days: 90, weeks: [],
  totals: { inflow: 0, outflow: 0, net: 0, closing_balance: 0 }, beyond_horizon: { inflow: 0, outflow: 0 },
  lowest: { week_index: 0, week_start: '', balance: 0 }, assumptions: {},
};
const AGING = {
  as_of: '2026-10-01', totals: { buckets: {}, total_outstanding: 0, unallocated_credits: 0, net_payable: 0 },
  vendors: [{ vendor_id: 'V-HOTEL', vendor_name: 'Hotel', buckets: {}, total_outstanding: 1180, net_payable: 1180 }],
};
const LEDGER = {
  vendor_id: 'V-HOTEL', vendor: null,
  ledger: { entries: [], closing_balance: 1180, total_billed: 1180, total_paid: 0, total_tds: 0, total_debit_notes: 0 },
  aging: { as_of: '2026-10-01', buckets: {}, total_outstanding: 1180, unallocated_credits: 0, net_payable: 1180 },
};
const FOOD = { bill_id: 'B-FOOD', vendor_id: 'V-HOTEL', bill_number: 'FOOD-1', bill_date: '2026-09-20',
  taxable_amount: 1000, tax_amount: 180, total_amount: 1180, bill_kind: 'SERVICES', itc_blocked: false };

beforeEach(() => {
  vi.clearAllMocks();
  fin.getRevenue.mockResolvedValue({ total_revenue: 0 });
  fin.getPnl.mockResolvedValue({ revenue: 0, expenses: {} });
  fin.getGstSummary.mockResolvedValue({ cgst: 0, sgst: 0, gst_input_credit: 0, net_gst_payable: 0 });
  fin.getOutstanding.mockResolvedValue([]);
  fin.getCashFlow.mockResolvedValue({});
  fin.getBudget.mockResolvedValue({ categories: {} });
  fin.getVendorPayments.mockResolvedValue([]);
  fin.getPeriodStatus.mockResolvedValue({ locked: false });
  fin.getPnlByStore.mockResolvedValue({ stores: [] });
  fin.getPnlByCategory.mockResolvedValue({ categories: [] });
  apis.cashFlowApi.ownerDashboard.mockResolvedValue(DASH);
  apis.cashFlowApi.forecast.mockResolvedValue(FORECAST);
  apis.vendorApApi.apAging.mockResolvedValue(AGING);
  apis.vendorApApi.ledger.mockResolvedValue(LEDGER);
  apis.vendorApApi.listBills.mockResolvedValue({ bills: [FOOD], total: 1 });
  apis.vendorApApi.listReceipts.mockResolvedValue([]);
  apis.vendorApApi.listPayments.mockResolvedValue({ payments: [] });
  apis.vendorApApi.createBill.mockResolvedValue({ bill_id: 'B-NEW', store_id: 'BV-DHN-01' });
});

describe('the Finance GST table is one row per GSTIN', () => {
  it('shows each registration, and says inline why a shared GSTIN has no input-side figure', async () => {
    fin.getGstReconciliation.mockResolvedValue({ registrations: [
      { entity_name: 'Better Vision', gstin: '20AABCB1234C1ZS', gst_collected: 1000, input_credit: 300, reverse_charge: 0, net_payable: 700 },
      { entity_name: 'Better Vision', gstin: '27AABCB1234C1ZQ', gst_collected: 500, input_credit: null, reverse_charge: null, net_payable: null, shops_outside_view: 1 },
    ] });
    render(<FinanceDashboard />);
    await waitFor(() => expect(fin.getGstReconciliation).toHaveBeenCalled());
    await userEvent.setup().click(screen.getByRole('button', { name: /gst management/i }));
    const jh = (await screen.findByText('20AABCB1234C1ZS')).closest('tr') as HTMLElement;
    expect(within(jh).getByText('₹700')).toBeInTheDocument();
    const mh = screen.getByText('27AABCB1234C1ZQ').closest('tr') as HTMLElement;
    expect(within(mh).getByTestId('gst-recon-withheld')).toHaveTextContent(/also covers 1 other shop\(s\) not in this view/);
    expect(within(mh).queryByText('₹0')).toBeNull();
  });

  it('a refused table says so in its place, never an empty table', async () => {
    fin.getGstReconciliation.mockRejectedValue(new Error('The GST figures could not be read for every shop; please retry.'));
    render(<FinanceDashboard />);
    await waitFor(() => expect(fin.getGstReconciliation).toHaveBeenCalled());
    await userEvent.setup().click(screen.getByRole('button', { name: /gst management/i }));
    expect(await screen.findByTestId('gst-recon-error')).toHaveTextContent('could not be read for every shop');
  });

  it('the cards say why input credit and net payable are not shown, never a 0', async () => {
    fin.getGstReconciliation.mockResolvedValue({ registrations: [] });
    fin.getGstSummary.mockResolvedValue({ cgst: 450, sgst: 450, igst: 0, gst_collected: 900,
      gst_input_credit: null, net_gst_payable: null, reverse_charge_tax: null, gst_input_credit_carried_forward: null });
    render(<FinanceDashboard />);
    await waitFor(() => expect(fin.getGstSummary).toHaveBeenCalled());
    await userEvent.setup().click(screen.getByRole('button', { name: /gst management/i }));
    expect(await screen.findByTestId('gst-input-withheld')).toBeInTheDocument();
    expect(screen.queryByText('Net GST Payable')).toBeNull();
    expect(screen.queryByText('Less: Input Tax Credit')).toBeNull();
  });
});

async function openSupplier() {
  render(<CashFlowPage />);
  fireEvent.click(await screen.findByRole('button', { name: 'AP Aging' }));
  fireEvent.click(await screen.findByText('Hotel'));
  await waitFor(() => expect(apis.vendorApApi.ledger).toHaveBeenCalledWith('V-HOTEL'));
}

describe('section 17(5) on the Cash Flow bill', () => {
  it('a services bill carries the tick and sends it; a goods bill has none', async () => {
    await openSupplier();
    fireEvent.click(await screen.findByRole('button', { name: 'Bill' }));
    const kind = await screen.findByDisplayValue('This bill is for…');
    fireEvent.change(kind, { target: { value: 'GOODS' } });
    expect(screen.queryByTestId('bill-itc-blocked')).toBeNull();
    fireEvent.change(kind, { target: { value: 'SERVICES' } });
    fireEvent.change(screen.getByPlaceholderText('Bill / invoice no'), { target: { value: 'FOOD-2' } });
    fireEvent.change(screen.getByPlaceholderText('Taxable amount'), { target: { value: '1000' } });
    fireEvent.change(screen.getByPlaceholderText('Tax (GST)'), { target: { value: '180' } });
    const tick = screen.getByTestId('bill-itc-blocked') as HTMLInputElement;
    expect(tick.checked).toBe(false);
    fireEvent.click(tick);
    fireEvent.click(screen.getByRole('button', { name: /Save/ }));
    await waitFor(() => expect(apis.vendorApApi.createBill).toHaveBeenCalled());
    expect(apis.vendorApApi.createBill.mock.calls[0][1]).toMatchObject({ bill_kind: 'SERVICES', itc_blocked: true });
  });

  it('a booked services bill is ticked later; a locked month is refused in words', async () => {
    apis.vendorApApi.setItcBlocked.mockResolvedValueOnce({ ...FOOD, itc_blocked: true, itc_eligible: false });
    await openSupplier();
    const box = await screen.findByLabelText('Credit not claimable on bill FOOD-1') as HTMLInputElement;
    fireEvent.click(box);
    await waitFor(() => expect(apis.vendorApApi.setItcBlocked).toHaveBeenCalledWith('V-HOTEL', 'B-FOOD', true));
    await waitFor(() => expect(box.checked).toBe(true));
    const locked = 'Accounting period 09/2026 is locked; cannot post to a closed month.';
    apis.vendorApApi.setItcBlocked.mockRejectedValueOnce({ response: { data: { detail: locked } } });
    fireEvent.click(box);
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith(locked));
    expect(box.checked).toBe(true);
  });
});
