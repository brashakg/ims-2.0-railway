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
};

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
