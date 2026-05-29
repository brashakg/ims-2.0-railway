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
   * The wizard Review panel (StepReview) GST split. PR #333 + #335 migrated its
   * taxBreakdown to be flag-aware (gstRuntime.isInclusivePricing): in the
   * default INCLUSIVE mode the CGST/SGST are now EXTRACTED from WITHIN the
   * Rs 999 (taxable = gross/(1+rate)) rather than added on top, and the panel's
   * Grand Total stays the inclusive Rs 999. This guards that fix.
   *
   * Mode-aware: in exclusive mode the same rule yields cgst/sgst on-top while
   * Grand Total = price + GST, so the assertion stays honest either way.
   */
  test('wizard Review shows CGST/SGST split per mode, reconciling to the Grand Total (#333/#335)', async ({
    page,
    mode,
  }) => {
    const expected = lineGst(SEED.frame.price, SEED.frame.gstRate, mode);

    const pos = new PosPage(page);
    await pos.goto();
    await pos.selectFirstSalespersonAndWalkin();
    await pos.addProductByName(SEED.frame.name);
    await pos.continueFromProducts();

    // CGST/SGST are shown WITHIN the inclusive price (23.78 / 23.79), not the
    // on-top 24.97 / 24.98 that the pre-#333 local calc produced.
    const cgst = pos.reviewTaxRowValue('CGST');
    const sgst = pos.reviewTaxRowValue('SGST');
    await expect(cgst).toHaveText(new RegExp(expected.cgst.toFixed(2).replace('.', '\\.')));
    await expect(sgst).toHaveText(new RegExp(expected.sgst.toFixed(2).replace('.', '\\.')));

    // The Grand Total still equals the inclusive counter price (Rs 999) — the
    // split is WITHIN it, not added on top.
    const grandTotalCell = pos.reviewRowValue('Grand Total');
    await expect(grandTotalCell).toHaveText(
      new RegExp(`${Math.round(expected.grandTotal).toLocaleString('en-IN')}`)
    );
  });

  // NB: the GST invoice paisa-split equality (line-item CGST === HSN-summary
  // CGST to the paise) is still unfixed and remains a `test.fixme` in
  // gst-invoice.spec.ts; no duplicate guard is added here.
});
