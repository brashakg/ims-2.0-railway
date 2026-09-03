// The loyalty tender must REACH THE ORDER.
//
// The burn-without-tender defect: submitPosOrder called /loyalty/redeem
// (atomically debiting the customer's points) and then SKIPPED the LOYALTY
// line in the addPayment loop — so the ORDER never recorded the leg, its
// balance_due stayed high by the redeemed value, and the customer both lost
// the points and still owed the same rupees. These tests assert on the
// TENDERS POSTED TO THE ORDER, not on any UI state — a button-state test
// cannot see this bug.
//
// Also pinned here: the burn-first ordering (redeem BEFORE the LOYALTY leg is
// recorded, so a tender is never banked against a burn that refused), the
// server's capped rupee_value winning over the UI line's amount, and the
// loud-warning contract when the burn fails (no leg, points untouched,
// balance honestly still due).
import { describe, it, expect, beforeEach, vi } from 'vitest';

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    orderApi: {
      ...(actual.orderApi as object),
      createOrder: vi.fn(),
      addPayment: vi.fn(),
    },
    loyaltyApi: {
      ...(actual.loyaltyApi as object),
      redeem: vi.fn(),
    },
    workshopApi: {
      ...(actual.workshopApi as object),
      createJob: vi.fn(),
    },
  };
});

import { orderApi, loyaltyApi } from '../../../services/api';
import { usePOSStore } from '../../../stores/posStore';
import { submitPosOrder } from '../submitOrder';

const mockedCreate = vi.mocked(orderApi.createOrder);
const mockedAddPayment = vi.mocked(orderApi.addPayment);
const mockedRedeem = vi.mocked(loyaltyApi.redeem);

const line = () =>
  ({
    id: 'a',
    product_id: 'p-a',
    name: 'Frame A',
    sku: 'SKU-A',
    category: 'FRAME',
    unit_price: 1000,
    mrp: 1000,
    quantity: 1,
    discount_percent: 0,
    discount_amount: 0,
    line_total: 1000,
  }) as any;

/** Posted addPayment bodies, in call order. */
const postedBodies = () => mockedAddPayment.mock.calls.map((c) => c[1] as any);
const loyaltyBodies = () => postedBodies().filter((b) => b.method === 'LOYALTY');

function seedStore(loyaltyRupees: number) {
  usePOSStore.setState({
    cart: [line()],
    customer: { id: 'cust-1', name: 'Asha' } as any,
    sale_type: 'quick_sale' as any,
    cart_discount_percent: 0,
    cart_discount_amount: 0,
    cash_tender: null,
    payments: [],
    pendingLoyaltyRedeem: null,
  } as any);
  const total = usePOSStore.getState().getGrandTotal();
  usePOSStore.setState({
    payments: [
      { method: 'LOYALTY', amount: loyaltyRupees, reference: `PENDING:${loyaltyRupees}pts`, timestamp: '' },
      { method: 'CASH', amount: Math.round((total - loyaltyRupees) * 100) / 100, timestamp: '' },
    ] as any,
    pendingLoyaltyRedeem: { points: loyaltyRupees, rupeeValue: loyaltyRupees, orderValue: total },
  } as any);
  return total;
}

