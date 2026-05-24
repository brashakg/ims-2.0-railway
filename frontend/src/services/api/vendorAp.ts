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
