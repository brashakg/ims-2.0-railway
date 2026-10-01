// ============================================================================
// IMS 2.0 - What the shop floor is told after a goods receipt is accepted
// ============================================================================
// ONE reading of POST /vendors/grn/{id}/accept for every receiving screen
// (audit C1): a receipt that still HOLDS lines -- their product is not
// catalogued yet, so accept minted nothing for them -- is never announced as a
// green success. It says what reached the shelf and who finishes the rest: the
// server has already given the catalogue manager a task, and finishing the
// product puts the held units on the shelf by itself.

export interface GrnAcceptResult {
  units_added?: number;
  po_status?: string | null;
  grn_status?: string;
  unresolved_lines?: unknown[];
}

interface ToastLike {
  success: (message: unknown, duration?: number) => void;
  warning: (message: unknown, duration?: number) => void;
}

export function reportGrnAccept(
  toast: ToastLike,
  grnNumber: string,
  res: GrnAcceptResult | null | undefined,
  fallbackUnits = 0,
): void {
  const units = res?.units_added ?? fallbackUnits;
  if (res?.grn_status === 'PARTIALLY_ACCEPTED') {
    const held = res.unresolved_lines?.length || 'some';
    toast.warning(
      `GRN ${grnNumber}: ${units} unit(s) added to stock; ${held} line(s) wait to be catalogued. ` +
        'The catalogue manager has a task — those units go on the shelf by themselves once the product is finished.',
      12000,
    );
    return;
  }
  toast.success(
    `GRN ${grnNumber} accepted — ${units} units added to stock` +
      (res?.po_status ? ` · PO ${res.po_status}` : ''),
  );
}

/** What a held receipt is waiting for, read off the server's
 *  unresolved_lines[].reason: "over_order" lines are beyond what the PO
 *  ordered (a second receipt of the same box, say) and wait for the store
 *  manager; every other held line waits to be catalogued. */
export function heldLinesSummary(lines: unknown): { text: string } {
  const held = (Array.isArray(lines) ? lines : []) as Array<{ reason?: string }>;
  const over = held.filter((l) => l?.reason === 'over_order');
  const catalogue = held.filter((l) => l?.reason !== 'over_order');
  const parts = [
    catalogue.length ? `${catalogue.length} line(s) waiting to be catalogued` : '',
    over.length ? `${over.length} line(s) beyond the order, for the store manager` : '',
  ].filter(Boolean);
  return { text: parts.join(' · ') || 'some line(s) held' };
}
