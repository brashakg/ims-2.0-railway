// ============================================================================
// POS route-resolution guard (legacy-till retirement, 2026-09-03/04)
// ============================================================================
// Four shops bill on /pos. The owner called "retire old pos" on 2026-09-03.
// The salvage audit found three capabilities only the legacy wizard till had
// (ring a prescription_order, take a deposit, clear a picked customer); all
// three landed on BillingSurface (pages/pos/next/__tests__/
// billingSurfaceRetirementGaps.test.tsx pins each), so /pos now REDIRECTS to
// /pos/new. The address must keep working forever: staff have it bookmarked,
// and walkouts/ResultPanel deep-links it with ?customer_id&walkout_id&
// return_to -- nothing reads those yet, but the redirect must not drop them.
//
// What each failure means:
//  - "/pos redirects" fails if the route is dropped (staff bookmarks 404), if
//    /pos renders a surface IN PLACE instead of redirecting (two addresses for
//    one till), or if the redirect loses the query string (the walkout
//    hand-off silently loses its ids).
//  - the /pos/new | /pos/counter | /pos/delivery cases fail if a replacement
//    address stops resolving.
//  - the "REPLACED capabilities" cases fail if a capability the audit marked
//    REPLACED (customer create, hold/recall, walk-in/walkout, bill note,
//    delivery date, counter handover) stops being reachable on the surface
//    that replaced it -- i.e. the retirement's own precondition regresses.

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

// Authenticated SUPERADMIN with NO active store on the USER. The surfaces fall
// back to posStore.store_id, which the reachability cases set -- so they mount
// the full register without a network (every fetch a customer-less mount
// makes is mocked below); with it empty they render their cheap no-store
// guard branch. Stable reference so no [user] effect loops.
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

import { MemoryRouter, Routes, useLocation } from 'react-router-dom';
import { posRoutes } from '../posRoutes';
import { usePOSStore } from '../../stores/posStore';
import { ToastProvider } from '../../context/ToastContext';

// Lazy route chunks compile on demand under vitest; the heavier surfaces can
// take more than the 1s default query timeout on a loaded machine.
const FIND = { timeout: 20000 };

/** Where the router actually ended up -- pathname + search, as one string. */
function LocationProbe() {
  const { pathname, search } = useLocation();
  return <div data-testid="location">{pathname + search}</div>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ToastProvider>
        <LocationProbe />
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
    // The surfaces fall back to store.store_id when the user has no active
    // store -- force it empty so every surface takes its no-store guard branch.
    usePOSStore.getState().setStoreId('');
  });
});

describe('POS routes keep resolving (retirement gate)', () => {
  it('/pos redirects to /pos/new with the query string intact', async () => {
    // The exact deep-link walkouts/ResultPanel builds.
    const query = '?customer_id=c1&walkout_id=w1&return_to=%2Fwalkouts%2Fw1';
    renderAt(`/pos${query}`);
    // This copy exists ONLY in BillingSurface's / GeneralCounterSurface's
    // no-store panel, so the redirect LANDED on the new till rather than
    // rendering something in place.
    expect(
      await screen.findByText(/Pick one from the header before billing/, undefined, FIND),
    ).toBeInTheDocument();
    // ...at the new address, with every query parameter still attached.
    expect(screen.getByTestId('location').textContent).toBe(`/pos/new${query}`);
  }, 30000);

  it('/pos/new resolves (BillingSurface)', async () => {
    renderAt('/pos/new');
    expect(
      await screen.findByText(/Pick one from the header before billing/, undefined, FIND),
    ).toBeInTheDocument();
    expect(screen.getByTestId('location').textContent).toBe('/pos/new');
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
