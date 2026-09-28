// Legacy /finance/expenses?tab=<x> -> /finance/expenses/<section> mapper.
//
// Pure so it can be tested without mounting the router. Used by
// ExpensesIndex in routes/financeRoutes.tsx, which only calls it when a
// ?tab= is present (a bare /finance/expenses IS the My Expenses page).
//
// The old page kept its tab in useState, so no link in the app carries a
// ?tab=; this is the W1 recipe's net for a bookmarked or typed one. 'my' and
// anything unknown land on My Expenses, which is where the old page opened.

// A Map, not an object literal: an inherited key (?tab=constructor,
// ?tab=__proto__, ?tab=toString) must miss and land on My Expenses, not
// build a URL out of Object.prototype.
const TAB_TO_PATH = new Map<string, string>([
  ['my', ''],
  ['approvals', 'approvals'],
  ['entry', 'entry'],
  ['aging', 'aging'],
  ['duplicates', 'duplicates'],
  ['advances', 'advances'],
  ['float', 'float'],
  ['settle', 'settle'],
  ['summary', 'summary'],
]);

/** `search` is a location search string / URLSearchParams-compatible input. */
export function legacyTabTarget(search: string | URLSearchParams): string {
  const params = new URLSearchParams(search);
  const section = TAB_TO_PATH.get(params.get('tab') || '') ?? '';
  // Every OTHER query param rides along - a deep link is more than its tab.
  params.delete('tab');
  const rest = params.toString();
  return `/finance/expenses${section ? `/${section}` : ''}${rest ? `?${rest}` : ''}`;
}
