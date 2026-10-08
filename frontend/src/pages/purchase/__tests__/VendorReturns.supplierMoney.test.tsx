// ============================================================================
// IMS 2.0 - Vendor Returns: the supplier credit is the accounts roles' alone
// ============================================================================
// Owner ruling 2026-10-01: "supplier balances should not be shown to anyone
// apart from admin superadmin and accountant". On a vendor return that is the
// credit the supplier owes us -- the return's value, the credit-note amount
// and number, the "Credit Value" total across returns -- and the GST debit
// note raised on the supplier: its amount, its print and its Tally voucher.
//
// Store and area managers keep the return itself (items, quantities, reasons,
// status, the debit-note serial). The server strips the money for them; this
// pins that the SCREEN never shows it either, even when an answer carries it,
// and never offers the Print / Export Tally doors the server now refuses them.
// A line's price per piece is that credit per piece (the return's value is
// qty x price), so it is hidden with the credit (panel 2026-10-08). The fixture
// prices the line so no line figure equals a credit figure.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ReactNode } from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
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

// The app's query cache: on All stores (an admin's default) each return card
// names its shop from the cached store list (review r3 #10).
const withCache = (node: ReactNode) => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    {node}
  </QueryClientProvider>
);

// A return the supplier has credited: value Rs 7,000, credit note CN-9 for
// Rs 5,000. The one line is 2 x Rs 3,000 (= Rs 6,000), so no item figure
// coincides with a credit figure.
const CREDITED = {
  return_id: 'VR1',
  vendor_id: 'V1',
  vendor_name: 'Acme Optics',
  store_id: 'BV-TEST-01',
  items: [{ product_id: 'P1', product_name: 'RB Frame', quantity: 2, reason: 'defective', unit_price: 3000 }],
  return_type: 'credit_note',
  status: 'received_by_vendor',
  total_value: 7000,
  credit_note_number: 'CN-9',
  credit_note_amount: 5000,
  created_at: '2026-09-29T10:00:00',
  created_by: 'u1',
  notes: '',
};

// The GST debit note raised for it: grand total Rs 7,350.
const NOTE = {
  debit_note_id: 'DN-1',
  debit_note_number: 'DN/26-27/0001',
  rtv_ref_id: 'VR1',
  vendor: { name: 'Acme Optics' },
  is_inter_state: false,
  lines: [],
  totals: { taxable_paise: 700000, cgst_paise: 17500, sgst_paise: 17500, igst_paise: 0, tax_paise: 35000, grand_total_paise: 735000 },
  totals_rupees: { taxable: 7000, cgst: 175, sgst: 175, igst: 0, tax: 350, grand_total: 7350 },
};

function load() {
  apiMock.get.mockImplementation((url: string) =>
    Promise.resolve({ data: url.startsWith('/vendor-returns') ? { returns: [CREDITED] } : { vendors: [] } }),
  );
  dnMock.list.mockResolvedValue({ debit_notes: [NOTE], total: 1 });
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
  await screen.findByText('DN/26-27/0001');
}

describe('VendorReturns - a manager reads the return, not the supplier credit', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    load();
  });

  it.each([['STORE_MANAGER'], ['AREA_MANAGER']])(
    '%s: no Credit Value, no return value, no credit note, no debit-note amount, no Print / Tally',
    async (role) => {
      roles = [role];
      await openReturn();
      const body = document.body.textContent || '';
      // The return itself is all there.
      expect(screen.getByText('RB Frame')).toBeInTheDocument();
      expect(screen.getByText(/Qty: 2/)).toBeInTheDocument();
      // The supplier credit is not.
      expect(screen.queryByText('Credit Value')).not.toBeInTheDocument();
      expect(body).not.toContain('7,000');
      expect(body).not.toContain('5,000');
      expect(body).not.toContain('CN-9');
      expect(body).not.toContain('7,350');
      // ...nor the price per piece or the line total it rebuilds.
      expect(body).not.toContain('3,000');
      expect(body).not.toContain('6,000');
      expect(body).not.toMatch(/CGST\+SGST|IGST/);
      expect(screen.queryByRole('button', { name: 'Print' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Export Tally' })).not.toBeInTheDocument();
      expect(dnMock.fetchPrintHtml).not.toHaveBeenCalled();
      expect(dnMock.fetchTallyXml).not.toHaveBeenCalled();
    },
  );

  it('STORE_MANAGER: a debit note issued from the screen comes back without its amount', async () => {
    roles = ['STORE_MANAGER'];
    dnMock.list.mockResolvedValue({ debit_notes: [], total: 0 });
    dnMock.issue.mockResolvedValue({ debit_note: NOTE, idempotent: false });
    render(
      withCache(
        <MemoryRouter>
          <VendorReturns />
        </MemoryRouter>,
      ),
    );
    fireEvent.click(await screen.findByText('Acme Optics'));
    fireEvent.click(await screen.findByRole('button', { name: 'Issue Debit Note' }));
    await screen.findByText('DN/26-27/0001');
    expect(document.body.textContent).not.toContain('7,350');
    expect(screen.queryByRole('button', { name: 'Print' })).not.toBeInTheDocument();
  });
});

describe('VendorReturns - the accounts roles still read the supplier credit', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    load();
  });

  it.each([['ACCOUNTANT'], ['ADMIN'], ['SUPERADMIN']])(
    '%s: Credit Value, the return value, the credit note, the debit-note amount, Print and Tally',
    async (role) => {
      roles = [role];
      await openReturn();
      const body = document.body.textContent || '';
      expect(screen.getByText('Credit Value')).toBeInTheDocument();
      expect(body).toContain('₹5,000');
      expect(body).toContain('₹7,000');
      expect(body).toContain('CN-9');
      expect(body).toContain('₹7,350');
      expect(body).toContain('@ ₹3,000');
      expect(body).toContain('₹6,000');
      expect(screen.getByRole('button', { name: 'Print' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Export Tally' })).toBeInTheDocument();
    },
  );
});
