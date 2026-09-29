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

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

vi.stubGlobal('requestIdleCallback', () => 0);
import { MemoryRouter } from 'react-router-dom';
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
  };
});

import { InventoryLayout } from '../InventoryLayout';

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
});
