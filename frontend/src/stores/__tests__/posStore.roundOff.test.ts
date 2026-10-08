// ============================================================================
// Bill round off at the till (owner ruling 2026-10-08)
// ============================================================================
// The cashier collects payment BEFORE the order exists, so the till's total
// must already be the rupee the server will bill: nearest rupee, 50 paise and
// above up, below down, applied once after GST. The round off is its own line;
// GST and taxable value do not move. Same anchors as the backend's
// tests/test_bill_round_off.py, so the two implementations are held to one
// answer (the server stays the authority on order-create).

import { describe, it, expect, beforeEach } from 'vitest';
import { usePOSStore } from '../posStore';

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

beforeEach(() => usePOSStore.getState().resetTransaction());

describe('the till rounds the bill to the nearest rupee', () => {
  it.each([
    [1000.49, 1000, -0.49],
    [1000.5, 1001, 0.5],
    [1000.51, 1001, 0.49],
    [1000, 1000, 0],
  ])('a %s bill is payable as %s with a round off of %s', (price, payable, roundOff) => {
    add(price);
    const s = usePOSStore.getState();
    expect(s.getGrandTotal()).toBe(payable);
    expect(s.getRoundOff()).toBe(roundOff);
  });

  it('a mixed-GST bill keeps its tax and taxable value; only the payable moves', () => {
    add(999.99, 'FRAME'); // 5%: 952.37 + 47.62
    add(500.5, 'SUNGLASS'); // 18%: 424.15 + 76.35
    const s = usePOSStore.getState();
    expect(s.getTax()).toBe(123.97);
    expect(s.getTaxableValue()).toBe(1376.52);
    expect(s.getRoundOff()).toBe(-0.49);
    expect(s.getGrandTotal()).toBe(1500);
    expect(Math.round((s.getTaxableValue() + s.getTax() + s.getRoundOff()) * 100) / 100)
      .toBe(s.getGrandTotal());
  });

  it('a split payment of the rounded total settles the bill', () => {
    add(1000.5);
    const s = usePOSStore.getState();
    s.addPayment({ method: 'UPI', amount: 500 } as never);
    s.addPayment({ method: 'CASH', amount: 501 } as never);
    expect(usePOSStore.getState().getBalance()).toBe(0);
  });
});
