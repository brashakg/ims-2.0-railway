// ============================================================================
// IMS 2.0 - Cash Flow: a supplier payment picks its bill; an advance is
// applied to a bill later (owner 2026-10-18, A12-2)
// ============================================================================
// This drawer is the only screen that records supplier payments. It never
// sent bill_id, so since #1219 (aging counts only explicit allocations) a
// paid bill stayed OUTSTANDING and the rejected-goods hold never ran.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }));
const apis = vi.hoisted(() => ({
  cashFlowApi: { ownerDashboard: vi.fn(), forecast: vi.fn() },
  vendorApApi: {
    apAging: vi.fn(), ledger: vi.fn(), listBills: vi.fn(), createBill: vi.fn(),
    createPayment: vi.fn(), createDebitNote: vi.fn(), listReceipts: vi.fn(),
    listPayments: vi.fn(), applyAdvance: vi.fn(),
  },
}));
const auth = vi.hoisted(() => ({ user: null as null | Record<string, unknown> }));

vi.mock('../../../services/api/vendorAp', () => apis);
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../../context/AuthContext', () => ({ useAuth: () => ({ user: auth.user }) }));
vi.mock('../../../hooks/usePOSQueries', () => ({
  useStores: () => ({ data: [{ store_id: 'WO-PUN-01', store_name: 'WizOpt Pune' }] }),
}));

import CashFlowPage from '../CashFlowPage';

const ACCOUNTANT = { id: 'u-acct', roles: ['ACCOUNTANT'], activeRole: 'ACCOUNTANT', activeStoreId: 'WO-PUN-01' };
const BILLS = [
  // A 400 post-dated cheque is on INV-101: 1,000 still reads outstanding today,
  // but only 600 is left to pay -- the figure the server's refusal tests.
  { bill_id: 'B1', vendor_id: 'V-PUN', bill_number: 'INV-101', bill_date: '2026-10-01', total_amount: 1000, outstanding: 1000, left_to_pay: 600, status: 'OUTSTANDING', store_id: 'WO-PUN-01' },
  { bill_id: 'B2', vendor_id: 'V-PUN', bill_number: 'INV-102', bill_date: '2026-10-05', total_amount: 500, outstanding: 0, left_to_pay: 0, status: 'PAID', store_id: 'WO-PUN-01' },
  // Fully covered by a post-dated cheque: nothing left to pay, so not offered.
  { bill_id: 'B3', vendor_id: 'V-PUN', bill_number: 'INV-103', bill_date: '2026-10-07', total_amount: 300, outstanding: 300, left_to_pay: 0, status: 'OUTSTANDING', store_id: 'WO-PUN-01' },
];
const PAYMENTS = [
  { payment_id: 'P-ADV', vendor_id: 'V-PUN', bill_id: null, amount: 400, payment_date: '2026-09-20', mode: 'BANK', reference: 'UTR9', store_id: 'WO-PUN-01' },
  { payment_id: 'P-BILL', vendor_id: 'V-PUN', bill_id: 'B2', amount: 500, payment_date: '2026-10-06', mode: 'BANK', store_id: 'WO-PUN-01' },
];

async function openForm(kind: 'Payment' | 'Apply advance') {
  render(<MemoryRouter><CashFlowPage /></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button', { name: 'AP Aging' }));
  fireEvent.click(await screen.findByText('Pune Lens Co'));
  fireEvent.click(await screen.findByRole('button', { name: kind }));
}

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = ACCOUNTANT;
  apis.cashFlowApi.ownerDashboard.mockResolvedValue({
    as_of: '2026-10-18', store_id: 'WO-PUN-01', receivables: { total: 0, buckets: {}, overdue: 0 },
    payables: { total: 1000, buckets: {}, overdue: 0, due_7d: 0, due_30d: 0, unallocated_credits: 0 },
    net_position: 0, this_month: { revenue: 0, expenses: 0, vendor_payments: 0, net_cash_flow: 0 }, alerts: [],
  });
  apis.cashFlowApi.forecast.mockResolvedValue({
    opening_cash: 0, as_of: '2026-10-18', horizon_days: 90, weeks: [],
    totals: { inflow: 0, outflow: 0, net: 0, closing_balance: 0 }, beyond_horizon: { inflow: 0, outflow: 0 },
    lowest: { week_index: 0, week_start: '', balance: 0 }, assumptions: {},
  });
  apis.vendorApApi.apAging.mockResolvedValue({
    as_of: '2026-10-18', totals: { buckets: {}, total_outstanding: 1000, unallocated_credits: 400, net_payable: 1000 },
    vendors: [{ vendor_id: 'V-PUN', vendor_name: 'Pune Lens Co', buckets: {}, total_outstanding: 1000, net_payable: 1000 }],
  });
  apis.vendorApApi.ledger.mockResolvedValue({
    vendor_id: 'V-PUN', vendor: null,
    ledger: { entries: [], closing_balance: 600, total_billed: 1500, total_paid: 900, total_tds: 0, total_debit_notes: 0 },
    aging: { as_of: '2026-10-18', buckets: {}, total_outstanding: 1000, unallocated_credits: 400, net_payable: 1000 },
  });
  apis.vendorApApi.listBills.mockResolvedValue({ bills: BILLS, total: 3 });
  apis.vendorApApi.listPayments.mockResolvedValue({ payments: PAYMENTS, total: 2 });
  apis.vendorApApi.createPayment.mockResolvedValue({ store_id: 'WO-PUN-01' });
  apis.vendorApApi.applyAdvance.mockResolvedValue({ payment: {}, advance_left: 0 });
});

