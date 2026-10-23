// ============================================================================
// IMS 2.0 - When a server answer about a product's identity still holds
// ============================================================================
// The Add-product form asks the server two questions that depend only on the
// fields that make a product what it is: the similar-products strip
// (useSimilarProducts) and the SKU preview (useQuickAddForm). Each answer
// names those fields (identity_fields, read off product_master.identity_parts
// on the server -- no list of them is kept here), and the form asks again
// only when one of them moves. One rule, both callers.

/** Council ruling: ask 400ms after the operator stops typing. */
export const IDENTITY_DEBOUNCE_MS = 400;

/** The form's attributes as typed: trimmed, blanks dropped, keys sorted (so
 *  its JSON is a stable key). */
export function typedAttributes(attributes: Record<string, unknown> | undefined): Record<string, string> {
  return Object.fromEntries(
    Object.entries(attributes || {})
      .map(([k, v]) => [k, String(v ?? '').trim()] as [string, string])
      .filter(([, v]) => v)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)),
  );
}

/** An answer still holds when no field it says decides it (`decides`, the
 *  server's identity_fields) has moved. No `decides` = any change. Shared by
 *  the similar-products strip and the Add-product SKU preview. */
export function answerStillHolds(
  asked: { category: string; typed: Record<string, string> },
  decides: string[] | undefined,
  category: string,
  typed: Record<string, string>,
): boolean {
  if (asked.category !== category) return false;
  return Object.keys({ ...asked.typed, ...typed }).every(
    (k) => asked.typed[k] === typed[k] || (decides !== undefined && !decides.includes(k)),
  );
}

