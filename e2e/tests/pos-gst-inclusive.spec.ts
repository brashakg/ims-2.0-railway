/**
 * POS GST-inclusive pricing (guards PR #331 — QA F3).
 *
 * The counter price is the ALL-IN price the customer pays. GST is the component
 * WITHIN it (taxable = price/(1+rate); tax = price - taxable), NOT added on top.
 * A Rs 999 frame @5% must ring up at Rs 999 (taxable 951.43, CGST 23.78 /
 * SGST 23.79), with the customer paying exactly Rs 999 — not Rs 1048.95.
 *
 * Source of truth = the persisted order (verified via API). The UI checks
 * confirm the customer is shown the same all-in figure.
 *
 * ONE-SURFACE TILL (/pos/new): the cart (Subtotal / GST / "Total (incl.
 * GST)") and the payment card ("Total Due (incl. GST)") are on screen at the
 * same time, so both figures are asserted before a single tender is taken.
 * Every bill needs a customer (owner rule), so each sale creates one first.
 *
 * Mode-aware: if the backend reports exclusive pricing (legacy), the same
 * spec asserts the exclusive expectation (Rs 1048.95) instead, so it stays
 * honest whichever mode is live.
 */
import { test, expect } from '../fixtures/test';
import { PosPage } from '../fixtures/pos-page';
import { lineGst, cartGst } from '../fixtures/gst-math';
import { SEED } from '../fixtures/constants';

/** The cart renders whole-rupee en-IN figures, e.g. "₹2,179". */
const rupees = (n: number) => `₹${Math.round(n).toLocaleString('en-IN')}`;

