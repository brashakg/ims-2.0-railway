// Money review 29 of #1264: a pending bill whose cart was emptied by hand is
// still parked with its key on every sign-out (menu, idle, a forced 401) and
// on a store switch; and the login page's "Clear cache" keeps the bill keys
// and held bills while clearing everything else.
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
import { billKeyEntry, billKeyFor, clearCacheKeepingBills } from '../posBillKey';

const heldBills = () => JSON.parse(localStorage.getItem('ims-held-bills') || '[]');

/** A bill whose press lost its answer, then the cashier emptied the cart. */
function emptiedPendingBill(): string {
  const s = usePOSStore.getState();
  s.addToCart({
    id: 'p-1', product_id: 'p-1', name: 'Frame', category: 'FRAME', quantity: 1,
    unit_price: 1000, price: 1000, mrp: 1000, discount_percent: 0,
  } as any);
  const key = billKeyFor(s.ensureBillId());
  usePOSStore.setState({ cart: [] } as any);
  return key;
}

describe('an emptied pending bill keeps its key', () => {
  beforeEach(() => {
    localStorage.clear();
    usePOSStore.setState({ store_id: '', salesperson_id: 'u1' } as any);
    usePOSStore.getState().resetTransaction();
  });

  it('a sign-out parks it with its key', () => {
    const key = emptiedPendingBill();
    usePOSStore.getState().clearAllOnLogout('u1', 'S1');
    expect(heldBills().map((b: any) => b.bill_key?.key)).toEqual([key]);
  });

  it('the idle sign-out parks it with its key', async () => {
    const { autoParkForIdleLogout } = await import('../../../hooks/idleSignOut');
    const key = emptiedPendingBill();
    autoParkForIdleLogout('u1');
    expect(heldBills().map((b: any) => b.bill_key?.key)).toEqual([key]);
  });

  it('a forced 401 sign-out parks it with its key', async () => {
    const { SIGNED_OUT_EVENT } = await import('../../../services/api/signOutEvent');
    const key = emptiedPendingBill();
    window.dispatchEvent(new CustomEvent(SIGNED_OUT_EVENT, { detail: { userId: 'u1', storeId: 'S1' } }));
    expect(heldBills().map((b: any) => b.bill_key?.key)).toEqual([key]);
  });

  it('a store switch parks it (parkIfPending says so only when it really parked)', () => {
    const key = emptiedPendingBill();
    expect(usePOSStore.getState().parkIfPending('u1', 'S1')).toBe(true);
    expect(heldBills().map((b: any) => b.bill_key?.key)).toEqual([key]);
  });

  it('a park that cannot be written reports false and the sign-out keeps the key', () => {
    emptiedPendingBill();
    const id = usePOSStore.getState().bill_id as string;
    const real = localStorage.setItem;
    localStorage.setItem = (k: string, v: string) => {
      if (k === 'ims-held-bills') throw new Error('quota');
      real(k, v);
    };
    try {
      expect(usePOSStore.getState().parkIfPending('u1', 'S1')).toBe(false);
      usePOSStore.getState().clearAllOnLogout('u1', 'S1');
      expect(billKeyEntry(id)?.pending).toBe(true);
    } finally {
      localStorage.setItem = real;
    }
  });
});

describe('the login page Clear cache', () => {
  it('keeps the bill keys and held bills, and clears everything else', () => {
    localStorage.clear();
    billKeyFor('bill-p');
    localStorage.setItem('ims-held-bills', JSON.stringify([{ id: 'h1', bill_key: { key: 'k', pending: true } }]));
    localStorage.setItem('ims_token', 't');
    localStorage.setItem('ims-pos-draft', '{}');
    clearCacheKeepingBills();
    expect(billKeyEntry('bill-p')?.pending).toBe(true);
    expect(heldBills()).toHaveLength(1);
    expect(localStorage.getItem('ims_token')).toBeNull();
    expect(localStorage.getItem('ims-pos-draft')).toBeNull();
  });
});
