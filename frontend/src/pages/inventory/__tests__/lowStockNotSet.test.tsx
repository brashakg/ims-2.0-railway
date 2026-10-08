// Review round 4, item 16: a low-stock row with no threshold says 'not set',
// never a blank 'Min: '.
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../InventoryLayout', () => ({ useInventoryContext: () => ({ storeId: 'BV-DHN-02' }) }));
vi.mock('../inventoryQueries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../inventoryQueries')>()),
  useLowStock: () => ({
    isPending: false,
    data: [
      { id: 'P1', name: 'Carrera', sku: 'S1', brand: 'Carrera', stock: 0, lowStockThreshold: undefined },
      // Round 11: the server says discontinued -- no Raise PO for it.
      { id: 'P2', name: 'Old Aviator', sku: 'S2', brand: 'Ray-Ban', stock: 3, lowStockThreshold: 5, discontinued: true },
    ],
  }),
}));

import { InventoryLowStockPage } from '../InventorySections';

describe('InventoryLowStockPage', () => {
  it("shows 'Min: not set' when the threshold is missing", () => {
    // MemoryRouter: each row links to the reorder desk (Raise PO).
    render(<MemoryRouter><InventoryLowStockPage /></MemoryRouter>);
    expect(screen.getByText('Min: not set')).toBeTruthy();
  });

  it('a discontinued row says so and offers no Raise PO', () => {
    render(<MemoryRouter><InventoryLowStockPage /></MemoryRouter>);
    expect(screen.getByText('Discontinued - not reordered')).toBeTruthy();
    // Only the Carrera row links to the reorder desk.
    expect(screen.getAllByRole('link', { name: 'Raise PO' })).toHaveLength(1);
  });
});
