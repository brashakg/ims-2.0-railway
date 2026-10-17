// ============================================================================
// Audit F63: every Purchase tab's LIST reads the one shop scope (screen half)
// ============================================================================
// Owner ruling 2026-09-28: admins open Purchase on all stores with a shop
// filter and every tab obeys it; everyone else keeps their own shop. The server
// half is the matrix in backend/tests/test_purchases_this_month.py; orders and
// the report have their own screen pins (purchaseAllStoresForAdmin,
// PurchasesThisMonthSection). This pins the other five tabs: invoices,
// reconciliation, variance, goods receipt and vendor returns.
//
// An admin sitting on Dhanbad picks Pune: every list read must ask for Pune --
// not his active shop, not a fixed one -- and with no pick, for all stores.
// An accountant (no picker) reads his own shop. The HTTP client is mocked, so
// any route to the scope passes; a list read that ignores it does not.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useEffect, type ReactNode } from 'react';
import { render, waitFor, cleanup } from '@testing-library/react';

vi.stubGlobal('requestIdleCallback', () => 0);

let roles: string[] = ['ADMIN'];
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Owner', roles, activeStoreId: 'BV-DHN-01', storeIds: ['BV-DHN-01'] },
    hasRole: (want: string[]) => roles.some((r) => r === 'ADMIN' || r === 'SUPERADMIN' || want.includes(r)),
    hasPermission: () => true,
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../../components/print/GRNPrint', () => ({ GRNPrint: () => null }));
vi.mock('../../../components/print/storeIdentity', () => ({
  resolveStoreIdentity: vi.fn().mockResolvedValue(null),
}));

const get = vi.hoisted(() => vi.fn());
vi.mock('../../../services/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api/client')>();
  const fake = { get, post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() };
  return { ...actual, default: fake, api: fake };
});

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { usePurchaseShop } from '../purchaseShop';
import { PurchaseInvoicesTab } from '../invoices/PurchaseInvoicesTab';
import ReconConsole from '../ReconConsole';
import { PurchaseVarianceTab } from '../PurchaseVarianceTab';
import { GoodsReceiptNote } from '../GoodsReceiptNote';
import { VendorReturns } from '../VendorReturns';

beforeEach(() => {
  cleanup();
  get.mockReset();
  get.mockImplementation((url: string) =>
    Promise.resolve({
      data: url.startsWith('/vendors/variance-report')
        ? { lines: [], total: 0 }
        : url.startsWith('/vendor-returns')
          ? { returns: [] }
          : url === '/vendors/recon/worklists'
            ? {
                stock_yet_to_receive: [],
                vendor_returns: [],
                pending_credit_notes_scheme: [],
                pending_credit_notes_return: [],
              }
            : {},
    }),
  );
});

/** Sets the admin's shop pick (the shared Purchase filter) before the tab mounts. */
function Pick({ shop, children }: { shop: string; children: ReactNode }) {
  const { setShop, shop: current } = usePurchaseShop();
  useEffect(() => setShop(shop), [shop, setShop]);
  return current === shop ? <>{children}</> : null;
}

const TABS: Record<string, { list: string; node: () => ReactNode }> = {
  invoices: { list: '/vendors/purchase-invoices', node: () => <PurchaseInvoicesTab suppliers={[]} /> },
  reconciliation: { list: '/vendors/recon/worklists', node: () => <ReconConsole /> },
  variance: { list: '/vendors/variance-report', node: () => <PurchaseVarianceTab /> },
  'goods receipt': { list: '/vendors/grn', node: () => <GoodsReceiptNote /> },
  'vendor returns': { list: '/vendor-returns/', node: () => <VendorReturns /> },
};

function open(tab: string, shop: string) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <Pick shop={shop}>{TABS[tab].node()}</Pick>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The store_id of every list read the tab made (undefined = none sent). */
async function listScopes(tab: string): Promise<(string | undefined)[]> {
  const reads = () => get.mock.calls.filter(([url]) => url === TABS[tab].list);
  await waitFor(() => expect(reads().length).toBeGreaterThan(0));
  return reads().map(([, cfg]) => (cfg as { params?: { store_id?: string } } | undefined)?.params?.store_id);
}

describe.each(Object.keys(TABS))('F63: the %s tab reads the one Purchase shop', (tab) => {
  it('an admin who picks Pune reads Pune -- not his own Dhanbad', async () => {
    roles = ['ADMIN'];
    open(tab, 'BV-PUN-01');
    expect(new Set(await listScopes(tab))).toEqual(new Set(['BV-PUN-01']));
  });

  it('an admin with no pick reads all stores (no store_id)', async () => {
    roles = ['ADMIN'];
    open(tab, '');
    expect(new Set(await listScopes(tab))).toEqual(new Set([undefined]));
  });

  it('an accountant (no picker) reads his own shop', async () => {
    roles = ['ACCOUNTANT'];
    open(tab, '');
    expect(new Set(await listScopes(tab))).toEqual(new Set(['BV-DHN-01']));
  });
});
