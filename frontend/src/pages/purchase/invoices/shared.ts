// ============================================================================
// Purchase Invoices - shared helpers (INR formatting, GST state-code / IGST
// rule, GST rate list, approval role lists). MOVED verbatim out of
// ../PurchaseInvoicesTab.tsx by the Wave 6 file diet; nothing rewritten.
// ============================================================================

import type { UserRole } from '../../../types';

export const inr = (n?: number) => `₹${(Math.round((n || 0) * 100) / 100).toLocaleString('en-IN')}`;
// Optical GST rates per business rules (5% frames/lenses/CL, 12% certain CL,
// 18% sunglasses/watches/accessories, plus 0/28 for completeness).
export const GST_RATES = [0, 5, 12, 18, 28];

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

// Pull a 2-digit state code from a place_of_supply ("27", "27-Maharashtra")
// or a GSTIN (first two chars). Mirrors backend itc_reconcile._state_code so
// the FE preview matches how the server will route the tax.
export function stateCode(value?: string): string {
  if (!value) return '';
  const m = String(value).trim().match(/\d{2}/);
  return m ? m[0] : '';
}

// True when the supplier's place_of_supply state differs from our recipient
// GSTIN's state -> the supply is inter-state -> IGST. Missing either side
// defaults to intra-state (CGST/SGST), matching the backend fallback.
export function isInterstate(placeOfSupply?: string, recipientGstin?: string): boolean {
  const pos = stateCode(placeOfSupply);
  const rec = stateCode(recipientGstin);
  if (!pos || !rec) return false;
  return pos !== rec;
}

export const EXCEPTION_APPROVE_ROLES: UserRole[] = ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'];

// Roles allowed to approve a 3-way-match exception (release an ON_HOLD invoice
// for payment despite a variance). Mirrors the _AP_ROLES backend gate.
export const APPROVE_ROLES: UserRole[] = ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'];
