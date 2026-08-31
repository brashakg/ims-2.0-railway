// ============================================================================
// POS per-sale cash capture — the SCREEN no longer discards the data
// ============================================================================
// The launch audit's exact finding (owner ruling 2026-08-25): the backend door
// for per-sale cash accountability existed and was tested, but POSPayment's
// CashChangeCalculator collected the tendered figure and THREW IT AWAY —
// POSLayout sent only method/amount/reference. These tests drive the REAL
// POSLayout + the REAL "Cash Tendered" input and assert what actually leaves
// the browser through orderApi.addPayment:
//
//   * typing a tendered figure -> the CASH leg body carries tendered_amount /
//     change_amount (the fields orders.py already reads)
//   * skipping it entirely -> the body is the EXACT legacy shape (optional:
//     accountability, not a wall) and the sale still completes
//   * a non-CASH leg on the same bill never gains a byte
//
// POS SAFETY: byte-level identity of the shared body builder is proven in
// POSPaymentBody.test.tsx; this file proves the WIRING fires.

import { render, screen, act, fireEvent, waitFor } from '@testing-library/react';
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

const MOCK_USER = {
  id: 'u1',
  name: 'Test Cashier',
  roles: ['STORE_MANAGER'],
  activeRole: 'STORE_MANAGER',
  activeStoreId: 'BV-BOK-01',
  storeIds: ['BV-BOK-01'],
  discountCap: 20,
};
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: MOCK_USER }),
}));

vi.mock('../../../hooks/usePOSQueries', () => ({
  useProducts: () => ({ data: [], isLoading: false }),
  useCustomerSearch: () => ({ data: [], isLoading: false }),
  useCustomer: () => ({ data: null }),
  useStores: () => ({ data: [], isLoading: false }),
}));

vi.mock('../../../constants/gstRuntime', () => ({
  resolveGstRate: () => 5,
  isInclusivePricing: () => true,
  loadHsnRates: vi.fn(),
  loadPricingMode: vi.fn(),
}));

// Capture exactly what the POS sends for each payment leg.
const createOrderMock = vi.fn(() => Promise.resolve({ order_id: 'ORD-77', order_number: 'BV-77' }));
const addPaymentMock = vi.fn(() => Promise.resolve({}));
vi.mock('../../../services/api', () => {
  const noop = () => Promise.resolve([]);
  return {
    customerApi: { search: noop, getCustomer: noop },
    orderApi: {
      createOrder: (...a: unknown[]) => (createOrderMock as any)(...a),
      addPayment: (...a: unknown[]) => (addPaymentMock as any)(...a),
    },
    prescriptionApi: { getPrescriptions: () => Promise.resolve({ prescriptions: [] }), createPrescription: noop },
    workshopApi: { createJob: noop, updateFittingDetails: noop },
    adminStoreApi: { listStores: noop, getStoreUsers: () => Promise.resolve([]), getStaff: () => Promise.resolve([]) },
    inventoryApi: { searchByBarcode: noop },
    loyaltyApi: { redeem: noop, getBalance: noop },
    storeApi: { getStore: () => Promise.resolve({ store_id: 'BV-BOK-01' }) },
  };
});

vi.mock('../../../services/api/walkouts', () => ({
  walkoutsApi: { walkinsPosIncrement: () => Promise.resolve({ total: 1 }) },
}));

vi.mock('../../../services/api/settings', () => ({
  policiesApi: { getOne: () => Promise.resolve({ value: 12 }) },
}));

import { MemoryRouter } from 'react-router-dom';
import { POSLayout } from '../POSLayout';
import { usePOSStore } from '../../../stores/posStore';
import { ToastProvider } from '../../../context/ToastContext';

function renderPOS() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <POSLayout />
      </ToastProvider>
    </MemoryRouter>,
  );
}

