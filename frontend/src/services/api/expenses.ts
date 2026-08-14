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
  // Anti-fraud: SHA-256 fingerprint of the uploaded bill + duplicate flags.
  bill_sha256?: string;
  duplicate_bill?: boolean;
  duplicate_of?: string | null;
}

// Shape returned by POST /expenses/{id}/upload-bill.
export interface UploadBillResult {
  message: string;
  filename?: string;
  file_id?: string;
  persisted?: boolean;
  bill_sha256?: string;
  duplicate_bill?: boolean;
  duplicate_of?: string | null;
}

// ---------------------------------------------------------------------------
// ADVANCES
// ---------------------------------------------------------------------------
// OWNER RULING 2026-08-14: an advance is for a BUSINESS reason and never for
// pay. A salary advance goes through Payroll, which is ADMIN-only. This list
// must stay identical to ADVANCE_TYPES in backend/api/routers/expenses.py --
// the backend rejects anything else with a 422, and a backend test
// (test_advances_scope.py) reads THIS file and fails if the two drift apart.
// There is deliberately no "Other": a catch-all becomes the default choice and
// re-opens the free-text hole. The free-text "purpose" field carries the
// specifics of an unusual request.
export const ADVANCE_TYPES = [
  { value: 'TRAVEL', label: 'Travel — tickets, fuel, lodging for a work trip' },
  { value: 'LOCAL_CONVEYANCE', label: 'Local conveyance — auto or taxi to the lab, bank or a delivery' },
  { value: 'VENDOR_PAYMENT', label: 'Vendor payment — lens lab, supplier or courier paid on delivery' },
  { value: 'WORKSHOP_SUPPLIES', label: 'Workshop supplies — lab consumables, solutions, small tools' },
  { value: 'STORE_MAINTENANCE', label: 'Store maintenance — electrician, plumber, AC, signage' },
  { value: 'MARKETING_EVENT', label: 'Marketing event — camp, school screening, local promotion' },
  { value: 'STATUTORY_FEE', label: 'Government fee — trade licence, registration renewal' },
] as const;

export type AdvanceType = (typeof ADVANCE_TYPES)[number]['value'];

export const advanceTypeLabel = (v?: string) =>
  ADVANCE_TYPES.find((t) => t.value === v)?.label || v || '—';

export interface AdvanceRecord {
  advance_id: string;
  employee_id?: string;
  employee_name?: string;
  store_id?: string;
  advance_type?: string;
  amount: number;
  purpose?: string;
  expected_settlement_date?: string | null;
  // PENDING | APPROVED | DISBURSED | PARTIALLY_SETTLED | SETTLED
  status: string;
  created_at?: string;
  approved_by?: string;
  approved_at?: string;
  disbursed_at?: string;
  disbursement_reference?: string;
  settled_at?: string;
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

// F17 petty-cash float ledger entry (one row per movement).
export interface PettyCashLedgerEntry {
  txn_id: string;
  type: 'CREDIT' | 'DEBIT';
  delta: number;
  balance_after?: number | null;
  reason?: string;
  expense_id?: string | null;
  actor?: string;
  created_at?: string;
  reverses?: string;
}

export interface PettyCashBalance {
  ok: boolean;
  store_id: string;
  exists: boolean;
  balance: number;
  float_limit: number;
  low_balance_threshold: number;
  status?: string | null;
  is_low: boolean;
  opened_by?: string;
  opened_at?: string;
  recent_ledger: PettyCashLedgerEntry[];
}

// F17 EOD settlement: the day's position for a store (read).
export interface PettyCashSettlementRecord {
  settlement_id: string;
  store_id: string;
  entity_id?: string | null;
  settle_date: string;
  opening_float: number;
  credits_today: number;
  debits_today: number;
  payouts_count: number;
  expected_closing: number;
  counted_closing: number;
  variance: number;
  variance_status: 'BALANCED' | 'OVER' | 'SHORT';
  tolerance: number;
  status: string;
  note?: string | null;
  settled_by?: string;
  settled_at?: string;
}

export interface PettyCashSettlementPosition {
  ok: boolean;
  store_id: string;
  settle_date: string;
  exists: boolean;
  float_status?: string | null;
  opening_float: number;
  credits_today: number;
  debits_today: number;
  payouts_count: number;
  expected_closing: number;
  tolerance: number;
  settled: boolean;
  settlement?: PettyCashSettlementRecord | null;
  day_ledger: PettyCashLedgerEntry[];
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

