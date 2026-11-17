// D95(4): a recorded return whose units are not placed yet (its press stopped
// after the record, or Accounts completed it in Needs completing) carries the
// button that places them -- "Update stock" on its row in Recent returns. The
// blocked-restock task names this button; before, no screen had one.
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { user_id: 'u-mgr', name: 'Mgr', roles: ['STORE_MANAGER'], activeStoreId: 'BV-DHN-02' } }),
}));
vi.mock('../../../services/api', () => ({
  orderApi: { getOrders: vi.fn(async () => ({ orders: [] })) },
  productApi: { searchProducts: vi.fn(async () => []) },
  inventoryApi: { getStockByBarcode: vi.fn(async () => { throw new Error('nf'); }) },
}));
const restock = vi.fn();
const list = vi.fn();
vi.mock('../../../services/api/returns', () => ({
  returnsApi: {
    list: (...a: unknown[]) => list(...a),
    restock: (...a: unknown[]) => restock(...a),
    quote: vi.fn(async () => ({ gross_refund: 0, net_refund: 0, restocking_fee: 0 })),
  },
}));

import ReturnsPage from '../ReturnsPage';

const row = (over: Record<string, unknown>) => ({
  return_id: 'RET-1', return_type: 'RETURN', status: 'COMPLETED', customer_name: 'Asha', order_number: 'BV/1',
  refund_amount: 900, created_at: '2026-11-17T06:00:00+00:00', ...over,
});

async function openHistory() {
  render(<ReturnsPage />);
  fireEvent.click(screen.getByRole('button', { name: /history/i }));
  await screen.findByText('RET-1');
}

describe('Recent returns: units not back in stock', () => {
  it('offers Update stock on a return whose stock step is open, and shows the server answer', async () => {
    list.mockResolvedValue({ returns: [row({ stock_pending: true }), row({ return_id: 'RET-2', stock_pending: false })] });
    restock.mockResolvedValueOnce({ return_id: 'RET-1', restock_applied: true, message: 'Restock applied' });
    await openHistory();
    expect(screen.getByText('Units not back in stock')).toBeTruthy();
    const buttons = screen.getAllByRole('button', { name: 'Update stock' });
    expect(buttons).toHaveLength(1); // only the return whose units are out
    fireEvent.click(buttons[0]);
    await waitFor(() => expect(restock).toHaveBeenCalledWith('RET-1'));
    expect((await screen.findByRole('status')).textContent).toBe('Restock applied');
    expect(list.mock.calls.length).toBeGreaterThanOrEqual(2); // the list is read again
  });

  it('offers nothing on a return whose units are back', async () => {
    list.mockResolvedValue({ returns: [row({ stock_pending: false })] });
    await openHistory();
    expect(screen.queryByRole('button', { name: 'Update stock' })).toBeNull();
    expect(screen.queryByText('Units not back in stock')).toBeNull();
  });
});