// A ready-to-pay sale: cart Rs 1,000 inclusive, on the payment step.
function seedPaidSale(methods: Array<{ method: string; amount?: number }>) {
  act(() => {
    const s = usePOSStore.getState();
    s.resetTransaction();
    s.setStoreId('BV-BOK-01');
    s.setSalesperson('sp1', 'Sales Person');
    s.setSaleType('otc_sale');
    usePOSStore.setState({ customer: { id: 'c1', name: 'Asha', phone: '9000000001' } as any });
    s.addToCart({
      product_id: 'p1', name: 'Sunglass A', sku: 'SG-1', category: 'SUNGLASSES',
      unit_price: 1000, mrp: 1000, quantity: 1,
    } as any);
    const total = s.getGrandTotal();
    for (const mth of methods) {
      s.addPayment({ method: mth.method as any, amount: mth.amount ?? total });
    }
    s.setStep('payment');
  });
}

async function completeOrder() {
  const btn = await screen.findByRole('button', { name: /Complete order/i });
  expect(btn).toBeEnabled();
  fireEvent.click(btn);
  await waitFor(() => expect(createOrderMock).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(addPaymentMock).toHaveBeenCalled());
}

beforeEach(() => {
  localStorage.clear();
  createOrderMock.mockClear();
  addPaymentMock.mockClear();
  act(() => usePOSStore.getState().resetTransaction());
});

describe('per-sale cash capture wiring (owner ruling 2026-08-25)', () => {
  it('REQUIREMENT: the tendered figure typed into the REAL calculator reaches the CASH leg body', async () => {
    seedPaidSale([{ method: 'CASH' }]);
    renderPOS();

    // The calculator the audit said "collects the tendered figure and
    // discards it": type Rs 2,000 against the Rs 1,000 bill.
    const tenderedInput = await screen.findByPlaceholderText('1000');
    fireEvent.change(tenderedInput, { target: { value: '2000' } });

    await completeOrder();

    const [orderId, body] = addPaymentMock.mock.calls[0] as unknown[] as [string, any];
    expect(orderId).toBe('ORD-77');
    // The money fields are untouched…
    expect(body.method).toBe('CASH');
    expect(body.amount).toBe(1000);
    // …and the accountability record now RIDES ALONG instead of vanishing.
    expect(body.tendered_amount).toBe(2000);
    expect(body.change_amount).toBe(1000);
    // The capture is consumed by the sale: it must not leak into the next one.
    expect(usePOSStore.getState().cash_tender).toBeNull();
  });

  it('OPTIONAL, NOT A WALL: skipping the calculator sends the exact legacy body and the sale completes', async () => {
    seedPaidSale([{ method: 'CASH' }]);
    renderPOS();

    await completeOrder();

    const [, body] = addPaymentMock.mock.calls[0] as unknown[] as [string, any];
    // What actually crosses the wire: JSON.stringify drops undefined keys.
    const wire = JSON.parse(JSON.stringify(body));
    expect(wire).toEqual({ method: 'CASH', amount: 1000 });
    expect('tendered_amount' in wire).toBe(false);
    expect('change_amount' in wire).toBe(false);
    expect('cash_tendered' in wire).toBe(false);
  });

  it('a split bill: the capture lands on the CASH leg only; the UPI leg gains nothing', async () => {
    seedPaidSale([
      { method: 'UPI', amount: 400 },
      { method: 'CASH', amount: 600 },
    ]);
    renderPOS();

    // Anchored to the CASH LEG (600), not the grand total (1000). This file
    // ALREADY asserted the recorded change is anchored to the cash leg
    // (change_amount 100 below); the screen was the half still measuring
    // against the whole bill, which is what produced the owner's
    // "Short: Rs 11,590" on a fully-settled split bill. Only the lookup
    // moves - every assertion below is untouched.
    const tenderedInput = await screen.findByPlaceholderText('600');
    fireEvent.change(tenderedInput, { target: { value: '700' } });

    await completeOrder();
    await waitFor(() => expect(addPaymentMock).toHaveBeenCalledTimes(2));

    const bodies = addPaymentMock.mock.calls.map((c) => (c as unknown[])[1] as any);
    const upi = bodies.find((b) => b.method === 'UPI');
    const cash = bodies.find((b) => b.method === 'CASH');
    expect(JSON.parse(JSON.stringify(upi))).toEqual({ method: 'UPI', amount: 400 });
    expect(cash.amount).toBe(600);
    expect(cash.tendered_amount).toBe(700);
    // Change anchored to the CASH LEG: 700 tendered against the Rs 600 leg.
    expect(cash.change_amount).toBe(100);
  });
});
