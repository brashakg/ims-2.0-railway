// ============================================================================
// IMS 2.0 - Finance Dashboard Shared Types
// ============================================================================

import type { UserRole } from '../../types';

// OWNER RULING 2026-09-27 (one door): the dashboard's own Cash Flow and
// Budgets tabs are gone. Cash flow lives on /finance/cash-flow and budgets on
// /finance/budgeting; the dashboard header links to both.
export type TabType =
  | 'revenue-pl'
  | 'gst'
  | 'outstanding'
  | 'period'
  | 'vendor-payments'
  | 'journal-entries';

/** Each dashboard section's own URL. Revenue & P&L is the index route. */
export const FINANCE_TAB_PATHS: Record<TabType, string> = {
  'revenue-pl': '/finance/dashboard',
  gst: '/finance/dashboard/gst',
  outstanding: '/finance/dashboard/outstanding',
  period: '/finance/dashboard/period',
  'vendor-payments': '/finance/dashboard/vendor-payments',
  'journal-entries': '/finance/dashboard/journal-entries',
};

/** Who may open /finance/cash-flow: its route gate AND the dashboard's link to
 *  it read this one list (the backend gate is _require_finance_admin). */
export const CASH_FLOW_ROLES: UserRole[] = ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'];

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
  // NULL when the caller is not allowed to see salary figures. Net profit is
  // revenue - COGS - expenses - PAYROLL, so it is a payroll-derived number and
  // the backend strips it for anyone below ADMIN (owner ruling 2026-08-09).
  // It must stay nullable: the alternative the frontend used to do -- fall back
  // to gross profit minus operating expenses -- silently reports a profit with
  // the entire wage bill left out, which overstates it.
  net_profit: number | null;
  profit_margin: number | null;
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

export interface VendorPaymentData {
  id: string;
  vendor_name: string;
  amount_due: number;
  due_date: string;
  days_overdue: number;
  status: 'pending' | 'partial' | 'paid';
}
