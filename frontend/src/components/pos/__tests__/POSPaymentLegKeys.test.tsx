// Money review 12 of #1264: every till payment leg carries its own
// Idempotency-Key (`<bill key>:leg:<i>`), so a leg whose answer was lost is
// sent ONCE more safely, and anything that is not a refusal says "could not
// be confirmed - check the order's payments", never "did NOT record".
// jsdom's localStorage in this runner has no working setItem: a Map-backed
// stand-in (the repo's established answer), installed before any import.
(() => {
  const m = new Map<string, string>();
  const ls = {
    getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
    setItem: (k: string, v: string) => { m.set(k, String(v)); },
    removeItem: (k: string) => { m.delete(k); },
    clear: () => { m.clear(); },
    key: (i: number) => Array.from(m.keys())[i] ?? null,
    get length() { return m.size; },
  };
  Object.defineProperty(globalThis, 'localStorage', { value: ls, configurable: true, writable: true });
})();

import { describe, it, expect, beforeEach, vi } from 'vitest';

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    orderApi: {
      ...(actual.orderApi as object),
      createOrder: vi.fn(),
      addPayment: vi.fn(),
      quoteBill: vi.fn(async () => ({
        subtotal: 1000, taxable: 952.38, tax: 47.62, total_discount: 0, grand_total: 1000, round_off: 0,
      })),
    },
  };
});

import { orderApi } from '../../../services/api';
import { buildApiError } from '../../../services/api/client';
import { usePOSStore } from '../../../stores/posStore';
import { submitPosOrder, TILL_SALE_UNCONFIRMED, TILL_SALE_UNCONFIRMED_CHECK } from '../submitOrder';
import { billKeyEntry, billKeyFor, clearBillKey } from '../posBillKey';
import { billFingerprint, orderPayloadOf } from '../orderPayload';

const mockedCreate = vi.mocked(orderApi.createOrder);
const mockedAddPayment = vi.mocked(orderApi.addPayment);
const apiErr = (status: number | undefined, detail?: string) => buildApiError({
  isAxiosError: true, message: 'x', config: { method: 'post' },
  response: status === undefined ? undefined : { status, data: { detail } },
} as any);

function seed() {
  usePOSStore.setState({
    cart: [{ id: 'a', product_id: 'p-a', name: 'Frame A', sku: 'SKU-A', category: 'FRAME', unit_price: 1000,
             mrp: 1000, quantity: 1, discount_percent: 0, discount_amount: 0, line_total: 1000 }],
    customer: { id: 'cust-1', name: 'Asha' }, sale_type: 'quick_sale', cart_discount_percent: 0,
    cart_discount_amount: 0, cash_tender: null, pendingLoyaltyRedeem: null,
    payments: [{ method: 'UPI', amount: 600, timestamp: '' }, { method: 'CASH', amount: 400, timestamp: '' }],
  } as any);
}

describe('till payment legs', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedCreate.mockResolvedValue({ order_id: 'ord-1', order_number: 'BV-001' } as any);
  });

  it('sends each leg with its own key and retries a lost answer once with the same key', async () => {
    seed();
    mockedAddPayment.mockRejectedValueOnce(apiErr(undefined)).mockResolvedValue({ payment_id: 'p' } as any);
    const res = await submitPosOrder(usePOSStore.getState() as any, 'bill-1');
    expect(res.ok).toBe(true);
    expect(res.warning).toBeUndefined();
    expect(mockedAddPayment.mock.calls.map((c) => c[2])).toEqual(['bill-1:leg:0', 'bill-1:leg:0', 'bill-1:leg:1']);
  });

  it('says a twice-lost leg could not be confirmed; a refusal is never re-sent', async () => {
    seed();
    mockedAddPayment
      .mockRejectedValueOnce(apiErr(500)).mockRejectedValueOnce(apiErr(500))
      .mockRejectedValueOnce(apiErr(400, 'Amount exceeds balance due'));
    const res = await submitPosOrder(usePOSStore.getState() as any, 'bill-2');
    expect(mockedAddPayment).toHaveBeenCalledTimes(3); // UPI twice, CASH once (refused)
    expect(res.warning).toMatch(/UPI ₹600 could not be confirmed - check the order's payments before adding it again/);
    expect(res.warning).toMatch(/CASH ₹400 \(Amount exceeds balance due\) did NOT record/);
    expect(res.warning).not.toMatch(/UPI ₹600[^.]*did NOT record/);
  });
});

