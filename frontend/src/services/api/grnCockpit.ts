// ============================================================================
// IMS 2.0 - Receiving API: bill upload + express receive
// ============================================================================
// Import DIRECTLY from this module, NOT via the services/api barrel
// (barrel re-exports of newly-added services fail with TS2614 in this repo).

import api from './client';

// ---- Upload-doc response ---------------------------------------------------

export interface UploadDocResult {
  file_id: string | null;
  filename: string;
  mime: string;
  size: number;
  sha256: string;
  persisted: boolean;
}

// ---- Express receive (procurement Phase 2) ---------------------------------
// POST /vendors/grn/express — one-shot create+accept for a CLEAN delivery
// (every line rejected_qty=0 and accepted_qty == received_qty > 0). The server
// answers 400 {code:"EXPRESS_NOT_CLEAN"} for anything else so the UI falls
// back to the two-step create+accept path.

export interface ExpressReceiveItemInput {
  po_item_id?: string;
  product_id: string;
  received_qty: number;
  accepted_qty: number;
  rejected_qty?: number;
  location_code?: string;
  batch_code?: string;
  lot_number?: string;
  expiry_date?: string;
}

export interface ExpressInvoiceDraftPreview {
  vendor_id: string | null;
  invoice_number: string | null;
  place_of_supply: string | null;
  lines_count: number;
  totals: {
    taxable_total: number | null;
    cgst_total: number | null;
    sgst_total: number | null;
    igst_total: number | null;
    tax_total: number | null;
    total: number | null;
  };
}

export interface ExpressMatchPreview {
  match_status: string;
  exception_count: number;
}

export interface ExpressReceiveResult {
  grn_id: string;
  grn_number: string;
  accepted_units: number | null;
  po_status: string | null;
  invoice_draft: ExpressInvoiceDraftPreview | null;
  match_preview: ExpressMatchPreview | null;
  accountant_task_id: string | null;
}

// ---- API -------------------------------------------------------------------

export const grnCockpitApi = {
  /**
   * Upload a vendor invoice / delivery challan image or PDF.
   * MUST be called before a STANDARD or bought-without-PO receipt.
   * Returns the file_id to pass as attachment_file_id.
   */
  uploadDoc: async (file: File): Promise<UploadDocResult> => {
    const form = new FormData();
    form.append('file', file);
    const res = await api.post('/vendors/grn/upload-doc', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return res.data as UploadDocResult;
  },

  /**
   * One-shot express receive for a CLEAN delivery (procurement Phase 2).
   * Creates AND accepts the GRN server-side, then returns a purchase-invoice
   * DRAFT + 3-way match PREVIEW (nothing booked — the accountant attestation
   * stays human) and the accountant task id.
   *
   * Error contract (all under err.response.data.detail):
   *   400 {code:"EXPRESS_NOT_CLEAN"}     -> fall back to two-step create+accept
   *   400 {code:"EXPRESS_STANDARD_ONLY"} -> DC attempted; use the classic flow
   *   400 {code:"ATTACHMENT_REQUIRED"|"ATTACHMENT_INVALID"} -> re-upload bill
   *   409 {code:"EXPRESS_PARTIAL", grn_id, grn_number, message, grn_status?}
   *       -> the GRN EXISTS but is not (fully) accepted: send the user to the
   *          pending-receipts panel (it handles accept/void).
   *          409 and NOT 5xx on purpose: the shared client auto-retries every
   *          5xx POST three times, and each retry creates a new grn_id that
   *          mints the whole delivery again.
   */
  expressReceive: async (payload: {
    po_id: string;
    vendor_invoice_no: string;
    vendor_invoice_date?: string;
    items: ExpressReceiveItemInput[];
    attachment_file_id: string;
    attachment_filename?: string;
    attachment_mime?: string;
    notes?: string;
  }): Promise<ExpressReceiveResult> => {
    const res = await api.post('/vendors/grn/express', payload);
    return res.data as ExpressReceiveResult;
  },

};
