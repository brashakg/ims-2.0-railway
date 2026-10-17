// ============================================================================
// IMS 2.0 - Cash Flow Bill form: an admin names the shop a services bill is
// for (owner ruling 2026-10-12, R9)
// ============================================================================
// A services bill (rent, freight) has no goods receipt, so nothing on it says
// which shop it is for. The server booked an admin's to HIS topbar shop -- a
// Pune rent bill keyed at Head Office sat in Head Office's books, on its GSTIN.
// Now an ADMIN / SUPERADMIN must pick the shop (required, no default; the
// server refuses the bill without one), and the form sends it as store_id.
// A goods bill is its receipt's shop (a legacy receipt with no shop on record
// is picked like a services bill). Anyone else is shown, read-only, that the
// bill is their own shop's.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}));

const apis = vi.hoisted(() => ({
  cashFlowApi: {
    ownerDashboard: vi.fn(),
    forecast: vi.fn(),
  },
  vendorApApi: {
    apAging: vi.fn(),
    ledger: vi.fn(),
    listBills: vi.fn(),
    createBill: vi.fn(),
    createPayment: vi.fn(),
    createDebitNote: vi.fn(),
    listReceipts: vi.fn(),
  },
}));

const auth = vi.hoisted(() => ({
  user: null as null | { id: string; roles: string[]; activeRole: string; activeStoreId?: string },
}));

vi.mock('../../../services/api/vendorAp', () => apis);
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../../context/AuthContext', () => ({ useAuth: () => ({ user: auth.user }) }));
vi.mock('../../../hooks/usePOSQueries', () => ({
  useStores: () => ({
    data: [
      { store_id: 'BV-DHN-01', store_name: 'Better Vision Dhanbad' },
      { store_id: 'WO-PUN-01', store_name: 'WizOpt Pune' },
      { store_id: 'BV-HQ-01', store_name: 'Head Office' },
    ],
  }),
}));

import CashFlowPage from '../CashFlowPage';

const ADMIN = { id: 'u-admin', roles: ['ADMIN'], activeRole: 'ADMIN', activeStoreId: 'BV-HQ-01' };
const SUPERADMIN = { id: 'u-super', roles: ['SUPERADMIN'], activeRole: 'SUPERADMIN' };
const PUNE_ACCOUNTANT = { id: 'u-acct', roles: ['ACCOUNTANT'], activeRole: 'ACCOUNTANT', activeStoreId: 'WO-PUN-01' };

const DASH = {
  as_of: '2026-10-01',
  store_id: null,
  receivables: { total: 0, buckets: {}, overdue: 0 },
  payables: { total: 5000, buckets: {}, overdue: 0, due_7d: 0, due_30d: 0, unallocated_credits: 0 },
  net_position: 0,
  this_month: { revenue: 0, expenses: 0, vendor_payments: 0, net_cash_flow: 0 },
  alerts: [],
};
const FORECAST = {
  opening_cash: 0, as_of: '2026-10-01', horizon_days: 90, weeks: [],
  totals: { inflow: 0, outflow: 0, net: 0, closing_balance: 0 },
  beyond_horizon: { inflow: 0, outflow: 0 },
  lowest: { week_index: 0, week_start: '', balance: 0 }, assumptions: {},
};
const AGING = {
  as_of: '2026-10-01',
  totals: { buckets: {}, total_outstanding: 5000, unallocated_credits: 0, net_payable: 5000 },
  vendors: [{ vendor_id: 'V-RENT', vendor_name: 'Landlord', buckets: {}, total_outstanding: 5000, net_payable: 5000 }],
};
const LEDGER = {
  vendor_id: 'V-RENT', vendor: null,
  ledger: { entries: [], closing_balance: 5000, total_billed: 5000, total_paid: 0, total_tds: 0, total_debit_notes: 0 },
  aging: { as_of: '2026-10-01', buckets: {}, total_outstanding: 5000, unallocated_credits: 0, net_payable: 5000 },
};
const RECEIPTS = [
  { grn_id: 'GRN-D', grn_number: 'RCPT 0008', store_id: 'BV-DHN-01' },
  { grn_id: 'GRN-OLD', grn_number: 'RCPT 0001', store_id: null }, // legacy: no shop on record
];

const BILL_SHOP = 'Shop this bill is for';

async function openBillForm(kind: 'GOODS' | 'SERVICES') {
  render(<CashFlowPage />);
  fireEvent.click(await screen.findByRole('button', { name: 'AP Aging' }));
  fireEvent.click(await screen.findByText('Landlord'));
  await waitFor(() => expect(apis.vendorApApi.ledger).toHaveBeenCalledWith('V-RENT'));
  fireEvent.click(await screen.findByRole('button', { name: 'Bill' }));
  fireEvent.change(await screen.findByDisplayValue('This bill is for…'), { target: { value: kind } });
  fireEvent.change(screen.getByPlaceholderText('Bill / invoice no'), { target: { value: 'RENT-OCT' } });
  fireEvent.change(screen.getByPlaceholderText('Taxable amount'), { target: { value: '10000' } });
  fireEvent.change(screen.getByPlaceholderText('Tax (GST)'), { target: { value: '1800' } });
}

