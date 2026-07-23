// ============================================================================
// IMS 2.0 — Accessible-store resolution (post-login store selector)
// ----------------------------------------------------------------------------
// Single source of truth for "which stores can THIS user operate as" and the
// "should we show the store picker" decision. Shared by LoginPage (branch
// decision), StoreSelectPage (the interstitial), and AppLayout (the guard) so
// the three never drift.
//
// Owner decision (multi-store roles only):
//   - SUPERADMIN / ADMIN see ALL active stores.
//   - AREA_MANAGER and everyone else see only the stores in their store_ids.
//   - >1 accessible store  -> pick before the dashboard.
//   - <=1 accessible store -> auto-proceed (single-store users are untouched).
// ============================================================================

import type { User } from '../types';

/** True when the user's role set sees the whole org (every active store), not
 *  just their assigned store_ids. Mirrors the topbar / backend all-stores set
 *  but deliberately scopes the picker to SUPERADMIN + ADMIN per the owner
 *  decision (AREA_MANAGER picks from their assigned store_ids). */
export function userSeesAllStores(roles: string[] | undefined | null): boolean {
  return (roles || []).some((r) => r === 'SUPERADMIN' || r === 'ADMIN');
}

/** A store reduced to just what the picker UI needs. */
export interface AccessibleStore {
  id: string;
  name: string;
  code: string;
  city?: string;
  brand?: string;
  entityId?: string;
  /** OS-029: raw store_type (RETAIL / HQ / WAREHOUSE / ONLINE) so the picker
   *  can badge ONLINE stores instead of presenting them as physical shops. */
  storeType?: string;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Normalise a raw store doc from GET /stores (snake_case from the API) into
 *  the picker shape. Tolerant of the camelCase / legacy id variants the topbar
 *  also defends against. */
export function normalizeStore(s: Record<string, unknown> | null | undefined): AccessibleStore | null {
  if (!s) return null;
  const id = (s.store_id || s.id || s._id) as string | undefined;
  if (!id) return null;
  const rawCode = (s.store_code || s.storeCode || '') as string;
  const name = (s.store_name || s.storeName || s.name || id) as string;
  // Never surface a raw uuid as the "code" line; only show a real human code.
  const code = rawCode || (UUID_RE.test(id) ? '' : id);
  return {
    id,
    name,
    code,
    city: (s.city || s.location || '') as string,
    brand: (s.brand || '') as string,
    entityId: (s.entity_id || s.entityId || '') as string,
    storeType: (s.store_type || s.storeType || '') as string,
  };
}

/** The stores a user may operate as, derived from the org store list. For an
 *  all-stores role this is every (active) store the API returned; for everyone
 *  else it's the subset whose id is in their store_ids. */
export function accessibleStoresFrom(
  user: User | null | undefined,
  rawStores: unknown,
): AccessibleStore[] {
  const list = Array.isArray(rawStores) ? rawStores : [];
  const all = list
    .map((s) => normalizeStore(s as Record<string, unknown>))
    .filter((s): s is AccessibleStore => s !== null);
  if (userSeesAllStores(user?.roles)) return all;
  const ids = new Set(user?.storeIds || []);
  return all.filter((s) => ids.has(s.id));
}

/** Whether the user currently has NO usable active store selected (empty /
 *  whitespace). Used by the AppLayout safety-net guard. */
export function hasNoActiveStore(user: User | null | undefined): boolean {
  const active = user?.activeStoreId;
  return !active || (typeof active === 'string' && active.trim() === '');
}

/** Whether the user could ever choose among multiple stores: an all-stores role
 *  OR more than one assigned store_id. Single-store, single-assignment users are
 *  never multi-store-capable, so the picker / guard never touches them. */
export function isMultiStoreCapable(user: User | null | undefined): boolean {
  return userSeesAllStores(user?.roles) || (user?.storeIds?.length ?? 0) > 1;
}
