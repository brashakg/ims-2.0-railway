// ============================================================================
// The count result: exact missing pieces, and each person's own door
// ============================================================================
// Owner rulings S1 + 2026-08-25: the result names the missing pieces by unit
// barcode; only ADMIN / SUPERADMIN is offered the write-off; a store manager
// is offered "send to admin for write-off" instead. Value at cost goes to
// COST_ROLES only.

import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

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

const box = vi.hoisted(() => ({ doc: null as any, reconcile: vi.fn(async () => ({ units_voided: 1 })) }));
vi.mock('../../../services/api', () => ({
  inventoryApi: {
    getStockCount: vi.fn(async () => box.doc),
    reconcileStockCount: (...a: unknown[]) => (box.reconcile as any)(...a),
  },
}));
beforeEach(() => {
  box.doc = DOC;
  box.reconcile.mockClear();
});
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

describe('section 17(5)(h) on the written-off count', () => {
  it('shows the GST input credit the write-off took back', async () => {
    auth.role = 'ADMIN';
    box.doc = { ...DOC, status: 'reconciled', units_voided: 1, shrinkage_value_written_off: 3100,
      itc_reversed: { igst: 0, cgst: 77.5, sgst: 77.5, total: 155 } };
    renderAt();
    expect(await screen.findByTestId('count-itc-reversed')).toHaveTextContent('GST input credit reversed: ₹155 (GSTR-3B 4(B)(1))');
  });

  it('a reversal held for want of a GSTIN is said, not counted as reversed', async () => {
    auth.role = 'ADMIN';
    box.doc = { ...DOC, status: 'reconciled', units_voided: 1, shrinkage_value_written_off: 3100,
      itc_reversed: { igst: 0, cgst: 0, sgst: 0, total: 0, unallocated_total: 155 } };
    renderAt();
    expect(await screen.findByTestId('count-itc-held')).toHaveTextContent('GST input credit held: ₹155');
    expect(screen.queryByTestId('count-itc-reversed')).toBeNull();
  });

  it('no line when the server sent none (a manager, or nothing claimed)', async () => {
    auth.role = 'STORE_MANAGER';
    box.doc = { ...DOC, status: 'reconciled', units_voided: 1 };
    renderAt();
    expect(await screen.findByText(/Written off: 1 piece/)).toBeInTheDocument();
    expect(screen.queryByTestId('count-itc-reversed')).toBeNull();
  });
});

describe('review round 1', () => {
  it('a zone count offers no write-off to anyone', async () => {
    auth.role = 'ADMIN';
    box.doc = { ...DOC, zone: 'Wall A', write_off_allowed: false };
    renderAt();
    expect(await screen.findByText('Zone count: recount the whole shop or category to write off.')).toBeInTheDocument();
    expect(screen.queryByText(/Write off these/)).toBeNull();
    expect(screen.queryByText(/to admin for write-off/)).toBeNull();
  });

  it('a store manager never sees the none-found value at cost', async () => {
    auth.role = 'STORE_MANAGER';
    box.doc = { ...DOC, write_off_allowed: true, none_found: [{ product_id: 'P9', product_name: 'Carrera 5003',
      sku: 'C5003', missing_units: [{ unit_id: 'u9', barcode: 'BV00000NF001', unit_cost: 1200 }] }] };
    renderAt();
    expect(await screen.findByText(/None found/)).toBeInTheDocument();
    expect(screen.queryByText(/1,200/)).toBeNull();
    expect(screen.queryByText(/pieces of styles with nothing found/)).toBeNull();
  });

  it('styles with nothing found are written off only after a second confirmation', async () => {
    auth.role = 'ADMIN';
    box.doc = {
      ...DOC,
      write_off_allowed: true,
      none_found: [{ product_id: 'P9', product_name: 'Carrera 5003', sku: 'C5003',
        missing_units: [{ unit_id: 'u9', barcode: 'BV00000NF001', unit_cost: 1200 }] }],
    };
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(true).mockReturnValueOnce(false);
    renderAt();
    expect(await screen.findByText(/None found \(1 style, 1 pieces\)/)).toBeInTheDocument();
    fireEvent.click(screen.getByText(/pieces of styles with nothing found/));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(String(confirm.mock.calls[1][0])).toBe(
      'Counted 46 of 48 styles. Write off 1 piece of 1 style worth ₹1,200 at cost?'
    );
    expect(box.reconcile).not.toHaveBeenCalled();
    confirm.mockReturnValueOnce(true).mockReturnValueOnce(true);
    fireEvent.click(screen.getByText(/pieces of styles with nothing found/));
    expect(box.reconcile).toHaveBeenCalledWith('C1', undefined, true);
    confirm.mockRestore();
  });
});
