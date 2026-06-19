// ============================================================================
// POS condensed-flow redesign — render + flow tests
// ============================================================================
// Verifies the condensed (default) 3-step grouping, the classic 6-step toggle,
// and the additive Rx source-gating empty-state, WITHOUT a backend. The heavy
// data deps (auth, react-query product/customer hooks, API services) are mocked
// so POSLayout mounts in jsdom; the real posStore (Zustand) drives navigation.
//
// These guard the redesign's two hard rules: condensed is default + classic
// stays toggleable, and the Rx surface is gated behind a source pick (without
// losing the existing Rx UI, which appears once a source is chosen).

import { render, screen, act, fireEvent, within } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// This Node/jsdom combo ships a partial localStorage (no clear/setItem). Replace
// it with a complete Map-backed stub so persist + usePOSWorkflow behave.
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
import { POS_WORKFLOW_KEY } from '../usePOSWorkflow';

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

describe('POS condensed flow (default)', () => {
  it('defaults to the 3-step condensed rail', () => {
    seedSale('prescription_order');
    renderPOS();
    // The condensed rail groups: Customer · Products & Rx · Pay & Review.
    expect(screen.getByTitle('Customer')).toBeInTheDocument();
    expect(screen.getByTitle('Products & Rx')).toBeInTheDocument();
    expect(screen.getByTitle('Pay & Review')).toBeInTheDocument();
    // "Checkout · 3 steps" eyebrow confirms the flow length.
    expect(screen.getByText(/Checkout · 3 steps/)).toBeInTheDocument();
  });

  it('toggles to the classic 6-step flow and back', () => {
    seedSale('prescription_order');
    renderPOS();
    // Flip to classic → the standalone Prescription / Review / Payment groups appear.
    fireEvent.click(screen.getByRole('button', { name: 'Classic' }));
    expect(screen.getByTitle('Prescription')).toBeInTheDocument();
    expect(screen.getByTitle('Review')).toBeInTheDocument();
    expect(screen.getByTitle('Payment')).toBeInTheDocument();
    expect(localStorage.getItem(POS_WORKFLOW_KEY)).toBe('classic');
    // The merged condensed group is gone in classic.
    expect(screen.queryByTitle('Products & Rx')).not.toBeInTheDocument();
    // Flip back.
    fireEvent.click(screen.getByRole('button', { name: 'Condensed' }));
    expect(screen.getByTitle('Products & Rx')).toBeInTheDocument();
    expect(localStorage.getItem(POS_WORKFLOW_KEY)).toBe('condensed');
  });

  it('persists the workflow preference across remounts', () => {
    localStorage.setItem(POS_WORKFLOW_KEY, 'classic');
    seedSale('prescription_order');
    renderPOS();
    expect(screen.getByText(/Checkout · 5 steps/)).toBeInTheDocument();
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

describe('condensed Pay & Review merges Review + Payment', () => {
  it('renders both the Review and Payment captions on the final group', () => {
    seedSale('quick_sale');
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
