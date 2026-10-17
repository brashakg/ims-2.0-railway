// ============================================================================
// /finance/cash-flow -- a login with no shop is told so (R4, 2026-10-12)
// ============================================================================
// "A non-admin login with NO shop assigned sees NO shop until an admin assigns
// one: empty lists and a clear 'No shop is assigned to this login' message."
//
// The Cash Flow & Payables screen is open to ACCOUNTANT. For an accountant
// with no shop it used to render the page anyway: every figure zero, a header
// reading 'Shop: __no_shop_assigned__' (the server's reserved no-shop id,
// echoed as the owner dashboard's store_id), and the + payment / + debit note
// buttons. The route now puts NoShopGate in front of the page.
//
// What each failure means:
//  - "no shop: the notice" fails if the gate is dropped from the route (the
//    page mounts again for a login the server gives no shop).
//  - "a shop / an admin: the page" fails if the gate swallows a login that
//    does hold a shop, or an admin (who reaches every shop).
//  - "the reserved id never shows as a shop" fails if PurchaseShopName prints
//    the server's no-shop id instead of saying no shop is assigned.

import { Suspense } from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

const AUTH: { roles: string[]; activeStoreId?: string; storeIds: string[] } = {
  roles: ['ACCOUNTANT'],
  activeStoreId: undefined,
  storeIds: [],
};
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({
    user: {
      id: 'u1',
      name: 'Accounts',
      roles: AUTH.roles,
      activeStoreId: AUTH.activeStoreId,
      storeIds: AUTH.storeIds,
    },
    isAuthenticated: true,
    isLoading: false,
    hasRole: (roles?: string[]) => !roles || roles.some((r) => AUTH.roles.includes(r)),
    hasPermission: () => true,
    hasModuleAccess: () => true,
  }),
}));

// The page itself is not what this file is about; its own suites cover it.
vi.mock('../../pages/finance/CashFlowPage', () => ({
  default: () => <div data-testid="cash-flow-page">cash flow page</div>,
}));

const get = vi.hoisted(() => vi.fn());
vi.mock('../../services/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api/client')>();
  const fake = { get, post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() };
  return { ...actual, default: fake, api: fake };
});

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes } from 'react-router-dom';
import { financeRoutes } from '../financeRoutes';
import { NO_SHOP_MESSAGE, NO_SHOP_SCOPE } from '../../components/common/NoShopNotice';
import { PurchaseShopName } from '../../pages/purchase/purchaseShop';

// Lazy route chunks compile on demand under vitest.
const FIND = { timeout: 20000 };

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Suspense fallback={<div data-testid="lazy-loading" />}>
        <Routes>{financeRoutes}</Routes>
      </Suspense>
    </MemoryRouter>,
  );
}

const as = (roles: string[], activeStoreId?: string, storeIds: string[] = []) => {
  AUTH.roles = roles;
  AUTH.activeStoreId = activeStoreId;
  AUTH.storeIds = storeIds;
};

beforeEach(() => {
  vi.clearAllMocks();
  get.mockResolvedValue({ data: { stores: [{ store_id: 'WO-PUN-01', store_name: 'Pune' }] } });
  as(['ACCOUNTANT']);
});

describe('/finance/cash-flow -- R4', () => {
  it('no shop: the notice in place of the page (no figures, no money buttons)', async () => {
    renderAt('/finance/cash-flow');
    expect(await screen.findByTestId('no-shop-notice', {}, FIND)).toHaveTextContent(NO_SHOP_MESSAGE);
    expect(screen.getByRole('heading', { name: /cash flow & payables/i })).toBeInTheDocument();
    expect(screen.queryByTestId('cash-flow-page')).toBeNull();
  });

  it.each([
    ['an accountant with a shop', ['ACCOUNTANT'], 'WO-PUN-01', ['WO-PUN-01']],
    ['an accountant with assigned shops but none active', ['ACCOUNTANT'], undefined, ['WO-PUN-01']],
    ['an admin with no shop picked', ['ADMIN'], undefined, []],
    ['a superadmin', ['SUPERADMIN'], undefined, []],
  ])('%s: the page, no notice', async (_label, roles, active, ids) => {
    as(roles as string[], active as string | undefined, ids as string[]);
    renderAt('/finance/cash-flow');
    expect(await screen.findByTestId('cash-flow-page', {}, FIND)).toBeInTheDocument();
    expect(screen.queryByTestId('no-shop-notice')).toBeNull();
  });
});

describe("the server's reserved no-shop id is never shown as a shop", () => {
  function name(storeId: string) {
    return render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <span data-testid="shop">
          <PurchaseShopName storeId={storeId} />
        </span>
      </QueryClientProvider>,
    );
  }

  it('reads "No shop assigned", not the id', () => {
    name(NO_SHOP_SCOPE);
    expect(screen.getByTestId('shop')).toHaveTextContent('No shop assigned');
    expect(screen.getByTestId('shop').textContent).not.toContain(NO_SHOP_SCOPE);
  });

  it('a real shop still reads its name', async () => {
    name('WO-PUN-01');
    expect(await screen.findByText('Pune')).toBeInTheDocument();
  });
});
