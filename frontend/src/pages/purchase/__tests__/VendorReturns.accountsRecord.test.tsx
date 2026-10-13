// ============================================================================
// IMS 2.0 - Vendor Returns: prices and supplier-money actions are accounts-only
// ============================================================================
// Owner rulings 2026-10-12 (extending 2026-10-01 "supplier balances should not
// be shown to anyone apart from admin superadmin and accountant"):
//   R2  Recording supplier money is ADMIN / SUPERADMIN / ACCOUNTANT only: a
//       vendor return's credit_issued ("Issue Credit") and the RTV GST debit
//       note's issue ("Issue Debit Note"). Managers keep the goods side --
//       approve, ship, received by vendor, and "replaced" on a REPLACEMENT
//       return. On a credit-note return "replaced" closes the return for good
//       without the supplier's credit (forgoing supplier money), so a manager
//       is not offered it there -- left with only that button, one click
//       would strand the credit accounts still have to record.
//   R3  On a vendor return a manager (and any role outside PAYABLES_ROLES)
//       reads items, quantities and reasons only: no "@ Rs unit price", no
//       line value, no rate -- even when an answer carries them -- and the
//       screen renders cleanly when the server has already dropped them.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ReactNode } from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn() }));
const dnMock = vi.hoisted(() => ({
  list: vi.fn(),
  issue: vi.fn(),
  fetchPrintHtml: vi.fn(),
  fetchTallyXml: vi.fn(),
}));
let roles: string[] = ['STORE_MANAGER'];

vi.mock('../../../services/api/client', () => ({ default: apiMock }));
vi.mock('../../../services/api/rtvDebitNotes', () => ({ rtvDebitNotesApi: dnMock }));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'BV-TEST-01', roles } }),
}));

import { VendorReturns } from '../VendorReturns';

const withCache = (node: ReactNode) => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    {node}
  </QueryClientProvider>
);

// One line: 2 x Rs 3,000 = Rs 6,000, plus every other key a line's price can
// arrive under (rate / unit cost in rupees and paise). The supplier has the
// goods back and owes the credit; no debit note is issued yet.
const PRICED_LINE = {
  product_id: 'P1',
  product_name: 'RB Frame',
  quantity: 2,
  reason: 'defective',
  unit_price: 3000,
  unit_cost: 3000,
  unit_cost_paise: 300000,
  rate: 3000,
  rate_paise: 300000,
  line_expected_paise: 600000,
};
const AT_VENDOR = {
  return_id: 'VR1',
  vendor_id: 'V1',
  vendor_name: 'Acme Optics',
  store_id: 'BV-TEST-01',
  items: [PRICED_LINE],
  return_type: 'credit_note',
  status: 'received_by_vendor',
  total_value: 6000,
  credit_note_number: null,
  credit_note_amount: null,
  created_at: '2026-10-12T10:00:00',
  created_by: 'u1',
  notes: '',
};
// The same return as the server now sends it to a manager (payables_mask).
const AT_VENDOR_STRIPPED = {
  ...AT_VENDOR,
  items: [{ product_id: 'P1', product_name: 'RB Frame', quantity: 2, reason: 'defective' }],
  total_value: undefined,
  credit_note_number: undefined,
  credit_note_amount: undefined,
};

function load(returns: unknown[]) {
  apiMock.get.mockImplementation((url: string) =>
    Promise.resolve({ data: url.startsWith('/vendor-returns') ? { returns } : { vendors: [] } }),
  );
  dnMock.list.mockResolvedValue({ debit_notes: [], total: 0 });
}

async function openReturn() {
  render(
    withCache(
      <MemoryRouter>
        <VendorReturns />
      </MemoryRouter>,
    ),
  );
  fireEvent.click(await screen.findByText('Acme Optics'));
  await screen.findByText('RB Frame');
}

function expectItemsOnly() {
  const body = document.body.textContent || '';
  // The goods are all there: item, quantity, reason.
  expect(screen.getByText('RB Frame')).toBeInTheDocument();
  expect(screen.getByText(/Qty: 2/)).toBeInTheDocument();
  expect(screen.getByText('Defective')).toBeInTheDocument();
  // No price, no line value, no rate -- and nothing half-rendered.
  expect(body).not.toContain('₹');
  expect(body).not.toContain('@');
  expect(body).not.toContain('3,000');
  expect(body).not.toContain('6,000');
  expect(body).not.toMatch(/NaN|undefined/);
}

