// ============================================================================
// IMS 2.0 - Book shipment must SAY whether the courier collects
// ============================================================================
// The card used to post {order_id, store_id} only. The server reads a missing
// payment_method as Prepaid, and Prepaid on an order with nothing paid is a
// hard refusal - so a web COD order (imported UNPAID, whole bill still owed)
// could not be shipped from the product at all. The choice is now explicit,
// defaults to COD exactly in that case, states what the courier will collect,
// and rides in the request body.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const book = vi.fn();

vi.mock('../../../services/api/shipping', () => ({
  shippingApi: {
    book: (payload: unknown) => book(payload),
    list: vi.fn().mockResolvedValue({ shipments: [], total: 0 }),
    track: vi.fn(),
  },
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));

import { OrderShippingCard } from '../OrderShippingCard';

const UNPAID_WEB_ORDER = {
  orderId: 'ORD-W1',
  orderNumber: 'INV-7001',
  storeId: 'BV-TEST-01',
  balanceDue: 18000,
  paymentStatus: 'UNPAID',
};

function radio(name: 'COD' | 'Prepaid'): HTMLInputElement {
  const label = screen.getByText(name).closest('label') as HTMLLabelElement;
  return label.querySelector('input[type="radio"]') as HTMLInputElement;
}

beforeEach(() => {
  book.mockReset();
  book.mockResolvedValue({
    shipment_id: 'SHP-1',
    order_id: 'ORD-W1',
    status: 'SIMULATED',
    simulated: true,
    message: 'Shipment simulated',
  });
});

describe('OrderShippingCard courier payment', () => {
  it('defaults to COD on an unpaid order and names what is collected', async () => {
    render(<OrderShippingCard {...UNPAID_WEB_ORDER} />);
    await waitFor(() => expect(radio('COD').checked).toBe(true));
    expect(radio('Prepaid').checked).toBe(false);
    expect(screen.getByText(/Collect .*18,000/)).toBeTruthy();
  });

  it('defaults to Prepaid once the order is paid, and COD is not offerable', async () => {
    render(
      <OrderShippingCard
        {...UNPAID_WEB_ORDER}
        balanceDue={0}
        paymentStatus="PAID"
      />,
    );
    await waitFor(() => expect(radio('Prepaid').checked).toBe(true));
    expect(radio('COD').checked).toBe(false);
    expect(radio('COD').disabled).toBe(true);
    expect(screen.getByText('Nothing to collect')).toBeTruthy();
  });

  it('defaults to Prepaid on a part-paid order (a balance alone is not COD)', async () => {
    render(
      <OrderShippingCard
        {...UNPAID_WEB_ORDER}
        balanceDue={2000}
        paymentStatus="PARTIAL"
      />,
    );
    await waitFor(() => expect(radio('Prepaid').checked).toBe(true));
    expect(radio('COD').disabled).toBe(false);
  });

  it('posts the chosen method in the request body', async () => {
    render(<OrderShippingCard {...UNPAID_WEB_ORDER} />);
    await waitFor(() => expect(radio('COD').checked).toBe(true));
    fireEvent.click(screen.getByText('Book shipment'));
    await waitFor(() => expect(book).toHaveBeenCalledTimes(1));
    expect(book.mock.calls[0][0]).toEqual({
      order_id: 'ORD-W1',
      store_id: 'BV-TEST-01',
      address: { payment_method: 'COD' },
    });
  });

  it('sends Prepaid when the user switches to it', async () => {
    render(<OrderShippingCard {...UNPAID_WEB_ORDER} />);
    await waitFor(() => expect(radio('COD').checked).toBe(true));
    fireEvent.click(radio('Prepaid'));
    fireEvent.click(screen.getByText('Book shipment'));
    await waitFor(() => expect(book).toHaveBeenCalledTimes(1));
    expect(book.mock.calls[0][0].address).toEqual({ payment_method: 'Prepaid' });
  });
});
