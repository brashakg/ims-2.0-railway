// ============================================================================
// A refused handover must not charge the customer twice
// ============================================================================
// Found by an adversarial audit of the money path, confirmed by three
// independent refuters, and it was live in this screen.
//
// THE SEQUENCE. Balance Rs 10,000. A cashier (not a manager) takes Rs 3,000
// cash and presses hand over. With one leg, `legs.slice(0, -1)` is empty, so
// nothing goes through the payments door and the 3,000 rides the deliver door.
// Server-side that door records the payment FIRST and only then runs the
// credit-delivery gate -- which refuses a cashier on a shortfall. So the money
// is banked and the handover is refused.
//
// The screen then kept its stale Rs 10,000 balance and its tender list, and
// two things went wrong on the retry:
//
//   1. `postedLegsRef` only ever recorded legs posted through the payments
//      loop, never the one handed to the deliver door. So the 3,000 was posted
//      AGAIN -- and `addPayment` sent no Idempotency-Key, so the server had
//      nothing to dedupe on and wrote a second row.
//   2. One idempotency key was minted per handover and reused even though the
//      LAST LEG had changed. The server matched the earlier attempt's payment
//      and recorded nothing for the new tender -- cash in the drawer with no
//      payment row against it.
//
// Net: customer hands over Rs 10,000, the order records Rs 6,000, Rs 4,000 is
// falsely outstanding on their khata and the drawer is Rs 4,000 over at the
// blind day-end count.
//
// The fix is three-part and each part is asserted below: a per-leg
// Idempotency-Key, a delivery key derived from the leg that actually rides the
// deliver door, and a resync with the server after any refusal -- because the
// server is the authority on what has been paid, not this screen's memory.

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-cashier', name: 'Meena', activeStoreId: 'BV-BOK-01', roles: ['CASHIER'] },
  }),
}));

const getOrder = vi.fn();
const addPayment = vi.fn();
const deliverWithPayment = vi.fn();
vi.mock('../../../../services/api/sales', () => ({
  orderApi: {
    getOrder: (...a: unknown[]) => getOrder(...a),
    getOrders: vi.fn().mockResolvedValue({ orders: [] }),
    getPendingDelivery: vi.fn().mockResolvedValue({ orders: [] }),
    addPayment: (...a: unknown[]) => addPayment(...a),
    deliverWithPayment: (...a: unknown[]) => deliverWithPayment(...a),
  },
}));

vi.mock('../../../../services/api', () => ({
  storeApi: { getStore: vi.fn().mockResolvedValue({ emi_annual_rate_percent: 12 }) },
  adminStoreApi: { getStoreUsers: vi.fn().mockResolvedValue({ users: [] }) },
}));

vi.mock('../DeliveryCompleteScreen', () => ({ default: () => <div>handed-over</div> }));

vi.mock('../../../../components/pos/BarcodeScanner', () => ({
  BarcodeScanner: ({ onManualSearch }: { onManualSearch: (v: string) => void }) => (
    <button type="button" onClick={() => onManualSearch('ORD-1')}>find-order</button>
  ),
}));

import { DeliverySurface } from '../DeliverySurface';
import { usePOSStore } from '../../../../stores/posStore';

const order = (balanceDue: number, amountPaid: number) => ({
  id: 'o-1',
  orderNumber: 'ORD-1',
  customerName: 'Asha Verma',
  customerPhone: '9876543210',
  orderStatus: 'READY',
  storeId: 'BV-BOK-01',
  grandTotal: 10000,
  amountPaid,
  balanceDue,
  items: [{ productName: 'Ray-Ban Aviator', quantity: 1 }],
});

/** Drive the shared till: pick a tender, type the amount, press Add. */
const addLeg = (method: string, amount: number) => {
  fireEvent.click(screen.getByRole('button', { name: method }));
  const inputs = Array.from(
    document.querySelectorAll('input[type="number"]'),
  ) as HTMLInputElement[];
  fireEvent.change(inputs[0], { target: { value: String(amount) } });
  fireEvent.click(screen.getByRole('button', { name: 'Add' }));
};

const handOver = () =>
  fireEvent.click(screen.getByRole('button', { name: /mark delivered|collect/i }));

const REFUSAL = {
  response: { data: { detail: 'Rs 7,000 is still due. A manager must approve a credit delivery.' } },
};

beforeEach(() => {
  getOrder.mockReset().mockResolvedValue(order(10000, 0));
  addPayment.mockReset().mockResolvedValue({});
  deliverWithPayment.mockReset();
  usePOSStore.getState().resetTransaction();
});

