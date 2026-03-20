// ============================================================================
// IMS 2.0 - Finance Dashboard Utilities
// ============================================================================

import type {
  RevenueData,
  ProfitLossStatement,
  GSTSummaryData,
  OutstandingReceivable,
  CashFlowData,
  BudgetData,
  VendorPaymentData,
  ReconciliationData,
} from './financeTypes';

export const formatCurrency = (amount: number): string => {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: 0,
  }).format(amount);
};

export interface SampleData {
  revenueData: RevenueData[];
  plStatement: ProfitLossStatement;
  gstSummary: GSTSummaryData;
  outstanding: OutstandingReceivable[];
  cashFlow: CashFlowData[];
  budgets: BudgetData[];
  vendorPayments: VendorPaymentData[];
  reconciliation: ReconciliationData[];
}

export const generateSampleData = (dateFrom: string, dateTo: string): SampleData => ({
  revenueData: [
    {
      period: 'April 2025',
      gross_sales: 450000,
      deductions: 15000,
      net_revenue: 435000,
      gst_collected: 78300,
    },
    {
      period: 'May 2025',
      gross_sales: 520000,
      deductions: 18000,
      net_revenue: 502000,
      gst_collected: 90360,
    },
    {
      period: 'June 2025',
      gross_sales: 480000,
      deductions: 16000,
      net_revenue: 464000,
      gst_collected: 83520,
    },
  ],
  plStatement: {
    revenue: 1401000,
    cost_of_goods: 560400,
    gross_profit: 840600,
    operating_expenses: 210150,
    operating_profit: 630450,
    tax_expense: 113481,
    net_profit: 516969,
    profit_margin: 36.88,
    period_start: dateFrom,
    period_end: dateTo,
  },
  gstSummary: {
    period: 'Apr 2025 - Jun 2025',
    cgst_collected: 63140,
    sgst_collected: 63140,
    igst_collected: 25040,
    total_gst: 151320,
    gst_payable: 135588,
    input_tax_credit: 15732,
    gst_type: 'CGST_SGST',
  },
  outstanding: [
    {
      id: 'REC001',
      customer_name: 'ABC Optical Clinic',
      amount: 85000,
      gst_amount: 15300,
      due_date: '2025-06-15',
      days_overdue: 3,
      status: 'overdue',
    },
    {
      id: 'REC002',
      customer_name: 'XYZ Hospital',
      amount: 120000,
      gst_amount: 21600,
      due_date: '2025-07-10',
      days_overdue: 0,
      status: 'active',
    },
    {
      id: 'REC003',
      customer_name: 'Metro Eye Center',
      amount: 65000,
      gst_amount: 11700,
      due_date: '2025-07-25',
      days_overdue: 0,
      status: 'active',
    },
  ],
  cashFlow: [
    {
      period: 'April 2025',
      opening_balance: 150000,
      cash_inflows: 420000,
      cash_outflows: 280000,
      closing_balance: 290000,
      free_cash_flow: 140000,
    },
    {
      period: 'May 2025',
      opening_balance: 290000,
      cash_inflows: 485000,
      cash_outflows: 310000,
      closing_balance: 465000,
      free_cash_flow: 175000,
    },
    {
      period: 'June 2025',
      opening_balance: 465000,
      cash_inflows: 450000,
      cash_outflows: 340000,
      closing_balance: 575000,
      free_cash_flow: 110000,
    },
  ],
  budgets: [
    {
      category: 'Employee Salaries',
      allocated: 180000,
      spent: 165000,
      remaining: 15000,
      variance: 15000,
      variance_percent: 8.3,
    },
    {
      category: 'Rent & Utilities',
      allocated: 60000,
      spent: 58500,
      remaining: 1500,
      variance: 1500,
      variance_percent: 2.5,
    },
    {
      category: 'Marketing',
      allocated: 40000,
      spent: 45200,
      remaining: -5200,
      variance: -5200,
      variance_percent: -13.0,
    },
    {
      category: 'Inventory',
      allocated: 150000,
      spent: 142800,
      remaining: 7200,
      variance: 7200,
      variance_percent: 4.8,
    },
  ],
  vendorPayments: [
    {
      id: 'VEN001',
      vendor_name: 'Global Optical Supplies',
      amount_due: 95000,
      due_date: '2025-06-20',
      days_overdue: 0,
      status: 'pending',
    },
    {
      id: 'VEN002',
      vendor_name: 'Precision Lens Manufacturing',
      amount_due: 125000,
      due_date: '2025-07-05',
      days_overdue: 0,
      status: 'pending',
    },
    {
      id: 'VEN003',
      vendor_name: 'Frame & Accessories Ltd',
      amount_due: 35000,
      due_date: '2025-06-15',
      days_overdue: 3,
      status: 'partial',
    },
  ],
  reconciliation: [
    {
      id: 'REC001',
      date: '2025-06-25',
      bank_amount: 575000,
      system_amount: 575000,
      difference: 0,
      status: 'matched',
    },
    {
      id: 'REC002',
      date: '2025-06-26',
      bank_amount: 24500,
      system_amount: 24500,
      difference: 0,
      status: 'matched',
    },
    {
      id: 'REC003',
      date: '2025-06-27',
      bank_amount: 8200,
      system_amount: 8500,
      difference: -300,
      status: 'pending',
    },
  ],
});
