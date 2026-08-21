// ============================================================================
// POS EMI rate — the screen quotes THE RATE THE BACKEND WILL APPLY
// ============================================================================
// Owner ruling 2026-08-21: "wire the screen to the real setting."
// The backend order add-payment endpoint builds the EMI schedule from policy
// `pos.emi_annual_rate_percent` (store > entity > global > default 12.0,
// see backend api/routers/orders.py::_emi_annual_rate). Before this fix the
// screen hardcoded 12% ((store as any).emiAnnualRate ?? 0.12 — a field nothing
// ever set) and the quote panel even used a separate hardcoded 1%/month, so a
// store with a configured rate would quote a number the backend did not charge.
//
// DECIDING TEST: plant a NON-DEFAULT rate (14.5%) and assert the SCREEN shows
// it and quotes the exact instalment the backend formula produces. The golden
// figures below were computed INDEPENDENTLY by running the backend
// build_emi_schedule math in Python (not by re-running the frontend code):
//   build_emi_schedule(principal=25000, annual_rate=14.5, months=12).monthly_emi
//     == 2250.56
//   build_emi_schedule(principal=25000, annual_rate=12.0, months=12).monthly_emi
//     == 2221.22   (the default-rate control — distinct, so a screen stuck on
//                   the default FAILS the 14.5% assertion)

import { render, screen, act, fireEvent } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// Complete Map-backed localStorage for the posStore persist middleware.
(() => {
  const m = new Map<string, string>();
  const ls = {
    getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
    setItem: (k: string, v: string) => { m.set(k, String(v)); },
    removeItem: (k: string) => { m.delete(k); },
    clear: () => { m.clear(); },
    key: (i: number) => Array.from(m.keys())[i] ?? null,
    get length() { return m.size; },
  };
  Object.defineProperty(globalThis, 'localStorage', { value: ls, configurable: true, writable: true });
})();

// GST runtime resolver -> static, no /health fetch (posStore totals use it).
vi.mock('../../../constants/gstRuntime', () => ({
  resolveGstRate: () => 5,
  isInclusivePricing: () => true,
  loadHsnRates: vi.fn(),
  loadPricingMode: vi.fn(),
}));

// The STORE-DETAIL read the screen performs — the ONLY rate source it may
// use. The first cut fetched the policies endpoint instead, which is closed
// to SALES_CASHIER/SALES_STAFF: the 403 died in a silent catch and cashiers
// quoted the 12% fallback while the order charged the configured rate. The
// store detail is AUTHENTICATED + store-scoped, so every billing role can
// read it — and the backend RBAC test pins that access by role name.
const getStoreMock = vi.fn();
vi.mock('../../../services/api', () => ({
  storeApi: { getStore: (...a: unknown[]) => getStoreMock(...a) },
}));

import { StepPayment } from '../POSPayment';
import { usePOSStore } from '../../../stores/posStore';

function seedCart(total: number) {
  act(() => {
    const s = usePOSStore.getState();
    s.resetTransaction();
    s.setStoreId('BV-BOK-01');
    s.addToCart({
      product_id: 'p1', name: 'Frame A', sku: 'FR-1', category: 'FRAMES',
      unit_price: total, mrp: total, quantity: 1, is_optical: false,
    } as any);
  });
}

/** Open the EMI form and enter a ₹5,000 down payment against a ₹30,000 bill. */
function openEmiWithDownPayment() {
  // The payment-methods grid EMI tile opens the form.
  fireEvent.click(screen.getAllByRole('button', { name: 'EMI' })[0]);
  fireEvent.change(screen.getByPlaceholderText(/^Max ₹/), { target: { value: '5000' } });
}

beforeEach(() => {
  localStorage.clear();
  getStoreMock.mockReset();
});

describe('POS EMI quote uses the store policy rate (pos.emi_annual_rate_percent)', () => {
  it('REQUIREMENT: a planted non-default 14.5% rate is shown on screen and the quote equals the backend schedule (2250.56, not the 12% default 2221.22)', async () => {
    getStoreMock.mockResolvedValue({
      store_id: 'BV-BOK-01', name: 'Bokaro', emi_annual_rate_percent: 14.5,
    });
    seedCart(30000);
    render(<StepPayment />);

    // The screen fetched the rate for THIS store's scope.
    expect(await screen.findByText(/Total Due/)).toBeInTheDocument();
    expect(getStoreMock).toHaveBeenCalledWith('BV-BOK-01');

    openEmiWithDownPayment();

    // The SCREEN displays the planted rate — not the 12% default.
    expect(await screen.findByText('14.5% p.a.')).toBeInTheDocument();
    // Loan = 30000 - 5000 = 25000 over 12m at 14.5% p.a. -> backend
    // build_emi_schedule monthly_emi is 2250.56 (golden, computed in Python).
    expect(screen.getByTestId('emi-monthly-quote').textContent).toBe('₹2250.56');

    // And the payment entry recorded for the order carries the same figure —
    // the number the cashier promised is the number the backend will build.
    fireEvent.click(screen.getByRole('button', { name: 'Add EMI' }));
    const payments = usePOSStore.getState().payments;
    expect(payments).toHaveLength(1);
    expect(payments[0].monthlyEMI).toBe(2250.56);
    expect(payments[0].emiBalance).toBe(25000);
  });

  it('positive control: when the policy fetch fails, the screen falls back to the backend default 12% (2221.22)', async () => {
    getStoreMock.mockRejectedValue(new Error('network down'));
    seedCart(30000);
    render(<StepPayment />);
    openEmiWithDownPayment();

    expect(await screen.findByText('12% p.a.')).toBeInTheDocument();
    expect(screen.getByTestId('emi-monthly-quote').textContent).toBe('₹2221.22');
  });
});