async function loadAndFailFirstAttempt() {
  // The deliver door banks the 3,000, then refuses. That is the real server
  // sequence, so the refetch must come back with the money already applied.
  deliverWithPayment.mockRejectedValueOnce(REFUSAL);
  render(<DeliverySurface />);
  fireEvent.click(screen.getByText('find-order'));
  await waitFor(() => expect(screen.getByText('Asha Verma')).toBeTruthy());

  addLeg('Cash', 3000);
  getOrder.mockResolvedValue(order(7000, 3000)); // what the server now holds
  handOver();
  await waitFor(() => expect(deliverWithPayment).toHaveBeenCalledTimes(1));
}

describe('a refused handover does not charge the customer twice', () => {
  it('re-reads the order after a refusal instead of trusting its own balance', async () => {
    await loadAndFailFirstAttempt();
    // Two reads: the initial lookup, then the resync. Without the resync the
    // screen keeps showing Rs 10,000 due when only Rs 7,000 is.
    await waitFor(() => expect(getOrder).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.getByText(/₹3,000 was recorded before it stopped/)).toBeTruthy(),
    );
    expect(screen.getByText(/₹7,000 is still due/)).toBeTruthy();
  });

  it('never re-posts the leg that rode the deliver door', async () => {
    await loadAndFailFirstAttempt();
    await waitFor(() => expect(getOrder).toHaveBeenCalledTimes(2));

    // The customer pays the rest. The tender list was cleared by the resync,
    // so the operator enters ONLY what is still owed.
    addLeg('Cash', 7000);
    handOver();
    await waitFor(() => expect(deliverWithPayment).toHaveBeenCalledTimes(2));

    // THE BUG: the 3,000 used to be posted a second time through the payments
    // door on this retry. Nothing may re-send it.
    const resent = addPayment.mock.calls.filter((c) => (c[1] as any)?.amount === 3000);
    expect(resent).toEqual([]);
  });

  it('sends a DIFFERENT delivery key when the tender changes and the resync FAILED', async () => {
    // The resync path already mints a fresh session, so it hides this defect.
    // Measured: with the resync in place, reverting the key derivation broke
    // nothing. The key must therefore be pinned on the path where the resync
    // could not run -- the refetch itself failing on a flaky shop connection --
    // because there the operator edits the tender in place and presses again.
    deliverWithPayment.mockRejectedValueOnce(REFUSAL);
    render(<DeliverySurface />);
    fireEvent.click(screen.getByText('find-order'));
    await waitFor(() => expect(screen.getByText('Asha Verma')).toBeTruthy());

    addLeg('Cash', 3000);
    getOrder.mockRejectedValue(new Error('network')); // the resync cannot run
    handOver();
    await waitFor(() => expect(deliverWithPayment).toHaveBeenCalledTimes(1));
    // The tender survives, because nothing could be confirmed about it.
    await waitFor(() => expect(screen.getByText(/still due|Could not complete/)).toBeTruthy());

    // The customer pays the rest; the operator adds it to the same list.
    deliverWithPayment.mockResolvedValueOnce({});
    addLeg('Cash', 7000);
    handOver();
    await waitFor(() => expect(deliverWithPayment).toHaveBeenCalledTimes(2));

    const firstKey = deliverWithPayment.mock.calls[0][2];
    const secondKey = deliverWithPayment.mock.calls[1][2];
    expect(firstKey).toBeTruthy();
    expect(secondKey).toBeTruthy();
    // Reusing one key made the server match the 3,000 it had already taken and
    // record NOTHING for the 7,000 -- cash with no payment row against it.
    expect(secondKey).not.toBe(firstKey);
  });

  it('gives every payments-door leg its own idempotency key', async () => {
    // A split that puts legs through the payments door: each must carry a key,
    // or a retried leg is written twice (the server only dedupes when the
    // header is present).
    deliverWithPayment.mockResolvedValue({});
    render(<DeliverySurface />);
    fireEvent.click(screen.getByText('find-order'));
    await waitFor(() => expect(screen.getByText('Asha Verma')).toBeTruthy());

    addLeg('Cash', 4000);
    addLeg('Cash', 6000);
    handOver();

    await waitFor(() => expect(addPayment).toHaveBeenCalledTimes(1));
    const key = addPayment.mock.calls[0][2];
    expect(typeof key).toBe('string');
    expect(key).toBeTruthy();
    // and it must not be the same string the deliver door was given
    await waitFor(() => expect(deliverWithPayment).toHaveBeenCalled());
    expect(key).not.toBe(deliverWithPayment.mock.calls[0][2]);
  });
});
