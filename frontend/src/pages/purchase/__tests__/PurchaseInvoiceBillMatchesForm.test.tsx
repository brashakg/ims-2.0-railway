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
//   Panel round 2: the form held its OWN copy of that rule and of the GST
//       math. It previewed CGST + SGST on a manual bill the server booked as
//       IGST, called a junk "88..." GSTIN inter-state, and rounded a paisa
//       differently. It now shows POST /preview (the booking's own math), and
//       both Approve doors are pressed through to the released status.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
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

// POST /preview exactly as the server answers it (the booking's own math).
function preview(over: Record<string, unknown> = {}) {
  return {
    vendor_gstin: '27ABCDE1234F1Z5',
    recipient_entity_id: 'E1',
    recipient_gstin: '20AAFCB6528A1ZD',
    supplier_state: '27',
    supply_place_recipient: '20',
    interstate: true,
    lines: [{ taxable: 9300, gst_rate: 5, cgst: 0, sgst: 0, igst: 465, line_total: 9765 }],
    taxable_total: 9300,
    cgst_total: 0,
    sgst_total: 0,
    igst_total: 465,
    tax_total: 465,
    total: 9765,
    ...over,
  };
}

// POSTs: /preview answers with `pv`; everything else with `other`.
function routePosts(pv: Record<string, unknown> = preview(), other: unknown = {}) {
  mockPost.mockImplementation(async (url: string) =>
    url.endsWith('/preview') ? { data: pv } : { data: other },
  );
}
// The booking form drawer only (the invoice list behind it has tax columns too).
const form = () => within(screen.getByText('New purchase invoice').closest('.fixed') as HTMLElement);
const createCalls = () => mockPost.mock.calls.filter((c) => c[0] === '/vendors/purchase-invoices');
const previewCalls = () => mockPost.mock.calls.filter((c) => String(c[0]).endsWith('/preview'));

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
  routePosts();
});

