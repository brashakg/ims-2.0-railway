// Legacy /reports?tab=<x> → /reports/<section> mapper.
//
// Pure so it can be tested without mounting the router, auth and five lazy
// section chunks. Used by ReportsTabRedirect in routes/reportRoutes.tsx.
//
// The old page's allow-list was ['sales','inventory','customers','gst'] —
// SHORT BY ONE. 'forecast' fell through to Sales, so the Forecast tab had no
// working link at all. It is in the map below; that is a deliberate fix.
// Everything else still falls back to Sales, exactly as the old page did
// (the module launcher links ?tab=dead-stock, ?tab=churn and friends, which
// were never tabs and always landed on Sales).

const TAB_TO_PATH: Record<string, string> = {
  sales: 'sales',
  inventory: 'inventory',
  customers: 'customers',
  gst: 'gst',
  forecast: 'forecast',
};

/** `search` is a location search string / URLSearchParams-compatible input. */
export function legacyTabTarget(search: string | URLSearchParams): string {
  const params = new URLSearchParams(search);
  const section = TAB_TO_PATH[params.get('tab') || ''] ?? 'sales';
  // Every OTHER query param rides along — a deep link is more than its tab.
  params.delete('tab');
  const rest = params.toString();
  return `/reports/${section}${rest ? `?${rest}` : ''}`;
}
