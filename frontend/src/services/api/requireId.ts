// ONE rule for a purchase-invoice id, shared by the mapper, every read and
// every write. A write must never go to /purchase-invoices/undefined/..., and
// an id is never put into a URL unencoded.

const NO_ID = ['', 'undefined', 'null'];

/** The id as a clean string, or null when there is none: trimmed; a string or a
 *  finite number (String(n)); '', 'undefined' and 'null' are no id. */
export function cleanInvoiceId(id: unknown): string | null {
  let s: string;
  if (typeof id === 'string') s = id.trim();
  else if (typeof id === 'number' && Number.isFinite(id)) s = String(id);
  else return null;
  return NO_ID.includes(s) ? null : s;
}

/** The first usable id among the candidates -- a blank earlier candidate never
 *  wins over a valid later one. */
export function firstInvoiceId(...candidates: unknown[]): string | undefined {
  for (const c of candidates) {
    const id = cleanInvoiceId(c);
    if (id) return id;
  }
  return undefined;
}

/** A WRITE needs an id: returns the clean one, or throws and sends nothing. */
export function requireInvoiceId(id: unknown): string {
  const clean = cleanInvoiceId(id);
  if (!clean) {
    throw new Error('This invoice has no id, so nothing was sent. Reload the list and try again.');
  }
  return clean;
}

/** The id as one URL path segment (a/b, ?x=1 and ../ cannot change the path). */
export function invoiceIdSegment(id: string): string {
  return encodeURIComponent(id);
}
