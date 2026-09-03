// ============================================================================
// A stored HSN must never change what the customer is CHARGED
// ============================================================================
// The client-side GSTInvoice modal was retired 2026-09-03 -- the statutory tax
// invoice is ONLY the server's GET /orders/{id}/invoice.pdf now (see
// ServerInvoiceOnly.test.tsx), so this file no longer proves what an invoice
// PRINTS. What remains is the money rule on the till itself: posStore rates a
// cart line by its CATEGORY, never by the hsn_code on its record (an exact-HSN
// hit BEATS the category inside resolveGstRate, so a stray stored code could
// otherwise move the percentage), and the Review screen quotes exactly that
// breakdown -- in inclusive and in the exclusive rollback mode.

import { act } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// Map-backed localStorage for the posStore persist middleware.
(() => {
  const m = new Map<string, string>();
  Object.defineProperty(globalThis, 'localStorage', {
    value: {
      getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
      setItem: (k: string, v: string) => { m.set(k, String(v)); },
      removeItem: (k: string) => { m.delete(k); },
      clear: () => { m.clear(); },
      key: (i: number) => Array.from(m.keys())[i] ?? null,
      get length() { return m.size; },
    },
    configurable: true,
    writable: true,
  });
})();

// Each case here resets the module registry and renders a whole POS screen or a
// full statutory invoice, which can outrun vitest's 5s default when the entire
// suite runs in parallel on a slow machine. Slow, not flaky -- give it room.
vi.setConfig({ testTimeout: 20000 });

const { apiGet } = vi.hoisted(() => ({ apiGet: vi.fn() }));
vi.mock('../../../services/api/client', () => ({ default: { get: apiGet } }));

import { usePOSStore } from '../../../stores/posStore';
import { loadHsnRates, loadPricingMode } from '../../../constants/gstRuntime';

const SERVER = {
  by_hsn: { '900410': 18 },
  by_cat: { SUNGLASSES: 18 },
  category_hint: { SUNGLASS: 'SUNGLASSES' },
  hsn_by_category: { SUNGLASS: '900410', FRAME: '900311' },
  rate_by_category: { SUNGLASS: 18, SUNGLASSES: 18, FRAME: 5 },
};


describe('the cart preview and the Review screen quote the SAME rate', () => {
  beforeEach(async () => {
    apiGet.mockResolvedValue({ data: SERVER });
    await loadHsnRates();
    act(() => usePOSStore.getState().resetTransaction());
  });

  it('rates a cart line by its category, never by the HSN on its record', async () => {
    // getTax()/getGrandTotal() read `(item as any).hsn_code` on main, where it
    // was dead: CartLineItem had no such field. Giving the line an hsn_code --
    // which is what lets the invoice print the right code -- would have woken
    // that argument up, and an exact-HSN hit BEATS the category inside
    // resolveGstRate. A 5% frame whose record carries 900410 (a master row at
    // 18%) would then have shown 18% GST in the cart while the invoice printed
    // 5%: two screens, one sale, two numbers.
    act(() => {
      usePOSStore.getState().addToCart({
        product_id: 'p3', name: 'Titan Frame', sku: 'FR-1',
        category: 'FRAME',           // 5%
        hsn_code: '900410',          // ...but the record carries the 18% code
        unit_price: 1050, mrp: 1050, quantity: 1, is_optical: true,
      } as never);
    });
    const tax = usePOSStore.getState().getTax();
    // GST-inclusive: 1050 - 1050/1.05 = 50.00 at 5%. At 18% it would be 160.17.
    expect(tax).toBe(50);
  });

  it('and the Review screen is quoting THAT number, not one of its own', async () => {
    // The Review step used to run its own copy of the rate lookup + the
    // inclusive/exclusive branch. It now reads getTaxBreakdown, and getTax is
    // defined as that breakdown's total -- so a mixed-rate cart must reconcile
    // three ways at once: the per-rate bases sum to the taxable value, the
    // per-line rates agree with them, and base + tax lands on the grand total.
    act(() => {
      const s = usePOSStore.getState();
      s.addToCart({
        product_id: 'p5', name: 'Titan Frame', sku: 'FR-3',
        category: 'FRAME', hsn_code: '900410',   // 5%, record carries the 18% code
        unit_price: 1050, mrp: 1050, quantity: 2, is_optical: true,
      } as never);
      s.addToCart({
        product_id: 'p6', name: 'Ray-Ban Meta', sku: 'SMTSG-2',
        category: 'SUNGLASS',                    // 18%
        unit_price: 29900, mrp: 29900, quantity: 1, is_optical: false,
      } as never);
    });
    const s = usePOSStore.getState();
    const bd = s.getTaxBreakdown();

    // Inclusive: 2100 at 5% -> base 2000, tax 100. 29900 at 18% -> base
    // 25338.98, tax 4561.02. The frame is rated 5% despite its stored 900410.
    expect(Object.keys(bd.rates).map(Number).sort((a, b) => a - b)).toEqual([5, 18]);
    expect(bd.rates[5]).toBe(2000);
    expect(bd.lineRates[s.cart[0].id]).toBe(5);
    expect(bd.lineRates[s.cart[1].id]).toBe(18);
    // getTax is the breakdown, by construction -- and the bases + the tax
    // reconcile to the grand total, which is what the screen prints.
    expect(s.getTax()).toBe(bd.totalTax);
    expect(s.getTaxableValue()).toBe(bd.rates[5] + bd.rates[18]);
    const summed = Object.entries(bd.rates)
      .reduce((t, [r, base]) => t + base * (Number(r) / 100), 0);
    expect(Math.round(summed * 100) / 100).toBe(bd.totalTax);
    expect(Math.round((s.getTaxableValue() + bd.totalTax) * 100) / 100).toBe(s.getGrandTotal());
  });

  it('and the same in EXCLUSIVE mode, the rollback the owner can flip live', async () => {
    // GST_PRICING_MODE=exclusive is the no-redeploy rollback (gstRuntime reads
    // it off /health). getGrandTotal keeps its own per-line accumulation here
    // (GST added on top, not extracted), so it is the one consumer that could
    // still disagree with the breakdown the Review screen prints.
    apiGet.mockImplementation((url: string) =>
      Promise.resolve({ data: url === '/health' ? { pricing_mode: 'exclusive' } : SERVER }));
    await loadPricingMode();
    try {
      act(() => {
        usePOSStore.getState().addToCart({
          product_id: 'p4', name: 'Titan Frame', sku: 'FR-2',
          category: 'FRAME', hsn_code: '900410',
          unit_price: 1000, mrp: 1000, quantity: 1, is_optical: true,
        } as never);
      });
      // Exclusive: GST is added ON TOP. 1000 + 5% = 1050; at 18% it would be 1180.
      const s = usePOSStore.getState();
      expect(s.getGrandTotal()).toBe(1050);
      // ...and the same three-way reconciliation as inclusive. Here the taxable
      // base IS the line total (1000), not an extraction from within it.
      const bd = s.getTaxBreakdown();
      expect(bd.rates[5]).toBe(1000);
      expect(bd.totalTax).toBe(50);
      expect(s.getTax()).toBe(bd.totalTax);
      expect(s.getTaxableValue()).toBe(bd.rates[5]);
      expect(bd.lineRates[s.cart[0].id]).toBe(5);
      expect(Math.round((s.getTaxableValue() + bd.totalTax) * 100) / 100).toBe(s.getGrandTotal());
    } finally {
      apiGet.mockImplementation((url: string) =>
        Promise.resolve({ data: url === '/health' ? { pricing_mode: 'inclusive' } : SERVER }));
      await loadPricingMode();
    }
  });
});
