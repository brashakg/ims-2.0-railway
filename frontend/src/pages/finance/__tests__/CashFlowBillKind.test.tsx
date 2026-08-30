// ============================================================================
// IMS 2.0 - Cash Flow vendor Record-Bill form: goods bills declare + link
// ============================================================================
// This form used to book "20 pcs assorted frames" (Rs 52,500) as prose with no
// receipt and no products named -- the free-text goods-bill hole. Now:
//   - the goods/services choice is REQUIRED before Save reaches the server
//   - GOODS requires picking one of the vendor's ACCEPTED receipts and sends
//     bill_kind + grn_id on the wire
//   - SERVICES books header-only, sending bill_kind with no grn_id
//   - a receipts endpoint that cannot be reached shows a distinct error state
//     (not a silent "no receipts")

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

vi.mock('../../../services/api/vendorAp', () => apis);
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));

import CashFlowPage from '../CashFlowPage';

const DASH = {
  as_of: '2026-08-30',
  receivables: { total: 0, buckets: {}, overdue: 0 },
  payables: { total: 0, buckets: {}, overdue: 0, due_7d: 0, due_30d: 0, unallocated_credits: 0 },
  net_position: 0,
  this_month: { revenue: 0, expenses: 0, vendor_payments: 0, net_cash_flow: 0 },
  alerts: [],
};
const FORECAST = {
  opening_cash: 0, as_of: '2026-08-30', horizon_days: 90, weeks: [],
  totals: { inflow: 0, outflow: 0, net: 0, closing_balance: 0 },
  beyond_horizon: { inflow: 0, outflow: 0 },
  lowest: { week_index: 0, week_start: '', balance: 0 }, assumptions: {},
};
const AGING = {
  as_of: '2026-08-30',
  totals: { buckets: {}, total_outstanding: 0, unallocated_credits: 0, net_payable: 0 },
  vendors: [{ vendor_id: 'V-77', vendor_name: 'Frames Wala', buckets: {}, total_outstanding: 0, net_payable: 52500 }],
};
const LEDGER = {
  vendor_id: 'V-77', vendor: null,
  ledger: { entries: [], closing_balance: 0, total_billed: 0, total_paid: 0, total_tds: 0, total_debit_notes: 0 },
  aging: { as_of: '2026-08-30', buckets: {}, total_outstanding: 0, unallocated_credits: 0, net_payable: 0 },
};

async function openBillForm() {
  render(<CashFlowPage />);
  fireEvent.click(await screen.findByRole('button', { name: 'AP Aging' }));
  fireEvent.click(await screen.findByText('Frames Wala'));
  await waitFor(() => expect(apis.vendorApApi.ledger).toHaveBeenCalled());
  fireEvent.click(await screen.findByRole('button', { name: /Bill/ }));
  await screen.findByText('This bill is for…');
}

function fillHeader() {
  fireEvent.change(screen.getByPlaceholderText('Bill / invoice no'), {
    target: { value: 'FW-2081' },
  });
  fireEvent.change(screen.getByPlaceholderText('Taxable amount'), {
    target: { value: '50000' },
  });
  fireEvent.change(screen.getByPlaceholderText('Tax (GST)'), {
    target: { value: '2500' },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  apis.cashFlowApi.ownerDashboard.mockResolvedValue(DASH);
  apis.cashFlowApi.forecast.mockResolvedValue(FORECAST);
  apis.vendorApApi.apAging.mockResolvedValue(AGING);
  apis.vendorApApi.ledger.mockResolvedValue(LEDGER);
  apis.vendorApApi.createBill.mockResolvedValue({});
  apis.vendorApApi.listReceipts.mockResolvedValue([
    {
      grn_id: 'DC-OTC-1',
      grn_number: 'RCPT/BV/26-27/0009',
      grn_subtype: 'DELIVERY_CHALLAN',
      dc_number: 'DC/26/08/9',
      dc_date: '2026-08-28',
    },
  ]);
});

describe('the goods/services declaration on the Record-Bill form', () => {
  it('refuses to save without the declaration', async () => {
    await openBillForm();
    fillHeader();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith(
        expect.stringContaining('what this bill is for'),
      ),
    );
    expect(apis.vendorApApi.createBill).not.toHaveBeenCalled();
  });

  it('GOODS without a receipt picked never reaches the server', async () => {
    await openBillForm();
    fillHeader();
    fireEvent.change(screen.getByDisplayValue('This bill is for…'), {
      target: { value: 'GOODS' },
    });
    await waitFor(() => expect(apis.vendorApApi.listReceipts).toHaveBeenCalledWith('V-77'));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith(
        expect.stringContaining('Pick the goods receipt'),
      ),
    );
    expect(apis.vendorApApi.createBill).not.toHaveBeenCalled();
  });

  it('GOODS with the DC receipt picked sends bill_kind + grn_id (Rs 52,500)', async () => {
    await openBillForm();
    fillHeader();
    fireEvent.change(screen.getByDisplayValue('This bill is for…'), {
      target: { value: 'GOODS' },
    });
    const receiptSelect = await screen.findByDisplayValue('Pick the goods receipt…');
    fireEvent.change(receiptSelect, { target: { value: 'DC-OTC-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(apis.vendorApApi.createBill).toHaveBeenCalledTimes(1));
    const [vendorId, payload] = apis.vendorApApi.createBill.mock.calls[0];
    expect(vendorId).toBe('V-77');
    expect(payload.bill_kind).toBe('GOODS');
    expect(payload.grn_id).toBe('DC-OTC-1');
    expect(payload.taxable_amount).toBe(50000);
    expect(payload.total_amount).toBe(52500);
  });

  it('SERVICES books header-only with bill_kind and no grn_id', async () => {
    await openBillForm();
    fillHeader();
    fireEvent.change(screen.getByDisplayValue('This bill is for…'), {
      target: { value: 'SERVICES' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(apis.vendorApApi.createBill).toHaveBeenCalledTimes(1));
    const [, payload] = apis.vendorApApi.createBill.mock.calls[0];
    expect(payload.bill_kind).toBe('SERVICES');
    expect(payload.grn_id).toBeUndefined();
    expect(apis.vendorApApi.listReceipts).not.toHaveBeenCalled();
  });

  it('a receipts endpoint that is down shows the error state, not an empty list', async () => {
    apis.vendorApApi.listReceipts.mockResolvedValue(null); // listReceipts maps failures to null
    await openBillForm();
    fireEvent.change(screen.getByDisplayValue('This bill is for…'), {
      target: { value: 'GOODS' },
    });
    await screen.findByText(/Could not load this vendor/);
  });
});
