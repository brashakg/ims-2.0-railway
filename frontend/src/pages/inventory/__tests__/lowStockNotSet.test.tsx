// Review round 4, item 16: a low-stock row with no threshold says 'not set',
// never a blank 'Min: '.
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../InventoryLayout', () => ({ useInventoryContext: () => ({ storeId: 'BV-DHN-02' }) }));
vi.mock('../inventoryQueries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../inventoryQueries')>()),
  useLowStock: () => ({
    isPending: false,
    data: [{ id: 'P1', name: 'Carrera', sku: 'S1', brand: 'Carrera', stock: 0, lowStockThreshold: undefined }],
  }),
}));

import { InventoryLowStockPage } from '../InventorySections';

describe('InventoryLowStockPage', () => {
  it("shows 'Min: not set' when the threshold is missing", () => {
    render(<InventoryLowStockPage />);
    expect(screen.getByText('Min: not set')).toBeTruthy();
  });
});
