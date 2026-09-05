// ============================================================================
// The delivery counter must read the shape GET /orders/{id} actually returns
// ============================================================================
// GET /orders/{order_id} runs order_to_frontend (backend/api/routers/orders.py:284),
// whose key_map is a DESTRUCTIVE RENAME, not an alias: order_id -> id,
// balance_due -> balanceDue, grand_total -> grandTotal, status -> orderStatus.
// The snake keys are GONE from the response, and the axios interceptor
// (services/api/client.ts:337) only ever adds camel aliases TO snake keys --
// never the reverse.
//
// DeliverySurface was written against the DB document shape instead, so
// `if (!doc?.order_id)` was ALWAYS true and the counter could never load an
// order at all. This test pins the contract: the fixture below is the real wire
// shape (camelCase only, no snake keys), so reverting the screen to snake keys
// makes it fail.

import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// The widget tiles + customer panel now mount on the delivery counter too
// (owner 2026-09-05); they read through react-query and are not what this
// file is about, so they are stubbed the way the billing/counter tests do.
vi.mock('../PosWidgets', () => ({ PosWidgets: () => null }));
vi.mock('../../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', activeStoreId: 'BV-BOK-01', roles: ['SUPERADMIN'] } }),
}));

const getOrder = vi.fn();
vi.mock('../../../../services/api/sales', () => ({
  orderApi: {
    getOrder: (...a: unknown[]) => getOrder(...a),
    getOrders: vi.fn().mockResolvedValue({ orders: [] }),
    deliverWithPayment: vi.fn(),
  },
}));

// The scanner is a keyboard/hardware surface; stand in a plain button that
// fires the same callback the real one does, so this test stays about the
// wire shape and not about scanner internals.
vi.mock('../../../../components/pos/BarcodeScanner', () => ({
  BarcodeScanner: ({ onManualSearch }: { onManualSearch: (v: string) => void }) => (
    <button type="button" onClick={() => onManualSearch('ORD-1')}>
      find-order
    </button>
  ),
}));

import { DeliverySurface } from '../DeliverySurface';

/** EXACTLY what order_to_frontend emits. Deliberately carries NO snake keys. */
const WIRE_ORDER = {
  id: 'o-1',
  orderNumber: 'ORD-1',
  customerName: 'Asha Verma',
  customerPhone: '9876543210',
  orderStatus: 'READY',
  grandTotal: 5000,
  amountPaid: 2000,
  balanceDue: 3000,
  items: [{ productName: 'Ray-Ban Aviator', quantity: 1 }],
};

describe('DeliverySurface reads the real /orders wire shape', () => {
  beforeEach(() => {
    getOrder.mockReset();
    getOrder.mockResolvedValue(WIRE_ORDER);
  });

  it('loads an order whose payload has camelCase keys only', async () => {
    render(<DeliverySurface />);
    screen.getByText('find-order').click();

    // The customer only renders once the order was accepted as found. With the
    // snake-key read this stayed null and the "No order found" error showed.
    await waitFor(() => expect(screen.getByText('Asha Verma')).toBeTruthy());

    expect(screen.queryByText(/No order found/i)).toBeNull();
    expect(screen.getByText('Ray-Ban Aviator', { exact: false })).toBeTruthy();
  });

  it('shows the server balance, and pre-fills the collect box with it', async () => {
    render(<DeliverySurface />);
    screen.getByText('find-order').click();

    // balanceDue 3000 -> the big red number AND the prefilled amount. A screen
    // reading balance_due would show 0 here and silently collect nothing.
    await waitFor(() => expect(screen.getByText('₹3,000')).toBeTruthy());
    const amount = document.querySelector('input[type="number"]') as HTMLInputElement;
    expect(amount).toBeTruthy();
    expect(Number(amount.value)).toBe(3000);

    // amountPaid / grandTotal drive the "paid" line.
    expect(screen.getByText(/₹2,000 of ₹5,000 paid/)).toBeTruthy();
  });
});
