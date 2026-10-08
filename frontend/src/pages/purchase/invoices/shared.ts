// ============================================================================
// Purchase Invoices - shared helpers (INR formatting, GST rate list, approval
// role list). The tax head is NOT decided here: the form shows the server's
// POST /preview (the booking's own math). MOVED verbatim out of
// ../PurchaseInvoicesTab.tsx by the Wave 6 file diet; nothing rewritten.
// ============================================================================

import type { UserRole } from '../../../types';

export const inr = (n?: number) => `₹${(Math.round((n || 0) * 100) / 100).toLocaleString('en-IN')}`;
// Optical GST rates per business rules (5% frames/lenses/CL, 12% certain CL,
// 18% sunglasses/watches/accessories, plus 0/28 for completeness).
export const GST_RATES = [0, 5, 12, 18, 28];

// A reverse-charge bill in one sentence: the form, the list and the detail
// drawer all say it, as visible text (a touch tablet shows no tooltip).
export const rcmNote = (tax?: number, owed?: number) =>
  `You pay this GST (${inr(tax)}) to the government; the supplier is owed ${inr(owed)}`;

// The server's `detail` is a string on some routes and a structured object
// ({code, message, lines}) on others -- the purchase-invoice gates are the
// latter. String()-ing an object rendered "[object Object]" at the user, which
// is how a precise refusal ("...is still missing Selling Price") became
// unreadable. Prefer the object's own message.
export function errMsg(e: unknown, fb: string) {
  if (e && typeof e === 'object' && 'response' in e) {
    const r = (e as { response?: { data?: { detail?: unknown } } }).response;
    const d = r?.data?.detail;
    if (typeof d === 'string' && d) return d;
    if (d && typeof d === 'object') {
      const m = (d as { message?: string }).message;
      if (m) return m;
    }
  }
  return e instanceof Error ? e.message : fb;
}

// The accounts roles: they approve a 3-way-match exception (release an ON_HOLD
// invoice for payment despite a variance) and alone see the bill form's
// Reverse charge option. Mirrors the _AP_ROLES backend gate.
export const APPROVE_ROLES: UserRole[] = ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'];
