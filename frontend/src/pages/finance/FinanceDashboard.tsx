// ============================================================================
// IMS 2.0 - Finance & Accounting Dashboard
// ============================================================================
// Comprehensive financial management for Indian optical retail accounting
// Supports GST management, P&L reporting, cash flow, reconciliation, budgeting

import { useState, useEffect } from 'react';
import {
  BarChart3,
  TrendingUp,
  TrendingDown,
  Calendar,
  FileText,
  Lock,
  Unlock,
  Download,
  Plus,
  Percent,
  Target,
  CreditCard,
  Loader2,
  Wallet,
  Building2,
  Scale,
  CheckCircle,
  AlertTriangle,
  X,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import clsx from 'clsx';

// Types for Finance Dashboard
type TabType = 'revenue-pl' | 'gst' | 'outstanding' | 'cash-flow' | 'period' | 'budgets' | 'vendor-payments' | 'reconciliation';
type GSTType = 'CGST_SGST' | 'IGST' | 'EXEMPT';
type ReconciliationStatus = 'pending' | 'matched' | 'discrepancy';

interface RevenueData {
  period: string;
  gross_sales: number;
  deductions: number;
  net_revenue: number;
  gst_collected: number;
}

interface ProfitLossStatement {
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

interface GSTSummaryData {
  period: string;
  cgst_collected: number;
  sgst_collected: number;
  igst_collected: number;
  total_gst: number;
  gst_payable: number;
  input_tax_credit: number;
  gst_type: GSTType;
}

interface OutstandingReceivable {
  id: string;
  customer_name: string;
  amount: number;
  gst_amount: number;
  due_date: string;
  days_overdue: number;
  status: 'active' | 'overdue' | 'disputed';
}

interface CashFlowData {
  period: string;
  opening_balance: number;
  cash_inflows: number;
  cash_outflows: number;
  closing_balance: number;
  free_cash_flow: number;
}

interface BudgetData {
  category: string;
  allocated: number;
  spent: number;
  remaining: number;
  variance: number;
  variance_percent: number;
}

interface VendorPaymentData {
  id: string;
  vendor_name: string;
  amount_due: number;
  due_date: string;
  days_overdue: number;
  status: 'pending' | 'partial' | 'paid';
}

interface ReconciliationData {
  id: string;
  date: string;
  bank_amount: number;
  system_amount: number;
  difference: number;
  status: ReconciliationStatus;
}

export default function FinanceDashboard() {
  const { user } = useAuth();
  const toast = useToast();

  // Tab management
  const [activeTab, setActiveTab] = useState<TabType>('revenue-pl');

  // Date filters
  const [dateFrom, setDateFrom] = useState(
    new Date(new Date().getFullYear(), 3, 1).toISOString().split('T')[0] // Financial year start: April 1
  );
  const [dateTo, setDateTo] = useState(new Date().toISOString().split('T')[0]);

  // Data states
  const [revenueData, setRevenueData] = useState<RevenueData[]>([]);
  const [plStatement, setPLStatement] = useState<ProfitLossStatement | null>(null);
  const [gstSummary, setGSTSummary] = useState<GSTSummaryData | null>(null);
  const [outstanding, setOutstanding] = useState<OutstandingReceivable[]>([]);
  const [cashFlow, setCashFlow] = useState<CashFlowData[]>([]);
  const [budgets, setBudgets] = useState<BudgetData[]>([]);
  const [vendorPayments, setVendorPayments] = useState<VendorPaymentData[]>([]);
  const [reconciliation, setReconciliation] = useState<ReconciliationData[]>([]);

  // UI states
  const [isLoading, setIsLoading] = useState(true);
  const [selectedYear, setSelectedYear] = useState('2025-2026'); // Financial year format
  const [periodLocked, setPeriodLocked] = useState(false);
  const [showBudgetModal, setShowBudgetModal] = useState(false);

  // Form states
  const [budgetCategory, setBudgetCategory] = useState('');
  const [budgetAmount, setBudgetAmount] = useState('');

  useEffect(() => {
    loadFinanceData();
  }, [activeTab, dateFrom, dateTo, selectedYear]);

  const loadFinanceData = async () => {
    setIsLoading(true);
    try {
      // Mock data initialization - in production, fetch from API
      setTimeout(() => {
        // Initialize sample data
        initializeSampleData();
        setIsLoading(false);
      }, 500);
    } catch (error) {
      toast.error('Failed to load financial data');
      setIsLoading(false);
    }
  };

  const initializeSampleData = () => {
    // Revenue data
    setRevenueData([
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
    ]);

    // P&L Statement
    setPLStatement({
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
    });

    // GST Summary
    setGSTSummary({
      period: `Apr 2025 - Jun 2025`,
      cgst_collected: 63140,
      sgst_collected: 63140,
      igst_collected: 25040,
      total_gst: 151320,
      gst_payable: 135588,
      input_tax_credit: 15732,
      gst_type: 'CGST_SGST',
    });

    // Outstanding Receivables
    setOutstanding([
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
    ]);

    // Cash Flow
    setCashFlow([
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
    ]);

    // Budget Allocations
    setBudgets([
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
    ]);

    // Vendor Payments
    setVendorPayments([
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
    ]);

    // Bank Reconciliation
    setReconciliation([
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
    ]);
  };

  const formatCurrency = (amount: number) => {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      minimumFractionDigits: 0,
    }).format(amount);
  };

  const handleLockPeriod = () => {
    if (user?.activeRole !== 'ACCOUNTANT' && user?.activeRole !== 'ADMIN') {
      toast.error('Only accountants and admins can lock periods');
      return;
    }
    setPeriodLocked(true);
    toast.success('Financial period locked successfully');
  };

  const handleUnlockPeriod = () => {
    if (user?.activeRole !== 'ADMIN' && user?.activeRole !== 'SUPERADMIN') {
      toast.error('Only admins can unlock periods');
      return;
    }
    setPeriodLocked(false);
    toast.success('Financial period unlocked successfully');
  };

  const handleAllocateBudget = () => {
    if (!budgetCategory || !budgetAmount) {
      toast.error('Please fill all budget fields');
      return;
    }
    toast.success(`Budget allocated for ${budgetCategory}`);
    setShowBudgetModal(false);
    setBudgetCategory('');
    setBudgetAmount('');
  };

  const handleReconcile = (_itemId: string) => {
    toast.success('Reconciliation item marked as matched');
  };

  // Tab: Revenue & P&L
  const RevenueTab = () => (
    <div className="space-y-6">
      {/* Revenue Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-gradient-to-br from-green-900 to-green-800 border border-green-700 rounded-lg p-6 text-white">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-green-200 text-sm font-medium">Total Revenue</p>
              <p className="text-2xl font-bold mt-2">{formatCurrency(1401000)}</p>
              <p className="text-xs text-green-300 mt-2">Apr - Jun 2025</p>
            </div>
            <TrendingUp className="w-10 h-10 text-green-400 opacity-50" />
          </div>
        </div>

        <div className="bg-gradient-to-br from-blue-900 to-blue-800 border border-blue-700 rounded-lg p-6 text-white">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-blue-200 text-sm font-medium">Gross Profit</p>
              <p className="text-2xl font-bold mt-2">{formatCurrency(840600)}</p>
              <p className="text-xs text-blue-300 mt-2">59.9% margin</p>
            </div>
            <BarChart3 className="w-10 h-10 text-blue-400 opacity-50" />
          </div>
        </div>

        <div className="bg-gradient-to-br from-purple-900 to-purple-800 border border-purple-700 rounded-lg p-6 text-white">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-purple-200 text-sm font-medium">Net Profit</p>
              <p className="text-2xl font-bold mt-2">{formatCurrency(516969)}</p>
              <p className="text-xs text-purple-300 mt-2">36.9% margin</p>
            </div>
            <TrendingUp className="w-10 h-10 text-purple-400 opacity-50" />
          </div>
        </div>

        <div className="bg-gradient-to-br from-orange-900 to-orange-800 border border-orange-700 rounded-lg p-6 text-white">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-orange-200 text-sm font-medium">Operating Expense</p>
              <p className="text-2xl font-bold mt-2">{formatCurrency(210150)}</p>
              <p className="text-xs text-orange-300 mt-2">15% of revenue</p>
            </div>
            <TrendingDown className="w-10 h-10 text-orange-400 opacity-50" />
          </div>
        </div>
      </div>

      {/* P&L Statement Detail */}
      {plStatement && (
        <div className="bg-slate-800 border border-slate-700 rounded-lg overflow-hidden">
          <div className="bg-slate-900 px-6 py-4 border-b border-slate-700">
            <h3 className="text-white font-semibold flex items-center gap-2">
              <FileText className="w-5 h-5 text-blue-400" />
              Profit & Loss Statement
            </h3>
          </div>
          <div className="p-6 space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="bg-slate-900 p-4 rounded border border-slate-700">
                <p className="text-slate-400 text-sm">Revenue</p>
                <p className="text-white text-lg font-semibold mt-1">
                  {formatCurrency(plStatement.revenue)}
                </p>
              </div>
              <div className="bg-slate-900 p-4 rounded border border-slate-700">
                <p className="text-slate-400 text-sm">Cost of Goods Sold</p>
                <p className="text-red-400 text-lg font-semibold mt-1">
                  {formatCurrency(plStatement.cost_of_goods)}
                </p>
              </div>

              <div className="col-span-2 bg-slate-700 p-4 rounded border border-slate-600">
                <p className="text-slate-300 text-sm">Gross Profit</p>
                <p className="text-green-400 text-xl font-semibold mt-1">
                  {formatCurrency(plStatement.gross_profit)}
                </p>
              </div>

              <div className="bg-slate-900 p-4 rounded border border-slate-700">
                <p className="text-slate-400 text-sm">Operating Expenses</p>
                <p className="text-red-400 text-lg font-semibold mt-1">
                  {formatCurrency(plStatement.operating_expenses)}
                </p>
              </div>
              <div className="bg-slate-900 p-4 rounded border border-slate-700">
                <p className="text-slate-400 text-sm">Tax Expense</p>
                <p className="text-red-400 text-lg font-semibold mt-1">
                  {formatCurrency(plStatement.tax_expense)}
                </p>
              </div>

              <div className="col-span-2 bg-gradient-to-r from-green-900 to-green-800 p-4 rounded border border-green-700">
                <p className="text-green-200 text-sm font-medium">Net Profit</p>
                <p className="text-green-300 text-2xl font-bold mt-1">
                  {formatCurrency(plStatement.net_profit)}
                </p>
                <p className="text-green-400 text-xs mt-2">
                  Profit Margin: {plStatement.profit_margin.toFixed(2)}%
                </p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Monthly Revenue Breakdown */}
      <div className="bg-slate-800 border border-slate-700 rounded-lg overflow-hidden">
        <div className="bg-slate-900 px-6 py-4 border-b border-slate-700">
          <h3 className="text-white font-semibold">Monthly Revenue Breakdown</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-slate-300">
            <thead className="bg-slate-900 border-b border-slate-700">
              <tr>
                <th className="px-6 py-3 text-left text-slate-400">Period</th>
                <th className="px-6 py-3 text-right text-slate-400">Gross Sales</th>
                <th className="px-6 py-3 text-right text-slate-400">Deductions</th>
                <th className="px-6 py-3 text-right text-slate-400">Net Revenue</th>
                <th className="px-6 py-3 text-right text-slate-400">GST Collected</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700">
              {revenueData.map((row) => (
                <tr key={row.period} className="hover:bg-slate-700/50 transition-colors">
                  <td className="px-6 py-4">{row.period}</td>
                  <td className="px-6 py-4 text-right font-medium">
                    {formatCurrency(row.gross_sales)}
                  </td>
                  <td className="px-6 py-4 text-right text-red-400">
                    -{formatCurrency(row.deductions)}
                  </td>
                  <td className="px-6 py-4 text-right font-medium text-green-400">
                    {formatCurrency(row.net_revenue)}
                  </td>
                  <td className="px-6 py-4 text-right font-medium text-blue-400">
                    {formatCurrency(row.gst_collected)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );

  // Tab: GST Management
  const GSTTab = () => (
    <div className="space-y-6">
      {/* GST Summary Cards */}
      {gstSummary && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="bg-gradient-to-br from-indigo-900 to-indigo-800 border border-indigo-700 rounded-lg p-6 text-white">
              <p className="text-indigo-200 text-sm font-medium">CGST Collected</p>
              <p className="text-2xl font-bold mt-2">
                {formatCurrency(gstSummary.cgst_collected)}
              </p>
              <p className="text-xs text-indigo-300 mt-2">Central GST</p>
            </div>

            <div className="bg-gradient-to-br from-violet-900 to-violet-800 border border-violet-700 rounded-lg p-6 text-white">
              <p className="text-violet-200 text-sm font-medium">SGST Collected</p>
              <p className="text-2xl font-bold mt-2">
                {formatCurrency(gstSummary.sgst_collected)}
              </p>
              <p className="text-xs text-violet-300 mt-2">State GST</p>
            </div>

            <div className="bg-gradient-to-br from-cyan-900 to-cyan-800 border border-cyan-700 rounded-lg p-6 text-white">
              <p className="text-cyan-200 text-sm font-medium">Total GST Payable</p>
              <p className="text-2xl font-bold mt-2">
                {formatCurrency(gstSummary.gst_payable)}
              </p>
              <p className="text-xs text-cyan-300 mt-2">Less Input Tax Credit</p>
            </div>
          </div>

          {/* GST Breakdown */}
          <div className="bg-slate-800 border border-slate-700 rounded-lg overflow-hidden">
            <div className="bg-slate-900 px-6 py-4 border-b border-slate-700">
              <h3 className="text-white font-semibold flex items-center gap-2">
                <Percent className="w-5 h-5 text-indigo-400" />
                GST Breakdown (18% standard rate)
              </h3>
            </div>
            <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-4">
                <div className="flex justify-between items-center p-4 bg-slate-900 rounded border border-slate-700">
                  <span className="text-slate-300">CGST (9%)</span>
                  <span className="text-indigo-400 font-semibold">
                    {formatCurrency(gstSummary.cgst_collected)}
                  </span>
                </div>
                <div className="flex justify-between items-center p-4 bg-slate-900 rounded border border-slate-700">
                  <span className="text-slate-300">SGST (9%)</span>
                  <span className="text-violet-400 font-semibold">
                    {formatCurrency(gstSummary.sgst_collected)}
                  </span>
                </div>
                <div className="flex justify-between items-center p-4 bg-slate-900 rounded border border-slate-700">
                  <span className="text-slate-300">IGST (0%)</span>
                  <span className="text-cyan-400 font-semibold">
                    {formatCurrency(gstSummary.igst_collected)}
                  </span>
                </div>
              </div>

              <div className="space-y-4">
                <div className="flex justify-between items-center p-4 bg-slate-700 rounded border border-slate-600">
                  <span className="text-slate-200 font-medium">Total GST Collected</span>
                  <span className="text-green-400 font-bold text-lg">
                    {formatCurrency(gstSummary.total_gst)}
                  </span>
                </div>
                <div className="flex justify-between items-center p-4 bg-slate-900 rounded border border-slate-700">
                  <span className="text-slate-300">Input Tax Credit</span>
                  <span className="text-orange-400 font-semibold">
                    {formatCurrency(gstSummary.input_tax_credit)}
                  </span>
                </div>
                <div className="flex justify-between items-center p-4 bg-gradient-to-r from-red-900 to-red-800 rounded border border-red-700">
                  <span className="text-red-200 font-medium">Net GST Payable</span>
                  <span className="text-red-300 font-bold text-lg">
                    {formatCurrency(gstSummary.gst_payable)}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );

  // Tab: Outstanding & Collections
  const OutstandingTab = () => (
    <div className="space-y-6">
      {/* Outstanding Summary */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-gradient-to-br from-red-900 to-red-800 border border-red-700 rounded-lg p-6 text-white">
          <p className="text-red-200 text-sm font-medium">Total Outstanding</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(outstanding.reduce((sum, r) => sum + r.amount, 0))}
          </p>
          <p className="text-xs text-red-300 mt-2">{outstanding.length} customers</p>
        </div>

        <div className="bg-gradient-to-br from-orange-900 to-orange-800 border border-orange-700 rounded-lg p-6 text-white">
          <p className="text-orange-200 text-sm font-medium">Overdue Amount</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(
              outstanding
                .filter((r) => r.status === 'overdue')
                .reduce((sum, r) => sum + r.amount, 0)
            )}
          </p>
          <p className="text-xs text-orange-300 mt-2">
            {outstanding.filter((r) => r.status === 'overdue').length} overdue
          </p>
        </div>

        <div className="bg-gradient-to-br from-green-900 to-green-800 border border-green-700 rounded-lg p-6 text-white">
          <p className="text-green-200 text-sm font-medium">With GST</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(outstanding.reduce((sum, r) => sum + r.amount + r.gst_amount, 0))}
          </p>
          <p className="text-xs text-green-300 mt-2">Including tax</p>
        </div>
      </div>

      {/* Receivables Table */}
      <div className="bg-slate-800 border border-slate-700 rounded-lg overflow-hidden">
        <div className="bg-slate-900 px-6 py-4 border-b border-slate-700">
          <h3 className="text-white font-semibold">Outstanding Receivables</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-slate-300">
            <thead className="bg-slate-900 border-b border-slate-700">
              <tr>
                <th className="px-6 py-3 text-left text-slate-400">Customer</th>
                <th className="px-6 py-3 text-right text-slate-400">Amount</th>
                <th className="px-6 py-3 text-right text-slate-400">GST</th>
                <th className="px-6 py-3 text-left text-slate-400">Due Date</th>
                <th className="px-6 py-3 text-center text-slate-400">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700">
              {outstanding.map((item) => (
                <tr key={item.id} className="hover:bg-slate-700/50 transition-colors">
                  <td className="px-6 py-4 font-medium">{item.customer_name}</td>
                  <td className="px-6 py-4 text-right font-semibold">
                    {formatCurrency(item.amount)}
                  </td>
                  <td className="px-6 py-4 text-right text-slate-400">
                    {formatCurrency(item.gst_amount)}
                  </td>
                  <td className="px-6 py-4 text-slate-400">{item.due_date}</td>
                  <td className="px-6 py-4 text-center">
                    <span
                      className={clsx(
                        'px-3 py-1 rounded-full text-xs font-semibold inline-block',
                        item.status === 'overdue'
                          ? 'bg-red-900/50 text-red-300 border border-red-700'
                          : 'bg-green-900/50 text-green-300 border border-green-700'
                      )}
                    >
                      {item.status === 'overdue'
                        ? `${item.days_overdue} days overdue`
                        : 'Active'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Vendor Payments */}
      <div className="bg-slate-800 border border-slate-700 rounded-lg overflow-hidden">
        <div className="bg-slate-900 px-6 py-4 border-b border-slate-700">
          <h3 className="text-white font-semibold">Vendor Payment Schedule</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-slate-300">
            <thead className="bg-slate-900 border-b border-slate-700">
              <tr>
                <th className="px-6 py-3 text-left text-slate-400">Vendor</th>
                <th className="px-6 py-3 text-right text-slate-400">Amount Due</th>
                <th className="px-6 py-3 text-left text-slate-400">Due Date</th>
                <th className="px-6 py-3 text-center text-slate-400">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700">
              {vendorPayments.map((item) => (
                <tr key={item.id} className="hover:bg-slate-700/50 transition-colors">
                  <td className="px-6 py-4 font-medium">{item.vendor_name}</td>
                  <td className="px-6 py-4 text-right font-semibold">
                    {formatCurrency(item.amount_due)}
                  </td>
                  <td className="px-6 py-4 text-slate-400">{item.due_date}</td>
                  <td className="px-6 py-4 text-center">
                    <span
                      className={clsx(
                        'px-3 py-1 rounded-full text-xs font-semibold inline-block',
                        item.status === 'pending'
                          ? 'bg-yellow-900/50 text-yellow-300 border border-yellow-700'
                          : item.status === 'partial'
                            ? 'bg-blue-900/50 text-blue-300 border border-blue-700'
                            : 'bg-green-900/50 text-green-300 border border-green-700'
                      )}
                    >
                      {item.status === 'pending' ? 'Pending' : 'Partial'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );

  // Tab: Cash Flow
  const CashFlowTab = () => (
    <div className="space-y-6">
      {/* Cash Flow Summary */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <div className="bg-gradient-to-br from-emerald-900 to-emerald-800 border border-emerald-700 rounded-lg p-6 text-white">
          <p className="text-emerald-200 text-sm font-medium">Current Cash Balance</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(cashFlow[cashFlow.length - 1]?.closing_balance || 0)}
          </p>
          <p className="text-xs text-emerald-300 mt-2">Latest month closing</p>
        </div>

        <div className="bg-gradient-to-br from-green-900 to-green-800 border border-green-700 rounded-lg p-6 text-white">
          <p className="text-green-200 text-sm font-medium">Total Cash Inflows</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(cashFlow.reduce((sum, cf) => sum + cf.cash_inflows, 0))}
          </p>
          <p className="text-xs text-green-300 mt-2">3-month period</p>
        </div>

        <div className="bg-gradient-to-br from-red-900 to-red-800 border border-red-700 rounded-lg p-6 text-white">
          <p className="text-red-200 text-sm font-medium">Total Cash Outflows</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(cashFlow.reduce((sum, cf) => sum + cf.cash_outflows, 0))}
          </p>
          <p className="text-xs text-red-300 mt-2">3-month period</p>
        </div>
      </div>

      {/* Cash Flow Trend */}
      <div className="bg-slate-800 border border-slate-700 rounded-lg overflow-hidden">
        <div className="bg-slate-900 px-6 py-4 border-b border-slate-700">
          <h3 className="text-white font-semibold">Cash Flow Analysis</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-slate-300">
            <thead className="bg-slate-900 border-b border-slate-700">
              <tr>
                <th className="px-6 py-3 text-left text-slate-400">Period</th>
                <th className="px-6 py-3 text-right text-slate-400">Opening Balance</th>
                <th className="px-6 py-3 text-right text-slate-400">Inflows</th>
                <th className="px-6 py-3 text-right text-slate-400">Outflows</th>
                <th className="px-6 py-3 text-right text-slate-400">Closing Balance</th>
                <th className="px-6 py-3 text-right text-slate-400">Free Cash Flow</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700">
              {cashFlow.map((row) => (
                <tr key={row.period} className="hover:bg-slate-700/50 transition-colors">
                  <td className="px-6 py-4 font-medium">{row.period}</td>
                  <td className="px-6 py-4 text-right text-slate-400">
                    {formatCurrency(row.opening_balance)}
                  </td>
                  <td className="px-6 py-4 text-right font-medium text-green-400">
                    {formatCurrency(row.cash_inflows)}
                  </td>
                  <td className="px-6 py-4 text-right font-medium text-red-400">
                    {formatCurrency(row.cash_outflows)}
                  </td>
                  <td className="px-6 py-4 text-right font-semibold text-emerald-400">
                    {formatCurrency(row.closing_balance)}
                  </td>
                  <td className="px-6 py-4 text-right font-semibold text-blue-400">
                    {formatCurrency(row.free_cash_flow)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Bank Reconciliation */}
      <div className="bg-slate-800 border border-slate-700 rounded-lg overflow-hidden">
        <div className="bg-slate-900 px-6 py-4 border-b border-slate-700 flex items-center justify-between">
          <h3 className="text-white font-semibold flex items-center gap-2">
            <CreditCard className="w-5 h-5 text-cyan-400" />
            Bank Reconciliation Status
          </h3>
          <span className="text-xs bg-green-900/50 text-green-300 px-3 py-1 rounded-full border border-green-700">
            Reconciled
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-slate-300">
            <thead className="bg-slate-900 border-b border-slate-700">
              <tr>
                <th className="px-6 py-3 text-left text-slate-400">Date</th>
                <th className="px-6 py-3 text-right text-slate-400">Bank Amount</th>
                <th className="px-6 py-3 text-right text-slate-400">System Amount</th>
                <th className="px-6 py-3 text-right text-slate-400">Difference</th>
                <th className="px-6 py-3 text-center text-slate-400">Status</th>
                <th className="px-6 py-3 text-center text-slate-400">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700">
              {reconciliation.map((item) => (
                <tr key={item.id} className="hover:bg-slate-700/50 transition-colors">
                  <td className="px-6 py-4">{item.date}</td>
                  <td className="px-6 py-4 text-right">{formatCurrency(item.bank_amount)}</td>
                  <td className="px-6 py-4 text-right">{formatCurrency(item.system_amount)}</td>
                  <td
                    className={clsx(
                      'px-6 py-4 text-right font-semibold',
                      item.difference === 0
                        ? 'text-green-400'
                        : 'text-red-400'
                    )}
                  >
                    {item.difference === 0 ? '✓ Match' : formatCurrency(item.difference)}
                  </td>
                  <td className="px-6 py-4 text-center">
                    <span
                      className={clsx(
                        'px-3 py-1 rounded-full text-xs font-semibold inline-block',
                        item.status === 'matched'
                          ? 'bg-green-900/50 text-green-300 border border-green-700'
                          : 'bg-yellow-900/50 text-yellow-300 border border-yellow-700'
                      )}
                    >
                      {item.status === 'matched' ? 'Matched' : 'Pending'}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-center">
                    {item.status === 'pending' && (
                      <button
                        onClick={() => handleReconcile(item.id)}
                        className="text-blue-400 hover:text-blue-300 transition-colors text-xs font-medium"
                      >
                        Mark Matched
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );

  // Tab: Period Management & Budget
  const PeriodTab = () => (
    <div className="space-y-6">
      {/* Period Status */}
      <div className="bg-slate-800 border border-slate-700 rounded-lg overflow-hidden">
        <div className="bg-slate-900 px-6 py-4 border-b border-slate-700">
          <h3 className="text-white font-semibold flex items-center gap-2">
            <Calendar className="w-5 h-5 text-cyan-400" />
            Financial Period Management
          </h3>
        </div>
        <div className="p-6 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="bg-slate-900 p-4 rounded border border-slate-700">
              <p className="text-slate-400 text-sm">Current Period</p>
              <p className="text-white text-lg font-semibold mt-2">April - June 2025</p>
              <p className="text-xs text-slate-500 mt-1">Financial Year: 2025-26</p>
            </div>
            <div className="bg-slate-900 p-4 rounded border border-slate-700">
              <p className="text-slate-400 text-sm">Period Status</p>
              <div className="flex items-center gap-2 mt-2">
                {periodLocked ? (
                  <Lock className="w-5 h-5 text-red-400" />
                ) : (
                  <Unlock className="w-5 h-5 text-green-400" />
                )}
                <span className="text-white text-lg font-semibold">
                  {periodLocked ? 'Locked' : 'Open'}
                </span>
              </div>
              <p className="text-xs text-slate-500 mt-1">
                {periodLocked ? 'No transactions allowed' : 'Editable'}
              </p>
            </div>
            <div className="bg-slate-900 p-4 rounded border border-slate-700">
              <p className="text-slate-400 text-sm">Last Reconciliation</p>
              <p className="text-white text-lg font-semibold mt-2">2025-06-27</p>
              <p className="text-xs text-green-400 mt-1">✓ Current</p>
            </div>
          </div>

          {/* Lock/Unlock Actions */}
          <div className="flex gap-3 pt-4 border-t border-slate-700">
            {!periodLocked ? (
              <button
                onClick={handleLockPeriod}
                className="flex items-center gap-2 px-4 py-2 bg-red-900 hover:bg-red-800 text-red-100 rounded border border-red-700 transition-colors font-medium text-sm"
              >
                <Lock className="w-4 h-4" />
                Lock Period
              </button>
            ) : (
              <button
                onClick={handleUnlockPeriod}
                className="flex items-center gap-2 px-4 py-2 bg-green-900 hover:bg-green-800 text-green-100 rounded border border-green-700 transition-colors font-medium text-sm"
              >
                <Unlock className="w-4 h-4" />
                Unlock Period
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Budget Tracking */}
      <div className="bg-slate-800 border border-slate-700 rounded-lg overflow-hidden">
        <div className="bg-slate-900 px-6 py-4 border-b border-slate-700 flex items-center justify-between">
          <h3 className="text-white font-semibold flex items-center gap-2">
            <Target className="w-5 h-5 text-purple-400" />
            Budget Allocations
          </h3>
          <button
            onClick={() => setShowBudgetModal(true)}
            className="flex items-center gap-2 px-3 py-1 bg-purple-900 hover:bg-purple-800 text-purple-100 rounded text-xs font-medium transition-colors"
          >
            <Plus className="w-4 h-4" />
            Allocate
          </button>
        </div>

        {/* Budget Summary Cards */}
        <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-4 bg-slate-900/50 border-b border-slate-700">
          <div className="bg-slate-800 p-4 rounded border border-slate-700">
            <p className="text-slate-400 text-sm">Total Allocated</p>
            <p className="text-white text-xl font-bold mt-2">
              {formatCurrency(budgets.reduce((sum, b) => sum + b.allocated, 0))}
            </p>
          </div>
          <div className="bg-slate-800 p-4 rounded border border-slate-700">
            <p className="text-slate-400 text-sm">Total Spent</p>
            <p className="text-white text-xl font-bold mt-2">
              {formatCurrency(budgets.reduce((sum, b) => sum + b.spent, 0))}
            </p>
          </div>
        </div>

        {/* Budget Table */}
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-slate-300">
            <thead className="bg-slate-900 border-b border-slate-700">
              <tr>
                <th className="px-6 py-3 text-left text-slate-400">Category</th>
                <th className="px-6 py-3 text-right text-slate-400">Allocated</th>
                <th className="px-6 py-3 text-right text-slate-400">Spent</th>
                <th className="px-6 py-3 text-right text-slate-400">Remaining</th>
                <th className="px-6 py-3 text-right text-slate-400">Variance %</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700">
              {budgets.map((budget) => (
                <tr key={budget.category} className="hover:bg-slate-700/50 transition-colors">
                  <td className="px-6 py-4 font-medium">{budget.category}</td>
                  <td className="px-6 py-4 text-right">{formatCurrency(budget.allocated)}</td>
                  <td className="px-6 py-4 text-right text-yellow-400 font-medium">
                    {formatCurrency(budget.spent)}
                  </td>
                  <td
                    className={clsx(
                      'px-6 py-4 text-right font-semibold',
                      budget.remaining >= 0 ? 'text-green-400' : 'text-red-400'
                    )}
                  >
                    {formatCurrency(budget.remaining)}
                  </td>
                  <td
                    className={clsx(
                      'px-6 py-4 text-right font-semibold',
                      budget.variance_percent >= 0 ? 'text-green-400' : 'text-red-400'
                    )}
                  >
                    {budget.variance_percent > 0 ? '+' : ''}
                    {budget.variance_percent.toFixed(1)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Budget Allocation Modal */}
      {showBudgetModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-slate-800 border border-slate-700 rounded-lg max-w-md w-full p-6">
            <h3 className="text-white font-semibold text-lg mb-4">Allocate Budget</h3>
            <div className="space-y-4">
              <div>
                <label className="text-slate-400 text-sm font-medium block mb-2">Category</label>
                <input
                  type="text"
                  value={budgetCategory}
                  onChange={(e) => setBudgetCategory(e.target.value)}
                  placeholder="e.g., Training & Development"
                  className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="text-slate-400 text-sm font-medium block mb-2">Amount (INR)</label>
                <input
                  type="number"
                  value={budgetAmount}
                  onChange={(e) => setBudgetAmount(e.target.value)}
                  placeholder="0"
                  className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>
            <div className="flex gap-3 mt-6">
              <button
                onClick={() => setShowBudgetModal(false)}
                className="flex-1 px-4 py-2 bg-slate-900 border border-slate-700 text-slate-300 rounded hover:bg-slate-800 transition-colors font-medium"
              >
                Cancel
              </button>
              <button
                onClick={handleAllocateBudget}
                className="flex-1 px-4 py-2 bg-purple-900 hover:bg-purple-800 text-purple-100 rounded border border-purple-700 transition-colors font-medium"
              >
                Allocate
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );

  // Main Component Rendering
  if (isLoading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 flex items-center justify-center p-4">
        <div className="text-center">
          <Loader2 className="w-12 h-12 text-blue-500 animate-spin mx-auto mb-4" />
          <p className="text-slate-400">Loading financial data...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 p-4 md:p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <div className="flex items-center justify-between mb-6">
            <div>
              <h1 className="text-3xl font-bold text-white flex items-center gap-3">
                <BarChart3 className="w-8 h-8 text-blue-400" />
                Finance & Accounting
              </h1>
              <p className="text-slate-400 mt-2">Financial year 2025-26 | Indian Accounting Standards</p>
            </div>
            <div className="flex gap-3">
              <select
                value={selectedYear}
                onChange={(e) => setSelectedYear(e.target.value)}
                className="bg-slate-900 border border-slate-700 text-white rounded px-4 py-2 text-sm focus:outline-none focus:border-blue-500"
              >
                <option>2025-2026</option>
                <option>2024-2025</option>
                <option>2023-2024</option>
              </select>
              <button className="flex items-center gap-2 px-4 py-2 bg-blue-900 hover:bg-blue-800 text-blue-100 rounded border border-blue-700 transition-colors font-medium text-sm">
                <Download className="w-4 h-4" />
                Export
              </button>
            </div>
          </div>

          {/* Date Filter */}
          <div className="flex flex-wrap gap-3">
            <div className="flex gap-2">
              <input
                type="date"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
                className="bg-slate-900 border border-slate-700 text-white rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
              />
              <input
                type="date"
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
                className="bg-slate-900 border border-slate-700 text-white rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
              />
            </div>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex flex-wrap gap-2 mb-6 border-b border-slate-700">
          {[
            { id: 'revenue-pl' as TabType, label: 'Revenue & P&L', icon: TrendingUp },
            { id: 'gst' as TabType, label: 'GST Management', icon: Percent },
            { id: 'outstanding' as TabType, label: 'Outstanding & Collections', icon: CreditCard },
            { id: 'cash-flow' as TabType, label: 'Cash Flow', icon: TrendingDown },
            { id: 'period' as TabType, label: 'Period Management', icon: Calendar },
            { id: 'budgets' as TabType, label: 'Budgets', icon: Target },
            { id: 'vendor-payments' as TabType, label: 'Vendor Payments', icon: Building2 },
            { id: 'reconciliation' as TabType, label: 'Reconciliation', icon: Scale },
          ].map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={clsx(
                'flex items-center gap-2 px-4 py-3 font-medium text-sm border-b-2 transition-colors',
                activeTab === id
                  ? 'text-blue-400 border-blue-400'
                  : 'text-slate-400 border-transparent hover:text-slate-300'
              )}
            >
              <Icon className="w-4 h-4" />
              {label}
            </button>
          ))}
        </div>

        {/* Tab Content */}
        <div className="animate-fadeIn">
          {activeTab === 'revenue-pl' && <RevenueTab />}
          {activeTab === 'gst' && <GSTTab />}
          {activeTab === 'outstanding' && <OutstandingTab />}
          {activeTab === 'cash-flow' && <CashFlowTab />}
          {activeTab === 'period' && <PeriodTab />}
          {activeTab === 'budgets' && <BudgetTab />}
          {activeTab === 'vendor-payments' && <VendorPaymentTab />}
          {activeTab === 'reconciliation' && <ReconciliationTab />}
        </div>
      </div>
    </div>
  );

  // ================================================================
  // BUDGET TRACKING TAB
  // ================================================================
  function BudgetTab() {
    const totalAllocated = budgets.reduce((s, b) => s + b.allocated, 0);
    const totalSpent = budgets.reduce((s, b) => s + b.spent, 0);
    return (
      <div className="space-y-6">
        {/* Summary Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
            <p className="text-sm text-slate-400">Total Budget</p>
            <p className="text-2xl font-bold text-white mt-1">{formatCurrency(totalAllocated)}</p>
          </div>
          <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
            <p className="text-sm text-slate-400">Total Spent</p>
            <p className="text-2xl font-bold text-blue-400 mt-1">{formatCurrency(totalSpent)}</p>
          </div>
          <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
            <p className="text-sm text-slate-400">Utilization</p>
            <p className="text-2xl font-bold text-white mt-1">{totalAllocated > 0 ? ((totalSpent / totalAllocated) * 100).toFixed(1) : 0}%</p>
          </div>
        </div>

        {/* Budget Allocation Button */}
        <div className="flex justify-between items-center">
          <h3 className="text-lg font-semibold text-white">Budget Allocations — FY {selectedYear}</h3>
          <button onClick={() => setShowBudgetModal(true)} className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700">
            <Plus className="w-4 h-4" /> Allocate Budget
          </button>
        </div>

        {/* Budget Table */}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-900 text-slate-400 text-left">
              <tr>
                <th className="px-4 py-3">Category</th>
                <th className="px-4 py-3 text-right">Allocated</th>
                <th className="px-4 py-3 text-right">Spent</th>
                <th className="px-4 py-3 text-right">Remaining</th>
                <th className="px-4 py-3 text-right">Variance %</th>
                <th className="px-4 py-3">Progress</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700">
              {budgets.map((b) => {
                const pct = b.allocated > 0 ? (b.spent / b.allocated) * 100 : 0;
                const overBudget = b.remaining < 0;
                return (
                  <tr key={b.category} className="text-white">
                    <td className="px-4 py-3 font-medium">{b.category}</td>
                    <td className="px-4 py-3 text-right">{formatCurrency(b.allocated)}</td>
                    <td className="px-4 py-3 text-right">{formatCurrency(b.spent)}</td>
                    <td className={clsx('px-4 py-3 text-right font-medium', overBudget ? 'text-red-400' : 'text-green-400')}>
                      {formatCurrency(b.remaining)}
                    </td>
                    <td className={clsx('px-4 py-3 text-right', b.variance_percent >= 0 ? 'text-green-400' : 'text-red-400')}>
                      {b.variance_percent > 0 ? '+' : ''}{b.variance_percent}%
                    </td>
                    <td className="px-4 py-3">
                      <div className="w-24 h-2 bg-slate-700 rounded-full overflow-hidden">
                        <div className={clsx('h-full rounded-full', overBudget ? 'bg-red-500' : pct > 80 ? 'bg-amber-500' : 'bg-green-500')} style={{ width: `${Math.min(pct, 100)}%` }} />
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Budget Modal */}
        {showBudgetModal && (
          <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
            <div className="bg-slate-800 border border-slate-700 rounded-xl p-6 w-full max-w-md">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-semibold text-white">Allocate Budget</h3>
                <button onClick={() => setShowBudgetModal(false)} className="text-slate-400 hover:text-white"><X className="w-5 h-5" /></button>
              </div>
              <div className="space-y-4">
                <div>
                  <label className="block text-sm text-slate-300 mb-1">Category</label>
                  <select value={budgetCategory} onChange={(e) => setBudgetCategory(e.target.value)} className="w-full bg-slate-900 border border-slate-600 text-white rounded-lg px-3 py-2 text-sm">
                    <option value="">Select category...</option>
                    <option value="Employee Salaries">Employee Salaries</option>
                    <option value="Rent & Utilities">Rent & Utilities</option>
                    <option value="Marketing">Marketing</option>
                    <option value="Inventory">Inventory</option>
                    <option value="Maintenance">Maintenance</option>
                    <option value="Technology">Technology</option>
                    <option value="Miscellaneous">Miscellaneous</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm text-slate-300 mb-1">Amount (₹)</label>
                  <input type="number" value={budgetAmount} onChange={(e) => setBudgetAmount(e.target.value)} placeholder="e.g. 50000" className="w-full bg-slate-900 border border-slate-600 text-white rounded-lg px-3 py-2 text-sm" />
                </div>
                <button onClick={handleAllocateBudget} className="w-full py-2.5 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700">Save Allocation</button>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  // ================================================================
  // VENDOR PAYMENTS TAB
  // ================================================================
  function VendorPaymentTab() {
    const totalDue = vendorPayments.reduce((s, v) => s + v.amount_due, 0);
    const overdueCount = vendorPayments.filter(v => v.days_overdue > 0).length;
    return (
      <div className="space-y-6">
        {/* Summary */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
            <p className="text-sm text-slate-400">Total Payable</p>
            <p className="text-2xl font-bold text-white mt-1">{formatCurrency(totalDue)}</p>
          </div>
          <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
            <p className="text-sm text-slate-400">Vendors with Dues</p>
            <p className="text-2xl font-bold text-amber-400 mt-1">{vendorPayments.length}</p>
          </div>
          <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
            <p className="text-sm text-slate-400">Overdue</p>
            <p className="text-2xl font-bold text-red-400 mt-1">{overdueCount}</p>
          </div>
        </div>

        <h3 className="text-lg font-semibold text-white">Vendor Payment Schedule</h3>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-900 text-slate-400 text-left">
              <tr>
                <th className="px-4 py-3">Vendor</th>
                <th className="px-4 py-3 text-right">Amount Due</th>
                <th className="px-4 py-3">Due Date</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3 text-right">Overdue</th>
                <th className="px-4 py-3">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700">
              {vendorPayments.map((v) => (
                <tr key={v.id} className="text-white">
                  <td className="px-4 py-3 font-medium">{v.vendor_name}</td>
                  <td className="px-4 py-3 text-right font-semibold">{formatCurrency(v.amount_due)}</td>
                  <td className="px-4 py-3 text-slate-300">{new Date(v.due_date).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })}</td>
                  <td className="px-4 py-3">
                    <span className={clsx('px-2 py-1 rounded text-xs font-medium', v.status === 'paid' ? 'bg-green-900/50 text-green-400' : v.status === 'partial' ? 'bg-amber-900/50 text-amber-400' : 'bg-slate-700 text-slate-300')}>
                      {v.status.charAt(0).toUpperCase() + v.status.slice(1)}
                    </span>
                  </td>
                  <td className={clsx('px-4 py-3 text-right', v.days_overdue > 0 ? 'text-red-400 font-medium' : 'text-slate-400')}>
                    {v.days_overdue > 0 ? `${v.days_overdue} days` : '—'}
                  </td>
                  <td className="px-4 py-3">
                    <button onClick={() => toast.success(`Payment recorded for ${v.vendor_name}`)} className="px-3 py-1.5 bg-green-600 text-white rounded text-xs hover:bg-green-700">
                      <Wallet className="w-3 h-3 inline mr-1" /> Pay
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  // ================================================================
  // BANK RECONCILIATION TAB
  // ================================================================
  function ReconciliationTab() {
    const matched = reconciliation.filter(r => r.status === 'matched').length;
    const pending = reconciliation.filter(r => r.status === 'pending').length;
    const discrepancies = reconciliation.filter(r => r.status === 'discrepancy').length;
    return (
      <div className="space-y-6">
        {/* Summary */}
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
          <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
            <p className="text-sm text-slate-400">Total Entries</p>
            <p className="text-2xl font-bold text-white mt-1">{reconciliation.length}</p>
          </div>
          <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
            <div className="flex items-center gap-2"><CheckCircle className="w-4 h-4 text-green-400" /><p className="text-sm text-slate-400">Matched</p></div>
            <p className="text-2xl font-bold text-green-400 mt-1">{matched}</p>
          </div>
          <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
            <div className="flex items-center gap-2"><Loader2 className="w-4 h-4 text-amber-400" /><p className="text-sm text-slate-400">Pending</p></div>
            <p className="text-2xl font-bold text-amber-400 mt-1">{pending}</p>
          </div>
          <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
            <div className="flex items-center gap-2"><AlertTriangle className="w-4 h-4 text-red-400" /><p className="text-sm text-slate-400">Discrepancies</p></div>
            <p className="text-2xl font-bold text-red-400 mt-1">{discrepancies}</p>
          </div>
        </div>

        <div className="flex justify-between items-center">
          <h3 className="text-lg font-semibold text-white">Bank vs System Reconciliation</h3>
          <button onClick={() => toast.info('Upload bank statement to auto-reconcile')} className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700">
            <Download className="w-4 h-4" /> Import Bank Statement
          </button>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-900 text-slate-400 text-left">
              <tr>
                <th className="px-4 py-3">Date</th>
                <th className="px-4 py-3 text-right">Bank Amount</th>
                <th className="px-4 py-3 text-right">System Amount</th>
                <th className="px-4 py-3 text-right">Difference</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700">
              {reconciliation.map((r) => (
                <tr key={r.id} className="text-white">
                  <td className="px-4 py-3">{new Date(r.date).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })}</td>
                  <td className="px-4 py-3 text-right">{formatCurrency(r.bank_amount)}</td>
                  <td className="px-4 py-3 text-right">{formatCurrency(r.system_amount)}</td>
                  <td className={clsx('px-4 py-3 text-right font-medium', r.difference === 0 ? 'text-green-400' : 'text-red-400')}>
                    {r.difference === 0 ? '—' : formatCurrency(r.difference)}
                  </td>
                  <td className="px-4 py-3">
                    <span className={clsx('px-2 py-1 rounded text-xs font-medium', r.status === 'matched' ? 'bg-green-900/50 text-green-400' : r.status === 'discrepancy' ? 'bg-red-900/50 text-red-400' : 'bg-amber-900/50 text-amber-400')}>
                      {r.status.charAt(0).toUpperCase() + r.status.slice(1)}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    {r.status !== 'matched' && (
                      <button onClick={() => handleReconcile(r.id)} className="px-3 py-1.5 bg-green-600 text-white rounded text-xs hover:bg-green-700">
                        <CheckCircle className="w-3 h-3 inline mr-1" /> Match
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }
}
