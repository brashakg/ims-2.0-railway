// ============================================================================
// posStore — single item_note field + legacy `notes` migration on recall
// ============================================================================
// The cart line used to carry TWO note fields (`notes` from POSCart,
// `item_note` from the Review step); only `item_note` was ever sent. The
// store now has ONE field (`item_note`) and ONE setter (updateItemNote).
// Held bills are real staff work in live shops: a bill parked BEFORE the
// merge still has `notes` in its snapshot — recalling it must fold that into
// `item_note`, not lose it.

import { describe, it, expect, beforeEach } from 'vitest';

// Complete Map-backed localStorage for the persist middleware + held bills.
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

import { usePOSStore } from '../posStore';

function addItem() {
  usePOSStore.getState().addToCart({
    product_id: 'p1', name: 'Frame A', sku: 'FR-1', category: 'FRAMES',
    unit_price: 1000, mrp: 1000, quantity: 1, is_optical: true,
  } as any);
  return usePOSStore.getState().cart[0].id;
}

beforeEach(() => {
  localStorage.clear();
  usePOSStore.getState().resetTransaction();
});

describe('single note field', () => {
  it('updateItemNote writes item_note (the wire field the order sends)', () => {
    const id = addItem();
    usePOSStore.getState().updateItemNote(id, 'PD 62');
    const item = usePOSStore.getState().cart[0];
    expect(item.item_note).toBe('PD 62');
    // The legacy twin field must not come back.
    expect((item as any).notes).toBeUndefined();
  });

  it('the redundant second setter is gone — one setter remains', () => {
    expect((usePOSStore.getState() as any).setItemNote).toBeUndefined();
    expect(typeof usePOSStore.getState().updateItemNote).toBe('function');
  });

  it('park -> recall round-trips item_note', () => {
    const id = addItem();
    usePOSStore.getState().updateItemNote(id, 'tint grey 40%');
    usePOSStore.getState().parkCurrentSale({ heldBy: 'u1' });
    const bills = JSON.parse(localStorage.getItem('ims-held-bills') || '[]');
    usePOSStore.getState().resetTransaction();
    usePOSStore.getState().restoreHeldSale(bills[0].state);
    expect(usePOSStore.getState().cart[0].item_note).toBe('tint grey 40%');
  });
});

describe('legacy held-bill migration (parked BEFORE the notes/item_note merge)', () => {
  const legacyLine = (extra: Record<string, unknown>) => ({
    id: 'line-legacy-1', product_id: 'p9', name: 'Old Frame', sku: 'FR-9',
    category: 'FRAMES', unit_price: 500, mrp: 500, quantity: 1,
    is_optical: true, discount_percent: 0, discount_amount: 0, line_total: 500,
    ...extra,
  });

  it('REQUIREMENT: recalling an old snapshot folds legacy `notes` into item_note — the note is NOT lost', () => {
    usePOSStore.getState().restoreHeldSale({
      sale_type: 'prescription_order',
      cart: [legacyLine({ notes: 'PD 60 · old parked note' })],
    });
    const item = usePOSStore.getState().cart[0];
    expect(item.item_note).toBe('PD 60 · old parked note');
    expect((item as any).notes).toBeUndefined(); // twin field dropped on recall
  });

  it('when a legacy snapshot carries BOTH fields, the Review-step field (item_note) wins', () => {
    usePOSStore.getState().restoreHeldSale({
      sale_type: 'prescription_order',
      cart: [legacyLine({ notes: 'cart note', item_note: 'review note' })],
    });
    expect(usePOSStore.getState().cart[0].item_note).toBe('review note');
  });
});

describe('persist rehydrate migration (a DRAFT saved before the merge, not a held bill)', () => {
  // The verifier's B3 probe, ported: deleting the `migrateLegacyItemNote` map
  // inside onRehydrateStorage killed no test in this suite — the recall path
  // above covers HELD bills, but a cashier's in-progress draft rides the
  // zustand persist snapshot, which rehydrates through a different door on
  // page load. Same legacy shape, different entrance; both must migrate.
  it('folds a legacy `notes` field into item_note when the persisted draft rehydrates', () => {
    const persisted = {
      state: {
        cart: [
          {
            id: 'line-old-1', product_id: 'p1', name: 'Frame', sku: 'FR-1',
            category: 'FRAMES', unit_price: 1000, mrp: 1000, quantity: 1,
            line_total: 1000, is_optical: false,
            notes: 'PD 61 · pre-merge draft note',
          },
        ],
        payments: [],
      },
      version: 0,
    };
    localStorage.setItem('ims-pos-draft', JSON.stringify(persisted));

    // Re-run persist's rehydrate on the seeded snapshot.
    (usePOSStore as unknown as { persist: { rehydrate: () => void } }).persist.rehydrate();

    const line = usePOSStore.getState().cart[0] as Record<string, unknown>;
    // THE REQUIREMENT: the note survives, on the ONE field the order sends.
    expect(line.item_note).toBe('PD 61 · pre-merge draft note');
    // ...and the legacy twin does not come back.
    expect('notes' in line).toBe(false);
  });
});
