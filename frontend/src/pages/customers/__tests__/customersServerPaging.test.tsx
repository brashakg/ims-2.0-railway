// ============================================================================
// Customers list: the page shown IS the server's page (audit R31)
// ============================================================================
// The screen asked the server for skip/limit, then sliced the answer again
// and counted pages from the 50 rows it got back, so page 2 could never be
// reached and would have been empty.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

const { getCustomers } = vi.hoisted(() => ({ getCustomers: vi.fn() }));

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', name: 'Meena', activeStoreId: 'BV-BOK-01', roles: ['SUPERADMIN'] },
    hasRole: () => true,
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../../services/api', () => ({
  customerApi: { getCustomers },
  prescriptionApi: { getPrescriptions: vi.fn().mockResolvedValue({ prescriptions: [] }) },
  orderApi: { getOrders: vi.fn().mockResolvedValue({ orders: [] }) },
}));
vi.mock('../../../services/api/loyalty', () => ({
  loyaltyApi: { getAccount: vi.fn().mockResolvedValue({ account: null }) },
}));
vi.mock('../../../components/customers/AddCustomerModal', () => ({ AddCustomerModal: () => null }));
vi.mock('../../../components/common/AutoSearch', () => ({ AutoSearch: () => null }));
vi.mock('../../../components/common/Pagination', () => ({
  Pagination: ({ totalItems, onPageChange }: { totalItems: number; onPageChange: (p: number) => void }) => (
    <div>
      <span data-testid="total">{totalItems}</span>
      <button onClick={() => onPageChange(2)}>page 2</button>
    </div>
  ),
}));
vi.mock('../../../components/crm/CustomerPurchaseHistory', () => ({ CustomerPurchaseHistory: () => null }));
vi.mock('../../../components/crm/PrescriptionQRCode', () => ({ PrescriptionQRCode: () => null }));
vi.mock('../../../components/pos/PrescriptionForm', () => ({ PrescriptionForm: () => null }));

import { CustomersPage } from '../CustomersPage';

const person = (n: number) => ({
  customer_id: `c-${n}`,
  name: `Audit Person ${String(n).padStart(4, '0')}`,
  mobile: `98765${String(n).padStart(5, '0')}`,
  customer_type: 'B2C',
  customerType: 'B2C',
  patients: [],
});

beforeEach(() => {
  getCustomers.mockReset();
  getCustomers.mockImplementation(async ({ skip = 0, limit = 50 }: { skip?: number; limit?: number }) => {
    const rows = Array.from({ length: limit }, (_, i) => person(skip + i));
    return { customers: rows, data: rows, pagination: { total: 230, page: skip / limit + 1, page_size: limit } };
  });
});

describe('Customers list paging', () => {
  it('counts pages from the server total and shows the server page 2 rows', async () => {
    render(
      <MemoryRouter>
        <CustomersPage />
      </MemoryRouter>,
    );
    expect(await screen.findByRole('button', { name: /Audit Person 0000/ })).toBeInTheDocument();
    expect(screen.getByTestId('total').textContent).toBe('230');

    fireEvent.click(screen.getByText('page 2'));
    await waitFor(() => expect(getCustomers).toHaveBeenLastCalledWith(expect.objectContaining({ skip: 50, limit: 50 })));
    expect(await screen.findByRole('button', { name: /Audit Person 0050/ })).toBeInTheDocument();
  });
});
