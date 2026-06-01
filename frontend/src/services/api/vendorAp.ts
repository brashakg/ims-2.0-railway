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

export const purchaseInvoicesApi = {
  // Reads are fail-soft: a backend that hasn't shipped the route yet (404/500)
  // returns an empty list so the Purchase page renders instead of erroring.
  list: async (params?: { vendor_id?: string; store_id?: string; status?: string }) => {
    try {
      const res = await api.get('/vendors/purchase-invoices', { params });
      const d = res.data as { purchase_invoices?: PurchaseInvoice[]; total?: number };
      return { purchase_invoices: d.purchase_invoices ?? [], total: d.total ?? (d.purchase_invoices?.length ?? 0) };
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
  create: async (payload: PurchaseInvoiceCreate) => {
    const res = await api.post('/vendors/purchase-invoices', payload);
    return res.data as PurchaseInvoice;
  },
  // Returns a server-prepared DRAFT prefilled from the ACCEPTED GRN (+ its PO).
  // Nothing is booked until create() is called with the reviewed draft.
  createFromGrn: async (grnId: string) => {
    const res = await api.post('/vendors/purchase-invoices/from-grn', { grn_id: grnId });
    return res.data as PurchaseInvoiceDraft;
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
