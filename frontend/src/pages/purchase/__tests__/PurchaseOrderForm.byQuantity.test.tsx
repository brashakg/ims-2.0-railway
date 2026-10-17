// ============================================================================
// Create Purchase Order: a manager orders by quantity (owner ruling 2026-10-12)
// ============================================================================
// What we pay is for COST_ROLES only. The REAL form (only the network is
// faked) with a role check that means it: an area or store manager picks the
// product and the quantity, and sees no cost box, no last price paid and no
// catalogue cost; an accountant still gets the box, filled from the last price.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

const auth = vi.hoisted(() => ({ role: 'STORE_MANAGER' }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Someone', roles: [auth.role], activeStoreId: 'BV-DHN-02' },
    hasRole: (r: string | string[]) => (Array.isArray(r) ? r : [r]).includes(auth.role),
    hasPermission: () => true,
  }),
}));

const getProducts = vi.hoisted(() => vi.fn());
vi.mock('../../../services/api', () => ({
  vendorsApi: { createPurchaseOrder: vi.fn() },
  productApi: { getProducts },
}));

const getLastCost = vi.hoisted(() => vi.fn());
vi.mock('../../../services/api/inventory', () => ({
  vendorsApi: {
    getLastCost,
    getPoGstHeads: vi.fn().mockResolvedValue({ shop_gstin: '', heads: {} }),
  },
}));

vi.mock('../../../services/api/stores', () => ({
  storeApi: { getStore: vi.fn().mockResolvedValue({ gstin: '20AABCU9603R1Z1', state: 'Jharkhand' }) },
}));

vi.mock('../../../services/api/entities', () => ({
  entitiesApi: { meta: vi.fn().mockResolvedValue({ state_codes: [], entity_types: [] }) },
}));

import { PurchaseOrderForm } from '../PurchaseOrderForm';
import type { Supplier } from '../purchaseTypes';

const MUMBAI: Supplier = {
  id: 'v-mum', name: 'Mumbai Lens House', code: 'V-MUM', contactPerson: '', phone: '',
  email: '', address: '', city: '', state: 'Jharkhand', stateCode: '20', gstNumber: '',
  paymentTerms: 30, creditLimit: 0, currentOutstanding: 0, rating: 0, totalPurchases: 0,
  lastPurchaseDate: '',
  performance: { onTimeDelivery: 0, qualityScore: 0, priceCompetitiveness: 0 },
};

const CARRERA = {
  product_id: 'P-CAR', sku: 'FR-CAR-CA8895-C1', brand: 'Carrera', model: 'CA 8895',
  category: 'FRAME', mrp: 8990, cost_price: 3200, gst_rate: 5, hsn_code: '900311',
};

async function openAndPick() {
  render(
    <PurchaseOrderForm suppliers={[MUMBAI]} existingPOCount={0} onClose={vi.fn()} onCreated={() => {}} />,
  );
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
  fireEvent.change(screen.getByLabelText('Vendor'), { target: { value: 'v-mum' } });
  fireEvent.change(screen.getByPlaceholderText('Search catalogued product...'), {
    target: { value: '8895' },
  });
  fireEvent.click(await screen.findByRole('button', { name: /Carrera CA 8895/ }, { timeout: 3000 }));
}

beforeEach(() => {
  vi.clearAllMocks();
  getProducts.mockResolvedValue({ products: [CARRERA] });
  getLastCost.mockResolvedValue({
    costs: { 'P-CAR': { unit_price: 3100, date: '2026-09-17T10:00:00', po_number: 'PO-2' } },
  });
});

describe('Create Purchase Order - what we pay is for accounts only', () => {
  it.each([['STORE_MANAGER'], ['AREA_MANAGER']])('%s: quantity only, no price anywhere', async (role) => {
    auth.role = role;
    await openAndPick();
    expect(screen.getAllByText(/Carrera/).length).toBeGreaterThan(0); // picked
    expect(screen.queryByLabelText('Unit cost for line 1')).toBeNull();
    expect(getLastCost).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toMatch(/3,?100|3,?200|last paid/);
  });

  it.each([['ACCOUNTANT'], ['ADMIN']])('%s: the cost box, from the last price paid', async (role) => {
    auth.role = role;
    await openAndPick();
    const cost = screen.getByLabelText('Unit cost for line 1') as HTMLInputElement;
    await waitFor(() => expect(cost.value).toBe('3100'));
  });
});
