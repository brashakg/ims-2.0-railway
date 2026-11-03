// Money review 15 of #1264: the till's bill key travels with a held bill
// (manual hold and auto-park alike), comes back on recall freshly stamped,
// and a reset or a logout drops ONLY this bill's key.
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

import { describe, it, expect, beforeEach } from 'vitest';
import { usePOSStore } from '../../../stores/posStore';
import { BILL_KEY_TTL_MS, billKeyEntry, billKeyFor, settleAfterPress, settleBillKey } from '../posBillKey';

const putCart = () => usePOSStore.getState().addToCart({
  id: 'p-1', product_id: 'p-1', name: 'Frame', category: 'FRAME', quantity: 1,
  unit_price: 1000, price: 1000, mrp: 1000, discount_percent: 0,
} as any);
const heldBills = () => JSON.parse(localStorage.getItem('ims-held-bills') || '[]');

describe('the bill key and held bills', () => {
  beforeEach(() => {
    localStorage.clear();
    usePOSStore.setState({ store_id: 'S1', salesperson_id: 'u1' } as any);
    usePOSStore.getState().resetTransaction();
  });

  for (const auto of [false, true]) {
    it(`a ${auto ? 'auto-park' : 'manual hold'} carries the key in the held bill and does not clear it`, () => {
      putCart();
      const fp = usePOSStore.getState().ensureBillId();
      const key = billKeyFor(fp);
      usePOSStore.getState().parkCurrentSale({ auto, heldBy: 'u1' });
      expect(heldBills()[0].bill_key.key).toBe(key);
      expect(billKeyEntry(fp)?.key).toBe(key);
    });
  }

  it('a recall restores the held key, freshly stamped, for the recalled bill', () => {
    putCart();
    const key = billKeyFor(usePOSStore.getState().ensureBillId());
    usePOSStore.getState().parkCurrentSale({ heldBy: 'u1' });
    const held = heldBills()[0];
    usePOSStore.getState().resetTransaction();
    localStorage.removeItem('ims_pos_bill_keys'); // e.g. another tab, or long gone
    const before = Date.now();
    usePOSStore.getState().restoreHeldSale(held.state, { ...held.bill_key, at: before - BILL_KEY_TTL_MS + 1 });
    const entry = billKeyEntry(usePOSStore.getState().ensureBillId());
    expect(entry?.key).toBe(key);
    expect(entry!.at).toBeGreaterThanOrEqual(before);
  });

  it('a logout drops only this bill key, never another bill key', () => {
    putCart();
    const fp = usePOSStore.getState().ensureBillId();
    billKeyFor(fp);
    const other = billKeyFor('another-tab-bill');
    usePOSStore.getState().clearAllOnLogout();
    expect(billKeyEntry(fp)).toBeNull();
    expect(billKeyEntry('another-tab-bill')?.key).toBe(other);
  });
});

