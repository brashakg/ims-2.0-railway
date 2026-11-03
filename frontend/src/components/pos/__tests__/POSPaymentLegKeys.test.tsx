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
    workshopApi: { createJob: vi.fn(async () => ({ job_id: 'JOB-1' })) },
  };
});

import { orderApi } from '../../../services/api';
import { buildApiError } from '../../../services/api/client';
import { usePOSStore } from '../../../stores/posStore';
import { submitPosOrder, TILL_SALE_UNCONFIRMED, TILL_SALE_UNCONFIRMED_CHECK } from '../submitOrder';
import { billKeyEntry, billKeyFor, clearBillKey, settleAfterPress, settleBillKey } from '../posBillKey';
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
    expect(mockedAddPayment.mock.calls.map((c) => c[2])).toEqual(['bill-1:leg:0:UPI', 'bill-1:leg:0:UPI', 'bill-1:leg:1:CASH']);
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

  it('never auto-retries a leg that debits store credit; the sale is not done and says not to take another tender', async () => {
    seed();
    usePOSStore.setState({ payments: [{ method: 'STORE_CREDIT', amount: 1000, timestamp: '' }] } as any);
    mockedAddPayment.mockRejectedValue(apiErr(undefined));
    const res = await submitPosOrder(usePOSStore.getState() as any, 'bill-3');
    expect(mockedAddPayment).toHaveBeenCalledTimes(1);
    // Money review 17: not ok -- the till keeps the bill key, so pressing again
    // re-sends THIS leg with its own key (the server finishes it once).
    expect(res.ok).toBe(false);
    expect(res.error).toMatch(/STORE_CREDIT ₹1,000 payment could not be confirmed\. Do NOT take another tender/);
  });

  it('a re-press re-sends the same debit leg key (it finishes once)', async () => {
    seed();
    usePOSStore.setState({ payments: [{ method: 'STORE_CREDIT', amount: 1000, timestamp: '' }] } as any);
    mockedAddPayment.mockRejectedValueOnce(apiErr(undefined)).mockResolvedValue({ payment_id: 'p' } as any);
    expect((await submitPosOrder(usePOSStore.getState() as any, 'bill-7')).ok).toBe(false);
    expect((await submitPosOrder(usePOSStore.getState() as any, 'bill-7')).ok).toBe(true);
    expect(mockedAddPayment.mock.calls.map((c) => c[2])).toEqual(['bill-7:leg:0:STORE_CREDIT', 'bill-7:leg:0:STORE_CREDIT']);
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
    // Money review 24: a PENDING key never expires; a settled one ages out after 15 minutes.
    expect(billKeyFor(bill(1), Date.now() + 16 * 60_000)).toBe(k1);
    settleBillKey(bill(2));
    expect(billKeyFor(bill(2), Date.now() + 16 * 60_000)).not.toBe(k2); // settled, older than 15 minutes
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

describe('money review 16: a same-key press while the first still saves', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('says the sale is still saving and not to change the bill', async () => {
    const { TILL_SALE_STILL_SAVING } = await import('../posBillKey');
    seed();
    mockedCreate.mockRejectedValueOnce(buildApiError({
      isAxiosError: true, message: 'x', config: { method: 'post' },
      response: { status: 409, data: { detail: { code: 'SALE_STILL_SAVING', message: 'still saving' } } },
    } as any));
    const res = await submitPosOrder(usePOSStore.getState() as any, 'bill-9');
    expect(res.ok).toBe(false);
    expect(res.error).toBe(TILL_SALE_STILL_SAVING);
  });
});


describe('money review 17: a replayed Rx sale gets its workshop job', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('a reload that restored the Rx replays the order and creates exactly one job', async () => {
    const { workshopApi } = await import('../../../services/api');
    seed();
    usePOSStore.setState({
      sale_type: 'prescription_order', prescription: { id: 'RX1' }, patient: { id: 'P2' },
      payments: [{ method: 'CASH', amount: 1000, timestamp: '' }],
      cart: [{ id: 'l', product_id: 'p-l', name: 'Lens', sku: 'SKU-L', category: 'OPTICAL_LENS', unit_price: 1000,
               mrp: 1000, quantity: 1, discount_percent: 0, discount_amount: 0, line_total: 1000 }],
    } as any);
    mockedCreate.mockResolvedValueOnce({ order_id: 'ord-1', order_number: 'BV-001' } as any); // the replay
    mockedAddPayment.mockResolvedValue({ payment_id: 'p' } as any);
    const res = await submitPosOrder(usePOSStore.getState() as any, 'bill-rx');
    expect(res.error).toBeUndefined();
    expect(res.ok).toBe(true);
    expect(vi.mocked(workshopApi.createJob)).toHaveBeenCalledTimes(1);
  });
});

