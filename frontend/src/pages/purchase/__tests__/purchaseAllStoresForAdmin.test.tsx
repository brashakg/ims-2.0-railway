// ============================================================================
// Audit F63: an admin opens Purchase on ALL STORES, not his active store
// ============================================================================
// Owner audit 2026-09-29: as admin, Purchase Orders and GRN say 0 while
// Purchase Invoices on the same page lists another shop's bills; a first-time
// admin is defaulted to the ONLINE store. Ruling 2026-09-28: admins open
// Purchase on all stores with a shop filter; every tab obeys it; managers keep
// their own shop.
//
// The server half is pinned in backend/tests/test_purchases_this_month.py
// (one scope rule: an omitted store_id means all stores for an admin and the
// caller's own shop for everyone else). This is the screen half: today every
// section sends user.activeStoreId (PurchaseOrdersSection.tsx:33), so an admin
// sitting on the online store asks for the online store's orders -- none.
//
// The HTTP client is mocked, not vendorsApi, so the fix may route the scope
// however it likes. `it.fails` = xfail(strict=True): flip to `it` when fixed.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';

vi.stubGlobal('requestIdleCallback', () => 0);

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    // First login: the admin's active store is the online store.
    user: { id: 'u1', name: 'Owner', roles: ['ADMIN'], activeStoreId: 'BV-ONLINE-01', storeIds: [] },
    hasRole: (want: string[]) => want.includes('ADMIN'),
    hasPermission: () => true,
  }),
}));
vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => true }));
vi.mock('../../../hooks/useStorePrintInfo', () => ({
  useStorePrintInfo: () => ({ storeName: '', address: '', city: '', state: '', pincode: '', stateCode: '' }),
}));
vi.mock('../../../components/print/storeIdentity', () => ({
  resolveStoreIdentity: vi.fn().mockResolvedValue(null),
}));
vi.mock('../../../services/api/entities', () => ({
  entitiesApi: { meta: vi.fn().mockResolvedValue({ state_codes: [], entity_types: [] }) },
}));

const get = vi.hoisted(() => vi.fn());
vi.mock('../../../services/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api/client')>();
  const fake = { get, post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() };
  return { ...actual, default: fake, api: fake };
});

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { PurchaseOrdersSection } from '../PurchaseOrdersSection';

beforeEach(() => {
  get.mockImplementation((url: string) =>
    Promise.resolve({
      data: url.startsWith('/vendors/purchase-orders')
        ? { purchase_orders: [], total: 0 }
        : url === '/vendors' ? { vendors: [], total: 0 } : {},
    }),
  );
});

function openOrders() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={['/purchase/orders']}>
        <PurchaseOrdersSection />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The params of the first purchase-order list read the screen made. */
async function orderListParams(): Promise<Record<string, unknown>> {
  await waitFor(() =>
    expect(get.mock.calls.some(([url]) => url === '/vendors/purchase-orders')).toBe(true),
  );
  const call = get.mock.calls.find(([url]) => url === '/vendors/purchase-orders');
  return ((call?.[1] as { params?: Record<string, unknown> })?.params) ?? {};
}

describe('F63: Purchase opens on all stores for an admin', () => {
  it('reads the purchase-order list on open (harness check)', async () => {
    openOrders();
    await orderListParams();
  });

  it.fails('F63: the admin\'s order list is not pinned to his active (online) store', async () => {
    openOrders();
    const params = await orderListParams();
    // No shop chosen = no store_id; the server's one scope rule reads that as
    // all stores for an admin (never the literal 'BV-ONLINE-01').
    expect(params.store_id ?? '').toBe('');
  });
});
