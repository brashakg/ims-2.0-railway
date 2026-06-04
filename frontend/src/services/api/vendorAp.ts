// ============================================================================
// IMS 2.0 - Vendor accounts-payable + cash-flow API
// ============================================================================
// Import this directly (not via the services/api barrel) -- newly-added
// services don't resolve through the barrel re-export (TS2614).

import api from './client';

// ---- Types -------------------------------------------------------------
export interface VendorBill {
  bill_id: string;
  vendor_id: string;
  vendor_name?: string;
  bill_number: string;
  bill_date: string;
  due_date?: string;
  taxable_amount: number;
  tax_amount: number;
  total_amount: number;
  outstanding?: number;
  status?: string;
  po_id?: string;
  grn_id?: string;
  notes?: string;
}

export interface VendorPayment {
  payment_id: string;
  vendor_id: string;
  bill_id?: string;
  amount: number;
  mode: string;
  payment_date: string;
  tds_section?: string;
  tds_amount?: number;
  reference?: string;
  notes?: string;
}

export interface DebitNote {
  debit_note_id: string;
  debit_note_number?: string;
  vendor_id: string;
  bill_id?: string;
  amount: number;
  date: string;
  reason: string;
}

// ---- Purchase Invoice (first-class AP + ITC document) -------------------
// A purchase invoice is the supplier's tax invoice booked into AP. Unlike the
// header-only VendorBill, it carries line items (with HSN + per-rate GST) and
// an explicit place_of_supply so the tax is split correctly:
//   intra-state  -> CGST + SGST
//   inter-state  -> IGST    (place_of_supply state != recipient/entity state)
// place_of_supply being WRITTEN here is the fix for the long-standing bug
// where every inter-state purchase was mis-booked as CGST+SGST.
export interface PurchaseInvoiceLine {
  product_id?: string;
  product_name: string;
  sku?: string;
  hsn_code?: string;
  quantity: number;
  unit_price: number;
  gst_rate: number;       // percent, e.g. 5 / 12 / 18
  taxable_amount?: number; // qty * unit_price (server recomputes; sent for convenience)
  cgst?: number;
  sgst?: number;
  igst?: number;
  line_total?: number;
  // Phase 2 (inventory valuation): the per-unit landed cost trued-up from this
  // invoice, surfaced read-only IF a future backend stamps it on the line.
  // (Today the moving-average true-up writes to the product master, not the
  // line, so this is optional + simply hidden when absent -- fail-soft.)
  unit_cost?: number;
  valuation_amount?: number;   // unit_cost * qty (the stock value)
}

export interface PurchaseInvoice {
  purchase_invoice_id: string;
  invoice_number?: string;        // our internal doc number (server-assigned)
  vendor_id: string;
  vendor_name?: string;
  vendor_invoice_no: string;      // supplier's invoice number (statutory)
  vendor_invoice_date: string;
  po_id?: string;
  po_number?: string;
  grn_id?: string;
  grn_number?: string;
  store_id?: string;
  place_of_supply?: string;       // 2-digit state code or "NN-StateName"
  recipient_gstin?: string;       // our GSTIN receiving the supply
  vendor_gstin?: string;
  is_interstate?: boolean;
  lines: PurchaseInvoiceLine[];
  taxable_amount: number;
  cgst: number;
  sgst: number;
  igst: number;
  tax_amount: number;
  total_amount: number;
  status?: string;                // DRAFT | BOOKED | ...
  bill_id?: string;               // AP bill this invoice posted into
  notes?: string;
  created_at?: string;
  // Phase 2 (3-way match): the verdict of comparing PO ordered vs GRN received
  // vs invoice invoiced (qty/price/tax within tolerance). The backend may embed
  // a compact summary on the list row; the full per-line breakdown is fetched
  // on demand via getMatch(). Both are optional so a Phase-1 backend (no match
  // engine yet) simply renders without the match column -- never white-screens.
  match_status?: MatchStatus | null;
  match_detail?: PurchaseInvoiceMatch | null;
  // Recorded when an ON_HOLD_EXCEPTION was approved (control-bypass audit).
  exception_override?: ExceptionOverride;
}

// ---- Phase 2: 3-way match + inventory valuation -------------------------
// A purchase invoice is "3-way matched" when, for every line, the quantity and
// unit price agree across the PURCHASE ORDER (what we ordered), the GRN (what we
// received/accepted into stock) and the INVOICE (what the supplier billed),
// within the configured tolerance. Anything outside tolerance puts the invoice
// ON_HOLD_EXCEPTION so a human reviews it before it is paid -- "Control over
// Convenience". An authorised user can Approve the exception, which records a
// MATCHED_OVERRIDE (paid despite the variance, with an audit trail).
export type MatchStatus =
  | 'MATCHED'             // every line within tolerance
  | 'ON_HOLD_EXCEPTION'   // one or more lines outside tolerance -> needs review
  | 'MATCHED_OVERRIDE'    // a reviewer approved the exception
  | 'UNMATCHED'           // no PO/GRN to match against (manual invoice)
  | 'NOT_APPLICABLE';     // match not computed (e.g. backend not ready)

