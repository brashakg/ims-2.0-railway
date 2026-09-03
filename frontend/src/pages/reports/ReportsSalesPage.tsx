// ============================================================================
// IMS 2.0 - Reports > Sales  (/reports/sales)
// ============================================================================
// The old ReportsPage `activeTab === 'sales'` blocks, lifted onto their own
// URL. Same widgets, same order, same export handlers. Data now comes from
// the shared React Query cache (reportsQueries) instead of a page-level
// useEffect that fetched all sixteen reports.

import { Fragment } from 'react';
import { Download, Loader2 } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { exportToCSV } from '../../utils/exportUtils';
import { PriceBandCard } from './sections/PriceBandCard';
import { LensDeepDiveCard } from './sections/LensDeepDiveCard';
import { SeasonalityCard } from './sections/SeasonalityCard';
import { ReportCardsGrid } from './ReportCardsGrid';
import { useReportsContext } from './ReportsLayout';
import { useSalesGrowth, useSalesSummary, useStaffRanking } from './reportsQueries';

const formatCurrency = (amount: number) => {
  if (amount >= 100000) {
    return `₹${(amount / 100000).toFixed(2)}L`;
  }
  return `₹${amount.toLocaleString('en-IN')}`;
};

export function ReportsSalesPage() {
  const toast = useToast();
  const { storeId, startDate, endDate, canExport } = useReportsContext();

  const summaryQ = useSalesSummary({ storeId, startDate, endDate });
  const growthQ = useSalesGrowth(storeId);
  const staffQ = useStaffRanking({ storeId, startDate, endDate });

  const isLoading = summaryQ.isPending;
  const dailyTrend = summaryQ.data?.dailyTrend ?? [];
  const categoryBreakdown = summaryQ.data?.categoryBreakdown ?? [];
  const salesGrowth = growthQ.data;
  const staffRanking = staffQ.data ?? [];

  const handleExportSalesTrend = () => {
    if (dailyTrend.length === 0) {
      toast.warning('No data to export');
      return;
    }
    exportToCSV(
      dailyTrend.map(d => ({ date: d.date, sales: d.sales.toFixed(2) })),
      'sales_trend',
      [{ key: 'date', label: 'Date' }, { key: 'sales', label: 'Sales (₹)' }]
    );
    toast.success('Sales trend exported');
  };

  const handleExportCategoryBreakdown = () => {
    if (categoryBreakdown.length === 0) {
      toast.warning('No data to export');
      return;
    }
    exportToCSV(
      categoryBreakdown.map(c => ({
        category: c.category,
        sales: c.sales.toFixed(2),
        units: c.units,
        percentage: c.percentage.toFixed(1),
      })),
      'category_breakdown',
      [
        { key: 'category', label: 'Category' },
        { key: 'sales', label: 'Sales (₹)' },
        { key: 'units', label: 'Units Sold' },
        { key: 'percentage', label: 'Percentage (%)' },
      ]
    );
    toast.success('Category breakdown exported');
  };

  return (
    <>
      <div className="grid grid-cols-1 laptop:grid-cols-2 gap-4">
        {/* Sales Trend */}
        <div className="chart-card">
          <div className="chart-head">
            <h3>Sales Trend</h3>
            <div className="spacer" />
            {canExport && (
              <button
                onClick={handleExportSalesTrend}
                className="btn sm flex items-center gap-1"
              >
                <Download className="w-4 h-4" />
                Export CSV
              </button>
            )}
          </div>
          <div className="chart-body">
            {isLoading ? (
              <div className="h-48 flex items-center justify-center">
                <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
              </div>
            ) : dailyTrend.length === 0 ? (
              <div className="h-48 flex items-center justify-center text-gray-500">
                <p>No sales data available for this period</p>
              </div>
            ) : (
              <div className="h-48 flex items-end gap-2">
                {dailyTrend.map((day, index) => {
                  const maxSales = Math.max(...dailyTrend.map(d => d.sales));
                  const height = maxSales > 0 ? (day.sales / maxSales) * 100 : 0;
                  return (
                    <div key={index} className="flex-1 flex flex-col items-center gap-1">
                      <div
                        className="w-full bg-bv-red-600 rounded-t transition-all hover:bg-bv-red-700"
                        style={{ height: `${height}%` }}
                        title={formatCurrency(day.sales)}
                      />
                      <span className="text-xs text-gray-500">{day.date}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {/* Category Breakdown */}
        <div className="chart-card">
          <div className="chart-head">
            <h3>Category Breakdown</h3>
            <div className="spacer" />
            {canExport && categoryBreakdown.length > 0 && (
              <button
                onClick={handleExportCategoryBreakdown}
                className="btn sm flex items-center gap-1"
              >
                <Download className="w-4 h-4" />
                Export CSV
              </button>
            )}
          </div>
          <div className="chart-body">
            {isLoading ? (
              <div className="space-y-3">
                {[1, 2, 3, 4, 5].map((i) => (
                  <div key={i}>
                    <div className="flex items-center justify-between mb-1">
                      <div className="h-4 w-20 bg-gray-200 animate-pulse rounded" />
                      <div className="h-4 w-16 bg-gray-200 animate-pulse rounded" />
                    </div>
                    <div className="h-2 bg-gray-200 rounded-full" />
                  </div>
                ))}
              </div>
            ) : categoryBreakdown.length === 0 ? (
              <div className="py-8 text-center text-gray-500">
                <p>No category data available</p>
              </div>
            ) : (
              <div className="bars">
                {categoryBreakdown.map((cat, index) => (
                  <Fragment key={index}>
                    <span className="label" title={cat.category}>{cat.category}</span>
                    <div className="track" title={`${formatCurrency(cat.sales)} · ${cat.percentage.toFixed(1)}%`}>
                      <div className="fill bv" style={{ width: `${Math.max(cat.percentage, 2)}%` }} />
                      <span className={cat.percentage >= 45 ? 'val' : 'val out'}>{formatCurrency(cat.sales)}</span>
                    </div>
                  </Fragment>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Sales Comparison Card — MoM + YoY from /reports/sales/growth */}
      <div className="chart-card" style={{ marginBottom: 14 }}>
        <div className="chart-head">
          <h3>Sales Comparison — Month-over-Month &amp; Year-over-Year</h3>
        </div>
        <div className="chart-body">
        {growthQ.isPending ? (
          <div className="h-32 flex items-center justify-center">
            <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
          </div>
        ) : salesGrowth ? (
          <div className="grid grid-cols-2 tablet:grid-cols-3 gap-4">
            <div className="bg-white rounded p-3">
              <p className="text-xs text-gray-500">This Month</p>
              <p className="text-lg font-bold text-gray-900 mt-1">
                ₹{(salesGrowth.current_month.sales / 100000).toFixed(2)}L
              </p>
              <p className="text-xs text-gray-500 mt-1">{salesGrowth.current_month.orders} orders</p>
            </div>
            <div className="bg-white rounded p-3">
              <p className="text-xs text-gray-500">MoM Growth</p>
              <p className={`text-lg font-bold mt-1 ${
                salesGrowth.mom_growth.percent >= 0 ? 'text-green-600' : 'text-red-600'
              }`}>
                {salesGrowth.mom_growth.percent >= 0 ? '+' : ''}
                {salesGrowth.mom_growth.percent.toFixed(1)}%
              </p>
              <p className="text-xs text-gray-500 mt-1">
                vs ₹{(salesGrowth.mom_growth.previous_month_sales / 100000).toFixed(2)}L last month
              </p>
            </div>
            <div className="bg-white rounded p-3">
              <p className="text-xs text-gray-500">YoY Growth</p>
              <p className={`text-lg font-bold mt-1 ${
                salesGrowth.yoy_growth.percent >= 0 ? 'text-green-600' : 'text-red-600'
              }`}>
                {salesGrowth.yoy_growth.percent >= 0 ? '+' : ''}
                {salesGrowth.yoy_growth.percent.toFixed(1)}%
              </p>
              <p className="text-xs text-gray-500 mt-1">
                vs ₹{(salesGrowth.yoy_growth.previous_year_sales / 100000).toFixed(2)}L this month last year
              </p>
            </div>
          </div>
        ) : (
          <p className="text-sm text-gray-500">Growth data unavailable for the current period.</p>
        )}
        </div>
      </div>

      {/* Staff Performance Card */}
      <div className="chart-card" style={{ marginBottom: 14 }}>
        <div className="chart-head">
          <h3>Top Performers</h3>
        </div>
        <div className="chart-body">
        {staffQ.isPending ? (
          <div className="h-32 flex items-center justify-center">
            <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
          </div>
        ) : staffRanking.length === 0 ? (
          <div className="py-8 text-center text-gray-500">
            <p>No staff data available</p>
          </div>
        ) : (
          <div className="space-y-2">
            {staffRanking.slice(0, 3).map((staff, idx: number) => (
              <div key={idx} className="flex items-center justify-between py-2 border-b border-gray-200 last:border-b-0">
                <div className="flex items-center gap-2">
                  <div className="w-6 h-6 rounded-full bg-bv-red-600 text-white text-xs flex items-center justify-center">{idx + 1}</div>
                  <span className="text-sm text-gray-600">{staff.staff_name}</span>
                </div>
                <span className="text-sm font-medium text-gray-900">₹{(staff.total_sales / 1000).toFixed(0)}K</span>
              </div>
            ))}
          </div>
        )}
        </div>
      </div>

      {/* TechCherry R1 — net-new analytics dimensions (Sales) */}
      <PriceBandCard storeId={storeId} />
      <LensDeepDiveCard storeId={storeId} />
      <SeasonalityCard storeId={storeId} />

      <ReportCardsGrid category="sales" />
    </>
  );
}

export default ReportsSalesPage;
