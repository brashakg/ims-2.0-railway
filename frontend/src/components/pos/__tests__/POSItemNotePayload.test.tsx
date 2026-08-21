// ============================================================================
// POS line-item notes — ONE note box, ALWAYS sent (owner ruling 2026-08-21)
// ============================================================================
// Before this fix the cart line had TWO note fields: POSCart wrote `notes`
// (via updateItemNote) and the Review step wrote `item_note` (via a second
// setter), but the order payload sent ONLY `item_note` — a note typed on the
// cart screen was SILENTLY LOST from the order. These tests type into the
// REAL inputs on both screens and assert the createOrder PAYLOAD (not a log,
// not store internals alone) carries the note.

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
const MOCK_AUTH = { user: MOCK_USER };
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => MOCK_AUTH,
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

// Capture the exact order payload the POS sends.
const createOrderMock = vi.fn(() => Promise.resolve({}));
vi.mock('../../../services/api', () => {
  const noop = () => Promise.resolve([]);
  return {
    customerApi: { search: noop, getCustomer: noop },
    orderApi: { createOrder: (...a: unknown[]) => (createOrderMock as any)(...a), addPayment: noop },
    prescriptionApi: { getPrescriptions: () => Promise.resolve({ prescriptions: [] }), createPrescription: noop },
    workshopApi: { createJob: noop, updateFittingDetails: noop },
    adminStoreApi: { listStores: noop, getStoreUsers: () => Promise.resolve([]), getStaff: () => Promise.resolve([]) },
    inventoryApi: { searchByBarcode: noop },
    loyaltyApi: { redeem: noop, getBalance: noop },
  };
});

vi.mock('../../../services/api/walkouts', () => ({
  walkoutsApi: { walkinsPosIncrement: () => Promise.resolve({ total: 1 }) },
}));

// StepPayment now fetches the EMI-rate policy on mount; keep it inert here.
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

function seedSaleWithOpticalItem() {
  act(() => {
    const s = usePOSStore.getState();
    s.resetTransaction();
    s.setStoreId('BV-BOK-01');
    s.setSalesperson('sp1', 'Sales Person');
    s.setSaleType('prescription_order');
    usePOSStore.setState({ customer: { id: 'c1', name: 'Asha', phone: '9000000001' } as any });
    s.addToCart({
      product_id: 'p1', name: 'Frame A', sku: 'FR-1', category: 'FRAMES',
      unit_price: 1000, mrp: 1000, quantity: 1, is_optical: true,
    } as any);
  });
}

/** Pay the bill in full and click the real "Complete order" button; returns
 *  the captured createOrder payload. */
async function completeOrder() {
  act(() => {
    const s = usePOSStore.getState();
    s.addPayment({ method: 'CASH', amount: s.getGrandTotal() });
    s.setStep('payment');
  });
  const btn = await screen.findByRole('button', { name: /Complete order/i });
  expect(btn).toBeEnabled();
  fireEvent.click(btn);
  await waitFor(() => expect(createOrderMock).toHaveBeenCalledTimes(1));
  return (createOrderMock.mock.calls[0] as unknown[])[0] as any;
}

const cartNoteInput = () => screen.getByPlaceholderText('PD / Fitting / Tint notes…') as HTMLInputElement;
const reviewNoteInput = () => screen.getByPlaceholderText('Item notes (PD, fitting, tint, coating...)') as HTMLInputElement;

beforeEach(() => {
  localStorage.clear();
  createOrderMock.mockClear();
  act(() => usePOSStore.getState().resetTransaction());
});

describe('one line-item note box, always sent', () => {
  it('REQUIREMENT (the lost-note bug): a note typed on the CART screen is in the createOrder payload as item_note', async () => {
    seedSaleWithOpticalItem();
    act(() => usePOSStore.getState().setStep('products'));
    renderPOS();

    fireEvent.change(cartNoteInput(), { target: { value: 'PD 62 · tint grey' } });

    const body = await completeOrder();
    expect(body.items).toHaveLength(1);
    expect(body.items[0].item_note).toBe('PD 62 · tint grey');
  });

  it('REQUIREMENT: a note typed on the REVIEW screen is in the createOrder payload as item_note', async () => {
    seedSaleWithOpticalItem();
    act(() => usePOSStore.getState().setStep('payment')); // merged Pay & Review
    renderPOS();

    fireEvent.change(reviewNoteInput(), { target: { value: 'fit low bridge' } });
    fireEvent.blur(reviewNoteInput()); // review editor commits on blur

    const body = await completeOrder();
    expect(body.items[0].item_note).toBe('fit low bridge');
  });

  it('REQUIREMENT (one source of truth): cart note then review edit -> the review edit wins, BOTH screens show it, payload carries it', async () => {
    seedSaleWithOpticalItem();
    act(() => usePOSStore.getState().setStep('products'));
    renderPOS();

    // Type on the cart screen…
    fireEvent.change(cartNoteInput(), { target: { value: 'PD 62' } });
    // …move to Review: the SAME note is visible there (not a blank twin field).
    act(() => usePOSStore.getState().setStep('payment'));
    expect(reviewNoteInput().value).toBe('PD 62');
    // Edit it on Review…
    fireEvent.change(reviewNoteInput(), { target: { value: 'PD 62 · edited at review' } });
    fireEvent.blur(reviewNoteInput());
    // …and the cart screen now shows the review edit too.
    act(() => usePOSStore.getState().setStep('products'));
    expect(cartNoteInput().value).toBe('PD 62 · edited at review');

    const body = await completeOrder();
    expect(body.items[0].item_note).toBe('PD 62 · edited at review');
  });

  it('no note -> item_note is absent from the wire payload (no undefined/null churn)', async () => {
    seedSaleWithOpticalItem();
    act(() => usePOSStore.getState().setStep('payment'));
    renderPOS();

    const body = await completeOrder();
    expect(body.items[0].item_note).toBeUndefined();
    // What actually crosses the wire: JSON.stringify drops undefined keys.
    expect('item_note' in JSON.parse(JSON.stringify(body)).items[0]).toBe(false);
  });
});
