// ============================================================================
// IMS 2.0 - F23 Blind EOD cash tally & Z-Read API
// ============================================================================
// A BLIND end-of-day count: the cashier enters the physically-counted cash
// WITHOUT seeing the system-expected figure; only after a manager LOCKS does the
// system reveal expected-vs-counted variance + the Z-Read. The day is then
// SOFT-LOCKED (transparent, reopenable with a reason). All money is in PAISA
// (integer) on the wire. Import directly (not via the api barrel) per convention.

import api from './client';

export type DenomKind = 'note' | 'coin';
export type TillStatus = 'OPEN' | 'BLIND_SUBMITTED' | 'LOCKED';
// NEGATIVE_EXPECTED: the arithmetic says the drawer should hold less than
// nothing (more cash refunded than it took in) -- a cash-in is missing, so the
// server withholds the over/short VERDICT rather than crediting a phantom
// overage. The figures themselves are still shown.
export type VarianceStatus =
  | 'BALANCED' | 'OVERAGE' | 'SHORTAGE' | 'NEGATIVE_EXPECTED' | null;

export interface DenominationLine {
  face: number;
  pieces: number;
  kind: DenomKind;
  line_total_paisa?: number;
}

export interface TillSession {
  session_id: string;
  store_id: string;
  cashier_id?: string | null;
  cashier_name?: string | null;
  session_date: string;
  status: TillStatus;
  shift?: string | null;
  opening_float_paisa: number;
  opening_denominations: DenominationLine[];
  opened_at?: string | null;
  blind_count_paisa?: number | null;
  blind_denominations?: DenominationLine[];
  cash_payouts_paisa?: number | null;
  // Revealed only to managers/finance (redacted for cashiers pre-lock).
  expected_cash_paisa?: number | null;
  // GROSS cash collected; recorded customer cash refunds are a SEPARATE line
  // (never merged into the manual "cash paid out" figure).
  cash_sales_paisa?: number | null;
  cash_refunds_paisa?: number | null;
  // True when a recorded cash refund and a manual cash payout coincide -- they
  // may be the same money entered twice (the pre-fix workaround).
  refund_double_entry_advisory?: boolean | null;
  negative_expected_advisory?: boolean | null;
  variance_paisa?: number | null;
  variance_status?: VarianceStatus;
  tolerance_paisa?: number | null;
  by_mode?: Record<string, { collected: number; refunded: number; net: number; count: number; ledger?: string }> | null;
  zread_number?: string | null;
  locked_at?: string | null;
  locked_by_name?: string | null;
  reopen_count?: number;
  history?: Array<{ action: string; at: string; by_name?: string; reason?: string }>;
  // Set true on a cashier's redacted view.
  expected_hidden?: boolean;
}

import type { FaceLedger } from '../../components/cash/FaceTallyTable';

export interface ZRead {
  ok: boolean;
  session_id: string;
  zread_number?: string | null;
  store_id: string;
  session_date: string;
  shift?: string | null;
  cashier_name?: string | null;
  status: TillStatus;
  opening_float_paisa: number;
  opening_denominations: DenominationLine[];
  blind_denominations: DenominationLine[];
  by_mode: Record<string, { collected: number; refunded: number; net: number; count: number; ledger?: string }>;
  // Z-Read identity: opening + cash_sales - cash_refunds - cash_payouts.
  // cash_sales_paisa is GROSS collected; cash_refunds_paisa is the recorded
  // customer cash refunds already auto-deducted from the expected drawer.
  cash_sales_paisa: number;
  cash_refunds_paisa?: number | null;
  cash_payouts_paisa: number;
  refund_double_entry_advisory?: boolean | null;
  negative_expected_advisory?: boolean | null;
  negative_expected_message?: string | null;
  expected_cash_paisa?: number | null;
  counted_cash_paisa?: number | null;
  variance_paisa?: number | null;
  variance_status?: VarianceStatus;
  tolerance_paisa?: number | null;
  locked_at?: string | null;
  locked_by_name?: string | null;
  reopen_count?: number;
  history?: Array<{ action: string; at: string; by_name?: string; reason?: string }>;
  // The day tallied FACE BY FACE (see components/cash/FaceTallyTable). Purely
  // additive: the rupee figures above are computed exactly as before and are
  // unaffected by whether anyone counted notes.
  face_ledger?: FaceLedger;
}

/** COUNTED | SUGGESTED | NOT_CAPTURED. Omitted = the grid was never touched,
 *  which the server records as NOT_CAPTURED -- never as an empty drawer. */
export type CountState = 'COUNTED' | 'SUGGESTED' | 'NOT_CAPTURED';

export interface OpenPayload {
  store_id?: string;
  session_date?: string;
  shift?: string;
  opening_denominations: DenominationLine[];
  opening_count_state?: CountState;
  opening_float_paisa?: number;
  note?: string;
}

export interface BlindSubmitPayload {
  blind_denominations: DenominationLine[];
  closing_count_state?: CountState;
  blind_count_paisa?: number;
  cash_payouts_paisa?: number;
  idempotency_key?: string;
}

interface SessionEnvelope {
  ok: boolean;
  session: TillSession;
  idempotent?: boolean;
  // ONE SHARED DRAWER PER STORE: true when a second open for the same store/day
  // returned the EXISTING shared session instead of creating a new drawer.
  already_open?: boolean;
}

export interface OpenResult {
  session: TillSession;
  already_open: boolean;
}

export const tillApi = {
  open: async (payload: OpenPayload): Promise<OpenResult> => {
    const res = await api.post('/till/sessions', payload);
    const env = res.data as SessionEnvelope;
    return { session: env.session, already_open: Boolean(env.already_open) };
  },
  blindSubmit: async (sessionId: string, payload: BlindSubmitPayload): Promise<TillSession> => {
    const res = await api.post(`/till/sessions/${sessionId}/blind-submit`, payload);
    return (res.data as SessionEnvelope).session;
  },
  lock: async (sessionId: string): Promise<TillSession> => {
    const res = await api.post(`/till/sessions/${sessionId}/lock`, {});
    return (res.data as SessionEnvelope).session;
  },
  reopen: async (sessionId: string, reason: string): Promise<TillSession> => {
    const res = await api.post(`/till/sessions/${sessionId}/reopen`, { reason });
    return (res.data as SessionEnvelope).session;
  },
  list: async (params: { store_id: string; date?: string; status?: string; limit?: number }): Promise<TillSession[]> => {
    const res = await api.get('/till/sessions', { params });
    return (res.data as { sessions: TillSession[] }).sessions;
  },
  get: async (sessionId: string): Promise<TillSession> => {
    const res = await api.get(`/till/sessions/${sessionId}`);
    return (res.data as SessionEnvelope).session;
  },
  zread: async (sessionId: string): Promise<ZRead> => {
    const res = await api.get(`/till/sessions/${sessionId}/zread`);
    return res.data as ZRead;
  },
};

// Paisa -> Rupee display helper.
export const paisaToInr = (p?: number | null): string =>
  `₹${(Math.round(Number(p) || 0) / 100).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
