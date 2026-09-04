/**
 * GST Tax Invoice reconciliation (guards PR #331 invoice math).
 *
 * The invoice is the SERVER's document: the till's completion screen prints
 * GET /orders/{id}/invoice.pdf, and GET /orders/{id}/invoice is the same
 * assembly (orders._assemble_invoice) as data. The client-side invoice modal
 * these specs used to read is gone, so they sell through the real till and
 * then read the statutory document through the API.
 *
 * After a Rs 999 inclusive sale, the invoice must:
 *   - reconcile its Grand Total to the amount the customer paid (Rs 999),
 *   - extract the taxable base within (Rs 951.43) + total GST (Rs 47.57),
 *   - split that GST into CGST + SGST that sum back EXACTLY to the line tax
 *     (23.78 + 23.79 -- the odd paisa lands on either head, per
 *     gst_rates.split_gst, which both the JSON and the PDF tables share).
 *
 * The old `test.fixme` here guarded a 1-paisa CGST swap between two CLIENT
 * tables (calculateGST floored, hsnTaxSummary rounded). Both tables were
 * retired with the client invoice; the server prints both from one split of
 * the same stored tax, so the paisa-exact check below is asserted for real.
 */
import { test, expect } from '../fixtures/test';
import { PosPage } from '../fixtures/pos-page';
import { lineGst } from '../fixtures/gst-math';
import { SEED } from '../fixtures/constants';

/** GET /orders/search hands back order_to_frontend rows: `order_id` -> `id`. */
function orderIdOf(order: any): string {
  const id = order.id ?? order.orderId ?? order.order_id;
  if (!id) throw new Error(`Order has no id: ${JSON.stringify(order).slice(0, 200)}`);
  return String(id);
}

const paise = (n: number) => Math.round(n * 100);

async function sellFrameAndReadInvoice(
  page: any,
  api: any
): Promise<{ order: any; invoice: any }> {
  const pos = new PosPage(page);
  await pos.goto();
  await pos.pickFirstSalesperson();
  await pos.createCustomer();
  await pos.addProduct(SEED.frame);
  await pos.payFullCash();
  const orderNumber = await pos.completeSale();
  const order = await api.getOrder(orderNumber);
  const invoice = await api.getInvoice(orderIdOf(order));
  return { order, invoice };
}

test.describe('GST Tax Invoice', () => {
  test('invoice Grand Total reconciles to amount paid (Rs 999), GST extracted within', async ({
    page,
    api,
    mode,
  }) => {
    test.skip(
      mode !== 'inclusive',
      'Invoice reconciliation values are specified for inclusive pricing.'
    );

    const expected = lineGst(SEED.frame.price, SEED.frame.gstRate, 'inclusive');
    const { order, invoice } = await sellFrameAndReadInvoice(page, api);

    // A statutory serial is minted on first read (FY-consecutive, Rule 46(b)).
    expect(invoice.invoiceNumber, 'invoice number minted').toBeTruthy();

    // Grand Total on the invoice is the all-in Rs 999.00 ...
    expect(invoice.grandTotal).toBeCloseTo(999, 2);
    // ... and equals what the customer actually paid on the persisted order.
    expect(order.amountPaid).toBeCloseTo(999, 2);
    expect(order.grandTotal).toBeCloseTo(999, 2);

    // Taxable base extracted within (Rs 951.43) + total GST (Rs 47.57), and
    // the two reconcile back to the Grand Total.
    expect(invoice.taxTotals.taxable).toBeCloseTo(951.43, 2);
    expect(invoice.taxTotals.tax).toBeCloseTo(47.57, 2);
    expect(invoice.taxTotals.taxable + invoice.taxTotals.tax).toBeCloseTo(invoice.grandTotal, 2);
    expect(expected.tax).toBeCloseTo(47.57, 2);
    expect(expected.cgst + expected.sgst).toBeCloseTo(expected.tax, 2);

    // The A4 the completion screen prints comes from the same assembly.
    const pdf = await api.rawGet(`/api/v1/orders/${orderIdOf(order)}/invoice.pdf`);
    expect(pdf.status()).toBe(200);
    expect(pdf.headers()['content-type']).toContain('pdf');
  });

  test('CGST + SGST split reconciles to the line tax to the paisa', async ({
    page,
    api,
    mode,
  }) => {
    test.skip(mode !== 'inclusive', 'Specified for inclusive pricing.');

    const { invoice } = await sellFrameAndReadInvoice(page, api);

    // One 5% rate row -- the HSN-wise summary the PDF prints. The modal-created
    // customer has no state, so place of supply defaults to intra-state.
    expect(invoice.interstate).toBe(false);
    const rows: Array<{
      rate: number;
      taxable: number;
      cgst: number;
      sgst: number;
      igst: number;
      tax: number;
    }> = invoice.taxSummary;
    expect(rows).toHaveLength(1);
    const [row] = rows;
    expect(row.rate).toBe(SEED.frame.gstRate);
    expect(row.igst).toBe(0);
    expect(row.tax).toBeCloseTo(47.57, 2);

    // 47.57 halves to 23.785: the odd paisa lands on ONE head (either), and
    // the heads must sum back to the line tax EXACTLY -- never 23.79 + 23.79.
    expect(paise(row.cgst) + paise(row.sgst)).toBe(paise(row.tax));
    expect([row.cgst, row.sgst].sort((a, b) => a - b)).toEqual([23.78, 23.79]);

    // The line items carry the same tax the summary aggregates.
    const lineTax = (invoice.items as any[]).reduce(
      (sum, it) => sum + Number(it.tax_amount ?? it.taxAmount ?? 0),
      0
    );
    expect(lineTax).toBeCloseTo(invoice.taxTotals.tax, 2);
    expect(paise(row.cgst) + paise(row.sgst)).toBe(paise(lineTax));
  });
});