describe('POS loyalty tender leg', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedCreate.mockResolvedValue({ order_id: 'ord-1', order_number: 'BV-001' } as any);
    mockedAddPayment.mockResolvedValue({ payment_id: 'pmt-1' } as any);
    mockedRedeem.mockResolvedValue({
      redeemed_points: 300,
      rupee_value: 300,
      was_capped: false,
      txn_id: 'txn-1',
    } as any);
  });

  it('KEY: a redeemed bill posts a LOYALTY leg matching the burn, cash lower by exactly that', async () => {
    const total = seedStore(300);

    const res = await submitPosOrder(usePOSStore.getState() as any, 'idem-1');
    expect(res.ok).toBe(true);
    expect(res.warning).toBeUndefined();

    // The burn ran, linked to the real order.
    expect(mockedRedeem).toHaveBeenCalledTimes(1);
    expect(mockedRedeem.mock.calls[0][0]).toMatchObject({
      customer_id: 'cust-1',
      order_id: 'ord-1',
      points: 300,
      order_value: total,
    });

    // THE ORDER'S TENDERS: a LOYALTY leg worth exactly the points burned...
    const loyalty = loyaltyBodies();
    expect(loyalty).toHaveLength(1);
    expect(loyalty[0].amount).toBe(300);
    expect(loyalty[0].reference).toContain('300pts');

    // ...and the cash leg lower by exactly that amount — the legs sum to the
    // bill total, so nothing is billed twice and nothing is left owing.
    const cash = postedBodies().filter((b) => b.method === 'CASH');
    expect(cash).toHaveLength(1);
    expect(cash[0].amount).toBeCloseTo(total - 300, 2);
    const postedSum = postedBodies().reduce((s, b) => s + b.amount, 0);
    expect(postedSum).toBeCloseTo(total, 2);
  });

  it('burn-first ordering: /loyalty/redeem completes BEFORE the LOYALTY leg is recorded', async () => {
    seedStore(300);
    await submitPosOrder(usePOSStore.getState() as any, 'idem-2');

    const redeemOrder = mockedRedeem.mock.invocationCallOrder[0];
    const loyaltyIdx = postedBodies().findIndex((b) => b.method === 'LOYALTY');
    expect(loyaltyIdx).toBeGreaterThanOrEqual(0);
    const legOrder = mockedAddPayment.mock.invocationCallOrder[loyaltyIdx];
    // A tender must never be banked against a burn that has not happened yet.
    expect(redeemOrder).toBeLessThan(legOrder);
  });

  it('a capped burn records the SERVER value, not the UI line, and warns about the gap', async () => {
    seedStore(300);
    mockedRedeem.mockResolvedValueOnce({
      redeemed_points: 200,
      rupee_value: 200,
      was_capped: true,
      txn_id: 'txn-2',
    } as any);

    const res = await submitPosOrder(usePOSStore.getState() as any, 'idem-3');
    expect(res.ok).toBe(true);

    const loyalty = loyaltyBodies();
    expect(loyalty).toHaveLength(1);
    // The recorded leg equals what was actually burned — not the ₹300 the
    // screen assumed. Anything else fabricates money on the order.
    expect(loyalty[0].amount).toBe(200);
    expect(res.warning).toMatch(/capped/i);
    expect(res.warning).toMatch(/100/); // the remaining amount to collect
  });

  it('a FAILED burn posts NO loyalty leg and warns loudly — points untouched, balance honestly due', async () => {
    seedStore(300);
    mockedRedeem.mockRejectedValueOnce(new Error('409 insufficient balance'));

    const res = await submitPosOrder(usePOSStore.getState() as any, 'idem-4');
    // Order still exists (fail-soft), but never silently.
    expect(res.ok).toBe(true);
    expect(res.warning).toMatch(/NOT debited/);
    expect(res.warning).toMatch(/300/);

    // No burn -> no leg. Recording one would gift the discount without
    // taking the points (the delivery-door class, inverted).
    expect(loyaltyBodies()).toHaveLength(0);
    // The other tenders still reach the order.
    expect(postedBodies().filter((b) => b.method === 'CASH')).toHaveLength(1);
    // The pending intent is consumed either way.
    expect(usePOSStore.getState().pendingLoyaltyRedeem).toBeNull();
  });

  it('a LOYALTY line with no pending intent behind it burns nothing, posts nothing, and is flagged', async () => {
    const total = seedStore(300);
    usePOSStore.setState({ pendingLoyaltyRedeem: null } as any);

    const res = await submitPosOrder(usePOSStore.getState() as any, 'idem-5');
    expect(res.ok).toBe(true);
    expect(mockedRedeem).not.toHaveBeenCalled();
    expect(loyaltyBodies()).toHaveLength(0);
    expect(res.warning).toMatch(/LOYALTY/);
    // The cash leg is untouched by the drift.
    const cash = postedBodies().filter((b) => b.method === 'CASH');
    expect(cash[0].amount).toBeCloseTo(total - 300, 2);
  });
});