// Per-product match line, side-by-side: ordered (PO) vs received (GRN) vs
// invoiced (invoice), with the % variances + human reasons the backend raised.
// Field names mirror services/purchase_match.three_way_match exactly.
export interface MatchLine {
  product_id?: string;
  description?: string;            // backend names it `description` (not product_name)
  hsn?: string;
  // PURCHASE ORDER side
  ordered_qty?: number | null;
  po_unit_price?: number | null;
  // GRN side (accepted units that entered stock)
  received_qty?: number | null;
  // INVOICE side (what the supplier billed)
  invoiced_qty?: number;
  invoice_unit_price?: number;
  // Signed % variances (null when not comparable, e.g. product not on the PO)
  qty_variance_pct?: number | null;
  price_variance_pct?: number | null;
  // Per-line verdict from the engine: "MATCHED" | "EXCEPTION".
  status?: 'MATCHED' | 'EXCEPTION';
  reasons?: string[];             // human-readable variance reasons for this line
}

// The nested 3-way-match detail (the `match_detail` object the backend stores on
// the invoice + returns from GET /{id}/match). Mirrors three_way_match's return.
export interface PurchaseInvoiceMatch {
  match_status: MatchStatus;
  tolerance_pct?: number;          // the +/- % tolerance the match used
  has_po?: boolean;
  has_grn?: boolean;
  lines: MatchLine[];
  exceptions?: string[];           // flat list of every reason across all lines
  summary?: { matched_lines?: number; exception_lines?: number; total_lines?: number };
}

// Recorded when an ADMIN/ACCOUNTANT overrides an ON_HOLD_EXCEPTION (the audit of
// a control bypass). Stored on the invoice doc + echoed by approve-exception.
export interface ExceptionOverride {
  approved_by?: string;
  reason?: string;
  approved_at?: string;
  prior_status?: string;
}

// Envelope returned by GET /{id}/match.
export interface MatchEnvelope {
  invoice_id?: string;
  match_status?: MatchStatus | null;
  match_detail?: PurchaseInvoiceMatch | null;
  po_id?: string;
  grn_id?: string;
}

// Envelope returned by POST /{id}/approve-exception.
export interface ApproveExceptionResult {
  invoice_id?: string;
  match_status?: MatchStatus;
  exception_override?: ExceptionOverride;
}

// The effective purchase config (GET /config -> { config, defaults,
// valuation_methods }). The active values live under `config`.
export interface PurchaseConfig {
  valuation_method?: string;       // MOVING_AVERAGE | FIFO
  match_tolerance_pct?: number;    // the +/- % tolerance for the 3-way match
}

export interface PurchaseInvoiceConfig {
  config?: PurchaseConfig;
  defaults?: PurchaseConfig;
  valuation_methods?: string[];
}

// Payload to create/book an invoice (manual or from a prepared GRN draft).
export interface PurchaseInvoiceCreate {
  vendor_id: string;
  vendor_invoice_no: string;
  vendor_invoice_date: string;
  place_of_supply?: string;
  recipient_gstin?: string;
  po_id?: string;
  grn_id?: string;
  store_id?: string;
  lines: PurchaseInvoiceLine[];
  notes?: string;
}

// Server-prepared draft returned by create-from-GRN: a NOT-yet-booked invoice
// prefilled from the GRN's accepted lines + the PO's unit prices. The user
// reviews / edits it, then books it via create().
export interface PurchaseInvoiceDraft extends Partial<PurchaseInvoice> {
  vendor_id: string;
  vendor_invoice_no: string;
  vendor_invoice_date: string;
  lines: PurchaseInvoiceLine[];
}

export interface LedgerEntry {
  date?: string;
  type: string;
  ref?: string;
  description?: string;
  debit: number;
  credit: number;
  balance: number;
}

export interface VendorLedger {
  vendor_id: string;
  vendor?: Record<string, unknown> | null;
  ledger: {
    entries: LedgerEntry[];
    closing_balance: number;
    total_billed: number;
    total_paid: number;
    total_tds: number;
    total_debit_notes: number;
  };
  aging: AgingResult;
}

export interface AgingResult {
  as_of: string;
  buckets: Record<string, number>;
  total_outstanding: number;
  unallocated_credits: number;
  net_payable: number;
  items?: Array<Record<string, unknown>>;
}

