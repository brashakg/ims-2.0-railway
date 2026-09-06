// ============================================================================
// The account page's Send Recall button points at the ADDRESS, not the tab
// ============================================================================
// Asserting the router's landing place would NOT discriminate here: the
// legacyTabRedirect shim forwards `/customers?tab=recalls` to the same screen,
// so a reverted button would still end up on recalls and a location assertion
// would stay green. This asserts the argument the button hands to navigate(),
// which is the thing that changed — revert it to '/customers?tab=recalls' and
// this file fails by name.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

const navigateSpy = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const orig = await importOriginal<typeof import('react-router-dom')>();
  return { ...orig, useNavigate: () => navigateSpy };
});

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', name: 'Meena', activeStoreId: 'BV-BOK-01', roles: ['SUPERADMIN'] },
    hasRole: () => true,
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

const { HOLDER } = vi.hoisted(() => ({
  HOLDER: {
    customer_id: 'cust-holder',
    name: 'Meena Devi',
    mobile: '9876500001',
    phone: '9876500001',
    customer_type: 'B2C',
    customerType: 'B2C',
    patients: [],
  },
}));

vi.mock('../../../services/api', () => ({
  customerApi: { getCustomers: vi.fn().mockResolvedValue({ customers: [HOLDER] }) },
  prescriptionApi: { getPrescriptions: vi.fn().mockResolvedValue({ prescriptions: [] }) },
  orderApi: { getOrders: vi.fn().mockResolvedValue({ orders: [] }) },
}));
vi.mock('../../../services/api/loyalty', () => ({
  loyaltyApi: { getAccount: vi.fn().mockResolvedValue({ account: null }) },
}));

// Leaf screens the page mounts; none is what this file is about.
vi.mock('../../../components/customers/AddCustomerModal', () => ({ AddCustomerModal: () => null }));
vi.mock('../../../components/common/AutoSearch', () => ({ AutoSearch: () => null }));
vi.mock('../../../components/common/Pagination', () => ({ Pagination: () => null }));
vi.mock('../../../components/crm/CustomerPurchaseHistory', () => ({
  CustomerPurchaseHistory: () => null,
}));
vi.mock('../../../components/crm/PrescriptionQRCode', () => ({ PrescriptionQRCode: () => null }));
vi.mock('../../../components/pos/PrescriptionForm', () => ({ PrescriptionForm: () => null }));

import { CustomersPage } from '../CustomersPage';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('CustomersPage quick actions', () => {
  it('Send Recall navigates to /customers/recalls', async () => {
    render(
      <MemoryRouter>
        <CustomersPage />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole('button', { name: /Meena Devi/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Send Recall' }));
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith('/customers/recalls'));
  });

  it('?tab=recalls no longer renders recalls INSIDE the page', async () => {
    // The in-page branch is DELETED, not kept alongside the route: one
    // RecallManager, one address. Mounted at the old query string, this page
    // is now just the customer list (the shim in routes/customerRoutes.tsx
    // forwards before it ever gets here).
    render(
      <MemoryRouter initialEntries={['/customers?tab=recalls']}>
        <CustomersPage />
      </MemoryRouter>,
    );
    expect(await screen.findByRole('button', { name: /Meena Devi/ })).toBeInTheDocument();
    expect(screen.queryByText(/Customer Recalls & Reminders/)).not.toBeInTheDocument();
  });
});
