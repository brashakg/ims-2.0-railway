// Money review 30 of #1264: ONE guard decides when the till's bill (its key
// and its saved draft) may be dropped. A bill whose press is pending is
// dropped only after a success, a confirmed Discard, or a park that really
// stored it -- so every caller (Hold, Recall, sign-out, store switch) keeps
// it REACHABLE (on the till or in a held bill) when its park fails.
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

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import * as fs from 'node:fs';
import * as path from 'node:path';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', activeStoreId: 'S1' } }),
}));

import { HeldBillsControls } from '../HeldBillsControls';
import { useHeldBills } from '../useHeldBills';
import { usePOSStore } from '../../../stores/posStore';
import { billKeyEntry, billKeyFor, COULD_NOT_PUT_ASIDE } from '../posBillKey';

const heldBills = () => JSON.parse(localStorage.getItem('ims-held-bills') || '[]');
const frame = () => usePOSStore.getState().addToCart({
  id: 'p-1', product_id: 'p-1', name: 'Frame', category: 'FRAME', quantity: 1,
  unit_price: 1000, price: 1000, mrp: 1000, discount_percent: 0,
} as any);
const pendingBill = (empty = false) => {
  frame();
  billKeyFor(usePOSStore.getState().ensureBillId());
  if (empty) usePOSStore.setState({ cart: [] } as any);
};
/** The bill is still reachable: on the till with its pending key, or held with it. */
const reachable = () => {
  const s = usePOSStore.getState();
  return (billKeyEntry(s.bill_id || '')?.pending === true && s.bill_id !== null)
    || heldBills().some((b: any) => b.bill_key?.pending);
};
let realSetItem: typeof localStorage.setItem;
const failParks = () => {
  realSetItem = localStorage.setItem;
  localStorage.setItem = (k: string, v: string) => {
    if (k === 'ims-held-bills') throw new Error('quota');
    realSetItem(k, v);
  };
};

beforeEach(() => {
  localStorage.clear();
  usePOSStore.setState({ store_id: '', salesperson_id: 'u1' } as any);
  usePOSStore.getState().resetTransaction('discard');
});
afterEach(() => {
  if (realSetItem) localStorage.setItem = realSetItem;
  vi.restoreAllMocks();
});

describe('THE guard', () => {
  it('refuses to drop a pending bill without a safe reason, and touches nothing', () => {
    pendingBill();
    const id = usePOSStore.getState().bill_id;
    expect(usePOSStore.getState().resetTransaction('not-pending')).toBe(false);
    expect(usePOSStore.getState().resetTransaction(undefined as any)).toBe(false);
    expect(usePOSStore.getState().cart).toHaveLength(1);
    expect(usePOSStore.getState().bill_id).toBe(id);
    expect(billKeyEntry(id || '')?.pending).toBe(true);
    expect(usePOSStore.getState().resetTransaction('discard')).toBe(true);
    expect(usePOSStore.getState().cart).toHaveLength(0);
  });

  it("a 'success' for ANOTHER bill never drops a pending bill; for this bill it does", () => {
    pendingBill();
    const id = usePOSStore.getState().bill_id;
    expect(usePOSStore.getState().resetTransaction('success', 'another-bill')).toBe(false);
    expect(usePOSStore.getState().resetTransaction('success')).toBe(false);
    expect(usePOSStore.getState().cart).toHaveLength(1);
    expect(usePOSStore.getState().resetTransaction('success', id)).toBe(true);
  });

  it('a recall never replaces a pending live bill without a safe reason', () => {
    pendingBill();
    expect(usePOSStore.getState().restoreHeldSale({ cart: [] }, null, 'S1', 'not-pending')).toBe(false);
    expect(usePOSStore.getState().cart).toHaveLength(1);
    expect(reachable()).toBe(true);
  });
});

