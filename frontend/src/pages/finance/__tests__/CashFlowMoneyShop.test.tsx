// ============================================================================
// IMS 2.0 - Cash Flow: which shop a payment / debit note is booked to, and
// which shop the page's payables cover (review 2026-10-01 #1 #13 #21)
// ============================================================================
// #1 / #13: this drawer is the only screen that records supplier payments and
// debit notes. It sent no shop, and the server stamped an admin's money with
// HIS topbar shop -- a Pune supplier paid from HQ stayed owed in Pune. Now the
// server leaves an admin's unnamed money to the supplier's shop by its bills,
// and the form lets an admin pick a shop (sent as store_id only when picked).
// Anyone else is shown, read-only, that the money is their own shop's.
// #21: the page never said which shop its payables cover.

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
const PUNE_ACCOUNTANT = { id: 'u-acct', roles: ['ACCOUNTANT'], activeRole: 'ACCOUNTANT', activeStoreId: 'WO-PUN-01' };

function dash(storeId?: string | null) {
  return {
    as_of: '2026-10-01',
    ...(storeId === undefined ? {} : { store_id: storeId }),
    receivables: { total: 0, buckets: {}, overdue: 0 },
    payables: { total: 5000, buckets: {}, overdue: 0, due_7d: 0, due_30d: 0, unallocated_credits: 0 },
    net_position: 0,
    this_month: { revenue: 0, expenses: 0, vendor_payments: 0, net_cash_flow: 0 },
    alerts: [],
  };
}
const FORECAST = {
  opening_cash: 0, as_of: '2026-10-01', horizon_days: 90, weeks: [],
  totals: { inflow: 0, outflow: 0, net: 0, closing_balance: 0 },
  beyond_horizon: { inflow: 0, outflow: 0 },
  lowest: { week_index: 0, week_start: '', balance: 0 }, assumptions: {},
};
const AGING = {
  as_of: '2026-10-01',
  totals: { buckets: {}, total_outstanding: 5000, unallocated_credits: 0, net_payable: 5000 },
  vendors: [{ vendor_id: 'V-PUN', vendor_name: 'Pune Lens Co', buckets: {}, total_outstanding: 5000, net_payable: 5000 }],
};
const LEDGER = {
  vendor_id: 'V-PUN', vendor: null,
  ledger: { entries: [], closing_balance: 5000, total_billed: 5000, total_paid: 0, total_tds: 0, total_debit_notes: 0 },
  aging: { as_of: '2026-10-01', buckets: {}, total_outstanding: 5000, unallocated_credits: 0, net_payable: 5000 },
};

const SHOP_SELECT = 'Shop this money is for';

async function openForm(kind: 'Payment' | 'Debit note' | 'Bill') {
  render(<CashFlowPage />);
  fireEvent.click(await screen.findByRole('button', { name: 'AP Aging' }));
  fireEvent.click(await screen.findByText('Pune Lens Co'));
  await waitFor(() => expect(apis.vendorApApi.ledger).toHaveBeenCalledWith('V-PUN'));
  fireEvent.click(await screen.findByRole('button', { name: kind }));
}

function fillPayment(amount: string) {
  fireEvent.change(screen.getByPlaceholderText('Amount paid'), { target: { value: amount } });
}

function save() {
  fireEvent.click(screen.getByRole('button', { name: /Save/ }));
}

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = ADMIN;
  apis.cashFlowApi.ownerDashboard.mockResolvedValue(dash(null));
  apis.cashFlowApi.forecast.mockResolvedValue(FORECAST);
  apis.vendorApApi.apAging.mockResolvedValue(AGING);
  apis.vendorApApi.ledger.mockResolvedValue(LEDGER);
  apis.vendorApApi.createPayment.mockResolvedValue({});
  apis.vendorApApi.createDebitNote.mockResolvedValue({});
});

