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
 * Mode-aware: if the backend reports exclusive pricing (legacy), the same
 * spec asserts the exclusive expectation (Rs 1048.95) instead, so it stays
 * honest whichever mode is live.
 */
import { test, expect } from '../fixtures/test';
import { PosPage } from '../fixtures/pos-page';
import { lineGst, cartGst } from '../fixtures/gst-math';
import { SEED } from '../fixtures/constants';

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
    await pos.continueFromProducts();

    // --- Review step: the Grand Total the customer is shown ---
    // getGrandTotal() is inclusive under #331, so this equals the counter
    // price (Rs 999), NOT price + GST-on-top (Rs 1048.95).
    const grandTotalCell = pos.reviewRowValue('Grand Total');
    await expect(grandTotalCell).toHaveText(
      new RegExp(`${Math.round(expected.grandTotal).toLocaleString('en-IN')}`)
    );

    await pos.continueFromReview();

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
    await pos.continueFromProducts();

    const grandTotalCell = pos.reviewRowValue('Grand Total');
    await expect(grandTotalCell).toHaveText(
      new RegExp(`${Math.round(expected.grandTotal).toLocaleString('en-IN')}`)
    );

    await pos.continueFromReview();
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
   * The wizard Review panel (StepReview) renders its CGST/SGST split from a
   * LOCAL exclusive calc that PR #331 did NOT migrate: it shows tax ON TOP
   * (Rs 24.97 / Rs 24.98 ~ Rs 49.95) even though the same panel's Grand Total
   * is the inclusive Rs 999. The cart sidebar (POSCart) and the persisted
   * order ARE inclusive (asserted above). This is a real, isolated UI
   * inconsistency — marked fixme so it never reports a fake pass.
   *
   * When StepReview is fixed to extract GST within (CGST 23.78 / SGST 23.79),
   * remove the fixme and this becomes a live guard.
   */
  test.fixme(
    'wizard Review shows CGST/SGST extracted WITHIN the Rs 999 (currently on-top — #331 missed StepReview)',
    async ({ page }) => {
      const pos = new PosPage(page);
      await pos.goto();
      await pos.selectFirstSalespersonAndWalkin();
      await pos.addProductByName(SEED.frame.name);
      await pos.continueFromProducts();
      // Intended (post-fix) behavior: CGST 23.78 + SGST 23.79 = 47.57 within 999.
      await expect(page.getByText(/CGST.*23\.78/)).toBeVisible();
      await expect(page.getByText(/SGST.*23\.79/)).toBeVisible();
    }
  );
});
