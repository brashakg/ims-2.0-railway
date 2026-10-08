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

// Only the axios instance is stubbed; buildApiError stays the real transform.
vi.mock('../../../services/api/client', async (orig) => ({
  ...(await orig<typeof import('../../../services/api/client')>()),
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));
// The accountant's shop (the top-bar picker) and role; a test may switch
// either. hasRole is AuthContext's own rule (SUPERADMIN/ADMIN pass, else any
// asked role held), so a test asks WHICH roles get a control, not a boolean.
const auth = vi.hoisted(() => ({ store: 'S1', role: 'ACCOUNTANT' }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { activeStoreId: auth.store, roles: [auth.role] },
    hasRole: (r: string | string[]) =>
      ['SUPERADMIN', 'ADMIN'].includes(auth.role) || [r].flat().includes(auth.role),
  }),
}));

import type { AxiosError } from 'axios';
import api, { buildApiError } from '../../../services/api/client';
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

const tab = (path = '/purchase/invoices') => (
  <MemoryRouter initialEntries={[path]}>
    <PurchaseInvoicesTab suppliers={[{ id: 'V1', name: 'Mumbai Lens House', gstNumber: '27ABCDE1234F1Z5' }] as never} />
  </MemoryRouter>
);
const renderTab = (path?: string) => render(tab(path));

beforeEach(() => {
  vi.clearAllMocks();
  auth.store = 'S1';
  auth.role = 'ACCOUNTANT';
  routeGets();
  routePosts();
});

