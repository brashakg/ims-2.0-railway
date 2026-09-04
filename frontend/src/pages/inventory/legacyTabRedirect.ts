// Legacy /inventory?tab=<x> -> /inventory/<section> mapper.
//
// Pure so it can be tested without mounting the router, auth and the lazy
// section chunks. Used by InventoryTabRedirect in routes/inventoryRoutes.tsx.
//
// Two tabs deliberately land on the EXISTING standalone pages instead of a
// new section, because the old page was a second implementation of both:
//   - stock-count  -> /inventory/audit (the tab rendered the same StockAudit
//     component at a second address, with a LOOSER role gate)
//   - power-grid   -> /inventory/power-grid (the tab rendered the OLD
//     products-based LensPowerGridWidget; the routed PowerGridPage is the
//     typed-lens-catalog replacement that knows real lens stock)
//
// ReportCardsGrid links ?tab=stock, which was never a valid tab and always
// fell through to the ledger; the default keeps that behaviour.

const TAB_TO_PATH: Record<string, string> = {
  alerts: 'alerts',
  catalog: 'stock',
  'display-layout': 'display-layout',
  'low-stock': 'low-stock',
  reorders: 'reorders',
  'serial-numbers': 'serial-numbers',
  aging: 'aging',
  transfers: 'transfers',
  movements: 'movements',
  'non-moving': 'non-moving',
  'stock-count': 'audit',
  'contact-lens': 'contact-lens',
  'power-grid': 'power-grid',
  'sell-through': 'sell-through',
  overstock: 'overstock',
  'brand-insights': 'brand-insights',
  'collection-insights': 'collection-insights',
  rebalance: 'rebalance',
  quarantine: 'quarantine',
};

/** `search` is a location search string / URLSearchParams-compatible input. */
export function legacyTabTarget(search: string | URLSearchParams): string {
  const params = new URLSearchParams(search);
  const section = TAB_TO_PATH[params.get('tab') || ''] ?? 'stock';
  // Every OTHER query param rides along - QuickAdd links ?tab=catalog&search=
  // and the Zone cell links ?tab=display-layout&fixture=.
  params.delete('tab');
  const rest = params.toString();
  return `/inventory/${section}${rest ? `?${rest}` : ''}`;
}