describe('money review 21: the leg key names its tender', () => {
  it('swapping the tender on a lost debit leg is a new leg (the server rule then applies)', async () => {
    mockedCreate.mockResolvedValue({ order_id: 'ord-1', order_number: 'BV-001' } as any);
    seed();
    usePOSStore.setState({ payments: [{ method: 'STORE_CREDIT', amount: 1000, timestamp: '' }] } as any);
    mockedAddPayment.mockRejectedValueOnce(apiErr(undefined)).mockResolvedValue({ payment_id: 'p' } as any);
    await submitPosOrder(usePOSStore.getState() as any, 'bill-9');
    usePOSStore.setState({ payments: [{ method: 'GIFT_VOUCHER', amount: 1000, voucherCode: 'gv1', timestamp: '' }] } as any);
    await submitPosOrder(usePOSStore.getState() as any, 'bill-9');
    const keys = mockedAddPayment.mock.calls.map((c) => c[2]);
    expect(keys.slice(-2)).toEqual(['bill-9:leg:0:STORE_CREDIT', 'bill-9:leg:0:GIFT_VOUCHER:GV1']);
  });
});

describe('money review 22: a leg key is the line, not its position', () => {
  it('removing and re-adding a recorded cash line after a lost debit leg re-sends it under the SAME key', async () => {
    mockedCreate.mockResolvedValue({ order_id: 'ord-1', order_number: 'BV-001' } as any);
    seed();
    usePOSStore.setState({ payments: [] } as any);
    const st = () => usePOSStore.getState();
    st().addPayment({ method: 'CASH', amount: 400 } as any);
    st().addPayment({ method: 'STORE_CREDIT', amount: 600 } as any);
    mockedAddPayment.mockResolvedValueOnce({ payment_id: 'c' } as any) // CASH recorded
      .mockRejectedValueOnce(apiErr(undefined)) // STORE_CREDIT: the answer is lost
      .mockResolvedValue({ payment_id: 'p' } as any);
    await submitPosOrder(st() as any, 'bill-r');
    st().removePayment(0); // the cashier moves the cash line to the end
    st().addPayment({ method: 'CASH', amount: 400 } as any);
    await submitPosOrder(st() as any, 'bill-r');
    const sent = mockedAddPayment.mock.calls.map((c) => [(c[1] as any).method, c[2]]);
    const cashKeys = new Set(sent.filter(([m]) => m === 'CASH').map(([, k]) => k));
    const creditKeys = new Set(sent.filter(([m]) => m === 'STORE_CREDIT').map(([, k]) => k));
    expect(cashKeys.size).toBe(1);
    expect(creditKeys.size).toBe(1);
  });
});

describe('money review 24: a press never pays onto another bill', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.removeItem('ims_user');
  });

  it('a bill saved earlier with other items (409) posts no payment and is not complete', async () => {
    seed();
    mockedCreate.mockRejectedValueOnce(buildApiError({
      isAxiosError: true, message: 'x', config: { method: 'post' },
      response: { status: 409, data: { detail: { code: 'IDEMPOTENCY_BODY_MISMATCH',
        message: 'This bill was already saved as order BV-001 with different items - open it on the Orders screen; nothing new was charged.' } } },
    } as any));
    const res = await submitPosOrder(usePOSStore.getState() as any, 'bill-e');
    expect(res.ok).toBe(false);
    expect(res.pending).toBe(true); // an order exists under the key: it stays pending (money review 25)
    expect(res.error).toMatch(/already saved as order BV-001 with different items/);
    expect(mockedAddPayment).not.toHaveBeenCalled();
  });

  it('a replayed order whose total is not this bill takes no payment', async () => {
    seed();
    mockedCreate.mockResolvedValueOnce({ order_id: 'ord-1', order_number: 'BV-001', grand_total: 700 } as any);
    const res = await submitPosOrder(usePOSStore.getState() as any, 'bill-t');
    expect(res.ok).toBe(false);
    expect(res.error).toMatch(/BV-001 was saved with a total of ₹700/);
    expect(mockedAddPayment).not.toHaveBeenCalled();
  });

  it('a bill made at another store is not pressed here', async () => {
    seed();
    usePOSStore.setState({ bill_store_id: 'S1' } as any);
    localStorage.setItem('ims_user', JSON.stringify({ id: 'u1', activeStoreId: 'S2' }));
    const res = await submitPosOrder(usePOSStore.getState() as any, 'bill-s');
    expect(res.ok).toBe(false);
    expect(res.error).toMatch(/started at S1/);
    expect(mockedCreate).not.toHaveBeenCalled();
    usePOSStore.setState({ bill_store_id: null } as any);
  });
});

