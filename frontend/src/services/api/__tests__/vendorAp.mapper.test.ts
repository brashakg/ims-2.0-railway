// ============================================================================
// IMS 2.0 - vendorAp.ts field-mapper tests
// ============================================================================
// These mappers are the FE<->BE seam that broke in prod: the stored
// vendor_bills / purchase_invoice doc uses invoice_number / invoice_date /
// cgst_total / sgst_total / igst_total / interstate, while the FE list + drawer
// read vendor_invoice_no / vendor_invoice_date / cgst / sgst / igst /
// is_interstate. And create() must translate the FE payload into the backend
// PurchaseInvoiceCreate wire schema (invoice_number / invoice_date +
// per-line description / qty / hsn / taxable) or the POST 422s.

import { vi, beforeEach, describe, it, expect } from 'vitest';

// Mock the axios client (default export) so we control the wire responses and
// can capture exactly what create() POSTs.
vi.mock('../client', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

import api from '../client';
import { purchaseInvoicesApi } from '../vendorAp';
import type { PurchaseInvoiceCreate } from '../vendorAp';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
});

describe('purchaseInvoicesApi.list -> mapInvoiceFromApi', () => {
  it('maps backend doc fields onto the FE-facing keys', async () => {
    const backendDoc = {
      purchase_invoice_id: 'pi_1',
      vendor_id: 'v_1',
      invoice_number: 'SUP-INV-0007',
      invoice_date: '2026-05-01',
      cgst_total: 90,
      sgst_total: 90,
      igst_total: 0,
      interstate: false,
      taxable_amount: 1000,
    };
    mockGet.mockResolvedValue({
      data: { purchase_invoices: [backendDoc], total: 1 },
    });

    const result = await purchaseInvoicesApi.list();

    expect(mockGet).toHaveBeenCalledWith('/vendors/purchase-invoices', { params: undefined });
    expect(result.total).toBe(1);
    expect(result.purchase_invoices).toHaveLength(1);

    const row = result.purchase_invoices[0];
    // The supplier invoice number/date come from invoice_number / invoice_date.
    expect(row.vendor_invoice_no).toBe('SUP-INV-0007');
    expect(row.vendor_invoice_date).toBe('2026-05-01');
    // GST split is read off cgst_total / sgst_total / igst_total.
    expect(row.cgst).toBe(90);
    expect(row.sgst).toBe(90);
    expect(row.igst).toBe(0);
    // intra-state doc -> is_interstate false (echoed from `interstate`).
    expect(row.is_interstate).toBe(false);
  });

  it('falls back to bill_number/bill_date and infers interstate from igst > 0', async () => {
    // A header-only vendor_bill style doc: no invoice_number, no explicit
    // interstate flag, but a positive IGST total -> must be treated interstate.
    const billDoc = {
      bill_id: 'b_2',
      vendor_id: 'v_2',
      bill_number: 'BILL-22',
      bill_date: '2026-04-15',
      igst_total: 360,
      taxable_amount: 2000,
    };
    mockGet.mockResolvedValue({
      data: { purchase_invoices: [billDoc] },
    });

    const result = await purchaseInvoicesApi.list({ vendor_id: 'v_2' });

    expect(mockGet).toHaveBeenCalledWith('/vendors/purchase-invoices', { params: { vendor_id: 'v_2' } });
    const row = result.purchase_invoices[0];
    expect(row.vendor_invoice_no).toBe('BILL-22');
    expect(row.vendor_invoice_date).toBe('2026-04-15');
    expect(row.cgst).toBe(0); // absent -> defaults to 0
    expect(row.sgst).toBe(0);
    expect(row.igst).toBe(360);
    // interstate flag absent but igst > 0 -> inferred true.
    expect(row.is_interstate).toBe(true);
    // total falls back to row count when the envelope omits it.
    expect(result.total).toBe(1);
  });

  it('prefers already-mapped FE keys when both shapes are present', async () => {
    const doc = {
      vendor_invoice_no: 'ALREADY-MAPPED',
      invoice_number: 'RAW-SHOULD-NOT-WIN',
      vendor_invoice_date: '2026-06-01',
      invoice_date: '2026-01-01',
      cgst: 5,
      cgst_total: 999,
      is_interstate: false,
      igst: 10,
    };
    mockGet.mockResolvedValue({ data: { purchase_invoices: [doc], total: 1 } });

    const row = (await purchaseInvoicesApi.list()).purchase_invoices[0];
    expect(row.vendor_invoice_no).toBe('ALREADY-MAPPED');
    expect(row.vendor_invoice_date).toBe('2026-06-01');
    expect(row.cgst).toBe(5);
    // is_interstate already present (false) wins over the igst>0 inference.
    expect(row.is_interstate).toBe(false);
  });

  it('returns an empty envelope (fail-soft) when the GET rejects', async () => {
    mockGet.mockRejectedValue(new Error('404 not shipped'));
    const result = await purchaseInvoicesApi.list();
    expect(result).toEqual({ purchase_invoices: [], total: 0 });
  });

  it('gives every row the id the screen acts on (F7: Approve hit /undefined/)', async () => {
    // The list returns the stored vendor_bills doc: bill_id / invoice_id, no
    // purchase_invoice_id. Every Approve / match / recon door reads the latter.
    mockGet.mockResolvedValue({
      data: { purchase_invoices: [{ bill_id: 'b_9', invoice_id: 'b_9', vendor_id: 'v' }] },
    });
    const row = (await purchaseInvoicesApi.list()).purchase_invoices[0];
    expect(row.purchase_invoice_id).toBe('b_9');
  });

  it('gives a Cash Flow "+ bill" row (bill_id only, no invoice_id) its id', async () => {
    // That door stores bill_id alone; without the bill_id fallback its row
    // opened /purchase-invoices/undefined/match and approved /undefined/.
    mockGet.mockResolvedValue({
      data: { purchase_invoices: [{ bill_id: 'b_2', bill_number: 'BILL-22', vendor_id: 'v' }] },
    });
    const row = (await purchaseInvoicesApi.list()).purchase_invoices[0];
    expect(row.purchase_invoice_id).toBe('b_2');
  });

  it('handles a missing purchase_invoices array without throwing', async () => {
    mockGet.mockResolvedValue({ data: {} });
    const result = await purchaseInvoicesApi.list();
    expect(result.purchase_invoices).toEqual([]);
    expect(result.total).toBe(0);
  });
});

