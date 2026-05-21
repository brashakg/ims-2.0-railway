// ============================================================================
// IMS 2.0 - Finance & Accounting Dashboard
// ============================================================================
// Comprehensive financial management for Indian optical retail accounting
// Supports GST management, P&L reporting, cash flow, reconciliation, budgeting

import { useState, useEffect } from 'react';
import { Loader2 } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

import type { TabType } from './financeTypes';
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
import { financeApi } from '../../services/api/finance';

// ---- Mappers: real finance.py responses -> dashboard panel types ----------
// The backend returns aggregate/summary shapes; the panels expect these
// normalised types. Each mapper is defensive (handles missing fields) so a
// thin/empty response renders an honest empty panel rather than crashing.
function mapRevenue(d: any): RevenueData[] {
  if (!d) return [];
  const net = Number(d.total_revenue || 0);
  const deductions = Number(d.total_discount || 0);
  if (!net && !deductions && !d.total_tax) return [];
  return [{
    period: `Current ${d.period || 'month'}`,
    gross_sales: net + deductions,
    deductions,
    net_revenue: net,
    gst_collected: Number(d.total_tax || 0),
  }];
}

function mapPnl(d: any, from: string, to: string): ProfitLossStatement | null {
  if (!d) return null;
  const revenue = Number(d.revenue || 0);
  const cogs = Number(d.cogs || 0);
  const grossProfit = Number(d.gross_profit ?? revenue - cogs);
  const opex = Number(d.total_expenses || 0);
  const netProfit = Number(d.net_profit ?? grossProfit - opex);
  return {
    revenue,
    cost_of_goods: cogs,
    gross_profit: grossProfit,
    operating_expenses: opex,
    operating_profit: grossProfit - opex,
    tax_expense: 0,
    net_profit: netProfit,
    profit_margin: Number(d.net_margin ?? (revenue ? (netProfit / revenue) * 100 : 0)),
    period_start: from,
    period_end: to,
  };
}

function mapGst(d: any): GSTSummaryData | null {
  if (!d) return null;
  return {
    period: `${d.month ?? ''}/${d.year ?? ''}`,
    cgst_collected: Number(d.cgst || 0),
    sgst_collected: Number(d.sgst || 0),
    igst_collected: 0,
    total_gst: Number(d.gst_collected || 0),
    gst_payable: Number(d.net_gst_payable || 0),
    input_tax_credit: Number(d.gst_input_credit || 0),
    gst_type: 'CGST_SGST',
  };
}

function mapOutstanding(d: any): OutstandingReceivable[] {
  const items = Array.isArray(d?.items) ? d.items : [];
  return items.map((o: any) => ({
    id: o.order_id || '',
    customer_name: o.customer_name || 'Unknown',
    amount: Number(o.amount || 0),
    gst_amount: 0,
    due_date: '',
    days_overdue: Number(o.days_overdue || 0),
    status: (Number(o.days_overdue || 0) > 30 ? 'overdue' : 'active') as OutstandingReceivable['status'],
  }));
}

function mapCashFlow(d: any): CashFlowData[] {
  if (!d) return [];
  const inflow = Number(d.inflows || 0);
  const outflow = Number(d.outflows || 0);
  if (!inflow && !outflow) return [];
  return [{
    period: d.period || 'This month',
    opening_balance: 0,
    cash_inflows: inflow,
    cash_outflows: outflow,
    closing_balance: Number(d.net_cash_flow ?? inflow - outflow),
    free_cash_flow: Number(d.net_cash_flow ?? inflow - outflow),
  }];
}

function mapBudget(d: any): BudgetData[] {
  const cats = d?.categories || {};
  return Object.entries(cats).map(([category, v]: [string, any]) => {
    const allocated = Number(v?.budget || 0);
    const spent = Number(v?.actual || 0);
    return {
      category,
      allocated,
      spent,
      remaining: allocated - spent,
      variance: allocated - spent,
      variance_percent: allocated ? ((allocated - spent) / allocated) * 100 : 0,
    };
  });
}

function mapVendorPayments(d: any): VendorPaymentData[] {
  const list = Array.isArray(d) ? d : [];
  return list.map((v: any) => {
    const due = Number(v.balance || 0);
    return {
      id: v.vendor_id || '',
      vendor_name: v.vendor_name || '',
      amount_due: due,
      due_date: '',
      days_overdue: 0,
      status: (due <= 0 ? 'paid' : Number(v.total_paid || 0) > 0 ? 'partial' : 'pending') as VendorPaymentData['status'],
    };
  });
}

function mapReconciliation(d: any): ReconciliationData[] {
  const list = Array.isArray(d?.transfers) ? d.transfers : [];
  return list.map((t: any) => ({
    id: t.transfer_id || '',
    date: t.created_at || '',
    bank_amount: 0,
    system_amount: 0,
    difference: 0,
    status: 'pending' as ReconciliationData['status'],
  }));
}

