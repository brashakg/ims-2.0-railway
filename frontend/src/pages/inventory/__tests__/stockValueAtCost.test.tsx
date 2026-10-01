// ============================================================================
// Audit F47: the Inventory "Stock value" headline is what the stock COST
// ============================================================================
// Owner audit 2026-09-29: "Stock value - total landed inventory" read Rs 5.0L,
// which is the SELLING price of 83 units that cost Rs 2.8L. Ruling 2026-09-28:
// the headline is at COST (it matches the bills); selling value is shown
// separately and labelled.
//
// It used to sum (offerPrice || mrp) * stock under that label. The backend
// (backend/tests/test_stock_value_at_cost.py) gives the manager's ledger rows
// `cost_value`; the tile sums it, and the selling value has its own cell.
//
// These were `it.fails` pins while the finding was open. The plain test first
// proves the harness renders the strip from these rows, so they cannot pass
// hollow.

import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

vi.stubGlobal('requestIdleCallback', () => 0);
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', activeStoreId: 'BV-DHN-01', storeIds: ['BV-DHN-01'], roles: ['STORE_MANAGER'] },
    hasRole: (roles: string[]) => roles.includes('STORE_MANAGER'),
  }),
}));
vi.mock('../../../components/inventory/StockTransferModal', () => ({
  StockTransferModal: () => null,
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

// 83 units: selling 50 x 6000 + 33 x 6061 = Rs 5.0L; cost 1,70,000 + 1,10,000
// = Rs 2.8L (the manager's rows carry cost_value, as the backend pin requires).
const ROWS: Record<string, unknown>[] = [
  { id: 'p1', sku: 'FR-1', name: 'Carrera CA 8895', category: 'FRAME', brand: 'Carrera',
    mrp: 7000, offerPrice: 6000, stock: 50, reserved: 0, cost_value: 170000, unit_cost: 3400 },
  { id: 'p2', sku: 'FR-2', name: 'Ray-Ban RB3025', category: 'FRAME', brand: 'Ray-Ban',
    mrp: 7000, offerPrice: 6061, stock: 33, reserved: 0, cost_value: 110000, unit_cost: 3333.33 },
];

vi.mock('../inventoryQueries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../inventoryQueries')>();
  const idle = { data: undefined, isFetching: false, isError: false, isPending: false };
  return {
    ...actual,
    useStock: () => ({ ...idle, data: rows }),
    useLowStock: () => idle,
    useOnlineStatus: () => idle,
    useFixturesMap: () => idle,
    useQuarantineUnlabeled: () => idle,
    useInventoryStores: () => ({ data: [] }),
    useOnlineSummary: () => idle,
    useCataloguers: () => idle,
    usePlacements: () => idle,
  };
});

import { InventoryLayout } from '../InventoryLayout';
import { InventoryStockPage } from '../InventoryStockPage';

let rows = ROWS;

function renderStrip() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/inventory/stock']}>
        <InventoryLayout />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The value line of the stat-strip cell whose label reads `label`. */
function statValue(label: string): string {
  const cell = screen.getByText(label).parentElement as HTMLElement;
  return (cell.querySelector('.v')?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

describe('F47: the Inventory stock-value headline', () => {
  it('renders the stat strip from the ledger rows (harness check)', () => {
    renderStrip();
    expect(statValue('Total SKUs')).toBe('2');
    expect(screen.getByText('Stock value')).toBeInTheDocument();
  });

  it('F47: "Stock value" is what the stock cost (Rs 2.8L), not its selling price (Rs 5.0L)', () => {
    renderStrip();
    expect(statValue('Stock value')).toBe('₹ 2.8L');
  });

  it('F47: the selling value is shown separately and labelled as selling', () => {
    renderStrip();
    // A stat cell whose label or caption says "selling" and whose value is 5.0L.
    const cells = screen
      .queryAllByText(/selling/i)
      .map((el) => el.parentElement?.querySelector('.v')?.textContent ?? '');
    expect(cells.some((v) => /5\.0L/.test(v))).toBe(true);
  });

  it('F47: a login the server sends no cost (the counter) sees no figure under "Stock value"', () => {
    rows = ROWS.map((r) => ({ ...r, cost_value: undefined, unit_cost: undefined }));
    try {
      renderStrip();
      expect(statValue('Stock value')).toBe('—');
    } finally {
      rows = ROWS;
    }
  });

  it('F47: both tiles count the same units -- a reserved frame is in the cost and the selling value', () => {
    // 20 on the shelf + 2 reserved for orders: cost covers 22 (stock_value), so
    // must the selling value: 22 x 10,000 = 2.2L, not 20 x 10,000 = 2.0L.
    rows = [{ id: 'p3', sku: 'FR-3', name: 'Vogue VO5', category: 'FRAME', brand: 'Vogue',
      mrp: 12000, offerPrice: 10000, stock: 20, reserved: 2, cost_value: 110000, unit_cost: 5000 }];
    try {
      renderStrip();
      expect(statValue('Stock value')).toBe('₹ 1.1L');
      expect(statValue('Selling value')).toBe('₹ 2.2L');
    } finally {
      rows = ROWS;
    }
  });

  it('F47: the cost tile says what cost it is -- the price at receipt, not the bill', () => {
    renderStrip();
    expect(screen.queryByText(/matches the bills/i)).toBeNull();
    // Review r1 #39: the caption also names the units it counts.
    expect(screen.getByText('at cost: price at receipt, ex GST · shelf + reserved')).toBeInTheDocument();
  });
});

// ============================================================================
// Review r1 #34: stock nobody priced is said, not valued at Rs 0 in silence
// ============================================================================
// A unit with no cost on it or its product added 0 to "Stock value" (at cost)
// with no hint, and its ledger row read "Cost / unit Rs 0". The server now
// sends unit_cost null + uncosted_units N (backend/tests/
// test_stock_value_at_cost.py, test_r1_34_*); the tile says "N units have no
// cost" and the row says "no cost".

const UNCOSTED: Record<string, unknown>[] = [
  ...ROWS,
  { id: 'p4', sku: 'FR-4', name: 'Unknown cost frame', category: 'FRAME', brand: 'Generic',
    mrp: 2000, offerPrice: 1500, stock: 2, reserved: 0, cost_value: 0, unit_cost: null, uncosted_units: 2 },
  { id: 'p5', sku: 'FR-5', name: 'Half priced frame', category: 'FRAME', brand: 'Generic',
    mrp: 6000, offerPrice: 5000, stock: 2, reserved: 0, cost_value: 4000, unit_cost: 4000, uncosted_units: 1 },
];

describe('review r1 #34: the stock-value tile counts the units with no cost', () => {
  it('says "3 units have no cost" under the at-cost headline', () => {
    rows = UNCOSTED;
    try {
      renderStrip();
      expect(statValue('Stock value')).toBe('₹ 2.8L'); // 2,80,000 + 0 + 4,000
      expect(screen.getByText('3 units have no cost')).toBeInTheDocument();
    } finally {
      rows = ROWS;
    }
  });

  it('says "1 unit has no cost" for one', () => {
    rows = [UNCOSTED[3]];
    try {
      renderStrip();
      expect(screen.getByText('1 unit has no cost')).toBeInTheDocument();
    } finally {
      rows = ROWS;
    }
  });

  it('says nothing when every unit has a cost, and nothing to the counter', () => {
    const first = renderStrip();
    expect(screen.queryByText(/no cost/)).toBeNull();
    first.unmount();
    rows = UNCOSTED.map((r) => ({ ...r, cost_value: undefined, unit_cost: undefined, uncosted_units: undefined }));
    try {
      renderStrip();
      expect(statValue('Stock value')).toBe('—');
      expect(screen.queryByText(/no cost/)).toBeNull();
    } finally {
      rows = ROWS;
    }
  });
});

function renderLedger() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/inventory/stock']}>
        <Routes>
          <Route element={<Outlet context={{ storeId: 'BV-DHN-01', isOnlineStoreView: false, stores: [] }} />}>
            <Route path="/inventory/stock" element={<InventoryStockPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The text of `product`'s cell in the column headed `header`. */
function ledgerCell(product: string, header: string): string {
  const table = screen.getByRole('table');
  const heads = within(table).getAllByRole('columnheader').map((h) => (h.textContent ?? '').trim());
  const col = heads.findIndex((h) => h.toLowerCase() === header.toLowerCase());
  expect(col).toBeGreaterThanOrEqual(0);
  const row = within(table).getByText(product).closest('tr') as HTMLElement;
  return (row.querySelectorAll('td')[col]?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

describe('review r1 #34: the ledger row of a unit with no cost', () => {
  it('reads "no cost", never Rs 0, and a part-priced row keeps the costed unit price', () => {
    rows = UNCOSTED;
    try {
      renderLedger();
      expect(ledgerCell('Unknown cost frame', 'Cost / unit')).toBe('no cost');
      expect(ledgerCell('Half priced frame', 'Cost / unit')).toBe('₹4,000+1 no cost');
      expect(ledgerCell('Carrera CA 8895', 'Cost / unit')).toBe('₹3,400');
    } finally {
      rows = ROWS;
    }
  });
});

// ============================================================================
// Review r1 #39: the In-Store cell and the CSV count the units the tiles count
// ============================================================================
// The server's `stock` is the AVAILABLE units only; reserved ones are counted
// apart (inventory/stock.py, backend test_r1_39_*). The cell showed
// stock - reserved (3 on the shelf + 1 reserved read "2 +1 reserved", 3 units
// for a shop holding 4) and the CSV wrote Available = stock - reserved. The
// tiles count stock + reserved = 4.

const HELD: Record<string, unknown>[] = [
  { id: 'p6', sku: 'FR-6', name: 'Vogue VO5', category: 'FRAME', brand: 'Vogue',
    mrp: 12000, offerPrice: 10000, stock: 3, reserved: 1, cost_value: 20000, unit_cost: 5000, uncosted_units: 0 },
];

describe('review r1 #39: stock is the units for sale, reserved is apart', () => {
  it('the In-Store cell shows 3 for sale +1 reserved (4 in the shop), not 2', () => {
    rows = HELD;
    try {
      renderLedger();
      // The cell also carries the shop's reorder level (#1179); the stock
      // figure leads it: 3 for sale, +1 reserved -- never 2 (reserved taken twice).
      expect(ledgerCell('Vogue VO5', 'In-Store')).toMatch(/^3\+1 reserved(?!\d)/);
    } finally {
      rows = ROWS;
    }
  });

  it('the tiles count the same 4 units: 4 x 10,000 selling, 4 x 5,000 at cost', () => {
    rows = HELD;
    try {
      renderStrip();
      expect(statValue('Selling value')).toBe('₹ 0.4L');
      expect(statValue('Stock value')).toBe('₹ 0.2L');
      expect(screen.getByText('at offer price (MRP if none) · shelf + reserved')).toBeInTheDocument();
    } finally {
      rows = ROWS;
    }
  });

  it('the product drawer says In stock 4, Reserved 1, Available 3', () => {
    rows = HELD;
    try {
      renderLedger();
      fireEvent.click(screen.getByRole('button', { name: 'View Details' }));
      const term = (label: string) =>
        (screen.getByText(label, { selector: 'dt' }).nextElementSibling?.textContent ?? '').trim();
      expect([term('In stock'), term('Reserved'), term('Available')]).toEqual(['4', '1', '3']);
    } finally {
      rows = ROWS;
    }
  });

  it('the CSV export writes In Stock 4, Reserved 1, Available 3', async () => {
    rows = HELD;
    let blob: Blob | null = null;
    const create = vi.fn((b: Blob) => { blob = b; return 'blob:csv'; });
    const origCreate = URL.createObjectURL;
    const origRevoke = URL.revokeObjectURL;
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, writable: true, value: create });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, writable: true, value: vi.fn() });
    try {
      renderLedger();
      fireEvent.click(screen.getByRole('button', { name: /Export/ }));
      expect(create).toHaveBeenCalledTimes(1);
      const text = await new Promise<string>((resolve) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.readAsText(blob as unknown as Blob);
      });
      const [head, line] = text.split('\n');
      const cols = head.split(',');
      const cells = line.split(',');
      const at = (name: string) => cells[cols.indexOf(name)];
      expect([at('In Stock'), at('Reserved'), at('Available')]).toEqual(['4', '1', '3']);
    } finally {
      click.mockRestore();
      Object.defineProperty(URL, 'createObjectURL', { configurable: true, writable: true, value: origCreate });
      Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, writable: true, value: origRevoke });
      rows = ROWS;
    }
  });
});