describe('purchaseInvoicesApi.create -> wire schema mapping', () => {
  const fePayload: PurchaseInvoiceCreate = {
    vendor_id: 'v_1',
    vendor_invoice_no: 'SUP-INV-9',
    vendor_invoice_date: '2026-05-20',
    recipient_gstin: '27ABCDE1234F1Z5',
    po_id: 'po_1',
    grn_id: 'grn_1',
    store_id: 'store_1',
    notes: 'urgent',
    lines: [
      {
        product_id: 'p_1',
        product_name: 'Ray-Ban Aviator',
        sku: 'RB-3025',
        hsn_code: '9004',
        quantity: 2,
        unit_price: 500,
        gst_rate: 18,
        taxable_amount: 1000,
      },
    ],
  };

  it('translates the FE payload into the backend PurchaseInvoiceCreate schema', async () => {
    mockPost.mockResolvedValue({
      data: {
        purchase_invoice_id: 'pi_created',
        invoice_number: 'SUP-INV-9',
        invoice_date: '2026-05-20',
        cgst_total: 90,
        sgst_total: 90,
        igst_total: 0,
        interstate: false,
      },
    });

    await purchaseInvoicesApi.create(fePayload);

    expect(mockPost).toHaveBeenCalledTimes(1);
    const [url, wire] = mockPost.mock.calls[0];
    expect(url).toBe('/vendors/purchase-invoices');

    // Header: vendor_invoice_no/date -> invoice_number/invoice_date.
    expect(wire.invoice_number).toBe('SUP-INV-9');
    expect(wire.invoice_date).toBe('2026-05-20');
    expect(wire.vendor_id).toBe('v_1');
    // F6: the server decides the tax head from the two GST numbers; no
    // "place of supply" goes on the wire for it to read another way.
    expect(wire).not.toHaveProperty('place_of_supply');
    expect(wire.recipient_gstin).toBe('27ABCDE1234F1Z5');
    expect(wire.po_id).toBe('po_1');
    expect(wire.grn_id).toBe('grn_1');
    expect(wire.store_id).toBe('store_1');
    expect(wire.notes).toBe('urgent');

    // The FE keys must NOT leak through to the wire body.
    expect(wire.vendor_invoice_no).toBeUndefined();
    expect(wire.vendor_invoice_date).toBeUndefined();

    // Per-line: product_name->description, quantity->qty, hsn_code->hsn,
    // taxable_amount->taxable. unit_price + gst_rate pass through unchanged.
    expect(wire.lines).toHaveLength(1);
    const line = wire.lines[0];
    expect(line.description).toBe('Ray-Ban Aviator');
    expect(line.qty).toBe(2);
    expect(line.hsn).toBe('9004');
    expect(line.taxable).toBe(1000);
    expect(line.unit_price).toBe(500);
    expect(line.gst_rate).toBe(18);
    expect(line.product_id).toBe('p_1');
    // The FE-only line keys must not appear on the wire line.
    expect(line.product_name).toBeUndefined();
    expect(line.quantity).toBeUndefined();
    expect(line.hsn_code).toBeUndefined();
    expect(line.taxable_amount).toBeUndefined();
  });

  it('maps the POST response back through mapInvoiceFromApi', async () => {
    mockPost.mockResolvedValue({
      data: {
        purchase_invoice_id: 'pi_created',
        invoice_number: 'SUP-INV-9',
        invoice_date: '2026-05-20',
        igst_total: 180,
        interstate: true,
      },
    });

    const created = await purchaseInvoicesApi.create(fePayload);
    // Returned object is normalised to FE keys.
    expect(created.vendor_invoice_no).toBe('SUP-INV-9');
    expect(created.vendor_invoice_date).toBe('2026-05-20');
    expect(created.igst).toBe(180);
    expect(created.is_interstate).toBe(true);
  });
});

