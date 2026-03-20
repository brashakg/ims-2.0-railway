import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  BarChart3,
  TrendingUp,
  Package,
  Users,
  ShoppingCart,
  AlertTriangle,
  Eye,
  Zap,
  RefreshCw,
  Download,
  DollarSign,
  Percent,
  Plus,
  ArrowRight,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { reportsApi, analyticsApi, adminStoreApi } from '../../services/api';
import { EnterpriseKpiCard } from '../../components/dashboard/EnterpriseKpiCard';
import { LineChart, DonutChart, formatChartValue } from '../../components/dashboard/AdvancedCharts';
import { MultiStorePerformanceTable } from '../../components/dashboard/MultiStorePerformanceTable';
import clsx from 'clsx';

// ============================================================================
// Types
// ============================================================================

interface EnterpriseMetrics {
  // Revenue Metrics
  totalRevenue: number;
  revenueChange: number;
  revenueYoY: number;
  revenueTrend: number[];

  // Order Metrics
  totalOrders: number;
  orderChange: number;
  conversionRate: number;

  // Value Metrics
  averageOrderValue: number;
  aovChange: number;
  aovTarget: number;

  // Margin Metrics - null means not available
  grossMarginPercent: number | null;
  marginTarget: number;

  // Inventory Metrics - null means not available
  inventoryTurnover: number | null;
  turnoverTarget: number;

  // Customer Metrics
  customerAcquisition: number | null;
  customerAcquisitionChange: number | null;
  newCustomers: number | null;
  returningCustomers: number | null;
  topCustomers: Array<{ name: string; spend: number; orders: number }>;

  // Inventory Intelligence
  lowStockItems: number;
  deadStockValue: number | null;
  deadStockItems: number | null;
  fastMovingItems: number | null;

  // Prescription Metrics
  prescriptionRenewals: number | null;
}

// StoreRow matches StoreMetrics from MultiStorePerformanceTable (all numbers required).
// When real data lacks certain fields, we pass 0 so the table still renders.
interface StoreRow {
  storeId: string;
  storeName: string;
  revenue: number;
  orders: number;
  averageOrderValue: number;
  marginPercent: number;
  stockValue: number;
  staffCount: number;
  revenuePerSqft: number;
  trend: number;
}

// ============================================================================
// Helpers
// ============================================================================

function getTodayDate(): string {
  return new Date().toISOString().split('T')[0];
}

function buildRevenueTrend(summaryRevenue: number): number[] {
  // Produce a flat trend line at the actual revenue value — no randomness.
  // The line will be overridden with real time-series data if the analytics
  // endpoint returns it, but this serves as a stable fallback.
  return Array(14).fill(summaryRevenue);
}

function buildChartDataFromTrend(
  trendPoints: Array<{ label: string; value: number; value2?: number }>
) {
  return trendPoints;
}

function buildFlatChartData(baseValue: number, points = 14) {
  const data = [];
  for (let i = 0; i < points; i++) {
    const date = new Date();
    date.setDate(date.getDate() - (points - i - 1));
    data.push({
      label: date.toLocaleDateString('en-IN', { month: 'short', day: 'numeric' }),
      value: baseValue,
      value2: baseValue,
    });
  }
  return data;
}

// ============================================================================
// Main Enterprise Dashboard Component
// ============================================================================

