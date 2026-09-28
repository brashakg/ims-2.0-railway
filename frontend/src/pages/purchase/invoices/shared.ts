// ============================================================================
// Purchase Invoices - shared helpers (INR formatting, GST state-code / IGST
// rule, GST rate list, approval role list). MOVED verbatim out of
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

// The 2-digit state code of a GSTIN (its first two characters).
export function stateCode(value?: string): string {
  if (!value) return '';
  const m = String(value).trim().match(/\d{2}/);
  return m ? m[0] : '';
}

// THE bill's tax-head rule, as the server applies it
// (purchase_invoice_engine.compute_invoice): the supplier's GSTIN state vs our
// GSTIN's state. Different -> inter-state -> IGST. Missing either GSTIN ->
// intra-state (CGST/SGST), the server's fallback.
export function isInterstate(supplierGstin?: string, recipientGstin?: string): boolean {
  const sup = stateCode(supplierGstin);
  const rec = stateCode(recipientGstin);
  if (!sup || !rec) return false;
  return sup !== rec;
}

// Roles allowed to approve a 3-way-match exception (release an ON_HOLD invoice
// for payment despite a variance). Mirrors the _AP_ROLES backend gate.
export const APPROVE_ROLES: UserRole[] = ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'];
