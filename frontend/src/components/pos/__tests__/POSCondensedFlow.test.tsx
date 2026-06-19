// ============================================================================
// POS condensed-flow — render + flow tests
// ============================================================================
// The condensed 3-step grouping is the SOLE checkout flow (the classic
// alternative + toggle were removed). These verify that grouping, the
// quick-sale review-skip parity, and the additive Rx source-gating empty-state,
// WITHOUT a backend. The heavy data deps (auth, react-query product/customer
// hooks, API services) are mocked so POSLayout mounts in jsdom; the real
// posStore (Zustand) drives navigation.
//
// These guard the flow's hard rules: condensed grouping (Customer · Products &
// Rx · Pay & Review), quick sales skip Review, and the Rx surface is gated
// behind a source pick (without losing the existing Rx UI, which appears once a
// source is chosen).

import { render, screen, act, fireEvent, within } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// This Node/jsdom combo ships a partial localStorage (no clear/setItem). Replace
// it with a complete Map-backed stub so the posStore persist middleware behaves.
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

// --- Mocks (declared before importing the component under test) -------------

// Stable user reference — returning a fresh object each render would make the
// POSLayout `[user]` effect (setStoreId) loop in tests.
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

// react-query POS hooks → inert, no network.
vi.mock('../../../hooks/usePOSQueries', () => ({
  useProducts: () => ({ data: [], isLoading: false }),
  useCustomerSearch: () => ({ data: [], isLoading: false }),
  useCustomer: () => ({ data: null }),
}));

// GST runtime resolver → static, no /health fetch.
vi.mock('../../../constants/gstRuntime', () => ({
  resolveGstRate: () => 5,
  isInclusivePricing: () => true,
  loadHsnRates: vi.fn(),
  loadPricingMode: vi.fn(),
}));

