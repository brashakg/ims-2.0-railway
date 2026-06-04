// ============================================================================
// IMS 2.0 - Finance & Accounting Dashboard
// ============================================================================
// Comprehensive financial management for Indian optical retail accounting
// Supports GST management, P&L reporting, cash flow, reconciliation, budgeting

import { useState, useEffect, useMemo } from 'react';
import { Loader2, ArrowUpDown } from 'lucide-react';
import clsx from 'clsx';
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
    due_date: o.due_date || '',
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

// NOTE: the old mapReconciliation() fabricated bank_amount/system_amount/
// difference as 0 from inter-store transfer rows — there is no bank-statement
// reconciliation backend, so it has been removed (SYSTEM_INTENT: never show
// fabricated money). The Reconciliation tab + the Cash Flow "Bank Reconciliation
// Status" block that consumed it are gone too.

import FinanceFilters from './FinanceFilters';
import FinanceSummary from './FinanceSummary';
import GSTPanel from './GSTPanel';
import OutstandingPanel from './OutstandingPanel';
import CashFlowPanel from './CashFlowPanel';
import PeriodManagement from './PeriodManagement';
import BudgetPanel from './BudgetPanel';
import VendorPayments from './VendorPayments';

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
  const [pnlByStore, setPnlByStore] = useState<Array<{ store_id?: string; entity_id?: string; revenue?: number; cogs?: number; expenses?: number; payroll?: number; net_profit?: number; net_margin?: number }>>([]);
  // Sortable per-store P&L. Default highest-revenue-first (mirrors the backend's
  // own sort) but every column header is clickable to re-sort.
  const [pnlStoreSort, setPnlStoreSort] = useState<{ key: 'store_id' | 'revenue' | 'cogs' | 'gross_profit' | 'margin' | 'expenses' | 'payroll' | 'net_profit'; dir: 'asc' | 'desc' }>({ key: 'revenue', dir: 'desc' });
  const [pnlByCategory, setPnlByCategory] = useState<Array<{ category?: string; revenue?: number; cogs?: number; gross_profit?: number }>>([]);
  const [gstRecon, setGstRecon] = useState<Array<{ entity_name?: string; gst_collected?: number; input_credit?: number; net_payable?: number }>>([]);

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
      const [rev, pnl, gst, out, cf, bud, vend] = await Promise.allSettled([
        financeApi.getRevenue({ period: 'month', store_id: storeId }),
        financeApi.getPnl({ store_id: storeId, from_date: dateFrom, to_date: dateTo }),
        financeApi.getGstSummary(),
        financeApi.getOutstanding({ store_id: storeId }),
        financeApi.getCashFlow({ period: 'month', store_id: storeId }),
        financeApi.getBudget(),
        financeApi.getVendorPayments(),
      ]);

      setRevenueData(rev.status === 'fulfilled' ? mapRevenue(rev.value) : []);
      setPLStatement(pnl.status === 'fulfilled' ? mapPnl(pnl.value, dateFrom, dateTo) : null);
      setGSTSummary(gst.status === 'fulfilled' ? mapGst(gst.value) : null);
      setOutstanding(out.status === 'fulfilled' ? mapOutstanding(out.value) : []);
      setCashFlow(cf.status === 'fulfilled' ? mapCashFlow(cf.value) : []);
      setBudgets(bud.status === 'fulfilled' ? mapBudget(bud.value) : []);
      setVendorPayments(vend.status === 'fulfilled' ? mapVendorPayments(vend.value) : []);

      // Reflect the real period-lock state for the selected month.
      try {
        const d = new Date(dateTo);
        const ps = await financeApi.getPeriodStatus(d.getMonth() + 1, d.getFullYear());
        setPeriodLocked(!!ps?.locked);
      } catch {
        /* non-fatal */
      }

      // P&L breakdowns + GST reconciliation (Phase 2/3 endpoints, fail-soft).
      try {
        const d = new Date(dateTo);
        const [ps2, pc2, gr2] = await Promise.allSettled([
          financeApi.getPnlByStore({ from_date: dateFrom, to_date: dateTo }),
          financeApi.getPnlByCategory({ from_date: dateFrom, to_date: dateTo, store_id: storeId }),
          financeApi.getGstReconciliation({ month: d.getMonth() + 1, year: d.getFullYear() }),
        ]);
        setPnlByStore(ps2.status === 'fulfilled' ? (ps2.value?.stores || []) : []);
        setPnlByCategory(pc2.status === 'fulfilled' ? (pc2.value?.categories || []) : []);
        setGstRecon(gr2.status === 'fulfilled' ? (gr2.value?.entities || []) : []);
      } catch {
        /* non-fatal */
      }
    } catch (error) {
      toast.error('Failed to load financial data');
    } finally {
      setIsLoading(false);
    }
  };

  // Per-store P&L rows enriched with gross profit (revenue - COGS) + margin %,
  // then sorted by the active column. Gross profit / margin aren't returned by
  // the endpoint so they're derived here.
  const pnlStoreRows = useMemo(() => {
    const enriched = pnlByStore.map((s) => {
      const revenue = s.revenue || 0;
      const cogs = s.cogs || 0;
      const grossProfit = revenue - cogs;
      return {
        ...s,
        revenue,
        cogs,
        gross_profit: grossProfit,
        // Gross margin %. Backend ships net_margin (after expenses + payroll);
        // this is the gross-level figure the column header asks for.
        margin: revenue > 0 ? (grossProfit / revenue) * 100 : 0,
      };
    });
    const { key, dir } = pnlStoreSort;
    const factor = dir === 'asc' ? 1 : -1;
    const numeric = (row: (typeof enriched)[number]): number => {
      switch (key) {
        case 'revenue': return row.revenue;
        case 'cogs': return row.cogs;
        case 'gross_profit': return row.gross_profit;
        case 'margin': return row.margin;
        case 'expenses': return row.expenses || 0;
        case 'payroll': return row.payroll || 0;
        case 'net_profit': return row.net_profit || 0;
        default: return 0;
      }
    };
    return enriched.sort((a, b) => {
      if (key === 'store_id') {
        return (a.store_id || '').localeCompare(b.store_id || '') * factor;
      }
      return (numeric(a) - numeric(b)) * factor;
    });
  }, [pnlByStore, pnlStoreSort]);

  const togglePnlStoreSort = (key: typeof pnlStoreSort.key) => {
    setPnlStoreSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: key === 'store_id' ? 'asc' : 'desc' },
    );
  };

  const handleLockPeriod = async () => {
    const role = user?.activeRole;
    if (role !== 'ACCOUNTANT' && role !== 'ADMIN' && role !== 'SUPERADMIN') {
      toast.error('Only accountants and admins can lock periods');
      return;
    }
    const d = new Date(dateTo);
    try {
      await financeApi.lockPeriod(d.getMonth() + 1, d.getFullYear());
      setPeriodLocked(true);
      toast.success(`Period ${d.getMonth() + 1}/${d.getFullYear()} locked`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to lock period');
    }
  };

  const handleUnlockPeriod = () => {
    // Locked periods are intentionally permanent (closed-book integrity); there
    // is no unlock endpoint. Surface that instead of faking a local toggle.
    toast.info('Locked periods are permanent for audit integrity — no unlock.');
  };

  const handleTallyExport = async () => {
    try {
      const blob = await financeApi.downloadTallySalesJv({
        from_date: dateFrom,
        to_date: dateTo,
        store_id: user?.activeStoreId,
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `sales_jv_${dateFrom}_${dateTo}.xml`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success('Tally sales voucher exported');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Tally export failed');
    }
  };

  // NOTE: Budget-allocate, vendor-Pay, Mark-Matched and Import-Statement were
  // toast-only no-ops (no write path on /finance/*). They've been removed rather
  // than fake success. Budget planning lives in the dedicated Budgets module
  // (/budgets) and vendor payments in the Cash Flow vendor ledger.

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
        <div>
          <button className="btn-secondary" onClick={handleTallyExport}>Export to Tally</button>
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
            <>
              <FinanceSummary revenueData={revenueData} plStatement={plStatement} />
              {pnlByStore.length > 0 && (
                <div className="card mt-4 overflow-x-auto">
                  <div className="px-4 py-2 text-sm font-medium text-gray-700 border-b border-gray-100">P&amp;L by store</div>
                  <table className="min-w-full text-sm">
                    <thead className="bg-gray-50 text-gray-600"><tr>
                      {([
                        { key: 'store_id', label: 'Store', align: 'left' },
                        { key: 'revenue', label: 'Revenue', align: 'right' },
                        { key: 'cogs', label: 'COGS', align: 'right' },
                        { key: 'gross_profit', label: 'Gross profit', align: 'right' },
                        { key: 'margin', label: 'Margin %', align: 'right' },
                        { key: 'expenses', label: 'Expenses', align: 'right' },
                        { key: 'payroll', label: 'Payroll', align: 'right' },
                        { key: 'net_profit', label: 'Net', align: 'right' },
                      ] as Array<{ key: typeof pnlStoreSort.key; label: string; align: 'left' | 'right' }>).map((col) => (
                        <th
                          key={col.key}
                          onClick={() => togglePnlStoreSort(col.key)}
                          className={clsx(
                            'px-3 py-2 cursor-pointer select-none hover:text-gray-900',
                            col.align === 'right' ? 'text-right' : 'text-left',
                          )}
                          title="Click to sort"
                        >
                          <span className={clsx('inline-flex items-center gap-1', col.align === 'right' && 'flex-row-reverse')}>
                            {col.label}
                            <ArrowUpDown
                              className={clsx(
                                'w-3 h-3',
                                pnlStoreSort.key === col.key ? 'text-bv-red-600' : 'text-gray-300',
                              )}
                            />
                          </span>
                        </th>
                      ))}
                    </tr></thead>
                    <tbody>
                      {pnlStoreRows.map((s) => (
                        <tr key={s.store_id} className="border-t border-gray-100">
                          <td className="px-3 py-2">{s.store_id}</td>
                          <td className="px-3 py-2 text-right">₹{Math.round(s.revenue).toLocaleString('en-IN')}</td>
                          <td className="px-3 py-2 text-right">₹{Math.round(s.cogs).toLocaleString('en-IN')}</td>
                          <td className={clsx('px-3 py-2 text-right font-medium', s.gross_profit < 0 ? 'text-red-600' : 'text-gray-900')}>₹{Math.round(s.gross_profit).toLocaleString('en-IN')}</td>
                          <td className={clsx('px-3 py-2 text-right', s.margin < 0 ? 'text-red-600' : 'text-gray-600')}>{s.margin.toFixed(1)}%</td>
                          <td className="px-3 py-2 text-right">₹{Math.round(s.expenses || 0).toLocaleString('en-IN')}</td>
                          <td className="px-3 py-2 text-right">₹{Math.round(s.payroll || 0).toLocaleString('en-IN')}</td>
                          <td className={clsx('px-3 py-2 text-right font-semibold', (s.net_profit || 0) < 0 ? 'text-red-600' : 'text-gray-900')}>₹{Math.round(s.net_profit || 0).toLocaleString('en-IN')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {pnlByCategory.length > 0 && (
                <div className="card mt-4 overflow-x-auto">
                  <div className="px-4 py-2 text-sm font-medium text-gray-700 border-b border-gray-100">P&amp;L by category</div>
                  <table className="min-w-full text-sm">
                    <thead className="bg-gray-50 text-gray-600"><tr>
                      <th className="px-3 py-2 text-left">Category</th>
                      <th className="px-3 py-2 text-right">Revenue</th>
                      <th className="px-3 py-2 text-right">COGS</th>
                      <th className="px-3 py-2 text-right">Gross profit</th>
                    </tr></thead>
                    <tbody>
                      {pnlByCategory.map((c) => (
                        <tr key={c.category} className="border-t border-gray-100">
                          <td className="px-3 py-2">{c.category}</td>
                          <td className="px-3 py-2 text-right">₹{Math.round(c.revenue || 0).toLocaleString('en-IN')}</td>
                          <td className="px-3 py-2 text-right">₹{Math.round(c.cogs || 0).toLocaleString('en-IN')}</td>
                          <td className="px-3 py-2 text-right font-semibold">₹{Math.round(c.gross_profit || 0).toLocaleString('en-IN')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
          {activeTab === 'gst' && (
            <>
              <GSTPanel gstSummary={gstSummary} />
              {gstRecon.length > 0 && (
                <div className="card mt-4 overflow-x-auto">
                  <div className="px-4 py-2 text-sm font-medium text-gray-700 border-b border-gray-100">GST reconciliation by entity (file via Tally)</div>
                  <table className="min-w-full text-sm">
                    <thead className="bg-gray-50 text-gray-600"><tr>
                      <th className="px-3 py-2 text-left">Entity</th>
                      <th className="px-3 py-2 text-right">GST collected</th>
                      <th className="px-3 py-2 text-right">Input credit</th>
                      <th className="px-3 py-2 text-right">Net payable</th>
                    </tr></thead>
                    <tbody>
                      {gstRecon.map((e, i) => (
                        <tr key={i} className="border-t border-gray-100">
                          <td className="px-3 py-2">{e.entity_name}</td>
                          <td className="px-3 py-2 text-right">₹{Math.round(e.gst_collected || 0).toLocaleString('en-IN')}</td>
                          <td className="px-3 py-2 text-right">₹{Math.round(e.input_credit || 0).toLocaleString('en-IN')}</td>
                          <td className="px-3 py-2 text-right font-semibold">₹{Math.round(e.net_payable || 0).toLocaleString('en-IN')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
          {activeTab === 'outstanding' && (
            <OutstandingPanel outstanding={outstanding} vendorPayments={vendorPayments} />
          )}
          {activeTab === 'cash-flow' && <CashFlowPanel cashFlow={cashFlow} />}
          {activeTab === 'period' && (
            <PeriodManagement
              periodLocked={periodLocked}
              onLockPeriod={handleLockPeriod}
              onUnlockPeriod={handleUnlockPeriod}
              dateFrom={dateFrom}
              dateTo={dateTo}
            />
          )}
          {activeTab === 'budgets' && (
            <BudgetPanel budgets={budgets} selectedYear={selectedYear} />
          )}
          {activeTab === 'vendor-payments' && (
            <VendorPayments vendorPayments={vendorPayments} />
          )}
        </div>
      </div>
    </div>
  );
}
