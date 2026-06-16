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
