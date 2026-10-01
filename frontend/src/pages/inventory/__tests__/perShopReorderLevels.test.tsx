// ============================================================================
// Per-shop reorder levels on the stock screens - audit F73, owner D12
// ============================================================================
// The owner (2026-09-29): reorder points are PER SHOP; a manager sets their own
// shop's level, an admin any shop. Before the fix the only stock-screen editor was the
// Reorder dashboard, which (a) lists only products ALREADY on the low-stock
// list -- so a product whose level is 'not set' can never be given one there --
// and (b) saves through PUT /products/{id}: one chain-wide reorder_point that a
// store manager is refused (ADMIN/CATALOG_MANAGER only) and that, when an admin
// saves it, changes every shop at once.
//
// Each test was committed as it.fails first (reproducing the finding) and
// passes with the fix. The network is the one seam: the axios client is a spy,
// so a test only asks WHAT was written, never which helper wrote it.

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { StockItem } from '../inventoryQueries';

const SHOP = 'BV-DHN-02';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-M', activeStoreId: 'BV-DHN-02', storeIds: ['BV-DHN-02'], roles: ['STORE_MANAGER'] },
    hasRole: (roles: string[]) => roles.includes('STORE_MANAGER'),
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

// The network seam. GETs answer the two lists the Reorder dashboard loads.
const LOW_STOCK = { items: [{ _id: 'P-FRAME', quantity: 1, reorder_point: 2, auto_reorder_disabled: false }] };
const STOCK_ROWS = {
  items: [{
    product_id: 'P-FRAME', sku: 'SKU-P-FRAME', name: 'Carrera CA8895', brand: 'Carrera',
    category: 'FR', quantity: 1, status: 'AVAILABLE', reorder_point: 2, reorder_quantity: 3,
  }],
};
const http = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(async () => ({ data: {} })),
  post: vi.fn(async () => ({ data: {} })),
  patch: vi.fn(async () => ({ data: {} })),
  delete: vi.fn(async () => ({ data: {} })),
}));
http.get.mockImplementation(async (url: string) => ({
  data: url.includes('low-stock') ? LOW_STOCK : url.includes('/inventory/stock') ? STOCK_ROWS : {},
}));
vi.mock('../../../services/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../services/api/client')>()),
  default: http,
}));

// The ledger's rows as the server sends them for THIS shop: no level typed
// here (null = not set).
const ITEMS: StockItem[] = [
  {
    id: 'P-FRAME', sku: 'SKU-P-FRAME', name: 'Carrera CA8895', brand: 'Carrera',
    category: 'FR', mrp: 9000, offerPrice: 9000, stock: 1, reserved: 0,
    reorder_point: null, low_stock: false,
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
import { ReorderDashboard } from '../../../components/inventory/ReorderDashboard';

type Call = { method: string; url: string; body: unknown };
const writes = (): Call[] =>
  (['put', 'post', 'patch'] as const).flatMap((method) =>
    http[method].mock.calls.map((c) => ({ method, url: String(c[0]), body: c[1] as unknown })),
  );
/** A write that names this shop and carries the level. */
const shopWrite = (level: number) =>
  writes().find((w) => {
    const s = JSON.stringify(w);
    return s.includes(SHOP) && new RegExp(`[:,[]${level}(?![0-9.])`).test(s);
  });
/** The chain-wide write the fix removes: reorder_point on the product. */
const chainWrite = () =>
  writes().find((w) => /\/products\//.test(w.url) && JSON.stringify(w.body ?? {}).includes('reorder_point'));

beforeEach(() => {
  for (const fn of Object.values(http)) fn.mockClear();
});

function renderLedger() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/inventory/stock']}>
        <Routes>
          <Route element={<Outlet context={{ storeId: SHOP, isOnlineStoreView: false, stores: [] }} />}>
            <Route path="/inventory/stock" element={<InventoryStockPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('F73/D12 - the stock ledger', () => {
  it("lets a store manager give a 'not set' product THIS shop's level, written for this shop only", async () => {
    renderLedger();
    const row = screen.getByText('Carrera CA8895').closest('tr') as HTMLElement;
    // The control (named 'reorder level') may be the input itself or a button
    // that opens it.
    let control = within(row).getAllByLabelText(/reorder level/i)[0] as HTMLElement;
    if (control.tagName !== 'INPUT') {
      fireEvent.click(control);
      control = (await screen.findAllByLabelText(/reorder level/i)).find((el) => el.tagName === 'INPUT') as HTMLElement;
    }
    fireEvent.change(control, { target: { value: '2' } });
    const save = screen.queryByRole('button', { name: /save/i });
    if (save) fireEvent.click(save);
    else fireEvent.keyDown(control, { key: 'Enter' });
    await waitFor(() => expect(shopWrite(2)).toBeTruthy());
    expect(chainWrite()).toBeUndefined();
  });
});

describe('F73/D12 - the Reorder dashboard', () => {
  it("saves the level for the manager's own shop, never one chain-wide reorder_point", async () => {
    render(
      <MemoryRouter>
        <ReorderDashboard />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByTitle('Configure reorder point'));
    // The level is sent only when it changed (round 4): 2 -> 3.
    fireEvent.change(await screen.findByPlaceholderText('not set'), { target: { value: '3' } });
    fireEvent.click(await screen.findByRole('button', { name: /save/i }));
    await waitFor(() => expect(writes().length).toBeGreaterThan(0));
    expect(chainWrite()).toBeUndefined();
    expect(shopWrite(3)).toBeTruthy();
  });
});