function save() {
  fireEvent.click(screen.getByRole('button', { name: /Save/ }));
}

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = ADMIN;
  apis.cashFlowApi.ownerDashboard.mockResolvedValue(DASH);
  apis.cashFlowApi.forecast.mockResolvedValue(FORECAST);
  apis.vendorApApi.apAging.mockResolvedValue(AGING);
  apis.vendorApApi.ledger.mockResolvedValue(LEDGER);
  apis.vendorApApi.listReceipts.mockResolvedValue(RECEIPTS);
  apis.vendorApApi.createBill.mockImplementation(async (_v: string, body: { store_id?: string }) => ({
    bill_id: 'B1',
    store_id: body.store_id ?? 'BV-DHN-01',
  }));
});

describe('an admin must name the shop a services bill is for', () => {
  it('offers every shop with no default -- never his topbar shop -- and will not save without one', async () => {
    await openBillForm('SERVICES');
    const select = screen.getByLabelText(BILL_SHOP) as HTMLSelectElement;
    expect(select.required).toBe(true);
    expect(select.value).toBe('');
    expect(Array.from(select.options).map((o) => o.text)).toEqual([
      'Pick the shop this bill is for…', 'Better Vision Dhanbad', 'WizOpt Pune', 'Head Office',
    ]);
    save();
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith('Pick the shop this bill is for'));
    expect(apis.vendorApApi.createBill).not.toHaveBeenCalled();
  });

  it('sends the shop he picks as store_id, and the toast names where it was booked', async () => {
    await openBillForm('SERVICES');
    fireEvent.change(screen.getByLabelText(BILL_SHOP), { target: { value: 'WO-PUN-01' } });
    save();
    await waitFor(() => expect(apis.vendorApApi.createBill).toHaveBeenCalledTimes(1));
    const [vendorId, payload] = apis.vendorApApi.createBill.mock.calls[0];
    expect(vendorId).toBe('V-RENT');
    expect(payload).toMatchObject({ bill_kind: 'SERVICES', store_id: 'WO-PUN-01', total_amount: 11800 });
    expect(payload.grn_id).toBeUndefined();
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Bill recorded for WizOpt Pune'));
  });

  it('a superadmin with no topbar shop picks one the same way', async () => {
    auth.user = SUPERADMIN;
    await openBillForm('SERVICES');
    save();
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith('Pick the shop this bill is for'));
    fireEvent.change(screen.getByLabelText(BILL_SHOP), { target: { value: 'BV-DHN-01' } });
    save();
    await waitFor(() => expect(apis.vendorApApi.createBill).toHaveBeenCalledTimes(1));
    expect(apis.vendorApApi.createBill.mock.calls[0][1].store_id).toBe('BV-DHN-01');
  });
});

describe('a goods bill is its receipt’s shop', () => {
  it('asks no shop for a receipt with one, and sends none', async () => {
    await openBillForm('GOODS');
    fireEvent.change(await screen.findByLabelText('Goods receipt'), { target: { value: 'GRN-D' } });
    expect(screen.queryByLabelText(BILL_SHOP)).toBeNull();
    save();
    await waitFor(() => expect(apis.vendorApApi.createBill).toHaveBeenCalledTimes(1));
    const payload = apis.vendorApApi.createBill.mock.calls[0][1];
    expect(payload).toMatchObject({ bill_kind: 'GOODS', grn_id: 'GRN-D' });
    expect(payload.store_id).toBeUndefined();
  });

  it('a legacy receipt with no shop on record is placed by the admin’s pick', async () => {
    await openBillForm('GOODS');
    fireEvent.change(await screen.findByLabelText('Goods receipt'), { target: { value: 'GRN-OLD' } });
    save();
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith('Pick the shop this bill is for'));
    expect(apis.vendorApApi.createBill).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(BILL_SHOP), { target: { value: 'WO-PUN-01' } });
    save();
    await waitFor(() => expect(apis.vendorApApi.createBill).toHaveBeenCalledTimes(1));
    expect(apis.vendorApApi.createBill.mock.calls[0][1]).toMatchObject({ grn_id: 'GRN-OLD', store_id: 'WO-PUN-01' });
  });
});

describe('anyone else’s services bill is their own shop’s, shown read-only', () => {
  it('shows "Shop: WizOpt Pune", offers no choice and sends no shop', async () => {
    auth.user = PUNE_ACCOUNTANT;
    apis.vendorApApi.createBill.mockResolvedValue({ bill_id: 'B1', store_id: 'WO-PUN-01' });
    await openBillForm('SERVICES');
    expect(screen.queryByLabelText(BILL_SHOP)).toBeNull();
    expect(screen.getByTestId('bill-shop').textContent).toBe('Shop: WizOpt Pune');
    save();
    await waitFor(() => expect(apis.vendorApApi.createBill).toHaveBeenCalledTimes(1));
    expect(apis.vendorApApi.createBill.mock.calls[0][1].store_id).toBeUndefined();
  });
});
