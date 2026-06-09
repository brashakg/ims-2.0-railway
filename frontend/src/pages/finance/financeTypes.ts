// ============================================================================
// IMS 2.0 - Finance Dashboard Shared Types
// ============================================================================

export type TabType =
  | 'revenue-pl'
  | 'gst'
  | 'outstanding'
  | 'cash-flow'
  | 'period'
  | 'budgets'
  | 'vendor-payments'
  | 'journal-entries';

// F17/#25 Maker-checker journal entries
export type JeStatus =
  | 'DRAFT'
  | 'SUBMITTED'
  | 'APPROVED'
  | 'REJECTED'
  | 'POSTED'
  | 'REVERSED';

export interface ChartAccount {
  account_code: string;
  account_name: string;
  account_type: 'ASSET' | 'LIABILITY' | 'EQUITY' | 'REVENUE' | 'EXPENSE';
  allow_manual_je: boolean;
  is_active?: boolean;
}

export interface JeLine {
  line_id?: string;
  account_code: string;
  account_name?: string;
  debit: number;   // paisa-exact integer from the server
  credit: number;  // paisa-exact integer from the server
  narration?: string | null;
}

export interface JournalEntry {
  je_id: string;
  je_number: string;
  store_id?: string | null;
  entity_id?: string | null;
  entry_date?: string | null;
  description: string;
  reference?: string | null;
  lines: JeLine[];
  total_debit: number;   // paisa
  total_credit: number;  // paisa
  status: JeStatus;
  maker_id: string;
  maker_name?: string | null;
  checker_id?: string | null;
  checker_name?: string | null;
  checker_note?: string | null;
  reversal_of?: string | null;
  reversed_by?: string | null;
  approval_request_id?: string | null;
  created_at?: string | null;
  submitted_at?: string | null;
  checked_at?: string | null;
  posted_at?: string | null;
}

export type GSTType = 'CGST_SGST' | 'IGST' | 'EXEMPT';

export interface RevenueData {
  period: string;
  gross_sales: number;
  deductions: number;
  net_revenue: number;
  gst_collected: number;
}

export interface ProfitLossStatement {
  revenue: number;
  cost_of_goods: number;
  gross_profit: number;
  operating_expenses: number;
  operating_profit: number;
  tax_expense: number;
  net_profit: number;
  profit_margin: number;
  period_start: string;
  period_end: string;
}

export interface GSTSummaryData {
  period: string;
  cgst_collected: number;
  sgst_collected: number;
  igst_collected: number;
  total_gst: number;
  gst_payable: number;
  input_tax_credit: number;
  gst_type: GSTType;
}

export interface OutstandingReceivable {
  id: string;
  customer_name: string;
  amount: number;
  gst_amount: number;
  due_date: string;
  days_overdue: number;
  status: 'active' | 'overdue' | 'disputed';
}

export interface CashFlowData {
  period: string;
  opening_balance: number;
  cash_inflows: number;
  cash_outflows: number;
  closing_balance: number;
  free_cash_flow: number;
}

export interface BudgetData {
  category: string;
  allocated: number;
  spent: number;
  remaining: number;
  variance: number;
  variance_percent: number;
}

export interface VendorPaymentData {
  id: string;
  vendor_name: string;
  amount_due: number;
  due_date: string;
  days_overdue: number;
  status: 'pending' | 'partial' | 'paid';
}
