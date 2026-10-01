// ============================================================================
// IMS 2.0 - Cash Flow vendor ledger: post-dated rows are shown, not counted
// ============================================================================
// Review 2026-10-01 #1: an accountant keyed a cheque dated 15 Nov on 1 Oct and
// it vanished -- the ledger strikes its balance on today, so the cheque was in
// no list at all. The server now returns the later rows apart
// (ledger.post_dated, no running balance); the drawer lists them under the
// ledger, labelled as not yet in the balance.

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

vi.mock('../../../services/api/vendorAp', () => apis);
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));

import CashFlowPage from '../CashFlowPage';

const DASH = {
  as_of: '2026-10-01',
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
  vendors: [{ vendor_id: 'V-PDC', vendor_name: 'Cheque Lens Co', buckets: {}, total_outstanding: 5000, net_payable: 5000 }],
};

function ledgerWith(postDated: unknown[] | undefined) {
  return {
    vendor_id: 'V-PDC', vendor: null,
    ledger: {
      entries: [{ date: '2026-09-20', type: 'BILL', ref: 'PD1', description: 'Vendor bill', debit: 0, credit: 5000, balance: 5000 }],
      closing_balance: 5000, total_billed: 5000, total_paid: 0, total_tds: 0, total_debit_notes: 0,
      ...(postDated === undefined ? {} : { post_dated: postDated }),
    },
    aging: { as_of: '2026-10-01', buckets: {}, total_outstanding: 5000, unallocated_credits: 0, net_payable: 5000 },
  };
}

async function openLedger() {
  render(<CashFlowPage />);
  fireEvent.click(await screen.findByRole('button', { name: 'AP Aging' }));
  fireEvent.click(await screen.findByText('Cheque Lens Co'));
  await waitFor(() => expect(apis.vendorApApi.ledger).toHaveBeenCalledWith('V-PDC'));
  await screen.findByText('Payable balance');
}

beforeEach(() => {
  vi.clearAllMocks();
  apis.cashFlowApi.ownerDashboard.mockResolvedValue(DASH);
  apis.cashFlowApi.forecast.mockResolvedValue(FORECAST);
  apis.vendorApApi.apAging.mockResolvedValue(AGING);
});

describe('the vendor ledger drawer lists post-dated rows apart', () => {
  it('shows the post-dated cheque and note under the ledger, labelled as not in the balance', async () => {
    apis.vendorApApi.ledger.mockResolvedValue(ledgerWith([
      { date: '2026-10-20', type: 'DEBIT_NOTE', ref: 'DN-PDC-1', description: 'short supply', debit: 300, credit: 0 },
      { date: '2026-11-15', type: 'PAYMENT', ref: 'CHQ-777', description: 'Payment (CHEQUE)', debit: 5000, credit: 0 },
    ]));
    await openLedger();
    const section = await screen.findByTestId('ledger-post-dated');
    expect(within(section).getByText('Post-dated: not in the balance yet')).toBeTruthy();
    const rows = within(section).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[1].textContent).toContain('2026-11-15');
    expect(rows[1].textContent).toContain('CHQ-777');
    expect(rows[1].textContent).toContain('5,000');
    expect(rows[0].textContent).toContain('DN-PDC-1');
    // The balance card is still today's figure: the cheque is not counted.
    expect(screen.getAllByText('₹5,000').length).toBeGreaterThan(0);
  });

  it('shows no post-dated section when nothing is dated after today (or an older server omits it)', async () => {
    apis.vendorApApi.ledger.mockResolvedValue(ledgerWith([]));
    await openLedger();
    expect(screen.queryByTestId('ledger-post-dated')).toBeNull();
  });

  it('copes with a ledger response that has no post_dated key', async () => {
    apis.vendorApApi.ledger.mockResolvedValue(ledgerWith(undefined));
    await openLedger();
    expect(screen.queryByTestId('ledger-post-dated')).toBeNull();
    expect(screen.getByText('PD1')).toBeTruthy();
  });
});
