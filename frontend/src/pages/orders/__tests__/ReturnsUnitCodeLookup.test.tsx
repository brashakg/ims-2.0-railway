// ============================================================================
// Returns: the code on the returned frame's label finds the sale
// ============================================================================
// A customer brings a frame back; the unit's IMS label ('BV0000000042', or an
// old 'BV--91FA3858') is the one thing on it. The order search matched only
// order number, customer name and phone, so the code found nothing. The till
// stamps the sale on the UNIT (stock_units.order_id), so the code is resolved
// through the unit lookup to the order it was sold on.

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { user_id: 'u-cashier', roles: ['CASHIER'], activeStoreId: 'BV-DHN-02' } }),
}));

const ORDERS = [
  { id: 'ORD-1', orderNumber: 'BV/26-27/0001', customerName: 'Asha', customerPhone: '9800000001', items: [] },
  { id: 'ORD-2', orderNumber: 'BV/26-27/0002', customerName: 'Ravi', customerPhone: '9800000002', items: [] },
];
const UNITS: Record<string, { barcode: string; order_id?: string; orderId?: string }> = {
  BV0000000042: { barcode: 'BV0000000042', order_id: 'ORD-2', orderId: 'ORD-2' },
  'BV--91FA3858': { barcode: 'BV--91FA3858', order_id: 'ORD-1', orderId: 'ORD-1' },
};

vi.mock('../../../services/api', () => ({
  orderApi: { getOrders: vi.fn(async () => ({ orders: ORDERS })) },
  productApi: { searchProducts: vi.fn(async () => []) },
  inventoryApi: {
    // The server matches a unit code in any letter case (unit_barcode_match).
    getStockByBarcode: vi.fn(async (code: string) => {
      const unit = UNITS[code.toUpperCase()];
      if (!unit) throw new Error('Stock item not found');
      return unit;
    }),
  },
}));
vi.mock('../../../services/api/returns', () => ({
  returnsApi: { create: vi.fn(), quote: vi.fn(), list: vi.fn(async () => ({ returns: [] })) },
}));

import ReturnsPage from '../ReturnsPage';

async function find(text: string) {
  render(<ReturnsPage />);
  fireEvent.change(screen.getByPlaceholderText(/Order number/), { target: { value: text } });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
}

describe('Returns: find the sale by the unit code on the frame', () => {
  it.each([
    ['bv0000000042', 'BV/26-27/0002', 'BV/26-27/0001'],
    ['BV--91FA3858', 'BV/26-27/0001', 'BV/26-27/0002'],
  ])('%s finds the order it was sold on', async (code, shown, hidden) => {
    await find(code);
    expect(await screen.findByText(shown)).toBeInTheDocument();
    expect(screen.queryByText(hidden)).not.toBeInTheDocument();
  });

  it('a customer name still finds their order', async () => {
    await find('Asha');
    expect(await screen.findByText('BV/26-27/0001')).toBeInTheDocument();
    expect(screen.queryByText('BV/26-27/0002')).not.toBeInTheDocument();
  });
});
