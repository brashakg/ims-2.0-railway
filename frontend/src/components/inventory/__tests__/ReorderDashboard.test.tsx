// Reorder dashboard: the PO estimate is cost x qty, never MRP x qty. The stock
// ledger row carries cost_price only for the product-cost roles (backend
// cost_mask); a row without it used to fall back to the MRP, so a manager read
// a retail figure as the reorder cost (and the generated PO lines took it).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

let ledgerRow: Record<string, unknown> = {};
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { activeStoreId: 'BV-TEST-01' },
    hasRole: () => true, // a STORE_MANAGER: a product-cost role
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}));
vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }));
vi.mock('../../../services/api/inventory', () => ({
  inventoryApi: {
    getLowStock: async () => ({ items: [{ _id: 'P1', quantity: 0 }] }),
    getStock: async () => ({ items: [ledgerRow] }),
  },
  vendorsApi: {},
  reorderApi: {},
}));

import { ReorderDashboard } from '../ReorderDashboard';

const ROW = { product_id: 'P1', sku: 'BV-FR-1', name: 'RB Frame', mrp: 4000, reorder_quantity: 5 };

describe('ReorderDashboard reorder cost', () => {
  beforeEach(() => {
    ledgerRow = { ...ROW };
  });

  it('never prices the reorder at MRP when the row has no cost', async () => {
    render(<ReorderDashboard />);
    await screen.findByText('RB Frame');
    expect(screen.queryByText(/20,000/)).toBeNull();
  });

  it('prices it at the cost the ledger row carries', async () => {
    ledgerRow = { ...ROW, cost_price: 1600 };
    render(<ReorderDashboard />);
    await screen.findByText('RB Frame');
    expect(screen.getAllByText('₹8,000').length).toBeGreaterThan(0);
  });
});
