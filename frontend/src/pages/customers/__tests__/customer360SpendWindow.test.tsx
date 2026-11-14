// ============================================================================
// D50 / Codex PR-1428 r1: Customer 360's spend tile may not stop silently at
// the 500-order read. When the read hits the limit the window is stated.
// ============================================================================
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-1', roles: ['STORE_MANAGER'] }, hasRole: () => false }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
const fetchCustomerOrders = vi.hoisted(() => vi.fn());
vi.mock('../../../hooks/useCustomerDues', () => ({ fetchCustomerOrders }));
vi.mock('../../../services/api', () => ({
  customerApi: {
    getCustomer: vi.fn().mockResolvedValue({
      customer_id: 'C1', name: 'Asha', phone: '9', created_at: '2024-01-01', store_id: 'S1',
    }),
    getCustomers: vi.fn(),
    updateCustomer: vi.fn(),
  },
  prescriptionApi: { getPrescriptions: vi.fn().mockResolvedValue({ prescriptions: [] }) },
}));
vi.mock('../../../services/api/loyalty', () => ({
  loyaltyApi: { getAccount: vi.fn().mockRejectedValue(new Error('not enrolled')) },
}));
vi.mock('../../../services/api/marketing', () => ({
  marketingApi: { getNpsDashboard: vi.fn().mockResolvedValue({ responses: [] }) },
}));
vi.mock('../../../components/customers/StoreCreditLedgerCard', () => ({ default: () => null }));
vi.mock('../../../components/customers/VipInterveneModal', () => ({ VipInterveneModal: () => null }));
vi.mock('../../../components/customers/CustomerTagsPanel', () => ({ CustomerTagsPanel: () => null }));
vi.mock('../../../components/clinical/PrescriptionVersionsEditor', () => ({ PrescriptionVersionsEditor: () => null }));
vi.mock('../../../components/clinical/PrescriptionHistoryModal', () => ({ PrescriptionHistoryModal: () => null }));

import { Customer360Dashboard } from '../Customer360Dashboard';
import { CUSTOMER_SPEND_CAPPED_NOTE, CUSTOMER_SPEND_LABEL } from '../../../utils/customerSpend';

const orders = (n: number) =>
  Array.from({ length: n }, (_, i) => ({
    id: `o${i}`, orderStatus: 'DELIVERED', grandTotal: 100, createdAt: '2026-11-01T10:00:00',
  }));

function open() {
  render(
    <MemoryRouter initialEntries={['/customers/C1/360']}>
      <Routes>
        <Route path="/customers/:customerId/360" element={<Customer360Dashboard />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('Customer 360 spend window', () => {
  it('states the 500-order window when the read hits it', async () => {
    fetchCustomerOrders.mockResolvedValue(orders(500));
    open();
    expect(await screen.findByText(CUSTOMER_SPEND_CAPPED_NOTE)).toBeInTheDocument();
  });

  it('says nothing extra under the limit', async () => {
    fetchCustomerOrders.mockResolvedValue(orders(2));
    open();
    expect(await screen.findByText(CUSTOMER_SPEND_LABEL)).toBeInTheDocument();
    expect(screen.queryByText(CUSTOMER_SPEND_CAPPED_NOTE)).toBeNull();
  });
});
