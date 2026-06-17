// ============================================================================
// IMS 2.0 - posStore park/hold + logout-clear tests
// ============================================================================
// Covers the auto-park-before-idle-logout feature (PR #752):
//   - parkCurrentSale snapshots a non-empty cart to `ims-held-bills`, tagged
//     with held_by / store_id / auto so a shared terminal can scope recall.
//   - an empty cart parks nothing (returns null).
//   - clearAllOnLogout clears the in-progress draft + state but PRESERVES
//     `ims-held-bills` (so a parked cart survives logout for resume-after-login).

import { describe, it, expect, beforeEach } from 'vitest';
import { usePOSStore } from '../posStore';
import type { CartLineItem } from '../posStore';

const HELD_KEY = 'ims-held-bills';
const DRAFT_KEY = 'ims-pos-draft';

/**
 * Deterministic in-memory localStorage. The vitest runner is launched with a
 * defective `--localstorage-file` shim in this environment (clear/removeItem are
 * missing). The store's reads/writes are try/catch-wrapped, so a broken store
 * would silently swallow held-bill writes. Install a clean Storage-like mock so
 * the store and the test helpers share one working store. (Mirrors the helper
 * in hooks/__tests__/useIdleLogout.test.ts.)
 */
function installMemoryLocalStorage() {
  const store = new Map<string, string>();
  const mock: Storage = {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    removeItem: (k: string) => store.delete(k),
    setItem: (k: string, v: string) => store.set(k, String(v)),
  };
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: mock,
  });
}

function makeLineItem(overrides: Partial<CartLineItem> = {}): CartLineItem {
  return {
    id: `line-${Math.random().toString(36).slice(2, 8)}`,
    product_id: 'p1',
    name: 'Ray-Ban Aviator',
    sku: 'RB-AV-001',
    category: 'FRAME',
    unit_price: 1000,
    mrp: 1000,
    quantity: 1,
    is_optical: false,
    discount_percent: 0,
    discount_amount: 0,
    line_total: 1000,
    ...overrides,
  };
}

function readHeldBills(): any[] {
  try {
    const raw = localStorage.getItem(HELD_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

describe('posStore.parkCurrentSale', () => {
  beforeEach(() => {
    // Clean store + storage between tests so held-bill assertions are isolated.
    installMemoryLocalStorage();
    localStorage.clear();
    usePOSStore.getState().clearAllOnLogout();
  });

  it('parks a non-empty cart as a held bill tagged with held_by / store_id / auto', () => {
    const store = usePOSStore.getState();
    store.setStoreId('BV-BOK-01');
    store.addToCart({
      product_id: 'p1',
      name: 'Ray-Ban Aviator',
      sku: 'RB-AV-001',
      category: 'FRAME',
      unit_price: 1000,
      mrp: 1000,
      quantity: 1,
      is_optical: false,
    } as any);

    const id = usePOSStore
      .getState()
      .parkCurrentSale({ auto: true, heldBy: 'user-123' });

    expect(id).toBeTruthy();
    const bills = readHeldBills();
    expect(bills).toHaveLength(1);
    const bill = bills[0];
    expect(bill.id).toBe(id);
    expect(bill.held_by).toBe('user-123');
    expect(bill.store_id).toBe('BV-BOK-01');
    expect(bill.auto).toBe(true);
    expect(bill.reason).toBe('Auto-saved on inactivity logout');
    // Snapshot carries the cart verbatim.
    expect(Array.isArray(bill.state.cart)).toBe(true);
    expect(bill.state.cart).toHaveLength(1);
    expect(bill.state.cart[0].product_id).toBe('p1');
  });

  it('defaults auto=false and falls back to salesperson_id for held_by', () => {
    const store = usePOSStore.getState();
    store.setStoreId('BV-BOK-01');
    store.setSalesperson('sp-9', 'Asha');
    store.addToCart(makeLineItem() as any);

    const id = usePOSStore.getState().parkCurrentSale();

    expect(id).toBeTruthy();
    const bill = readHeldBills()[0];
    expect(bill.auto).toBe(false);
    expect(bill.reason).toBe('Held by cashier');
    expect(bill.held_by).toBe('sp-9'); // fell back to salesperson_id
  });

  it('parks nothing and returns null when the cart is empty', () => {
    const id = usePOSStore.getState().parkCurrentSale({ heldBy: 'user-123' });
    expect(id).toBeNull();
    expect(readHeldBills()).toHaveLength(0);
  });

  it('appends to existing held bills rather than overwriting them', () => {
    // Pre-seed a held bill from a different user.
    localStorage.setItem(
      HELD_KEY,
      JSON.stringify([{ id: 'hold-other', held_by: 'user-other', state: { cart: [] } }]),
    );

    const store = usePOSStore.getState();
    store.addToCart(makeLineItem() as any);
    usePOSStore.getState().parkCurrentSale({ heldBy: 'user-123' });

    const bills = readHeldBills();
    expect(bills).toHaveLength(2);
    expect(bills.map((b) => b.held_by)).toContain('user-other');
    expect(bills.map((b) => b.held_by)).toContain('user-123');
  });
});

describe('posStore.clearAllOnLogout', () => {
  beforeEach(() => {
    localStorage.clear();
    usePOSStore.getState().clearAllOnLogout();
  });

  it('clears the in-progress draft + state but PRESERVES held bills', () => {
    const store = usePOSStore.getState();
    store.addToCart(makeLineItem() as any);
    // Park the current sale so a held bill exists.
    usePOSStore.getState().parkCurrentSale({ auto: true, heldBy: 'user-123' });
    // Simulate the persisted in-progress draft existing.
    localStorage.setItem(DRAFT_KEY, JSON.stringify({ state: { cart: [{}] } }));

    expect(readHeldBills()).toHaveLength(1);

    usePOSStore.getState().clearAllOnLogout();

    // Held bill survives logout (resumable after re-login)...
    expect(readHeldBills()).toHaveLength(1);
    // ...but the in-progress draft is removed...
    expect(localStorage.getItem(DRAFT_KEY)).toBeNull();
    // ...and the in-memory cart is reset.
    expect(usePOSStore.getState().cart).toHaveLength(0);
  });
});
