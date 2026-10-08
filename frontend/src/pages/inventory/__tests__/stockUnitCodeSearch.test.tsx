// ============================================================================
// Inventory > Stock: a unit's own IMS code finds its product, and Manage
// Barcode opens with the MANUFACTURER's code
// ============================================================================
// A shop scans or types the code on a unit's label ('BV0000000042', or an old
// label 'BV--91FA3858') to find it. The search box matched only name / SKU /
// brand, so a unit that was on the shelf read "No products found". Each ledger
// row now carries the codes of the units on hand (unit_barcodes).
//
// The row's `barcode` is one unit's IMS code; its `gtin` is the maker's UPC /
// EAN. Manage Barcode edits the gtin, so it must open pre-filled with the gtin
// -- opening with the unit code is the bug that made the server refuse a save
// nobody had typed.

import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { StockItem } from '../inventoryQueries';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', activeStoreId: 'BV-DHN-02', storeIds: ['BV-DHN-02'], roles: ['STORE_MANAGER'] },
    hasRole: (roles: string[]) => roles.includes('STORE_MANAGER'),
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

const ITEMS: StockItem[] = [
  {
    id: 'P1', sku: 'FR-CARRERA-8895-807', name: 'Carrera CA 8895', brand: 'Carrera',
    category: 'FR', mrp: 9990, offerPrice: 9990, stock: 2, reserved: 0,
    barcode: 'BV0000000042', gtin: '4006381333931',
    unit_barcodes: ['BV0000000042', 'BV0000000043'],
  },
  {
    id: 'P2', sku: 'FR-RAYB-2140-BLK', name: 'Wayfarer', brand: 'Ray-Ban',
    category: 'FR', mrp: 9990, offerPrice: 9990, stock: 1, reserved: 0,
    barcode: 'BV--91FA3858', unit_barcodes: ['BV--91FA3858'],
    // main's old Generate wrote a random EAN-13 to the product barcode
    unverified_barcode: '5260181590836',
  },
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

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/inventory/stock']}>
        <Routes>
          <Route
            element={<Outlet context={{ storeId: 'BV-DHN-02', isOnlineStoreView: false, stores: [] }} />}
          >
            <Route path="/inventory/stock" element={<InventoryStockPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const search = (text: string) =>
  fireEvent.change(screen.getByPlaceholderText(/Search by name, SKU/), { target: { value: text } });

describe('Inventory > Stock search finds a unit by its own code', () => {
  it.each([
    ['BV0000000043', 'Carrera CA 8895', 'Wayfarer'],
    ['bv0000000042', 'Carrera CA 8895', 'Wayfarer'],
    ['BV--91FA3858', 'Wayfarer', 'Carrera CA 8895'],
  ])('%s shows its product and only its product', (code, shown, hidden) => {
    renderPage();
    search(code);
    expect(screen.getByText(shown)).toBeInTheDocument();
    expect(screen.queryByText(hidden)).not.toBeInTheDocument();
  });

  // A label names ONE unit, so a code matches whole: part of one lists nothing.
  it.each(['BV', 'bv00000000', 'BV000000004'])('%s (part of a code) lists no product', (part) => {
    renderPage();
    search(part);
    expect(screen.queryByText('Carrera CA 8895')).not.toBeInTheDocument();
    expect(screen.queryByText('Wayfarer')).not.toBeInTheDocument();
  });
});

describe('Manage Barcode from the stock row', () => {
  it("opens with the product's manufacturer GTIN, not the unit's IMS code", () => {
    renderPage();
    search('Carrera');
    fireEvent.click(screen.getByRole('button', { name: 'Manage Barcode' }));
    expect(screen.getByLabelText(/manufacturer barcode/i)).toHaveValue('4006381333931');
  });

  it('shows an old product barcode apart and opens with an empty box', () => {
    renderPage();
    search('Wayfarer');
    fireEvent.click(screen.getByRole('button', { name: 'Manage Barcode' }));
    expect(screen.getByLabelText(/manufacturer barcode/i)).toHaveValue('');
    expect(screen.getByText(/not a maker barcode/i)).toHaveTextContent('Old IMS code 5260181590836');
  });
});
