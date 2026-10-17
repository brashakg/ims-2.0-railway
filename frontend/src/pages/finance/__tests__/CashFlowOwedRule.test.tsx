// ============================================================================
// IMS 2.0 - Cash Flow & Payables: THE ONE 'WE OWE' RULE, a fresh form per
// kind, receipts and money that name their shop (review r3 #1 #11 #14)
// ============================================================================
// #1  One supplier Rs 10,000 ahead and another owed Rs 5,000: the card read
//     'Rs 0 · Rs 5,000 overdue', its note 'Less Rs 10,000 paid ... = Rs 0
//     owed' (5,000 - 10,000 is not 0), and AP Aging's footer subtracted the
//     advance. Now every figure reads what we owe (Rs 5,000) and names the
//     Rs 10,000 paid ahead APART -- never taken off.
// #11 The record form kept its state across Payment / Debit note / Bill: an
//     amount and shop typed for a payment were saved on a debit note whose
//     boxes showed empty.
// #14 An admin's goods-receipt list covers every shop, yet named none and its
//     empty state said 'at your store'.
// And after saving money, the toast names the shop the server booked it to.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';

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
    ],
  }),
}));

import CashFlowPage from '../CashFlowPage';

const ADMIN = { id: 'u-admin', roles: ['ADMIN'], activeRole: 'ADMIN', activeStoreId: 'BV-DHN-01' };
const PUNE_ACCOUNTANT = { id: 'u-acct', roles: ['ACCOUNTANT'], activeRole: 'ACCOUNTANT', activeStoreId: 'WO-PUN-01' };

// The server's answer for: Advance Frames Rs 10,000 ahead, Owed Lens Co owed
// Rs 5,000 (due 9 Sep, 22 days late on 1 Oct). OWED 5000, ADVANCES 10000.
const DASH = {
  as_of: '2026-10-01',
  store_id: null,
  receivables: { total: 0, buckets: {}, overdue: 0 },
  payables: {
    total: 5000,
    buckets: { current: 0, '1_30': 5000, '31_60': 0, '61_90': 0, '90_plus': 0 },
    overdue: 5000, due_7d: 0, due_30d: 0,
    advances: 10000, unallocated_credits: 10000,
  },
  net_position: -5000,
  this_month: { revenue: 0, expenses: 0, vendor_payments: 0, net_cash_flow: 0 },
  alerts: [],
};
const FORECAST = {
  opening_cash: 0, as_of: '2026-10-01', horizon_days: 90, weeks: [],
  totals: { inflow: 0, outflow: 0, net: 0, closing_balance: 0 },
  beyond_horizon: { inflow: 0, outflow: 0 },
  lowest: { week_index: 0, week_start: '', balance: 0 }, assumptions: {},
};
const NO_BUCKETS = { current: 0, '1_30': 0, '31_60': 0, '61_90': 0, '90_plus': 0 };
const AGING = {
  as_of: '2026-10-01',
  totals: {
    buckets: { ...NO_BUCKETS, '1_30': 5000 },
    total_outstanding: 5000, owed: 5000, advances: 10000, unallocated_credits: 10000, net_payable: 5000,
  },
  vendors: [
    { vendor_id: 'V-OWE', vendor_name: 'Owed Lens Co', buckets: { ...NO_BUCKETS, '1_30': 5000 },
      total_outstanding: 5000, owed: 5000, advances: 0, balance: 5000, unallocated_credits: 0, net_payable: 5000 },
    { vendor_id: 'V-ADV', vendor_name: 'Advance Frames', buckets: NO_BUCKETS,
      total_outstanding: 0, owed: 0, advances: 10000, balance: -10000, unallocated_credits: 10000, net_payable: 0 },
  ],
};
const LEDGER = {
  vendor_id: 'V-OWE', vendor: null,
  ledger: { entries: [], closing_balance: 5000, total_billed: 5000, total_paid: 0, total_tds: 0, total_debit_notes: 0 },
  aging: { as_of: '2026-10-01', buckets: {}, total_outstanding: 5000, unallocated_credits: 0, net_payable: 5000 },
};

const SHOP_SELECT = 'Shop this money is for';

async function openAging() {
  render(<CashFlowPage />);
  fireEvent.click(await screen.findByRole('button', { name: 'AP Aging' }));
}

async function openForm(kind: 'Payment' | 'Debit note' | 'Bill') {
  await openAging();
  fireEvent.click(await screen.findByText('Owed Lens Co'));
  await waitFor(() => expect(apis.vendorApApi.ledger).toHaveBeenCalledWith('V-OWE'));
  fireEvent.click(await screen.findByRole('button', { name: kind }));
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
  apis.vendorApApi.createPayment.mockResolvedValue({});
  apis.vendorApApi.createDebitNote.mockResolvedValue({});
  apis.vendorApApi.createBill.mockResolvedValue({});
  apis.vendorApApi.listReceipts.mockResolvedValue([]);
});

