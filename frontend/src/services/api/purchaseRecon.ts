// ============================================================================
// IMS 2.0 - Purchase Reconciliation API (S6 - Accountant Console)
// ============================================================================
// Import DIRECTLY from this module -- never via the services/api barrel
// (TS2614 re-export resolution issue with newly-added modules).
//
// Endpoints:
//   POST /api/v1/vendors/purchase-invoices/{invoice_id}/recon  -- write ticks
//   GET  /api/v1/vendors/purchase-invoices/{invoice_id}/recon  -- read ticks
//   GET  /api/v1/vendors/recon/worklists                       -- 4 worklists

import api from './client';

// ---- Types ---------------------------------------------------------------

export interface ReconBlock {
  // The 4 accountant tick flags
  reconciled: boolean;
  entered_tally: boolean;
  filed_gst: boolean;
  payment_settled: boolean;
  // Per-flag audit stamps (present only when the flag is true)
  reconciled_by?: string;
  reconciled_at?: string;
  entered_tally_by?: string;
  entered_tally_at?: string;
  filed_gst_by?: string;
  filed_gst_at?: string;
  payment_settled_by?: string;
  payment_settled_at?: string;
  // Optional free-text note
  note?: string;
  note_by?: string;
  note_at?: string;
  // Last-touch audit stamp
  last_updated_by?: string;
  last_updated_at?: string;
}

export interface ReconResponse {
  invoice_id: string;
  recon: ReconBlock;
}

export interface ReconUpdate {
  reconciled?: boolean;
  entered_tally?: boolean;
  filed_gst?: boolean;
  payment_settled?: boolean;
  note?: string;
}

// ---- Worklist types (mirrors backend _stock_yet_to_receive / _pending_* helpers)

export interface OpenLine {
  product_id?: string;
  product_name?: string;
  sku?: string;
  ordered_qty: number;
  received_qty: number;
  pending_qty: number;
}

export interface StockYetToReceiveRow {
  po_id?: string;
  po_number?: string;
  vendor_id?: string;
  status?: string;
  expected_date?: string;
  delivery_store_id?: string;
  total_pending_qty: number;
  open_lines: OpenLine[];
}

export interface VendorReturnRow {
  return_id?: string;
  vendor_id?: string;
  vendor_name?: string;
  store_id?: string;
  return_type?: string;
  status?: string;
  total_value?: number;
  credit_note_number?: string;
  created_at?: string;
}

export interface SchemeCreditNoteRow {
  credit_note_number?: string;
  vendor_id?: string;
  vendor_name?: string;
  amount?: number;
  amount_paise?: number;
  rebate_id?: string;
  created_at?: string;
}

export interface ReturnCreditNoteRow {
  return_id?: string;
  vendor_id?: string;
  vendor_name?: string;
  store_id?: string;
  status?: string;
  total_value?: number;
  credit_note_number?: string;
  created_at?: string;
}

export interface ReconWorklists {
  stock_yet_to_receive: StockYetToReceiveRow[];
  vendor_returns: VendorReturnRow[];
  pending_credit_notes_scheme: SchemeCreditNoteRow[];
  pending_credit_notes_return: ReturnCreditNoteRow[];
}

// ---- API -----------------------------------------------------------------

export const purchaseReconApi = {
  /**
   * Read the recon block for a single purchase invoice.
   * Returns an empty-flags block if no recon has been done yet (backend default).
   * Fail-soft: returns null on error so the UI degrades gracefully.
   */
  getRecon: async (invoiceId: string): Promise<ReconResponse | null> => {
    try {
      const res = await api.get(`/vendors/purchase-invoices/${invoiceId}/recon`);
      return res.data as ReconResponse;
    } catch {
      return null;
    }
  },

  /**
   * Write (or update) the recon ticks on a purchase invoice.
   * Only the fields you provide are changed; others remain as-is.
   * THROWS on error so the UI can show a toast failure.
   */
  upsertRecon: async (invoiceId: string, payload: ReconUpdate): Promise<ReconResponse> => {
    const res = await api.post(`/vendors/purchase-invoices/${invoiceId}/recon`, payload);
    return res.data as ReconResponse;
  },

  /**
   * Fetch all 4 accountant worklists in one call.
   * Fail-soft: returns empty lists on error (backend is also fail-soft per-list).
   */
  getWorklists: async (params?: {
    store_id?: string;
    vendor_id?: string;
  }): Promise<ReconWorklists> => {
    const empty: ReconWorklists = {
      stock_yet_to_receive: [],
      vendor_returns: [],
      pending_credit_notes_scheme: [],
      pending_credit_notes_return: [],
    };
    try {
      const res = await api.get('/vendors/recon/worklists', { params });
      return (res.data as ReconWorklists) ?? empty;
    } catch {
      return empty;
    }
  },

  /**
   * Mark a scheme / volume-rebate credit note as physically RECEIVED, so it
   * drops off the pending-scheme-CN worklist. THROWS on error so the UI can
   * toast a failure.
   */
  markSchemeCnReceived: async (creditNoteNumber: string): Promise<void> => {
    await api.post(
      `/vendors/recon/credit-notes/${encodeURIComponent(creditNoteNumber)}/mark-received`,
    );
  },
};
