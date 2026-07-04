// ============================================================================
// IMS 2.0 - Goods-Receipt Cockpit API  (Purchase P1 / S4)
// ============================================================================
// Import DIRECTLY from this module, NOT via the services/api barrel
// (barrel re-exports of newly-added services fail with TS2614 in this repo).

import api from './client';

// ---- Cockpit payload types -------------------------------------------------

export interface CockpitOpenPOLine {
  product_id: string | null;
  product_name: string | null;
  sku: string | null;
  ordered_qty: number;
  received_qty: number;
  pending_qty: number;
  unit_price: number | null;
  tax_rate: number | null;
}

export interface CockpitOpenPO {
  po_id: string;
  po_number: string;
  status: string;
  expected_date: string | null;
  lines: CockpitOpenPOLine[];
}

export interface CockpitPendingItem {
  product_id: string;
  product_name: string | null;
  sku: string | null;
  ordered_qty: number;
  received_qty: number;
  pending_qty: number;
}

export interface CockpitCatalogedItem {
  product_id: string;
  product_name: string | null;
  sku: string | null;
  category: string | null;
}

export interface CockpitPayload {
  vendor_id: string;
  open_pos: CockpitOpenPO[];
  pending_not_received: CockpitPendingItem[];
  pending_cataloged: CockpitCatalogedItem[];
}

// ---- GRN item for the create call -----------------------------------------

export interface GRNItemInput {
  po_item_id?: string;
  product_id: string;
  received_qty: number;
  accepted_qty: number;
  rejected_qty: number;
  rejection_reason?: string;
  // P2: supplier batch + expiry (contact lenses) -> dates the minted units for
  // FEFO. Optional; omitted for frames / undated spectacle lenses.
  batch_code?: string;
  expiry_date?: string;
}

// ---- Upload-doc response ---------------------------------------------------

export interface UploadDocResult {
  file_id: string | null;
  filename: string;
  mime: string;
  size: number;
  sha256: string;
  persisted: boolean;
}

// ---- Create-GRN response ---------------------------------------------------

export interface CreateGRNResult {
  grn_id: string;
  grn_number: string;
  grn_subtype: string;
  dc_number: string | null;
  total_received: number;
  has_discrepancy: boolean;
  message: string;
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

// ---- Vendor list type (minimal shape for the picker) ----------------------

export interface VendorOption {
  vendor_id: string;
  trade_name?: string;
  legal_name?: string;
  display_name?: string;
}

// ---- API -------------------------------------------------------------------

export const grnCockpitApi = {
  /**
   * Fetch the three worklists for a vendor (+ optional store filter).
   * Returns open POs with residual lines, per-product pending totals,
   * and ACTIVE cataloged items not already on an open PO.
   */
  getCockpit: async (params: {
    vendor_id: string;
    store_id?: string;
  }): Promise<CockpitPayload> => {
    const res = await api.get('/vendors/goods-receipt/cockpit', { params });
    return res.data as CockpitPayload;
  },

  /**
   * Upload a vendor invoice / delivery challan image or PDF.
   * MUST be called before createGRN for a STANDARD GRN.
   * Returns the file_id to pass as attachment_file_id in createGRN.
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
   * Create a STANDARD goods receipt note.
   * attachment_file_id MUST be the file_id returned by uploadDoc — the
   * backend returns 400 {code:"ATTACHMENT_REQUIRED",...} otherwise.
   */
  createGRN: async (payload: {
    po_id: string;
    vendor_invoice_no: string;
    attachment_file_id: string;
    items: GRNItemInput[];
    notes?: string;
  }): Promise<CreateGRNResult> => {
    const res = await api.post('/vendors/grn', {
      ...payload,
      grn_subtype: 'STANDARD',
      vendor_invoice_date: new Date().toISOString().split('T')[0],
    });
    return res.data as CreateGRNResult;
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
   *   500 {code:"EXPRESS_PARTIAL", grn_id, grn_number, message, grn_status?}
   *       -> the GRN EXISTS but is not (fully) accepted: send the user to the
   *          pending-receipts panel (it handles accept/void).
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

  /**
   * List vendors for the vendor picker.
   * Fail-soft -> [] when the DB is unavailable so the picker renders empty
   * rather than error-screen.
   */
  listVendors: async (storeId?: string): Promise<VendorOption[]> => {
    try {
      const res = await api.get('/vendors', {
        params: storeId ? { store_id: storeId } : {},
      });
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const d = res.data as any;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const raw: any[] = Array.isArray(d) ? d : d.vendors ?? d.data ?? [];
      return raw.map((v) => ({
        vendor_id: v.vendor_id ?? v.id ?? '',
        trade_name: v.trade_name,
        legal_name: v.legal_name,
        display_name: v.trade_name ?? v.legal_name ?? v.vendor_id,
      }));
    } catch {
      return [];
    }
  },
};