describe('VendorReturns - managers read the goods, not the prices, and record no supplier money', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    load([AT_VENDOR]);
  });

  it.each([['STORE_MANAGER'], ['AREA_MANAGER']])(
    '%s: items, quantities and reasons only, even when the answer carries the prices',
    async (role) => {
      roles = [role];
      await openReturn();
      expectItemsOnly();
    },
  );

  it.each([['STORE_MANAGER'], ['AREA_MANAGER']])(
    '%s: a credit-note return offers no Issue Credit, no Issue Debit Note and no Mark as Replaced',
    async (role) => {
      roles = [role];
      await openReturn();
      expect(screen.queryByRole('button', { name: 'Issue Credit' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Issue Debit Note' })).not.toBeInTheDocument();
      expect(screen.getByText("Accounts record the supplier's credit")).toBeInTheDocument();
      expect(screen.getByText('Accounts issue the debit note')).toBeInTheDocument();
      // 'replaced' would close this return without the credit the supplier
      // owes -- no button a manager can strand that credit with.
      expect(screen.queryByRole('button', { name: 'Mark as Replaced' })).not.toBeInTheDocument();
      expect(apiMock.patch).not.toHaveBeenCalled();
      expect(dnMock.issue).not.toHaveBeenCalled();
    },
  );

  it('STORE_MANAGER: an untyped (legacy) return is owed a credit too -- no Mark as Replaced', async () => {
    // The server reads a return without return_type as a credit return
    // (vendor_returns.update_return_status refuses a manager's 'replaced').
    roles = ['STORE_MANAGER'];
    load([{ ...AT_VENDOR, return_type: undefined }]);
    await openReturn();
    expect(screen.getByText('Credit Note')).toBeInTheDocument();
    expect(screen.queryByText('Replacement')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Mark as Replaced' })).not.toBeInTheDocument();
    expect(screen.getByText("Accounts record the supplier's credit")).toBeInTheDocument();
  });

  it.each([['STORE_MANAGER'], ['AREA_MANAGER']])(
    '%s: a replacement return keeps Mark as Replaced -- the goods coming back are theirs',
    async (role) => {
      roles = [role];
      load([{ ...AT_VENDOR, return_type: 'replacement' }]);
      apiMock.patch.mockResolvedValue({
        data: { return: { ...AT_VENDOR_STRIPPED, return_type: 'replacement', status: 'replaced' } },
      });
      await openReturn();
      expect(screen.queryByRole('button', { name: 'Issue Credit' })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Mark as Replaced' }));
      await waitFor(() =>
        expect(apiMock.patch).toHaveBeenCalledWith('/vendor-returns/VR1/status', { status: 'replaced' }),
      );
    },
  );

  it.each([
    ['created', 'Approve', 'approved'],
    ['approved', 'Mark as Shipped', 'shipped'],
    ['shipped', 'Received by Vendor', 'received_by_vendor'],
  ])('STORE_MANAGER keeps the goods side: a %s return offers %s', async (status, label, next) => {
    roles = ['STORE_MANAGER'];
    load([{ ...AT_VENDOR, status }]);
    apiMock.patch.mockResolvedValue({ data: { return: { ...AT_VENDOR, status: next } } });
    await openReturn();
    fireEvent.click(screen.getByRole('button', { name: label }));
    await waitFor(() =>
      expect(apiMock.patch).toHaveBeenCalledWith('/vendor-returns/VR1/status', { status: next }),
    );
  });

  it('STORE_MANAGER: the server-stripped return renders cleanly', async () => {
    roles = ['STORE_MANAGER'];
    load([AT_VENDOR_STRIPPED]);
    await openReturn();
    expectItemsOnly();
    expect(screen.getByText('1 item(s)')).toBeInTheDocument();
  });

  it('WORKSHOP_STAFF: items only -- no price, no value, no supplier-money action', async () => {
    roles = ['WORKSHOP_STAFF'];
    await openReturn();
    expectItemsOnly();
    expect(screen.queryByRole('button', { name: 'Issue Credit' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Issue Debit Note' })).not.toBeInTheDocument();
  });
});

describe('VendorReturns - the accounts roles read the prices and record the supplier money', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    load([AT_VENDOR]);
  });

  it.each([['ACCOUNTANT'], ['ADMIN'], ['SUPERADMIN']])(
    '%s: the unit price and line value, Issue Credit and Issue Debit Note',
    async (role) => {
      roles = [role];
      await openReturn();
      const body = document.body.textContent || '';
      expect(body).toContain('@ ₹3,000');
      expect(body).toContain('₹6,000');
      expect(screen.getByRole('button', { name: 'Issue Credit' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Issue Debit Note' })).toBeInTheDocument();
      expect(screen.queryByText("Accounts record the supplier's credit")).not.toBeInTheDocument();
      // Closing a credit-note return as replaced forgoes the credit: theirs to
      // decide (the server lets the accounts roles do it).
      expect(screen.getByRole('button', { name: 'Mark as Replaced' })).toBeInTheDocument();
    },
  );

  it('ACCOUNTANT: Issue Credit records credit_issued; Issue Debit Note issues the note', async () => {
    roles = ['ACCOUNTANT'];
    apiMock.patch.mockResolvedValue({
      data: { return: { ...AT_VENDOR, status: 'credit_issued', credit_note_number: 'CN-9', credit_note_amount: 6000 } },
    });
    dnMock.issue.mockResolvedValue({
      debit_note: {
        debit_note_id: 'DN-1',
        debit_note_number: 'DN/26-27/0001',
        rtv_ref_id: 'VR1',
        is_inter_state: false,
        lines: [],
        totals_rupees: { taxable: 6000, cgst: 150, sgst: 150, igst: 0, tax: 300, grand_total: 6300 },
      },
      idempotent: false,
    });
    await openReturn();
    fireEvent.click(screen.getByRole('button', { name: 'Issue Debit Note' }));
    await screen.findByText('DN/26-27/0001');
    expect(dnMock.issue).toHaveBeenCalledWith('VR1', 'vendor_return');
    expect(document.body.textContent).toContain('₹6,300');
    fireEvent.click(screen.getByRole('button', { name: 'Issue Credit' }));
    await waitFor(() =>
      expect(apiMock.patch).toHaveBeenCalledWith('/vendor-returns/VR1/status', { status: 'credit_issued' }),
    );
  });
});
