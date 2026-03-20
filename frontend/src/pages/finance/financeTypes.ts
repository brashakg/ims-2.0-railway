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
  | 'reconciliation';

export type GSTType = 'CGST_SGST' | 'IGST' | 'EXEMPT';
export type ReconciliationStatus = 'pending' | 'matched' | 'discrepancy';

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

export interface ReconciliationData {
  id: string;
  date: string;
  bank_amount: number;
  system_amount: number;
  difference: number;
  status: ReconciliationStatus;
}