describe('every caller keeps a pending bill reachable when its park fails', () => {
  it('Hold: refused with the words, the bill stays on the till', () => {
    pendingBill();
    const alert = vi.spyOn(window, 'alert').mockImplementation(() => undefined);
    failParks();
    const { result } = renderHook(() => useHeldBills(usePOSStore.getState(), 'u1', 'S1'));
    act(() => { result.current.holdCurrentBill(); });
    expect(alert).toHaveBeenCalledWith(COULD_NOT_PUT_ASIDE);
    expect(usePOSStore.getState().cart).toHaveLength(1);
    expect(reachable()).toBe(true);
  });

  it('Recall: refused, the live bill stays on the till', () => {
    localStorage.setItem('ims-held-bills', JSON.stringify([{
      id: 'h1', customer: 'Asha', items: 1, total: 1000, heldAt: '', held_by: 'u1', store_id: 'S1',
      state: { cart: [] }, bill_key: null,
    }]));
    pendingBill();
    const alert = vi.spyOn(window, 'alert').mockImplementation(() => undefined);
    failParks();
    const { result } = renderHook(() => useHeldBills(usePOSStore.getState(), 'u1', 'S1'));
    let ok = true;
    act(() => { ok = result.current.recallBill('h1'); });
    expect(ok).toBe(false);
    expect(alert).toHaveBeenCalledWith(COULD_NOT_PUT_ASIDE);
    expect(usePOSStore.getState().cart).toHaveLength(1);
    expect(reachable()).toBe(true);
  });

  it('sign-out: the bill, its key and its draft all stay', () => {
    pendingBill();
    localStorage.setItem('ims-pos-draft', '{"state":{"cart":[1]}}');
    failParks();
    expect(usePOSStore.getState().clearAllOnLogout('u1', 'S1')).toBe(false);
    expect(localStorage.getItem('ims-pos-draft')).not.toBeNull();
    expect(usePOSStore.getState().cart).toHaveLength(1);
    expect(reachable()).toBe(true);
  });
});

describe('an emptied pending live bill', () => {
  it('can be held: it goes aside with its key and the till clears', () => {
    pendingBill(true);
    render(<HeldBillsControls />);
    const hold = screen.getByRole('button', { name: 'Hold bill' }) as HTMLButtonElement;
    expect(hold.disabled).toBe(false);
    fireEvent.click(hold);
    expect(heldBills().map((b: any) => b.bill_key?.pending)).toEqual([true]);
    expect(usePOSStore.getState().bill_id).toBeNull();
  });

  it('can be discarded, after the may-already-be-paid confirm', () => {
    pendingBill(true);
    const id = usePOSStore.getState().bill_id || '';
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<HeldBillsControls />);
    const discard = screen.getByRole('button', { name: /Discard bill/ }) as HTMLButtonElement;
    expect(discard.disabled).toBe(false);
    fireEvent.click(discard);
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/may already be paid/));
    expect(billKeyEntry(id)).toBeNull();
  });
});

describe('no caller can skip the guard', () => {
  const SRC = path.resolve(__dirname, '../../..');
  const files = (dir: string): string[] => fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return e.name === '__tests__' || e.name === 'node_modules' ? [] : files(p);
    return /\.(ts|tsx)$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [p] : [];
  });

  it('every resetTransaction / restoreHeldSale call names its reason, and keys are dropped only in the store', () => {
    const bad: string[] = [];
    for (const f of files(SRC)) {
      const rel = path.relative(SRC, f).replace(/\\/g, '/');
      const lines = fs.readFileSync(f, 'utf-8').split(/\r?\n/);
      lines.forEach((line, i) => {
        const code = line.replace(/\/\/.*$/, '');
        if (/\.resetTransaction\(\s*\)/.test(code)) bad.push(`${rel}:${i + 1} resetTransaction without a reason`);
        const restore = code.match(/\.restoreHeldSale\(([^)]*)\)/);
        if (restore && restore[1].split(',').length < 4) bad.push(`${rel}:${i + 1} restoreHeldSale without a reason`);
        if (/\bclearBillKey\(/.test(code) && !/export function clearBillKey/.test(code)
            && rel !== 'stores/posStore.ts') {
          bad.push(`${rel}:${i + 1} drops a bill key outside the store's guard`);
        }
      });
    }
    expect(bad).toEqual([]);
  });
});
