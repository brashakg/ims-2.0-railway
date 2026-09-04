// ============================================================================
// The delivery counter must be the SAME till, and must not weaken the gate
// ============================================================================
// Owner, on the live screen: "split payment option and credit delivery options
// not showing on delivery screen" / "ui ux and options should have the same
// design language as the pos screen" / "who is giving delivery to the customer
// (salesperson) should also be logged".
//
// All three are one defect: the counter hand-rolled a miniature payment block
// (three buttons and one amount box) instead of rendering StepPayment, the
// component both other tills already use. These tests pin the fix at the wire:
//
//   1. a SPLIT tender really reaches the server as two legs;
//   2. the credit-delivery manager gate still fires on a shortfall, and the
//      richer surface offers no tender that could paper over it client-side;
//   3. the handover names the STAFF member who handed the goods over, under
//      the keys the backend half of this change persists.
//
// Reverting the screen to its own three-button block fails 1 and 3; adding a
// cart-bound tender (loyalty / voucher / khata) to the counter fails 2. Store
// credit is the one tender with a target seam and is owner-enabled here
// (2026-09-04) -- see deliverySurfaceStoreCredit.test.tsx.

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-staff-1', name: 'Meena', activeStoreId: 'BV-BOK-01', roles: ['SUPERADMIN'] },
  }),
}));

const getOrder = vi.fn();
const addPayment = vi.fn();
const deliverWithPayment = vi.fn();
vi.mock('../../../../services/api/sales', () => ({
  orderApi: {
    getOrder: (...a: unknown[]) => getOrder(...a),
    getOrders: vi.fn().mockResolvedValue({ orders: [] }),
    addPayment: (...a: unknown[]) => addPayment(...a),
    deliverWithPayment: (...a: unknown[]) => deliverWithPayment(...a),
  },
}));

// The barrel: StepPayment reads the store's EMI rate, the picker reads the
// store's sales floor. Neither is what these tests are about.
vi.mock('../../../../services/api', () => ({
  storeApi: { getStore: vi.fn().mockResolvedValue({ emi_annual_rate_percent: 12 }) },
  adminStoreApi: { getStoreUsers: vi.fn().mockResolvedValue({ users: [] }) },
}));

// The completion screen is a routed, self-fetching surface with its own
// tests; here it only has to prove WHICH staff member the handover credited.
const completedProps = vi.fn();
vi.mock('../DeliveryCompleteScreen', () => ({
  default: (props: Record<string, unknown>) => {
    completedProps(props);
    return <div>handed-over</div>;
  },
}));

vi.mock('../../../../components/pos/BarcodeScanner', () => ({
  BarcodeScanner: ({ onManualSearch }: { onManualSearch: (v: string) => void }) => (
    <button type="button" onClick={() => onManualSearch('ORD-1')}>
      find-order
    </button>
  ),
}));

import { DeliverySurface } from '../DeliverySurface';
import { usePOSStore } from '../../../../stores/posStore';

/** EXACTLY what order_to_frontend emits — camelCase, no snake keys. */
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

/** Drive the shared till: pick a tender, type the amount (+ ref), press Add. */
const addLeg = (method: string, amount: number, reference?: string) => {
  fireEvent.click(screen.getByRole('button', { name: method }));
  const inputs = Array.from(
    document.querySelectorAll('input[type="number"]'),
  ) as HTMLInputElement[];
  fireEvent.change(inputs[0], { target: { value: String(amount) } });
  if (reference !== undefined) {
    const ref = document.querySelector(
      'input[placeholder="UPI Txn ID *"], input[placeholder="Approval code"], input[placeholder="Reference"]',
    ) as HTMLInputElement;
    fireEvent.change(ref, { target: { value: reference } });
  }
  fireEvent.click(screen.getByRole('button', { name: 'Add' }));
};

