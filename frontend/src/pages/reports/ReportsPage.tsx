// ============================================================================
// IMS 2.0 - Reports Page
// ============================================================================
// NO MOCK DATA - All data from API

import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  BarChart3,
  TrendingUp,
  Download,
  Package,
  Users,
  FileText,
  Eye,
  Printer,
  Loader2,
  RefreshCw,
  AlertTriangle,
  X,
} from 'lucide-react';
import { reportsApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { GSTR1Report } from '../../components/reports/GSTR1Report';
import { GSTR3BReport } from '../../components/reports/GSTR3BReport';
import { DemandForecast } from '../../components/reports/DemandForecast';
import { exportToCSV, SALES_REPORT_COLUMNS, INVENTORY_REPORT_COLUMNS, CUSTOMER_REPORT_COLUMNS, GST_REPORT_COLUMNS } from '../../utils/exportUtils';

type ReportType = 'sales' | 'inventory' | 'customers' | 'gst' | 'forecast';
type DateRange = 'today' | 'week' | 'month' | 'quarter' | 'custom';

// Types
interface SalesSummary {
  totalSales: number;
  orderCount: number;
  averageOrderValue: number;
  topCategory: string;
  grossProfit: number;
  gstCollected: number;
}

interface CategoryBreakdown {
  category: string;
  sales: number;
  units: number;
  percentage: number;
}

interface DailyTrend {
  date: string;
  sales: number;
}

// Report cards
const REPORT_CARDS = [
  {
    id: 'daily-sales',
    title: 'Daily Sales Report',
    description: 'Day-wise sales breakdown with payment modes',
    icon: BarChart3,
    category: 'sales' as ReportType,
  },
  {
    id: 'monthly-sales',
    title: 'Monthly Sales Summary',
    description: 'Monthly sales with category and brand analysis',
    icon: TrendingUp,
    category: 'sales' as ReportType,
  },
  {
    id: 'stock-report',
    title: 'Stock Report',
    description: 'Current stock levels by category and brand',
    icon: Package,
    category: 'inventory' as ReportType,
  },
  {
    id: 'stock-movement',
    title: 'Stock Movement',
    description: 'Stock in/out movements and transfers',
    icon: Package,
    category: 'inventory' as ReportType,
  },
  {
    id: 'customer-report',
    title: 'Customer Report',
    description: 'Customer acquisition and purchase patterns',
    icon: Users,
    category: 'customers' as ReportType,
  },
  {
    id: 'gst-report',
    title: 'GST Report',
    description: 'GSTR-1 and GSTR-3B data for filing',
    icon: FileText,
    category: 'gst' as ReportType,
  },
];

export function ReportsPage() {
  const { user, hasRole } = useAuth();
  const toast = useToast();
  const [searchParams] = useSearchParams();

  // Data state
  const [salesSummary, setSalesSummary] = useState<SalesSummary>({
    totalSales: 0,
    orderCount: 0,
    averageOrderValue: 0,
    topCategory: '-',
    grossProfit: 0,
    gstCollected: 0,
  });
  const [categoryBreakdown, setCategoryBreakdown] = useState<CategoryBreakdown[]>([]);
  const [dailyTrend, setDailyTrend] = useState<DailyTrend[]>([]);
  const [_salesComparison] = useState<any>(null);
  const [_growthMetrics] = useState<any>(null);
  // Phase 6.3 — cash-tied-up-on-shelves & MoM/YoY growth
  const [nonMovingStock, setNonMovingStock] = useState<Array<{
    product_id: string | null;
    sku: string | null;
    brand: string | null;
    model: string | null;
    category: string | null;
    mrp: number;
    last_sold_at: string | null;
    days_since_sold: number | null;
    never_sold: boolean;
    total_sold_all_time: number;
  }>>([]);
  const [salesGrowth, setSalesGrowth] = useState<{
    current_month: { sales: number; orders: number };
    mom_growth: { percent: number; previous_month_sales: number };
    yoy_growth: { percent: number; previous_year_sales: number };
  } | null>(null);
  const [staffRanking, ] = useState<any[]>([]);
  const [stockCount, ] = useState<any>(null);
  const [brandSellthrough, ] = useState<any[]>([]);
  const [customerAcquisition, ] = useState<any>(null);
  const [discountAnalysis, ] = useState<any>(null);
  // Unused - setExpenseVsRevenue not used in this component
  const [expenseVsRevenue] = useState<any>(null);
  // UI state
  const [activeTab, setActiveTab] = useState<ReportType>('sales');
  const [dateRange, setDateRange] = useState<DateRange>('month');

  // Sync active tab from URL query params (e.g. /reports?tab=gst)
  useEffect(() => {
    const tabParam = searchParams.get('tab');
    if (tabParam && tabParam !== activeTab) {
      const validTabs: ReportType[] = ['sales', 'inventory', 'customers', 'gst'];
      if (validTabs.includes(tabParam as ReportType)) {
        setActiveTab(tabParam as ReportType);
      }
    }
  }, [searchParams]);

  // Loading state
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // GST Reports Modal state
  const [showGSTR1, setShowGSTR1] = useState(false);
  const [showGSTR3B, setShowGSTR3B] = useState(false);

  // Role-based permissions
  const canExport = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']);

  // Load data on mount and when date range changes
  useEffect(() => {
    loadReportData();
  }, [user?.activeStoreId, dateRange]);

  const getDateRange = (): { startDate: string; endDate: string } => {
    const now = new Date();
    const endDate = now.toISOString().split('T')[0];
    let startDate: string;

    switch (dateRange) {
      case 'today':
        startDate = endDate;
        break;
      case 'week':
        const weekAgo = new Date(now);
        weekAgo.setDate(weekAgo.getDate() - 7);
        startDate = weekAgo.toISOString().split('T')[0];
        break;
      case 'month':
        const monthAgo = new Date(now);
        monthAgo.setMonth(monthAgo.getMonth() - 1);
        startDate = monthAgo.toISOString().split('T')[0];
        break;
      case 'quarter':
        const quarterAgo = new Date(now);
        quarterAgo.setMonth(quarterAgo.getMonth() - 3);
        startDate = quarterAgo.toISOString().split('T')[0];
        break;
      default:
        startDate = endDate;
    }

    return { startDate, endDate };
  };

  const loadReportData = async () => {
    if (!user?.activeStoreId) return;

    setIsLoading(true);
    setError(null);

    try {
      const { startDate, endDate } = getDateRange();
      const response = await reportsApi.getSalesSummary(user.activeStoreId, startDate, endDate);

      if (response) {
        setSalesSummary({
          totalSales: response.summary?.total_sales || 0,
          orderCount: response.summary?.total_orders || 0,
          averageOrderValue: response.summary?.avg_order_value || 0,
          topCategory: '-', // Not available from backend summary
          grossProfit: 0, // Not available from backend summary
          gstCollected: response.summary?.total_tax || 0,
        });

        if (response.categoryBreakdown) {
          setCategoryBreakdown(response.categoryBreakdown);
        }

        if (response.dailyTrend) {
          setDailyTrend(response.dailyTrend);
        }
      }

      // Phase 6.3 — fetch non-moving stock + MoM/YoY growth in parallel.
      // Both are additive — if either fails, the page still renders with
      // the core sales summary above.
      const now = new Date();
      const [nmsRes, growthRes] = await Promise.allSettled([
        reportsApi.getNonMovingStock(user.activeStoreId, 90, 200),
        reportsApi.getSalesGrowth(user.activeStoreId, now.getFullYear(), now.getMonth() + 1),
      ]);
      if (nmsRes.status === 'fulfilled') {
        setNonMovingStock(nmsRes.value.data || []);
      }
      if (growthRes.status === 'fulfilled') {
        setSalesGrowth(growthRes.value);
      }
    } catch {
      setError('Failed to load report data. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  const filteredReports = REPORT_CARDS.filter(r => r.category === activeTab);

  // Export handlers
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

  const handleExportReport = (reportId: string) => {
    // Export current summary data based on report type
    const reportData: Record<string, any>[] = [];
    let columns: { key: string; label: string }[] = [];

    switch (reportId) {
      case 'daily-sales':
      case 'monthly-sales':
        reportData.push({
          period: dateRange,
          totalSales: salesSummary.totalSales.toFixed(2),
          orderCount: salesSummary.orderCount,
          averageOrderValue: salesSummary.averageOrderValue.toFixed(2),
          grossProfit: salesSummary.grossProfit.toFixed(2),
          gstCollected: salesSummary.gstCollected.toFixed(2),
          topCategory: salesSummary.topCategory,
        });
        columns = SALES_REPORT_COLUMNS.slice(0, 4).concat([
          { key: 'totalSales', label: 'Total Sales (₹)' },
          { key: 'orderCount', label: 'Orders' },
          { key: 'averageOrderValue', label: 'Avg Order Value (₹)' },
          { key: 'grossProfit', label: 'Gross Profit (₹)' },
          { key: 'gstCollected', label: 'GST Collected (₹)' },
        ]);
        break;
      case 'stock-report':
      case 'stock-movement':
        columns = INVENTORY_REPORT_COLUMNS;
        break;
      case 'customer-report':
        columns = CUSTOMER_REPORT_COLUMNS;
        break;
      case 'gst-report':
        columns = GST_REPORT_COLUMNS;
        break;
    }

    if (reportData.length > 0) {
      exportToCSV(reportData, reportId, columns);
      toast.success(`${reportId.replace('-', ' ')} exported`);
    } else {
      // For reports without local data, export what we have
      const summaryData = [{
        period: dateRange,
        totalSales: salesSummary.totalSales.toFixed(2),
        orderCount: salesSummary.orderCount,
        averageOrderValue: salesSummary.averageOrderValue.toFixed(2),
        gstCollected: salesSummary.gstCollected.toFixed(2),
      }];
      exportToCSV(summaryData, reportId);
      toast.success(`${reportId.replace(/-/g, ' ')} summary exported`);
    }
  };

  const handlePrintReport = (reportTitle: string) => {
    // Print current page content
    const printContent = document.querySelector('.space-y-4');
    if (printContent) {
      const printWindow = window.open('', '_blank');
      if (printWindow) {
        printWindow.document.write(`
          <!DOCTYPE html><html><head><title>${reportTitle}</title>
          <style>
            body { font-family: sans-serif; padding: 20px; }
            .card { border: 1px solid #ddd; padding: 16px; margin: 8px 0; border-radius: 8px; }
            table { width: 100%; border-collapse: collapse; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; font-size: 12px; }
            th { background: #f5f5f5; }
          </style></head><body>
          <h1>${reportTitle}</h1>
          <p>Date: ${new Date().toLocaleDateString('en-IN')}</p>
          <table><tr><th>Metric</th><th>Value</th></tr>
          <tr><td>Total Sales</td><td>₹${salesSummary.totalSales.toLocaleString('en-IN')}</td></tr>
          <tr><td>Orders</td><td>${salesSummary.orderCount}</td></tr>
          <tr><td>Avg Order Value</td><td>₹${salesSummary.averageOrderValue.toLocaleString('en-IN')}</td></tr>
          <tr><td>GST Collected</td><td>₹${salesSummary.gstCollected.toLocaleString('en-IN')}</td></tr>
          </table>
          </body></html>
        `);
        printWindow.document.close();
        printWindow.focus();
        printWindow.print();
        printWindow.close();
      }
    }
  };

  const formatCurrency = (amount: number) => {
    if (amount >= 100000) {
      return `₹${(amount / 100000).toFixed(2)}L`;
    }
    return `₹${amount.toLocaleString('en-IN')}`;
  };

  const reportTabs = [
    { id: 'sales' as ReportType,     label: 'Sales',     icon: BarChart3 },
    { id: 'inventory' as ReportType, label: 'Inventory', icon: Package },
    { id: 'customers' as ReportType, label: 'Customers', icon: Users },
    { id: 'gst' as ReportType,       label: 'GST',       icon: FileText },
    { id: 'forecast' as ReportType,  label: 'Forecast',  icon: TrendingUp },
  ];

  return (
    <div className="r-body">
      {/* Editorial header */}
      <div className="r-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Reports</div>
          <h1>The day, in numbers.</h1>
          <div className="hint">Day-end close, MoM & YoY trends, sell-through by category, aging cohorts, GST filing prep.</div>
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
            onClick={loadReportData}
            disabled={isLoading}
            className="btn sm"
          >
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            Refresh
          </button>
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="s-section" style={{ padding: 12, borderColor: 'var(--err-50)', background: 'var(--err-50)', display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
          <AlertTriangle className="w-5 h-5" style={{ color: 'var(--err)' }} />
          <span style={{ color: 'var(--err)' }}>{error}</span>
          <button onClick={loadReportData} className="btn sm" style={{ marginLeft: 'auto' }}>Retry</button>
        </div>
      )}

      {/* 4-col KPI grid */}
      <div className="kpi-grid">
        <div className="kpi">
          <div className="l">Total sales</div>
          <div className="v">
            {isLoading ? <span className="mute">—</span> : formatCurrency(salesSummary.totalSales)}
          </div>
        </div>
        <div className="kpi">
          <div className="l">Orders</div>
          <div className="v">
            {isLoading ? <span className="mute">—</span> : salesSummary.orderCount}
          </div>
        </div>
        <div className="kpi">
          <div className="l">Avg order value</div>
          <div className="v">
            {isLoading ? <span className="mute">—</span> : formatCurrency(salesSummary.averageOrderValue)}
          </div>
        </div>
        <div className="kpi">
          <div className="l">GST collected</div>
          <div className="v">
            {isLoading ? <span className="mute">—</span> : formatCurrency(salesSummary.gstCollected)}
          </div>
        </div>
      </div>

      {/* Tabs — underline style, reuses .inv-tabs from Inventory */}
      <div className="inv-tabs">
        {reportTabs.map(tab => {
          const TabIcon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={activeTab === tab.id ? 'on' : ''}
            >
              <TabIcon className="w-4 h-4" />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Report Content */}
      {activeTab === 'sales' && (
        <div className="grid grid-cols-1 laptop:grid-cols-2 gap-4">
          {/* Sales Trend */}
          <div className="card">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-semibold text-gray-900">Sales Trend</h3>
              {canExport && (
                <button
                  onClick={handleExportSalesTrend}
                  className="text-sm text-bv-red-600 hover:text-bv-red-700 flex items-center gap-1"
                >
                  <Download className="w-4 h-4" />
                  Export CSV
                </button>
              )}
            </div>
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

          {/* Category Breakdown */}
          <div className="card">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-semibold text-gray-900">Category Breakdown</h3>
              {canExport && categoryBreakdown.length > 0 && (
                <button
                  onClick={handleExportCategoryBreakdown}
                  className="text-sm text-bv-red-600 hover:text-bv-red-700 flex items-center gap-1"
                >
                  <Download className="w-4 h-4" />
                  Export CSV
                </button>
              )}
            </div>
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
              <div className="space-y-3">
                {categoryBreakdown.map((cat, index) => (
                  <div key={index}>
                    <div className="flex items-center justify-between text-sm mb-1">
                      <span className="text-gray-500">{cat.category}</span>
                      <span className="font-medium">{formatCurrency(cat.sales)}</span>
                    </div>
                    <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-bv-red-600 rounded-full"
                        style={{ width: `${cat.percentage}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}


      {/* Sales Comparison + Top Performers — sales-tab-only.
          Audit 2026-04-21 flagged these rendering on Inventory + Customers
          tabs (tab-isolation bug). Wrapped in the same conditional as the
          other sales widgets above. */}
      {activeTab === 'sales' && <>
      {/* Sales Comparison Card — MoM + YoY from /reports/sales/growth */}
      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold text-gray-900">Sales Comparison — Month-over-Month &amp; Year-over-Year</h3>
        </div>
        {isLoading ? (
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

      {/* Staff Performance Card */}
      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold text-gray-900">Top Performers</h3>
        </div>
        {isLoading ? (
          <div className="h-32 flex items-center justify-center">
            <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
          </div>
        ) : staffRanking.length === 0 ? (
          <div className="py-8 text-center text-gray-500">
            <p>No staff data available</p>
          </div>
        ) : (
          <div className="space-y-2">
            {staffRanking.slice(0, 3).map((staff: any, idx: number) => (
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
      </>}
      {/* Report Cards */}
      <div>
        <h3 className="font-semibold text-gray-900 mb-3">Available Reports</h3>
        <div className="grid grid-cols-1 tablet:grid-cols-2 laptop:grid-cols-3 gap-4">
          {filteredReports.map(report => (
            <div key={report.id} className="card hover:border-bv-red-300 transition-colors">
              <div className="flex items-start gap-4">
                <div className="w-10 h-10 bg-bv-red-100 rounded-lg flex items-center justify-center flex-shrink-0">
                  <report.icon className="w-5 h-5 text-bv-red-600" />
                </div>
                <div className="flex-1 min-w-0">
                  <h4 className="font-medium text-gray-900">{report.title}</h4>
                  <p className="text-sm text-gray-500 mt-1">{report.description}</p>
                  <div className="flex items-center gap-2 mt-3">
                    {report.id === 'gst-report' ? (
                      <button
                        onClick={() => setShowGSTR1(true)}
                        className="text-sm text-bv-red-600 hover:text-bv-red-700 flex items-center gap-1"
                      >
                        <Eye className="w-4 h-4" />
                        View GSTR-1
                      </button>
                    ) : (
                      <button
                        onClick={() => toast.info(`Detailed ${report.title} view requires store data`)}
                        className="text-sm text-bv-red-600 hover:text-bv-red-700 flex items-center gap-1"
                      >
                        <Eye className="w-4 h-4" />
                        View
                      </button>
                    )}
                    {canExport && (
                      <button
                        onClick={() => handleExportReport(report.id)}
                        className="text-sm text-gray-500 hover:text-gray-700 flex items-center gap-1"
                      >
                        <Download className="w-4 h-4" />
                        Export CSV
                      </button>
                    )}
                    <button
                      onClick={() => handlePrintReport(report.title)}
                      className="text-sm text-gray-500 hover:text-gray-700 flex items-center gap-1"
                    >
                      <Printer className="w-4 h-4" />
                      Print
                    </button>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

            {/* Inventory Tab Content */}
      {activeTab === 'inventory' && (
        <div className="grid grid-cols-1 laptop:grid-cols-2 gap-4">
          {/* Stock Count Card */}
          <div className="card">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-semibold text-gray-900">Stock Summary</h3>
            </div>
            {isLoading ? (
              <div className="h-32 flex items-center justify-center">
                <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
              </div>
            ) : (
              <div className="space-y-3">
                {stockCount ? (
                  <>
                    <div className="flex items-center justify-between">
                      <span className="text-gray-500">Total Items:</span>
                      <span className="font-medium text-gray-900">{stockCount.summary?.total_items || 0}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-gray-500">Total Quantity:</span>
                      <span className="font-medium text-gray-900">{stockCount.summary?.total_quantity || 0} units</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-gray-500">Total Value:</span>
                      <span className="font-medium text-gray-900">₹{((stockCount.summary?.total_value || 0) / 100000).toFixed(2)}L</span>
                    </div>
                  </>
                ) : (
                  <p className="text-gray-500">No data available</p>
                )}
              </div>
            )}
          </div>

          {/* Brand Sell-through Card */}
          <div className="card">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-semibold text-gray-900">Brand Performance</h3>
            </div>
            {isLoading ? (
              <div className="h-32 flex items-center justify-center">
                <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
              </div>
            ) : brandSellthrough.length === 0 ? (
              <div className="py-8 text-center text-gray-500">
                <p>No brand data available</p>
              </div>
            ) : (
              <div className="space-y-2">
                {brandSellthrough.slice(0, 5).map((brand: any, idx: number) => (
                  <div key={idx} className="flex items-center justify-between py-2 border-b border-gray-200 last:border-b-0">
                    <span className="text-sm text-gray-600 truncate">{brand.brand}</span>
                    <div className="text-right">
                      <p className="text-sm font-medium text-gray-900">{brand.quantity_sold} units</p>
                      <p className="text-xs text-gray-500">{brand.sellthrough_percent || 0}% sell-through</p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Non-moving Stock — full-width table below the 2-col grid.
              Surfaces products with no sale in the last 90 days. Never-sold
              SKUs float to the top. Read by store managers making clearance
              + transfer decisions. */}
          <div className="card laptop:col-span-2">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="font-semibold text-gray-900">Non-moving Stock (90+ days)</h3>
                <p className="text-xs text-gray-500 mt-1">
                  {nonMovingStock.length} product{nonMovingStock.length === 1 ? '' : 's'} with no sales in the last 90 days
                </p>
              </div>
              {canExport && nonMovingStock.length > 0 && (
                <button
                  onClick={() => {
                    exportToCSV(
                      nonMovingStock.map(p => ({
                        sku: p.sku || '',
                        brand: p.brand || '',
                        model: p.model || '',
                        category: p.category || '',
                        mrp: p.mrp,
                        last_sold_at: p.last_sold_at || 'Never sold',
                        days_since_sold: p.never_sold ? 'Never' : String(p.days_since_sold ?? '-'),
                        total_sold_all_time: p.total_sold_all_time,
                      })),
                      'non_moving_stock_90d',
                      [
                        { key: 'sku', label: 'SKU' },
                        { key: 'brand', label: 'Brand' },
                        { key: 'model', label: 'Model' },
                        { key: 'category', label: 'Category' },
                        { key: 'mrp', label: 'MRP (₹)' },
                        { key: 'last_sold_at', label: 'Last Sold' },
                        { key: 'days_since_sold', label: 'Days Since' },
                        { key: 'total_sold_all_time', label: 'Lifetime Units Sold' },
                      ]
                    );
                    toast.success('Non-moving stock exported');
                  }}
                  className="text-sm text-bv-red-600 hover:text-bv-red-700 flex items-center gap-1"
                >
                  <Download className="w-4 h-4" />
                  Export CSV
                </button>
              )}
            </div>
            {isLoading ? (
              <div className="h-32 flex items-center justify-center">
                <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
              </div>
            ) : nonMovingStock.length === 0 ? (
              <div className="py-8 text-center text-gray-500">
                <p>No stale inventory detected — everything has turned over in the last 90 days.</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="border-b border-gray-200 text-left text-xs uppercase tracking-wider text-gray-500">
                    <tr>
                      <th className="py-2 pr-3">SKU</th>
                      <th className="py-2 pr-3">Brand · Model</th>
                      <th className="py-2 pr-3">Category</th>
                      <th className="py-2 pr-3 text-right">MRP</th>
                      <th className="py-2 pr-3">Last Sold</th>
                      <th className="py-2 pr-3 text-right">Days Stale</th>
                    </tr>
                  </thead>
                  <tbody>
                    {nonMovingStock.slice(0, 50).map((p, idx) => (
                      <tr key={p.product_id || p.sku || idx} className="border-b border-gray-100 last:border-b-0">
                        <td className="py-2 pr-3 font-mono text-xs text-gray-700">{p.sku || '-'}</td>
                        <td className="py-2 pr-3 text-gray-900">
                          <span className="font-medium">{p.brand || 'Unbranded'}</span>
                          {p.model ? <span className="text-gray-500"> · {p.model}</span> : null}
                        </td>
                        <td className="py-2 pr-3 text-gray-600">{p.category || '-'}</td>
                        <td className="py-2 pr-3 text-right text-gray-700">₹{(p.mrp || 0).toLocaleString('en-IN')}</td>
                        <td className="py-2 pr-3 text-gray-600">
                          {p.never_sold
                            ? <span className="text-red-600 font-medium">Never sold</span>
                            : (p.last_sold_at ? new Date(p.last_sold_at).toLocaleDateString('en-IN') : '-')}
                        </td>
                        <td className="py-2 pr-3 text-right">
                          <span className={`font-medium ${
                            p.never_sold || (p.days_since_sold ?? 0) >= 180 ? 'text-red-600'
                            : (p.days_since_sold ?? 0) >= 120 ? 'text-orange-600'
                            : 'text-yellow-600'
                          }`}>
                            {p.never_sold ? '∞' : p.days_since_sold}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {nonMovingStock.length > 50 && (
                  <p className="text-xs text-gray-500 mt-3">
                    Showing first 50 of {nonMovingStock.length}. Export CSV for the full list.
                  </p>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Customers Tab Content */}
      {activeTab === 'customers' && (
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-semibold text-gray-900">Customer Acquisition & Retention</h3>
          </div>
          {isLoading ? (
            <div className="h-40 flex items-center justify-center">
              <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
            </div>
          ) : customerAcquisition ? (
            <div className="grid grid-cols-2 tablet:grid-cols-4 gap-3">
              <div className="bg-white rounded p-3">
                <p className="text-xs text-gray-500">New Customers</p>
                <p className="text-xl font-bold text-green-600 mt-1">{customerAcquisition.new_customers}</p>
              </div>
              <div className="bg-white rounded p-3">
                <p className="text-xs text-gray-500">Returning</p>
                <p className="text-xl font-bold text-blue-600 mt-1">{customerAcquisition.returning_customers}</p>
              </div>
              <div className="bg-white rounded p-3">
                <p className="text-xs text-gray-500">Retention Rate</p>
                <p className="text-xl font-bold text-purple-600 mt-1">{customerAcquisition.retention_percent}%</p>
              </div>
              <div className="bg-white rounded p-3">
                <p className="text-xs text-gray-500">Total Customers</p>
                <p className="text-xl font-bold text-orange-600 mt-1">{customerAcquisition.total_customers}</p>
              </div>
            </div>
          ) : (
            <p className="text-gray-500">No data available</p>
          )}
        </div>
      )}

{/* GST Reports Section (when GST tab selected) */}
      {activeTab === 'gst' && (
        <div className="card bg-yellow-50 border-yellow-200">
          <div className="flex items-start gap-4">
            <div className="w-10 h-10 bg-yellow-100 rounded-lg flex items-center justify-center flex-shrink-0">
              <FileText className="w-5 h-5 text-yellow-600" />
            </div>
            <div>
              <h4 className="font-medium text-gray-900">GST Filing Data Ready</h4>
              <p className="text-sm text-gray-500 mt-1">
                GST data for the period has been compiled. Download the reports for GSTR-1 and GSTR-3B filing.
              </p>
              <div className="flex gap-3 mt-3">
                <button
                  onClick={() => setShowGSTR1(true)}
                  className="btn-primary text-sm"
                >
                  View GSTR-1
                </button>
                <button
                  onClick={() => setShowGSTR3B(true)}
                  className="btn-outline text-sm"
                >
                  View GSTR-3B
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

            {/* Discount Analysis Section (in GST tab) */}
      {activeTab === 'gst' && (
        <div className="card mt-4">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-semibold text-gray-900">Discount Analysis</h3>
          </div>
          {isLoading ? (
            <div className="h-32 flex items-center justify-center">
              <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
            </div>
          ) : discountAnalysis ? (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="bg-white rounded p-3">
                  <p className="text-xs text-gray-500">Total Discount</p>
                  <p className="text-lg font-bold text-gray-900">₹{((discountAnalysis.summary?.total_discount || 0) / 1000).toFixed(1)}K</p>
                </div>
                <div className="bg-white rounded p-3">
                  <p className="text-xs text-gray-500">Discount %</p>
                  <p className="text-lg font-bold text-orange-600">{discountAnalysis.summary?.discount_percent || 0}%</p>
                </div>
              </div>
              <div className="space-y-2">
                <p className="text-sm font-medium text-gray-500">By Category:</p>
                {discountAnalysis.by_category?.slice(0, 3).map((cat: any, idx: number) => (
                  <div key={idx} className="flex items-center justify-between text-sm">
                    <span className="text-gray-600">{cat.category}</span>
                    <span className="text-bv-red-400">₹{(cat.total_discount / 1000).toFixed(1)}K</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <p className="text-gray-500">No data available</p>
          )}
        </div>
      )}

      {/* Expense vs Revenue Section (in Forecast tab) */}
      {activeTab === 'forecast' && (
        <div className="card mb-4">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-semibold text-gray-900">Expense vs Revenue</h3>
          </div>
          {isLoading ? (
            <div className="h-40 flex items-center justify-center">
              <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
            </div>
          ) : expenseVsRevenue ? (
            <div className="space-y-4">
              <div className="grid grid-cols-2 tablet:grid-cols-4 gap-3">
                <div className="bg-white rounded p-3">
                  <p className="text-xs text-gray-500">Revenue</p>
                  <p className="text-lg font-bold text-green-600">₹{((expenseVsRevenue.revenue || 0) / 100000).toFixed(2)}L</p>
                </div>
                <div className="bg-white rounded p-3">
                  <p className="text-xs text-gray-500">Cost</p>
                  <p className="text-lg font-bold text-red-600">₹{((expenseVsRevenue.cost || 0) / 100000).toFixed(2)}L</p>
                </div>
                <div className="bg-white rounded p-3">
                  <p className="text-xs text-gray-500">Profit</p>
                  <p className="text-lg font-bold text-blue-600">₹{((expenseVsRevenue.profit || 0) / 100000).toFixed(2)}L</p>
                </div>
                <div className="bg-white rounded p-3">
                  <p className="text-xs text-gray-500">Margin</p>
                  <p className="text-lg font-bold text-purple-600">{expenseVsRevenue.margin_percent || 0}%</p>
                </div>
              </div>
            </div>
          ) : (
            <p className="text-gray-500">No data available</p>
          )}
        </div>
      )}

{/* Demand Forecast Section */}
      {activeTab === 'forecast' && (
        <DemandForecast />
      )}

      {/* GSTR-1 Modal */}
      {showGSTR1 && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-7xl max-h-[90vh] overflow-y-auto">
            <div className="sticky top-0 bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
              <h2 className="text-xl font-bold text-gray-900">GSTR-1 Report</h2>
              <button
                onClick={() => setShowGSTR1(false)}
                className="p-2 hover:bg-gray-100 rounded-lg"
              >
                <X className="w-5 h-5 text-gray-500" />
              </button>
            </div>
            <div className="p-6">
              <GSTR1Report />
            </div>
          </div>
        </div>
      )}

      {/* GSTR-3B Modal */}
      {showGSTR3B && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-7xl max-h-[90vh] overflow-y-auto">
            <div className="sticky top-0 bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
              <h2 className="text-xl font-bold text-gray-900">GSTR-3B Report</h2>
              <button
                onClick={() => setShowGSTR3B(false)}
                className="p-2 hover:bg-gray-100 rounded-lg"
              >
                <X className="w-5 h-5 text-gray-500" />
              </button>
            </div>
            <div className="p-6">
              <GSTR3BReport />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default ReportsPage;
