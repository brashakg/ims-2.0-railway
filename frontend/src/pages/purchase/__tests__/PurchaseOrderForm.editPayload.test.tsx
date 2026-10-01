// ============================================================================
// IMS 2.0 - Edit a draft: the save carries each line's stored rate and HSN, and
// a cleared date is sent as null (a PUT keeps a field it does not carry).
// ============================================================================

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Admin', roles: ['ADMIN'], activeStoreId: 'BV-BOK-01' },
    hasRole: () => true,
    hasPermission: () => true,
  }),
}));

const updatePurchaseOrder = vi.hoisted(() => vi.fn());
vi.mock('../../../services/api', () => ({
  vendorsApi: { updatePurchaseOrder, createPurchaseOrder: vi.fn() },
  productApi: { getProducts: vi.fn().mockResolvedValue({ products: [] }) },
}));
vi.mock('../../../services/api/inventory', () => ({
  vendorsApi: { getLastCost: vi.fn().mockResolvedValue({ costs: {} }) },
}));
vi.mock('../../../services/api/stores', () => ({
  storeApi: { getStore: vi.fn().mockResolvedValue({ gstin: '20AABCU9603R1Z1', state: 'Jharkhand' }) },
}));
vi.mock('../../../services/api/entities', () => ({
  entitiesApi: { meta: vi.fn().mockResolvedValue({ state_codes: [], entity_types: [] }) },
}));

import { PurchaseOrderForm } from '../PurchaseOrderForm';
import { mapPOtoPurchaseOrder } from '../purchaseMappers';
import type { Supplier } from '../purchaseTypes';

const vendor = {
  id: 'v1', name: 'Universal Optics', code: 'SUP004', contactPerson: 'R', phone: '9000000000',
  email: 'r@u.in', address: 'x', city: 'Ranchi', state: '', stateCode: undefined,
  gstNumber: '20AABCU9603R1ZF', paymentTerms: 30, creditLimit: 0, currentOutstanding: 0,
  rating: 4, totalPurchases: 0, lastPurchaseDate: '',
  performance: { onTimeDelivery: 90, qualityScore: 90, priceCompetitiveness: 90 },
} as Supplier;

// Stored by API with a typed-in rate and HSN the catalogue would not give.
const stored = (over: Record<string, unknown> = {}) =>
  mapPOtoPurchaseOrder({
    po_id: 'PO1', po_number: 'PO/1', vendor_id: 'v1', status: 'DRAFT',
    expected_date: '2030-01-01', notes: 'handle with care',
    items: [
      { product_id: 'P1', product_name: 'Carrera', sku: 'P1', quantity: 2, unit_price: 1000,
        tax_rate: 12, hsn: '9004' },
      { product_id: 'P2', product_name: 'Ray-Ban', sku: 'P2', quantity: 1, unit_price: 500,
        tax_rate: 0, gst_unresolved: true },
    ],
    ...over,
  });

async function save(po: ReturnType<typeof stored>) {
  updatePurchaseOrder.mockResolvedValue({ po_id: 'PO1', status: 'DRAFT', items: [] });
  render(
    <PurchaseOrderForm suppliers={[vendor]} existingPOCount={1} editing={po}
      onClose={() => {}} onCreated={() => {}} />,
  );
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /save changes/i })); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  expect(updatePurchaseOrder).toHaveBeenCalledTimes(1);
  return updatePurchaseOrder.mock.calls[0][1];
}

describe('saving an edit to a draft', () => {
  beforeEach(() => updatePurchaseOrder.mockReset());

  it('sends each settled line its stored rate and HSN, and pins nothing for an unsettled one', async () => {
    const body = await save(stored());
    const [p1, p2] = body.items;
    expect([p1.gst_rate, p1.hsn]).toEqual([12, '9004']);
    expect(p2.gst_rate).toBeUndefined();
    expect(p2.hsn).toBeUndefined();
  });

  it('sends a cleared delivery date and notes as null, never as an omitted field', async () => {
    const body = await save(stored({ expected_date: '', notes: '' }));
    expect(body.expected_date).toBeNull();
    expect(body.notes).toBeNull();
  });
});
