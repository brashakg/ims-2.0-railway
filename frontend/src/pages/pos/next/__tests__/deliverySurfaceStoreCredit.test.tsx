// ============================================================================
// Store credit at the delivery counter (owner 2026-09-04: "yes, enable it")
// ============================================================================
// StepPayment renders the store-credit tender in target mode ONLY when the
// target names the ORDER's customer. The delivery counter used to hand in no
// customerId, so a customer's own credit could never settle their balance at
// pickup. It now passes the order's customerId -- camelCase, the wire shape
// order_to_frontend emits (pinned by deliverySurfaceWireShape). The id scopes
// the balance the till DISPLAYS; the server redeems against the order's
// customer regardless.
//
// Reverting the customerId line in DeliverySurface fails the first test.

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', name: 'Meena', activeStoreId: 'BV-BOK-01', roles: ['SUPERADMIN'] },
  }),
}));

const getOrder = vi.fn();
vi.mock('../../../../services/api/sales', () => ({
  orderApi: {
    getOrder: (...a: unknown[]) => getOrder(...a),
    getOrders: vi.fn().mockResolvedValue({ orders: [] }),
    addPayment: vi.fn(),
    deliverWithPayment: vi.fn(),
  },
}));

// The barrel: StepPayment reads the store's EMI rate, the picker the sales
// floor. Neither is what this file is about.
vi.mock('../../../../services/api', () => ({
  storeApi: { getStore: vi.fn().mockResolvedValue({ emi_annual_rate_percent: 12 }) },
  adminStoreApi: { getStoreUsers: vi.fn().mockResolvedValue({ users: [] }) },
}));

const getStoreCreditLedger = vi.fn();
vi.mock('../../../../services/api/customers', () => ({
  customerApi: { getStoreCreditLedger: (...a: unknown[]) => getStoreCreditLedger(...a) },
}));

vi.mock('../DeliveryCompleteScreen', () => ({ default: () => <div>handed-over</div> }));
vi.mock('../../../../components/pos/BarcodeScanner', () => ({
  BarcodeScanner: ({ onManualSearch }: { onManualSearch: (v: string) => void }) => (
    <button type="button" onClick={() => onManualSearch('ORD-1')}>
      find-order
    </button>
  ),
}));

import { DeliverySurface } from '../DeliverySurface';
import { usePOSStore } from '../../../../stores/posStore';

/** EXACTLY what order_to_frontend emits -- camelCase, no snake keys. */
const WIRE_ORDER = {
  id: 'o-1',
  orderNumber: 'ORD-1',
  customerName: 'Asha Verma',
  customerPhone: '9876543210',
  orderStatus: 'READY',
  storeId: 'BV-BOK-01',
  grandTotal: 5000,
  amountPaid: 2000,
  balanceDue: 3000,
  items: [{ productName: 'Ray-Ban Aviator', quantity: 1 }],
};

const loadOrder = async () => {
  render(<DeliverySurface />);
  fireEvent.click(screen.getByText('find-order'));
  await waitFor(() => expect(screen.getByText('Asha Verma')).toBeTruthy());
};

beforeEach(() => {
  usePOSStore.getState().resetTransaction();
  getOrder.mockReset();
  getStoreCreditLedger.mockReset().mockResolvedValue({ customer_id: 'c-1', balance: 750 });
});

describe('store credit at the delivery counter', () => {
  it("offers the ORDER customer's store credit against the balance", async () => {
    getOrder.mockResolvedValue({ ...WIRE_ORDER, customerId: 'c-1' });
    await loadOrder();

    await screen.findByText('Store credit');
    // The balance shown is THAT customer's -- read by their id, not a cart's.
    await waitFor(() => expect(getStoreCreditLedger).toHaveBeenCalledWith('c-1'));
  });

  it('offers nothing when the order carries no customer -- there is no one to redeem for', async () => {
    getOrder.mockResolvedValue(WIRE_ORDER);
    await loadOrder();

    // The till itself is up (a tender is offered)...
    expect(screen.getByRole('button', { name: 'Cash' })).toBeTruthy();
    // ...but no store-credit leg, and no ledger read for a customer that is
    // not there.
    expect(screen.queryByText('Store credit')).toBeNull();
    expect(getStoreCreditLedger).not.toHaveBeenCalled();
  });
});
