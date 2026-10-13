// ============================================================================
// Review r3 #16: Stock aging "Tied capital (at cost)" is never "Rs 0.0L"
// ============================================================================
// Inventory > Stock aging, the Class C card. "Tied capital (at cost)" printed
// the slow movers' cost as lakhs to one decimal, so a shop whose slow stock
// cost Rs 4,000 read "Rs 0.0L" -- the F56 "Rs 0 on one screen" symptom that
// review r2 #22 fixed on the inventory value tiles only. The rule is the
// tiles' one (InventoryLayout tileMoney): under a lakh, whole rupees; from a
// lakh up, lakhs.
//
// Owner ruling 2026-10-12 (what we pay, stock at cost included, is the accounts
// roles'): the server sends costValue to them only. Everyone else -- managers
// included -- reads Tied capital at MRP, the figure the card always showed.

import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', activeStoreId: 'BV-DHN-01', storeIds: ['BV-DHN-01'], roles: ['STORE_MANAGER'] },
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

let products: Record<string, unknown>[] = [];
const getAgingReport = vi.fn(async () => ({ products, summary: {} }));
vi.mock('../../../services/api', () => ({
  inventoryApi: { getAgingReport: (...a: unknown[]) => getAgingReport(...(a as [])) },
}));

import { StockAgingReport } from '../StockAgingReport';

/** One aging row; `costValue` is the product's stock at cost (null = the
 *  server sent no cost), `value` its selling value (quantity x MRP). */
function row(id: string, classification: 'A' | 'B' | 'C', costValue: number | null, value = 5800) {
  return {
    id, sku: `FR-${id}`, name: `Frame ${id}`, brand: 'Test', category: 'FRAME',
    quantity: 1, value, costValue, daysInStock: 120, salesLast30Days: 0, salesLast90Days: 0,
    turnoverRate: 0, classification, ageCategory: '91-180',
  };
}

async function tiedCapital(): Promise<string> {
  const line = await screen.findByText(/Tied capital \(at /);
  await waitFor(() => expect(getAgingReport).toHaveBeenCalled());
  return (line.textContent ?? '').replace(/\s+/g, ' ').trim();
}

describe('Stock aging Tied capital (review r3 #16)', () => {
  beforeEach(() => {
    getAgingReport.mockClear();
  });

  it('reads whole rupees under a lakh, never Rs 0.0L', async () => {
    // Slow movers cost 2,500 + 1,500.40 = Rs 4,000 (to the rupee); the fast
    // mover's cost is not tied capital.
    products = [row('p1', 'C', 2500), row('p2', 'C', 1500.4), row('p3', 'A', 90000)];
    render(<StockAgingReport />);
    await waitFor(async () => expect(await tiedCapital()).toContain('₹4,000'));
    expect(await tiedCapital()).not.toContain('0.0L');
  });

  it('still reads lakhs from a lakh up', async () => {
    products = [row('p1', 'C', 250000)];
    render(<StockAgingReport />);
    await waitFor(async () => expect(await tiedCapital()).toBe('Tied capital (at cost): ₹2.5L'));
  });

  it('counts the units with no cost instead of valuing them at Rs 0 (F47)', async () => {
    // P-COST: 2 units at 1,000. P-NOCOST: 3 units with no cost anywhere -- the
    // server values it 0 and says uncostedUnits 3.
    products = [
      { ...row('P-COST', 'C', 2000), quantity: 2 },
      { ...row('P-NOCOST', 'C', 0), quantity: 3, uncostedUnits: 3 },
    ];
    render(<StockAgingReport />);
    await waitFor(async () => expect(await tiedCapital()).toBe('Tied capital (at cost): ₹2,000'));
    expect(screen.getByText('3 units have no cost')).toBeInTheDocument();
    const cell = screen.getByText('Frame P-NOCOST').closest('tr')!;
    expect(cell.textContent).toContain('no cost');
    expect(cell.textContent).not.toContain('₹0');
  });

  it('a login the server sends no cost (a manager) reads Tied capital at MRP, never a cost', async () => {
    products = [row('p1', 'C', null, 5800), row('p2', 'C', null, 1200), row('p3', 'A', null, 90000)];
    render(<StockAgingReport />);
    await waitFor(async () => expect(await tiedCapital()).toBe('Tied capital (at MRP): ₹7,000'));
    expect(screen.getByText('Value (MRP)')).toBeInTheDocument();
    expect(screen.queryByText(/at cost|Value \(cost\)|no cost/)).toBeNull();
    const cell = screen.getByText('Frame p1').closest('tr')!;
    expect(cell.textContent).toContain('₹5,800');
  });

  it('a cost reader sees Value (cost) per row, not the MRP', async () => {
    products = [row('p1', 'C', 3100, 5800)];
    render(<StockAgingReport />);
    await waitFor(async () => expect(await tiedCapital()).toBe('Tied capital (at cost): ₹3,100'));
    expect(screen.getByText('Value (cost)')).toBeInTheDocument();
    const cell = screen.getByText('Frame p1').closest('tr')!;
    expect(cell.textContent).toContain('₹3,100');
    expect(cell.textContent).not.toContain('5,800');
  });
});