describe('a payment picks its bill', () => {
  it('offers the open bills and an advance, and refuses to guess', async () => {
    await openForm('Payment');
    const picker = await screen.findByLabelText('Apply this payment to') as HTMLSelectElement;
    await waitFor(() => expect(picker.options.length).toBe(3));
    expect(Array.from(picker.options).map((o) => o.text)).toEqual([
      'Apply this payment to…', 'Bill INV-101 · 2026-10-01 · ₹600 left', 'Advance (no bill yet)',
    ]);
    fireEvent.change(screen.getByPlaceholderText('Amount paid'), { target: { value: '1000' } });
    fireEvent.click(screen.getByRole('button', { name: /Save/ }));
    expect(apis.vendorApApi.createPayment).not.toHaveBeenCalled();
    expect(toastMock.error).toHaveBeenCalledWith('Pick the bill this payment is for, or choose Advance (no bill yet)');
  });

  it('sends the bill picked, and no shop (the bill’s shop wins)', async () => {
    await openForm('Payment');
    const picker = await screen.findByLabelText('Apply this payment to');
    await waitFor(() => expect((picker as HTMLSelectElement).options.length).toBe(3));
    fireEvent.change(picker, { target: { value: 'B1' } });
    expect(screen.queryByTestId('money-shop')).toBeNull();
    fireEvent.change(screen.getByPlaceholderText('Amount paid'), { target: { value: '1000' } });
    fireEvent.click(screen.getByRole('button', { name: /Save/ }));
    await waitFor(() => expect(apis.vendorApApi.createPayment).toHaveBeenCalledTimes(1));
    const payload = apis.vendorApApi.createPayment.mock.calls[0][1];
    expect(payload).toMatchObject({ amount: 1000, bill_id: 'B1' });
    expect(payload.store_id).toBeUndefined();
    // One request id per form: a retried or re-clicked save records one payment.
    expect(typeof payload.request_id).toBe('string');
    expect(payload.request_id.length).toBeGreaterThanOrEqual(8);
  });

  it('a second Save on the same form sends the same request id', async () => {
    apis.vendorApApi.createPayment.mockRejectedValueOnce(new Error('504'));
    await openForm('Payment');
    const picker = await screen.findByLabelText('Apply this payment to');
    await waitFor(() => expect((picker as HTMLSelectElement).options.length).toBe(3));
    fireEvent.change(picker, { target: { value: 'B1' } });
    fireEvent.change(screen.getByPlaceholderText('Amount paid'), { target: { value: '600' } });
    fireEvent.click(screen.getByRole('button', { name: /Save/ }));
    await waitFor(() => expect(apis.vendorApApi.createPayment).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: /Save/ }));
    await waitFor(() => expect(apis.vendorApApi.createPayment).toHaveBeenCalledTimes(2));
    const [first, second] = apis.vendorApApi.createPayment.mock.calls.map((c) => c[1].request_id);
    expect(typeof first).toBe('string');
    expect(first).toBe(second);
  });

  it('records an advance with no bill when chosen', async () => {
    await openForm('Payment');
    const picker = await screen.findByLabelText('Apply this payment to');
    await waitFor(() => expect((picker as HTMLSelectElement).options.length).toBe(3));
    fireEvent.change(picker, { target: { value: '__advance__' } });
    fireEvent.change(screen.getByPlaceholderText('Amount paid'), { target: { value: '400' } });
    fireEvent.click(screen.getByRole('button', { name: /Save/ }));
    await waitFor(() => expect(apis.vendorApApi.createPayment).toHaveBeenCalledTimes(1));
    expect(apis.vendorApApi.createPayment.mock.calls[0][1].bill_id).toBeUndefined();
  });
});

describe('an advance is applied to a bill later, never on its own', () => {
  it('lists only unapplied advances, offers the most that fits, and applies it', async () => {
    await openForm('Apply advance');
    const advance = await screen.findByLabelText('Advance') as HTMLSelectElement;
    await waitFor(() => expect(advance.options.length).toBe(2));
    expect(advance.options[1].text).toBe('₹400 paid 2026-09-20 · UTR9');
    fireEvent.change(advance, { target: { value: 'P-ADV' } });
    fireEvent.change(screen.getByLabelText('Bill to apply it to'), { target: { value: 'B1' } });
    const amount = screen.getByLabelText('Amount to apply') as HTMLInputElement;
    expect(amount.value).toBe('400');
    fireEvent.change(amount, { target: { value: '250' } });
    fireEvent.click(screen.getByRole('button', { name: /Save/ }));
    // advance_left: what the form showed was left, so a repeated request is refused.
    await waitFor(() => expect(apis.vendorApApi.applyAdvance).toHaveBeenCalledWith('V-PUN', 'P-ADV', { bill_id: 'B1', amount: 250, advance_left: 400 }));
  });

  it('says so when the supplier has no unapplied advance', async () => {
    apis.vendorApApi.listPayments.mockResolvedValue({ payments: [PAYMENTS[1]], total: 1 });
    await openForm('Apply advance');
    expect(await screen.findByTestId('no-advances')).toHaveTextContent('This supplier has no unapplied advance.');
  });
});
