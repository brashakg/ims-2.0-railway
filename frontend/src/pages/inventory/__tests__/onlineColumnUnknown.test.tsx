// ============================================================================
// IMS 2.0 - the Inventory Online column says "Unverified" when IMS could not
// read which listings are live, never "In-store only"
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

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', activeStoreId: 'BV-RAN-01', storeIds: ['BV-RAN-01'], roles: ['STORE_MANAGER'] },
    hasRole: (roles: string[]) => roles.includes('STORE_MANAGER'),
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

let onlineStatus: Record<string, { online: boolean | null; online_stock: null }> = {};

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

describe('the Inventory Online column', () => {
  it('says Unverified for an unknown live read', () => {
    onlineStatus = {
      'FR-RAYB-3025-GLD': { online: null, online_stock: null },
      'FR-RAYB-2140-BLK': { online: null, online_stock: null },
    };
    renderAt('/inventory/stock');
    expect(screen.getAllByText('Unverified')).toHaveLength(2);
    expect(screen.queryByText('In-store only')).toBeNull();
  });

  it('keeps an unknown row out of both the Online and the Offline filter', () => {
    onlineStatus = {
      'FR-RAYB-3025-GLD': { online: null, online_stock: null },
      'FR-RAYB-2140-BLK': { online: false, online_stock: null },
    };
    renderAt('/inventory/stock');
    fireEvent.click(screen.getByRole('button', { name: /^Offline$/ }));
    expect(screen.getByText('Wayfarer')).toBeInTheDocument();
    expect(screen.queryByText('Aviator Classic')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Online/ }));
    expect(screen.queryByText('Aviator Classic')).toBeNull();
  });

  it('says Unverified in the detail panel and the CSV export', async () => {
    onlineStatus = {
      'FR-RAYB-3025-GLD': { online: null, online_stock: null },
      'FR-RAYB-2140-BLK': { online: null, online_stock: null },
    };
    const blobs: Blob[] = [];
    const create = vi.fn((b: Blob) => { blobs.push(b); return 'blob:x'; });
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() });
    renderAt('/inventory/stock');
    fireEvent.click(screen.getAllByRole('button', { name: 'View Details' })[0]);
    expect(screen.getByText(/Unverified \(could not read the website\)/)).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Close' })[0]);
    fireEvent.click(screen.getByRole('button', { name: /Export/ }));
    expect(create).toHaveBeenCalled();
    const text = await blobs[0].text();
    expect(text.split('\n').slice(1).every((line) => line.includes(',Unverified,'))).toBe(true);
  });

  it('says Online / In-store only when the read worked', () => {
    onlineStatus = {
      'FR-RAYB-3025-GLD': { online: true, online_stock: null },
      'FR-RAYB-2140-BLK': { online: false, online_stock: null },
    };
    renderAt('/inventory/stock');
    expect(screen.getAllByText('In-store only')).toHaveLength(1);
    expect(screen.queryByText('Unverified')).toBeNull();
  });
});
