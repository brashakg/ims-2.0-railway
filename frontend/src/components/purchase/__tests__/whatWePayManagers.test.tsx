// ============================================================================
// What we pay is for the owner and accounts only (owner ruling 2026-10-12)
// ============================================================================
// A store / area manager still raises and reads a purchase order, but by
// QUANTITY: no cost box, no last-paid caption, no line totals, no grand total,
// no price lookup, and no price sent -- the server prices the lines. The PO
// detail and the printed PO list products and quantities only. Accounts keep
// all of it. The role list is CostCell's COST_ROLES (one list).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import type { UserRole } from '../../../types';

const toastMock = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(),
}));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));

const auth = vi.hoisted(() => ({ role: 'STORE_MANAGER' as string }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', roles: [auth.role], activeStoreId: 'BV-TEST-01' },
    hasRole: (r: string | string[]) => (Array.isArray(r) ? r : [r]).includes(auth.role),
  }),
}));

vi.mock('../../../services/api/inventory', () => ({
  vendorsApi: { getLastCost: vi.fn() },
}));
vi.mock('../../print/storeIdentity', () => ({
  resolveStoreIdentity: vi.fn().mockResolvedValue(null),
}));

import { PurchaseOrderComposer } from '../PurchaseOrderComposer';
import type { ComposerLine } from '../PurchaseOrderComposer';
import { COST_ROLES } from '../../common/CostCell';
import { vendorsApi } from '../../../services/api/inventory';
import { PurchaseOrderDetail } from '../../../pages/purchase/PurchaseOrderDetail';
import type { PurchaseOrder } from '../../../pages/purchase/purchaseTypes';

const getLastCost = vendorsApi.getLastCost as unknown as ReturnType<typeof vi.fn>;

const LINE: ComposerLine = {
  productId: 'prod-a',
  productName: 'Ray-Ban RX5154',
  sku: 'RB5154',
  quantity: 3,
  unitCost: 1234,
  taxRate: 12,
  costTouched: false,
  lastPaid: { unitPrice: 1234, date: '2026-10-01' },
};

function renderComposer(showPrices: boolean) {
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(
    <PurchaseOrderComposer
      mode="modal"
      vendors={[{ id: 'v-1', name: 'Luxottica India' }]}
      showPrices={showPrices}
      initialVendorId="v-1"
      initialLines={[LINE]}
      renderProductCell={({ line }) => <div>{line.productName}</div>}
      onSubmit={onSubmit}
    />,
  );
  return onSubmit;
}

const PO = {
  id: 'PO1',
  poNumber: 'PO-2026-0042',
  supplierId: 'V1',
  supplierName: 'Essilor India',
  date: '2026-10-01',
  expectedDelivery: '2026-10-10',
  status: 'DRAFT',
  items: [
    { productId: 'p1', productName: 'Ray-Ban RB2140', sku: 'RB-2140', quantity: 2, unitCost: 4321, taxRate: 18, total: 8642 },
  ],
  subtotal: 8642,
  taxAmount: 1555.56,
  total: 10197.56,
} as PurchaseOrder;

describe('the PO form for a role that may not see what we pay', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getLastCost.mockResolvedValue({ costs: { 'prod-a': { unit_price: 999, date: '2026-10-01' } } });
  });

  it('shows quantities only and never asks for the last price paid', async () => {
    renderComposer(false);
    expect(screen.queryByLabelText(/unit cost for line 1/i)).toBeNull();
    expect(screen.queryByText(/last paid/i)).toBeNull();
    expect(screen.queryByText(/line total/i)).toBeNull();
    expect(screen.queryByText(/grand total/i)).toBeNull();
    expect(screen.queryByText(/1,234/)).toBeNull();
    expect(screen.getByText(/enter the quantities only/i)).toBeTruthy();
    await new Promise((r) => setTimeout(r, 300));
    expect(getLastCost).not.toHaveBeenCalled();
  });

  it('sends no price: the server prices the line', async () => {
    const onSubmit = renderComposer(false);
    fireEvent.click(screen.getByRole('button', { name: /create as draft/i }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const item = onSubmit.mock.calls[0][0].items[0];
    expect(item).toEqual(expect.objectContaining({ product_id: 'prod-a', quantity: 3 }));
    expect('unit_price' in item).toBe(false);
  });

  it('keeps the cost box, the caption and the totals for accounts', async () => {
    const onSubmit = renderComposer(true);
    expect(screen.getByLabelText(/unit cost for line 1/i)).toBeTruthy();
    expect(screen.getByText(/grand total/i)).toBeTruthy();
    await waitFor(() => expect(getLastCost).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: /create as draft/i }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].items[0].unit_price).toBeGreaterThan(0);
  });
});

describe('the PO detail', () => {
  it.each<UserRole>(['STORE_MANAGER', 'AREA_MANAGER', 'CATALOG_MANAGER'])(
    'shows %s no price column and no totals',
    (role) => {
      auth.role = role;
      render(<PurchaseOrderDetail po={PO} onClose={() => {}} onAction={() => {}} />);
      expect(screen.getByText('Ray-Ban RB2140')).toBeTruthy();
      expect(screen.queryByText('Unit Cost')).toBeNull();
      expect(screen.queryByText('Subtotal')).toBeNull();
      expect(screen.queryByText(/4,321/)).toBeNull();
      expect(screen.queryByText(/10,197/)).toBeNull();
    },
  );

  it.each<UserRole>(COST_ROLES)('shows %s the prices', (role) => {
    auth.role = role;
    render(<PurchaseOrderDetail po={PO} onClose={() => {}} onAction={() => {}} />);
    expect(screen.getByText('Unit Cost')).toBeTruthy();
    expect(screen.getAllByText(/4,321/).length).toBeGreaterThan(0);
  });
});

describe('the one role list', () => {
  it('is the owner and the accounts roles only', () => {
    expect([...COST_ROLES].sort()).toEqual(['ACCOUNTANT', 'ADMIN', 'SUPERADMIN']);
  });
});