test.describe('POS — GST-inclusive sale', () => {
  test('Rs 999 frame: GST extracted within, customer pays Rs 999, order PAID', async ({
    page,
    api,
    mode,
  }) => {
    const expected = lineGst(SEED.frame.price, SEED.frame.gstRate, mode);
    // Independent sanity: in inclusive mode the all-in equals the counter price.
    if (mode === 'inclusive') {
      expect(expected.grandTotal).toBe(999);
      expect(expected.taxable).toBeCloseTo(951.43, 2);
      expect(expected.tax).toBeCloseTo(47.57, 2);
      expect(expected.cgst).toBeCloseTo(23.78, 2);
      expect(expected.sgst).toBeCloseTo(23.79, 2);
    }

    const pos = new PosPage(page);
    await pos.goto();
    await pos.pickFirstSalesperson();
    await pos.createCustomer();
    await pos.addProduct(SEED.frame);

    // --- Cart: the all-in total the customer is shown. getGrandTotal() is
    // inclusive under #331, so this equals the counter price (Rs 999), NOT
    // price + GST-on-top (Rs 1048.95).
    await expect(pos.cartRowValue('Total (incl. GST)')).toHaveText(
      rupees(expected.grandTotal)
    );

    // --- Payment card: "Total Due (incl. GST)" headline ---
    await expect(pos.totalDueHeadline).toHaveText(
      new RegExp(`${Math.round(expected.grandTotal).toLocaleString('en-IN')}`)
    );

    await pos.payFullCash();
    const orderNumber = await pos.completeSale();
    expect(orderNumber).toMatch(/^ORD-/);

    // --- Source of truth: the persisted order (camelCase API) ---
    const order = await api.getOrder(orderNumber);
    expect(order.grandTotal).toBeCloseTo(expected.grandTotal, 2);
    expect(order.amountPaid).toBeCloseTo(expected.grandTotal, 2);
    expect(order.balanceDue).toBe(0);
    expect(order.paymentStatus).toBe('PAID');
    expect(order.taxAmount).toBeCloseTo(expected.tax, 2);
    expect(order.subtotal ?? order.taxAmount + order.taxableValue).toBeDefined();

    // Per-line extracted GST is stamped on the stored item (drives GSTR-1).
    const item = (order.items ?? [])[0];
    expect(item, 'order should have a line item').toBeTruthy();
    expect(item.gst_rate).toBe(SEED.frame.gstRate);
    expect(item.taxable_value).toBeCloseTo(expected.taxable, 2);
    expect(item.tax_amount).toBeCloseTo(expected.tax, 2);
    // The two components reconcile to the all-in line price.
    expect(item.taxable_value + item.tax_amount).toBeCloseTo(
      expected.grandTotal,
      2
    );
  });

  test('multi-rate cart (5% frame + 18% sunglass): total == sum of inclusive prices', async ({
    page,
    api,
    mode,
  }) => {
    const lines = [
      { price: SEED.frame.price, rate: SEED.frame.gstRate },
      { price: SEED.sunglass.price, rate: SEED.sunglass.gstRate },
    ];
    const expected = cartGst(lines, mode);
    if (mode === 'inclusive') {
      // Rs 999 + Rs 1180 = Rs 2179 all-in.
      expect(expected.grandTotal).toBe(2179);
    }

    const pos = new PosPage(page);
    await pos.goto();
    await pos.pickFirstSalesperson();
    await pos.createCustomer();
    await pos.addProduct(SEED.frame);
    await pos.addProduct(SEED.sunglass);

    // The cart's all-in total equals the sum of the inclusive line prices.
    await expect(pos.cartRowValue('Total (incl. GST)')).toHaveText(
      rupees(expected.grandTotal)
    );

    await pos.payFullCash();
    const orderNumber = await pos.completeSale();

    const order = await api.getOrder(orderNumber);
    // The all-in total equals the sum of the inclusive line prices.
    expect(order.grandTotal).toBeCloseTo(expected.grandTotal, 2);
    expect(order.amountPaid).toBeCloseTo(expected.grandTotal, 2);
    expect(order.balanceDue).toBe(0);
    expect(order.paymentStatus).toBe('PAID');
    expect(order.taxAmount).toBeCloseTo(expected.tax, 2);

    // Both rates appear across the stored line items (5% and 18%).
    const rates = (order.items ?? []).map((i: any) => i.gst_rate).sort();
    expect(rates).toContain(5);
    expect(rates).toContain(18);
  });

  /**
   * The GST the cart shows is the component EXTRACTED WITHIN the inclusive
   * total (₹48 within ₹999 at 5%), not added on top (which would show ~₹50
   * over a ₹1,049 total), and the payment card's headline agrees with the
   * cart before any tender is taken.
   *
   * (The paisa-level split of the persisted order is asserted via API in the
   * Rs 999 spec above, and on the server's tax invoice in gst-invoice.spec.ts.)
   *
   * Mode-aware: in exclusive mode the same rows must show the on-top figures
   * (GST ₹50, total ₹1,049), so the assertion stays honest either way.
   */
  test('cart shows GST extracted within the inclusive total, headline agrees', async ({
    page,
    mode,
  }) => {
    const expected = lineGst(SEED.frame.price, SEED.frame.gstRate, mode);

    const pos = new PosPage(page);
    await pos.goto();
    await pos.pickFirstSalesperson();
    await pos.createCustomer();
    await pos.addProduct(SEED.frame);

    // Cart: GST is the extracted-within component; the total stays the
    // inclusive counter price. (Whole-rupee display: 47.57 -> ₹48, 999 -> ₹999.
    // A regression to on-top math would show ₹50 / ₹1,049 in inclusive mode.)
    await expect(pos.cartRowValue('Subtotal')).toHaveText(rupees(SEED.frame.price));
    await expect(pos.cartRowValue('GST')).toHaveText(rupees(expected.tax));
    await expect(pos.cartRowValue('Total (incl. GST)')).toHaveText(
      rupees(expected.grandTotal)
    );

    // Payment card, on the same screen: the Total-Due headline matches.
    await expect(pos.totalDueHeadline).toBeVisible();
    await expect(pos.totalDueHeadline).toHaveText(
      new RegExp(`${Math.round(expected.grandTotal).toLocaleString('en-IN')}`)
    );
  });
});
