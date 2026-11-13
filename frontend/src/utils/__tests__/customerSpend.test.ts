// L2-CUST-F01: one spend rule for every customer door.
import { describe, it, expect } from 'vitest';
import { CUSTOMER_SPEND_ORDER_LIMIT, summariseCustomerSpend } from '../customerSpend';

// The audit's customer: 1,000 + 1,000 + 1,000 delivered and a 3,000 draft, as
// GET /orders returns them (camelCase; no totalAmount field).
const AUDIT_ORDERS = [
  { orderNumber: 'D', orderStatus: 'DRAFT', grandTotal: 3000, createdAt: '2026-11-04T10:00:00' },
  { orderNumber: 'C', orderStatus: 'DELIVERED', grandTotal: 1000, createdAt: '2026-11-03T10:00:00' },
  { orderNumber: 'B', orderStatus: 'DELIVERED', grandTotal: 1000, createdAt: '2026-11-02T10:00:00' },
  { orderNumber: 'A', orderStatus: 'DELIVERED', grandTotal: 1000, createdAt: '2026-11-01T10:00:00' },
];

describe('summariseCustomerSpend', () => {
  it('sums grandTotal (never Rs 0 for real orders) and leaves the draft out', () => {
    const s = summariseCustomerSpend(AUDIT_ORDERS);
    expect(s).toEqual({
      totalSpend: 3000, orderCount: 3, avgOrderValue: 1000,
      lastOrderDate: '2026-11-03T10:00:00', capped: false,
    });
  });

  it('leaves cancelled and refunded orders out, any case, and reads raw rows too', () => {
    const s = summariseCustomerSpend([
      { status: 'cancelled', grand_total: 500 },
      { orderStatus: 'REFUNDED', grandTotal: 700 },
      { status: 'SHIPPED', grand_total: 250, created_at: '2026-10-01' },
    ]);
    expect([s.totalSpend, s.orderCount]).toEqual([250, 1]);
  });

  it('says when the read hit the limit', () => {
    const many = Array.from({ length: CUSTOMER_SPEND_ORDER_LIMIT }, () => ({ orderStatus: 'DELIVERED', grandTotal: 10 }));
    const s = summariseCustomerSpend(many);
    expect(s.capped).toBe(true);
    expect(s.totalSpend).toBe(10 * CUSTOMER_SPEND_ORDER_LIMIT);
  });

  it('no orders is an honest zero', () => {
    expect(summariseCustomerSpend([])).toMatchObject({ totalSpend: 0, orderCount: 0, avgOrderValue: 0, capped: false });
  });
});
