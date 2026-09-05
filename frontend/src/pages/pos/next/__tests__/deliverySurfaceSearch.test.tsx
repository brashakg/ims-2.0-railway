// ============================================================================
// The counter must find a job WITHOUT the job card
// ============================================================================
// Owner, on the live screen: "let users search through 30 days pending
// delivery data ... also let users search through customer name and phone
// number too".
//
// What the counter did before: try GET /orders/{typed}, and on a 404 fetch the
// newest TWENTY orders of ANY status and match `orderNumber === typed` exactly,
// client-side. So a customer who had lost their job card could not be served:
// a name found nothing, a phone number found nothing, and the twenty-first
// waiting job was invisible even by its own number.
//
// It now searches the delivery QUEUE (GET /orders/pending/delivery?q=), where
// the server matches order number / customer name / phone, keeps only rows
// still awaiting collection, and applies the 30-day browse horizon —
// ADMIN/SUPERADMIN exempt, and naming one customer lifts it for that customer.
//
// The window is deliberately NOT tested here: it is enforced server-side and
// has its own tests (backend/tests/test_delivery_counter_horizon_search.py).
// A UI test that "proved" the horizon would only be proving its own fixture.

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// The widget tiles + customer panel now mount on the delivery counter too
// (owner 2026-09-05); they read through react-query and are not what this
// file is about, so they are stubbed the way the billing/counter tests do.
vi.mock('../PosWidgets', () => ({ PosWidgets: () => null }));
vi.mock('../../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-staff-1', name: 'Meena', activeStoreId: 'BV-BOK-01', roles: ['SALES_STAFF'] },
  }),
}));

const getOrder = vi.fn();
const getOrders = vi.fn();
const getPendingDelivery = vi.fn();
vi.mock('../../../../services/api/sales', () => ({
  orderApi: {
    getOrder: (...a: unknown[]) => getOrder(...a),
    getOrders: (...a: unknown[]) => getOrders(...a),
    getPendingDelivery: (...a: unknown[]) => getPendingDelivery(...a),
    addPayment: vi.fn().mockResolvedValue({}),
    deliverWithPayment: vi.fn().mockResolvedValue({}),
  },
}));

vi.mock('../../../../services/api', () => ({
  storeApi: { getStore: vi.fn().mockResolvedValue({ emi_annual_rate_percent: 12 }) },
  adminStoreApi: { getStoreUsers: vi.fn().mockResolvedValue({ users: [] }) },
}));

vi.mock('../DeliveryCompleteScreen', () => ({
  default: () => <div>handed-over</div>,
}));

// A scanner that lets the test type, rather than one that always scans the
// same number — the whole point here is WHAT was typed.
vi.mock('../../../../components/pos/BarcodeScanner', () => ({
  BarcodeScanner: ({
    onManualSearch,
    placeholder,
  }: {
    onManualSearch: (v: string) => void;
    placeholder?: string;
  }) => (
    <div>
      <input
        aria-label="find"
        placeholder={placeholder}
        onKeyDown={(e) => {
          if (e.key === 'Enter') onManualSearch((e.target as HTMLInputElement).value);
        }}
      />
    </div>
  ),
}));

import { DeliverySurface } from '../DeliverySurface';
import { usePOSStore } from '../../../../stores/posStore';

/** order_to_frontend's shape — camelCase, no snake keys. */
const row = (over: Record<string, unknown> = {}) => ({
  id: 'o-1',
  orderNumber: 'ORD-1',
  customerName: 'Asha Verma',
  customerPhone: '9876543210',
  orderStatus: 'READY',
  storeId: 'BV-BOK-01',
  grandTotal: 5000,
  amountPaid: 5000,
  balanceDue: 0,
  items: [{ productName: 'Ray-Ban Aviator', quantity: 1 }],
  ...over,
});

const search = async (text: string) => {
  render(<DeliverySurface />);
  const box = screen.getByLabelText('find') as HTMLInputElement;
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: 'Enter' });
};

beforeEach(() => {
  // Nothing typed here is an order id, so the direct read always misses.
  getOrder.mockReset().mockRejectedValue(new Error('404'));
  getOrders.mockReset().mockResolvedValue({ orders: [] });
  getPendingDelivery.mockReset().mockResolvedValue({ orders: [] });
  usePOSStore.getState().resetTransaction();
});

describe('finding a waiting job by name or phone', () => {
  it('asks the DELIVERY QUEUE, not the generic order list', async () => {
    // The discriminating assertion: restore the old fallback and this fails.
    // getOrders returns orders of every status, so matching there could hand
    // over an order that was already DELIVERED or is still in the workshop.
    getPendingDelivery.mockResolvedValue({ orders: [row()] });
    await search('Asha');

    await waitFor(() => expect(getPendingDelivery).toHaveBeenCalledTimes(1));
    expect(getPendingDelivery.mock.calls[0][0]).toMatchObject({
      q: 'Asha',
      storeId: 'BV-BOK-01',
    });
    expect(getOrders).not.toHaveBeenCalled();
  });

  it('loads the job when the search resolves to exactly one', async () => {
    getPendingDelivery.mockResolvedValue({ orders: [row()] });
    await search('9876543210');
    await waitFor(() => expect(screen.getByText('Asha Verma')).toBeTruthy());
  });

  it('offers a CHOICE when several jobs match, and loads none of them', async () => {
    // A family shares a surname and one customer can have two pairs on the
    // shelf. Auto-loading the first would hand over the wrong goods, and the
    // staff member would have no way to tell.
    getPendingDelivery.mockResolvedValue({
      orders: [
        row({ id: 'o-1', orderNumber: 'ORD-1', customerName: 'Asha Verma' }),
        row({ id: 'o-2', orderNumber: 'ORD-2', customerName: 'Asha Verma' }),
      ],
    });
    await search('Asha');

    await waitFor(() => expect(screen.getByText('2 jobs waiting — pick one')).toBeTruthy());
    // No single job is committed to, so nothing can be handed over yet.
    const button = () =>
      screen.getByRole('button', { name: /mark delivered/i }) as HTMLButtonElement;
    expect(button().disabled).toBe(true);

    // Picking one commits to it, the list goes away, and — since these bills
    // are fully paid — handover becomes possible. That last part is the
    // negative control: without it a screen that simply never enables the
    // button would pass the assertion above.
    fireEvent.click(screen.getByText('ORD-2 · 9876543210'));
    await waitFor(() => expect(screen.queryByText('2 jobs waiting — pick one')).toBeNull());
    expect(screen.getByText('ORD-2 · 9876543210')).toBeTruthy();
    expect(button().disabled).toBe(false);
  });

  it('says what else to try when the queue has nothing', async () => {
    await search('Nobody');
    await waitFor(() =>
      expect(screen.getByText(/Nothing awaiting collection for "Nobody"/)).toBeTruthy(),
    );
    expect(screen.getByText(/name, or their phone number/)).toBeTruthy();
  });

  it('tells the staff member they can search by name or phone', async () => {
    // The old placeholder said "type the order number", so nobody would ever
    // have tried a name even once the server could answer one.
    render(<DeliverySurface />);
    const box = screen.getByLabelText('find') as HTMLInputElement;
    expect(box.placeholder).toMatch(/name/i);
    expect(box.placeholder).toMatch(/phone/i);
  });
});
