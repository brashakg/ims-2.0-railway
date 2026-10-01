// ============================================================================
// IMS 2.0 - Create Purchase Order while the supplier list loads (audit F85)
// ============================================================================
// The vendor dropdown read 'Select a vendor...' with no vendors in it while
// the list was still on its way -- the page never handed the form its loading
// flag -- so a manager concluded no suppliers existed and went to add one.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Manager', roles: ['STORE_MANAGER'], activeStoreId: 'BV-DHN-02' },
    hasRole: () => true,
    hasPermission: () => true,
  }),
}));

vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));

const getVendors = vi.fn();
const getPurchaseOrders = vi.fn();

vi.mock('../../../services/api', () => ({
  vendorsApi: {
    getVendors: (...a: unknown[]) => getVendors(...a),
    getPurchaseOrders: (...a: unknown[]) => getPurchaseOrders(...a),
    createPurchaseOrder: vi.fn(),
  },
  productApi: { getProducts: vi.fn() },
}));

vi.mock('../../../services/api/inventory', () => ({
  vendorsApi: { getLastCost: vi.fn().mockResolvedValue({ costs: {} }) },
}));

vi.mock('../../../services/api/stores', () => ({
  storeApi: { getStore: vi.fn().mockResolvedValue(null) },
}));

vi.mock('../../../services/api/entities', () => ({
  entitiesApi: { meta: vi.fn().mockResolvedValue({ state_codes: [], entity_types: [] }) },
}));

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { PurchaseOrdersSection } from '../PurchaseOrdersSection';

beforeEach(() => {
  vi.clearAllMocks();
  getPurchaseOrders.mockResolvedValue({ purchase_orders: [] });
});

function openNewPO() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={['/purchase/orders?new=1']}>
        <PurchaseOrdersSection />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Create Purchase Order - vendor dropdown while suppliers load (F85)', () => {
  it('says the vendors are loading, not "Select a vendor"', async () => {
    getVendors.mockReturnValue(new Promise(() => {})); // still on its way
    openNewPO();
    const vendor = (await screen.findByLabelText('Vendor', {}, { timeout: 15000 })) as HTMLSelectElement;
    expect(vendor.options[0].textContent).toBe('Loading vendors…');
    expect(vendor).toBeDisabled();
  });

  it('offers the suppliers once they arrive', async () => {
    getVendors.mockResolvedValue({
      vendors: [{ vendor_id: 'v-mum', legal_name: 'Mumbai Lens House', vendor_code: 'MLH', is_active: true }],
    });
    openNewPO();
    expect(await screen.findByRole('option', { name: /Mumbai Lens House/ }, { timeout: 15000 })).toBeInTheDocument();
    const vendor = screen.getByLabelText('Vendor') as HTMLSelectElement;
    expect(vendor.options[0].textContent).toBe('Select a vendor…');
    expect(vendor).not.toBeDisabled();
  });
});
