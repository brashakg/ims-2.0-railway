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
 * CONDENSED FLOW (#783/#790): a quick sale is Customer -> Products -> Payment.
 * The Review step no longer exists for quick sales (QUICK_STEPS parity), so the
 * pre-payment totals the customer sees live in the CART SIDEBAR
 * (Subtotal / GST / "Total (incl. GST)"), asserted on the Products step.
 *
 * Mode-aware: if the backend reports exclusive pricing (legacy), the same
 * spec asserts the exclusive expectation (Rs 1048.95) instead, so it stays
 * honest whichever mode is live.
 */
import { test, expect } from '../fixtures/test';
import { PosPage } from '../fixtures/pos-page';
import { lineGst, cartGst } from '../fixtures/gst-math';
import { SEED } from '../fixtures/constants';

/** The cart sidebar renders whole-rupee en-IN figures, e.g. "₹2,179". */
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
    await pos.selectFirstSalespersonAndWalkin();
    await pos.addProductByName(SEED.frame.name);

    // --- Products step, cart sidebar: the all-in total the customer is shown.
    // getGrandTotal() is inclusive under #331, so this equals the counter
    // price (Rs 999), NOT price + GST-on-top (Rs 1048.95).
    await expect(pos.cartRowValue('Total (incl. GST)')).toHaveText(
      rupees(expected.grandTotal)
    );

    // Quick sale: Continue goes STRAIGHT to Payment (no Review step).
    await pos.continueFromProducts();

    // --- Payment step: "Total Due (incl. GST)" headline ---
    await expect(pos.totalDueHeadline).toHaveText(
      new RegExp(`${Math.round(expected.grandTotal).toLocaleString('en-IN')}`)
    );

    await pos.payFullCashAndComplete();
    const orderNumber = await pos.waitForOrderCreated();
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
    await pos.selectFirstSalespersonAndWalkin();
    await pos.addProductByName(SEED.frame.name);
    await pos.addProductByName(SEED.sunglass.name);

    // The cart's all-in total equals the sum of the inclusive line prices.
    await expect(pos.cartRowValue('Total (incl. GST)')).toHaveText(
      rupees(expected.grandTotal)
    );

    await pos.continueFromProducts();
    await pos.payFullCashAndComplete();
    const orderNumber = await pos.waitForOrderCreated();

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
   * Condensed-flow behavior guard (#783/#790, QUICK_STEPS parity): a quick
   * sale must go STRAIGHT from Products to Payment — the Review panel
   * ("Order Review") must never render — and the GST the cart shows is the
   * component EXTRACTED WITHIN the inclusive total (₹48 within ₹999 at 5%),
   * not added on top (which would show ~₹50 over a ₹1,049 total).
   *
   * (The Review panel's own CGST/SGST split — the old #333/#335 guard — now
   * only exists inside a prescription order's merged "Pay & Review" group and
   * is covered by the component tests in POSCondensedFlow.test.tsx; the
   * paisa-level split of the persisted order is asserted via API in the
   * Rs 999 spec above, and on the Tax Invoice in gst-invoice.spec.ts.)
   *
   * Mode-aware: in exclusive mode the same rows must show the on-top figures
   * (GST ₹50, total ₹1,049), so the assertion stays honest either way.
   */
  test('quick sale skips Review: cart shows GST extracted within the inclusive total', async ({
    page,
    mode,
  }) => {
    const expected = lineGst(SEED.frame.price, SEED.frame.gstRate, mode);

    const pos = new PosPage(page);
    await pos.goto();
    await pos.selectFirstSalespersonAndWalkin();
    await pos.addProductByName(SEED.frame.name);

    // Cart sidebar: GST is the extracted-within component; the total stays the
    // inclusive counter price. (Whole-rupee display: 47.57 -> ₹48, 999 -> ₹999.
    // A regression to on-top math would show ₹50 / ₹1,049 in inclusive mode.)
    await expect(pos.cartRowValue('Subtotal')).toHaveText(rupees(SEED.frame.price));
    await expect(pos.cartRowValue('GST')).toHaveText(rupees(expected.tax));
    await expect(pos.cartRowValue('Total (incl. GST)')).toHaveText(
      rupees(expected.grandTotal)
    );

    // Continue from Products lands DIRECTLY on Payment: the Total-Due headline
    // appears and no "Order Review" panel ever renders.
    await pos.continueFromProducts();
    await expect(pos.totalDueHeadline).toBeVisible();
    await expect(pos.totalDueHeadline).toHaveText(
      new RegExp(`${Math.round(expected.grandTotal).toLocaleString('en-IN')}`)
    );
    await expect(
      page.getByRole('heading', { name: 'Order Review' })
    ).toHaveCount(0);
  });

  // NB: the GST invoice paisa-split equality (line-item CGST === HSN-summary
  // CGST to the paise) is still unfixed and remains a `test.fixme` in
  // gst-invoice.spec.ts; no duplicate guard is added here.
});
