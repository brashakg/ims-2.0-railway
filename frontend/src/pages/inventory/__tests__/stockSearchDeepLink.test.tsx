// ============================================================================
// /inventory/stock?search= must actually pre-scope the ledger
// ============================================================================
// QuickAddPage's duplicate-SKU rescue popup has always linked
// /inventory?tab=catalog&search=<sku> ("Open the existing product"), and the
// old mega-page IGNORED the search param - the manager landed on the full
// unfiltered ledger and had to re-type the SKU. The split fixes it: the
// legacy redirect carries ?search= through and the Stock ledger page seeds
// its search box from the URL. This proves the FILTERING, not just the box:
// the non-matching row must be gone.

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { StockItem } from '../inventoryQueries';

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
    useOnlineStatus: () => idle,
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
          <Route
            element={<Outlet context={{ storeId: 'BV-RAN-01', isOnlineStoreView: false, stores: [] }} />}
          >
            <Route path="/inventory/stock" element={<InventoryStockPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('the QuickAdd ?search= deep link', () => {
  it('seeds the search box and filters the table to the linked SKU', () => {
    renderAt('/inventory/stock?search=FR-RAYB-2140');
    expect(screen.getByPlaceholderText(/Search by name, SKU/)).toHaveValue('FR-RAYB-2140');
    expect(screen.getByText('Wayfarer')).toBeInTheDocument();
    // The other product must be filtered OUT - a page that merely displays
    // the param but ignores it fails here.
    expect(screen.queryByText('Aviator Classic')).not.toBeInTheDocument();
  });

  it('shows the full ledger when there is no search param', () => {
    renderAt('/inventory/stock');
    expect(screen.getByText('Wayfarer')).toBeInTheDocument();
    expect(screen.getByText('Aviator Classic')).toBeInTheDocument();
  });
});