describe('an admin picks the shop a payment or debit note is for, or leaves it to the supplier', () => {
  it('sends no shop by default: the supplier’s shop by its bills, never his topbar shop', async () => {
    await openForm('Payment');
    const select = screen.getByLabelText(SHOP_SELECT) as HTMLSelectElement;
    expect(select.value).toBe('');
    expect(select.options[select.selectedIndex].text).toBe('The supplier’s shop (by its bills)');
    expect(Array.from(select.options).map((o) => o.text)).toEqual([
      'The supplier’s shop (by its bills)', 'Better Vision Dhanbad', 'WizOpt Pune', 'Head Office',
    ]);
    fillPayment('5000');
    save();
    await waitFor(() => expect(apis.vendorApApi.createPayment).toHaveBeenCalledTimes(1));
    const [vendorId, payload] = apis.vendorApApi.createPayment.mock.calls[0];
    expect(vendorId).toBe('V-PUN');
    expect(payload.amount).toBe(5000);
    expect(payload.store_id).toBeUndefined();
  });

  it('sends the shop he picks for a payment', async () => {
    await openForm('Payment');
    fireEvent.change(screen.getByLabelText(SHOP_SELECT), { target: { value: 'WO-PUN-01' } });
    fillPayment('5000');
    save();
    await waitFor(() => expect(apis.vendorApApi.createPayment).toHaveBeenCalledTimes(1));
    expect(apis.vendorApApi.createPayment.mock.calls[0][1].store_id).toBe('WO-PUN-01');
  });

  it('sends the shop he picks for a debit note, and none when he leaves the default', async () => {
    await openForm('Debit note');
    fireEvent.change(screen.getByPlaceholderText('Amount'), { target: { value: '300' } });
    fireEvent.change(screen.getByPlaceholderText('Reason (e.g. rejected goods)'), { target: { value: 'rejected' } });
    fireEvent.change(screen.getByLabelText(SHOP_SELECT), { target: { value: 'BV-DHN-01' } });
    save();
    await waitFor(() => expect(apis.vendorApApi.createDebitNote).toHaveBeenCalledTimes(1));
    expect(apis.vendorApApi.createDebitNote.mock.calls[0][1]).toMatchObject({ amount: 300, reason: 'rejected', store_id: 'BV-DHN-01' });

    fireEvent.click(await screen.findByRole('button', { name: 'Debit note' }));
    fireEvent.change(screen.getByPlaceholderText('Amount'), { target: { value: '10' } });
    save();
    await waitFor(() => expect(apis.vendorApApi.createDebitNote).toHaveBeenCalledTimes(2));
    expect(apis.vendorApApi.createDebitNote.mock.calls[1][1].store_id).toBeUndefined();
  });

  it('asks no shop on a bill (a bill is booked to its goods receipt’s shop)', async () => {
    await openForm('Bill');
    await screen.findByText('This bill is for…');
    expect(screen.queryByLabelText(SHOP_SELECT)).toBeNull();
  });
});

describe('anyone else is told, read-only, the money is their own shop’s', () => {
  it('shows "Shop: WizOpt Pune", offers no choice and sends no shop', async () => {
    auth.user = PUNE_ACCOUNTANT;
    await openForm('Payment');
    expect(screen.queryByLabelText(SHOP_SELECT)).toBeNull();
    expect(screen.getByTestId('money-shop').textContent).toBe('Shop: WizOpt Pune');
    fillPayment('100');
    save();
    await waitFor(() => expect(apis.vendorApApi.createPayment).toHaveBeenCalledTimes(1));
    expect(apis.vendorApApi.createPayment.mock.calls[0][1].store_id).toBeUndefined();
  });

  it('shows the same read-only shop on a debit note', async () => {
    auth.user = PUNE_ACCOUNTANT;
    await openForm('Debit note');
    expect(screen.queryByLabelText(SHOP_SELECT)).toBeNull();
    expect(screen.getByTestId('money-shop').textContent).toBe('Shop: WizOpt Pune');
  });
});

describe('the page says which shop its payables cover (#21)', () => {
  it('reads "All shops" under the title and on AP Aging when the server answers every shop', async () => {
    render(<CashFlowPage />);
    expect((await screen.findByTestId('payables-shop')).textContent).toBe('All shops');
    fireEvent.click(screen.getByRole('button', { name: 'AP Aging' }));
    expect((await screen.findByTestId('aging-shop')).textContent).toBe('All shops');
  });

  it('names the shop when the server scoped the figures to one', async () => {
    auth.user = PUNE_ACCOUNTANT;
    apis.cashFlowApi.ownerDashboard.mockResolvedValue(dash('WO-PUN-01'));
    render(<CashFlowPage />);
    expect((await screen.findByTestId('payables-shop')).textContent).toBe('Shop: WizOpt Pune');
    fireEvent.click(screen.getByRole('button', { name: 'AP Aging' }));
    expect((await screen.findByTestId('aging-shop')).textContent).toBe('Shop: WizOpt Pune');
  });

  it('says nothing rather than guess when an older server leaves store_id out', async () => {
    apis.cashFlowApi.ownerDashboard.mockResolvedValue(dash(undefined));
    render(<CashFlowPage />);
    await screen.findByText('Payables (AP)');
    expect(screen.queryByTestId('payables-shop')).toBeNull();
  });
});