describe('#1 every Cash Flow figure reads what we owe, and the advance apart', () => {
  it('the Payables card says Rs 5,000 owed and names the Rs 10,000 paid ahead, never as a subtraction', async () => {
    render(<CashFlowPage />);
    const card = await screen.findByTestId('payables-card');
    expect(within(card).getByText('₹5,000')).toBeInTheDocument();
    expect(card.textContent).toContain('₹5,000 overdue');
    expect(card.textContent).toContain('₹10,000 paid ahead to suppliers, not taken off');
    expect(card.textContent).not.toMatch(/less/i);
    expect(card.textContent).not.toContain('₹0');
  });

  it('the Payables aging note states both figures, with no "less ... =" arithmetic', async () => {
    render(<CashFlowPage />);
    const bars = await screen.findByTestId('payables-aging');
    expect(bars.textContent).toContain('We owe ₹5,000.');
    expect(bars.textContent).toContain('₹10,000 paid ahead to suppliers');
    expect(bars.textContent).not.toMatch(/less/i);
    expect(bars.textContent).not.toContain('=');
    expect(bars.textContent).not.toContain('₹0 owed');
  });

  it('says nothing about advances when nobody was paid ahead', async () => {
    apis.cashFlowApi.ownerDashboard.mockResolvedValue({
      ...DASH, payables: { ...DASH.payables, advances: 0, unallocated_credits: 0 },
    });
    render(<CashFlowPage />);
    const card = await screen.findByTestId('payables-card');
    expect(card.textContent).not.toContain('paid ahead');
    expect(screen.getByTestId('payables-aging').textContent).not.toContain('paid ahead');
  });

  it('AP Aging: the total is what we owe; the advance is its own row, not subtracted', async () => {
    await openAging();
    const total = await screen.findByTestId('aging-total');
    const cells = within(total).getAllByRole('cell');
    expect(cells[0].textContent).toBe('Total we owe');
    expect(cells[cells.length - 1].textContent).toBe('₹5,000');
    const ahead = screen.getByTestId('aging-paid-ahead');
    const aheadCells = within(ahead).getAllByRole('cell');
    expect(aheadCells[aheadCells.length - 1].textContent).toBe('₹10,000');
    expect(ahead.textContent).toContain('not taken off the total');
    expect(screen.queryByText(/^Less/)).toBeNull();
    expect(document.body.textContent).not.toContain('-₹10,000');
  });

  it('AP Aging: the supplier paid ahead reads its own advance; the one we owe reads its debt', async () => {
    await openAging();
    const owedCells = await screen.findAllByTestId('aging-vendor-owed');
    expect(owedCells.map((c) => c.textContent)).toEqual(['₹5,000', '₹10,000 advance']);
  });

  it('the supplier paid ahead: its ledger drawer reads "₹10,000 advance", not a minus balance', async () => {
    apis.vendorApApi.ledger.mockResolvedValue({
      ...LEDGER, vendor_id: 'V-ADV',
      ledger: { ...LEDGER.ledger, closing_balance: -10000, total_billed: 1000, total_paid: 11000 },
    });
    await openAging();
    fireEvent.click(await screen.findByText('Advance Frames'));
    const card = await screen.findByTestId('ledger-balance');
    expect(within(card).getByText('₹10,000 advance')).toBeInTheDocument();
    expect(card.textContent).not.toContain('-');
  });
});

describe('#11 each kind of record mounts a fresh form', () => {
  it('a payment amount and shop typed, then Debit note: the note sends neither', async () => {
    await openForm('Payment');
    fireEvent.change(screen.getByPlaceholderText('Amount paid'), { target: { value: '5000' } });
    fireEvent.change(screen.getByLabelText(SHOP_SELECT), { target: { value: 'WO-PUN-01' } });

    fireEvent.click(screen.getByRole('button', { name: 'Debit note' }));
    expect((screen.getByPlaceholderText('Amount') as HTMLInputElement).value).toBe('');
    expect((screen.getByLabelText(SHOP_SELECT) as HTMLSelectElement).value).toBe('');
    fireEvent.change(screen.getByPlaceholderText('Reason (e.g. rejected goods)'), { target: { value: 'rejected' } });
    save();

    await waitFor(() => expect(apis.vendorApApi.createDebitNote).toHaveBeenCalledTimes(1));
    const [, payload] = apis.vendorApApi.createDebitNote.mock.calls[0];
    expect(payload.amount).toBe(0);
    expect(payload.store_id).toBeUndefined();
    expect(payload.reason).toBe('rejected');
    expect(apis.vendorApApi.createPayment).not.toHaveBeenCalled();
  });

  it('a debit-note amount and shop typed, then Payment: the payment sends neither', async () => {
    await openForm('Debit note');
    fireEvent.change(screen.getByPlaceholderText('Amount'), { target: { value: '700' } });
    fireEvent.change(screen.getByLabelText(SHOP_SELECT), { target: { value: 'BV-DHN-01' } });

    fireEvent.click(screen.getByRole('button', { name: 'Payment' }));
    expect((screen.getByPlaceholderText('Amount paid') as HTMLInputElement).value).toBe('');
    expect((screen.getByLabelText(SHOP_SELECT) as HTMLSelectElement).value).toBe('');
    save();

    await waitFor(() => expect(apis.vendorApApi.createPayment).toHaveBeenCalledTimes(1));
    const [, payload] = apis.vendorApApi.createPayment.mock.calls[0];
    expect(payload.amount).toBe(0);
    expect(payload.store_id).toBeUndefined();
    expect(apis.vendorApApi.createDebitNote).not.toHaveBeenCalled();
  });
});

