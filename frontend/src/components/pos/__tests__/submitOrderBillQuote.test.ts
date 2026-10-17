// Pay checks the tenders against the SERVER's bill (POST /orders/quote).
//
// The till used to round its own per-line paise: a Rs 265 frame with 1.5%
// line and 2.5% bill discount came to 254.49 there, so it asked Rs 254, while
// create billed Rs 255. The cashier took Rs 254, every leg recorded, and the
// order was saved PARTIAL with Rs 1 owing and no warning. submitPosOrder now
// takes a fresh quote first and checks the tenders against it.
import { describe, it, expect, beforeEach, vi } from 'vitest';

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    orderApi: {
      ...(actual.orderApi as object),
      quoteBill: vi.fn(),
      createOrder: vi.fn(),
      addPayment: vi.fn(),
    },
  };
});

import { orderApi } from '../../../services/api';
import { usePOSStore } from '../../../stores/posStore';
import { submitPosOrder } from '../submitOrder';

const mockedQuote = vi.mocked(orderApi.quoteBill);
const mockedCreate = vi.mocked(orderApi.createOrder);

const SERVER_BILL = {
  subtotal: 261.03, taxable: 242.38, tax: 12.12, total_discount: 10.5,
  grand_total: 255, round_off: 0.5,
};

/** The panel's cart, with no quote taken yet, and `cash` tendered. */
function rs265Cart(cash: number) {
  const s = usePOSStore.getState();
  s.resetTransaction();
  s.addToCart({
    product_id: 'p-frame', name: 'Frame', sku: 'S', category: 'FRAME',
    unit_price: 265, mrp: 265, quantity: 1, is_optical: false,
  } as never);
  const st = usePOSStore.getState();
  st.applyDiscount(st.cart[0].id, 1.5, 'regular customer');
  usePOSStore.getState().setCartDiscount(2.5, 'festival offer');
  usePOSStore.getState().addPayment({ method: 'CASH', amount: cash } as never);
}

describe('Pay checks the tenders against the server-rounded bill', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedQuote.mockResolvedValue(SERVER_BILL);
    mockedCreate.mockResolvedValue({ order_id: 'ord-1', order_number: 'BV-1' } as never);
    vi.mocked(orderApi.addPayment).mockResolvedValue({} as never);
  });

  it('refuses Rs 254 on a bill the server makes Rs 255 -- no order is created', async () => {
    rs265Cart(254);
    const res = await submitPosOrder(usePOSStore.getState(), 'idem-1');
    expect(res.ok).toBe(false);
    expect(res.error).toMatch(/Payment incomplete/);
    expect(mockedCreate).not.toHaveBeenCalled();
  });

  it("takes Rs 255 even though the till's own estimate was 254.49", async () => {
    rs265Cart(255);
    const res = await submitPosOrder(usePOSStore.getState(), 'idem-2');
    expect(res.ok).toBe(true);
    // The quote priced exactly the lines the order was then created with.
    expect(mockedQuote.mock.calls[0][0]).toEqual({
      items: (mockedCreate.mock.calls[0][0] as { items: unknown[] }).items,
      cart_discount_percent: 2.5,
    });
  });

  it('refuses to collect when the server cannot price the bill', async () => {
    rs265Cart(255);
    mockedQuote.mockRejectedValueOnce(new Error('offline'));
    const res = await submitPosOrder(usePOSStore.getState(), 'idem-3');
    expect(res.ok).toBe(false);
    expect(res.error).toMatch(/Could not get the bill total/);
    expect(mockedCreate).not.toHaveBeenCalled();
  });

  it('refuses a cart changed while Pay is waiting for its quote', async () => {
    rs265Cart(255);
    let finish!: (value: typeof SERVER_BILL) => void;
    mockedQuote.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const pending = submitPosOrder(usePOSStore.getState(), 'edited-during-pay');
    usePOSStore.getState().setCartDiscount(3, 'updated offer');
    finish(SERVER_BILL);
    const result = await pending;
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/changed while it was being priced/);
    expect(mockedCreate).not.toHaveBeenCalled();
  });

  it('preserves the scanned unit through the shared quote and create line builder', async () => {
    rs265Cart(255);
    const state = usePOSStore.getState();
    usePOSStore.setState({ cart: state.cart.map((line) => ({ ...line, stock_id: 'SCANNED-UNIT-2' })) });
    const result = await submitPosOrder(usePOSStore.getState(), 'scanned');
    expect(result.ok).toBe(true);
    expect(mockedQuote.mock.calls[0][0].items[0]).toMatchObject({ stock_id: 'SCANNED-UNIT-2' });
    expect(mockedCreate.mock.calls[0][0].items[0]).toMatchObject({ stock_id: 'SCANNED-UNIT-2' });
  });

  it("names the server's reason when it refuses to price the bill", async () => {
    rs265Cart(255);
    // A role that may open the till but not bill (the API client's message).
    mockedQuote.mockRejectedValueOnce(new Error('Your role is not permitted to create orders.'));
    const res = await submitPosOrder(usePOSStore.getState(), 'idem-4');
    expect(res.error).toBe(
      'Could not get the bill total from the server: Your role is not permitted to create orders.',
    );
  });

  it('shows the paise when a payment taken from the estimate is over the rounded bill', async () => {
    // Cash added at the till's estimate (1000.49) before the server's Rs 1000.
    const s = usePOSStore.getState();
    s.resetTransaction();
    s.addToCart({
      product_id: 'p-frame', name: 'Frame', sku: 'S', category: 'FRAME',
      unit_price: 1000.49, mrp: 1000.49, quantity: 1, is_optical: false,
    } as never);
    usePOSStore.getState().addPayment({ method: 'CASH', amount: 1000.49 } as never);
    mockedQuote.mockResolvedValueOnce({
      subtotal: 1000.49, taxable: 952.85, tax: 47.64, total_discount: 0,
      grand_total: 1000, round_off: -0.49,
    });
    const res = await submitPosOrder(usePOSStore.getState(), 'idem-5');
    expect(res.error).toBe(
      'Tendered ₹1,000.49 is ₹0.49 more than the bill total of ₹1,000. ' +
        'The total changed after the payment was added. Remove the payment and add it again.',
    );
    expect(mockedCreate).not.toHaveBeenCalled();
  });
});
