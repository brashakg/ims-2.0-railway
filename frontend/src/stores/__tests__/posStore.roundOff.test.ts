// ============================================================================
// Bill round off at the till (owner ruling 2026-10-08)
// ============================================================================
// The bill is rounded to the nearest rupee by the SERVER (POST /orders/quote,
// priced by order create's own math). The till never rounds a bill itself: it
// collects the quote for the live cart, and shows its own unrounded figure
// only as an estimate while no quote is current. A till-side rounding once
// quoted a Rs 265 frame (1.5% line + 2.5% bill discount) at Rs 254 while
// create billed Rs 255 -- the backend's tests/test_bill_round_off.py pins the
// quote to create on that cart; these pin the till to the quote.

import { describe, it, expect, beforeEach } from 'vitest';
import { billQuoteKey, usePOSStore } from '../posStore';

const add = (unit_price: number, category = 'FRAME') =>
  usePOSStore.getState().addToCart({
    product_id: `p-${category}-${unit_price}`,
    name: `${category} ${unit_price}`,
    sku: 'S',
    category,
    unit_price,
    mrp: unit_price,
    quantity: 1,
    is_optical: false,
  } as never);

/** Store the server's answer for the CURRENT cart, as billQuote.ts does. */
const quote = (grand_total: number, round_off: number) =>
  usePOSStore.getState().setBillQuote({
    key: billQuoteKey(usePOSStore.getState()),
    grand_total,
    round_off,
    tax: 0,
    total_discount: 0,
  });

/** The panel's cart: Rs 265 frame, 1.5% line and 2.5% bill discount. */
const rs265Cart = () => {
  add(265);
  const s = usePOSStore.getState();
  s.applyDiscount(s.cart[0].id, 1.5, 'regular customer');
  usePOSStore.getState().setCartDiscount(2.5, 'festival offer');
};

beforeEach(() => usePOSStore.getState().resetTransaction());

describe('the till collects the server-rounded bill and never rounds one itself', () => {
  it('without a quote the total is the unrounded estimate and there is no round off', () => {
    add(1000.49);
    const s = usePOSStore.getState();
    expect(s.getGrandTotal()).toBe(1000.49);
    expect(s.getRoundOff()).toBe(0);
  });

  it("collects the server's figure for the Rs 265 cart, not its own paise", () => {
    rs265Cart();
    // The till's own paise land on 254.49; rounding those would bill Rs 254.
    expect(usePOSStore.getState().getBillValue()).toBe(254.49);
    quote(255, 0.5);
    const s = usePOSStore.getState();
    expect(s.getGrandTotal()).toBe(255);
    expect(s.getRoundOff()).toBe(0.5);
    s.addPayment({ method: 'CASH', amount: 254 } as never);
    expect(usePOSStore.getState().getBalance()).toBe(1);
  });

  it('ignores a quote for a cart that has since changed', () => {
    add(1000.5);
    quote(1001, 0.5);
    expect(usePOSStore.getState().getGrandTotal()).toBe(1001);
    add(500, 'SUNGLASS');
    const s = usePOSStore.getState();
    expect(s.getQuotedBill()).toBeNull();
    expect(s.getGrandTotal()).toBe(1500.5);
    expect(s.getRoundOff()).toBe(0);
    // A bill-discount change is a different bill too.
    quote(1501, 0.5);
    usePOSStore.getState().setCartDiscount(5, 'festival offer');
    expect(usePOSStore.getState().getQuotedBill()).toBeNull();
  });

  it('a mixed-GST bill keeps its tax and taxable value; only the payable moves', () => {
    add(999.99, 'FRAME'); // 5%: 952.37 + 47.62
    add(500.5, 'SUNGLASS'); // 18%: 424.15 + 76.35
    quote(1500, -0.49);
    const s = usePOSStore.getState();
    expect(s.getTax()).toBe(123.97);
    expect(s.getTaxableValue()).toBe(1376.52);
    expect(s.getRoundOff()).toBe(-0.49);
    expect(s.getGrandTotal()).toBe(1500);
  });

  it('a split payment of the quoted total settles the bill', () => {
    add(1000.5);
    quote(1001, 0.5);
    const s = usePOSStore.getState();
    s.addPayment({ method: 'UPI', amount: 500 } as never);
    s.addPayment({ method: 'CASH', amount: 501 } as never);
    expect(usePOSStore.getState().getBalance()).toBe(0);
  });
});
