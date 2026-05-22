// ============================================================================
// IMS 2.0 - Finance API
// ============================================================================
// Real finance endpoints (backend/api/routers/finance.py). Replaces the
// fabricated generateSampleData the Finance dashboard used to render.

import api from './client';

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
};
