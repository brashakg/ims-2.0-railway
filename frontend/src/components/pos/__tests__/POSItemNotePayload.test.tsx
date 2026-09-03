// ============================================================================
// POS line-item notes — ONE note box, ALWAYS sent (owner ruling 2026-08-21)
// ============================================================================
// Before this fix the cart line had TWO note fields: POSCart wrote `notes`
// (via updateItemNote) and the classic Review step wrote `item_note` (via a
// second setter), but the order payload sent ONLY `item_note` — a note typed
// on the cart screen was SILENTLY LOST from the order. These tests type into
// the REAL input and assert the createOrder PAYLOAD (not a log, not store
// internals alone) carries the note.
//
// The till is BillingSurface (the legacy wizard was retired 2026-09-04); its
// cart IS the review, so there is exactly one note box and the second-editor
// half of the bug cannot recur. The two cases that typed into the wizard's
// Review-screen box retired with it (owner ruling 2026-09-04).

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
const MOCK_AUTH = {
  user: MOCK_USER,
  // CustomerCardWithLoyalty gates its edit door on hasRole.
  hasRole: (r: string | string[]) => [r].flat().some((x) => MOCK_USER.roles.includes(x)),
};
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
    // POSPayment reads the EMI rate off the store detail; an unmocked read
    // here would reject and the screen falls back to 12% -- fine for these
    // tests, but mock it resolved so no unhandled-rejection noise.
    storeApi: { getStore: () => Promise.resolve({ store_id: 'BV-BOK-01' }) },
  };
});

// Leaves of the surface that fetch through DIRECT module imports, not the
// barrel: loyalty (customer card + loyalty tender), customers (search bar +
// store-credit tender), handoffs (Rx picker). Inert -- none is under test.
vi.mock('../../../services/api/loyalty', () => ({
  loyaltyApi: {
    getAccount: () => Promise.resolve({
      account: { balance_points: 0, tier: 'BRONZE' }, settings: {}, expiring_soon_points: 0,
    }),
  },
}));
vi.mock('../../../services/api/customers', () => ({
  customerApi: {
    getCustomers: () => Promise.resolve([]),
    getCustomer: () => Promise.resolve(null),
    createCustomer: () => Promise.resolve({}),
    getStoreCreditLedger: () => Promise.resolve({ balance: 0 }),
  },
  customersApi: {},
}));
vi.mock('../../../services/api/handoffs', () => ({
  handoffsApi: { listClinicalInbox: () => Promise.resolve({ handoffs: [] }) },
}));

vi.mock('../../../services/api/walkouts', () => ({
  walkoutsApi: { walkinsPosIncrement: () => Promise.resolve({ total: 1 }) },
}));

// StepPayment now fetches the EMI-rate policy on mount; keep it inert here.
vi.mock('../../../services/api/settings', () => ({
  policiesApi: { getOne: () => Promise.resolve({ value: 12 }) },
}));

// Off-assertion strips that fetch through modules the barrel mock does not cover.
vi.mock('../../../pages/pos/next/PosWidgets', () => ({ PosWidgets: () => null }));
vi.mock('../../../pages/pos/next/SaleCompleteScreen', () => ({ default: () => null }));

import { MemoryRouter } from 'react-router-dom';
import { BillingSurface } from '../../../pages/pos/next/BillingSurface';
import { usePOSStore } from '../../../stores/posStore';
import { ToastProvider } from '../../../context/ToastContext';

function renderPOS() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <BillingSurface />
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

/** Pay the bill in full and click the real "Complete sale" button; returns
 *  the captured createOrder payload. */
async function completeOrder() {
  act(() => {
    const s = usePOSStore.getState();
    s.addPayment({ method: 'CASH', amount: s.getGrandTotal() });
  });
  const btn = await screen.findByRole('button', { name: /Complete sale/i });
  expect(btn).toBeEnabled();
  fireEvent.click(btn);
  await waitFor(() => expect(createOrderMock).toHaveBeenCalledTimes(1));
  return (createOrderMock.mock.calls[0] as unknown[])[0] as any;
}

const cartNoteInput = () => screen.getByPlaceholderText('PD / Fitting / Tint notes…') as HTMLInputElement;

beforeEach(() => {
  localStorage.clear();
  createOrderMock.mockClear();
  act(() => usePOSStore.getState().resetTransaction());
});

describe('one line-item note box, always sent', () => {
  it('REQUIREMENT (the lost-note bug): a note typed on the CART screen is in the createOrder payload as item_note', async () => {
    seedSaleWithOpticalItem();
    renderPOS();

    fireEvent.change(cartNoteInput(), { target: { value: 'PD 62 · tint grey' } });

    const body = await completeOrder();
    expect(body.items).toHaveLength(1);
    expect(body.items[0].item_note).toBe('PD 62 · tint grey');
  });

  it('no note -> item_note is absent from the wire payload (no undefined/null churn)', async () => {
    seedSaleWithOpticalItem();
    renderPOS();

    const body = await completeOrder();
    expect(body.items[0].item_note).toBeUndefined();
    // What actually crosses the wire: JSON.stringify drops undefined keys.
    expect('item_note' in JSON.parse(JSON.stringify(body)).items[0]).toBe(false);
  });
});
