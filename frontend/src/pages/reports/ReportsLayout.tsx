// ============================================================================
// IMS 2.0 - Reports module layout
// ============================================================================
// Wave 2 split: the old ReportsPage held five tabs in useState, so nothing
// was linkable and every tab paid for all sixteen requests. Each tab is now
// a real page with its own URL (/reports/sales, /reports/gst, ...).
//
// This layout keeps what was ALWAYS on screen - the editorial header, the
// period picker, the refresh button, the error banner, the KPI strip and the
// section nav - and each section page owns its own widgets and data.
//
// The period lives in layout state, not the URL: the layout is the parent
// route, so it survives section navigation and the picker keeps working
// exactly as it did. Sections read it (and the derived from/to dates) off
// the outlet context.

import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Outlet, useLocation, useNavigate, useOutletContext } from 'react-router-dom';
import {
  BarChart3,
  Package,
  Users,
  FileText,
  TrendingUp,
  Loader2,
  RefreshCw,
  AlertTriangle,
  Sparkles,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import {
  resolveDateRange,
  useSalesGrowth,
  useSalesSummary,
  type DateRange,
} from './reportsQueries';

export interface ReportsContext {
  storeId: string | undefined;
  dateRange: DateRange;
  startDate: string;
  endDate: string;
  canExport: boolean;
}

/** Section pages read the period + permissions the layout resolved. */
export function useReportsContext() {
  return useOutletContext<ReportsContext>();
}

const SECTIONS = [
  { path: '/reports/sales', label: 'Sales', icon: BarChart3 },
  { path: '/reports/inventory', label: 'Inventory', icon: Package },
  { path: '/reports/customers', label: 'Customers', icon: Users },
  { path: '/reports/gst', label: 'GST', icon: FileText },
  { path: '/reports/forecast', label: 'Forecast', icon: TrendingUp },
];

export function ReportsLayout() {
  const { user, hasRole } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [dateRange, setDateRange] = useState<DateRange>('month');

  const storeId = user?.activeStoreId;
  const { startDate, endDate } = resolveDateRange(dateRange);
  // Role-based permissions
  const canExport = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']);

  // The KPI strip is on every section, so the layout owns its two queries.
  // Section pages that also draw on them (Sales) hit the same cache entry.
  const summaryQ = useSalesSummary({ storeId, startDate, endDate });
  const growthQ = useSalesGrowth(storeId);

  const salesSummary = summaryQ.data?.summary;
  const salesGrowth = growthQ.data;
  const isLoading = summaryQ.isPending;
  // Only the summary call surfaced an error banner on the old page; the other
  // fifteen were Promise.allSettled and failed silently. Same here.
  const error = summaryQ.isError ? 'Failed to load report data. Please try again.' : null;

  // Warm the sibling section chunks once the browser is idle, so the FIRST
  // click on any section renders without the lazy-chunk spinner (Wave 1
  // template). Vite dedupes these against the route-level lazy() imports.
  useEffect(() => {
    const idle: (cb: () => void) => void =
      'requestIdleCallback' in window
        ? (cb) => (window as Window & { requestIdleCallback: (cb: () => void) => void }).requestIdleCallback(cb)
        : (cb) => { setTimeout(cb, 1500); };
    idle(() => {
      void import('./ReportsSalesPage');
      void import('./ReportsInventoryPage');
      void import('./ReportsCustomersPage');
      void import('./ReportsGstPage');
      void import('./ReportsForecastPage');
    });
  }, []);

  // Refresh must reload the SECTION the manager is looking at, not just the KPI
  // strip. The old single-page loadReportData() reloaded all eight datasets;
  // refetching only the two KPI queries meant that on Inventory the button left
  // Stock Summary, Brand Performance, Non-moving Stock and Purchase
  // Recommendations untouched - and with isLoading false whenever data already
  // exists, it did not even spin. A manager pressing Refresh on stale numbers
  // would have got no spinner and no new figures.
  //
  // Invalidating the whole 'reports' key refetches every ACTIVE query, so each
  // section reloads exactly what it has mounted and nothing it has not.
  const queryClient = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);
  const refresh = async () => {
    setRefreshing(true);
    try {
      await queryClient.invalidateQueries({ queryKey: ['reports'] });
    } finally {
      setRefreshing(false);
    }
  };

  const formatCurrency = (amount: number) => {
    if (amount >= 100000) {
      return `₹${(amount / 100000).toFixed(2)}L`;
    }
    return `₹${amount.toLocaleString('en-IN')}`;
  };

  const ctx: ReportsContext = { storeId, dateRange, startDate, endDate, canExport };

  return (
    <div className="r-body">
      {/* Editorial header */}
      <div className="r-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Reports</div>
          <h1>The day, in numbers.</h1>
          <div className="hint">Day-end close, MoM &amp; YoY trends, sell-through by category, aging cohorts, GST filing prep.</div>
        </div>
        <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
          <select
            value={dateRange}
            onChange={e => setDateRange(e.target.value as DateRange)}
            className="input"
            style={{ maxWidth: 160 }}
          >
            <option value="today">Today</option>
            <option value="week">This week</option>
            <option value="month">This month</option>
            <option value="quarter">This quarter</option>
          </select>
          <button
            onClick={refresh}
            disabled={isLoading || refreshing}
            className="btn sm"
          >
            {isLoading || refreshing ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            Refresh
          </button>
          {hasRole(['SUPERADMIN']) && (
            <button
              onClick={() => navigate('/reports/blueprint')}
              className="btn-primary sm flex items-center gap-1"
              title="Open the JARVIS-narrated 12-section consultant blueprint"
            >
              <Sparkles className="w-4 h-4" />
              Growth Blueprint
            </button>
          )}
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="s-section" style={{ padding: 12, borderColor: 'var(--err-50)', background: 'var(--err-50)', display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
          <AlertTriangle className="w-5 h-5" style={{ color: 'var(--err)' }} />
          <span style={{ color: 'var(--err)' }}>{error}</span>
          <button onClick={refresh} className="btn sm" style={{ marginLeft: 'auto' }}>Retry</button>
        </div>
      )}

      {/* 4-col KPI grid */}
      <div className="kpi-grid">
        <div className="kpi">
          <div className="l">Total sales</div>
          <div className="v">
            {isLoading || !salesSummary ? <span className="mute">&mdash;</span> : formatCurrency(salesSummary.totalSales)}
          </div>
          {!isLoading && salesGrowth && (
            <div className="r">
              <span className={`dlt ${salesGrowth.mom_growth.percent >= 0 ? 'up' : 'dn'}`}>
                {salesGrowth.mom_growth.percent >= 0 ? '+' : ''}
                {salesGrowth.mom_growth.percent.toFixed(1)}%
              </span>
              <span className="vs">MoM vs last month</span>
            </div>
          )}
        </div>
        <div className="kpi">
          <div className="l">Orders</div>
          <div className="v">
            {isLoading || !salesSummary ? <span className="mute">&mdash;</span> : salesSummary.orderCount}
          </div>
        </div>
        <div className="kpi">
          <div className="l">Avg order value</div>
          <div className="v">
            {isLoading || !salesSummary ? <span className="mute">&mdash;</span> : formatCurrency(salesSummary.averageOrderValue)}
          </div>
        </div>
        <div className="kpi">
          <div className="l">GST collected</div>
          <div className="v">
            {isLoading || !salesSummary ? <span className="mute">&mdash;</span> : formatCurrency(salesSummary.gstCollected)}
          </div>
        </div>
      </div>

      {/* Section nav — same underline tabs, but each now navigates to a real
          URL instead of flipping a useState, so every section is linkable and
          bookmarkable.
          ponytail: still <button>, not <NavLink>, because index.css styles
          `.inv-tabs button` by element — an <a> would render unstyled and
          index.css is outside this PR's blast radius. Widen that selector to
          `.inv-tabs button, .inv-tabs a` in a follow-up and these become real
          anchors (middle-click, open-in-new-tab). */}
      <div className="inv-tabs">
        {SECTIONS.map(({ path, label, icon: TabIcon }) => (
          <button
            key={path}
            onClick={() => navigate(path)}
            className={pathname === path ? 'on' : ''}
          >
            <TabIcon className="w-4 h-4" />
            {label}
          </button>
        ))}
      </div>

      <Outlet context={ctx} />
    </div>
  );
}

export default ReportsLayout;