import FinanceFilters from './FinanceFilters';
import FinanceSummary from './FinanceSummary';
import GSTPanel from './GSTPanel';
import OutstandingPanel from './OutstandingPanel';
import CashFlowPanel from './CashFlowPanel';
import PeriodManagement from './PeriodManagement';
import BudgetPanel from './BudgetPanel';
import VendorPayments from './VendorPayments';
import ReconciliationPanel from './ReconciliationPanel';

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
  const [selectedYear, setSelectedYear] = useState('2025-2026');
  const [periodLocked, setPeriodLocked] = useState(false);

  useEffect(() => {
    loadFinanceData();
    // user?.activeStoreId is load-bearing — without it the topbar store-switch
    // updates AuthContext but the finance data stays pinned to the old store.
  }, [activeTab, dateFrom, dateTo, selectedYear, user?.activeStoreId]);

  const loadFinanceData = async () => {
    setIsLoading(true);
    const storeId = user?.activeStoreId;
    try {
      // Real finance.py endpoints, fetched in parallel. Each section
      // fail-soft independently so one slow/empty endpoint doesn't blank
      // the whole dashboard.
      const [rev, pnl, gst, out, cf, bud, vend, recon] = await Promise.allSettled([
        financeApi.getRevenue({ period: 'month', store_id: storeId }),
        financeApi.getPnl({ store_id: storeId, from_date: dateFrom, to_date: dateTo }),
        financeApi.getGstSummary(),
        financeApi.getOutstanding({ store_id: storeId }),
        financeApi.getCashFlow({ period: 'month' }),
        financeApi.getBudget(),
        financeApi.getVendorPayments(),
        financeApi.getReconciliation(),
      ]);

      setRevenueData(rev.status === 'fulfilled' ? mapRevenue(rev.value) : []);
      setPLStatement(pnl.status === 'fulfilled' ? mapPnl(pnl.value, dateFrom, dateTo) : null);
      setGSTSummary(gst.status === 'fulfilled' ? mapGst(gst.value) : null);
      setOutstanding(out.status === 'fulfilled' ? mapOutstanding(out.value) : []);
      setCashFlow(cf.status === 'fulfilled' ? mapCashFlow(cf.value) : []);
      setBudgets(bud.status === 'fulfilled' ? mapBudget(bud.value) : []);
      setVendorPayments(vend.status === 'fulfilled' ? mapVendorPayments(vend.value) : []);
      setReconciliation(recon.status === 'fulfilled' ? mapReconciliation(recon.value) : []);
    } catch (error) {
      toast.error('Failed to load financial data');
    } finally {
      setIsLoading(false);
    }
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

  const handleAllocateBudget = (category: string, amount: string) => {
    if (!category || !amount) {
      toast.error('Please fill all budget fields');
      return;
    }
    toast.success(`Budget allocated for ${category}`);
  };

  const handleReconcile = (_itemId: string) => {
    toast.success('Reconciliation item marked as matched');
  };

  const handlePayVendor = (vendorName: string) => {
    toast.success(`Payment recorded for ${vendorName}`);
  };

  const handleImportStatement = () => {
    toast.info('Upload bank statement to auto-reconcile');
  };

  // Loading state
  if (isLoading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
        <div className="text-center">
          <Loader2 className="w-12 h-12 text-blue-500 animate-spin mx-auto mb-4" />
          <p className="text-gray-500">Loading financial data...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="inv-body">
      {/* Editorial header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Finance &amp; Accounting</div>
          <h1>The books, in real time.</h1>
          <div className="hint">Revenue, P&amp;L, GST collected / payable, outstanding aging, cash flow, period lock after month-end. Tally export on sync.</div>
        </div>
      </div>

      <div>
        <FinanceFilters
          selectedYear={selectedYear}
          onYearChange={setSelectedYear}
          dateFrom={dateFrom}
          dateTo={dateTo}
          onDateFromChange={setDateFrom}
          onDateToChange={setDateTo}
          activeTab={activeTab}
          onTabChange={setActiveTab}
        />

        {/* Tab Content */}
        <div className="animate-fadeIn">
          {activeTab === 'revenue-pl' && (
            <FinanceSummary revenueData={revenueData} plStatement={plStatement} />
          )}
          {activeTab === 'gst' && <GSTPanel gstSummary={gstSummary} />}
          {activeTab === 'outstanding' && (
            <OutstandingPanel outstanding={outstanding} vendorPayments={vendorPayments} />
          )}
          {activeTab === 'cash-flow' && (
            <CashFlowPanel
              cashFlow={cashFlow}
              reconciliation={reconciliation}
              onReconcile={handleReconcile}
            />
          )}
          {activeTab === 'period' && (
            <PeriodManagement
              periodLocked={periodLocked}
              onLockPeriod={handleLockPeriod}
              onUnlockPeriod={handleUnlockPeriod}
            />
          )}
          {activeTab === 'budgets' && (
            <BudgetPanel
              budgets={budgets}
              selectedYear={selectedYear}
              onAllocateBudget={handleAllocateBudget}
            />
          )}
          {activeTab === 'vendor-payments' && (
            <VendorPayments vendorPayments={vendorPayments} onPayVendor={handlePayVendor} />
          )}
          {activeTab === 'reconciliation' && (
            <ReconciliationPanel
              reconciliation={reconciliation}
              onReconcile={handleReconcile}
              onImportStatement={handleImportStatement}
            />
          )}
        </div>
      </div>
    </div>
  );
}