// The manual form: Services, the Maharashtra supplier, one line.
async function openManualServicesBill(line: { name: string; qty: string; price: string; rate: string }) {
  const view = renderTab();
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
  return view;
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

  it('a receipt with no supplier invoice date opens on the IST day and books it', async () => {
    // The draft carries invoice_date null; the box opened blank (`'' ?? today`
    // is ''), the bill booked with invoice_date '' and no due date. Then it
    // opened on the UTC day: at 01:00 IST on 1 October that is 30 September,
    // so the bill fell into September's GSTR-3B (or its lock).
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-09-30T19:30:00Z')); // 2026-10-01 01:00 IST
    try {
      routeGets({ '/vendors/purchase-invoices/from-grn/G1': { ...GRN_DRAFT, invoice_date: null } });
      renderTab('/purchase/invoices?grn_id=G1');
      await screen.findByText(/Inter-state supply:/);
      expect((document.querySelector('input[type="date"]') as HTMLInputElement).value).toBe('2026-10-01');

      fireEvent.click(screen.getByRole('button', { name: /Book invoice/i }));
      await waitFor(() => expect(createCalls()).toHaveLength(1));
      expect(createCalls()[0][1].invoice_date).toBe('2026-10-01');
    } finally {
      vi.useRealTimers();
    }
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

describe('panel round 4 - every door hands its lines on, and the list follows the drawer', () => {
  // GET /vendors/grn answers by what is asked: accepted receipts, or open DCs.
  function routeReceipts(rows: { grns: unknown[] }, dcs: { grns: unknown[] }, extra: Record<string, unknown> = {}) {
    routeGets(extra);
    const base = mockGet.getMockImplementation() as (url: string, cfg?: unknown) => Promise<unknown>;
    mockGet.mockImplementation(async (url: string, cfg?: { params?: Record<string, unknown> }) => {
      if (url !== '/vendors/grn') return base(url, cfg);
      if (cfg?.params?.grn_subtype === 'DELIVERY_CHALLAN') return { data: dcs };
      return { data: cfg?.params?.status === 'ACCEPTED' ? rows : { grns: [] } };
    });
  }

  it('Create from GRN: the picked receipt opens with its products, HSNs and quantities', async () => {
    routeReceipts(
      { grns: [{ grn_id: 'G1', grn_number: 'RCPT 0004', vendor_id: 'V1', vendor_name: 'Mumbai Lens House', status: 'ACCEPTED', total_accepted: 3 }] },
      { grns: [] },
      { '/vendors/purchase-invoices/from-grn/G1': GRN_DRAFT },
    );
    renderTab();
    fireEvent.click(await screen.findByRole('button', { name: /Create from GRN/ }));
    const picker = within((await screen.findByText('Pick an accepted GRN to invoice')).closest('.fixed') as HTMLElement);
    fireEvent.click(await picker.findByRole('button', { name: /Invoice/ }));

    expect(await screen.findByDisplayValue('Carrera CA 8895 807')).toBeTruthy();
    expect(screen.getByDisplayValue('9003')).toBeTruthy();
    expect(screen.getByDisplayValue('3')).toBeTruthy();
  });

  it.each(['the GRN picker', 'the ?grn_id= link'])(
    'a refused receipt draft opens no form from %s, only the reason',
    async (door) => {
      // The draft door refuses on purpose (the receipt's shop has no company):
      // the picker opened 'Invoice from GRN' with one blank line anyway, and
      // the preview and Book then hit the same 422.
      const refusal = buildApiError({
        message: 'Request failed with status code 422',
        response: {
          status: 422,
          data: { detail: { code: 'RECIPIENT_UNRESOLVED', message: 'Shop S9 has no company set. Set it in Settings, then bill this receipt.' } },
        },
      } as unknown as AxiosError<never>);
      routeReceipts(
        { grns: [{ grn_id: 'G1', grn_number: 'RCPT 0004', vendor_id: 'V1', vendor_name: 'Mumbai Lens House', status: 'ACCEPTED', total_accepted: 3, store_id: 'S9' }] },
        { grns: [] },
      );
      const base = mockGet.getMockImplementation() as (url: string, cfg?: unknown) => Promise<unknown>;
      mockGet.mockImplementation(async (url: string, cfg?: unknown) => {
        if (url === '/vendors/purchase-invoices/from-grn/G1') throw refusal;
        return base(url, cfg);
      });
      if (door === 'the GRN picker') {
        renderTab();
        fireEvent.click(await screen.findByRole('button', { name: /Create from GRN/ }));
        const picker = within((await screen.findByText('Pick an accepted GRN to invoice')).closest('.fixed') as HTMLElement);
        fireEvent.click(await picker.findByRole('button', { name: /Invoice/ }));
      } else {
        renderTab('/purchase/invoices?grn_id=G1');
      }

      await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith(expect.stringMatching(/has no company set/)));
      expect(toastMock.warning).not.toHaveBeenCalled();
      expect(screen.queryByText(/Invoice from GRN/)).toBeNull();
      expect(screen.queryByRole('button', { name: /Book invoice/i })).toBeNull();
      expect(previewCalls()).toHaveLength(0);
    },
  );

  it('Match DCs to Invoice: the draft opens with the challans\' products, HSNs and quantities', async () => {
    routeReceipts(
      { grns: [] },
      { grns: [{ grn_id: 'D1', dc_number: 'DC-7', vendor_id: 'V1', vendor_name: 'Mumbai Lens House', dc_date: '2026-09-10', total_accepted: 2, store_id: 'S1' }] },
      {
        '/vendors/purchase-invoices/from-dcs': {
          status: 'DRAFT', vendor_id: 'V1', vendor_name: 'Mumbai Lens House', vendor_gstin: '27ABCDE1234F1Z5',
          recipient_gstin: '20AAFCB6528A1ZD', linked_dc_ids: ['D1'],
          lines: [{ product_id: 'P2', description: 'Ray-Ban RB3025 L0205', hsn: '9004', qty: 2, unit_price: 5000, gst_rate: 18 }],
        },
      },
    );
    renderTab();
    fireEvent.click(await screen.findByRole('button', { name: /Match DCs to Invoice/ }));
    fireEvent.click(await screen.findByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: /Generate Draft Invoice \(1\)/ }));

    expect(await screen.findByDisplayValue('Ray-Ban RB3025 L0205')).toBeTruthy();
    expect(screen.getByDisplayValue('9004')).toBeTruthy();
    expect(screen.getByDisplayValue('2')).toBeTruthy();
  });

  it('the detail drawer Approve takes the bill off hold in the list behind it', async () => {
    // Without it the hold card kept its Approve door, and pressing it again was
    // a 400 ('Only an invoice on ON_HOLD_EXCEPTION can be exception-approved').
    mockPost.mockResolvedValue({ data: { match_status: 'MATCHED_OVERRIDE' } });
    renderTab();
    expect(await screen.findByText('On hold')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /View detail/ }));
    fireEvent.change(await screen.findByPlaceholderText(/Why release this invoice for payment/), {
      target: { value: 'Supplier price rise agreed by phone' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Approve exception/ }));
    await waitFor(() => expect(toastMock.success).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole('button', { name: 'Close' }));

    await waitFor(() => expect(screen.queryByPlaceholderText(/Why release this invoice for payment/)).toBeNull());
    expect(screen.queryByRole('button', { name: /^Approve$/ })).toBeNull();
    expect(screen.queryByText('On hold')).toBeNull();
    expect(screen.getByText('Override approved')).toBeTruthy();
  });

  it("a booked bill's detail drawer shows its own lines (stored as description / hsn / qty / taxable)", async () => {
    const SVC = {
      bill_id: 'b-svc-1', invoice_id: 'b-svc-1', doc_type: 'PURCHASE_INVOICE', vendor_id: 'V1',
      vendor_name: 'Mumbai Lens House', invoice_number: 'FR-9', invoice_date: '2026-09-10', bill_kind: 'SERVICES',
      taxable_amount: 1000.11, tax_amount: 120.01, igst_total: 120.01, total_amount: 1120.12, interstate: true,
      lines: [{ description: 'Freight to Ranchi', hsn: '9965', qty: 3, unit_price: 333.37, taxable: 1000.11,
        gst_rate: 12, cgst: 0, sgst: 0, igst: 120.01, line_total: 1120.12 }],
    };
    routeGets({
      '/vendors/purchase-invoices': { purchase_invoices: [SVC], total: 1 },
      '/vendors/purchase-invoices/b-svc-1/match': {},
    });
    renderTab();
    fireEvent.click(await screen.findByTitle('View 3-way match detail'));
    const table = within((await screen.findByText('Invoice lines')).parentElement as HTMLElement);
    const cells = table.getAllByRole('cell').map((c) => c.textContent);
    expect(cells).toEqual(['Freight to Ranchi', '9965', '3', '₹333.37', '12%', '₹1,000.11']);
  });

  it('a PRODUCT_NOT_CATALOGUED refusal on Book asks the cataloguer for the named products', async () => {
    const refusal = buildApiError({
      message: 'Request failed with status code 422',
      response: {
        status: 422,
        data: {
          detail: {
            code: 'PRODUCT_NOT_CATALOGUED',
            message: 'Carrera CA 8895 807 is still missing Selling Price. Finish cataloguing it, then book the bill.',
            lines: [{ product_id: 'P1', product: 'Carrera CA 8895 807', missing: ['Selling Price'] }],
          },
        },
      },
    } as unknown as AxiosError<never>);
    mockPost.mockImplementation(async (url: string) => {
      if (url.endsWith('/preview')) return { data: preview() };
      if (url === '/vendors/purchase-invoices') throw refusal;
      return { data: { requested: [] } };
    });
    routeGets({ '/vendors/purchase-invoices/from-grn/G1': GRN_DRAFT });
    renderTab('/purchase/invoices?grn_id=G1');
    await screen.findByText(/Inter-state supply:/);
    fireEvent.click(screen.getByRole('button', { name: /Book invoice/i }));

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith('/vendors/purchase-invoices/request-cataloguing', {
        product_ids: ['P1'],
        note: undefined,
      }),
    );
    expect(toastMock.error).toHaveBeenCalledWith(expect.stringMatching(/still missing Selling Price/));
  });

  it('the form asks again when the shop changes, and Book sends the shop it asked about', async () => {
    // A bill with no receipt is booked for store_id's company; the preview key
    // left the shop out, so a switched shop kept the old shop's figures.
    const view = await openManualServicesBill({ name: 'Freight', qty: '1', price: '1000', rate: '18' });
    await screen.findByText(/Inter-state supply:/);
    expect(previewCalls().at(-1)?.[1].store_id).toBe('S1');
    const asked = previewCalls().length;
    auth.store = 'PUNE';
    view.rerender(tab());
    await waitFor(() => expect(previewCalls().length).toBeGreaterThan(asked));
    expect(previewCalls().at(-1)?.[1].store_id).toBe('PUNE');
    await waitFor(() => expect(screen.getByRole('button', { name: /Book invoice/i })).not.toBeDisabled());
    fireEvent.click(screen.getByRole('button', { name: /Book invoice/i }));
    await waitFor(() => expect(createCalls()).toHaveLength(1));
    expect(createCalls()[0][1].store_id).toBe('PUNE');
  });
});

