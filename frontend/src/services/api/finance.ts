// ============================================================================
// IMS 2.0 - Finance API
// ============================================================================
// Real finance endpoints (backend/api/routers/finance.py). Replaces the
// fabricated generateSampleData the Finance dashboard used to render.

import api from './client';
import type { JournalEntry, ChartAccount } from '../../pages/finance/financeTypes';

export const financeApi = {
  getRevenue: async (params?: { period?: string; store_id?: string }) => {
    const response = await api.get('/finance/revenue', { params });
    return response.data;
  },

  getPnl: async (params?: { store_id?: string; from_date?: string; to_date?: string }) => {
    const response = await api.get('/finance/pnl', { params });
    return response.data;
  },

  getGstSummary: async (params?: { month?: number; year?: number }) => {
    const response = await api.get('/finance/gst/summary', { params });
    return response.data;
  },

  getOutstanding: async (params?: { store_id?: string }) => {
    const response = await api.get('/finance/outstanding', { params });
    return response.data;
  },

  getVendorPayments: async () => {
    const response = await api.get('/finance/vendor-payments');
    return response.data;
  },

  getCashFlow: async (params?: { period?: string; store_id?: string }) => {
    const response = await api.get('/finance/cash-flow', { params });
    return response.data;
  },

  getBudget: async (params?: { month?: number; year?: number; mode?: string }) => {
    const response = await api.get('/finance/budget', { params });
    return response.data;
  },

  getReconciliation: async () => {
    const response = await api.get('/finance/reconciliation');
    return response.data;
  },

  getPeriodLocks: async () => {
    const response = await api.get('/finance/period-locks');
    return response.data;
  },

  lockPeriod: async (month: number, year: number) => {
    const response = await api.post('/finance/period-lock', null, { params: { month, year } });
    return response.data;
  },

  // --- Phase 2/3: GST reconciliation, Tally export, P&L breakdowns ---
  getGstReconciliation: async (params?: { month?: number; year?: number; entity_id?: string }) => {
    const response = await api.get('/finance/gst/reconciliation', { params });
    return response.data;
  },

  getPnlByStore: async (params?: { from_date?: string; to_date?: string; entity_id?: string }) => {
    const response = await api.get('/finance/pnl/by-store', { params });
    return response.data;
  },

  getPnlByCategory: async (params?: { from_date?: string; to_date?: string; store_id?: string; entity_id?: string }) => {
    const response = await api.get('/finance/pnl/by-category', { params });
    return response.data;
  },

  getPeriodStatus: async (month: number, year: number) => {
    const response = await api.get('/finance/period-status', { params: { month, year } });
    return response.data;
  },

  downloadTallySalesJv: async (params: { from_date?: string; to_date?: string; store_id?: string; entity_id?: string }) => {
    const response = await api.get('/finance/tally/sales-jv', { params, responseType: 'blob' });
    return response.data as Blob;
  },

  // --- F34 Global target ticker ---
  // Privacy-stratified server-side: management roles get rupee + pace fields;
  // floor roles get pct_complete only (money keys absent from the payload).
  getTargetTicker: async (storeId?: string) => {
    const response = await api.get('/finance/target-ticker', {
      params: storeId ? { store_id: storeId } : {},
    });
    return response.data as TickerResponse;
  },

  updateTickerSettings: async (payload: { milestone_pcts: number[]; refresh_seconds: number }) => {
    const response = await api.post('/finance/target-ticker/settings', payload);
    return response.data as { milestone_pcts: number[]; refresh_seconds: number; saved: boolean };
  },

  // --- F17/#25 Maker-checker journal entries -------------------------------
  // The maker drafts a balanced voucher, submits it (opens an E4 approval), a
  // DIFFERENT checker PIN-approves, then posts. The PIN is sent in the body and
  // never stored client-side.
  listJournalEntries: async (params?: { store_id?: string; status?: string; maker_id?: string }) => {
    const response = await api.get('/finance/journal-entries', { params });
    return response.data as { journal_entries: JournalEntry[]; total: number };
  },

  getJournalEntry: async (jeId: string) => {
    const response = await api.get(`/finance/journal-entries/${jeId}`);
    return response.data as JournalEntry;
  },

  createJournalEntry: async (payload: {
    description: string;
    lines: Array<{ account_code: string; debit: number; credit: number; narration?: string }>;
    store_id?: string;
    entity_id?: string;
    entry_date?: string;
    reference?: string;
  }) => {
    const response = await api.post('/finance/journal-entries', payload);
    return response.data as { ok: boolean; je: JournalEntry };
  },

  submitJournalEntry: async (jeId: string) => {
    const response = await api.post(`/finance/journal-entries/${jeId}/submit`, {});
    return response.data;
  },

  approveJournalEntry: async (jeId: string, pin: string) => {
    const response = await api.post(`/finance/journal-entries/${jeId}/approve`, { pin });
    return response.data;
  },

  rejectJournalEntry: async (jeId: string, pin: string, note: string) => {
    const response = await api.post(`/finance/journal-entries/${jeId}/reject`, { pin, note });
    return response.data;
  },

  postJournalEntry: async (jeId: string) => {
    const response = await api.post(`/finance/journal-entries/${jeId}/post`, {});
    return response.data;
  },

  reverseJournalEntry: async (jeId: string) => {
    const response = await api.post(`/finance/journal-entries/${jeId}/reverse`, {});
    return response.data;
  },

  getChartOfAccounts: async (params?: { manual_only?: boolean }) => {
    const response = await api.get('/finance/chart-of-accounts', { params });
    return response.data as { accounts: ChartAccount[] };
  },

  downloadTallyJournalJv: async (params: { from_date?: string; to_date?: string; store_id?: string }) => {
    const response = await api.get('/finance/tally/journal-jv', { params, responseType: 'blob' });
    return response.data as Blob;
  },

  // --- B2B invoices -> Tally (accountant export console + reminder worklist) --
  // Owner decision: GST e-invoice (IRN) + e-way bill are issued IN TALLY, not in
  // IMS. The accountant pulls B2B sales invoices as Tally-importable XML and
  // keeps a worklist of which invoices still need handling in Tally.
  listB2BInvoices: async (params?: {
    from_date?: string;
    to_date?: string;
    store_id?: string;
    entity_id?: string;
    tally_status?: 'PENDING' | 'IN_TALLY' | 'DONE';
  }) => {
    const response = await api.get('/finance/b2b-invoices', { params });
    return response.data as B2BInvoiceListResponse;
  },

  downloadB2BInvoiceXml: async (orderId: string) => {
    const response = await api.get(
      `/finance/b2b-invoices/${encodeURIComponent(orderId)}/tally-xml`,
      { responseType: 'blob' },
    );
    return response.data as Blob;
  },

  exportB2BInvoicesToTally: async (orderIds: string[], markInTally = true) => {
    const response = await api.post(
      '/finance/b2b-invoices/export',
      { order_ids: orderIds, mark_in_tally: markInTally },
      { responseType: 'blob' },
    );
    return response.data as Blob;
  },

  markB2BInvoicesExported: async (orderIds: string[]) => {
    const response = await api.post('/finance/b2b-invoices/mark-exported', {
      order_ids: orderIds,
    });
    return response.data as { ok: boolean; marked: number; exported_by: string };
  },

  markB2BInvoiceDone: async (orderId: string) => {
    const response = await api.post(
      `/finance/b2b-invoices/${encodeURIComponent(orderId)}/mark-done`,
      {},
    );
    return response.data as { ok: boolean; order_id: string; tally_status: string };
  },

  setB2BAttentionNote: async (orderId: string, note: string) => {
    const response = await api.post(
      `/finance/b2b-invoices/${encodeURIComponent(orderId)}/attention-note`,
      { note },
    );
    return response.data as { ok: boolean; order_id: string; attention_note: string };
  },
};

