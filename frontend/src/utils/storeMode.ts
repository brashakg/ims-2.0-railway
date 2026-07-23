// ============================================================================
// Store-mode helper (ONLINE store detection) — SHARED
// ----------------------------------------------------------------------------
// An ONLINE store (store_type === "ONLINE", e.g. BV-ONLINE-01 / WO-ONLINE-01)
// owns NO physical stock of its own — it sells from the pooled stock of every
// shop (reserve-on-order), has no till and no walk-ins. Physical framings
// (POS billing, stock value, till open, transfer destination, PO delivery)
// are therefore meaningless for such a store.
//
// W1.4 / RC-B: lifted from pages/inventory/storeMode.ts to utils/ so EVERY
// surface (shell banner, POS, purchase, till, transfers) shares the one
// testable source of truth. The old path re-exports from here.
// Mirrors backend/api/services/stores_util.py — keep the id list in sync.
// ============================================================================

/** Known ONLINE store ids (created 2026-07-20). Used as a fast-path so the
 *  online view renders instantly even before the store list has loaded — and
 *  as a fallback if a legacy store doc is missing its store_type. Prefer the
 *  store_type field; these ids are the belt-and-braces. */
export const ONLINE_STORE_IDS: ReadonlySet<string> = new Set([
  'BV-ONLINE-01',
  'WO-ONLINE-01',
]);

/** Minimal shape we need to classify a store. All fields optional so callers
 *  can pass a partial store row (the Inventory store dropdown only maps a few
 *  fields) without a cast. */
export interface StoreModeInput {
  id?: string | null;
  store_id?: string | null;
  store_type?: string | null;
  owns_stock?: boolean | null;
}

/** True when the given store is an ONLINE (stockless, pooled-fulfilment) store.
 *  Decision order: explicit store_type wins; then the known-id allow-list. */
export function isOnlineStore(store: StoreModeInput | null | undefined): boolean {
  if (!store) return false;
  if (String(store.store_type ?? '').trim().toUpperCase() === 'ONLINE') return true;
  const id = String(store.store_id ?? store.id ?? '').trim();
  return id !== '' && ONLINE_STORE_IDS.has(id);
}

/** True when the given store id is a known ONLINE store id. Lets a caller flip
 *  to the online view from just the active-store id (before the full store row
 *  is available). */
export function isOnlineStoreId(storeId: string | null | undefined): boolean {
  const id = String(storeId ?? '').trim();
  return id !== '' && ONLINE_STORE_IDS.has(id);
}
