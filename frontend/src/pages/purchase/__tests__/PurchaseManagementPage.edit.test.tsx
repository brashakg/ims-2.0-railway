// ============================================================================
// IMS 2.0 - Purchase page: the Edit pencil must open an EDITOR
// ============================================================================
// Owner report (2026-08-26): "edit vendor button is not working".
//
// SupplierPanel.edit.test.tsx proves the pencil calls onEdit. That is only half
// the wire. The other half -- the page passing the clicked vendor INTO the
// modal -- was pinned by nothing: deleting `supplier={editingSupplier}` from
// PurchaseManagementPage left `tsc -b` clean and every purchase test green,
// while the pencil silently opened a blank "Add Supplier" form whose Save would
// CREATE A SECOND VENDOR instead of editing the one on the card. That is the
// owner's exact complaint, regressing in silence.
//
// So this test drives the real page: click Suppliers, click the pencil, and
// assert the modal that opens is the EDITOR, carrying that vendor's values.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const toastMock = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(),
}));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Admin', roles: ['ADMIN'], activeStoreId: 'BV-BOK-01' },
    hasRole: () => true,
    hasPermission: () => true,
  }),
}));

vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));

vi.mock('../../../hooks/useStorePrintInfo', () => ({
  useStorePrintInfo: () => ({
    storeName: 'BV Bokaro', address: '', city: 'Bokaro', state: 'Jharkhand',
    pincode: '', stateCode: '20',
  }),
}));

vi.mock('../../../services/api/entities', () => ({
  entitiesApi: {
    meta: vi.fn().mockResolvedValue({
      state_codes: [{ code: '20', name: 'Jharkhand' }, { code: '27', name: 'Maharashtra' }],
      entity_types: [],
    }),
  },
}));

// The raw backend vendor doc, as GET /vendors really returns it -- the page's
// own mapVendorToSupplier has to survive this shape too.
const VENDOR = {
  vendor_id: 'v1',
  legal_name: 'Universal Optics Pvt Ltd',
  trade_name: 'Universal Optics',
  vendor_code: 'SUP004',
  contact_person: 'Rakesh Sinha',
  mobile: '9000000000',
  email: 'r@universal.in',
  address: '12 Main Road',
  city: 'Ranchi',
  state: 'Jharkhand',
  state_code: '20',
  gstin: '20AABCU9603R1Z1',
  credit_days: 30,
  credit_limit: 250000,
};

const getVendors = vi.fn();
const getPurchaseOrders = vi.fn();

vi.mock('../../../services/api', () => ({
  vendorsApi: {
    getVendors: (...a: unknown[]) => getVendors(...a),
    getPurchaseOrders: (...a: unknown[]) => getPurchaseOrders(...a),
    createVendor: vi.fn(),
    updateVendor: vi.fn(),
    generatePortalToken: vi.fn(),
    sendPurchaseOrder: vi.fn(),
    cancelPurchaseOrder: vi.fn(),
    getGRNs: vi.fn(),
    getVarianceReport: vi.fn(),
    dismissVariance: vi.fn(),
    getLastCost: vi.fn(),
    createPurchaseOrder: vi.fn(),
  },
  productApi: { getProducts: vi.fn() },
}));

import { MemoryRouter } from 'react-router-dom';
import { PurchaseManagementPage } from '../PurchaseManagementPage';

beforeEach(() => {
  vi.clearAllMocks();
  getVendors.mockResolvedValue({ vendors: [VENDOR] });
  getPurchaseOrders.mockResolvedValue({ purchase_orders: [] });
});

async function openSuppliersTab() {
  render(
    <MemoryRouter>
      <PurchaseManagementPage />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByText('Suppliers'));
  return screen.findByText('Universal Optics');
}

describe('Purchase page - Edit pencil opens the supplier EDITOR', () => {
  it('opens "Edit Supplier" pre-filled with the vendor that was clicked', async () => {
    await openSuppliersTab();

    fireEvent.click(screen.getByRole('button', { name: /edit universal optics/i }));

    // The editor, not the Add form. If the page ever stops handing the clicked
    // vendor to the modal, this reads "Add Supplier" and Save creates a second
    // vendor row for a supplier that already exists.
    expect(await screen.findByText('Edit Supplier')).toBeInTheDocument();
    expect(screen.queryByText('Add Supplier')).not.toBeInTheDocument();

    // ...and it is THAT vendor: the fields carry his values, not blanks.
    await waitFor(() => {
      expect((screen.getByLabelText(/company name/i) as HTMLInputElement).value)
        .toBe('Universal Optics');
    });
    expect((screen.getByLabelText(/supplier code/i) as HTMLInputElement).value).toBe('SUP004');
    expect((screen.getByLabelText(/contact person/i) as HTMLInputElement).value).toBe('Rakesh Sinha');
    expect((screen.getByLabelText(/gst number/i) as HTMLInputElement).value).toBe('20AABCU9603R1Z1');
  });
});