describe('money review 13 at the till', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedCreate.mockResolvedValue({ order_id: 'ord-1', order_number: 'BV-001' } as any);
  });

  it('never auto-retries a leg that debits store credit; says to check instead', async () => {
    seed();
    usePOSStore.setState({ payments: [{ method: 'STORE_CREDIT', amount: 1000, timestamp: '' }] } as any);
    mockedAddPayment.mockRejectedValue(apiErr(undefined));
    const res = await submitPosOrder(usePOSStore.getState() as any, 'bill-3');
    expect(mockedAddPayment).toHaveBeenCalledTimes(1);
    expect(res.warning).toMatch(/STORE_CREDIT ₹1,000 could not be confirmed - check the order's payments/);
  });

  it('a lost sale promises a safe re-press ONLY while the server guarantees the replay', async () => {
    const quote = vi.mocked(orderApi.quoteBill);
    for (const [guaranteed, words] of [[true, TILL_SALE_UNCONFIRMED], [false, TILL_SALE_UNCONFIRMED_CHECK]] as const) {
      seed();
      quote.mockResolvedValueOnce({ subtotal: 1000, taxable: 952.38, tax: 47.62, total_discount: 0,
                                    grand_total: 1000, round_off: 0, replay_guaranteed: guaranteed } as any);
      mockedCreate.mockRejectedValueOnce(apiErr(undefined));
      const res = await submitPosOrder(usePOSStore.getState() as any, 'bill-4');
      expect(res.ok).toBe(false);
      expect(res.error).toBe(words);
      expect(res.error).not.toMatch(/^Failed to create order/);
    }
  });
});

describe('the bill key lives with the bill (money review 15)', () => {
  const bill = (qty: number) => billFingerprint({
    customer: { id: 'c' }, store_id: 's', cart: [{ product_id: 'p', category: 'FRAME', quantity: qty, unit_price: 100 }],
  });

  it('survives a reload for the same bill, differs per bill, and expires', () => {
    localStorage.clear();
    const k1 = billKeyFor(bill(1));
    expect(billKeyFor(bill(1))).toBe(k1); // a reload reads the same key back
    const k2 = billKeyFor(bill(2));
    expect(k2).not.toBe(k1);
    expect(billKeyFor(bill(1))).toBe(k1); // the other bill's key was never overwritten
    expect(billKeyFor(bill(2), Date.now() + 16 * 60_000)).not.toBe(k2); // older than 15 minutes: a new key
  });

  it('each press refreshes the key time, so a bill re-pressed for 20 minutes keeps it', () => {
    localStorage.clear();
    const t0 = Date.now();
    const k = billKeyFor(bill(1), t0);
    expect(billKeyFor(bill(1), t0 + 10 * 60_000)).toBe(k);
    expect(billKeyFor(bill(1), t0 + 20 * 60_000)).toBe(k);
  });

  it('prefers the surface in-memory key for the bill over a kept or a fresh one', () => {
    localStorage.clear();
    billKeyFor(bill(1));
    expect(billKeyFor(bill(1), Date.now(), 'mem-key')).toBe('mem-key');
    expect(billKeyEntry(bill(1))?.key).toBe('mem-key');
  });

  it('clearing one bill never wipes another tab key', () => {
    localStorage.clear();
    const mine = billKeyFor(bill(1));
    const theirs = billKeyFor(bill(2));
    clearBillKey(bill(1));
    expect(billKeyEntry(bill(1))).toBeNull();
    expect(billKeyEntry(bill(2))?.key).toBe(theirs);
    expect(billKeyFor(bill(1))).not.toBe(mine);
  });

  it('ONE fingerprint: it is the order body the sale sends, minus the approval id', () => {
    const store = { customer: { id: 'c' }, store_id: 's', cart: [{ product_id: 'p', category: 'FRAME', quantity: 1, unit_price: 100 }] };
    expect(billFingerprint(store)).toBe(JSON.stringify(orderPayloadOf(store)));
    expect(billFingerprint(store)).toBe(JSON.stringify(orderPayloadOf(store, null)));
    expect(JSON.stringify(orderPayloadOf(store, 'REQ-1'))).not.toBe(billFingerprint(store));
    // A field the sale sends (the bill note) makes it another bill.
    expect(billFingerprint({ ...store, cart_note: 'gift' })).not.toBe(billFingerprint(store));
  });
});