describe('money review 25: what a press tells the key', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.removeItem('ims_user');
  });
  const refused = (status: number, detail: unknown) => buildApiError({
    isAxiosError: true, message: 'x', config: { method: 'post' },
    response: { status, data: { detail } },
  } as any);

  it('only a key_checked refusal says the key was checked; the store refusal is not', async () => {
    seed();
    mockedCreate.mockRejectedValueOnce(refused(400, { message: 'Order must have at least one item', key_checked: true }));
    const a = await submitPosOrder(usePOSStore.getState() as any, 'k-a');
    expect([a.sent, a.definite, a.keyChecked]).toEqual([true, true, true]);
    mockedCreate.mockRejectedValueOnce(refused(409, { code: 'BILL_STORE_MISMATCH', message: 'started at S1' }));
    const b = await submitPosOrder(usePOSStore.getState() as any, 'k-b');
    expect([b.sent, b.keyChecked]).toEqual([true, false]);
  });

  it('a total that is not this bill keeps the key pending; the bill names its store to the server', async () => {
    seed();
    usePOSStore.setState({ bill_store_id: 'S1' } as any);
    mockedCreate.mockResolvedValueOnce({ order_id: 'ord-1', order_number: 'BV-001', grand_total: 700 } as any);
    const res = await submitPosOrder(usePOSStore.getState() as any, 'k-t');
    expect(res.pending).toBe(true);
    expect(res.error).toMatch(/start a new bill/);
    expect((mockedCreate.mock.calls[0][0] as any).bill_store_id).toBe('S1');
    usePOSStore.setState({ bill_store_id: null } as any);
  });
});

describe('money review 26: a refusal that proves nothing never settles a pending key', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.removeItem('ims_user');
  });
  const refused = (status: number, detail: unknown) => buildApiError({
    isAxiosError: true, message: 'x', config: { method: 'post' },
    response: { status, data: { detail } },
  } as any);
  const pressAfterLostAnswer = async (err: unknown, wasPending: boolean) => {
    seed();
    const fp = `bill-${Math.random()}`;
    billKeyFor(fp); // this press marks the key pending; wasPending says whether it already was
    mockedCreate.mockRejectedValueOnce(err);
    const res = await submitPosOrder(usePOSStore.getState() as any, 'k-r');
    settleAfterPress(fp, wasPending, res);
    return billKeyEntry(fp)?.pending;
  };

  it('a 429, a 403, a 422 without key_checked and a 400 period lock keep a pending key pending', async () => {
    expect(await pressAfterLostAnswer(refused(429, 'Too many requests'), true)).toBe(true);
    expect(await pressAfterLostAnswer(refused(403, 'Your role is not permitted to create orders.'), true)).toBe(true);
    expect(await pressAfterLostAnswer(refused(422, [{ msg: 'field required' }]), true)).toBe(true);
    expect(await pressAfterLostAnswer(refused(423, 'This period is locked.'), true)).toBe(true);
  });

  it('a key_checked refusal settles a pending key; any 4xx settles a key that was not pending', async () => {
    expect(await pressAfterLostAnswer(refused(423, { message: 'This period is locked.', key_checked: true }), true))
      .toBe(false);
    expect(await pressAfterLostAnswer(refused(403, 'Your role is not permitted to create orders.'), false)).toBe(false);
  });
});
