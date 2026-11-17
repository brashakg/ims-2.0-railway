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

  // D122(3): a step that stopped part way reads as any unfinished step -- IMS
  // finishes it (this button, or its hourly sweep); nobody checks or adds by hand.
  it('shows every unfinished stock step the same way, with Update stock', async () => {
    list.mockResolvedValue({ returns: [
      row({ stock_pending: true, restock_in_progress: true }),
      row({ return_id: 'RET-2', stock_pending: true }),
    ] });
    await openHistory();
    expect(screen.getAllByText('Units not back in stock')).toHaveLength(2);
    expect(screen.queryByText(/by hand/i)).toBeNull();
    expect(screen.getAllByRole('button', { name: 'Update stock' })).toHaveLength(2);
  });

  // D7: a record completed in Needs completing is retried there (Accounts / Admin).
  it('points a record completed in Needs completing to its own Retry stock step', async () => {
    list.mockResolvedValue({ returns: [row({ stock_pending: true, recovered: { by: 'U9' } })] });
    await openHistory();
    expect(screen.getByText('Units not back in stock')).toBeTruthy();
    expect(screen.getByText('Retry stock step is in Needs completing (Accounts / Admin)')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Update stock' })).toBeNull();
  });

  // D109(1): a read that failed is never an empty history.
  it('says the history could not be loaded and retries', async () => {
    list.mockReset();
    list.mockRejectedValueOnce(new Error('503'));
    render(<ReturnsPage />);
    fireEvent.click(screen.getByRole('button', { name: /history/i }));
    expect((await screen.findByRole('alert')).textContent).toContain('Could not load returns.');
    expect(screen.queryByText('No returns recorded yet.')).toBeNull();
    list.mockResolvedValue({ returns: [row({})] });
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await screen.findByText('RET-1');
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
