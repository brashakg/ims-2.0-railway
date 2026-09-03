// ============================================================================
// POS route-resolution guard (legacy-till retirement gate, 2026-09-03/04)
// ============================================================================
// Four shops bill on /pos. The owner called "retire old pos" on 2026-09-03.
// The salvage audit (routes/posRoutes.tsx comment has the summary) found the
// legacy till (components/pos/POSLayout.tsx) is still the ONLY surface that can:
//
//  (a) ring a prescription_order -- the sale type submitOrder.ts keys the
//      workshop-job auto-create and the "Rx order needs a lens" rule on
//      (submitOrder.ts: `store.sale_type === 'prescription_order'`). Only
//      POSLayout calls setSaleType('prescription_order'); BillingSurface never
//      sets sale_type and GeneralCounterSurface forces 'quick_sale'.
//  (b) take a DEPOSIT (is_advance_payment: pay part now, balance at delivery).
//      submitOrder.ts refuses a partly-paid bill unless that flag is set, and
//      only POSLayout's review panel sets it. The delivery counter exists to
//      collect exactly that balance, so retiring /pos today removes the way
//      to create the order it collects on.
//  (c) change / clear a picked customer mid-bill (no setCustomer(null) door on
//      the new surfaces; with an empty cart "Hold bill" is disabled, so a
//      wrong pick can only be cleared by reloading the page).
//
// Until those land on the new surfaces, /pos must keep resolving to POSLayout,
// and the three replacement routes must keep resolving too.
//
// What each failure means:
//  - "/pos still mounts the LEGACY till" fails if the route is dropped (staff
//    bookmarks 404) OR if it is repointed at a new surface before the gaps
//    close (a premature retirement that silently removes deposit-taking and
//    workshop-job creation from the shops).
//  - the /pos/new | /pos/counter | /pos/delivery cases fail if a replacement
//    address stops resolving.
//  - the "REPLACED capabilities" cases fail if a capability the audit marked
//    REPLACED (customer create, hold/recall, walk-in/walkout, bill note,
//    delivery date, counter handover) stops being reachable on the surface
//    that replaced it -- i.e. the retirement's own precondition regresses.
// When the owner calls the real switch, retarget the first case to assert the
// redirect instead -- do not delete the file; the addresses must work forever.

import { Suspense } from 'react';
import { render, screen, act } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// jsdom in this repo ships a partial localStorage; posStore's persist
// middleware needs the full surface (same stub the other POS suites use).
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

// --- Mocks (before importing the routes) ------------------------------------

// Authenticated SUPERADMIN with NO active store on the USER. The legacy till
// reads the store from the user only, so /pos always renders its cheap
// "no store" guard branch. The new surfaces fall back to posStore.store_id,
// which the reachability cases set -- so they mount the full register
// without a network (every fetch a customer-less mount makes is mocked
// below). Stable reference -- a fresh object per render would loop
// POSLayout's [user] effect.
const MOCK_USER = {
  id: 'u1',
  name: 'Guard User',
  roles: ['SUPERADMIN'],
  activeRole: 'SUPERADMIN',
  activeStoreId: '',
  storeIds: [],
  discountCap: 20,
};
const MOCK_AUTH = {
  user: MOCK_USER,
  isAuthenticated: true,
  isLoading: false,
  hasRole: () => true,
  hasPermission: () => true,
  hasModuleAccess: () => true,
};
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => MOCK_AUTH,
}));

// react-query POS hooks -> inert (also feeds useIsOnlineStore's store list).
vi.mock('../../hooks/usePOSQueries', () => ({
  useProducts: () => ({ data: [], isLoading: false }),
  useCustomerSearch: () => ({ data: [], isLoading: false }),
  useCustomer: () => ({ data: null }),
  useStores: () => ({ data: [], isLoading: false }),
}));

// API barrel -> every call resolves empty (SalespersonPicker fetches staff on
// mount; StepPayment reads the store's EMI rate; nothing else fires without a
// customer on the bill).
vi.mock('../../services/api', () => {
  const noop = () => Promise.resolve([]);
  return {
    customerApi: { search: noop, getCustomer: noop, getCustomers: noop },
    orderApi: { createOrder: noop, addPayment: noop, getOrders: noop },
    prescriptionApi: { getPrescriptions: () => Promise.resolve({ prescriptions: [] }) },
    workshopApi: { createJob: noop, updateFittingDetails: noop },
    adminStoreApi: { listStores: noop, getStoreUsers: () => Promise.resolve([]), getStaff: () => Promise.resolve([]) },
    inventoryApi: { searchByBarcode: noop },
    loyaltyApi: { redeem: noop, getBalance: noop },
    storeApi: { getStore: () => Promise.resolve({}) },
  };
});
vi.mock('../../services/api/walkouts', () => ({
  walkoutsApi: { walkinsPosIncrement: () => Promise.resolve({ total: 1 }) },
}));

