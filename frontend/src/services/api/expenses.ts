// ============================================================================
// IMS 2.0 - Expenses API
// ============================================================================

import api from './client';

export interface ExpenseRecord {
  id?: string;
  expense_id: string;
  employee_id?: string;
  employee_name?: string;
  store_id?: string;
  category: string;
  amount: number;
  description: string;
  expense_date?: string;
  payment_mode?: string | null;
  status: string; // PENDING | APPROVED | REJECTED | SENT_TO_ACCOUNTANT | ENTERED
  created_at?: string;
  submitted_at?: string;
  approved_by?: string;
  approved_at?: string;
  rejection_reason?: string;
  sent_to_accountant_at?: string;
  entered_at?: string;
  ledger_reference?: string;
  bill_file_id?: string;
  bill_filename?: string;
}

export interface ExpenseCapEntry {
  role: string;
  category: string;
  daily?: number | null;
  monthly?: number | null;
}

export interface ExpenseCapsConfig {
  caps: ExpenseCapEntry[];
  global: { daily?: number | null; monthly?: number | null };
}

export interface AgingBucket {
  count: number;
  amount: number;
}

export interface AgingRow {
  expense_id: string;
  employee_id?: string;
  employee_name?: string;
  store_id?: string;
  category?: string;
  amount: number;
  status: string;
  since?: string;
  days_pending: number;
  bucket: string;
}

export interface AgingReport {
  buckets: Record<'0-7' | '8-15' | '15+', AgingBucket>;
  rows: AgingRow[];
  total_count: number;
  total_amount: number;
}

export const expensesApi = {
  getExpenses: async (params?: { store_id?: string; status?: string; from_date?: string; to_date?: string }) => {
    const response = await api.get('/expenses/', { params });
    return response.data;
  },

  createExpense: async (data: {
    category: string;
    amount: number;
    description: string;
    expense_date?: string;
    payment_mode?: string;
    store_id?: string;
    advance_id?: string;
  }) => {
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

  // Workflow: approved -> sent to accountant -> entered in books
  sendToAccountant: async (expenseId: string) => {
    const response = await api.post(`/expenses/${expenseId}/send-to-accountant`);
    return response.data;
  },

  markEntered: async (expenseId: string, ledgerReference?: string) => {
    const response = await api.post(`/expenses/${expenseId}/mark-entered`, null, {
      params: ledgerReference ? { ledger_reference: ledgerReference } : undefined,
    });
    return response.data;
  },

  // Approver queue (PENDING expenses + advances for the store).
  getPendingApproval: async (storeId?: string) => {
    const response = await api.get('/expenses/pending-approval', { params: { store_id: storeId } });
    return response.data;
  },

  // Accountant queue (SENT_TO_ACCOUNTANT, awaiting ledger entry).
  getToEnter: async (storeId?: string) => {
    const response = await api.get('/expenses/to-enter', { params: { store_id: storeId } });
    return response.data;
  },

  uploadBill: async (expenseId: string, file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    const response = await api.post(`/expenses/${expenseId}/upload-bill`, fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return response.data;
  },

  // Per-(role, category) spend caps + global fallback.
  getCaps: async (): Promise<ExpenseCapsConfig> => {
    const response = await api.get('/expenses/caps');
    return response.data;
  },

  // Reimbursement aging (admin/accountant only).
  getAging: async (storeId?: string): Promise<AgingReport> => {
    const response = await api.get('/expenses/aging', { params: { store_id: storeId } });
    return response.data;
  },
};