const RECEIPTS = [
  { grn_id: 'G-D', grn_number: 'GRN-D', vendor_invoice_no: 'D-1', store_id: 'BV-DHN-01' },
  { grn_id: 'G-P', grn_number: 'GRN-P', vendor_invoice_no: 'P-1', store_id: 'WO-PUN-01' },
  { grn_id: 'G-X', grn_number: 'GRN-X', vendor_invoice_no: 'X-1' },
];

async function openGoods() {
  await openForm('Bill');
  fireEvent.change(await screen.findByDisplayValue('This bill is for…'), { target: { value: 'GOODS' } });
  await waitFor(() => expect(apis.vendorApApi.listReceipts).toHaveBeenCalledWith('V-OWE'));
}

describe('#14 the Bill form’s goods receipts name their shop', () => {
  it('an admin’s list (every shop) names each receipt’s shop', async () => {
    apis.vendorApApi.listReceipts.mockResolvedValue(RECEIPTS);
    await openGoods();
    const select = (await screen.findByLabelText('Goods receipt')) as HTMLSelectElement;
    await waitFor(() => expect(select.options.length).toBe(4));
    expect(Array.from(select.options).slice(1).map((o) => o.text)).toEqual([
      'GRN-D · inv D-1 · for Better Vision Dhanbad',
      'GRN-P · inv P-1 · for WizOpt Pune',
      'GRN-X · inv X-1 · no shop on record',
    ]);
  });

  it('an admin’s empty list says it searched every store', async () => {
    await openGoods();
    const empty = await screen.findByTestId('receipts-empty');
    expect(empty.textContent).toContain('No unbilled goods receipts for this vendor in any store.');
    expect(empty.textContent).not.toContain('your store');
  });

  it('a shop accountant’s list is his shop’s: no shop on each row, and the empty state names it', async () => {
    auth.user = PUNE_ACCOUNTANT;
    apis.vendorApApi.listReceipts.mockResolvedValue([RECEIPTS[1]]);
    await openGoods();
    const select = (await screen.findByLabelText('Goods receipt')) as HTMLSelectElement;
    await waitFor(() => expect(select.options.length).toBe(2));
    expect(select.options[1].text).toBe('GRN-P · inv P-1');
  });

  it('a shop accountant’s empty list says which shop it searched', async () => {
    auth.user = PUNE_ACCOUNTANT;
    await openGoods();
    const empty = await screen.findByTestId('receipts-empty');
    expect(empty.textContent).toContain('No unbilled goods receipts for this vendor at WizOpt Pune.');
  });
});

describe('after saving money, the toast names the shop it was booked to', () => {
  it('a payment the server stamped Pune reads "Payment recorded for WizOpt Pune"', async () => {
    apis.vendorApApi.createPayment.mockResolvedValue({ payment_id: 'p1', store_id: 'WO-PUN-01' });
    await openForm('Payment');
    fireEvent.change(screen.getByPlaceholderText('Amount paid'), { target: { value: '3000' } });
    save();
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Payment recorded for WizOpt Pune'));
  });

  it('a debit note the server stamped Dhanbad reads "Debit note recorded for Better Vision Dhanbad"', async () => {
    apis.vendorApApi.createDebitNote.mockResolvedValue({ debit_note_id: 'd1', store_id: 'BV-DHN-01' });
    await openForm('Debit note');
    fireEvent.change(screen.getByPlaceholderText('Amount'), { target: { value: '300' } });
    save();
    await waitFor(() =>
      expect(toastMock.success).toHaveBeenCalledWith('Debit note recorded for Better Vision Dhanbad'),
    );
  });

  it('money booked to no shop says so', async () => {
    apis.vendorApApi.createPayment.mockResolvedValue({ payment_id: 'p2', store_id: null });
    await openForm('Payment');
    fireEvent.change(screen.getByPlaceholderText('Amount paid'), { target: { value: '100' } });
    save();
    await waitFor(() =>
      expect(toastMock.success).toHaveBeenCalledWith('Payment recorded with no shop on record'),
    );
  });
});