  uploadBill: async (expenseId: string, file: File): Promise<UploadBillResult> => {
    const fd = new FormData();
    fd.append('file', file);
    const response = await api.post(`/expenses/${expenseId}/upload-bill`, fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return response.data;
  },

  // Anti-fraud watch-list: expenses whose bill matched an earlier receipt
  // (approver / finance gated).
  getDuplicateBills: async (storeId?: string) => {
    const response = await api.get('/expenses/duplicate-bills', { params: { store_id: storeId } });
    return response.data;
  },

  // Advances. The backend scopes this list: the approver tier
  // (ADMIN / AREA_MANAGER / STORE_MANAGER / ACCOUNTANT / SUPERADMIN) gets every
  // advance for the store; everyone else gets only their own, whatever is
  // passed here.
  getAdvances: async (params?: { store_id?: string; status?: string; employee_id?: string }) => {
    const response = await api.get('/expenses/advances', { params });
    return response.data as { advances: AdvanceRecord[]; total: number };
  },

  // advance_type must be one of ADVANCE_TYPES; anything else is a 422.
  createAdvance: async (data: {
    advance_type: AdvanceType;
    amount: number;
    purpose: string;
    expected_settlement_date?: string;
  }) => {
    const response = await api.post('/expenses/advances', data);
    return response.data;
  },

  approveAdvance: async (advanceId: string) => {
    const response = await api.post(`/expenses/advances/${advanceId}/approve`);
    return response.data;
  },

  disburseAdvance: async (advanceId: string, reference: string) => {
    const response = await api.post(`/expenses/advances/${advanceId}/disburse`, null, {
      params: { reference },
    });
    return response.data;
  },

  settleAdvance: async (advanceId: string) => {
    const response = await api.post(`/expenses/advances/${advanceId}/settle`);
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

  // F17 petty-cash float: balance + recent ledger for a store (manager /
  // accountant / admin). Fail-soft to a not-open envelope on the server side.
  getPettyCashBalance: async (storeId: string): Promise<PettyCashBalance> => {
    const response = await api.get('/expenses/petty-cash/balance', { params: { store_id: storeId } });
    return response.data;
  },

  // Open a store float (manager / admin). amount + optional limit/threshold.
  openPettyCashFloat: async (data: {
    store_id: string;
    amount: number;
    float_limit?: number;
    low_balance_threshold?: number;
  }) => {
    const response = await api.post('/expenses/petty-cash/open', data);
    return response.data;
  },

  // Top up a store float (manager / admin).
  topupPettyCashFloat: async (data: { store_id: string; amount: number; reason?: string }) => {
    const response = await api.post('/expenses/petty-cash/topup', data);
    return response.data;
  },

  // F17 EOD settlement: the day's position (opening / payouts / expected close)
  // + any recorded settlement for a store on an IST day.
  getPettyCashSettlementPosition: async (
    storeId: string,
    settleDate?: string,
  ): Promise<PettyCashSettlementPosition> => {
    const response = await api.get('/expenses/petty-cash/settlement/position', {
      params: { store_id: storeId, settle_date: settleDate },
    });
    return response.data;
  },

  // Record the counted closing cash + compute variance; closes the day.
  settlePettyCashDay: async (data: {
    store_id: string;
    counted_closing: number;
    settle_date?: string;
    note?: string;
  }) => {
    const response = await api.post('/expenses/petty-cash/settlement', data);
    return response.data;
  },

  // Settlement history for a store (newest first).
  listPettyCashSettlements: async (
    storeId: string,
    params?: { from_date?: string; to_date?: string; limit?: number },
  ): Promise<{ settlements: PettyCashSettlementRecord[]; total: number }> => {
    const response = await api.get('/expenses/petty-cash/settlement', {
      params: { store_id: storeId, ...(params || {}) },
    });
    return response.data;
  },
};
