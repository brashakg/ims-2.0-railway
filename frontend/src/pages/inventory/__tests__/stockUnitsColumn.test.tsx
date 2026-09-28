// ============================================================================
// F27 - the ledger row stops presenting one unit's barcode as the product's
// ============================================================================
// Every unit has its own barcode. The row showed ONE of them (and it changed
// when that unit shipped). Now the row shows how many units there are and
// links to them; the drawer does the same.

import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { StockItem } from '../inventoryQueries';

const getUnits = vi.hoisted(() => vi.fn());

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', activeStoreId: 'BV-DHN-02', storeIds: ['BV-DHN-02'], roles: ['STORE_MANAGER'] },
    hasRole: (roles: string[]) => roles.includes('STORE_MANAGER'),
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../../services/api/inventory', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api/inventory')>();
  return { ...actual, inventoryApi: { ...actual.inventoryApi, getUnits } };
});

// A row as an OLD server sent it: one unit's code in `barcode`.
const ITEMS: StockItem[] = [
  {
    id: 'P-CARRERA', sku: 'FR-CAR-8895', name: 'Carrera CA 8895', brand: 'Carrera',
    category: 'FR', mrp: 8990, offerPrice: 8990, stock: 9, reserved: 0, barcode: 'BV--00F1D2CC',
  } as StockItem,
];

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

function renderPage(storeId = 'BV-DHN-02') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/inventory/stock']}>
        <Routes>
          <Route element={<Outlet context={{ storeId, isOnlineStoreView: false, stores: [] }} />}>
            <Route path="/inventory/stock" element={<InventoryStockPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('the stock ledger row (F27)', () => {
  it('shows the unit count, never one unit barcode, and opens the units', async () => {
    getUnits.mockResolvedValue({ units: [], total: 0 });
    renderPage();
    expect(screen.queryByText('BV--00F1D2CC')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /9 units/ }));
    expect(getUnits).toHaveBeenCalledWith({ store_id: 'BV-DHN-02', product_id: 'P-CARRERA' });
    expect(await screen.findByRole('dialog', { name: 'Carrera CA 8895' })).toBeInTheDocument();
  });

  it('lists the units of the shop the ledger is showing, not the active shop', async () => {
    // An admin browsing another shop's ledger: the row counts THAT shop's
    // units, so its units view must read that shop too.
    getUnits.mockResolvedValue({ units: [], total: 0 });
    renderPage('BV-BOK-01');
    fireEvent.click(screen.getByRole('button', { name: /9 units/ }));
    expect(getUnits).toHaveBeenCalledWith({ store_id: 'BV-BOK-01', product_id: 'P-CARRERA' });
  });

  it('the drawer shows units on hand and links to them', async () => {
    getUnits.mockResolvedValue({ units: [], total: 0 });
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'View Details' }));
    expect(screen.queryByText('BV--00F1D2CC')).not.toBeInTheDocument();
    expect(screen.getByText('Units on hand')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /units & labels/i }));
    expect(await screen.findByRole('dialog', { name: 'Carrera CA 8895' })).toBeInTheDocument();
  });
});