describe('draft lines -> form lines (F37: from-GRN lines arrived blank, qty 1)', () => {
  const serverLines = [
    { product_id: 'p_1', description: 'Carrera CA 8895 807', hsn: '9003', qty: 3, unit_price: 3100, gst_rate: 5, taxable: 9300, igst: 465 },
  ];

  it('createFromGrn maps description/hsn/qty onto the form keys', async () => {
    mockGet.mockResolvedValue({ data: { invoice_number: 'MLH-77', invoice_date: '2026-09-10', lines: serverLines } });
    const draft = await purchaseInvoicesApi.createFromGrn('G1');
    expect(mockGet).toHaveBeenCalledWith('/vendors/purchase-invoices/from-grn/G1');
    expect(draft.vendor_invoice_no).toBe('MLH-77');
    expect(draft.lines).toEqual([
      expect.objectContaining({ product_id: 'p_1', product_name: 'Carrera CA 8895 807', hsn_code: '9003', quantity: 3, unit_price: 3100, gst_rate: 5 }),
    ]);
  });

  it('createFromDcs maps the same way', async () => {
    mockGet.mockResolvedValue({ data: { linked_dc_ids: ['dc1'], lines: serverLines } });
    const draft = await purchaseInvoicesApi.createFromDcs(['dc1']);
    expect(draft.lines[0]).toEqual(
      expect.objectContaining({ product_name: 'Carrera CA 8895 807', hsn_code: '9003', quantity: 3 }),
    );
  });
});