// The manual form: Services, the Maharashtra supplier, one line.
async function openManualServicesBill(line: { name: string; qty: string; price: string; rate: string }) {
  renderTab();
  fireEvent.click(await screen.findByRole('button', { name: /Manual invoice/i }));
  fireEvent.change(await screen.findByDisplayValue(/Choose: goods, or services/), { target: { value: 'SERVICES' } });
  fireEvent.change(screen.getByDisplayValue('Select supplier...'), { target: { value: 'V1' } });
  fireEvent.change(screen.getByPlaceholderText(/As printed on the supplier's bill/), { target: { value: 'FR-9' } });
  fireEvent.change(screen.getByPlaceholderText('Item description'), { target: { value: line.name } });
  const [qty, price] = Array.from(document.querySelectorAll('input[type="number"]')) as HTMLInputElement[];
  fireEvent.change(qty, { target: { value: line.qty } });
  fireEvent.change(price, { target: { value: line.price } });
  const rate = screen.getAllByRole('combobox').find((el) => (el as HTMLSelectElement).value === '5') as HTMLSelectElement;
  fireEvent.change(rate, { target: { value: line.rate } });
}

describe('F7 - the Approve doors reach the real bill', () => {
  it('the hold card Approve releases the bill: right id, success message, off hold', async () => {
    mockPost.mockResolvedValue({ data: { match_status: 'MATCHED_OVERRIDE' } });
    renderTab();
    fireEvent.click(await screen.findByRole('button', { name: /^Approve$/ }));
    fireEvent.change(screen.getByPlaceholderText(/Why release this invoice/), {
      target: { value: 'Supplier price rise agreed by phone' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Approve exception/ }));
    await waitFor(() => expect(mockPost).toHaveBeenCalled());
    expect(mockPost.mock.calls[0][0]).toBe('/vendors/purchase-invoices/b-held-1/approve-exception');
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith(expect.stringMatching(/released for payment/)));
    // Its status changed: the bill is no longer on hold, so no Approve door is left.
    await waitFor(() => expect(screen.queryByRole('button', { name: /^Approve$/ })).toBeNull());
  });

  it('the detail drawer fetches the match for the bill id and its Approve releases it', async () => {
    mockPost.mockResolvedValue({ data: { match_status: 'MATCHED_OVERRIDE' } });
    renderTab();
    fireEvent.click(await screen.findByRole('button', { name: /View detail/ }));
    await waitFor(() =>
      expect(mockGet).toHaveBeenCalledWith('/vendors/purchase-invoices/b-held-1/match'),
    );
    expect(mockGet.mock.calls.map((c) => c[0]).join(' ')).not.toContain('undefined');

    fireEvent.change(await screen.findByPlaceholderText(/Why release this invoice for payment/), {
      target: { value: 'Supplier price rise agreed by phone' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Approve exception/ }));
    await waitFor(() => expect(mockPost).toHaveBeenCalled());
    expect(mockPost.mock.calls[0][0]).toBe('/vendors/purchase-invoices/b-held-1/approve-exception');
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Exception approved - invoice released for payment'));
    expect(await screen.findByText('An exception was approved despite a variance.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Approve exception/ })).toBeNull();
  });
});

describe('F37 + F6 - invoice from a goods receipt', () => {
  it('lines carry the receipt products, HSNs and accepted quantities; the booking sends them', async () => {
    routeGets({ '/vendors/purchase-invoices/from-grn/G1': GRN_DRAFT });
    renderTab('/purchase/invoices?grn_id=G1');

    expect(await screen.findByDisplayValue('Carrera CA 8895 807')).toBeTruthy();
    expect(screen.getByDisplayValue('9003')).toBeTruthy();
    expect(screen.getByDisplayValue('3')).toBeTruthy();

    await screen.findByText(/Inter-state supply:/);
    fireEvent.click(screen.getByRole('button', { name: /Book invoice/i }));
    await waitFor(() => expect(createCalls()).toHaveLength(1));
    const [url, wire] = createCalls()[0];
    expect(url).toBe('/vendors/purchase-invoices');
    expect(wire.lines).toEqual([
      expect.objectContaining({ product_id: 'P1', description: 'Carrera CA 8895 807', hsn: '9003', qty: 3 }),
    ]);
  });

  it("shows the server's IGST and sends no place of supply", async () => {
    routeGets({ '/vendors/purchase-invoices/from-grn/G1': GRN_DRAFT });
    renderTab('/purchase/invoices?grn_id=G1');

    expect(await screen.findByText(/Inter-state supply:/)).toBeTruthy();
    expect(screen.queryByPlaceholderText(/e\.g\. 27 or 27-Maharashtra/)).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /Book invoice/i }));
    await waitFor(() => expect(createCalls()).toHaveLength(1));
    const wire = createCalls()[0][1];
    expect(wire).not.toHaveProperty('place_of_supply');
    expect(wire.recipient_gstin).toBe('20AAFCB6528A1ZD');
  });
});

