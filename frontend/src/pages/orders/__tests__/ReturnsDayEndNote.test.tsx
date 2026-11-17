// D95(3): after "Not confirmed. Do not pay yet. Press Return again" the
// identical re-press is answered by the server's replay of the landed record.
// The done screen's Day-End note is keyed on the SERVER's answer
// (drawer_auto_netted + refund_tenders). The replay used to leave them out,
// so the screen told the cashier to enter cash Day-End already nets as a
// paid-out (the refund deducted twice). The replay now carries them.
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
    // A bill paid in cash: one source tender, prefilled as the refund tender.
    quote: vi.fn(async () => ({
      gross_refund: 1000, net_refund: 1000, restocking_fee: 0,
      captured_tenders: { CASH: 1000 }, refundable_by_tender: { CASH: 1000 },
    })),
    list: vi.fn(async () => ({ returns: [] })),
  },
}));

import ReturnsPage from '../ReturnsPage';

async function refundInCash() {
  render(<ReturnsPage />);
  fireEvent.change(screen.getByPlaceholderText(/Order number/), { target: { value: 'Asha' } });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  fireEvent.click(await screen.findByText('BV/26-27/0001'));
  fireEvent.change((await screen.findAllByRole('combobox'))[0], { target: { value: '1' } });
  const review = await screen.findByRole('button', { name: /Review Return/ });
  await waitFor(() => expect((review as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(review);
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
  await screen.findByText('Return Processed');
}

describe('Returns done screen: the Day-End note follows the server answer', () => {
  it('a replayed cash refund the record netted reads "recorded in Day-End", never "cash paid out"', async () => {
    create.mockResolvedValueOnce({
      return_id: 'RET-1', _idempotent_replay: true, drawer_auto_netted: true,
      refund_tenders: [{ method: 'CASH', amount: 1000 }],
    });
    await refundInCash();
    expect(screen.getByText(/cash refund is now recorded in Day-End/)).toBeTruthy();
    expect(screen.queryByText(/could NOT be auto-deducted/)).toBeNull();
  });

  it('an answer without the drawer facts reads as not netted (why the replay must carry them)', async () => {
    create.mockResolvedValueOnce({ return_id: 'RET-2', _idempotent_replay: true });
    await refundInCash();
    expect(screen.getByText(/could NOT be auto-deducted/)).toBeTruthy();
  });
});
