// ============================================================================
// /customers/recalls — the last in-page tab becomes a real address
// ============================================================================
// CustomersPage used to early-return <RecallManager /> when the URL said
// `?tab=recalls`: a whole screen with no address, no bookmark, no menu row and
// no way to hand it to a colleague. It is a route now, gated with the SAME six
// roles the in-page tab inherited from /customers.
//
// What each failure means:
//  - "resolves for an allowed role" fails if the route is dropped, or if it
//    stops mounting RecallManager.
//  - "bounces a disallowed role" fails if the gate is widened (WORKSHOP_STAFF
//    stands in for every role /customers never let in).
//  - "?tab=recalls forwards" fails if CustomersIndex or the shim table is
//    removed — staff bookmarks and the old Send Recall link would land on the
//    customer list instead, silently.
//  - "bare /customers still renders the list" fails if the wrapper turns the
//    module's home into a redirect (it is a real screen, not a tab container).

import { Suspense } from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// --- Mocks (before importing the routes) ------------------------------------

const AUTH = { roles: ['SALES_STAFF'] as string[] };
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Guard User', roles: AUTH.roles, activeStoreId: 'BV-BOK-01' },
    isAuthenticated: true,
    isLoading: false,
    hasRole: (roles?: string[]) => !roles || roles.some((r) => AUTH.roles.includes(r)),
    hasPermission: () => true,
    hasModuleAccess: () => true,
  }),
}));

// RecallManager's only fetch: the Rx-expiry buckets.
vi.mock('../../services/api/marketing', () => ({
  marketingApi: {
    getRxExpiryAlerts: () => Promise.resolve({ urgent: [], soon: [], upcoming: [] }),
    sendRxReminder: () => Promise.resolve({}),
  },
}));

// The customer list itself is not what this file is about — and mounting it
// would drag in the whole CRM detail screen. Its own suites cover it.
vi.mock('../../pages/customers/CustomersPage', () => ({
  CustomersPage: () => <div data-testid="customer-list" />,
}));

import { MemoryRouter, Routes, useLocation } from 'react-router-dom';
import { customerRoutes } from '../customerRoutes';
import { ToastProvider } from '../../context/ToastContext';

// Lazy route chunks compile on demand under vitest.
const FIND = { timeout: 20000 };

/** Where the router actually ended up — pathname + search, as one string. */
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
          <Routes>{customerRoutes}</Routes>
        </Suspense>
      </ToastProvider>
    </MemoryRouter>,
  );
}

/** Copy that exists ONLY in RecallManager. */
const RECALLS_HEADING = /Customer Recalls & Reminders/;

beforeEach(() => {
  AUTH.roles = ['SALES_STAFF'];
});

describe('/customers/recalls', () => {
  it('resolves to RecallManager for an allowed role', async () => {
    renderAt('/customers/recalls');
    expect(await screen.findByText(RECALLS_HEADING, undefined, FIND)).toBeInTheDocument();
    expect(screen.getByTestId('location').textContent).toBe('/customers/recalls');
  }, 30000);

  it.each([
    ['SUPERADMIN'], ['ADMIN'], ['STORE_MANAGER'], ['OPTOMETRIST'], ['CASHIER'], ['SALES_STAFF'],
  ])('%s keeps the access the in-page tab gave it', async (role) => {
    AUTH.roles = [role];
    renderAt('/customers/recalls');
    expect(await screen.findByText(RECALLS_HEADING, undefined, FIND)).toBeInTheDocument();
  }, 30000);

  it('bounces a role the /customers gate never allowed', async () => {
    AUTH.roles = ['WORKSHOP_STAFF'];
    renderAt('/customers/recalls');
    expect(await screen.findByText('/unauthorized', undefined, FIND)).toBeInTheDocument();
    expect(screen.queryByText(RECALLS_HEADING)).not.toBeInTheDocument();
  }, 30000);
});

describe('legacy ?tab= links still land', () => {
  it('/customers?tab=recalls forwards to /customers/recalls and paints it', async () => {
    renderAt('/customers?tab=recalls');
    expect(await screen.findByText(RECALLS_HEADING, undefined, FIND)).toBeInTheDocument();
    expect(screen.getByTestId('location').textContent).toBe('/customers/recalls');
  }, 30000);

  it('/customers?tab=recalls&search=true keeps the other query params', async () => {
    renderAt('/customers?tab=recalls&search=true');
    expect(await screen.findByText(RECALLS_HEADING, undefined, FIND)).toBeInTheDocument();
    expect(screen.getByTestId('location').textContent).toBe('/customers/recalls?search=true');
  }, 30000);

  it('bare /customers still renders the customer list, not a redirect', async () => {
    renderAt('/customers');
    expect(await screen.findByTestId('customer-list', undefined, FIND)).toBeInTheDocument();
    expect(screen.getByTestId('location').textContent).toBe('/customers');
  }, 30000);
});