describe('the delivery counter runs the shared POS till', () => {
  beforeEach(() => {
    getOrder.mockReset().mockResolvedValue(WIRE_ORDER);
    addPayment.mockReset().mockResolvedValue({});
    deliverWithPayment.mockReset().mockResolvedValue({});
    completedProps.mockReset();
    usePOSStore.getState().resetTransaction();
  });

  it('collects a SPLIT tender — two legs, both reaching the server', async () => {
    await loadOrder();

    addLeg('Cash', 1000);
    addLeg('UPI', 2000, 'UPI-77');

    // Both legs are on the bill, so the action button offers the full balance.
    await waitFor(() =>
      expect(screen.getByText('Collect ₹3,000 & mark delivered')).toBeTruthy(),
    );
    fireEvent.click(screen.getByText('Collect ₹3,000 & mark delivered'));

    // Leg 1 goes through the payments door, leg 2 rides the deliver door —
    // the same server sequence deliver_with_payment runs internally.
    await waitFor(() => expect(deliverWithPayment).toHaveBeenCalledTimes(1));
    expect(addPayment).toHaveBeenCalledTimes(1);
    expect(addPayment.mock.calls[0][0]).toBe('o-1');
    expect(addPayment.mock.calls[0][1]).toMatchObject({ method: 'CASH', amount: 1000 });

    const body = deliverWithPayment.mock.calls[0][1];
    expect(body.payment).toMatchObject({ method: 'UPI', amount: 2000, reference: 'UPI-77' });
    // The one-button screen could only ever send ONE tender.
    expect(addPayment.mock.calls[0][1].method).not.toBe(body.payment.method);
  });

  it('logs WHO handed over, defaulted to the signed-in user', async () => {
    await loadOrder();
    addLeg('Cash', 3000);
    fireEvent.click(screen.getByText('Collect ₹3,000 & mark delivered'));

    await waitFor(() => expect(deliverWithPayment).toHaveBeenCalledTimes(1));
    const { handover } = deliverWithPayment.mock.calls[0][1];
    // The keys the backend half persists. picked_up_by_name is the CUSTOMER
    // side and must not be confused with this.
    expect(handover.delivered_by_id).toBe('u-staff-1');
    expect(handover.delivered_by_name).toBe('Meena');

    // ...and the delivery lands on THAT person's day, not the seller's.
    await waitFor(() => expect(completedProps).toHaveBeenCalled());
    expect(completedProps.mock.calls.at(-1)[0]).toMatchObject({
      salespersonId: 'u-staff-1',
      salespersonName: 'Meena',
    });
  });

  it('keeps the credit-delivery gate: a shortfall still asks for the manager token', async () => {
    await loadOrder();
    addLeg('Cash', 1000); // 2,000 short of the 3,000 balance

    const token = (await screen.findByPlaceholderText(
      /Manager approval token/i,
    )) as HTMLInputElement;
    fireEvent.change(token, { target: { value: 'TKN-9' } });

    fireEvent.click(screen.getByText('Collect ₹1,000 & mark delivered'));
    await waitFor(() => expect(deliverWithPayment).toHaveBeenCalledTimes(1));
    expect(deliverWithPayment.mock.calls[0][1].approval_token).toBe('TKN-9');

    // ...and the richer surface hands the counter NO cart-bound tender that
    // would zero the shortfall client-side while the server balance stays
    // owing. Loyalty / voucher / khata read the CART and stay off this screen.
    expect(screen.queryByText('Voucher / gift card')).toBeNull();
    expect(screen.queryByRole('button', { name: 'CREDIT' })).toBeNull();
  });

  it('gives the cart its salesperson back when the counter unmounts', async () => {
    // The picker writes posStore.salesperson_id — the BILL's attribution,
    // which feeds incentives. Borrowing it for the handover must not re-credit
    // a bill still open at the till.
    usePOSStore.getState().setSalesperson('u-other', 'Ravi');
    const view = render(<DeliverySurface />);
    expect(usePOSStore.getState().salesperson_id).toBe('u-staff-1');
    view.unmount();
    expect(usePOSStore.getState().salesperson_id).toBe('u-other');
    expect(usePOSStore.getState().salesperson_name).toBe('Ravi');
  });
});