// --- B2B invoice types ------------------------------------------------------
export type TallyStatus = 'PENDING' | 'IN_TALLY' | 'DONE';

export interface B2BInvoice {
  order_id: string;
  invoice_number: string;
  has_invoice_number: boolean;
  date: string;
  store_id: string;
  customer_id: string;
  customer_name: string;
  customer_gstin: string;
  place_of_supply: string;
  interstate: boolean;
  taxable: number;
  cgst: number;
  sgst: number;
  igst: number;
  tax: number;
  total: number;
  needs_eway: boolean;
  tally_status: TallyStatus;
  exported_to_tally: boolean;
  exported_at?: string | null;
  exported_by?: string | null;
  done_at?: string | null;
  done_by?: string | null;
  attention_note: string;
  age_days: number | null;
  overdue: boolean;
}

export interface B2BInvoiceSummary {
  count: number;
  pending: number;
  in_tally: number;
  done: number;
  needs_eway: number;
  overdue: number;
  exported: number;
  total_taxable: number;
  total_tax: number;
  total_value: number;
}

export interface B2BInvoiceListResponse {
  invoices: B2BInvoice[];
  summary: B2BInvoiceSummary;
  eway_threshold: number;
  pending_reminder_days: number;
}

// Per-store ticker entry. Money keys are OPTIONAL because they are ABSENT (not
// null) for floor roles -- the server never sends them, so the client can never
// reveal a rupee figure by flipping a flag.
export interface TickerStore {
  store_id: string;
  store_name?: string;
  monthly_target?: number | null;
  mtd_revenue?: number;
  pct_complete: number;
  days_elapsed: number;
  days_in_month: number;
  pace_revenue?: number;
  pace_delta?: number;
  milestones_fired: number[];
  no_target: boolean;
}

export interface TickerResponse {
  raw_visible: boolean;
  stores: TickerStore[];
  ticker_refresh_seconds: number;
}
