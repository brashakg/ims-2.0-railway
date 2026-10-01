// ============================================================================
// IMS 2.0 - a live SKU that shares its Shopify item with another product shows
// the SAME shared label as the Online Stock page on the Inventory column, the
// detail line and the CSV (never "Unverified (could not read the website)'),
// and counts as Online in the filter
// ============================================================================
// Multi-location PR 4, round 17 review round 2: /catalog/online-status now
// answers `online` from THE one live-listing reader, and online: null when
// that read failed. Treat null as "not online" again -> "In-store only" on
// every row -> fails.

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { StockItem } from '../inventoryQueries';
import { SHARES_ITEM_LABEL } from '../sharedItem';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', activeStoreId: 'BV-RAN-01', storeIds: ['BV-RAN-01'], roles: ['STORE_MANAGER'] },
    hasRole: (roles: string[]) => roles.includes('STORE_MANAGER'),
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

let onlineStatus: Record<string, { online: boolean | null; online_stock: null; shares_item?: boolean }> = {};

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
    useOnlineStatus: () => ({ ...idle, data: onlineStatus }),
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

const SHARED = { online: null, online_stock: null, shares_item: true };

describe('a shared-item SKU on the Inventory page', () => {
  it('shows the shared label on the column, never Unverified', () => {
    onlineStatus = { 'FR-RAYB-3025-GLD': SHARED, 'FR-RAYB-2140-BLK': { online: false, online_stock: null } };
    renderAt('/inventory/stock');
    expect(screen.getAllByText(SHARES_ITEM_LABEL)).toHaveLength(1);
    expect(screen.queryByText('Unverified')).toBeNull();
  });

  it('is Online in the filter (a live listing), not Offline', () => {
    onlineStatus = { 'FR-RAYB-3025-GLD': SHARED, 'FR-RAYB-2140-BLK': { online: false, online_stock: null } };
    renderAt('/inventory/stock');
    fireEvent.click(screen.getByRole('button', { name: /^Offline$/ }));
    expect(screen.queryByText('Aviator Classic')).toBeNull();
    expect(screen.getByText('Wayfarer')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Online/ }));
    expect(screen.getByText('Aviator Classic')).toBeInTheDocument();
    expect(screen.queryByText('Wayfarer')).toBeNull();
  });

  it('shows the shared label in the detail panel and the CSV', async () => {
    onlineStatus = { 'FR-RAYB-3025-GLD': SHARED, 'FR-RAYB-2140-BLK': SHARED };
    const blobs: Blob[] = [];
    const create = vi.fn((b: Blob) => { blobs.push(b); return 'blob:x'; });
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() });
    renderAt('/inventory/stock');
    fireEvent.click(screen.getAllByRole('button', { name: 'View Details' })[0]);
    expect(screen.queryByText(/could not read the website/)).toBeNull();
    expect(screen.getAllByText(SHARES_ITEM_LABEL).length).toBeGreaterThan(2);
    fireEvent.click(screen.getAllByRole('button', { name: 'Close' })[0]);
    fireEvent.click(screen.getByRole('button', { name: /Export/ }));
    const text = await blobs[0].text();
    expect(text.split('\n').slice(1).every((line) => line.includes(`,${SHARES_ITEM_LABEL},`))).toBe(true);
  });
});
