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
import { ApiError } from '../../../services/api/client';

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

// ----------------------------------------------------------------------------
// Review round 2: the card must not GUESS a balance the API did not send, and
// a 409 (parcel already out) needs a confirmed re-book path in the UI.
// ----------------------------------------------------------------------------

function alreadyBooked409() {
  return new ApiError(
    'Shipment SHP-LIVE (AWB-1, BOOKED) is already out for this order. Re-book only if that parcel is not coming.',
    {
      status: 409,
      code: 'SHIPMENT_ALREADY_BOOKED',
      detail: { code: 'SHIPMENT_ALREADY_BOOKED', shipment_id: 'SHP-LIVE', awb: 'AWB-1', status: 'BOOKED' },
    },
  );
}

describe('OrderShippingCard when the balance is not recorded', () => {
  it('keeps COD offerable and shows the bill (the server reads the whole bill as owed)', async () => {
    render(
      <OrderShippingCard
        orderId="ORD-L1"
        orderNumber="INV-2"
        storeId="BV-TEST-01"
        grandTotal={3000}
        paymentStatus="UNPAID"
      />,
    );
    await waitFor(() => expect(radio('COD').checked).toBe(true));
    expect(radio('COD').disabled).toBe(false);
    expect(screen.getByText(/Collect .*3,000/)).toBeTruthy();
    expect(screen.queryByText('Nothing to collect')).toBeNull();
  });

  it('defers the figure to the server when neither balance nor bill is known', async () => {
    render(<OrderShippingCard orderId="ORD-L2" orderNumber="INV-3" storeId="BV-TEST-01" />);
    await waitFor(() => expect(screen.getByText('Book shipment')).toBeTruthy());
    expect(radio('COD').disabled).toBe(false);
    expect(screen.getByText('Amount confirmed by the server')).toBeTruthy();
  });
});

describe('OrderShippingCard on 409 SHIPMENT_ALREADY_BOOKED', () => {
  it('names the existing shipment and re-posts with rebook only after confirm', async () => {
    book.mockRejectedValueOnce(alreadyBooked409());
    render(<OrderShippingCard {...UNPAID_WEB_ORDER} />);
    await waitFor(() => expect(radio('COD').checked).toBe(true));
    fireEvent.click(screen.getByText('Book shipment'));
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());
    expect(screen.getByRole('alert').textContent).toMatch(/AWB-1.*BOOKED.*already out/);
    expect(book).toHaveBeenCalledTimes(1);
    expect(book.mock.calls[0][0].rebook).toBeUndefined();

    fireEvent.click(screen.getByText('Book again anyway'));
    await waitFor(() => expect(book).toHaveBeenCalledTimes(2));
    expect(book.mock.calls[1][0]).toEqual({
      order_id: 'ORD-W1',
      store_id: 'BV-TEST-01',
      address: { payment_method: 'COD' },
      rebook: true,
    });
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  });

  it('lets the user keep the existing shipment instead', async () => {
    book.mockRejectedValueOnce(alreadyBooked409());
    render(<OrderShippingCard {...UNPAID_WEB_ORDER} />);
    await waitFor(() => expect(radio('COD').checked).toBe(true));
    fireEvent.click(screen.getByText('Book shipment'));
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());
    fireEvent.click(screen.getByText('Keep the existing shipment'));
    expect(screen.queryByRole('alert')).toBeNull();
    expect(book).toHaveBeenCalledTimes(1);
  });
});
