// ============================================================================
// IMS 2.0 - F20 RTV Debit Note API
// ============================================================================
// The GST-compliant DEBIT NOTE document issued to a vendor when goods are
// returned (the physical RTV / vendor_return / vendor_rma already moved stock).
// This is an accounting/document layer; it does NOT change the return state.
// Money is authoritative in integer paise with a rupee display block alongside.

import api from './client';

export interface DebitNoteLine {
  sku?: string | null;
  description: string;
  hsn: string;
  qty: number;
  rate_paise: number;
  taxable_paise: number;
  gst_rate: number;
  cgst_paise: number;
  sgst_paise: number;
  igst_paise: number;
  tax_paise: number;
  line_total_paise: number;
}

export interface DebitNoteTotals {
  taxable_paise: number;
  cgst_paise: number;
  sgst_paise: number;
  igst_paise: number;
  tax_paise: number;
  grand_total_paise: number;
}

export interface DebitNote {
  debit_note_id: string;
  debit_note_number: string;
  financial_year: string;
  issue_date: string;
  entity_id?: string | null;
  store_id?: string | null;
  seller: { name: string; gstin: string; state_code: string; address: string };
  vendor: {
    vendor_id?: string | null;
    name: string;
    gstin: string;
    state_code: string;
    address: string;
  };
  original_invoice: { number?: string | null; date?: string | null };
  rtv_ref: { type: 'vendor_return' | 'vendor_rma'; id?: string | null };
  is_inter_state: boolean;
  place_of_supply: string;
  lines: DebitNoteLine[];
  totals: DebitNoteTotals;
  totals_rupees?: {
    taxable: number;
    cgst: number;
    sgst: number;
    igst: number;
    tax: number;
    grand_total: number;
  };
  rtv_ref_id?: string;
  created_at?: string;
  created_by?: string;
}

export interface ListDebitNotesResponse {
  debit_notes: DebitNote[];
  total: number;
}

export interface IssueDebitNoteResponse {
  idempotent: boolean;
  debit_note: DebitNote;
}

const BASE = '/rtv-debit-notes';

export const rtvDebitNotesApi = {
  list(params?: { store_id?: string; vendor_id?: string; skip?: number; limit?: number }) {
    return api.get<ListDebitNotesResponse>(BASE, { params }).then((r) => r.data);
  },

  get(debitNoteId: string) {
    return api.get<DebitNote>(`${BASE}/${debitNoteId}`).then((r) => r.data);
  },

  issue(rtvId: string, sourceType: 'vendor_return' | 'vendor_rma' = 'vendor_return') {
    return api
      .post<IssueDebitNoteResponse>(`${BASE}/issue`, { source_type: sourceType, rtv_id: rtvId })
      .then((r) => r.data);
  },

  // Printable HTML view (open in a new tab / window).
  printUrl(debitNoteId: string) {
    return `${BASE}/${debitNoteId}/print`;
  },

  fetchPrintHtml(debitNoteId: string) {
    return api.get<string>(`${BASE}/${debitNoteId}/print`).then((r) => r.data);
  },

  // Tally import XML (Debit Note voucher).
  fetchTallyXml(debitNoteId: string) {
    return api.get<string>(`${BASE}/${debitNoteId}/tally`).then((r) => r.data);
  },
};

export default rtvDebitNotesApi;
