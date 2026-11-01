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
import { billFingerprint } from '../orderPayload';
import { BILL_KEY_TTL_MS, billKeyEntry, billKeyFor } from '../posBillKey';

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
      const fp = billFingerprint(usePOSStore.getState());
      const key = billKeyFor(fp);
      usePOSStore.getState().parkCurrentSale({ auto, heldBy: 'u1' });
      expect(heldBills()[0].bill_key.key).toBe(key);
      expect(billKeyEntry(fp)?.key).toBe(key);
    });
  }

  it('a recall restores the held key, freshly stamped, for the recalled bill', () => {
    putCart();
    const key = billKeyFor(billFingerprint(usePOSStore.getState()));
    usePOSStore.getState().parkCurrentSale({ heldBy: 'u1' });
    const held = heldBills()[0];
    usePOSStore.getState().resetTransaction();
    localStorage.removeItem('ims_pos_bill_keys'); // e.g. another tab, or long gone
    const before = Date.now();
    usePOSStore.getState().restoreHeldSale(held.state, { ...held.bill_key, at: before - BILL_KEY_TTL_MS + 1 });
    const entry = billKeyEntry(billFingerprint(usePOSStore.getState()));
    expect(entry?.key).toBe(key);
    expect(entry!.at).toBeGreaterThanOrEqual(before);
  });

  it('a logout drops only this bill key, never another bill key', () => {
    putCart();
    const fp = billFingerprint(usePOSStore.getState());
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
      let msg: string | null = TILL_SALE_UNCONFIRMED;
      const set = (f: (m: string | null) => string | null) => { msg = f(msg); };
      renderHook(() => useUnconfirmedWording('bill-A', TILL_SALE_UNCONFIRMED, set));
      expect(msg).toBe(TILL_SALE_UNCONFIRMED);
      vi.advanceTimersByTime(1500);
      expect(msg).toBe(TILL_SALE_UNCONFIRMED_CHECK);
      msg = TILL_SALE_UNCONFIRMED;
      renderHook(() => useUnconfirmedWording('bill-gone', TILL_SALE_UNCONFIRMED, set));
      expect(msg).toBe(TILL_SALE_UNCONFIRMED_CHECK);
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
    putCart();
    usePOSStore.setState({ customer: { id: 'C1', name: 'Asha' }, patient: { id: 'MOTHER' } } as any);
    const key = billKeyFor(billFingerprint(usePOSStore.getState()));
    usePOSStore.setState({ patient: { id: 'SON' } } as any);
    expect(billKeyFor(billFingerprint(usePOSStore.getState()))).not.toBe(key);
  });

  it('a reload after an unconfirmed press restores the customer, member and Rx, and keeps the key', () => {
    putCart();
    usePOSStore.setState({ customer: { id: 'C1', name: 'Asha' }, patient: { id: 'P2' },
                           prescription: { id: 'RX1' }, sale_type: 'prescription_order' } as any);
    const key = billKeyFor(billFingerprint(usePOSStore.getState()));
    const saved = (usePOSStore as any).persist.getOptions().partialize(usePOSStore.getState());
    usePOSStore.setState(JSON.parse(JSON.stringify(saved)));
    const s = usePOSStore.getState() as any;
    expect([s.customer?.id, s.patient?.id, s.prescription?.id]).toEqual(['C1', 'P2', 'RX1']);
    expect(billKeyFor(billFingerprint(usePOSStore.getState()))).toBe(key);
  });

  it('with no press pending, the draft keeps no customer, member or Rx', () => {
    putCart();
    usePOSStore.setState({ customer: { id: 'C1', name: 'Asha' }, patient: { id: 'P2' },
                           prescription: { id: 'RX1' } } as any);
    const saved = (usePOSStore as any).persist.getOptions().partialize(usePOSStore.getState());
    expect([saved.customer, saved.patient, saved.prescription]).toEqual([null, null, null]);
  });
});