export interface ApAgingByVendor {
  as_of: string;
  totals: { buckets: Record<string, number>; total_outstanding: number; unallocated_credits: number; net_payable: number };
  vendors: Array<{
    vendor_id: string;
    vendor_name?: string;
    buckets: Record<string, number>;
    total_outstanding: number;
    net_payable: number;
  }>;
}

export interface OwnerDashboard {
  as_of: string;
  receivables: { total: number; buckets: Record<string, number>; overdue: number };
  payables: { total: number; buckets: Record<string, number>; overdue: number; due_7d: number; due_30d: number; unallocated_credits: number };
  net_position: number;
  this_month: { revenue: number; expenses: number; vendor_payments: number; net_cash_flow: number };
  alerts: Array<{ level: string; message: string }>;
}

export interface CashFlowForecast {
  opening_cash: number;
  as_of: string;
  horizon_days: number;
  weeks: Array<{ index: number; start: string; end: string; label: string; inflow: number; outflow: number; net: number; closing_balance: number }>;
  totals: { inflow: number; outflow: number; net: number; closing_balance: number };
  beyond_horizon: { inflow: number; outflow: number };
  lowest: { week_index: number; week_start: string; balance: number };
  assumptions?: Record<string, number>;
}

// ---- API ----------------------------------------------------------------
export const vendorApApi = {
  apAging: async () => {
    const res = await api.get('/vendors/ap-aging');
    return res.data as ApAgingByVendor;
  },
  ledger: async (vendorId: string) => {
    const res = await api.get(`/vendors/${vendorId}/ledger`);
    return res.data as VendorLedger;
  },
  listBills: async (vendorId: string) => {
    const res = await api.get(`/vendors/${vendorId}/bills`);
    return res.data as { bills: VendorBill[]; total: number };
  },
  createBill: async (vendorId: string, payload: Partial<VendorBill>) => {
    const res = await api.post(`/vendors/${vendorId}/bills`, payload);
    return res.data as VendorBill;
  },
  createPayment: async (vendorId: string, payload: Partial<VendorPayment> & { amount: number; payment_date: string }) => {
    const res = await api.post(`/vendors/${vendorId}/payments`, payload);
    return res.data as VendorPayment;
  },
  createDebitNote: async (vendorId: string, payload: Partial<DebitNote> & { amount: number; date: string; reason: string }) => {
    const res = await api.post(`/vendors/${vendorId}/debit-notes`, payload);
    return res.data as DebitNote;
  },
};

// The stored vendor_bills doc uses invoice_number / bill_number for the
// supplier invoice no, invoice_date / bill_date for the date, cgst_total /
// sgst_total / igst_total for the GST split, and `interstate` for the tax-type
// flag. The FE list + drawer read vendor_invoice_no / vendor_invoice_date /
// cgst / sgst / igst / is_interstate. Map the API doc onto those keys so the
// list shows the invoice no/date + GST amounts and labels CGST+SGST vs IGST
// correctly (instead of blanks + dashes).
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapInvoiceFromApi(doc: Record<string, any>): PurchaseInvoice {
  const cgst = doc.cgst ?? doc.cgst_total ?? 0;
  const sgst = doc.sgst ?? doc.sgst_total ?? 0;
  const igst = doc.igst ?? doc.igst_total ?? 0;
  return {
    ...doc,
    vendor_invoice_no: doc.vendor_invoice_no ?? doc.invoice_number ?? doc.bill_number ?? '',
    vendor_invoice_date: doc.vendor_invoice_date ?? doc.invoice_date ?? doc.bill_date ?? '',
    cgst,
    sgst,
    igst,
    is_interstate: doc.is_interstate ?? doc.interstate ?? igst > 0,
  } as PurchaseInvoice;
}