describe('a bill that never had a tax head decided', () => {
  const legacy = {
    bill_id: 'b-legacy', bill_number: 'CF-1', bill_date: '2026-08-20',
    taxable_amount: 1000, tax_amount: 180, total_amount: 1180,
  };

  it('has no interstate verdict (an old Cash Flow "+ bill")', async () => {
    mockGet.mockResolvedValue({ data: { purchase_invoices: [legacy], total: 1 } });
    const { purchase_invoices: rows } = await purchaseInvoicesApi.list();
    expect(rows[0].is_interstate).toBeUndefined();
  });

  it('keeps the verdict of a bill that stored heads', async () => {
    const heads = { ...legacy, cgst_total: 90, sgst_total: 90, igst_total: 0 };
    const ig = { ...legacy, cgst_total: 0, sgst_total: 0, igst_total: 180 };
    mockGet.mockResolvedValue({ data: { purchase_invoices: [heads, ig], total: 2 } });
    const { purchase_invoices: rows } = await purchaseInvoicesApi.list();
    expect([rows[0].is_interstate, rows[1].is_interstate]).toEqual([false, true]);
  });
});

describe('round 12 mapper hardening (#10, #11, #12)', () => {
  it('#10 a null / non-object line is dropped, not thrown on', async () => {
    mockGet.mockResolvedValue({
      data: {
        purchase_invoices: [
          { purchase_invoice_id: 'pi_1', lines: [null, 'x', 7, [], { description: 'Frame', qty: 2 }] },
        ],
        total: 1,
      },
    });
    const { purchase_invoices } = await purchaseInvoicesApi.list();
    expect(purchase_invoices).toHaveLength(1);
    expect(purchase_invoices[0].lines).toHaveLength(1);
    expect(purchase_invoices[0].lines[0].product_name).toBe('Frame');
  });

  it('#10 a draft with lines:[null] still opens', async () => {
    mockGet.mockResolvedValue({ data: { vendor_id: 'v', lines: [null] } });
    const draft = await purchaseInvoicesApi.createFromGrn('G1');
    expect(draft.lines).toEqual([]);
  });

  it('#11 the id is purchase_invoice_id, else bill_id, invoice_id, id, _id', async () => {
    for (const [doc, want] of [
      [{ purchase_invoice_id: 'a', id: 'z' }, 'a'],
      [{ bill_id: 'b' }, 'b'],
      [{ invoice_id: 'c' }, 'c'],
      [{ id: 'd' }, 'd'],
      [{ _id: 'e' }, 'e'],
      [{ purchase_invoice_id: '', _id: 'f' }, 'f'],
    ] as const) {
      mockGet.mockResolvedValue({ data: { purchase_invoices: [doc], total: 1 } });
      const { purchase_invoices } = await purchaseInvoicesApi.list();
      expect(purchase_invoices[0].purchase_invoice_id).toBe(want);
    }
  });

  it('#11 a write with no id throws and sends nothing; a read returns null', async () => {
    for (const bad of ['', undefined, null, 'undefined']) {
      await expect(
        purchaseInvoicesApi.approveException(bad as unknown as string, { reason: 'ok' }),
      ).rejects.toThrow(/no id/);
    }
    expect(await purchaseInvoicesApi.getMatch(undefined as unknown as string)).toBeNull();
    expect(mockPost).not.toHaveBeenCalled();
    expect(mockGet).not.toHaveBeenCalled();
  });

  it('#11 a real id still posts', async () => {
    mockPost.mockResolvedValue({ data: { ok: true } });
    await purchaseInvoicesApi.approveException('pi_9', { reason: 'ok' });
    expect(mockPost).toHaveBeenCalledWith(
      '/vendors/purchase-invoices/pi_9/approve-exception',
      { reason: 'ok' },
    );
  });

  it('#12 an empty string falls through to the alias (name and HSN)', async () => {
    mockGet.mockResolvedValue({
      data: {
        purchase_invoices: [
          { purchase_invoice_id: 'p', lines: [{ hsn_code: '', hsn: '9003', product_name: '', description: 'X' }] },
        ],
        total: 1,
      },
    });
    const { purchase_invoices } = await purchaseInvoicesApi.list();
    expect(purchase_invoices[0].lines[0]).toMatchObject({ hsn_code: '9003', product_name: 'X' });
  });
});
