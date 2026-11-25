// ============================================================================
// Returns: a bill whose points an Admin closed never promises points back (D28)
// ============================================================================
// The part of a return that was paid with loyalty points is held back from the
// money and normally "goes back to the customer's points by itself". Once an
// Admin has closed the points recovery exception on the bill (owner ruling
// 2026-11-08) nothing gives points back automatically, so the quote line must
// say so instead: the cashier reads this line to the customer.
//
// Drives the REAL page: find the sale, pick one piece, read the quote line.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { user_id: 'u-cashier', roles: ['CASHIER'], activeStoreId: 'BV-DHN-02' } }),
}));

const ORDER = {
  id: 'ORD-1', orderNumber: 'BV/26-27/0001', customerName: 'Asha', customerPhone: '9800000001',
  items: [{ id: 'it-1', productName: 'Frame A', sku: 'FR-A', quantity: 1, unitPrice: 1000,
    taxable_value: 952.38, tax_amount: 47.62, gst_rate: 5 }],
};

vi.mock('../../../services/api', () => ({
  orderApi: { getOrders: vi.fn(async () => ({ orders: [ORDER] })) },
  productApi: { searchProducts: vi.fn(async () => []) },
  inventoryApi: { getStockByBarcode: vi.fn(async () => { throw new Error('Stock item not found'); }) },
}));

const quote = vi.fn();
vi.mock('../../../services/api/returns', () => ({
  returnsApi: { create: vi.fn(), quote: (...a: unknown[]) => quote(...a), list: vi.fn(async () => ({ returns: [] })) },
}));

import ReturnsPage from '../ReturnsPage';

const QUOTE = {
  return_type: 'RETURN', gross_refund: 1000, restocking_fee: 0, net_refund: 900,
  points_paid_share: 100, captured_tenders: { CASH: 900 }, non_refundable_tenders: { LOYALTY: 100 },
  refundable_tenders: { CASH: 900 }, replacement_items_priced: [], replacement_total: 0,
};

async function quoteLine(points_waived: boolean) {
  quote.mockResolvedValue({ ...QUOTE, points_waived });
  render(<ReturnsPage />);
  fireEvent.change(screen.getByPlaceholderText(/Order number/), { target: { value: 'Asha' } });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  fireEvent.click(await screen.findByText('BV/26-27/0001'));
  await screen.findByText('Frame A');
  // The first select on the item row is its "Return qty".
  fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: '1' } });
  return (await screen.findByText(/was paid with loyalty points/)).textContent || '';
}

describe('Returns quote: the points-paid part', () => {
  beforeEach(() => quote.mockReset());

  it('on a bill an Admin closed: says head office settles the points, promises nothing', async () => {
    const text = await quoteLine(true);
    expect(text).toMatch(/settled by head office and are not returned automatically/);
    expect(text).not.toMatch(/goes back to the customer's points/);
    expect(text).toMatch(/not paid as money or store credit/);
  });

  it('on any other bill: the wording is unchanged', async () => {
    const text = await quoteLine(false);
    expect(text).toMatch(
      /was paid with loyalty points: it goes back to the customer's points by itself, not as money or store credit\./,
    );
    expect(text).not.toMatch(/head office/);
  });
});
