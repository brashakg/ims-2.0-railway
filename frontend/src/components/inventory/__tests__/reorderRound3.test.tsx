// ============================================================================
// Reorder screens - review round 3 (F73, owner D12)
// ============================================================================
// 1. The Reorder dashboard shows the SERVER's verdict, never its own sum of
//    available - reserved against the level.
// 6. With no active shop the level is never sent with store_id ''.
// 7. Stock Replenishment shows the server's top-up quantity.

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

const auth = vi.hoisted(() => ({ store: 'BV-DHN-02' as string | undefined }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'U', activeStoreId: auth.store, storeIds: ['BV-DHN-02'], roles: ['STORE_MANAGER'] },
    hasRole: (roles: string[]) => roles.includes('STORE_MANAGER'),
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

const feed = vi.hoisted(() => ({
  rows: [] as Array<Record<string, unknown>>,
  ledger: [] as Array<Record<string, unknown>>,
}));
const http = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(async () => ({ data: {} })),
  post: vi.fn(async () => ({ data: {} })),
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
import { StockReplenishment } from '../../../pages/inventory/StockReplenishment';

const putUrls = () => http.put.mock.calls.map((c) => String((c as unknown[])[0]));

const ledgerRow = (status: string, n = 1) =>
  Array.from({ length: n }, () => ({
    product_id: 'P-FRAME', sku: 'SKU-P-FRAME', name: 'Carrera CA8895', brand: 'Carrera',
    category: 'FR', quantity: 1, status,
  }));

beforeEach(() => {
  for (const fn of Object.values(http)) fn.mockClear();
  auth.store = 'BV-DHN-02';
});

describe('Reorder dashboard reads the server verdict', () => {
  it('level 6, 3 sellable + 3 reserved on the ledger: the server says Low, so the screen says Low (not its own Critical)', async () => {
    feed.rows = [{ _id: 'P-FRAME', quantity: 3, reorder_point: 6, stock_status: 'low', top_up_qty: 4 }];
    feed.ledger = [...ledgerRow('AVAILABLE', 3), ...ledgerRow('RESERVED', 3)];
    render(<MemoryRouter><ReorderDashboard /></MemoryRouter>);
    expect(await screen.findByText('Low Stock')).toBeTruthy();
    expect(screen.queryByText('Critical', { selector: 'span' })).toBeNull();
    expect(screen.queryByText('Out of Stock')).toBeNull();
  });

  it('the server says Critical: the screen says Critical whatever the ledger sums to', async () => {
    feed.rows = [{ _id: 'P-FRAME', quantity: 2, reorder_point: 6, stock_status: 'critical', top_up_qty: 5 }];
    feed.ledger = ledgerRow('AVAILABLE', 5);
    render(<MemoryRouter><ReorderDashboard /></MemoryRouter>);
    expect(await screen.findByText('Critical', { selector: 'span' })).toBeTruthy();
  });
});

describe('Reorder level save with no active shop', () => {
  it('never sends store_id "" - says Pick a shop first and the level field is locked', async () => {
    feed.rows = [{ _id: 'P-FRAME', quantity: 1, reorder_point: 2, stock_status: 'low', top_up_qty: 2 }];
    feed.ledger = ledgerRow('AVAILABLE', 1);
    const view = render(<MemoryRouter><ReorderDashboard /></MemoryRouter>);
    fireEvent.click(await screen.findByTitle('Configure reorder point'));
    auth.store = undefined; // the admin switches to "no shop"
    view.rerender(<MemoryRouter><ReorderDashboard /></MemoryRouter>);
    expect((await screen.findByRole('alert')).textContent).toMatch(/pick a shop first/i);
    fireEvent.click(await screen.findByRole('button', { name: /save/i }));
    await Promise.resolve();
    expect(putUrls().some((u) => u.includes('reorder-levels'))).toBe(false);
  });
});

describe('Stock Replenishment shows the server top-up', () => {
  it('level 5, 2 on hand: suggests the server\'s 4, not level - stock (3)', async () => {
    feed.rows = [{
      product_id: 'P-FRAME', _id: 'P-FRAME', name: 'Carrera CA8895', sku: 'SKU-P-FRAME',
      quantity: 2, reorder_point: 5, stock_status: 'low', top_up_qty: 4,
    }];
    render(<MemoryRouter><StockReplenishment /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Carrera CA8895')).toBeTruthy());
    // level 5 and the server's top-up 4 are shown; the old client gap (3) is not
    const qtyCell = screen.getAllByText('4');
    expect(qtyCell.length).toBeGreaterThan(0);
    expect(screen.queryByText('3')).toBeNull();
  });
});
