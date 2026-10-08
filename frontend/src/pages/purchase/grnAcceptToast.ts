// ============================================================================
// IMS 2.0 - What the shop floor is told after a goods receipt is accepted
// ============================================================================
// ONE reading of POST /vendors/grn/{id}/accept for every receiving screen
// (audit C1): a receipt that still HOLDS lines -- their product is not
// catalogued yet, so accept minted nothing for them -- is never announced as a
// green success. It says what reached the shelf and what each held line waits
// for (heldLinesSummary) -- naming no person the server may not have found:
// only a line waiting to be catalogued goes on the shelf by itself, once its
// product is finished (R1-91, R1-96).

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
    const held = heldLinesSummary(res.unresolved_lines);
    toast.warning(
      `GRN ${grnNumber}: ${units} unit(s) added to stock; held: ${held.text}.` +
        (held.catalogue
          ? ' Lines waiting to be catalogued go on the shelf by themselves once their product is finished.'
          : ''),
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
 *  manager; "not_catalogued" lines name no product the catalogue has, so
 *  nothing finishes them by itself; every other held line waits to be
 *  catalogued. `catalogue` counts those last ones. */
export function heldLinesSummary(lines: unknown): { text: string; catalogue: number; over: number } {
  const held = (Array.isArray(lines) ? lines : []) as Array<{ reason?: string }>;
  const over = held.filter((l) => l?.reason === 'over_order').length;
  const unknown = held.filter((l) => l?.reason === 'not_catalogued').length;
  const catalogue = held.length - over - unknown;
  const parts = [
    catalogue ? `${catalogue} line(s) waiting to be catalogued` : '',
    over ? `${over} line(s) beyond the order, for the store manager` : '',
    unknown ? `${unknown} line(s) for a product not in the catalogue` : '',
  ].filter(Boolean);
  return { text: parts.join(' · ') || 'some line(s) held', catalogue, over };
}
