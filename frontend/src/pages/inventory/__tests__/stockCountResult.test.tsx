// ============================================================================
// The count result: exact missing pieces, and each person's own door
// ============================================================================
// Owner rulings S1 + 2026-08-25: the result names the missing pieces by unit
// barcode; only ADMIN / SUPERADMIN is offered the write-off; a store manager
// is offered "send to admin for write-off" instead. Value at cost goes to
// COST_ROLES only.

import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';

const DOC = {
  count_id: 'C1',
  audit_number: 'AUDIT-1',
  status: 'completed',
  store_id: 'S1',
  pieces_scanned: 45,
  products_expected: 48,
  products_counted: 46,
  products_missed: 2,
  full_count: false,
  not_counted: [{ product_id: 'P9', product_name: 'Carrera 5003', sku: 'C5003' }],
  variances: [
    {
      product_id: 'P1',
      product_name: 'Ray-Ban RB2140',
      sku: 'RB2140',
      missing_units: [{ unit_id: 'u1', barcode: 'BV19WF4471E9', arrived_at: '2026-08-12T00:00:00', unit_cost: 5210 }],
      extra_units: [{ barcode: 'BV18HC3304H1', recorded_store_id: 'S2', recorded_status: 'AVAILABLE' }],
    },
  ],
};

vi.mock('../../../services/api', () => ({
  inventoryApi: { getStockCount: vi.fn(async () => DOC) },
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}));
const auth = vi.hoisted(() => ({ role: 'STORE_MANAGER' }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u', activeStoreId: 'S1', roles: [auth.role] },
    hasRole: (roles: string[]) => roles.includes(auth.role),
  }),
}));

import { StockCountResult } from '../StockCountResult';

function renderAt() {
  return render(
    <MemoryRouter initialEntries={['/inventory/audit/C1']}>
      <Routes>
        <Route path="/inventory/audit/:countId" element={<StockCountResult />} />
      </Routes>
    </MemoryRouter>
  );
}

describe('stock count result', () => {
  it('a store manager sees the exact pieces and can only SEND for write-off', async () => {
    auth.role = 'STORE_MANAGER';
    renderAt();
    expect(await screen.findByText('BV19WF4471E9')).toBeInTheDocument();
    expect(screen.getByText(/BV18HC3304H1/)).toBeInTheDocument();
    expect(screen.getByText(/Carrera 5003/)).toBeInTheDocument();
    expect(screen.getByText(/46 of 48 styles counted/)).toBeInTheDocument();
    expect(screen.getByText('Send 1 piece to admin for write-off')).toBeInTheDocument();
    expect(screen.queryByText(/Write off these/)).toBeNull();
    expect(screen.queryByText(/5,210/)).toBeNull();
  });

  it('an admin is offered the write-off, with the value at cost', async () => {
    auth.role = 'ADMIN';
    renderAt();
    expect(await screen.findByText('Write off these 1 piece')).toBeInTheDocument();
    expect(screen.queryByText(/Send 1 piece/)).toBeNull();
    expect(screen.getAllByText('₹5,210').length).toBeGreaterThan(0);
  });
});
