// ============================================================================
// IMS 2.0 - Expenses API
// ============================================================================

import api from './client';

export const expensesApi = {
  getExpenses: async (params?: { store_id?: string; status?: string; from_date?: string; to_date?: string }) => {
    const response = await api.get('/expenses/', { params });
    return response.data;
  },

  createExpense: async (data: { category: string; amount: number; description: string; expense_date?: string; store_id?: string }) => {
    const response = await api.post('/expenses/', data);
    return response.data;
  },

  approveExpense: async (expenseId: string) => {
    const response = await api.post(`/expenses/${expenseId}/approve`);
    return response.data;
  },

  rejectExpense: async (expenseId: string, reason: string) => {
    const response = await api.post(`/expenses/${expenseId}/reject`, null, { params: { reason } });
    return response.data;
  },

  submitExpense: async (expenseId: string) => {
    const response = await api.post(`/expenses/${expenseId}/submit`);
    return response.data;
  },
};
