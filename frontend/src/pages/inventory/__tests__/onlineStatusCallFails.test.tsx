// ============================================================================
// IMS 2.0 - a FAILED /catalog/online-status call reads Unverified, never
// "In-store only"
// ============================================================================
// Multi-location PR 4, round 18 review item 5: useOnlineStatus swallowed an
// HTTP failure into {}, and a missing key rendered "In-store only" (and "No"
// in the CSV, "none synced online" on the card) -- the confident false
// negative the backend's online: null already avoids. The catch now answers
// online: null for every id. Put `return {}` back -> fails.

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { StockItem } from '../inventoryQueries';

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api')>();
  return { ...actual, catalogApi: { ...actual.catalogApi, getOnlineStatus: vi.fn().mockRejectedValue(new Error('network')) } };
});
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', activeStoreId: 'BV-RAN-01', storeIds: ['BV-RAN-01'], roles: ['STORE_MANAGER'] },
    hasRole: (roles: string[]) => roles.includes('STORE_MANAGER'),
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));


const ITEMS: StockItem[] = [
  {
    id: 'P1', sku: 'FR-RAYB-3025-GLD', name: 'Aviator Classic', brand: 'Ray-Ban',
    category: 'FR', mrp: 12990, offerPrice: 12990, stock: 4, reserved: 0,
  },
  {
    id: 'P2', sku: 'FR-RAYB-2140-BLK', name: 'Wayfarer', brand: 'Ray-Ban',
    category: 'FR', mrp: 9990, offerPrice: 9990, stock: 6, reserved: 0,
  },
];

// Stub only the data hooks; the URL-seeding + filtering under test stay real.
vi.mock('../inventoryQueries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../inventoryQueries')>();
  const idle = { data: undefined, isFetching: false, isError: false, isPending: false };
  return {
    ...actual,
    useStock: () => ({ data: ITEMS, isPending: false, isError: false }),
    useCataloguers: () => idle,
    usePlacements: () => idle,
    useFixturesMap: () => idle,
  };
});

import { InventoryStockPage } from '../InventoryStockPage';

function renderAt(url: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route element={<Outlet context={{ storeId: 'BV-RAN-01', isOnlineStoreView: false, stores: [] }} />}>
            <Route path="/inventory/stock" element={<InventoryStockPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('the Inventory Online column when the online-status call fails', () => {
  it('says Unverified on every row, never In-store only', async () => {
    renderAt('/inventory/stock');
    expect(await screen.findAllByText('Unverified')).toHaveLength(2);
    expect(screen.queryByText('In-store only')).toBeNull();
  });
});