describe('round 11 - a bill with no tax head is not shown as CGST+SGST', () => {
  it('an old Cash Flow "+ bill" reads "Not set"; a bill with heads keeps its badge', async () => {
    const legacy = {
      bill_id: 'b-legacy', bill_number: 'CF-1', bill_date: '2026-08-20',
      taxable_amount: 1000, tax_amount: 180, total_amount: 1180,
    };
    const intra = {
      bill_id: 'b-intra', invoice_number: 'IN-1', invoice_date: '2026-08-21', taxable_amount: 1000,
      tax_amount: 180, cgst_total: 90, sgst_total: 90, igst_total: 0, interstate: false, total_amount: 1180,
    };
    routeGets({ '/vendors/purchase-invoices': { purchase_invoices: [legacy, intra, HELD_BILL], total: 3 } });
    renderTab();
    await screen.findByText('Not set');
    expect(screen.getAllByText('CGST+SGST')).toHaveLength(1);
    expect(screen.getAllByText('IGST').length).toBeGreaterThan(0);
    const row = screen.getByText('CF-1').closest('tr') as HTMLElement;
    expect(within(row).getByText('Not set')).toBeTruthy();
    expect(within(row).queryByText('CGST+SGST')).toBeNull();
  });
});

describe('round 14 - the credit verdict is visible and settable', () => {
  it('shows "no valid GSTIN" when the preview says no credit, and nothing when it says credit', async () => {
    routePosts(preview({ itc_eligible: false }));
    await openManualServicesBill({ name: 'Freight', qty: '1', price: '1000', rate: '18' });
    expect(await screen.findByText('No input credit: the supplier has no valid GSTIN')).toBeTruthy();
    expect(screen.queryByText('No input credit: switched off')).toBeNull();
  });

  it('shows no line when the preview says the credit is claimable', async () => {
    routePosts(preview({ itc_eligible: true }));
    await openManualServicesBill({ name: 'Freight', qty: '1', price: '1000', rate: '18' });
    await screen.findByText(/Inter-state supply:/);
    expect(screen.queryByText(/No input credit/)).toBeNull();
  });

  it.each([
    ['absent', {}],
    ['null', { itc_eligible: null }],
  ])('shows no "No input credit" line when the preview leaves itc_eligible %s', async (_label, over) => {
    // Only an explicit false is a denial; a preview that says nothing is not one.
    routePosts(preview(over));
    await openManualServicesBill({ name: 'Freight', qty: '1', price: '1000', rate: '18' });
    await screen.findByText(/Inter-state supply:/);
    expect(screen.queryByText(/No input credit/)).toBeNull();
  });

  it('switching credit off says "switched off", re-asks the preview and books with itc_eligible false', async () => {
    routePosts(preview());
    await openManualServicesBill({ name: 'Freight', qty: '1', price: '1000', rate: '18' });
    await screen.findByText(/Inter-state supply:/);
    expect(previewCalls().at(-1)?.[1].itc_eligible).toBe(true);
    expect(previewCalls().at(-1)?.[1].reverse_charge).not.toBe(true);

    routePosts(preview({ itc_eligible: false }));
    const before = previewCalls().length;
    fireEvent.click(screen.getByRole('switch', { name: /Claim input credit/ }));
    expect(await screen.findByText('No input credit: switched off')).toBeTruthy();
    expect(previewCalls().length).toBeGreaterThan(before);
    const asked = previewCalls().at(-1)?.[1];
    expect(asked.itc_eligible).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: /Book invoice/i }));
    await waitFor(() => expect(createCalls()).toHaveLength(1));
    expect(createCalls()[0][1]).toEqual(asked);
    expect(createCalls()[0][1].itc_eligible).toBe(false);
  });

  it('an accounts role ticks Reverse charge: asked, shown as what the supplier is owed, and booked', async () => {
    auth.role = 'ACCOUNTANT';
    // Freight Rs 1000 @ 18% under reverse charge, as the server answers it.
    const rcm = preview({
      reverse_charge: true,
      lines: [{ taxable: 1000, gst_rate: 18, cgst: 0, sgst: 0, igst: 180, line_total: 1180 }],
      taxable_total: 1000, igst_total: 180, tax_total: 180, total: 1000,
    });
    routePosts(preview());
    await openManualServicesBill({ name: 'Freight', qty: '1', price: '1000', rate: '18' });
    await screen.findByText(/Inter-state supply:/);
    expect(previewCalls().at(-1)?.[1].reverse_charge).toBe(false);
    expect(screen.queryByText(/You pay this GST/)).toBeNull();

    routePosts(rcm);
    const before = previewCalls().length;
    fireEvent.click(screen.getByRole('switch', { name: /Reverse charge/ }));
    expect(
      await screen.findByText('You pay this GST (₹180) to the government; the supplier is owed ₹1,000'),
    ).toBeTruthy();
    expect(previewCalls().length).toBeGreaterThan(before);
    const asked = previewCalls().at(-1)?.[1];
    expect(asked.reverse_charge).toBe(true);
    expect(form().getByText('Supplier is owed')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /Book invoice/i }));
    await waitFor(() => expect(createCalls()).toHaveLength(1));
    expect(createCalls()[0][1]).toEqual(asked);
    expect(createCalls()[0][1].reverse_charge).toBe(true);
  });

  it.each(['STORE_MANAGER', 'AREA_MANAGER'])('%s (outside accounts) sees no Reverse charge option and never sends it', async (role) => {
    auth.role = role;
    routePosts(preview());
    await openManualServicesBill({ name: 'Freight', qty: '1', price: '1000', rate: '18' });
    await screen.findByText(/Inter-state supply:/);
    expect(screen.queryByRole('switch', { name: /Reverse charge/ })).toBeNull();
    expect(screen.queryByText(/Reverse charge/i)).toBeNull();
    expect(previewCalls().at(-1)?.[1].reverse_charge).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: /Book invoice/i }));
    await waitFor(() => expect(createCalls()).toHaveLength(1));
    expect(createCalls()[0][1].reverse_charge).toBe(false);
  });

  it('the list and the detail drawer badge a reverse-charge bill and show what the supplier is owed', async () => {
    routeGets({
      '/vendors/purchase-invoices': {
        purchase_invoices: [
          { ...HELD_BILL, reverse_charge: true, total_amount: 9300, tax_amount: 465 },
          { ...HELD_BILL, bill_id: 'b-ok', invoice_id: 'b-ok', invoice_number: 'MLH-78' },
        ],
        total: 2,
      },
    });
    renderTab();
    expect(await screen.findAllByText('Reverse charge')).toHaveLength(1);
    expect(screen.getAllByText('₹9,300').length).toBeGreaterThan(0);
    // Visible text, not a tooltip (a touch tablet shows none): the row says
    // what the shop pays the government and what the supplier is owed.
    const note = 'You pay this GST (₹465) to the government; the supplier is owed ₹9,300';
    expect(screen.getAllByText(note)).toHaveLength(1);
    expect(screen.getAllByText('Supplier is owed')).toHaveLength(1);
    fireEvent.click(screen.getAllByRole('button', { name: /View detail/ })[0]);
    await waitFor(() => expect(screen.getAllByText('Reverse charge')).toHaveLength(2));
    expect(screen.getAllByText(note)).toHaveLength(2);
  });

  it('the list and the detail drawer badge a stored itc_eligible=false bill "No credit"', async () => {
    routeGets({
      '/vendors/purchase-invoices': {
        purchase_invoices: [
          { ...HELD_BILL, itc_eligible: false },
          { ...HELD_BILL, bill_id: 'b-ok', invoice_id: 'b-ok', invoice_number: 'MLH-78', itc_eligible: true },
        ],
        total: 2,
      },
    });
    renderTab();
    // The list: one badge, on the no-credit bill only.
    expect(await screen.findAllByText('No credit')).toHaveLength(1);
    // The drawer for that bill carries it too (list badge + drawer badge).
    fireEvent.click(screen.getAllByRole('button', { name: /View detail/ })[0]);
    await waitFor(() => expect(screen.getAllByText('No credit')).toHaveLength(2));
  });
  it.each([['absent', undefined], ['null', null]])(
    'a bill with itc_eligible %s is not badged "No credit" in the list or the drawer',
    async (_label, value) => {
      const bill: Record<string, unknown> = { ...HELD_BILL };
      delete bill.itc_eligible;
      if (value === null) bill.itc_eligible = null;
      routeGets({ '/vendors/purchase-invoices': { purchase_invoices: [bill], total: 1 } });
      renderTab();
      await screen.findAllByRole('button', { name: /View detail/ });
      expect(screen.queryByText('No credit')).toBeNull();
      fireEvent.click(screen.getAllByRole('button', { name: /View detail/ })[0]);
      // The drawer is open once its Approve / detail content mounts; badge still absent.
      await screen.findAllByText(new RegExp(String(bill.invoice_number ?? 'MLH'), 'i'));
      expect(screen.queryByText('No credit')).toBeNull();
    },
  );
});
