// The done screen's amount is the money the SERVER recorded, never the
// screen's own estimate. The live quote is dropped once the step leaves
// 'select', and the big figure used to fall back to the local estimate (the
// bill's gross, points included): on a Rs 1,000 bill paid Rs 900 cash + Rs 100
// points it said "Rs 1,000 Store credit added" while Rs 900 was credited and
// Rs 100 went back as points.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { user_id: 'u-sales', name: 'Sales', roles: ['SALES_STAFF'], activeStoreId: 'BV-DHN-02' } }),
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
    quote: vi.fn(async () => ({
      gross_refund: 1000, net_refund: 900, restocking_fee: 0, points_paid_share: 100, points_waived: false,
      captured_tenders: { CASH: 900 }, refundable_by_tender: { CASH: 900 }, non_refundable_tenders: { LOYALTY: 100 },
    })),
    list: vi.fn(async () => ({ returns: [] })),
  },
}));

import ReturnsPage from '../ReturnsPage';

async function press(typeLabel: string | null, review: RegExp, done: string) {
  render(<ReturnsPage />);
  fireEvent.change(screen.getByPlaceholderText(/Order number/), { target: { value: 'Asha' } });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  fireEvent.click(await screen.findByText('BV/26-27/0001'));
  if (typeLabel) fireEvent.click(await screen.findByText(typeLabel));
  fireEvent.change((await screen.findAllByRole('combobox'))[0], { target: { value: '1' } });
  await screen.findByText(/was paid with loyalty points/);
  const btn = await screen.findByRole('button', { name: review });
  await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(btn);
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
  await screen.findByText(done);
  // Let the step change settle: the quote effect drops the quote AFTER the
  // first done render, which is when the old screen fell back to its estimate.
  await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
}

// The record's net_refund is the bill value returned (points included); what
// the press PAYS is net_refund less points_paid_share (_press_answer).
const ANSWER = { net_refund: 1000, points_paid_share: 100, points_waived: false };

describe('Returns done screen: the amount is what the server recorded', () => {
  beforeEach(() => create.mockReset());

  it('a store credit on a points-part bill reads Rs 900, never the Rs 1,000 estimate', async () => {
    create.mockResolvedValueOnce({ ...ANSWER, return_id: 'RET-1', return_type: 'CREDIT_NOTE', credit_amount: 900 });
    await press('Store Credit', /Review Credit Note/, 'Credit Note Issued');
    expect(screen.getByText('₹900')).toBeTruthy();
    expect(screen.queryByText('₹1,000')).toBeNull();
    expect(screen.getByText('Store credit added to customer account')).toBeTruthy();
  });

  it('a cash refund on a points-part bill reads Rs 900, never the Rs 1,000 estimate', async () => {
    create.mockResolvedValueOnce({ ...ANSWER, return_id: 'RET-2', return_type: 'RETURN',
      drawer_auto_netted: true, refund_tenders: [{ method: 'CASH', amount: 900 }] });
    await press(null, /Review Return/, 'Return Processed');
    expect(screen.getByText('₹900')).toBeTruthy();
    expect(screen.queryByText('₹1,000')).toBeNull();
  });
});
