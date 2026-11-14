// ============================================================================
// D50 / Codex PR-1428 r1: the Customers page spend cards may not stop silently
// at the 500-order read. When the read hits the limit the window is stated.
// ============================================================================
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

const getOrders = vi.hoisted(() => vi.fn());
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
  orderApi: { getOrders },
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
import { CUSTOMER_SPEND_CAPPED_NOTE } from '../../../utils/customerSpend';

const orders = (n: number) =>
  Array.from({ length: n }, (_, i) => ({
    id: `o${i}`, orderNumber: `BV-${i}`, orderStatus: 'DELIVERED', grandTotal: 100,
    createdAt: '2026-11-01T10:00:00', items: [],
  }));

async function openHolder() {
  render(
    <MemoryRouter>
      <CustomersPage />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole('button', { name: /Meena Devi/ }));
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('CustomersPage spend cards window', () => {
  it('states the 500-order window when the read hits it', async () => {
    getOrders.mockResolvedValue({ orders: orders(500) });
    await openHolder();
    expect(await screen.findByText(CUSTOMER_SPEND_CAPPED_NOTE)).toBeInTheDocument();
  });

  it('says nothing extra under the limit', async () => {
    getOrders.mockResolvedValue({ orders: orders(3) });
    await openHolder();
    await waitFor(() => expect(getOrders).toHaveBeenCalled());
    expect(await screen.findByText('Total ordered (gross)')).toBeInTheDocument();
    expect(screen.queryByText(CUSTOMER_SPEND_CAPPED_NOTE)).toBeNull();
  });
});