export default function EnterpriseAnalyticsDashboard() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();

  // State
  const [timeRange, setTimeRange] = useState<'today' | 'week' | 'month' | 'quarter' | 'year'>('month');
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<EnterpriseMetrics | null>(null);
  const [chartToggle, setChartToggle] = useState<'daily' | 'weekly' | 'monthly'>('daily');
  const [stores, setStores] = useState<StoreRow[]>([]);
  const [chartData, setChartData] = useState<Array<{ label: string; value: number; value2?: number }>>([]);

  // Load data on mount and when filters change
  const loadDashboardData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      // Fetch all data in parallel; individual failures are caught per-call
      const [dashboardRes, salesRes, analyticsRes, revTrendsRes, storePerformanceRes, invIntelRes, customerInsightsRes] =
        await Promise.all([
          reportsApi.getDashboardStats(user?.activeStoreId || '').catch(() => null),
          reportsApi.getSalesSummary(user?.activeStoreId || '', getTodayDate(), getTodayDate()).catch(() => null),
          analyticsApi.getDashboardSummary(timeRange).catch(() => null),
          analyticsApi.getRevenueTrends(chartToggle, 14).catch(() => null),
          analyticsApi.getStorePerformance(timeRange).catch(() => null),
          analyticsApi.getInventoryIntelligence().catch(() => null),
          analyticsApi.getCustomerInsights(timeRange).catch(() => null),
        ]);

      // ---- Revenue ----
      const totalRevenue =
        analyticsRes?.total_revenue ??
        dashboardRes?.totalSales ??
        0;

      const revenueChange =
        analyticsRes?.revenue_change ??
        dashboardRes?.change ??
        0;

      // ---- Orders ----
      const totalOrders =
        analyticsRes?.total_orders ??
        salesRes?.summary?.total_orders ??
        0;

      const averageOrderValue =
        analyticsRes?.avg_order_value ??
        salesRes?.summary?.avg_order_value ??
        0;

      // ---- Margin ----
      // Only show if the analytics endpoint provides it
      const grossMarginPercent: number | null =
        analyticsRes?.gross_margin_percent ??
        analyticsRes?.grossMarginPercent ??
        null;

      // ---- Inventory Turnover ----
      const inventoryTurnover: number | null =
        invIntelRes?.inventory_turnover ??
        invIntelRes?.inventoryTurnover ??
        null;

      // ---- Customer data ----
      const newCustomers: number | null =
        customerInsightsRes?.new_customers ??
        customerInsightsRes?.newCustomers ??
        null;

      const returningCustomers: number | null =
        customerInsightsRes?.returning_customers ??
        customerInsightsRes?.returningCustomers ??
        null;

      const customerAcquisition: number | null =
        customerInsightsRes?.acquisition_rate ??
        customerInsightsRes?.customerAcquisition ??
        null;

      const customerAcquisitionChange: number | null =
        customerInsightsRes?.acquisition_change ??
        null;

      // Top customers — use API data if available, otherwise empty
      const rawTopCustomers =
        customerInsightsRes?.top_customers ??
        customerInsightsRes?.topCustomers ??
        [];

      const topCustomers: Array<{ name: string; spend: number; orders: number }> =
        Array.isArray(rawTopCustomers) && rawTopCustomers.length > 0
          ? rawTopCustomers.map((c: Record<string, unknown>) => ({
              name: (c.name ?? c.customer_name ?? 'Unknown') as string,
              spend: (c.spend ?? c.total_spend ?? c.total_revenue ?? 0) as number,
              orders: (c.orders ?? c.order_count ?? 0) as number,
            }))
          : [];

      // ---- Inventory Intelligence ----
      const deadStockValue: number | null =
        invIntelRes?.dead_stock_value ??
        invIntelRes?.deadStockValue ??
        null;

      const deadStockItems: number | null =
        invIntelRes?.dead_stock_items ??
        invIntelRes?.deadStockItems ??
        null;

      const fastMovingItems: number | null =
        invIntelRes?.fast_moving_items ??
        invIntelRes?.fastMovingItems ??
        null;

      // ---- Prescription Renewals ----
      const prescriptionRenewals: number | null =
        analyticsRes?.prescription_renewals ??
        analyticsRes?.prescriptionRenewals ??
        null;

      // ---- Revenue trend (sparkline) ----
      let revenueTrend: number[];
      if (revTrendsRes?.data && Array.isArray(revTrendsRes.data) && revTrendsRes.data.length > 0) {
        revenueTrend = revTrendsRes.data.map((p: Record<string, unknown>) =>
          typeof p.value === 'number' ? p.value : (typeof p.revenue === 'number' ? p.revenue : 0)
        );
      } else {
        revenueTrend = buildRevenueTrend(totalRevenue);
      }

      // ---- Chart data (line chart) ----
      if (revTrendsRes?.data && Array.isArray(revTrendsRes.data) && revTrendsRes.data.length > 0) {
        const pts = revTrendsRes.data.map((p: Record<string, unknown>) => ({
          label: typeof p.label === 'string' ? p.label : String(p.date ?? p.period ?? ''),
          value: typeof p.value === 'number' ? p.value : (typeof p.revenue === 'number' ? p.revenue : 0),
          value2: typeof p.value2 === 'number' ? p.value2 : (typeof p.yoy === 'number' ? p.yoy : undefined),
        }));
        setChartData(buildChartDataFromTrend(pts));
      } else {
        setChartData(buildFlatChartData(totalRevenue, 14));
      }

      const processedMetrics: EnterpriseMetrics = {
        totalRevenue,
        revenueChange,
        revenueYoY: analyticsRes?.revenue_yoy ?? revenueChange,
        revenueTrend,

        totalOrders,
        orderChange: analyticsRes?.order_change ?? 0,
        conversionRate: analyticsRes?.conversion_rate ?? 0,

        averageOrderValue,
        aovChange: analyticsRes?.aov_change ?? 0,
        aovTarget: analyticsRes?.aov_target ?? 15000,

        grossMarginPercent,
        marginTarget: analyticsRes?.margin_target ?? 42,

        inventoryTurnover,
        turnoverTarget: invIntelRes?.turnover_target ?? analyticsRes?.turnover_target ?? 10,

        customerAcquisition,
        customerAcquisitionChange,
        newCustomers,
        returningCustomers,
        topCustomers,

        lowStockItems: dashboardRes?.lowStockItems ?? invIntelRes?.low_stock_items ?? 0,
        deadStockValue,
        deadStockItems,
        fastMovingItems,

        prescriptionRenewals,
      };

      setMetrics(processedMetrics);

      // ---- Stores (multi-store table) ----
      if (storePerformanceRes?.stores && Array.isArray(storePerformanceRes.stores) && storePerformanceRes.stores.length > 0) {
        const storeRows: StoreRow[] = storePerformanceRes.stores.map((s: Record<string, unknown>) => ({
          storeId: (s.store_id ?? s.storeId ?? '') as string,
          storeName: (s.store_name ?? s.storeName ?? 'Unknown Store') as string,
          revenue: ((s.revenue ?? 0) as number),
          orders: ((s.orders ?? s.order_count ?? 0) as number),
          averageOrderValue: ((s.avg_order_value ?? s.averageOrderValue ?? 0) as number),
          marginPercent: ((s.margin_percent ?? s.marginPercent ?? 0) as number),
          stockValue: ((s.stock_value ?? s.stockValue ?? 0) as number),
          staffCount: ((s.staff_count ?? s.staffCount ?? 0) as number),
          revenuePerSqft: ((s.revenue_per_sqft ?? s.revenuePerSqft ?? 0) as number),
          trend: ((s.trend ?? s.revenue_change ?? 0) as number),
        }));
        setStores(storeRows);
      } else {
        // Fall back: try to fetch all stores and show only what we have metrics for
        const storesRes = await adminStoreApi.getStores().catch(() => null);
        if (storesRes && Array.isArray(storesRes) && storesRes.length > 0) {
          const currentStoreId = user?.activeStoreId || '';
          // Build a single row for the current store using the data we already have
          const currentStore = storesRes.find(
            (s: Record<string, unknown>) =>
              (s._id ?? s.id ?? s.store_id) === currentStoreId
          ) ?? storesRes[0];
          const singleRow: StoreRow = {
            storeId: currentStoreId,
            storeName: (currentStore?.name ?? currentStore?.store_name ?? 'Current Store') as string,
            revenue: totalRevenue,
            orders: totalOrders,
            averageOrderValue,
            marginPercent: grossMarginPercent ?? 0,
            stockValue: 0,
            staffCount: 0,
            revenuePerSqft: 0,
            trend: revenueChange,
          };
          setStores([singleRow]);
        } else {
          setStores([]);
        }
      }

      setError(null);
    } catch (err) {
      setError('Failed to load dashboard data');
    } finally {
      setIsLoading(false);
    }
  }, [timeRange, chartToggle, user?.activeStoreId]);

  useEffect(() => {
    loadDashboardData();
  }, [loadDashboardData]);

  const handleExportReport = () => {
    toast.info('Export feature coming soon');
  };

  const handleQuickAction = (action: string) => {
    switch (action) {
      case 'new-order':
        navigate('/pos');
        break;
      case 'new-customer':
        navigate('/customers?action=add');
        break;
      case 'stock-transfer':
        navigate('/inventory?tab=transfers');
        break;
      case 'report':
        navigate('/reports');
        break;
      case 'workshop':
        navigate('/workshop');
        break;
      default:
        break;
    }
  };

  if (isLoading && !metrics) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center">
          <div className="w-12 h-12 rounded-full border-4 border-blue-600 border-t-transparent animate-spin mx-auto mb-4" />
          <p className="text-gray-600">Loading enterprise dashboard...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900 flex items-center gap-2">
            <BarChart3 className="w-8 h-8 text-blue-600" />
            Enterprise Analytics Dashboard
          </h1>
          <p className="text-gray-600 mt-1">
            Real-time business intelligence{stores.length > 1 ? ` for ${stores.length} locations` : ''}
          </p>
        </div>

        <div className="flex items-center gap-3">
          {/* Time Range Selector */}
          <select
            value={timeRange}
            onChange={(e) => setTimeRange(e.target.value as typeof timeRange)}
            className="px-4 py-2 border border-gray-300 rounded-lg bg-white text-gray-900 text-sm font-medium hover:border-gray-400 transition-colors"
          >
            <option value="today">Today</option>
            <option value="week">This Week</option>
            <option value="month">This Month</option>
            <option value="quarter">This Quarter</option>
            <option value="year">This Year</option>
          </select>

          {/* Refresh Button */}
          <button
            onClick={loadDashboardData}
            disabled={isLoading}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors disabled:opacity-50"
            title="Refresh data"
          >
            <RefreshCw className={clsx('w-5 h-5 text-gray-600', isLoading && 'animate-spin')} />
          </button>

          {/* Export Button */}
          <button
            onClick={handleExportReport}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
          >
            <Download className="w-4 h-4" />
            Export
          </button>
        </div>
      </div>

      {/* Error Alert */}
      {error && (
        <div className="p-4 bg-red-50 border border-red-200 rounded-lg flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
          <div>
            <p className="font-medium text-red-900">{error}</p>
            <p className="text-sm text-red-700 mt-1">Try refreshing the page or check your connection</p>
          </div>
        </div>
      )}

      {/* SECTION 1: Executive KPI Cards */}
      {metrics && (
        <div>
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Key Performance Indicators</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {/* Total Revenue */}
            <EnterpriseKpiCard
              label="Total Revenue"
              value={formatChartValue(metrics.totalRevenue)}
              unit={timeRange}
              change={metrics.revenueChange}
              icon={DollarSign}
              sparklineData={metrics.revenueTrend}
              status={metrics.revenueChange > 0 ? 'positive' : 'negative'}
              loading={isLoading}
              onClick={() => navigate('/reports?tab=sales')}
            />

            {/* Total Orders */}
            <EnterpriseKpiCard
              label="Total Orders"
              value={metrics.totalOrders}
              subtext={
                metrics.conversionRate > 0
                  ? `Conversion: ${metrics.conversionRate.toFixed(1)}%`
                  : undefined
              }
              change={metrics.orderChange}
              icon={ShoppingCart}
              status={metrics.orderChange > 0 ? 'positive' : 'neutral'}
              loading={isLoading}
              onClick={() => navigate('/orders')}
            />

            {/* Average Order Value */}
            <EnterpriseKpiCard
              label="Average Order Value"
              value={formatChartValue(metrics.averageOrderValue)}
              target={metrics.aovTarget}
              change={metrics.aovChange}
              icon={TrendingUp}
              status={metrics.averageOrderValue >= metrics.aovTarget ? 'success' : 'warning'}
              loading={isLoading}
              onClick={() => navigate('/reports?tab=sales')}
            />

            {/* Gross Margin */}
            <EnterpriseKpiCard
              label="Gross Margin %"
              value={metrics.grossMarginPercent !== null ? metrics.grossMarginPercent.toFixed(1) : '--'}
              unit="%"
              target={metrics.grossMarginPercent !== null ? metrics.marginTarget : undefined}
              icon={Percent}
              status={
                metrics.grossMarginPercent === null
                  ? 'neutral'
                  : metrics.grossMarginPercent >= metrics.marginTarget
                  ? 'success'
                  : 'warning'
              }
              loading={isLoading}
            />
          </div>

          {/* Second Row of KPIs */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mt-4">
            {/* Inventory Turnover */}
            <EnterpriseKpiCard
              label="Inventory Turnover Ratio"
              value={metrics.inventoryTurnover !== null ? metrics.inventoryTurnover.toFixed(1) : '--'}
              unit="x"
              target={metrics.inventoryTurnover !== null ? metrics.turnoverTarget : undefined}
              icon={Package}
              status={
                metrics.inventoryTurnover === null
                  ? 'neutral'
                  : metrics.inventoryTurnover >= metrics.turnoverTarget
                  ? 'success'
                  : 'warning'
              }
              loading={isLoading}
              onClick={() => navigate('/inventory')}
            />

            {/* Customer Acquisition */}
            <EnterpriseKpiCard
              label="Customer Acquisition Rate"
              value={metrics.customerAcquisition !== null ? metrics.customerAcquisition : '--'}
              subtext={
                metrics.newCustomers !== null
                  ? `${metrics.newCustomers} new customers`
                  : undefined
              }
              change={metrics.customerAcquisitionChange ?? undefined}
              icon={Users}
              status={metrics.customerAcquisition !== null ? 'positive' : 'neutral'}
              loading={isLoading}
              onClick={() => navigate('/customers')}
            />

            {/* Low Stock Items */}
            <EnterpriseKpiCard
              label="Low Stock Alerts"
              value={metrics.lowStockItems}
              icon={AlertTriangle}
              status={metrics.lowStockItems > 0 ? 'warning' : 'success'}
              loading={isLoading}
              onClick={() => navigate('/inventory?tab=low-stock')}
            />

            {/* Prescription Renewals */}
            <EnterpriseKpiCard
              label="Prescription Renewals"
              value={metrics.prescriptionRenewals !== null ? metrics.prescriptionRenewals : '--'}
              subtext={metrics.prescriptionRenewals !== null ? 'Pending eye tests' : 'No data available'}
              icon={Eye}
              status="neutral"
              loading={isLoading}
              onClick={() => navigate('/clinical')}
            />
          </div>
        </div>
      )}

      {/* SECTION 2: Revenue Analytics */}
      {metrics && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Revenue Trend */}
          <div className="lg:col-span-2 bg-white rounded-xl border border-gray-200 shadow-sm p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-gray-900">Revenue Trend</h3>
              <div className="flex gap-2">
                {(['daily', 'weekly', 'monthly'] as const).map((type) => (
                  <button
                    key={type}
                    onClick={() => setChartToggle(type)}
                    className={clsx(
                      'px-3 py-1 rounded-lg text-sm font-medium transition-colors',
                      chartToggle === type
                        ? 'bg-blue-600 text-white'
                        : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                    )}
                  >
                    {type.charAt(0).toUpperCase() + type.slice(1)}
                  </button>
                ))}
              </div>
            </div>
            <LineChart
              data={chartData}
              color="#3b82f6"
              color2="#10b981"
              showLegend
              loading={isLoading}
            />
          </div>

          {/* Revenue by Category */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Revenue by Category</h3>
            <DonutChart
              data={[
                { label: 'Frames', value: 35, color: '#3b82f6' },
                { label: 'Lenses', value: 28, color: '#10b981' },
                { label: 'Contact Lenses', value: 18, color: '#f59e0b' },
                { label: 'Sunglasses', value: 12, color: '#8b5cf6' },
                { label: 'Accessories', value: 5, color: '#ec4899' },
                { label: 'Services', value: 2, color: '#6366f1' },
              ]}
              showLegend
              loading={isLoading}
            />
          </div>
        </div>
      )}

      {/* SECTION 3: Multi-Store Performance */}
      {metrics && stores.length > 0 && (
        <MultiStorePerformanceTable
          stores={stores}
          onStoreClick={(storeId) => navigate(`/stores/${storeId}/analytics`)}
          loading={isLoading}
        />
      )}

      {/* SECTION 4: Inventory Intelligence */}
      {metrics && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Low Stock Alerts */}
          <div className="bg-white rounded-xl border border-amber-200 bg-amber-50 shadow-sm p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-amber-900">Low Stock Alerts</h3>
              <AlertTriangle className="w-5 h-5 text-amber-600" />
            </div>
            <p className="text-3xl font-bold text-amber-600 mb-2">{metrics.lowStockItems}</p>
            <p className="text-sm text-amber-700">Items below reorder point</p>
            <button
              onClick={() => navigate('/inventory?tab=low-stock')}
              className="mt-4 w-full px-4 py-2 bg-amber-600 text-white rounded-lg hover:bg-amber-700 transition-colors text-sm font-medium"
            >
              View Low Stock
            </button>
          </div>

          {/* Dead Stock */}
          <div className="bg-white rounded-xl border border-red-200 bg-red-50 shadow-sm p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-red-900">Dead Stock</h3>
              <Package className="w-5 h-5 text-red-600" />
            </div>
            {metrics.deadStockValue !== null ? (
              <>
                <p className="text-3xl font-bold text-red-600 mb-2">
                  {formatChartValue(metrics.deadStockValue)}
                </p>
                <p className="text-sm text-red-700">
                  {metrics.deadStockItems !== null ? `${metrics.deadStockItems} items` : 'Items'} not sold in 90+ days
                </p>
              </>
            ) : (
              <>
                <p className="text-3xl font-bold text-red-400 mb-2">N/A</p>
                <p className="text-sm text-red-600">No data available</p>
              </>
            )}
            <button
              onClick={() => navigate('/inventory?tab=dead-stock')}
              className="mt-4 w-full px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors text-sm font-medium"
            >
              Review Dead Stock
            </button>
          </div>

          {/* Fast Moving Items */}
          <div className="bg-white rounded-xl border border-green-200 bg-green-50 shadow-sm p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-green-900">Fast Moving Items</h3>
              <TrendingUp className="w-5 h-5 text-green-600" />
            </div>
            {metrics.fastMovingItems !== null ? (
              <>
                <p className="text-3xl font-bold text-green-600 mb-2">{metrics.fastMovingItems}</p>
                <p className="text-sm text-green-700">Top performers this period</p>
              </>
            ) : (
              <>
                <p className="text-3xl font-bold text-green-400 mb-2">N/A</p>
                <p className="text-sm text-green-600">No data available</p>
              </>
            )}
            <button
              onClick={() => navigate('/reports?tab=products')}
              className="mt-4 w-full px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors text-sm font-medium"
            >
              Analyze Products
            </button>
          </div>
        </div>
      )}

      {/* SECTION 5: Customer Insights */}
      {metrics && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Customer Composition */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Customer Composition</h3>
            {metrics.newCustomers !== null && metrics.returningCustomers !== null ? (
              <div className="space-y-4">
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-gray-700">New Customers</span>
                    <span className="text-2xl font-bold text-blue-600">{metrics.newCustomers}</span>
                  </div>
                  <div className="w-full h-2 bg-gray-200 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-blue-500 transition-all"
                      style={{
                        width: `${
                          metrics.newCustomers + metrics.returningCustomers > 0
                            ? (metrics.newCustomers / (metrics.newCustomers + metrics.returningCustomers)) * 100
                            : 0
                        }%`,
                      }}
                    />
                  </div>
                </div>
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-gray-700">Returning Customers</span>
                    <span className="text-2xl font-bold text-green-600">{metrics.returningCustomers}</span>
                  </div>
                  <div className="w-full h-2 bg-gray-200 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-green-500 transition-all"
                      style={{
                        width: `${
                          metrics.newCustomers + metrics.returningCustomers > 0
                            ? (metrics.returningCustomers / (metrics.newCustomers + metrics.returningCustomers)) * 100
                            : 0
                        }%`,
                      }}
                    />
                  </div>
                </div>
                <p className="text-xs text-gray-600 mt-4">
                  Retention Rate:{' '}
                  {metrics.newCustomers + metrics.returningCustomers > 0
                    ? (
                        (metrics.returningCustomers /
                          (metrics.newCustomers + metrics.returningCustomers)) *
                        100
                      ).toFixed(1)
                    : '0.0'}
                  %
                </p>
              </div>
            ) : (
              <p className="text-gray-500 text-sm">No customer composition data available for this period.</p>
            )}
          </div>

          {/* Top Customers */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Top 10 Customers by Spend</h3>
            {metrics.topCustomers.length > 0 ? (
              <div className="space-y-3">
                {metrics.topCustomers.map((customer, idx) => (
                  <div
                    key={idx}
                    className="flex items-center justify-between p-2 hover:bg-gray-50 rounded-lg transition-colors"
                  >
                    <div className="flex items-center gap-3 flex-1">
                      <div className="w-8 h-8 rounded-full bg-gradient-to-br from-blue-400 to-purple-500 flex items-center justify-center text-white text-sm font-bold">
                        {idx + 1}
                      </div>
                      <div>
                        <p className="font-medium text-gray-900">{customer.name}</p>
                        <p className="text-xs text-gray-500">{customer.orders} orders</p>
                      </div>
                    </div>
                    <p className="font-semibold text-gray-900">{formatChartValue(customer.spend)}</p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-gray-500 text-sm">No customer data available for this period.</p>
            )}
          </div>
        </div>
      )}

      {/* SECTION 6: Quick Actions Panel */}
      <div className="bg-gradient-to-r from-blue-50 to-purple-50 rounded-xl border border-blue-200 p-6">
        <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
          <Zap className="w-5 h-5 text-blue-600" />
          Quick Actions
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
          {[
            { icon: Plus, label: 'Create New Order', action: 'new-order', color: 'bg-blue-600 hover:bg-blue-700' },
            { icon: Users, label: 'Add New Customer', action: 'new-customer', color: 'bg-green-600 hover:bg-green-700' },
            { icon: ArrowRight, label: 'Stock Transfer', action: 'stock-transfer', color: 'bg-purple-600 hover:bg-purple-700' },
            { icon: BarChart3, label: 'Generate Report', action: 'report', color: 'bg-orange-600 hover:bg-orange-700' },
            { icon: Zap, label: 'Workshop Jobs', action: 'workshop', color: 'bg-red-600 hover:bg-red-700' },
          ].map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.action}
                onClick={() => handleQuickAction(item.action)}
                className={clsx(
                  'flex flex-col items-center gap-2 px-4 py-3 rounded-lg text-white font-medium transition-colors',
                  item.color
                )}
              >
                <Icon className="w-5 h-5" />
                <span className="text-sm text-center">{item.label}</span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
