// ============================================================================
// IMS 2.0 — Vouchers / Gift-Card API client
// ============================================================================
// Backend: backend/api/routers/vouchers.py at /api/v1/vouchers
//
// The POS only needs the read-only validate/lookup call: it checks a card
// at "Apply" time and shows the balance. The actual REDEEM (decrement)
// happens server-side inside the order payment endpoint when the payment
// is recorded — never from the client — so an abandoned sale can't burn a
// card and there's no double-spend.

import api from './client';

export interface VoucherValidation {
  valid: boolean;
  code: string;
  balance?: number;
  status?: string;
  expiry_date?: string | null;
  type?: string;
  reason?: string;
}

export const vouchersApi = {
  /** Validate / look up a voucher by code (case-insensitive, read-only). */
  validate: async (code: string): Promise<VoucherValidation> => {
    const r = await api.get(`/vouchers/${encodeURIComponent(code)}`);
    return r.data;
  },
};
