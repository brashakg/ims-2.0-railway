// L2-CUST-F01: the Purchase History summary read `totalAmount` (absent from
// GET /orders) and showed Rs 0 for a customer with real orders. It now uses the
// shared spend rule over the same 500-order read as the page's own cards.
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

const getOrders = vi.hoisted(() => vi.fn());
vi.mock('../../../services/api', () => ({ orderApi: { getOrders } }));

import { CustomerPurchaseHistory } from '../CustomerPurchaseHistory';

describe('CustomerPurchaseHistory', () => {
  it('shows the grand-total spend of the counted orders', async () => {
    getOrders.mockResolvedValue({
      orders: [
        { orderStatus: 'DRAFT', grandTotal: 3000, createdAt: '2026-11-04T10:00:00', items: [] },
        { orderStatus: 'DELIVERED', grandTotal: 1000, createdAt: '2026-11-03T10:00:00', items: [] },
        { orderStatus: 'DELIVERED', grandTotal: 2000, createdAt: '2026-11-02T10:00:00', items: [] },
      ],
    });
    render(<CustomerPurchaseHistory customerId="C1" />);
    expect(await screen.findByText('₹3,000')).toBeInTheDocument();
    expect(screen.getByText('₹1,500')).toBeInTheDocument(); // average of the two counted
    expect(getOrders).toHaveBeenCalledWith({ customerId: 'C1', limit: 500 });
  });
});
