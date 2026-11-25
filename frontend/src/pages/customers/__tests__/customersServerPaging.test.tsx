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

  // Codex PR-1427 round 1 (P2) / D52: the B2B chip narrowed only the 50 rows
  // on screen and showed that count as the total, so B2B customers beyond the
  // first page were unreachable ("No customers yet", no next page).
  it('B2B chip asks the server for B2B across all pages, from page 1, with the server total', async () => {
    getCustomers.mockImplementation(async ({ skip = 0, limit = 50, customer_type }: { skip?: number; limit?: number; customer_type?: string }) => {
      if (customer_type === 'B2B') {
        const rows = Array.from({ length: limit }, (_, i) => ({ ...person(9000 + skip + i), name: `Firm ${String(skip + i).padStart(4, '0')}`, customer_type: 'B2B', customerType: 'B2B' }));
        return { customers: rows, data: rows, pagination: { total: 120, page: skip / limit + 1, page_size: limit } };
      }
      const rows = Array.from({ length: limit }, (_, i) => person(skip + i));
      return { customers: rows, data: rows, pagination: { total: 230, page: skip / limit + 1, page_size: limit } };
    });
    render(
      <MemoryRouter>
        <CustomersPage />
      </MemoryRouter>,
    );
    expect(await screen.findByRole('button', { name: /Audit Person 0000/ })).toBeInTheDocument();
    fireEvent.click(screen.getByText('page 2'));
    expect(await screen.findByRole('button', { name: /Audit Person 0050/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'B2B' }));
    await waitFor(() =>
      expect(getCustomers).toHaveBeenLastCalledWith(expect.objectContaining({ customer_type: 'B2B', skip: 0, limit: 50 })),
    );
    expect(await screen.findByRole('button', { name: /Firm 0000/ })).toBeInTheDocument();
    expect(screen.getByTestId('total').textContent).toBe('120');

    fireEvent.click(screen.getByText('page 2'));
    await waitFor(() =>
      expect(getCustomers).toHaveBeenLastCalledWith(expect.objectContaining({ customer_type: 'B2B', skip: 50 })),
    );
    expect(await screen.findByRole('button', { name: /Firm 0050/ })).toBeInTheDocument();
  });

  it('an older page answer landing after the chip change never overwrites the B2B list', async () => {
    let releaseAll: (() => void) | undefined;
    getCustomers.mockImplementation(({ skip = 0, limit = 50, customer_type }: { skip?: number; limit?: number; customer_type?: string }) => {
      if (customer_type === 'B2B') {
        const rows = [{ ...person(9001), name: 'Firm Only', customer_type: 'B2B', customerType: 'B2B' }];
        return Promise.resolve({ customers: rows, data: rows, pagination: { total: 1, page: 1, page_size: limit } });
      }
      const rows = Array.from({ length: limit }, (_, i) => person(skip + i));
      const answer = { customers: rows, data: rows, pagination: { total: 230, page: skip / limit + 1, page_size: limit } };
      if (skip === 50) return new Promise(res => { releaseAll = () => res(answer); });
      return Promise.resolve(answer);
    });
    render(
      <MemoryRouter>
        <CustomersPage />
      </MemoryRouter>,
    );
    expect(await screen.findByRole('button', { name: /Audit Person 0000/ })).toBeInTheDocument();
    fireEvent.click(screen.getByText('page 2')); // held in flight
    await waitFor(() => expect(releaseAll).toBeDefined());
    fireEvent.click(screen.getByRole('button', { name: 'B2B' }));
    expect(await screen.findByRole('button', { name: /Firm Only/ })).toBeInTheDocument();
    releaseAll!();
    await new Promise(r => setTimeout(r, 20));
    expect(screen.getByRole('button', { name: /Firm Only/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Audit Person 0050/ })).toBeNull();
    expect(screen.getByTestId('total').textContent).toBe('1');
  });
});
