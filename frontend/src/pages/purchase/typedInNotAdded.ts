// ============================================================================
// IMS 2.0 - Typed-in PO lines the server could not add
// ============================================================================
// A line typed in (brand, model, colour, size, MRP) instead of picked becomes
// a product only AFTER the order is saved. If that write fails, the server
// takes the line off the order (or, if the order moved on meanwhile, asks the
// person to check it) and names it in `products_not_created`. A "saved" toast
// alone hid that, so the form says it.

export interface TypedInNotAdded {
  product_id: string;
  product_name?: string | null;
  reason: string;
}

export function typedInNotAddedMessage(lines?: TypedInNotAdded[] | null): string | null {
  if (!lines || lines.length === 0) return null;
  return lines
    .map((l) => `${l.product_name || 'A typed-in item'} ${l.reason}.`)
    .join(' ');
}

/** What the form says after an edit is saved. An edit whose typed-in items
 *  could not be added -- leaving nothing, so the server cancelled the order --
 *  is never "saved". */
export function editOutcomeToasts(
  poNumber: string,
  status: string,
  lines?: TypedInNotAdded[] | null,
): Array<{ kind: 'success' | 'warning' | 'error'; text: string }> {
  const notAdded = typedInNotAddedMessage(lines);
  if (status === 'CANCELLED') {
    return [{ kind: 'error', text: `${poNumber} was cancelled. ${notAdded ?? ''}`.trim() }];
  }
  const out: Array<{ kind: 'success' | 'warning' | 'error'; text: string }> = [
    { kind: 'success', text: `${poNumber} saved` },
  ];
  if (notAdded) out.push({ kind: 'warning', text: notAdded });
  return out;
}
