// ============================================================================
// Review r2 #24: the Reorder dashboard never shows the MRP as a cost
// ============================================================================
// Inventory > Reorders. Each line's cost was mapped from
// unit_cost ?? cost_price ?? mrp, so a product with no cost on its units or
// master showed MRP x qty in the Order Qty cost cell -- and once CostCell
// showed managers per-unit cost (F47), a STORE_MANAGER read Rs 15,000 (MRP
// Rs 3,000 x 5) as the reorder's cost. 'Est. PO Value' summed it, and a PO
// generated there was drafted at the MRP (a PO rate becomes the product's
// cost: vendors/gst _promote_cost_from_rate).
//
// Now: the cost is the product's cost price, else what the shop's units were
// received at; a line with neither reads "no cost", is left out of the
// estimate and counted, and goes on a generated PO at 0 for the buyer to price.

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

const auth = vi.hoisted(() => ({ role: 'STORE_MANAGER' }));
const toast = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(),
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'U', activeStoreId: 'BV-DHN-01', storeIds: ['BV-DHN-01'], roles: [auth.role] },
    hasRole: (roles: string[]) => roles.includes(auth.role),
  }),
}));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));

const feed = vi.hoisted(() => ({
  rows: [] as Array<Record<string, unknown>>,
  ledger: [] as Array<Record<string, unknown>>,
}));
const http = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(async () => ({ data: {} })),
  post: vi.fn(async () => ({ data: { po_id: 'PO-1' } })),
  patch: vi.fn(async () => ({ data: {} })),
  delete: vi.fn(async () => ({ data: {} })),
}));
http.get.mockImplementation(async (url: string) => ({
  data: url.includes('low-stock')
    ? { items: feed.rows }
    : url.includes('/inventory/stock')
      ? { items: feed.ledger }
      : {},
}));
vi.mock('../../../services/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../services/api/client')>()),
  default: http,
}));

import { ReorderDashboard } from '../ReorderDashboard';

// P-NOCOST: no cost anywhere (unit_cost null on the ledger row), MRP Rs 3,000,
// reorder 5. P-COSTED: its units were received at Rs 1,200, reorder 2.
const NOCOST = {
  product_id: 'P-NOCOST', sku: 'FR-NC', name: 'Uncosted frame', category: 'FRAME',
  mrp: 3000, offer_price: 3000, reorder_quantity: 5, unit_cost: null, supplier_id: 'V-1',
};
const COSTED = {
  product_id: 'P-COSTED', sku: 'FR-C', name: 'Costed frame', category: 'FRAME',
  mrp: 4000, offer_price: 4000, reorder_quantity: 2, unit_cost: 1200, supplier_id: 'V-1',
};
const low = (id: string) => ({ _id: id, quantity: 0, reorder_point: 2, stock_status: 'out-of-stock' });

const renderDashboard = () =>
  render(
    <MemoryRouter>
      <ReorderDashboard />
    </MemoryRouter>,
  );

