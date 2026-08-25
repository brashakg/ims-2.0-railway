// ============================================================================
// IMS 2.0 - Cash reconciliation summary API (#7)
// ============================================================================
// Manager-facing console: a unified, read-only view across the manual
// close-by-denomination flow (cash_register_sessions) AND the blind-EOD Z-Read
// flow (till_sessions). One row per closed session with expected vs counted
// variance, flagged BALANCED / OVERAGE / SHORTAGE. Optional manager sign-off.
// Import directly (not via the api barrel) per the established convention.

import api from './client';

// NOT_COUNTED: nobody counted that drawer, so there is no variance and no
// verdict. NEGATIVE_EXPECTED: the expected drawer computed below zero (a
// cash-in is missing), so the over/short verdict is withheld. Both are
// withheld verdicts, never a Rs 0.00 drawer.
export type ReconStatus =
  | 'BALANCED'
  | 'OVERAGE'
  | 'SHORTAGE'
  | 'NEGATIVE_EXPECTED'
  | 'NOT_COUNTED';
export type ReconSource = 'CASH_REGISTER' | 'BLIND_EOD';

export interface ReconByModeRow {
  net: number;
  count: number;
}

export interface ReconSignoff {
  reviewed: boolean;
  reviewed_by?: string | null;
  reviewed_by_name?: string | null;
  reviewed_at?: string | null;
  note?: string | null;
}

export interface ReconRow {
  session_id: string;
  source: ReconSource;
  store_id: string;
  store_name: string;
  session_date: string;
  shift?: string | null;
  opening_float: number;
  cash_sales: number;
  cash_refunds: number;
  cash_expenses: number;
  bank_deposit: number;
  expected_cash: number;
  // NULL means nobody counted this drawer -- there is no counted figure and no
  // variance. Never render a null as zero.
  counted_cash: number | null;
  blind: boolean;
  variance: number | null;
  variance_status: ReconStatus;
  tolerance: number;
  by_mode: Record<string, ReconByModeRow>;
  // Which definition the by_mode figures use. Both close systems now report
  // NET_OF_RECORDED_REFUNDS; sessions closed before that change are labelled
  // PAYMENTS_ONLY_LEGACY so one grid never mixes two definitions unannounced.
  by_mode_basis?: 'NET_OF_RECORDED_REFUNDS' | 'PAYMENTS_ONLY_LEGACY';
  // Present when a recorded cash refund may also have been keyed as a manual
  // cash payout/expense (possible double entry). Advisory only.
  refund_double_entry_advisory?: { message?: string; matched_amount?: number; reason?: string } | null;
  // True when the expected drawer computed NEGATIVE (a cash-in is missing) —
  // the over/short verdict is withheld rather than crediting a phantom overage.
  negative_expected_advisory?: boolean;
  // True when an expense in this session's window is NOT paid from the till
  // (salaries / advances / PF-ESI) and was therefore left out of
  // `cash_expenses` and `expected_cash`. Carried from the close record, never
  // recomputed, and never carrying the amount.
  off_till_expense_advisory?: boolean;
  closed_by?: string | null;
  closed_by_name?: string | null;
  closed_at?: string | null;
  zread_number?: string | null;
  signoff?: ReconSignoff;
}

export interface ReconTotals {
  sessions: number;
  balanced: number;
  overage: number;
  shortage: number;
  opening_float: number;
  cash_sales: number;
  cash_refunds: number;
  cash_expenses: number;
  expected_cash: number;
  counted_cash: number;
  variance: number;
  overage_amount: number;
  shortage_amount: number;
}

export interface CashReconSummary {
  from: string;
  to: string;
  store_id: string | null;
  rows: ReconRow[];
  totals: ReconTotals;
}

export interface SignoffPayload {
  session_id: string;
  source?: ReconSource;
  note?: string;
}

export const cashReconciliationApi = {
  summary: async (params: {
    from?: string;
    to?: string;
    store_id?: string;
  }): Promise<CashReconSummary> => {
    const res = await api.get('/finance/cash-reconciliation-summary', { params });
    return res.data as CashReconSummary;
  },
  signoff: async (payload: SignoffPayload): Promise<{ ok: boolean; signoff: ReconSignoff }> => {
    const res = await api.post('/finance/cash-reconciliation-signoff', payload);
    return res.data as { ok: boolean; signoff: ReconSignoff };
  },
};
