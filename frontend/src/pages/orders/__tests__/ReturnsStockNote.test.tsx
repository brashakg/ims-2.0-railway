// D92 round 12: when the server could not put the returned units back in
// stock it says so in `stock_note` on its 201 answer -- the done screen shows
// that note (it used to show only "Credit Note Issued").
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { user_id: 'u-cashier', name: 'Cash', roles: ['CASHIER'], activeStoreId: 'BV-DHN-02' } }),
}));
const ORDERS = [{ id: 'ORD-1', orderNumber: 'BV/26-27/0001', customerName: 'Asha', customerPhone: '9800000001',
  items: [{ id: 'L1', productName: 'Frame', quantity: 1, unitPrice: 1000, taxable_value: 952.38, tax_amount: 47.62, gst_rate: 5 }] }];
vi.mock('../../../services/api', () => ({
  orderApi: { getOrders: vi.fn(async () => ({ orders: ORDERS })) },
  productApi: { searchProducts: vi.fn(async () => []) },
  inventoryApi: { getStockByBarcode: vi.fn(async () => { throw new Error('nf'); }) },
}));
const create = vi.fn();
vi.mock('../../../services/api/returns', () => ({
  returnsApi: {
    create: (...a: unknown[]) => create(...a),
    quote: vi.fn(async () => ({ gross_refund: 1000, net_refund: 1000, restocking_fee: 0 })),
    list: vi.fn(async () => ({ returns: [] })),
  },
}));

import ReturnsPage from '../ReturnsPage';

const NOTE = 'Its units were NOT put back in stock (the bill could not be checked just now): '
  + 'tell the store manager - do not add them by hand';

async function issueCreditNote() {
  render(<ReturnsPage />);
  fireEvent.click(screen.getByText('Store Credit').closest('button')!);
  fireEvent.change(screen.getByPlaceholderText(/Order number/), { target: { value: 'Asha' } });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  fireEvent.click(await screen.findByText('BV/26-27/0001'));
  fireEvent.change((await screen.findAllByRole('combobox'))[0], { target: { value: '1' } });
  const review = await screen.findByRole('button', { name: /Review Credit Note/ });
  await waitFor(() => expect((review as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(review);
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
  await screen.findByText('Credit Note Issued');
}

describe('Returns done screen: units not put back', () => {
  it('shows the server stock_note', async () => {
    create.mockResolvedValueOnce({ return_id: 'RET-1', credit_amount: 1000, restock_applied: false, stock_note: NOTE });
    await issueCreditNote();
    expect(screen.getByRole('alert').textContent).toBe(NOTE);
  });

  it('shows nothing extra when the units went back', async () => {
    create.mockResolvedValueOnce({ return_id: 'RET-2', credit_amount: 1000, restock_applied: true, stock_note: null });
    await issueCreditNote();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
