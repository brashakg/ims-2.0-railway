// ============================================================================
// A new order for one product (?product=<id>) - stock redesign S5
// ============================================================================
// The Buy Desk is retired into the Reorder list; its "order this product"
// links (catalogue drawer "Order stock", Quick Add "Order this now", old
// /catalog/buy-desk?add_product= bookmarks) open the ONE PO form with that
// product already on the first line.

import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Someone', roles: ['STORE_MANAGER'], activeStoreId: 'BV-DHN-02' },
    hasRole: (r: string | string[]) => (Array.isArray(r) ? r : [r]).includes('STORE_MANAGER'),
    hasPermission: () => true,
  }),
}));

const getProduct = vi.hoisted(() => vi.fn());
vi.mock('../../../services/api', () => ({
  vendorsApi: { createPurchaseOrder: vi.fn() },
  productApi: { getProducts: vi.fn().mockResolvedValue({ products: [] }), getProduct },
}));
vi.mock('../../../services/api/inventory', () => ({
  vendorsApi: { getLastCost: vi.fn(), getPoGstHeads: vi.fn().mockResolvedValue({ shop_gstin: '', heads: {} }) },
}));
vi.mock('../../../services/api/stores', () => ({
  storeApi: { getStore: vi.fn().mockResolvedValue({ gstin: '', state: 'Jharkhand' }) },
}));
vi.mock('../../../services/api/entities', () => ({
  entitiesApi: { meta: vi.fn().mockResolvedValue({ state_codes: [], entity_types: [] }) },
}));

import { PurchaseOrderForm } from '../PurchaseOrderForm';
import { buyDeskForward } from '../../inventory/legacyTabRedirect';

const CARRERA = {
  product_id: 'P-CAR', sku: 'FR-CAR-CA8895-C1', brand: 'Carrera', model: 'CA 8895',
  category: 'FRAME', mrp: 8990, gst_rate: 5, hsn_code: '900311',
};

describe('a new order opened for one product', () => {
  it('reads the product and starts the order with it', async () => {
    getProduct.mockResolvedValueOnce(CARRERA);
    render(<PurchaseOrderForm suppliers={[]} existingPOCount={0} prefillProductId="P-CAR" onClose={() => {}} onCreated={() => {}} />);
    await waitFor(() => expect(getProduct).toHaveBeenCalledWith('P-CAR'));
    expect(await screen.findByText(/Carrera CA 8895/)).toBeInTheDocument();
  });

  it('a product that cannot be read leaves an empty order and says so', async () => {
    getProduct.mockRejectedValueOnce(new Error('404'));
    render(<PurchaseOrderForm suppliers={[]} existingPOCount={0} prefillProductId="P-GONE" onClose={() => {}} onCreated={() => {}} />);
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(screen.queryByText(/Carrera/)).toBeNull();
  });
});

describe('the retired Buy Desk address forwards (S5)', () => {
  it('to the Reorder list, or to a new order for the product it named', () => {
    expect(buyDeskForward('')).toBe('/inventory/reorders');
    expect(buyDeskForward('?add_product=P 1')).toBe('/purchase/orders?new=1&product=P%201');
  });
});
