// ============================================================================
// The cart carries the product's HSN -- and the ORDER still does not
// ============================================================================
// Two halves of the same rule, both on the POS screen itself.
//
// 1. The first link of the chain the tax invoice depends on. POSInvoiceStoredHsn
//    proves the cart line reaches the printed document; this proves the
//    product's hsn_code reaches the cart line. Drop it here and every
//    downstream test still passes while the invoice quietly goes back to
//    deriving a code from the category -- which printed 900410, the SUNGLASSES
//    code, on all 35 live smartglasses products whose records say 852580.
//
// 2. THE MONEY GUARD. orders.py rates a line with
//    resolve_gst_rate(hsn_code=item.hsn_code, category=item.category), and an
//    exact HSN hit BEATS the category. So the moment the checkout payload
//    starts carrying hsn_code, a stored code can change what a customer is
//    CHARGED -- not just what the invoice files the supply under. The payload
//    is an explicit field list today and hsn_code is not in it; this pins that.

import { render, screen, act, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// Complete Map-backed localStorage for the posStore persist middleware.
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

// ONE stable object: POSLayout has an effect keyed on `user`, so a fresh
// literal per call re-runs setStoreId every render and React aborts with
// "Maximum update depth exceeded" before anything is asserted.
const MOCK_AUTH = { user: {
  id: 'u1', name: 'Test Cashier', roles: ['STORE_MANAGER'],
  activeRole: 'STORE_MANAGER', activeStoreId: 'BV-BOK-01',
  storeIds: ['BV-BOK-01'], discountCap: 20,
} };
vi.mock('../../../context/AuthContext', () => ({ useAuth: () => MOCK_AUTH }));

// One catalogue product, carrying the HSN its record holds. Its CATEGORY
// implies a different code (SUNGLASS -> 900410), so only the record can
// explain 852580 turning up on the cart line.
const PRODUCT = {
  product_id: 'p1', name: 'Ray-Ban Meta Wayfarer', sku: 'SMTSG-1',
  brand: 'Ray-Ban', category: 'SUNGLASS', hsn_code: '852580',
  mrp: 29900, offer_price: 29900,
};

vi.mock('../../../hooks/usePOSQueries', () => ({
  useProducts: () => ({ data: [PRODUCT], isLoading: false }),
  useCustomerSearch: () => ({ data: [], isLoading: false }),
  useCustomer: () => ({ data: null }),
  useStores: () => ({ data: [], isLoading: false }),
}));

// The REAL rate resolver, fed the real endpoint shape. Stubbing it out would
// make the Review-step assertion below meaningless -- the whole question is
// which argument the resolver is called with.
const { apiGet } = vi.hoisted(() => ({ apiGet: vi.fn() }));
vi.mock('../../../services/api/client', () => ({ default: { get: apiGet } }));
const GST = {
  by_hsn: { '900410': 18, '900311': 5 },
  by_cat: { SUNGLASSES: 18, FRAME: 5 },
  category_hint: { SUNGLASS: 'SUNGLASSES', FRAME: 'FRAME' },
  hsn_by_category: { SUNGLASS: '900410', FRAME: '900311' },
  rate_by_category: { SUNGLASS: 18, SUNGLASSES: 18, FRAME: 5 },
};

// Capture the exact order payload the POS sends.
const createOrderMock = vi.fn(() => Promise.resolve({}));
// The barcode door: a second, hand-written mapping of the product master onto
// a cart-ready object, in the same component.
const scanMock = vi.fn(() => Promise.resolve({} as any));
vi.mock('../../../services/api', () => {
  const noop = () => Promise.resolve([]);
  return {
    customerApi: { search: noop, getCustomer: noop },
    orderApi: { createOrder: (...a: unknown[]) => (createOrderMock as any)(...a), addPayment: noop },
    prescriptionApi: { getPrescriptions: () => Promise.resolve({ prescriptions: [] }), createPrescription: noop },
    workshopApi: { createJob: noop, updateFittingDetails: noop },
    adminStoreApi: { listStores: noop, getStoreUsers: () => Promise.resolve([]), getStaff: () => Promise.resolve([]) },
    inventoryApi: { searchByBarcode: (...a: unknown[]) => (scanMock as any)(...a) },
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
import { loadHsnRates } from '../../../constants/gstRuntime';
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

function seedSale() {
  act(() => {
    const s = usePOSStore.getState();
    s.resetTransaction();
    s.setStoreId('BV-BOK-01');
    s.setSalesperson('sp1', 'Sales Person');
    usePOSStore.setState({ customer: { id: 'c1', name: 'Asha', phone: '9000000001' } as never });
    s.setStep('products');
  });
}

beforeEach(async () => {
  localStorage.clear();
  createOrderMock.mockClear();
  scanMock.mockReset();
  apiGet.mockResolvedValue({ data: GST });
  await loadHsnRates();
  act(() => usePOSStore.getState().resetTransaction());
});

describe('POS add-to-cart', () => {
  it("puts the product's stored HSN on the cart line", async () => {
    seedSale();
    renderPOS();

    const card = await screen.findByRole('button', { name: /Ray-Ban Meta Wayfarer/i });
    fireEvent.click(card);

    await waitFor(() => expect(usePOSStore.getState().cart).toHaveLength(1));
    // The line carries the code the PRODUCT is registered under, not the one
    // its category implies (SUNGLASS -> 900410).
    expect(usePOSStore.getState().cart[0].hsn_code).toBe('852580');
  });

  it('carries it in through the BARCODE door too, not just the search list', async () => {
    // handleBarcodeScan builds its OWN product object out of the scan hit
    // before handing it to the same add-to-cart function -- one rule, two
    // hand-written field lists. Losing hsn_code on this one alone would let
    // every SCANNED sale (which is most of them) print a derived code while
    // the search-click path stayed correct, and nothing would say so.
    scanMock.mockResolvedValue({
      barcode: '8901234567890', product: { ...PRODUCT },
    } as never);
    seedSale();
    renderPOS();

    const box = await screen.findByPlaceholderText(/Scan barcode or search products/i);
    fireEvent.change(box, { target: { value: '8901234567890' } });
    fireEvent.keyDown(box, { key: 'Enter' });

    await waitFor(() => expect(usePOSStore.getState().cart).toHaveLength(1));
    expect(usePOSStore.getState().cart[0].hsn_code).toBe('852580');
  });

  it('does NOT send that HSN to the server, so no billed rate can move', async () => {
    seedSale();
    renderPOS();

    fireEvent.click(await screen.findByRole('button', { name: /Ray-Ban Meta Wayfarer/i }));
    await waitFor(() => expect(usePOSStore.getState().cart).toHaveLength(1));

    act(() => {
      const s = usePOSStore.getState();
      s.addPayment({ method: 'CASH', amount: s.getGrandTotal() } as never);
      s.setStep('payment');
    });
    const done = await screen.findByRole('button', { name: /Complete order/i });
    await waitFor(() => expect(done).toBeEnabled());
    fireEvent.click(done);
    await waitFor(() => expect(createOrderMock).toHaveBeenCalledTimes(1));

    const body = (createOrderMock.mock.calls[0] as unknown[])[0] as any;
    expect(body.items).toHaveLength(1);
    // The cart HAS the code...
    expect(usePOSStore.getState().cart[0].hsn_code).toBe('852580');
    // ...and the wire does not. orders.py would let an exact HSN override the
    // category rate, so this is the line between "files the supply correctly"
    // and "changes what the customer pays".
    expect(Object.keys(body.items[0])).not.toContain('hsn_code');
    expect(body.items[0].category).toBe('SUNGLASS');
  });
});

describe('the Review screen quotes the same rate as the invoice', () => {
  it('shows the CATEGORY rate for a line whose record carries another code', async () => {
    // Order Review recomputes the per-line GST% and the tax breakdown with its
    // OWN two calls to the resolver -- a third and fourth copy of the same
    // lookup, in the same component as the cart. A 5% frame whose record
    // carries 900410 (a master row at 18%) is the fixture that can tell them
    // apart, and neither is reached by the cart-total tests.
    act(() => {
      const st = usePOSStore.getState();
      st.resetTransaction();
      st.setStoreId('BV-BOK-01');
      st.setSalesperson('sp1', 'Sales Person');
      st.setSaleType('prescription_order');
      usePOSStore.setState({ customer: { id: 'c1', name: 'Asha', phone: '9000000001' } as never });
      st.addToCart({
        product_id: 'p9', name: 'Titan Frame', sku: 'FR-9',
        category: 'FRAME', hsn_code: '900410',
        unit_price: 1050, mrp: 1050, quantity: 1, is_optical: true,
      } as never);
    });
    act(() => usePOSStore.getState().setStep('payment'));   // merged Pay & Review
    renderPOS();

    // The Review step's per-line note box marks the screen as rendered.
    await screen.findByPlaceholderText('Item notes (PD, fitting, tint, coating...)');
    const row = screen.getAllByText('Titan Frame')
      .map((n) => n.closest('tr'))
      .find((r): r is HTMLTableRowElement => !!r)!;
    expect(row.textContent).toContain('5%');
    expect(row.textContent).not.toContain('18%');
    // ...and the tax breakdown under it, which is the second copy.
    expect(screen.getAllByText(/5%/).length).toBeGreaterThan(1);
    expect(screen.queryAllByText(/18%/)).toHaveLength(0);
  });
});
