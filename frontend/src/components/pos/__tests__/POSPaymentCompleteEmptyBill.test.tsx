// ============================================================================
// Audit UI-01 (owner approved 2026-11-13): an EMPTY bill never says
// "Payment complete". Only that message's condition changed; prices, payments
// and steps are untouched.
// ============================================================================
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

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

const DONE = /Payment complete/;
const base = { customer: { id: 'c1', name: 'Asha', phone: '9800000000' } as any,
  cart_discount_percent: 0, cart_discount_amount: 0 };

describe('Payment complete message', () => {
  it('is not shown on an empty bill', () => {
    usePOSStore.setState({ ...base, cart: [], payments: [] } as any);
    render(<StepPayment />);
    expect(screen.queryByText(DONE)).toBeNull();
  });

  it('is still shown once a real bill is fully paid', () => {
    usePOSStore.setState({
      ...base,
      cart: [{ id: 'l1', product_id: 'p1', name: 'Frame', sku: 'SKU1', category: 'FRAME',
        unit_price: 1000, mrp: 1000, quantity: 1, is_optical: true,
        discount_percent: 0, discount_amount: 0, line_total: 1000 }],
      payments: [{ id: 'a', method: 'UPI', amount: 1000 }],
    } as any);
    render(<StepPayment />);
    expect(screen.queryByText(DONE)).not.toBeNull();
  });
});
