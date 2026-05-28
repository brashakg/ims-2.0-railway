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
}

export interface SessionsResponse {
  sessions: CashRegisterSession[];
  open_session: CashRegisterSession | null;
  expected_preview: ExpectedPreview | null;
}

export interface OpenPayload {
  store_id?: string;
  shift?: string;
  denominations: DenominationLine[];
  opening_float?: number;
  note?: string;
}

export interface ClosePayload {
  session_id: string;
  denominations: DenominationLine[];
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
