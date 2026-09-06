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

const TAB_TO_PATH: Record<string, string> = {
  recalls: 'recalls',
  campaigns: 'campaigns',
};

/** `search` is a location search string / URLSearchParams-compatible input.
 *  Returns null when the URL names no legacy tab (render /customers itself). */
export function legacyTabTarget(search: string | URLSearchParams): string | null {
  const params = new URLSearchParams(search);
  const section = TAB_TO_PATH[params.get('tab') || ''];
  if (!section) return null;
  // Every OTHER query param rides along - a deep link is more than its tab.
  params.delete('tab');
  const rest = params.toString();
  return `/customers/${section}${rest ? `?${rest}` : ''}`;
}