/** The Order Qty cell of the row showing `name`. */
function orderQtyCell(name: string): string {
  const table = screen.getByRole('table');
  const heads = within(table).getAllByRole('columnheader').map((h) => (h.textContent ?? '').trim().toLowerCase());
  const col = heads.indexOf('order qty');
  expect(col).toBeGreaterThanOrEqual(0);
  const row = within(table).getByText(name).closest('tr') as HTMLElement;
  return (row.querySelectorAll('td')[col]?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

/** The Est. PO Value card's text (value + caption). */
function estimateCard(): string {
  const label = screen.getByText('Est. PO Value');
  return (label.parentElement?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

beforeEach(() => {
  for (const fn of [...Object.values(http), ...Object.values(toast)]) fn.mockClear();
  auth.role = 'STORE_MANAGER';
  feed.rows = [low('P-NOCOST')];
  feed.ledger = [NOCOST];
});

describe('review r2 #24: a reorder line with no cost is never priced at its MRP', () => {
  it('renders the low-stock line from the feed (harness check)', async () => {
    renderDashboard();
    expect(await screen.findByText('Uncosted frame')).toBeInTheDocument();
    expect(orderQtyCell('Uncosted frame')).toMatch(/^5/);
  });

  it('a manager reads "no cost" in the cost cell, never MRP x qty (Rs 15,000)', async () => {
    renderDashboard();
    await screen.findByText('Uncosted frame');
    expect(orderQtyCell('Uncosted frame')).toBe('5no cost');
    expect(screen.queryByText(/15,000/)).toBeNull();
  });

  it('Est. PO Value is a dash, and says the line has no cost, when no line has one', async () => {
    renderDashboard();
    await screen.findByText('Uncosted frame');
    const card = estimateCard();
    expect(card).not.toMatch(/15,000|0\.2L|0\.1L/);
    expect(card).toBe('Est. PO Value-at cost · 1 line has no cost, not counted');
  });

  it('Est. PO Value sums the costed lines only and counts the rest', async () => {
    feed.rows = [low('P-NOCOST'), low('P-COSTED')];
    feed.ledger = [NOCOST, COSTED];
    renderDashboard();
    await screen.findByText('Costed frame');
    expect(orderQtyCell('Costed frame')).toBe('2₹2,400');
    expect(orderQtyCell('Uncosted frame')).toBe('5no cost');
    // 2 x 1,200 = 2,400 -- not 2,400 + 5 x 3,000 = 17,400.
    expect(estimateCard()).toBe('Est. PO Value₹2,400at cost · 1 line has no cost, not counted');
  });

  it("the product's cost price comes first, then what its units were received at", async () => {
    feed.rows = [low('P-COSTED')];
    feed.ledger = [{ ...COSTED, cost_price: 1000 }];
    renderDashboard();
    await screen.findByText('Costed frame');
    expect(orderQtyCell('Costed frame')).toBe('2₹2,000');
    expect(estimateCard()).toBe('Est. PO Value₹2,000at cost');
  });

  it('a cost of 0 is no cost (the server never sends a made-up Rs 0)', async () => {
    feed.ledger = [{ ...NOCOST, cost_price: 0, unit_cost: 0 }];
    renderDashboard();
    await screen.findByText('Uncosted frame');
    expect(orderQtyCell('Uncosted frame')).toBe('5no cost');
  });

  it('a counter role sees a dash in both, and no "no cost" hint', async () => {
    auth.role = 'SALES_STAFF';
    feed.rows = [low('P-NOCOST'), low('P-COSTED')];
    feed.ledger = [NOCOST, COSTED];
    renderDashboard();
    await screen.findByText('Costed frame');
    expect(orderQtyCell('Costed frame')).toBe('2-');
    expect(orderQtyCell('Uncosted frame')).toBe('5-');
    expect(screen.queryByText(/no cost/)).toBeNull();
    expect(estimateCard()).toBe('Est. PO Value-');
  });

  it('a generated PO drafts the uncosted line at 0, never at the MRP, and the costed one at its cost', async () => {
    feed.rows = [low('P-NOCOST'), low('P-COSTED')];
    feed.ledger = [NOCOST, COSTED];
    renderDashboard();
    await screen.findByText('Costed frame');
    fireEvent.click(screen.getByText('Select All'));
    fireEvent.click(screen.getByRole('button', { name: /Generate PO/ }));
    await waitFor(() => expect(http.post).toHaveBeenCalledTimes(1));
    const [url, body] = http.post.mock.calls[0] as unknown as [string, { items: Array<{ product_id: string; unit_price: number }> }];
    expect(url).toBe('/vendors/purchase-orders');
    const price = Object.fromEntries(body.items.map((i) => [i.product_id, i.unit_price]));
    expect(price).toEqual({ 'P-NOCOST': 0, 'P-COSTED': 1200 });
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
    const said = String(toast.success.mock.calls[0][0]);
    expect(said).toContain('Est. ₹2,400 at cost');
    expect(said).toContain('1 line(s) have no cost: price them on the PO');
    expect(said).not.toMatch(/17,400/);
  });
});
