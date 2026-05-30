// ============================================================================
// IMS 2.0 - Budgets API (dual-mode: planned vs actual)
// ============================================================================
// Real endpoints in backend/api/routers/budgets.py (mounted at /api/v1/budgets).
// Per store, per period (YYYY-MM), per head. REVENUE = income target; any other
// head = an expense category. Variance derives actuals from orders + APPROVED
// expenses server-side.

import api from './client';

export interface BudgetLine {
  budget_id: string | null;
  store_id: string;
  period: string; // YYYY-MM
  head: string; // 'REVENUE' or an expense category
  planned_amount: number;
}

export interface BudgetVarianceLine {
  head: string;
  is_revenue: boolean;
  planned: number;
  actual: number;
  variance: number; // actual - planned
  variance_pct: number | null; // null when there is no plan (planned == 0)
}

export interface BudgetVarianceTotals {
  revenue_planned: number;
  revenue_actual: number;
  revenue_variance: number;
  revenue_variance_pct: number | null;
  expense_planned: number;
  expense_actual: number;
  expense_variance: number;
  expense_variance_pct: number | null;
  net_planned: number;
  net_actual: number;
}

export interface BudgetVariance {
  store_id: string | null;
  period: string;
  lines: BudgetVarianceLine[];
  totals: BudgetVarianceTotals;
}

export const budgetsApi = {
  list: async (params: { store_id?: string; period?: string }): Promise<{ budgets: BudgetLine[]; total: number }> => {
    const response = await api.get('/budgets', { params });
    return response.data;
  },

  upsert: async (body: {
    store_id?: string;
    period: string;
    head: string;
    planned_amount: number;
  }): Promise<{ budget: BudgetLine; persisted: boolean }> => {
    const response = await api.post('/budgets', body);
    return response.data;
  },

  remove: async (budgetId: string): Promise<{ deleted: boolean; budget_id: string }> => {
    const response = await api.delete(`/budgets/${budgetId}`);
    return response.data;
  },

  variance: async (params: { store_id?: string; period: string }): Promise<BudgetVariance> => {
    const response = await api.get('/budgets/variance', { params });
    return response.data;
  },
};
