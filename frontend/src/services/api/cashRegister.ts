// ============================================================================
// IMS 2.0 - Cash register / EOD reconciliation API
// ============================================================================
// Till sessions: open with an opening float by denomination, close with a
// counted denomination breakdown -> expected vs counted variance. Store-scoped.
// Import directly (not via the api barrel) per the established convention.

import api from './client';
import type { TillDrawerSummary } from './reports';

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
  // NULL means NOBODY COUNTED. A drawer closed without a count has no counted
  // figure and no variance, and its verdict is withheld (NOT_COUNTED) -- it is
  // never a Rs 0.00 drawer with a full-day shortfall.
  counted?: number | null;
  expected?: number | null;
  variance?: number | null;
  variance_status?:
    | 'BALANCED'
    | 'OVER'
    | 'SHORT'
    | 'NEGATIVE_EXPECTED'
    | 'NOT_COUNTED'
    // D53: the day had several drawers; no single verdict is true for it.
    | 'SEVERAL_DRAWERS'
    // The close could not read the day's drawers: no verdict is claimed.
    | 'COMPARISON_UNAVAILABLE'
    // D111: opened on a drawer already counted and closed with no count of
    // its own. The till's count is shown; nothing is compared with it.
    | 'COUNTED_EARLIER'
    | null;
  tolerance?: number | null;
  closing_note?: string | null;
  // TWO DOORS, ONE RECORD. The shared till session this drawer was counted on,
  // and what happened when this screen linked to it. `counted_from_shared_record`
  // means the day was ALREADY counted at POS Day-End: that count stands and is
  // the `counted` figure above, so both screens show one answer for one drawer.
  till_session_id?: string | null;
  till_link_ok?: boolean;
  till_link_error?: string | null;
  till_already_counted?: boolean;
  till_count_differs?: boolean;
  counted_from_shared_record?: boolean;
  till_opening_float_not_recorded?: boolean;
  // D111: this session opened on a drawer that was already counted. Its
  // expected cash starts from that count (`till_standing_count`), not from the
  // float typed at opening, and covers the cash moved since that count.
  till_counted_earlier?: boolean;
  till_standing_count?: number | null;
  // D128(1): the drawer's earlier count (Blind EOD / Day-End) was beyond the
  // band and not yet explained; this close shows it (the Z-Read lock takes
  // its note). Rupees, the count's own difference.
  till_count_variance?: number | null;
  // D127(2): why nothing was compared (an earlier close could not be placed
  // on one drawer), and the sentence for staff.
  comparison_withheld_reason?: string | null;
  comparison_withheld_message?: string | null;
  // Cash drawers on this shop-day. Above 1, `counted` is the latest drawer's
  // count only, not the day's total (D40).
  till_drawers_on_day?: number | null;
  // D53: each drawer's own count and (once locked) variance; never summed.
  till_drawers?: TillDrawerSummary[] | null;
  // The day already carried a declared opening float; that one stands.
  till_float_already_declared?: boolean;
  // The manager task(s) this close owes (a variance alert, "count the drawer
  // on the till"). `pending` true = not raised yet; the server raises it on
  // the next retry or load of this screen.
  manager_followup?: { pending: boolean; tasks?: Array<{ kind: string }> } | null;
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
  // `tolerance` was DELETED (owner ruling 2026-08-25): the band is the ONE
  // store-scopable policy the server reads; a closer never chooses it.
  // `note` is MANDATORY server-side when |variance| is beyond that band.
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
