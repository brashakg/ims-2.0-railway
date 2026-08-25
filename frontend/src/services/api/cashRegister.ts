// ============================================================================
// IMS 2.0 - Cash register / EOD reconciliation API
// ============================================================================
// Till sessions: open with an opening float by denomination, close with a
// counted denomination breakdown -> expected vs counted variance. Store-scoped.
// Import directly (not via the api barrel) per the established convention.

import api from './client';

export type DenomKind = 'note' | 'coin';

export interface DenominationLine {
  face: number;
  pieces: number;
  kind: DenomKind;
  line_total?: number;
}

export interface CashRegisterSession {
  session_id: string;
  store_id: string;
  status: 'OPEN' | 'CLOSED';
  shift?: string | null;
  opening_float: number;
  opening_denominations: DenominationLine[];
  opened_at: string;
  opened_by?: string | null;
  opened_by_name?: string | null;
  opening_note?: string | null;
  closed_at?: string | null;
  closed_by?: string | null;
  closed_by_name?: string | null;
  closing_denominations?: DenominationLine[];
  cash_sales?: number | null;
  cash_refunds?: number | null;
  cash_expenses?: number | null;
  bank_deposit?: number | null;
  counted?: number | null;
  expected?: number | null;
  variance?: number | null;
  variance_status?: 'BALANCED' | 'OVER' | 'SHORT' | null;
  tolerance?: number | null;
  closing_note?: string | null;
}

export interface ExpectedPreview {
  opening_float: number;
  cash_sales: number;
  cash_refunds: number;
  cash_expenses: number;
  bank_deposit: number;
  expected: number;
  // Present when a manual CASH expense MATCHES a recorded cash refund to the
  // paisa (or is refund-flavoured) — probably the same money entered twice.
  // Amount-matched, not a bare co-occurrence, so it does not fire every day.
  refund_double_entry_advisory?: {
    matched_amount: number;
    cash_refunds: number;
    reason: 'AMOUNT_MATCH' | 'REFUND_CATEGORY';
    message: string;
  } | null;
  // True when an expense booked in this window is NOT paid out of the shop
  // till (salaries, staff advances, PF/ESI — owner ruling 2026-08-14) and has
  // therefore been left OUT of `cash_expenses` and out of `expected`. Never
  // carries the amount: a figure a human counts money against must say that it
  // leaves something out, without saying what or how much.
  off_till_expense_advisory?: boolean;
  off_till_expense_message?: string | null;
  // True when the expected drawer computes NEGATIVE (a cash-in is missing).
  negative_expected_advisory?: boolean;
  negative_expected_message?: string | null;
}

export interface SessionsResponse {
  sessions: CashRegisterSession[];
  open_session: CashRegisterSession | null;
  expected_preview: ExpectedPreview | null;
}

/** COUNTED | SUGGESTED | NOT_CAPTURED. Omitted = the grid was never touched,
 *  which the server records as NOT_CAPTURED -- never as an empty drawer. */
export type CountState = 'COUNTED' | 'SUGGESTED' | 'NOT_CAPTURED';

export interface OpenPayload {
  store_id?: string;
  shift?: string;
  denominations: DenominationLine[];
  opening_count_state?: CountState;
  opening_float?: number;
  note?: string;
}

export interface ClosePayload {
  session_id: string;
  denominations: DenominationLine[];
  closing_count_state?: CountState;
  bank_deposit?: number;
  counted_override?: number;
  tolerance?: number;
  note?: string;
}

export const cashRegisterApi = {
  open: async (payload: OpenPayload): Promise<CashRegisterSession> => {
    const res = await api.post('/finance/cash-register/open', payload);
    return res.data as CashRegisterSession;
  },
  close: async (payload: ClosePayload): Promise<CashRegisterSession> => {
    const res = await api.post('/finance/cash-register/close', payload);
    return res.data as CashRegisterSession;
  },
  sessions: async (params?: {
    store_id?: string;
    status?: string;
    limit?: number;
  }): Promise<SessionsResponse> => {
    const res = await api.get('/finance/cash-register/sessions', { params });
    return res.data as SessionsResponse;
  },
};
