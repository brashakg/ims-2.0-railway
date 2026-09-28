// ============================================================================
// IMS 2.0 - Purchase Invoices: what the screen shows is what the server gets
// ============================================================================
// Procurement-audit findings, driven through the REAL api seam (only the axios
// client is mocked, so vendorAp's field mappers run exactly as in the app):
//   F7  both Approve buttons POSTed .../undefined/approve-exception and the
//       drawer GETs .../undefined/match: the list returns bill_id/invoice_id,
//       the screen reads purchase_invoice_id.
//   F37 invoice-from-GRN lines arrived blank with qty 1: the draft returns
//       description/hsn/qty, the form reads product_name/hsn_code/quantity.
//   F6  the tax head is the supplier's GSTIN vs ours -- the form shows that,
//       and sends no "place of supply" the server could read another way.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../services/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'S1', roles: ['ACCOUNTANT'] }, hasRole: () => true }),
}));

import api from '../../../services/api/client';
import { PurchaseInvoicesTab } from '../PurchaseInvoicesTab';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;

// A bill exactly as GET /vendors/purchase-invoices returns it (vendor_bills doc).
const HELD_BILL = {
  bill_id: 'b-held-1',
  invoice_id: 'b-held-1',
  doc_type: 'PURCHASE_INVOICE',
  vendor_id: 'V1',
  vendor_name: 'Mumbai Lens House',
  invoice_number: 'MLH-77',
  invoice_date: '2026-09-10',
  total_amount: 9765,
  tax_amount: 465,
  igst_total: 465,
  interstate: true,
  match_status: 'ON_HOLD_EXCEPTION',
  match_detail: { match_status: 'ON_HOLD_EXCEPTION', lines: [], exceptions: ['Price 5% over the PO'] },
};

// GET /from-grn/{id} exactly as the server drafts it.
const GRN_DRAFT = {
  status: 'DRAFT',
  vendor_id: 'V1',
  vendor_name: 'Mumbai Lens House',
  vendor_gstin: '27ABCDE1234F1Z5',
  recipient_entity_id: 'E1',
  recipient_gstin: '20AAFCB6528A1ZD',
  place_of_supply: '27', // the SUPPLIER state (the ITC-register key)
  supply_place_recipient: '20',
  interstate: true,
  invoice_number: 'MLH-77',
  invoice_date: '2026-09-10',
  po_id: 'PO1',
  grn_id: 'G1',
  grn_number: 'RCPT 0004',
  lines: [
    { product_id: 'P1', description: 'Carrera CA 8895 807', hsn: '9003', qty: 3, unit_price: 3100, gst_rate: 5 },
  ],
};

function routeGets(extra: Record<string, unknown> = {}) {
  mockGet.mockImplementation(async (url: string) => {
    if (url in extra) return { data: extra[url] };
    if (url === '/vendors/purchase-invoices') return { data: { purchase_invoices: [HELD_BILL], total: 1 } };
    if (url.endsWith('/match')) return { data: { match_status: 'ON_HOLD_EXCEPTION', match_detail: HELD_BILL.match_detail } };
    return { data: null };
  });
}

function renderTab(path = '/purchase/invoices') {
  render(
    <MemoryRouter initialEntries={[path]}>
      <PurchaseInvoicesTab suppliers={[{ id: 'V1', name: 'Mumbai Lens House', gstNumber: '27ABCDE1234F1Z5' }] as never} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  routeGets();
});

describe('F7 - the Approve doors reach the real bill', () => {
  it('the hold card Approve posts to the bill id, not undefined', async () => {
    mockPost.mockResolvedValue({ data: { match_status: 'MATCHED_OVERRIDE' } });
    renderTab();
    fireEvent.click(await screen.findByRole('button', { name: /^Approve$/ }));
    fireEvent.change(screen.getByPlaceholderText(/Why release this invoice/), {
      target: { value: 'Supplier price rise agreed by phone' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Approve exception/ }));
    await waitFor(() => expect(mockPost).toHaveBeenCalled());
    expect(mockPost.mock.calls[0][0]).toBe('/vendors/purchase-invoices/b-held-1/approve-exception');
  });

  it('the detail drawer fetches the match for the bill id', async () => {
    renderTab();
    fireEvent.click(await screen.findByRole('button', { name: /View detail/ }));
    await waitFor(() =>
      expect(mockGet).toHaveBeenCalledWith('/vendors/purchase-invoices/b-held-1/match'),
    );
    expect(mockGet.mock.calls.map((c) => c[0]).join(' ')).not.toContain('undefined');
  });
});

describe('F37 + F6 - invoice from a goods receipt', () => {
  it('lines carry the receipt products, HSNs and accepted quantities; the booking sends them', async () => {
    routeGets({ '/vendors/purchase-invoices/from-grn/G1': GRN_DRAFT });
    mockPost.mockResolvedValue({ data: {} });
    renderTab('/purchase/invoices?grn_id=G1');

    expect(await screen.findByDisplayValue('Carrera CA 8895 807')).toBeTruthy();
    expect(screen.getByDisplayValue('9003')).toBeTruthy();
    expect(screen.getByDisplayValue('3')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /Book invoice/i }));
    await waitFor(() => expect(mockPost).toHaveBeenCalled());
    const [url, wire] = mockPost.mock.calls[0];
    expect(url).toBe('/vendors/purchase-invoices');
    expect(wire.lines).toEqual([
      expect.objectContaining({ product_id: 'P1', description: 'Carrera CA 8895 807', hsn: '9003', qty: 3 }),
    ]);
  });

  it('shows IGST from the two GST numbers and sends no place of supply', async () => {
    routeGets({ '/vendors/purchase-invoices/from-grn/G1': GRN_DRAFT });
    mockPost.mockResolvedValue({ data: {} });
    renderTab('/purchase/invoices?grn_id=G1');

    expect(await screen.findByText(/Inter-state supply:/)).toBeTruthy();
    expect(screen.queryByPlaceholderText(/e\.g\. 27 or 27-Maharashtra/)).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /Book invoice/i }));
    await waitFor(() => expect(mockPost).toHaveBeenCalled());
    const wire = mockPost.mock.calls[0][1];
    expect(wire).not.toHaveProperty('place_of_supply');
    expect(wire.recipient_gstin).toBe('20AAFCB6528A1ZD');
  });
});
