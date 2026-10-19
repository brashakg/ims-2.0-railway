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
import { COST_ROLES } from '../../components/common/CostCell';

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
import { NoShopNotice, hasNoShop } from '../../components/common/NoShopNotice';
import { PurchaseShopLabel, PurchaseShopPicker, usePurchaseShop } from '../purchase/purchaseShop';
import { mapGst } from './financeUtils';

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
  const cogs = d.cogs == null ? null : Number(d.cogs);
  const grossProfit = d.gross_profit == null ? null : Number(d.gross_profit);
  const opex = Number(d.total_expenses || 0);
  // net_profit is ABSENT for any role below ADMIN -- it is revenue - COGS -
  // expenses - PAYROLL, so the backend strips it as a payroll-derived figure.
  // This used to fall back to `grossProfit - opex`, which quietly reported a
  // profit with the whole wage bill missing and labelled it "Net Profit". Show
  // nothing rather than a number that is wrong by a month's salaries.
  const netProfit = d.net_profit === undefined || d.net_profit === null
    ? null
    : Number(d.net_profit);
  return {
    revenue,
    cost_of_goods: cogs,
    gross_profit: grossProfit,
    operating_expenses: opex,
    operating_profit: grossProfit == null ? null : grossProfit - opex,
    tax_expense: 0,
    net_profit: netProfit,
    profit_margin:
      d.net_margin !== undefined && d.net_margin !== null
        ? Number(d.net_margin)
        : netProfit !== null && revenue
          ? (netProfit / revenue) * 100
          : null,
    period_start: from,
    period_end: to,
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

import FinanceFilters, { currentFyLabelIST } from './FinanceFilters';
import FinanceSummary from './FinanceSummary';
import GSTPanel from './GSTPanel';
import OutstandingPanel from './OutstandingPanel';
import CashFlowPanel from './CashFlowPanel';
import PeriodManagement from './PeriodManagement';
import BudgetPanel from './BudgetPanel';
import RestrictedTotalsNotice from './RestrictedTotalsNotice';
import VendorPayments from './VendorPayments';
import JournalEntriesPanel from './JournalEntriesPanel';

export default function FinanceDashboard() {
  const { user } = useAuth();
  const toast = useToast();
  // The one shop scope of the Purchase and Finance screens (audit F63, owner
  // ruling 2026-09-28 'admins see all stores'): an admin reads EVERY shop
  // until he narrows it with the Shop picker in the header -- never his
  // topbar (often HQ) shop -- and everyone else reads their own shop.
  const { storeId } = usePurchaseShop();

  // OWNER DECISION 2026-08-13: the store-by-store profit table is ADMIN /
  // SUPERADMIN only, because every row carries that store's monthly wage bill
  // and a 1-5 person store's payroll total IS an individual's pay. Mirrors the
  // backend gate (finance.get_pnl_by_store + the rbac_policy row) so a store
  // manager gets a plain-English explanation instead of a 403 toast or an
  // empty table that looks broken.
  const canSeeStorePayroll = (user?.roles || []).some(
    (r) => r === 'ADMIN' || r === 'SUPERADMIN',
  );
  // What we pay is the accounts roles' only (COST_ROLES, owner rulings F60
  // 2026-09-28 / 2026-10-12): per-vendor payables (GET /finance/vendor-payments,
  // as the vendor ledger and /ap-aging) and the cost of goods and gross profit
  // by category. A manager gets no payables tab and no schedule rather than an
  // empty list that reads as "we owe nobody", and revenue by category only.
  const canSeeWhatWePay = (user?.roles || []).some((r) => COST_ROLES.includes(r));

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
  const [gstSummaryError, setGSTSummaryError] = useState<string | null>(null);
  const [outstanding, setOutstanding] = useState<OutstandingReceivable[]>([]);
  const [cashFlow, setCashFlow] = useState<CashFlowData[]>([]);
  const [budgets, setBudgets] = useState<BudgetData[]>([]);
  const [vendorPayments, setVendorPayments] = useState<VendorPaymentData[]>([]);
  const [pnlByStore, setPnlByStore] = useState<Array<{ store_id?: string; store_name?: string; entity_id?: string; revenue?: number; cogs?: number; expenses?: number; payroll?: number; net_profit?: number; net_margin?: number }>>([]);
  // Sortable per-store P&L. Default highest-revenue-first (mirrors the backend's
  // own sort) but every column header is clickable to re-sort.
  const [pnlStoreSort, setPnlStoreSort] = useState<{ key: 'store_id' | 'revenue' | 'cogs' | 'gross_profit' | 'margin' | 'expenses' | 'payroll' | 'net_profit'; dir: 'asc' | 'desc' }>({ key: 'revenue', dir: 'desc' });
  const [pnlByCategory, setPnlByCategory] = useState<Array<{ category?: string; revenue?: number; cogs?: number; gross_profit?: number }>>([]);
  // One row per GST registration, read off its GSTR-3B (audit A04-3). A
  // null net_payable: the GSTIN also files for shops outside this login's
  // view, so no cash figure rather than a wrong one.
  const [gstRecon, setGstRecon] = useState<Array<{ entity_name?: string; gstin?: string; gst_collected?: number; input_credit?: number | null; reverse_charge?: number | null; net_payable?: number | null; shops_outside_view?: number }>>([]);
  // The table's own refusal (503 unreadable, 409 a registration to correct),
  // shown in its place -- never an empty table that reads as "no GST".
  const [gstReconError, setGstReconError] = useState<string | null>(null);
  // The backend tells us when it has withheld an expense head from this reader.
  // THREE separate flags on three separate responses -- /pnl, /budget and
  // /cash-flow -- and they are kept as three pieces of state on purpose: two of
  // them share the key name `expenses_partially_restricted`, so a single shared
  // flag would look correct in any test that varies only one at a time and would
  // put a notice on a tab whose total is whole. Carrying them into state is what
  // stops a shortened total rendering as the truth -- see RestrictedTotalsNotice.
  const [expensesRestricted, setExpensesRestricted] = useState(false);
  const [budgetRestricted, setBudgetRestricted] = useState(false);
  // THE THIRD ONE, and the easiest to miss. /finance/cash-flow strips the same
  // pay heads below ADMIN and sets the SAME flag name as /pnl (finance.py:1500)
  // -- but mapCashFlow() below keeps only the four numbers and drops the rest
  // of the body, so the flag was arriving and being thrown away. "Total
  // outflows" then rendered short, with nothing on screen saying so.
  const [cashFlowRestricted, setCashFlowRestricted] = useState(false);

  // UI states
  const [isLoading, setIsLoading] = useState(true);
  // OS-054: default to the CURRENT Indian FY (computed from the IST date)
  // instead of a hardcoded year that goes stale every April.
  const [selectedYear, setSelectedYear] = useState(currentFyLabelIST());
  const [periodLocked, setPeriodLocked] = useState(false);

  useEffect(() => {
    // R4: a login with no shop is shown NoShopNotice below and reads nothing
    // -- budgets and the period lock are not shop-scoped, so even they would
    // hand it organisation data.
    if (hasNoShop(user)) return;
    loadFinanceData();
    // storeId is load-bearing -- without it a shop pick (an admin's picker,
    // anyone else's topbar store) leaves the figures on the old shop.
  }, [activeTab, dateFrom, dateTo, selectedYear, storeId]);

  const loadFinanceData = async () => {
    setIsLoading(true);
    try {
      // Real finance.py endpoints, fetched in parallel. Each section
      // fail-soft independently so one slow/empty endpoint doesn't blank
      // the whole dashboard.
      const [rev, pnl, gst, out, cf, bud, vend] = await Promise.allSettled([
        financeApi.getRevenue({ period: 'month', store_id: storeId }),
        financeApi.getPnl({ store_id: storeId, from_date: dateFrom, to_date: dateTo }),
        // Input tax from supplier bills: accounts only (R1), as the server.
        canSeeWhatWePay ? financeApi.getGstSummary() : Promise.resolve(null),
        financeApi.getOutstanding({ store_id: storeId }),
        financeApi.getCashFlow({ period: 'month', store_id: storeId }),
        financeApi.getBudget(),
        canSeeWhatWePay ? financeApi.getVendorPayments(storeId) : Promise.resolve([]),
      ]);

      setRevenueData(rev.status === 'fulfilled' ? mapRevenue(rev.value) : []);
      setPLStatement(pnl.status === 'fulfilled' ? mapPnl(pnl.value, dateFrom, dateTo) : null);
      setGSTSummary(gst.status === 'fulfilled' ? mapGst(gst.value) : null);
      setGSTSummaryError(gst.status === 'rejected'
        ? (gst.reason instanceof Error ? gst.reason.message : 'GST summary could not be calculated. Please retry.')
        : null);
      setOutstanding(out.status === 'fulfilled' ? mapOutstanding(out.value) : []);
      setCashFlow(cf.status === 'fulfilled' ? mapCashFlow(cf.value) : []);
      setBudgets(bud.status === 'fulfilled' ? mapBudget(bud.value) : []);
      setVendorPayments(vend.status === 'fulfilled' ? mapVendorPayments(vend.value) : []);
      // A failed/absent call must NOT leave a stale "incomplete" banner up, and
      // must not invent one either — default false on anything but an explicit
      // true from the backend.
      setExpensesRestricted(
        pnl.status === 'fulfilled' && !!(pnl.value as any)?.expenses_partially_restricted,
      );
      setBudgetRestricted(
        bud.status === 'fulfilled' && !!(bud.value as any)?.categories_partially_restricted,
      );
      // Either flag shortens "Total outflows": a pay head withheld below
      // ADMIN, or what was paid to suppliers (ADMIN + ACCOUNTANT only, owner
      // ruling 2026-10-12 -- the backend leaves it out and says so).
      setCashFlowRestricted(
        cf.status === 'fulfilled' &&
          (!!(cf.value as any)?.expenses_partially_restricted ||
            !!(cf.value as any)?.vendor_payments_restricted),
      );

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
          // Not requested at all for a non-admin: the endpoint would 403, and
          // firing a call we know will be refused just to discard the answer
          // makes the network log look like a bug.
          canSeeStorePayroll
            ? financeApi.getPnlByStore({ from_date: dateFrom, to_date: dateTo })
            : Promise.resolve({ stores: [] }),
          financeApi.getPnlByCategory({ from_date: dateFrom, to_date: dateTo, store_id: storeId }),
          canSeeWhatWePay
            ? financeApi.getGstReconciliation({ month: d.getMonth() + 1, year: d.getFullYear() })
            : Promise.resolve({ registrations: [] }),
        ]);
        setPnlByStore(ps2.status === 'fulfilled' ? (ps2.value?.stores || []) : []);
        setPnlByCategory(pc2.status === 'fulfilled' ? (pc2.value?.categories || []) : []);
        setGstRecon(gr2.status === 'fulfilled' ? (gr2.value?.registrations || []) : []);
        setGstReconError(gr2.status === 'rejected'
          ? (gr2.reason instanceof Error ? gr2.reason.message : 'The GST table could not be worked out. Please retry.')
          : null);
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
        store_id: storeId,
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

  // R4 (owner ruling 2026-10-12): a non-admin login with no shop sees no
  // shop. Say so, and why, instead of a dashboard of empty panels.
  if (hasNoShop(user)) {
    return (
      <div className="inv-body">
        <div className="inv-head">
          <div>
            <div className="eyebrow mb-1.5">Finance &amp; Accounting</div>
            <h1>The books, in real time.</h1>
          </div>
        </div>
        <NoShopNotice />
      </div>
    );
  }

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
          <div className="eyebrow mb-1.5">Finance &amp; Accounting</div>
          <h1>The books, in real time.</h1>
          <div className="hint">Revenue, P&amp;L, GST collected / payable, outstanding aging, cash flow, period lock after month-end. Tally export on sync.</div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <PurchaseShopPicker />
          <PurchaseShopLabel />
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
          canSeePayables={canSeeWhatWePay}
        />

        {/* Tab Content */}
        <div className="animate-fadeIn">
          {activeTab === 'revenue-pl' && (
            <>
              {/* The operating-expense figure below is short by whatever the
                  backend withheld from this role. Say so BEFORE the number,
                  not after it. */}
              <RestrictedTotalsNotice
                show={expensesRestricted}
                scope="profit and expense figures"
                className="mb-4"
              />
              <FinanceSummary revenueData={revenueData} plStatement={plStatement} />
              {!canSeeStorePayroll && (
                <div className="card mt-4 p-4">
                  <div className="text-sm font-medium text-gray-700">P&amp;L by store</div>
                  <p className="text-sm text-gray-500 mt-1">
                    Not shown for your role. This table includes each store's monthly
                    salary bill, and with only a few people per store that total would
                    reveal what individual colleagues are paid. Salary figures are
                    restricted to administrators.
                  </p>
                  {/* Points at a screen that ACTUALLY EXISTS and that this role
                      can actually open: ReportsPage's Forecast tab renders
                      /reports/finance/expense-vs-revenue, which is scoped to the
                      caller's own store and carries revenue, cost and profit
                      with no payroll term. (/reports/profit/by-store is also
                      clean and open, but nothing in the UI calls it, so naming
                      it here would send people somewhere that isn't there.) */}
                  <p className="text-sm text-gray-500 mt-2">
                    Your own store's revenue, cost and profit — with no salary line —
                    are on Reports &rsaquo; Forecast, under "Expense vs Revenue".
                  </p>
                </div>
              )}
              {canSeeStorePayroll && pnlByStore.length > 0 && (
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
                          <td className="px-3 py-2">{s.store_name || s.store_id}</td>
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
                  <div className="px-4 py-2 text-sm font-medium text-gray-700 border-b border-gray-100">{canSeeWhatWePay ? <>P&amp;L by category</> : 'Revenue by category'}</div>
                  <table className="min-w-full text-sm">
                    <thead className="bg-gray-50 text-gray-600"><tr>
                      <th className="px-3 py-2 text-left">Category</th>
                      <th className="px-3 py-2 text-right">Revenue</th>
                      {canSeeWhatWePay && <th className="px-3 py-2 text-right">COGS</th>}
                      {canSeeWhatWePay && <th className="px-3 py-2 text-right">Gross profit</th>}
                    </tr></thead>
                    <tbody>
                      {pnlByCategory.map((c) => (
                        <tr key={c.category} className="border-t border-gray-100">
                          <td className="px-3 py-2">{c.category}</td>
                          <td className="px-3 py-2 text-right">₹{Math.round(c.revenue || 0).toLocaleString('en-IN')}</td>
                          {canSeeWhatWePay && <td className="px-3 py-2 text-right">₹{Math.round(c.cogs || 0).toLocaleString('en-IN')}</td>}
                          {canSeeWhatWePay && <td className="px-3 py-2 text-right font-semibold">₹{Math.round(c.gross_profit || 0).toLocaleString('en-IN')}</td>}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
          {activeTab === 'gst' && canSeeWhatWePay && (
            <>
              {gstSummaryError && (
                <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
                  {gstSummaryError}
                </div>
              )}
              <GSTPanel gstSummary={gstSummary} />
              {gstReconError && (
                <div role="alert" className="card mt-4 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800" data-testid="gst-recon-error">
                  GST by registration: {gstReconError}
                </div>
              )}
              {!gstReconError && gstRecon.length > 0 && (
                <div className="card mt-4 overflow-x-auto">
                  <div className="px-4 py-2 text-sm font-medium text-gray-700 border-b border-gray-100">GST by registration, as each GSTR-3B (file via Tally)</div>
                  <table className="min-w-full text-sm">
                    <thead className="bg-gray-50 text-gray-600"><tr>
                      <th className="px-3 py-2 text-left">Entity</th>
                      <th className="px-3 py-2 text-left">GSTIN</th>
                      <th className="px-3 py-2 text-right">Output tax</th>
                      <th className="px-3 py-2 text-right">Input credit</th>
                      <th className="px-3 py-2 text-right">Reverse charge</th>
                      <th className="px-3 py-2 text-right">Net payable (cash)</th>
                    </tr></thead>
                    <tbody>
                      {gstRecon.map((e, i) => (
                        <tr key={i} className="border-t border-gray-100">
                          <td className="px-3 py-2">{e.entity_name}</td>
                          <td className="px-3 py-2 font-mono text-xs">{e.gstin || 'No GSTIN'}</td>
                          <td className="px-3 py-2 text-right">₹{Math.round(e.gst_collected || 0).toLocaleString('en-IN')}</td>
                          {e.net_payable == null ? (
                            <td colSpan={3} className="px-3 py-2 text-right text-xs text-gray-600" data-testid="gst-recon-withheld">
                              — Input credit, reverse charge and net payable are this GSTIN&rsquo;s whole return, which also covers {e.shops_outside_view || 0} other shop(s) not in this view. Ask an admin.
                            </td>
                          ) : (<>
                            <td className="px-3 py-2 text-right">₹{Math.round(e.input_credit || 0).toLocaleString('en-IN')}</td>
                            <td className="px-3 py-2 text-right">₹{Math.round(e.reverse_charge || 0).toLocaleString('en-IN')}</td>
                            <td className="px-3 py-2 text-right font-semibold">₹{Math.round(e.net_payable).toLocaleString('en-IN')}</td>
                          </>)}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="px-4 py-2 text-xs text-gray-500 border-t border-gray-100">
                    Output tax after credit notes; input credit from booked supplier bills (credit not claimable left out, supplier credit notes taken off in their note&rsquo;s month); net payable is the cash due after set-off, plus reverse charge.
                  </p>
                </div>
              )}
            </>
          )}
          {activeTab === 'outstanding' && (
            <OutstandingPanel
              outstanding={outstanding}
              vendorPayments={canSeeWhatWePay ? vendorPayments : null}
            />
          )}
          {activeTab === 'cash-flow' && (
            <>
              {/* "Total outflows" below is short by whatever was withheld from
                  this role. Say so above the number, not after it. */}
              <RestrictedTotalsNotice
                show={cashFlowRestricted}
                scope="cash outflow figures"
                className="mb-4"
              />
              <CashFlowPanel cashFlow={cashFlow} />
            </>
          )}
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
            <>
              {/* Rows AND the totals underneath are short by the same amount. */}
              <RestrictedTotalsNotice
                show={budgetRestricted}
                scope="budget rows and totals"
                className="mb-4"
              />
              <BudgetPanel budgets={budgets} selectedYear={selectedYear} />
            </>
          )}
          {activeTab === 'vendor-payments' && canSeeWhatWePay && (
            <VendorPayments vendorPayments={vendorPayments} />
          )}
          {activeTab === 'journal-entries' && canSeeWhatWePay && <JournalEntriesPanel />}
        </div>
      </div>
    </div>
  );
}