describe('panel round 2 - every tax figure on the form is the server preview', () => {
  it('manual bill, Recipient GSTIN blank: shows the IGST and our GSTIN the server will book', async () => {
    // The panel's exact booking: the old form said CGST 90 + SGST 90 and
    // "booked as CGST + SGST until then"; the server stored IGST 180 on
    // 20AAFCB6528A1ZD (the accountant's shop's company).
    routePosts(preview({
      lines: [{ taxable: 1000, gst_rate: 18, cgst: 0, sgst: 0, igst: 180, line_total: 1180 }],
      taxable_total: 1000, igst_total: 180, tax_total: 180, total: 1180,
    }));
    await openManualServicesBill({ name: 'Freight', qty: '1', price: '1000', rate: '18' });

    expect(await screen.findByText(/Inter-state supply:/)).toBeTruthy();
    expect(screen.getByText(/Left blank: booked on ours, 20AAFCB6528A1ZD/)).toBeTruthy();
    expect(screen.queryByText(/booked as CGST \+ SGST until then/)).toBeNull();
    expect(form().getAllByText('IGST')).toHaveLength(3); // the banner, the column, the total
    expect(form().getAllByText('₹180').length).toBeGreaterThan(0);
    expect(form().queryByText('CGST')).toBeNull();

    // The preview was asked for exactly the body Book then sends.
    const asked = previewCalls().at(-1)?.[1];
    expect(asked.recipient_gstin).toBeUndefined();
    fireEvent.click(screen.getByRole('button', { name: /Book invoice/i }));
    await waitFor(() => expect(createCalls()).toHaveLength(1));
    expect(createCalls()[0][1]).toEqual(asked);
  });

  it('a junk "88..." supplier GSTIN reads as the server books it (CGST + SGST), not IGST', async () => {
    routeGets();
    routePosts(preview({
      vendor_gstin: '88AABCU9603R1ZF',
      supplier_state: null,
      interstate: false,
      lines: [{ taxable: 1000, gst_rate: 18, cgst: 90, sgst: 90, igst: 0, line_total: 1180 }],
      taxable_total: 1000, cgst_total: 90, sgst_total: 90, igst_total: 0, tax_total: 180, total: 1180,
    }));
    render(
      <MemoryRouter>
        <PurchaseInvoicesTab suppliers={[{ id: 'V1', name: 'Legacy Vendor', gstNumber: '88AABCU9603R1ZF' }] as never} />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole('button', { name: /Manual invoice/i }));
    fireEvent.change(await screen.findByDisplayValue(/Choose: goods, or services/), { target: { value: 'SERVICES' } });
    fireEvent.change(screen.getByDisplayValue('Select supplier...'), { target: { value: 'V1' } });
    fireEvent.change(screen.getByPlaceholderText('Item description'), { target: { value: 'Freight' } });
    fireEvent.change(screen.getByPlaceholderText(/GSTIN receiving the supply/), { target: { value: '20AAFCB6528A1ZD' } });

    expect(await screen.findByText(/the supplier's GSTIN names no state/)).toBeTruthy();
    expect(screen.queryByText(/Inter-state supply/)).toBeNull();
    expect(form().getByText('CGST')).toBeTruthy();
    expect(form().queryByText('IGST')).toBeNull();
  });

  it('shows the paisa the server stores (CGST 25.02 + SGST 25.03), not tax/2 twice', async () => {
    routePosts(preview({
      supplier_state: '20', interstate: false,
      lines: [{ taxable: 1001, gst_rate: 5, cgst: 25.02, sgst: 25.03, igst: 0, line_total: 1051.05 }],
      taxable_total: 1001, cgst_total: 25.02, sgst_total: 25.03, igst_total: 0, tax_total: 50.05, total: 1051.05,
    }));
    await openManualServicesBill({ name: 'Freight', qty: '1', price: '1001', rate: '5' });

    expect(await form().findByText('₹25.02')).toBeTruthy();
    expect(form().getByText('₹25.03')).toBeTruthy();
    expect(form().getByText('₹1,051.05')).toBeTruthy();
  });

  it('Book waits until the figures on screen are for the form as it stands', async () => {
    let answer: (v: unknown) => void = () => {};
    mockPost.mockImplementation((url: string) =>
      url.endsWith('/preview') ? new Promise((r) => { answer = r; }) : Promise.resolve({ data: {} }),
    );
    await openManualServicesBill({ name: 'Freight', qty: '1', price: '1000', rate: '18' });
    await waitFor(() => expect(previewCalls().length).toBeGreaterThan(0));
    expect(screen.getByRole('button', { name: /Book invoice/i })).toBeDisabled();
    answer({ data: preview() });
    await waitFor(() => expect(screen.getByRole('button', { name: /Book invoice/i })).not.toBeDisabled());
  });
});