describe('the lost-sale promise ends with its key', () => {
  it('swaps to the CHECK words once the key is older than the TTL, and at once when it is gone', async () => {
    const { renderHook } = await import('@testing-library/react');
    const { vi } = await import('vitest');
    const { TILL_SALE_UNCONFIRMED, TILL_SALE_UNCONFIRMED_CHECK, useUnconfirmedWording } = await import('../posBillKey');
    vi.useFakeTimers();
    try {
      localStorage.clear();
      const now = Date.now();
      billKeyFor('bill-A', now - BILL_KEY_TTL_MS + 1000); // one second of life left
      settleBillKey('bill-A'); // a SETTLED key ages out; a pending one never does (money review 24)
      let msg: string | null = TILL_SALE_UNCONFIRMED;
      const set = (f: (m: string | null) => string | null) => { msg = f(msg); };
      renderHook(() => useUnconfirmedWording('bill-A', TILL_SALE_UNCONFIRMED, set));
      expect(msg).toBe(TILL_SALE_UNCONFIRMED);
      vi.advanceTimersByTime(1500);
      expect(msg).toBe(TILL_SALE_UNCONFIRMED_CHECK);
      msg = TILL_SALE_UNCONFIRMED;
      renderHook(() => useUnconfirmedWording('bill-gone', TILL_SALE_UNCONFIRMED, set));
      expect(msg).toBe(TILL_SALE_UNCONFIRMED_CHECK);
      msg = TILL_SALE_UNCONFIRMED;
      billKeyFor('bill-P', Date.now() - BILL_KEY_TTL_MS + 1000); // pending
      renderHook(() => useUnconfirmedWording('bill-P', TILL_SALE_UNCONFIRMED, set));
      vi.advanceTimersByTime(60 * 60_000);
      expect(msg).toBe(TILL_SALE_UNCONFIRMED); // a pending key keeps its promise
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('money review 17: the member is part of the bill; a reload keeps member and Rx', () => {
  beforeEach(() => {
    localStorage.clear();
    usePOSStore.setState({ store_id: 'S1', salesperson_id: 'u1' } as any);
    usePOSStore.getState().resetTransaction();
  });

  it('another family member with the same cart is another bill (a NEW key)', () => {
    // Money review 23: the key is filed under the bill's own id, so the
    // son's bill after the mother's sale is a new bill (a new id, a new key).
    putCart();
    usePOSStore.setState({ customer: { id: 'C1', name: 'Asha' }, patient: { id: 'MOTHER' } } as any);
    const key = billKeyFor(usePOSStore.getState().ensureBillId());
    usePOSStore.getState().resetTransaction();
    putCart();
    usePOSStore.setState({ customer: { id: 'C1', name: 'Asha' }, patient: { id: 'SON' } } as any);
    expect(billKeyFor(usePOSStore.getState().ensureBillId())).not.toBe(key);
  });

  it('a reload after an unconfirmed press restores the customer, member and Rx, and keeps the key', () => {
    putCart();
    usePOSStore.setState({ customer: { id: 'C1', name: 'Asha' }, patient: { id: 'P2' },
                           prescription: { id: 'RX1' }, sale_type: 'prescription_order' } as any);
    const key = billKeyFor(usePOSStore.getState().ensureBillId());
    const saved = (usePOSStore as any).persist.getOptions().partialize(usePOSStore.getState());
    usePOSStore.setState(JSON.parse(JSON.stringify(saved)));
    const s = usePOSStore.getState() as any;
    expect([s.customer?.id, s.patient?.id, s.prescription?.id]).toEqual(['C1', 'P2', 'RX1']);
    expect(billKeyFor(usePOSStore.getState().ensureBillId())).toBe(key);
  });

  it('with no press pending, the draft keeps no customer, member or Rx', () => {
    putCart();
    usePOSStore.setState({ customer: { id: 'C1', name: 'Asha' }, patient: { id: 'P2' },
                           prescription: { id: 'RX1' } } as any);
    const saved = (usePOSStore as any).persist.getOptions().partialize(usePOSStore.getState());
    expect([saved.customer, saved.patient, saved.prescription]).toEqual([null, null, null]);
  });
});


describe('money review 18: the draft keeps PII only while a press is pending', () => {
  beforeEach(() => {
    localStorage.clear();
    usePOSStore.setState({ store_id: 'S1', salesperson_id: 'u1' } as any);
    usePOSStore.getState().resetTransaction();
  });

  it('a refused press leaves no customer, member or Rx in the saved draft (after the save delay)', async () => {
    putCart();
    usePOSStore.setState({ customer: { id: 'C1', name: 'Meena' }, patient: { id: 'P1' },
                           prescription: { id: 'rx7' } } as any);
    const fp = usePOSStore.getState().ensureBillId();
    billKeyFor(fp); // the press
    usePOSStore.getState().setProcessing(true);
    await new Promise((r) => setTimeout(r, 650));
    expect(localStorage.getItem('ims-pos-draft') || '').toContain('Meena'); // pending: kept
    settleBillKey(fp); // a definite refusal
    usePOSStore.getState().setProcessing(false);
    await new Promise((r) => setTimeout(r, 650));
    const draft = localStorage.getItem('ims-pos-draft') || '';
    expect(draft).not.toContain('Meena');
    expect(draft).not.toContain('rx7');
  });
});


describe('money review 20: the promise of a lost debit leg ends with its key', () => {
  it('swaps to the check-the-Orders words once the bill changes or its key is gone', async () => {
    const { renderHook } = await import('@testing-library/react');
    const { TILL_DEBIT_CHECK, tillDebitLegUnconfirmed, useUnconfirmedWording } = await import('../posBillKey');
    localStorage.clear();
    billKeyFor('bill-D');
    const words = tillDebitLegUnconfirmed(['STORE_CREDIT Rs 100']);
    let msg: string | null = words;
    const set = (f: (m: string | null) => string | null) => { msg = f(msg); };
    renderHook(() => useUnconfirmedWording('bill-D', words, set));
    expect(msg).toBe(words); // its key is kept: the promise stands
    renderHook(() => useUnconfirmedWording('bill-CHANGED', words, set));
    expect(msg).toBe(TILL_DEBIT_CHECK);
  });
});

describe('money review 22: a manual logout keeps a pending press', () => {
  beforeEach(() => {
    localStorage.clear();
    usePOSStore.setState({ store_id: 'S1', salesperson_id: 'u1' } as any);
    usePOSStore.getState().resetTransaction();
  });

  it('parks the bill WITH its key when a press is pending, once (the idle park is not repeated)', () => {
    putCart();
    const fp = usePOSStore.getState().ensureBillId();
    const key = billKeyFor(fp); // a press is pending
    usePOSStore.getState().clearAllOnLogout('u1');
    expect(heldBills().map((b: any) => [b.bill_key?.key, b.held_by])).toEqual([[key, 'u1']]);
    expect(usePOSStore.getState().cart).toEqual([]);

    localStorage.clear();
    usePOSStore.getState().resetTransaction();
    putCart();
    const fp2 = usePOSStore.getState().ensureBillId();
    billKeyFor(fp2);
    usePOSStore.getState().parkCurrentSale({ auto: true, heldBy: 'u1' }); // the idle sign-out parks first
    usePOSStore.getState().clearAllOnLogout('u1');
    expect(heldBills()).toHaveLength(1);
  });

  it('a bill with nothing pending is not parked by a manual logout', () => {
    putCart();
    const fp = usePOSStore.getState().ensureBillId();
    billKeyFor(fp);
    settleBillKey(fp);
    usePOSStore.getState().clearAllOnLogout('u1');
    expect(heldBills()).toEqual([]);
  });
});

describe('money review 23: the key belongs to the bill, wherever and whenever it is pressed', () => {
  beforeEach(() => {
    localStorage.clear();
    usePOSStore.setState({ store_id: '', salesperson_id: 'u1' } as any);
    usePOSStore.getState().resetTransaction();
  });

  it('a pending bill parked at sign-out and recalled the next IST day presses with the SAME key', () => {
    putCart();
    usePOSStore.getState().setDeliveryDate('2026-11-02');
    const key = billKeyFor(usePOSStore.getState().ensureBillId()); // pressed, answer lost
    usePOSStore.getState().clearAllOnLogout('u1', 'S1');
    const [held] = heldBills();
    expect(held.bill_key).toMatchObject({ key, pending: true });
    usePOSStore.getState().restoreHeldSale(held.state, held.bill_key);
    usePOSStore.getState().setDeliveryDate('2026-11-03'); // the counter stamps today
    expect(billKeyEntry(usePOSStore.getState().ensureBillId())?.pending).toBe(true);
    expect(billKeyFor(usePOSStore.getState().ensureBillId())).toBe(key);
  });

  it('the parked bill names its store', () => {
    putCart();
    billKeyFor(usePOSStore.getState().ensureBillId());
    usePOSStore.getState().clearAllOnLogout('u1', 'S1');
    expect(heldBills()[0].store_id).toBe('S1');
  });

  it('a forced sign-out (a 401) runs the same step: the pending bill is parked, then cleared', async () => {
    const { SIGNED_OUT_EVENT } = await import('../../../services/api/signOutEvent');
    putCart();
    const key = billKeyFor(usePOSStore.getState().ensureBillId());
    window.dispatchEvent(new CustomEvent(SIGNED_OUT_EVENT, { detail: { userId: 'u1', storeId: 'S1' } }));
    expect(heldBills().map((b: any) => [b.bill_key?.key, b.held_by])).toEqual([[key, 'u1']]);
    expect(usePOSStore.getState().cart).toEqual([]);
  });
});

describe('money review 24: a pending key never expires, and the bill keeps its store', () => {
  beforeEach(() => {
    localStorage.clear();
    usePOSStore.setState({ store_id: '', salesperson_id: 'u1' } as any);
    usePOSStore.getState().resetTransaction();
  });
  const SIXTEEN_MIN = 16 * 60_000;

  it('a manual hold 16 minutes after a lost answer still carries the key, pending', () => {
    putCart();
    const key = billKeyFor(usePOSStore.getState().ensureBillId(), Date.now() - SIXTEEN_MIN);
    usePOSStore.getState().parkCurrentSale({ heldBy: 'u1' });
    expect(heldBills()[0].bill_key).toMatchObject({ key, pending: true });
  });

  it('the idle sign-out 16 minutes later parks it with its key', async () => {
    const { autoParkForIdleLogout } = await import('../../../hooks/idleSignOut');
    putCart();
    const key = billKeyFor(usePOSStore.getState().ensureBillId(), Date.now() - SIXTEEN_MIN);
    autoParkForIdleLogout('u1');
    expect(heldBills().map((b: any) => b.bill_key?.key)).toEqual([key]);
  });

  it('a 401 sign-out 16 minutes later parks it with its key', async () => {
    const { SIGNED_OUT_EVENT } = await import('../../../services/api/signOutEvent');
    putCart();
    const key = billKeyFor(usePOSStore.getState().ensureBillId(), Date.now() - SIXTEEN_MIN);
    window.dispatchEvent(new CustomEvent(SIGNED_OUT_EVENT, { detail: { userId: 'u1', storeId: 'S1' } }));
    expect(heldBills().map((b: any) => b.bill_key?.key)).toEqual([key]);
  });

  it('the bill records its store when it is made, and parks under it', () => {
    localStorage.setItem('ims_user', JSON.stringify({ id: 'u1', activeStoreId: 'S1' }));
    putCart();
    billKeyFor(usePOSStore.getState().ensureBillId());
    localStorage.setItem('ims_user', JSON.stringify({ id: 'u1', activeStoreId: 'S2' })); // switched
    usePOSStore.getState().parkCurrentSale({ heldBy: 'u1', storeId: 'S2' });
    expect(heldBills()[0].store_id).toBe('S1');
  });
});

describe('money review 25: THE one settle rule after a press', () => {
  beforeEach(() => localStorage.clear());

  it('a refusal before the order door keeps a PENDING key pending', () => {
    billKeyFor('b-p'); // an earlier press lost its answer
    settleAfterPress('b-p', true, { sent: false }); // e.g. the store guard, a failed quote
    expect(billKeyEntry('b-p')?.pending).toBe(true);
  });

  it('a refusal at the order door that says nothing about the key keeps it pending', () => {
    billKeyFor('b-s');
    settleAfterPress('b-s', true, { sent: true, definite: false }); // e.g. BILL_STORE_MISMATCH
    expect(billKeyEntry('b-s')?.pending).toBe(true);
  });

  it('a pending key settles only on a key_checked refusal; a key that was not pending settles on any refusal', () => {
    // Money review 26: a 4xx alone (a 403, a 422, a period lock refused
    // before the key was looked up) proves nothing about a pending key.
    billKeyFor('b-d');
    settleAfterPress('b-d', true, { sent: true, definite: true });
    expect(billKeyEntry('b-d')?.pending).toBe(true);
    settleAfterPress('b-d', true, { sent: true, definite: true, keyChecked: true });
    expect(billKeyEntry('b-d')?.pending).toBe(false);
    billKeyFor('b-f'); // not pending before this press: settles as before
    settleAfterPress('b-f', false, { sent: true, definite: true });
    expect(billKeyEntry('b-f')?.pending).toBe(false);
    billKeyFor('b-n'); // this press only
    settleAfterPress('b-n', false, { sent: false });
    expect(billKeyEntry('b-n')?.pending).toBe(false);
  });

  it('an order known to exist under the key keeps it pending', () => {
    billKeyFor('b-o');
    settleAfterPress('b-o', false, { sent: true, pending: true });
    expect(billKeyEntry('b-o')?.pending).toBe(true);
  });
});

describe('money review 26: a recalled bill always names its store', () => {
  beforeEach(() => {
    localStorage.clear();
    usePOSStore.setState({ store_id: '', salesperson_id: 'u1' } as any);
    usePOSStore.getState().resetTransaction();
  });

  it('a bill parked before any press, recalled, gets its store and sends it', async () => {
    const { orderPayloadOf } = await import('../orderPayload');
    localStorage.setItem('ims_user', JSON.stringify({ id: 'u1', activeStoreId: 'S1' }));
    putCart();
    usePOSStore.getState().parkCurrentSale({ heldBy: 'u1' }); // no press: no bill id, no store on the snapshot
    const [held] = heldBills();
    expect(held.state.bill_store_id ?? null).toBeNull();
    usePOSStore.getState().resetTransaction();
    usePOSStore.getState().restoreHeldSale(held.state, held.bill_key, held.store_id);
    expect(usePOSStore.getState().bill_store_id).toBe('S1');
    usePOSStore.setState({ bill_store_id: null } as any); // a snapshot from before the store was kept
    usePOSStore.getState().ensureBillId();
    expect(usePOSStore.getState().bill_store_id).toBe('S1');
    expect(orderPayloadOf(usePOSStore.getState()).bill_store_id).toBe('S1');
  });
});
