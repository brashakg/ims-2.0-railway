// ============================================================================
// useIsOnlineStore — "is the (active) store an ONLINE store?" hook
// ----------------------------------------------------------------------------
// W1.4 / OS-029: the shared React signal for the ONLINE store-type guard rails.
// Detection prefers the store doc's store_type (from the cached GET /stores
// react-query list — 30-min stale, deduped app-wide via useStores), with the
// known online-store ids as an instant fast-path so guarded surfaces flip
// BEFORE the store list has loaded. Pass a storeId to test a specific store;
// omit it to test the signed-in user's ACTIVE store.
// ============================================================================

import { useAuth } from '../context/AuthContext';
import { useStores } from './usePOSQueries';
import { isOnlineStore, isOnlineStoreId } from '../utils/storeMode';

export function useIsOnlineStore(storeId?: string | null): boolean {
  const { user } = useAuth();
  // NOTE: hooks must run unconditionally — resolve the target id after.
  const { data: stores } = useStores();
  const targetId = String(storeId ?? user?.activeStoreId ?? '').trim();
  if (!targetId) return false;
  if (isOnlineStoreId(targetId)) return true;
  const list: unknown[] = Array.isArray(stores) ? stores : [];
  const row = list.find((s) => {
    const r = s as Record<string, unknown>;
    return (r.store_id || r.id || r._id) === targetId;
  }) as Record<string, unknown> | undefined;
  return isOnlineStore({
    id: targetId,
    store_type: row ? String(row.store_type ?? '') : null,
  });
}