export const purchaseInvoicesApi = {
  // Reads are fail-soft: a backend that hasn't shipped the route yet (404/500)
  // returns an empty list so the Purchase page renders instead of erroring.
  list: async (params?: { vendor_id?: string; store_id?: string; status?: string }) => {
    try {
      const res = await api.get('/vendors/purchase-invoices', { params });
      const d = res.data as { purchase_invoices?: Record<string, unknown>[]; total?: number };
      const rows = (d.purchase_invoices ?? []).map(mapInvoiceFromApi);
      return { purchase_invoices: rows, total: d.total ?? rows.length };
    } catch {
      return { purchase_invoices: [] as PurchaseInvoice[], total: 0 };
    }
  },
  get: async (id: string) => {
    const res = await api.get(`/vendors/purchase-invoices/${id}`);
    return res.data as PurchaseInvoice;
  },
  // Writes THROW so booking failures (validation, missing GRN, period lock) are
  // surfaced loudly to the user rather than silently swallowed.
  // The FE form uses display-friendly keys (vendor_invoice_no / quantity /
  // product_name / hsn_code); the backend PurchaseInvoiceCreate schema wants
  // invoice_number / invoice_date and per-line description / qty / hsn. Map at
  // this seam so the form code + TS types stay stable and the POST never 422s.
  create: async (payload: PurchaseInvoiceCreate) => {
    const wire = {
      vendor_id: payload.vendor_id,
      invoice_number: payload.vendor_invoice_no,
      invoice_date: payload.vendor_invoice_date,
      place_of_supply: payload.place_of_supply,
      recipient_gstin: payload.recipient_gstin,
      po_id: payload.po_id,
      grn_id: payload.grn_id,
      store_id: payload.store_id,
      notes: payload.notes,
      lines: payload.lines.map((l) => ({
        product_id: l.product_id,
        description: l.product_name,
        hsn: l.hsn_code,
        qty: l.quantity,
        unit_price: l.unit_price,
        gst_rate: l.gst_rate,
        taxable: l.taxable_amount,
      })),
    };
    const res = await api.post('/vendors/purchase-invoices', wire);
    return mapInvoiceFromApi(res.data as Record<string, unknown>);
  },
  // Returns a server-prepared DRAFT prefilled from the ACCEPTED GRN (+ its PO).
  // Nothing is booked until create() is called with the reviewed draft.
  // Backend route is GET /from-grn/{grn_id} (it doesn't persist) -- a POST 405s.
  // The draft uses invoice_number / invoice_date; alias them to the FE's
  // vendor_invoice_no / vendor_invoice_date so the form prefill works.
  createFromGrn: async (grnId: string) => {
    const res = await api.get(`/vendors/purchase-invoices/from-grn/${grnId}`);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const d = res.data as Record<string, any>;
    return {
      ...d,
      vendor_invoice_no: d.vendor_invoice_no ?? d.invoice_number ?? '',
      vendor_invoice_date: d.vendor_invoice_date ?? d.invoice_date ?? '',
    } as PurchaseInvoiceDraft;
  },
  // Phase 2: the 3-way match breakdown for one invoice. The backend returns an
  // envelope { invoice_id, match_status, match_detail, po_id, grn_id }; we unwrap
  // and return the nested `match_detail` (which carries the lines/exceptions).
  // Fail-soft -> null when the backend hasn't shipped the match engine yet
  // (404/500) or the invoice has no PO/GRN to match -> the drawer hides the
  // section instead of throwing/white-screening.
  getMatch: async (id: string): Promise<PurchaseInvoiceMatch | null> => {
    try {
      const res = await api.get(`/vendors/purchase-invoices/${id}/match`);
      const env = res.data as MatchEnvelope;
      const detail = env?.match_detail;
      if (detail && Array.isArray(detail.lines)) return detail;
      // Tolerate a bare verdict with no detail -> synthesise an empty-lines shape
      // so the caller can still show the status badge.
      if (env?.match_status) return { match_status: env.match_status, lines: [] };
      return null;
    } catch {
      return null;
    }
  },
  // Approve an ON_HOLD_EXCEPTION invoice -> records a MATCHED_OVERRIDE with an
  // audit trail. The backend REQUIRES a non-empty `reason`. WRITE -> throws so a
  // failure (RBAC 403, not-on-hold 400, DB 5xx) surfaces loudly to the user.
  approveException: async (
    id: string,
    payload: { reason: string },
  ): Promise<ApproveExceptionResult> => {
    const res = await api.post(`/vendors/purchase-invoices/${id}/approve-exception`, payload);
    return res.data as ApproveExceptionResult;
  },
  // Phase 2: the active match/valuation settings (read-only display). Fail-soft
  // -> null when the backend doesn't expose a config route, so the note is just
  // omitted rather than erroring the tab.
  getConfig: async (): Promise<PurchaseInvoiceConfig | null> => {
    try {
      const res = await api.get('/vendors/purchase-invoices/config');
      return (res.data ?? null) as PurchaseInvoiceConfig | null;
    } catch {
      return null;
    }
  },
};

export const cashFlowApi = {
  ownerDashboard: async () => {
    const res = await api.get('/finance/owner-dashboard');
    return res.data as OwnerDashboard;
  },
  forecast: async (params?: { days?: number; opening_cash?: number; collection_lag_days?: number; recurring_monthly_outflow?: number }) => {
    const res = await api.get('/finance/cash-flow-forecast', { params });
    return res.data as CashFlowForecast;
  },
};