// API service barrel → every call resolves empty so step components don't throw.
vi.mock('../../../services/api', () => {
  const noop = () => Promise.resolve([]);
  return {
    customerApi: { search: noop, getCustomer: noop },
    orderApi: { createOrder: noop, addPayment: noop },
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

import { POSLayout } from '../POSLayout';
import { usePOSStore } from '../../../stores/posStore';
import { ToastProvider } from '../../../context/ToastContext';

function renderPOS() {
  return render(
    <ToastProvider>
      <POSLayout />
    </ToastProvider>,
  );
}

// Seed a customer + salesperson so navigation guards pass.
function seedSale(saleType: 'quick_sale' | 'prescription_order') {
  act(() => {
    const s = usePOSStore.getState();
    s.resetTransaction();
    s.setStoreId('BV-BOK-01');
    s.setSalesperson('sp1', 'Sales Person');
    s.setSaleType(saleType);
    (s as any).setCustomer
      ? (s as any).setCustomer({ id: 'c1', name: 'Asha', phone: '9000000001' })
      : usePOSStore.setState({ customer: { id: 'c1', name: 'Asha', phone: '9000000001' } as any });
  });
}

beforeEach(() => {
  localStorage.clear();
  act(() => usePOSStore.getState().resetTransaction());
});

describe('POS condensed flow (the only flow)', () => {
  it('renders the 3-step condensed rail', () => {
    seedSale('prescription_order');
    renderPOS();
    // The condensed rail groups: Customer · Products & Rx · Pay & Review.
    expect(screen.getByTitle('Customer')).toBeInTheDocument();
    expect(screen.getByTitle('Products & Rx')).toBeInTheDocument();
    expect(screen.getByTitle('Pay & Review')).toBeInTheDocument();
    // "Checkout · 3 steps" eyebrow confirms the flow length.
    expect(screen.getByText(/Checkout · 3 steps/)).toBeInTheDocument();
  });

  it('has no classic/condensed toggle in the rail', () => {
    seedSale('prescription_order');
    renderPOS();
    // The old segmented control is gone — condensed is always-on.
    expect(screen.queryByRole('button', { name: 'Classic' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Condensed' })).not.toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Checkout flow' })).not.toBeInTheDocument();
    // The standalone classic-only groups never appear.
    expect(screen.queryByTitle('Prescription')).not.toBeInTheDocument();
    expect(screen.queryByTitle('Review')).not.toBeInTheDocument();
  });
});

describe('Rx source-gating (additive)', () => {
  it('hides the Rx surface behind a source pick on the merged Products & Rx step', () => {
    seedSale('prescription_order');
    renderPOS();
    // Move the store onto the products anchor (condensed Products & Rx group).
    act(() => usePOSStore.getState().setStep('products'));
    // Empty state shown; the source picker offers the four sources.
    expect(screen.getByText(/No prescription selected yet/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Use last exam' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '+ Fresh Rx' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'External (upload)' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'No Rx · accessory' })).toBeInTheDocument();
    // The existing Rx UI (Browse All / New Prescription) is NOT shown yet.
    expect(screen.queryByText('Browse All Prescriptions')).not.toBeInTheDocument();
    // Pick "last exam" → empty state clears, existing Rx UI is revealed.
    fireEvent.click(screen.getByRole('button', { name: 'Use last exam' }));
    expect(screen.queryByText(/No prescription selected yet/)).not.toBeInTheDocument();
    expect(screen.getByText('Browse All Prescriptions')).toBeInTheDocument();
    expect(screen.getByText('New Prescription')).toBeInTheDocument();
  });

  it('shows the accessory note for the No-Rx source', () => {
    seedSale('prescription_order');
    renderPOS();
    act(() => usePOSStore.getState().setStep('products'));
    fireEvent.click(screen.getByRole('button', { name: 'No Rx · accessory' }));
    expect(screen.getByText(/Accessory-only sale\./)).toBeInTheDocument();
  });
});

describe('condensed Pay & Review merges Review + Payment (prescription order)', () => {
  it('renders both the Review and Payment captions on the final group', () => {
    seedSale('prescription_order');
    act(() => {
      usePOSStore.getState().addToCart({
        product_id: 'p1', name: 'Frame A', sku: 'FR-1', category: 'FRAMES',
        unit_price: 1000, mrp: 1000, quantity: 1, is_optical: true,
      } as any);
      usePOSStore.getState().setStep('payment');
    });
    renderPOS();
    const scroll = document.querySelector('.pos-payreview');
    expect(scroll).toBeTruthy();
    const caps = within(scroll as HTMLElement).getAllByText(/Review|Payment/);
    expect(caps.length).toBeGreaterThanOrEqual(2);
  });
});

describe('quick sale excludes the Review step (origin/main QUICK_STEPS parity)', () => {
  it('condensed quick sale: no Review/Pay-&-Review/Products-&-Rx rail entries', () => {
    seedSale('quick_sale');
    renderPOS();
    // 3 steps: Customer · Products · Payment. No review anywhere.
    expect(screen.getByText(/Checkout · 3 steps/)).toBeInTheDocument();
    expect(screen.getByTitle('Customer')).toBeInTheDocument();
    expect(screen.getByTitle('Products')).toBeInTheDocument();
    expect(screen.getByTitle('Payment')).toBeInTheDocument();
    expect(screen.queryByTitle('Review')).not.toBeInTheDocument();
    expect(screen.queryByTitle('Pay & Review')).not.toBeInTheDocument();
    expect(screen.queryByTitle('Products & Rx')).not.toBeInTheDocument();
  });

  it('condensed quick sale: payment step does NOT render the merged review panel', () => {
    seedSale('quick_sale');
    act(() => {
      usePOSStore.getState().addToCart({
        product_id: 'p1', name: 'Frame A', sku: 'FR-1', category: 'FRAMES',
        unit_price: 1000, mrp: 1000, quantity: 1, is_optical: true,
      } as any);
      usePOSStore.getState().setStep('payment');
    });
    renderPOS();
    // The two-column Review+Payment merge must NOT exist for a quick sale.
    expect(document.querySelector('.pos-payreview')).toBeNull();
  });
});

describe("Rx 'No Rx · accessory' source unblocks Continue on a prescription order", () => {
  it('lets the prescription step proceed without an Rx once accessory is picked', () => {
    seedSale('prescription_order');
    act(() => {
      usePOSStore.getState().addToCart({
        product_id: 'a1', name: 'Lens Cloth', sku: 'ACC-1', category: 'ACCESSORIES',
        unit_price: 100, mrp: 100, quantity: 1, is_optical: false,
      } as any);
      usePOSStore.getState().setStep('products');
    });
    renderPOS();
    // The Continue button sits in the action bar. Before picking a source, the
    // merged Products & Rx group is not satisfied (no Rx) → Continue disabled.
    const continueBtn = () => screen.getByRole('button', { name: /Continue/i });
    expect(continueBtn()).toBeDisabled();
    // Pick the accessory/no-Rx source → step is allowed to proceed.
    fireEvent.click(screen.getByRole('button', { name: 'No Rx · accessory' }));
    expect(continueBtn()).toBeEnabled();
  });
});