import { MemoryRouter, Routes } from 'react-router-dom';
import { posRoutes } from '../posRoutes';
import { usePOSStore } from '../../stores/posStore';
import { ToastProvider } from '../../context/ToastContext';

// Lazy route chunks compile on demand under vitest; the heavier surfaces can
// take more than the 1s default query timeout on a loaded machine.
const FIND = { timeout: 20000 };

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ToastProvider>
        <Suspense fallback={<div data-testid="lazy-loading" />}>
          <Routes>{posRoutes}</Routes>
        </Suspense>
      </ToastProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.clear();
  act(() => {
    usePOSStore.getState().resetTransaction();
    // The new surfaces fall back to store.store_id when the user has no active
    // store -- force it empty so every surface takes its no-store guard branch.
    usePOSStore.getState().setStoreId('');
  });
});

describe('POS routes keep resolving (retirement gate)', () => {
  it('/pos still mounts the LEGACY till (POSLayout), not a redirect or a new surface', async () => {
    renderAt('/pos');
    // This exact copy exists ONLY in POSLayout's no-store panel. BillingSurface
    // and GeneralCounterSurface print "Pick one from the header before
    // billing." instead -- so pointing /pos at either of them fails here.
    expect(
      await screen.findByText(/POS requires an active store to process transactions/, undefined, FIND),
    ).toBeInTheDocument();
  }, 30000);

  it('/pos/new resolves (BillingSurface)', async () => {
    renderAt('/pos/new');
    expect(
      await screen.findByText(/Pick one from the header before billing/, undefined, FIND),
    ).toBeInTheDocument();
  }, 30000);

  it('/pos/counter resolves (GeneralCounterSurface)', async () => {
    renderAt('/pos/counter');
    expect(
      await screen.findByText(/Pick one from the header before billing/, undefined, FIND),
    ).toBeInTheDocument();
  }, 30000);

  it('/pos/delivery resolves (DeliverySurface)', async () => {
    renderAt('/pos/delivery');
    expect(
      await screen.findByPlaceholderText(/Scan job card/, undefined, FIND),
    ).toBeInTheDocument();
  }, 30000);
});

// ----------------------------------------------------------------------------
// REPLACED capabilities are reachable on the surface that replaced them.
// ----------------------------------------------------------------------------
// The salvage audit may only retire a legacy capability once the new surface
// actually offers it. These mount the FULL register (store set on posStore,
// no customer on the bill) and look for the real controls by their visible
// label / accessible name -- the thing a cashier taps -- not for a component
// name. Removing HeldBillsControls, the "+ New customer" door, the walk-in /
// walkout pair, the bill note or the counter's handover choice from a surface
// fails here BY NAME.
describe('REPLACED capabilities are reachable on the new surfaces', () => {
  beforeEach(() => {
    act(() => {
      usePOSStore.getState().setStoreId('S1');
    });
  });

  it('/pos/new offers customer create, hold/recall, walk-in/walkout, delivery date and bill note', async () => {
    renderAt('/pos/new');
    // The register is up (not the no-store panel) once the scanner row paints.
    expect(await screen.findByPlaceholderText(/Scan barcode/, undefined, FIND)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /\+ New customer/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Hold bill$/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Held/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /\+1 walk-in/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Walkout$/ })).toBeInTheDocument();
    expect(screen.getByLabelText('Delivery or collection date')).toBeInTheDocument();
    expect(screen.getByLabelText('Note for this bill')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Complete sale/ })).toBeInTheDocument();
  }, 30000);

  it('/pos/counter offers customer create, hold/recall, walk-in/walkout, handover choice and bill note', async () => {
    renderAt('/pos/counter');
    expect(await screen.findByPlaceholderText(/Scan barcode/, undefined, FIND)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /\+ New customer/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Hold bill$/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Held/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /\+1 walk-in/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Walkout$/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Take away now/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Home delivery/ })).toBeInTheDocument();
    expect(screen.getByLabelText('Note for this bill')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Complete sale/ })).toBeInTheDocument();
  }, 30000);
});
