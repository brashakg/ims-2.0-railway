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
