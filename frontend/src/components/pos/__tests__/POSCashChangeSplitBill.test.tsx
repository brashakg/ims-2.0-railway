// ============================================================================
// The cash change calculator is about the CASH LEG, not the whole bill
// ============================================================================
// Owner-reported, with these exact figures: UPI 590 + CARD 12,000 + CASH
// 28,000 on a 40,590 bill, 29,000 in notes on the counter, and the screen
// announced "Short: 11,590" (= 40,590 - 29,000). Nobody was short a rupee:
// 29,000 handed over against a 28,000 cash leg is 1,000 CHANGE.
//
// The direction of the error is the dangerous part - it tells a cashier to
// collect another 11,590 that the customer has ALREADY paid by card and UPI.
//
// The quick-tender chips had the same base and so suggested 40,600 / 41,000 /
// 42,000 for a 28,000 cash leg.
import { render, screen } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

(() => {
  const m = new Map<string, string>();
  Object.defineProperty(globalThis, 'localStorage', {
    value: {
      getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
      setItem: (k: string, v: string) => { m.set(k, String(v)); },
      removeItem: (k: string) => { m.delete(k); },
      clear: () => m.clear(),
      key: (i: number) => Array.from(m.keys())[i] ?? null,
      get length() { return m.size; },
    },
    configurable: true, writable: true,
  });
})();

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', name: 'Cashier', roles: ['STORE_MANAGER'], discountCap: 10 } }),
}));
vi.mock('../../../constants/gstRuntime', () => ({
  resolveGstRate: () => 5,
  isInclusivePricing: () => true,
  loadHsnRates: vi.fn(),
  loadPricingMode: vi.fn(),
}));

import { usePOSStore } from '../../../stores/posStore';
import { StepPayment } from '../POSPayment';

/** The owner's bill, to the rupee. */
const seedSplitBill = () => {
  usePOSStore.setState({
    customer: { id: 'c1', name: 'Test Customer', phone: '9800000000' } as any,
    cart: [{
      id: 'l1', product_id: 'p1', name: 'Frame', sku: 'SKU1', category: 'FRAME',
      unit_price: 40590, mrp: 40590, quantity: 1, is_optical: true,
      discount_percent: 0, discount_amount: 0, line_total: 40590,
    }] as any,
    payments: [
      { id: 'a', method: 'UPI', amount: 590 },
      { id: 'b', method: 'CARD', amount: 12000 },
      { id: 'c', method: 'CASH', amount: 28000 },
    ] as any,
    cart_discount_percent: 0, cart_discount_amount: 0,
  });
};

describe('cash change on a split bill', () => {
  beforeEach(seedSplitBill);

  it('measures the notes against the CASH leg, not the whole bill', () => {
    render(<StepPayment />);
    const cash = screen.getByPlaceholderText(/^28000$/);
    expect(cash, 'the tendered box should default to the 28,000 cash leg').toBeTruthy();
  });

  it('offers quick tenders sized for the cash leg', () => {
    render(<StepPayment />);
    // 40,600 / 41,000 / 42,000 were round-ups of the WHOLE bill.
    expect(screen.queryByText(/40,600/)).toBeNull();
    expect(screen.queryByText(/41,000/)).toBeNull();
    expect(screen.queryByText(/42,000/)).toBeNull();
  });

  it('never tells the cashier to collect money already paid by card and UPI', () => {
    render(<StepPayment />);
    // Nothing on a fully-settled split bill should read as a shortfall.
    expect(screen.queryByText(/Short: ₹11,590/)).toBeNull();
  });
});
