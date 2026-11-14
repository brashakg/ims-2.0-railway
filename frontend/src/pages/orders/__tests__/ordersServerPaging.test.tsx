// ============================================================================
// Orders list: the page shown IS the server's page (audit R30)
// ============================================================================
// The screen asked the server for skip/limit, then sliced the answer AGAIN
// and counted pages from the array it got back. Once the server pages
// properly, page 2 would be 50 rows sliced at [50..100] = nothing, and the
// page count would be 1. Both are pinned here through the real component.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

const { getOrders } = vi.hoisted(() => ({ getOrders: vi.fn() }));

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', name: 'Asha', activeStoreId: 'BV-BOK-01', roles: ['ADMIN'], activeRole: 'ADMIN' },
    hasRole: () => true,
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../../services/api', () => ({ orderApi: { getOrders, getOrder: vi.fn() } }));
vi.mock('../../../services/api/marketing', () => ({ marketingApi: {} }));
vi.mock('../../../services/api/printDocuments', () => ({ printDocumentsApi: {} }));
vi.mock('../../../components/common/Pagination', () => ({
  Pagination: ({ totalItems, onPageChange }: { totalItems: number; onPageChange: (p: number) => void }) => (
    <div>
      <span data-testid="total">{totalItems}</span>
      <button onClick={() => onPageChange(2)}>page 2</button>
    </div>
  ),
}));

import { OrdersPage } from '../OrdersPage';

const row = (n: number) => ({
  id: `o-${n}`,
  orderNumber: `BV-ORD-${String(n).padStart(4, '0')}`,
  customerName: `Customer ${n}`,
  orderStatus: 'CONFIRMED',
  paymentStatus: 'PAID',
  grandTotal: 1000,
  amountPaid: 1000,
  balanceDue: 0,
  items: [],
  createdAt: '2026-11-10T10:00:00Z',
});

beforeEach(() => {
  getOrders.mockReset();
  getOrders.mockImplementation(async ({ skip = 0, limit = 50 }: { skip?: number; limit?: number }) => {
    const rows = Array.from({ length: limit }, (_, i) => row(skip + i));
    return { orders: rows, data: rows, pagination: { total: 620, page: skip / limit + 1, page_size: limit } };
  });
});

describe('Orders list paging', () => {
  it('counts pages from the server total and shows the server page 2 rows', async () => {
    render(
      <MemoryRouter>
        <OrdersPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText('BV-ORD-0000')).toBeInTheDocument();
    expect(screen.getByTestId('total').textContent).toBe('620');

    fireEvent.click(screen.getByText('page 2'));
    await waitFor(() => expect(getOrders).toHaveBeenLastCalledWith(expect.objectContaining({ skip: 50, limit: 50 })));
    expect(await screen.findByText('BV-ORD-0050')).toBeInTheDocument();
    expect(screen.queryByText('BV-ORD-0000')).not.toBeInTheDocument();
  });
});
