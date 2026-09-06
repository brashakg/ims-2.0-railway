// ============================================================================
// Shopify location preselect (per-store locations, owner ruling 2026-09-06)
// ----------------------------------------------------------------------------
// THE one rule the Organization StoreModal uses to suggest a location for a
// shop that has none yet: exactly one Shopify location whose NAME equals the
// store's name or code, case-insensitively, whitespace-trimmed. Nothing is
// saved until the owner presses Save; the pick is a hint the dropdown shows.
//
// Deliberately NOT a substring / word / city match: #1125's hint picker
// matched "Better Vision Sector 4" on the words better/vision of the ONLINE
// row and would have pointed the pooled number at the 0-unit Bokaro
// location. A store name that equals a location name cannot collide across
// shops; anything looser can. Two locations with the same name => no pick.
// Mirrors the design doc section 3.3 -- keep it this strict.
// ============================================================================

export interface LocationNameRow {
  id: string;
  name?: string | null;
}

export interface StoreNameRow {
  store_name?: string | null;
  store_code?: string | null;
}

function norm(v: string | null | undefined): string {
  return String(v ?? '').trim().toLowerCase();
}

/** The gid of the ONE location whose name equals the store's name or code
 *  (case-insensitive, trimmed), else ''. Never a substring match. */
export function exactLocationMatch(
  locations: readonly LocationNameRow[] | null | undefined,
  store: StoreNameRow | null | undefined,
): string {
  const wanted = new Set([norm(store?.store_name), norm(store?.store_code)]);
  wanted.delete('');
  if (wanted.size === 0) return '';
  const hits = (locations ?? []).filter((l) => l?.id && wanted.has(norm(l.name)));
  return hits.length === 1 ? hits[0].id : '';
}
