// ============================================================================
// Ctrl-K order search keeps its reach after GET /orders started honouring limit
// ============================================================================
// Audit R30 made GET /orders page on ONE query that honours skip/limit. Before
// it, a store login's list ignored `limit` and returned the shop's newest 500,
// and the palette (which filters client-side) relied on that. This pins the
// palette to the same 500 reach: the 60th-newest order must still be found.

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeAll } from 'vitest';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Tester', roles: ['SALES_STAFF'], activeRole: 'SALES_STAFF', activeStoreId: 'BV-DHN-01', storeIds: ['BV-DHN-01'] },
    hasModuleAccess: () => true,
  }),
}));
vi.mock('../../../services/api/customers', () => ({
  customerApi: { searchByPhone: vi.fn().mockResolvedValue([]), getCustomers: vi.fn().mockResolvedValue([]) },
}));
// A server that honours limit, like GET /orders after R30: newest first.
const ALL = Array.from({ length: 60 }, (_, i) => ({ order_id: `ORD-REACH-${String(i + 1).padStart(3, '0')}` }));
const getOrders = vi.fn(async (p?: { limit?: number }) => ({ orders: ALL.slice(0, p?.limit ?? 50) }));
vi.mock('../../../services/api/sales', () => ({ orderApi: { getOrders: (p: any) => getOrders(p) } }));
vi.mock('../../../services/api/products', () => ({
  productApi: { getProducts: vi.fn().mockResolvedValue([]), searchProducts: vi.fn().mockResolvedValue([]) },
}));

import { CommandPalette } from '../CommandPalette';

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  Element.prototype.scrollIntoView ??= () => {};
});

describe('CommandPalette - order search reach', () => {
  it('finds the 60th-newest order of the shop', async () => {
    render(
      <MemoryRouter>
        <CommandPalette open onOpenChange={vi.fn()} />
      </MemoryRouter>,
    );
    await userEvent.setup().type(await screen.findByRole('combobox'), 'ORD-REACH-060');
    expect(await screen.findByText('ORD-REACH-060', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(getOrders).toHaveBeenCalledWith(expect.objectContaining({ limit: 500 }));
  });
});
