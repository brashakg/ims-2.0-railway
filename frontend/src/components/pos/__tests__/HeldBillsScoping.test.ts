// ===========================================================================
// A parked bill belongs to ONE cashier
// ===========================================================================
// Held bills survive logout, so an auto-parked cart can be resumed after
// re-login. On a shared shop terminal that makes ownership a real rule, not a
// nicety: cashier B must never see, recall or discard cashier A's parked cart,
// and a legacy bill with no owner recorded must be treated as NOT yours rather
// than as everyone's.
//
// The logic used to live inside POSLayout, so the new till had no hold/recall
// at all and an auto-parked cart there could never be brought back. It is now
// one shared hook; these tests pin the scoping rules that are easy to get
// subtly wrong and impossible to notice on a single-user machine.

// jsdom's localStorage in this runner has no clear(); the repo's established
// answer is a complete Map-backed stand-in installed before the import graph
// touches storage (same helper as stores/__tests__/posStore.itemNote.test.ts).
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
import { renderHook, act } from '@testing-library/react';
import { useHeldBills } from '../useHeldBills';

const KEY = 'ims-held-bills';
const ME = 'user-me';
const THEM = 'user-them';

function bill(id: string, held_by: string | null, extra: Record<string, unknown> = {}) {
  return { id, customer: 'C', items: 1, total: 100, heldAt: '', held_by, state: { id }, ...extra };
}

function seed(...bills: unknown[]) {
  localStorage.setItem(KEY, JSON.stringify(bills));
}

function stored(): any[] {
  return JSON.parse(localStorage.getItem(KEY) || '[]');
}

function fakeStore() {
  return {
    parkCurrentSale: vi.fn(),
    resetTransaction: vi.fn(),
    restoreHeldSale: vi.fn(),
  };
}

beforeEach(() => localStorage.clear());

describe('held bills are scoped to the cashier', () => {
  it('lists only my own parked bills', () => {
    seed(bill('mine', ME), bill('theirs', THEM));
    const { result } = renderHook(() => useHeldBills(fakeStore(), ME));
    expect(result.current.heldBills.map((b) => b.id)).toEqual(['mine']);
  });

  it('hides a legacy bill that records no owner', () => {
    // A missing owner is not a shared owner.
    seed(bill('legacy', null));
    const { result } = renderHook(() => useHeldBills(fakeStore(), ME));
    expect(result.current.heldBills).toHaveLength(0);
  });

  it('refuses to recall another cashier`s bill, and leaves it in storage', () => {
    seed(bill('theirs', THEM));
    const store = fakeStore();
    const { result } = renderHook(() => useHeldBills(store, ME));
    let ok = true;
    act(() => { ok = result.current.recallBill('theirs') as boolean; });
    expect(ok).toBe(false);
    expect(store.restoreHeldSale).not.toHaveBeenCalled();
    expect(stored().map((b) => b.id)).toEqual(['theirs']);
  });

  it('refuses to discard another cashier`s bill', () => {
    seed(bill('theirs', THEM));
    const { result } = renderHook(() => useHeldBills(fakeStore(), ME));
    act(() => { result.current.discardBill('theirs'); });
    expect(stored().map((b) => b.id)).toEqual(['theirs']);
  });

  it('recalls my own bill and removes ONLY that one', () => {
    // The negative control for the two refusals above: without this, a hook
    // that refused everything would pass them both.
    seed(bill('mine', ME), bill('theirs', THEM), bill('mine2', ME));
    const store = fakeStore();
    const { result } = renderHook(() => useHeldBills(store, ME));
    let ok = false;
    act(() => { ok = result.current.recallBill('mine') as boolean; });
    expect(ok).toBe(true);
    expect(store.restoreHeldSale).toHaveBeenCalledWith({ id: 'mine' });
    expect(stored().map((b) => b.id).sort()).toEqual(['mine2', 'theirs']);
  });

  it('holding parks through the store and then clears the till', () => {
    const store = fakeStore();
    const { result } = renderHook(() => useHeldBills(store, ME));
    act(() => { result.current.holdCurrentBill(); });
    expect(store.parkCurrentSale).toHaveBeenCalledWith({ heldBy: ME });
    // Order matters: park first, THEN reset, or the snapshot is of an empty cart.
    expect(store.parkCurrentSale.mock.invocationCallOrder[0])
      .toBeLessThan(store.resetTransaction.mock.invocationCallOrder[0]);
  });

  it('survives unparseable storage instead of breaking the till', () => {
    localStorage.setItem(KEY, 'not json{');
    const { result } = renderHook(() => useHeldBills(fakeStore(), ME));
    expect(result.current.heldBills).toEqual([]);
  });
});
