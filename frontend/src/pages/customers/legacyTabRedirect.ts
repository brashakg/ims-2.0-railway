// Legacy /customers?tab=<x> -> /customers/<section> mapper.
//
// Pure so it can be tested without mounting the router, auth and the lazy
// section chunks. Used by CustomersIndex in routes/customerRoutes.tsx.
//
// DIFFERENT FROM THE INVENTORY / REPORTS SHIMS in one way that matters: bare
// /customers is a REAL screen (the customer list), not a tab container, so
// there is no default section to fall back to. No `tab=`, or a `tab=` value
// that was never a tab, returns null and the list renders in place -- exactly
// what the old page did.
//
// `campaigns` was already a redirect inside CustomersPage (the in-page tab was
// a dead-duplicate promotion builder with no backend; /customers/campaigns
// mounts the real CampaignManager). `recalls` was the LAST in-page tab in the
// app: it rendered RecallManager at a second, unbookmarkable address.

// Maps, not object literals (the finance shim's lesson): an inherited key
// (?tab=constructor, ?tab=__proto__, ?tab=toString) must miss, not build a
// URL out of Object.prototype.
const TAB_TO_PATH = new Map<string, string>([
  ['recalls', 'recalls'],
  ['campaigns', 'campaigns'],
]);

/** `search` is a location search string / URLSearchParams-compatible input.
 *  Returns null when the URL names no legacy tab (render /customers itself). */
export function legacyTabTarget(search: string | URLSearchParams): string | null {
  const params = new URLSearchParams(search);
  const section = TAB_TO_PATH.get(params.get('tab') || '');
  if (!section) return null;
  // Every OTHER query param rides along - a deep link is more than its tab.
  params.delete('tab');
  const rest = params.toString();
  return `/customers/${section}${rest ? `?${rest}` : ''}`;
}

// Legacy /customers/loyalty?tab=<x> -> /customers/loyalty/<section> (Wave 6
// B12). Used by LoyaltyIndex in routes/customerRoutes.tsx, which only calls it
// when a ?tab= is present (a bare /customers/loyalty IS the Overview). The old
// page kept its tab in useState, so no link in the app carries a ?tab=; this
// is the net for a bookmarked or typed one. 'overview', the deleted
// 'promotions' tab (a placeholder that pointed at the Campaign Manager) and
// anything unknown land on the Overview, which is where the old page opened.
const LOYALTY_TAB_TO_PATH = new Map<string, string>([
  ['overview', ''],
  ['tiers', 'tiers'],
  ['rewards', 'rewards'],
  ['promotions', ''],
]);

export function legacyLoyaltyTabTarget(search: string | URLSearchParams): string {
  const params = new URLSearchParams(search);
  const section = LOYALTY_TAB_TO_PATH.get(params.get('tab') || '') ?? '';
  // Every OTHER query param rides along - a deep link is more than its tab.
  params.delete('tab');
  const rest = params.toString();
  return `/customers/loyalty${section ? `/${section}` : ''}${rest ? `?${rest}` : ''}`;
}
