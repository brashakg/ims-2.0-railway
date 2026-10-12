// ============================================================================
// IMS 2.0 - A login with no shop sees no shop (owner ruling 2026-10-12, R4)
// ============================================================================
// The server (api.dependencies.resolve_store_scope) now answers a non-admin
// login with no shop with EMPTY lists, and naming a shop is a 403 that says
// "No shop is assigned to this login". Purchase and Finance must say the same
// thing instead of an empty table with no reason -- and only to that login:
// an admin reaches every shop, and a multi-store login with assigned shops but
// no active one still reads its own first shop on the server.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

vi.stubGlobal('requestIdleCallback', () => 0);

let roles: string[] = ['ACCOUNTANT'];
let activeStoreId: string | undefined;
let storeIds: string[] = [];
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', name: 'Staff', roles, activeStoreId, storeIds },
    hasRole: (want: string[]) => want.some((r) => roles.includes(r)),
    hasPermission: () => true,
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));
vi.mock('../../../services/api/finance', () => ({
  financeApi: {
    getRevenue: vi.fn(),
    getPnl: vi.fn(),
    getGstSummary: vi.fn(),
    getOutstanding: vi.fn(),
    getCashFlow: vi.fn(),
    getBudget: vi.fn(),
    getVendorPayments: vi.fn(),
    getPeriodStatus: vi.fn(),
    getPnlByStore: vi.fn(),
    getPnlByCategory: vi.fn(),
    getGstReconciliation: vi.fn(),
    exportTally: vi.fn(),
  },
}));
const get = vi.hoisted(() => vi.fn());
vi.mock('../../../services/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api/client')>();
  const fake = { get, post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() };
  return { ...actual, default: fake, api: fake };
});

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { NO_SHOP_MESSAGE, NoShopNotice, hasNoShop } from '../NoShopNotice';
import { PurchaseLayout } from '../../../pages/purchase/PurchaseLayout';
import FinanceDashboard from '../../../pages/finance/FinanceDashboard';
import { financeApi } from '../../../services/api/finance';

const api = financeApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

function primeFinance() {
  api.getRevenue.mockResolvedValue({ total_revenue: 0 });
  api.getPnl.mockResolvedValue({ revenue: 0, expenses: {} });
  api.getGstSummary.mockResolvedValue({});
  api.getOutstanding.mockResolvedValue([]);
  api.getCashFlow.mockResolvedValue({});
  api.getBudget.mockResolvedValue({ categories: {} });
  api.getVendorPayments.mockResolvedValue([]);
  api.getPeriodStatus.mockResolvedValue({ locked: false });
  api.getPnlByStore.mockResolvedValue({ stores: [] });
  api.getPnlByCategory.mockResolvedValue({ categories: [] });
  api.getGstReconciliation.mockResolvedValue({ entities: [] });
}

function openPurchase(path = '/purchase/orders') {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/purchase" element={<PurchaseLayout />}>
            <Route path="orders" element={<div>orders section</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const as = (r: string[], active?: string, ids: string[] = []) => {
  roles = r;
  activeStoreId = active;
  storeIds = ids;
};

beforeEach(() => {
  vi.clearAllMocks();
  get.mockResolvedValue({ data: { stores: [] } });
  primeFinance();
  as(['ACCOUNTANT']);
});

describe('hasNoShop -- the server rule, on the screen', () => {
  it.each([['ACCOUNTANT'], ['STORE_MANAGER'], ['AREA_MANAGER'], ['SALES_STAFF']])(
    'a %s with no active shop and no assigned shop holds no shop',
    (role) => {
      expect(hasNoShop({ roles: [role] as never })).toBe(true);
      expect(hasNoShop({ roles: [role] as never, activeStoreId: '', storeIds: [''] })).toBe(true);
    },
  );

  it('an admin reaches every shop, with or without one picked', () => {
    expect(hasNoShop({ roles: ['ADMIN'], storeIds: [] })).toBe(false);
    expect(hasNoShop({ roles: ['SUPERADMIN'] })).toBe(false);
  });

  it('a login holding a shop -- active, or only assigned -- is not shopless', () => {
    expect(hasNoShop({ roles: ['ACCOUNTANT'], activeStoreId: 'WO-PUN-01' })).toBe(false);
    // the server reads a multi-store login's first own shop for it
    expect(hasNoShop({ roles: ['AREA_MANAGER'], storeIds: ['WO-PUN-01', 'BV-DHN-01'] })).toBe(false);
    expect(hasNoShop(null)).toBe(false);
  });

  it("carries the server's words", () => {
    expect(NO_SHOP_MESSAGE).toBe('No shop is assigned to this login. Ask an admin to assign one.');
    render(<NoShopNotice />);
    expect(screen.getByRole('status')).toHaveTextContent(NO_SHOP_MESSAGE);
  });
});

describe('Purchase -- a login with no shop is told why, not shown an empty section', () => {
  it('no shop: the notice instead of the section, and no create button', async () => {
    openPurchase();
    expect(await screen.findByTestId('no-shop-notice')).toHaveTextContent(NO_SHOP_MESSAGE);
    expect(screen.queryByText('orders section')).toBeNull();
    expect(screen.queryByRole('button', { name: /new po/i })).toBeNull();
  });

  it('a shop: the section, no notice', async () => {
    as(['ACCOUNTANT'], 'WO-PUN-01', ['WO-PUN-01']);
    openPurchase();
    expect(await screen.findByText('orders section')).toBeInTheDocument();
    expect(screen.queryByTestId('no-shop-notice')).toBeNull();
  });

  it('an admin with no shop picked: every shop, no notice', async () => {
    as(['ADMIN']);
    openPurchase();
    expect(await screen.findByText('orders section')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /new po/i })).toBeInTheDocument();
    expect(screen.queryByTestId('no-shop-notice')).toBeNull();
  });

  it('assigned shops but none active: the section (the server reads the first)', async () => {
    as(['AREA_MANAGER'], undefined, ['WO-PUN-01', 'BV-DHN-01']);
    openPurchase();
    expect(await screen.findByText('orders section')).toBeInTheDocument();
    expect(screen.queryByTestId('no-shop-notice')).toBeNull();
  });
});

describe('Finance -- a login with no shop is told why, not shown empty panels', () => {
  it('no shop: the notice instead of the dashboard', async () => {
    render(<FinanceDashboard />);
    expect(await screen.findByTestId('no-shop-notice')).toHaveTextContent(NO_SHOP_MESSAGE);
    expect(screen.queryByRole('button', { name: /export to tally/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /outstanding/i })).toBeNull();
  });

  it.each([
    ['an accountant with a shop', ['ACCOUNTANT'], 'WO-PUN-01'],
    ['an admin with no shop picked', ['ADMIN'], undefined],
  ])('%s: the dashboard, no notice', async (_label, r, active) => {
    as(r, active);
    render(<FinanceDashboard />);
    await waitFor(() => expect(api.getPnl).toHaveBeenCalled());
    expect(await screen.findByRole('button', { name: /export to tally/i })).toBeInTheDocument();
    expect(screen.queryByTestId('no-shop-notice')).toBeNull();
  });
});
