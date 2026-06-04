// ============================================================================
// IMS 2.0 - Executive Dashboard
// ============================================================================
// Complete business overview for decision makers

import { useState, useEffect } from 'react';
import {
  TrendingUp,
  TrendingDown,
  DollarSign,
  Package,
  ShoppingCart,
  AlertTriangle,
  BarChart3,
  PieChart,
  Activity,
  Store,
  Loader2,
  ArrowUpRight,
  ArrowDownRight,
  RefreshCw,
} from 'lucide-react';

import { useAuth } from '../../context/AuthContext';
import { reportsApi, analyticsApi } from '../../services/api';
// financeApi isn't re-exported from the api barrel — import it directly (the
// barrel re-export resolves to undefined for this module; see prior sessions).
import { financeApi } from '../../services/api/finance';

interface StorePerformance {
  storeId: string;
  storeName: string;
  revenue: number;
  orders: number;
  // These derived metrics are null when the backend can't source them
  // (margin needs per-item COGS; sqft + headcount aren't stored). They render
  // as a dash rather than a fabricated number (SYSTEM_INTENT: fail loudly).
  profitMargin: number | null;
  inventoryValue: number;
  salesPerSqFt: number | null;
  staffCount: number | null;
  trend: 'up' | 'down' | 'stable';
  trendPercentage: number;
}

interface BusinessMetrics {
  totalRevenue: number;
  totalProfit: number;
  // True when net profit was sourced from the analytics summary; false for
  // viewers without analytics access (the card then shows a dash, not 0).
  profitKnown: boolean;
  totalOrders: number;
  totalCustomers: number;
  averageOrderValue: number;
  inventoryTurnover: number;
  deadStockPercentage: number;
  cashFlow: number;
  revenueGrowth: number;
  profitGrowth: number;
}

interface CategoryPerformance {
  category: string;
  revenue: number;
  profit: number;
  margin: number;
  turnover: number;
  trend: 'up' | 'down' | 'stable';
}

interface TopProduct {
  id: string;
  name: string;
  category: string;
  revenue: number;
  quantity: number;
  margin: number;
}

function getDateRange(range: string): { startDate: string; endDate: string } {
  const now = new Date();
  const endDate = now.toISOString().split('T')[0];
  const start = new Date(now);

  switch (range) {
    case 'today':
      break;
    case 'week':
      start.setDate(start.getDate() - 7);
      break;
    case 'quarter':
      start.setMonth(start.getMonth() - 3);
      break;
    case 'year':
      start.setFullYear(start.getFullYear() - 1);
      break;
    case 'month':
    default:
      start.setMonth(start.getMonth() - 1);
      break;
  }

  return { startDate: start.toISOString().split('T')[0], endDate };
}

export function ExecutiveDashboard() {
  const { user } = useAuth();

  const [timeRange, setTimeRange] = useState<'today' | 'week' | 'month' | 'quarter' | 'year'>('month');
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<BusinessMetrics | null>(null);
  const [stores, setStores] = useState<StorePerformance[]>([]);
  const [categories, setCategories] = useState<CategoryPerformance[]>([]);
  const [topProducts, setTopProducts] = useState<TopProduct[]>([]);

  useEffect(() => {
    loadDashboardData();
    // user?.activeStoreId in deps so the dashboard re-fetches when the
    // topbar's store-switcher changes the active store.
  }, [timeRange, user?.activeStoreId]);

  const loadDashboardData = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const storeId = user?.activeStoreId || '';
      const { startDate, endDate } = getDateRange(timeRange);

      // Real backend sources, all fail-soft & independent:
      //  - reports/* : revenue, inventory, sales summary (open to all roles)
      //  - analytics/dashboard-summary : profit, inventory turnover, top-5,
      //    cash register (admin-gated -> null for non-admins, handled honestly)
      //  - analytics/store-performance : per-store comparison (admin-gated)
      //  - finance/cash-flow : real net cash flow for the cash-flow card
      const [dashboardRes, inventoryRes, salesRes, summaryRes, storePerfRes, cashFlowRes] =
        await Promise.all([
          reportsApi.getDashboardStats(storeId).catch(() => null),
          reportsApi.getInventoryReport(storeId).catch(() => null),
          reportsApi.getSalesSummary(storeId, startDate, endDate).catch(() => null),
          analyticsApi.getDashboardSummary(timeRange, storeId).catch(() => null),
          analyticsApi.getStorePerformance(timeRange).catch(() => null),
          financeApi.getCashFlow({ period: 'month', store_id: storeId || undefined }).catch(() => null),
        ]);

      // Business Metrics
      const salesSummary = salesRes?.summary;
      const totalItems = inventoryRes?.totalItems ?? 0;
      const outOfStock = inventoryRes?.outOfStock ?? 0;

      // Profit + inventory turnover come from the analytics summary (real
      // figures); cash flow prefers the dedicated finance endpoint's
      // net_cash_flow, falling back to the summary's closing balance.
      const netProfit = summaryRes?.margins?.net_profit ?? null;
      const invTurnover = summaryRes?.inventory?.turnover_ratio ?? 0;
      const netCashFlow =
        cashFlowRes?.net_cash_flow ?? summaryRes?.cash_register?.closing_balance ?? 0;

      setMetrics({
        totalRevenue:
          salesSummary?.total_sales ??
          summaryRes?.revenue?.total ??
          dashboardRes?.totalSales ??
          0,
        totalProfit: netProfit ?? 0,
        profitKnown: netProfit !== null,
        totalOrders: salesSummary?.total_orders ?? summaryRes?.revenue?.total_orders ?? 0,
        totalCustomers: summaryRes?.customers?.footfall ?? 0,
        averageOrderValue:
          salesSummary?.avg_order_value ?? summaryRes?.revenue?.avg_transaction_value ?? 0,
        inventoryTurnover: Number(invTurnover) || 0,
        deadStockPercentage: totalItems > 0 ? (outOfStock / totalItems) * 100 : 0,
        cashFlow: Number(netCashFlow) || 0,
        revenueGrowth: dashboardRes?.change ?? summaryRes?.revenue?.change_percent ?? 0,
        profitGrowth: 0,
      });

      // Store Performance — real per-store comparison from analytics/store-performance.
      // margin_percent / staff_count / revenue_per_sqft are null from the API
      // (not fabricated); kept null here so the UI shows a dash.
      const perfStores: any[] = storePerfRes?.stores ?? [];
      setStores(
        perfStores.map((s: any) => {
          const trendPct = Number(s.revenue_trend ?? 0);
          return {
            storeId: s.store_id ?? '',
            storeName: s.store_name ?? s.store_id ?? 'Store',
            revenue: Number(s.revenue ?? 0),
            orders: Number(s.orders ?? 0),
            profitMargin: s.margin_percent ?? null,
            inventoryValue: Number(s.stock_value ?? 0),
            salesPerSqFt: s.revenue_per_sqft ?? null,
            staffCount: s.staff_count ?? null,
            trend: trendPct > 0.5 ? 'up' : trendPct < -0.5 ? 'down' : 'stable',
            trendPercentage: Math.round(trendPct * 10) / 10,
          };
        })
      );

      // Category Performance from inventory categories
      const inventoryCategories: any[] = inventoryRes?.categories ?? [];
      setCategories(
        inventoryCategories.map((cat: any) => ({
          category: cat.name ?? 'Other',
          revenue: cat.value ?? 0,
          profit: 0,
          margin: 0,
          turnover: 0,
          trend: 'stable' as const,
        }))
      );

      // Top 5 Products — real revenue-ranked list from the analytics summary.
      const tp: any[] = summaryRes?.top_products ?? [];
      setTopProducts(
        tp.map((p: any) => ({
          id: p.product_id ?? p.sku ?? p.name ?? '',
          name: p.name ?? 'Unknown',
          category: p.category ?? '',
          revenue: Number(p.revenue ?? 0),
          quantity: Number(p.units ?? p.quantity ?? 0),
          margin: 0,
        }))
      );

    } catch {
      setError('Failed to load dashboard data. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  const formatCurrency = (amount: number) => {
    if (amount >= 10000000) {
      return `₹${(amount / 10000000).toFixed(2)} Cr`;
    } else if (amount >= 100000) {
      return `₹${(amount / 100000).toFixed(2)} L`;
    }
    return `₹${amount.toLocaleString('en-IN')}`;
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 animate-spin text-purple-600" />
      </div>
    );
  }

  if (error && !metrics) {
    return (
      <div className="flex flex-col items-center justify-center h-96 text-center">
        <AlertTriangle className="w-12 h-12 text-red-600 mb-4" />
        <p className="text-gray-700 mb-4">{error}</p>
        <button onClick={loadDashboardData} className="btn-primary">
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <BarChart3 className="w-7 h-7 text-purple-600" />
            Executive Dashboard
          </h1>
          <p className="text-gray-500 mt-1">Complete business overview and analytics</p>
        </div>
        <div className="flex items-center gap-3">
          {/* Time Range Selector */}
          <select
            value={timeRange}
            onChange={(e) => setTimeRange(e.target.value as typeof timeRange)}
            className="input-field w-auto"
            disabled={isLoading}
          >
            <option value="today">Today</option>
            <option value="week">This Week</option>
            <option value="month">This Month</option>
            <option value="quarter">This Quarter</option>
            <option value="year">This Year</option>
          </select>
          <button
            onClick={loadDashboardData}
            disabled={isLoading}
            className="btn-outline flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
            {isLoading ? 'Loading...' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Key Metrics */}
      {metrics && (
        <div className="grid grid-cols-1 tablet:grid-cols-2 desktop:grid-cols-5 gap-4">
          {/* Total Revenue */}
          <div className="card">
            <div className="flex items-center justify-between">
              <div className="flex-1">
                <p className="text-sm text-gray-600 mb-1">Total Revenue</p>
                <p className="text-2xl font-bold text-gray-900">{formatCurrency(metrics.totalRevenue)}</p>
                <div className="flex items-center gap-1 mt-2">
                  {metrics.revenueGrowth >= 0 ? (
                    <ArrowUpRight className="w-4 h-4 text-green-600" />
                  ) : (
                    <ArrowDownRight className="w-4 h-4 text-red-600" />
                  )}
                  <span className={`text-sm font-medium ${metrics.revenueGrowth >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                    {Math.abs(metrics.revenueGrowth)}%
                  </span>
                  <span className="text-xs text-gray-500">vs last period</span>
                </div>
              </div>
              <div className="w-12 h-12 bg-green-100 rounded-lg flex items-center justify-center">
                <DollarSign className="w-6 h-6 text-green-600" />
              </div>
            </div>
          </div>

          {/* Average Order Value */}
          <div className="card">
            <div className="flex items-center justify-between">
              <div className="flex-1">
                <p className="text-sm text-gray-600 mb-1">Avg. Order Value</p>
                <p className="text-2xl font-bold text-gray-900">{formatCurrency(metrics.averageOrderValue)}</p>
                <div className="flex items-center gap-1 mt-2">
                  <span className="text-xs text-gray-500">{metrics.totalOrders} orders this period</span>
                </div>
              </div>
              <div className="w-12 h-12 bg-purple-100 rounded-lg flex items-center justify-center">
                <TrendingUp className="w-6 h-6 text-purple-600" />
              </div>
            </div>
          </div>

          {/* Net Profit — real net_profit from the analytics summary. Shows a
              dash (not a fabricated 0) when the viewer can't access analytics. */}
          <div className="card">
            <div className="flex items-center justify-between">
              <div className="flex-1">
                <p className="text-sm text-gray-600 mb-1">Net Profit</p>
                <p className={`text-2xl font-bold ${
                  !metrics.profitKnown ? 'text-gray-400' : metrics.totalProfit < 0 ? 'text-red-700' : 'text-gray-900'
                }`}>
                  {!metrics.profitKnown ? '—' : formatCurrency(metrics.totalProfit)}
                </p>
                <div className="flex items-center gap-1 mt-2">
                  <span className="text-xs text-gray-500">
                    {!metrics.profitKnown
                      ? 'Requires analytics access'
                      : metrics.totalRevenue > 0
                        ? `${((metrics.totalProfit / metrics.totalRevenue) * 100).toFixed(1)}% net margin`
                        : 'This period'}
                  </span>
                </div>
              </div>
              <div className="w-12 h-12 bg-blue-100 rounded-lg flex items-center justify-center">
                <BarChart3 className="w-6 h-6 text-blue-600" />
              </div>
            </div>
          </div>

          {/* Dead Stock Alert */}
          <div className={`card border-2 ${metrics.deadStockPercentage > 10 ? 'border-orange-200 bg-orange-50' : 'border-green-200 bg-green-50'}`}>
            <div className="flex items-center justify-between">
              <div className="flex-1">
                <p className={`text-sm mb-1 ${metrics.deadStockPercentage > 10 ? 'text-orange-800' : 'text-green-800'}`}>Out of Stock</p>
                <p className={`text-2xl font-bold ${metrics.deadStockPercentage > 10 ? 'text-orange-900' : 'text-green-900'}`}>{metrics.deadStockPercentage.toFixed(1)}%</p>
                <div className="flex items-center gap-1 mt-2">
                  {metrics.deadStockPercentage > 10 ? (
                    <>
                      <AlertTriangle className="w-4 h-4 text-orange-600" />
                      <span className="text-xs text-orange-700">High - needs attention</span>
                    </>
                  ) : (
                    <span className="text-xs text-green-700">Within acceptable range</span>
                  )}
                </div>
              </div>
              <div className={`w-12 h-12 rounded-lg flex items-center justify-center ${metrics.deadStockPercentage > 10 ? 'bg-orange-200' : 'bg-green-200'}`}>
                <Package className={`w-6 h-6 ${metrics.deadStockPercentage > 10 ? 'text-orange-700' : 'text-green-700'}`} />
              </div>
            </div>
          </div>

          {/* Cash Flow */}
          <div className={`card border-2 ${metrics.cashFlow < 0 ? 'border-red-200 bg-red-50' : metrics.cashFlow > 0 ? 'border-green-200 bg-green-50' : 'border-gray-200 bg-gray-50'}`}>
            <div className="flex items-center justify-between">
              <div className="flex-1">
                <p className={`text-sm mb-1 ${metrics.cashFlow < 0 ? 'text-red-800' : metrics.cashFlow > 0 ? 'text-green-800' : 'text-gray-600'}`}>Cash Flow</p>
                <p className={`text-2xl font-bold ${metrics.cashFlow < 0 ? 'text-red-900' : metrics.cashFlow > 0 ? 'text-green-900' : 'text-gray-900'}`}>
                  {metrics.cashFlow === 0 ? 'N/A' : formatCurrency(Math.abs(metrics.cashFlow))}
                </p>
                <div className="flex items-center gap-1 mt-2">
                  {metrics.cashFlow < 0 ? (
                    <>
                      <TrendingDown className="w-4 h-4 text-red-600" />
                      <span className="text-xs text-red-700">Negative - monitor closely</span>
                    </>
                  ) : metrics.cashFlow > 0 ? (
                    <span className="text-xs text-green-700">Positive cash flow</span>
                  ) : (
                    <span className="text-xs text-gray-500">Cash flow data not available</span>
                  )}
                </div>
              </div>
              <div className={`w-12 h-12 rounded-lg flex items-center justify-center ${metrics.cashFlow < 0 ? 'bg-red-200' : metrics.cashFlow > 0 ? 'bg-green-200' : 'bg-gray-200'}`}>
                <Activity className={`w-6 h-6 ${metrics.cashFlow < 0 ? 'text-red-700' : metrics.cashFlow > 0 ? 'text-green-700' : 'text-gray-500'}`} />
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Store Performance Comparison */}
      {stores.length > 0 ? (
      <div className="card">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
            <Store className="w-5 h-5 text-purple-600" />
            Store Performance Comparison
          </h2>
          <span className="text-sm text-gray-500">Ranked by revenue</span>
        </div>

        <div className="space-y-4">
          {stores.map((store, index) => (
            <div
              key={store.storeId}
              className={`p-4 rounded-lg border-2 ${
                index >= 3 ? 'border-red-200 bg-red-50' : 'border-green-200 bg-green-50'
              }`}
            >
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-3">
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center font-bold ${
                    index >= 3 ? 'bg-red-200 text-red-800' : 'bg-green-200 text-green-800'
                  }`}>
                    {index + 1}
                  </div>
                  <div>
                    <h3 className="font-semibold text-gray-900">{store.storeName}</h3>
                    <p className="text-sm text-gray-600">
                      {store.orders} orders this period
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {store.trend === 'up' && <TrendingUp className="w-5 h-5 text-green-600" />}
                  {store.trend === 'down' && <TrendingDown className="w-5 h-5 text-red-600" />}
                  <span className={`text-sm font-medium ${
                    store.trend === 'up' ? 'text-green-600' : store.trend === 'down' ? 'text-red-600' : 'text-gray-600'
                  }`}>
                    {store.trend === 'up' ? '+' : store.trend === 'down' ? '' : ''}{store.trendPercentage}%
                  </span>
                </div>
              </div>

              <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
                <div>
                  <p className="text-xs text-gray-600">Revenue</p>
                  <p className="text-sm font-semibold text-gray-900">{formatCurrency(store.revenue)}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-600">Avg Order</p>
                  <p className="text-sm font-semibold text-gray-900">
                    {store.orders > 0 ? formatCurrency(Math.round(store.revenue / store.orders)) : '—'}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-gray-600">Profit Margin</p>
                  <p className="text-sm font-semibold text-gray-900">
                    {store.profitMargin === null ? '—' : `${store.profitMargin}%`}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-gray-600">Stock Value</p>
                  <p className="text-sm font-semibold text-gray-900">
                    {store.inventoryValue > 0 ? formatCurrency(store.inventoryValue) : '—'}
                  </p>
                </div>
              </div>

              {/* Problem Indicators for underperforming stores. Only fire on
                  signals we actually have (declining trend / low margin when
                  margin is known) — never on the null/dashed metrics. */}
              {index >= 3 && (store.trend === 'down' || (store.profitMargin !== null && store.profitMargin < 15)) && (
                <div className="mt-3 p-3 bg-white rounded border border-red-200">
                  <p className="text-xs font-medium text-red-800 mb-2">Performance Issues:</p>
                  <div className="flex flex-wrap gap-2">
                    {store.profitMargin !== null && store.profitMargin < 15 && (
                      <span className="px-2 py-1 bg-red-100 text-red-800 text-xs rounded">Low Margin</span>
                    )}
                    {store.trend === 'down' && (
                      <span className="px-2 py-1 bg-red-100 text-red-800 text-xs rounded">Declining Trend</span>
                    )}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
      ) : null}

      <div className="grid grid-cols-1 desktop:grid-cols-2 gap-6">
        {/* Category Performance */}
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 flex items-center gap-2 mb-4">
            <PieChart className="w-5 h-5 text-purple-600" />
            Category Performance
          </h2>
          <div className="space-y-3">
            {categories.length === 0 ? (
              <p className="text-center text-gray-500 py-6">No category data available</p>
            ) : categories.map((cat) => (
              <div key={cat.category} className="p-3 bg-gray-50 rounded-lg">
                <div className="flex items-center justify-between mb-2">
                  <span className="font-medium text-gray-900">{cat.category}</span>
                  <div className="flex items-center gap-2">
                    {cat.trend === 'up' && <TrendingUp className="w-4 h-4 text-green-600" />}
                    {cat.trend === 'down' && <TrendingDown className="w-4 h-4 text-red-600" />}
                  </div>
                </div>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div>
                    <p className="text-gray-600">Revenue</p>
                    <p className="font-semibold text-gray-900">{formatCurrency(cat.revenue)}</p>
                  </div>
                  <div>
                    <p className="text-gray-600">Margin</p>
                    <p className="font-semibold text-gray-900">{cat.margin}%</p>
                  </div>
                  <div>
                    <p className="text-gray-600">Turnover</p>
                    <p className="font-semibold text-gray-900">{cat.turnover}x</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Top Selling Products */}
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 flex items-center gap-2 mb-4">
            <ShoppingCart className="w-5 h-5 text-purple-600" />
            Top 5 Products
          </h2>
          <div className="space-y-3">
            {topProducts.length === 0 ? (
              <p className="text-center text-gray-500 py-6">No product data available</p>
            ) : topProducts.map((product, index) => (
              <div key={product.id} className="flex items-center gap-3 p-3 bg-gray-50 rounded-lg">
                <div className="w-6 h-6 rounded-full bg-purple-100 flex items-center justify-center text-xs font-bold text-purple-600">
                  {index + 1}
                </div>
                <div className="flex-1">
                  <p className="font-medium text-gray-900">{product.name}</p>
                  <p className="text-xs text-gray-600">{product.category}</p>
                </div>
                <div className="text-right">
                  <p className="text-sm font-semibold text-gray-900">{formatCurrency(product.revenue)}</p>
                  <p className="text-xs text-gray-600">{product.quantity} units sold</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Key Insights & Recommendations */}
      {metrics && (
      <div className="card border-2 border-purple-200 bg-purple-50">
        <h2 className="text-lg font-semibold text-purple-900 flex items-center gap-2 mb-4">
          <Activity className="w-5 h-5 text-purple-600" />
          Key Insights & Recommendations
        </h2>
        <div className="grid grid-cols-1 desktop:grid-cols-2 gap-4">
          {metrics.deadStockPercentage > 5 && (
          <div className="p-4 bg-white rounded-lg border border-purple-200">
            <div className="flex items-start gap-3">
              <AlertTriangle className="w-5 h-5 text-orange-600 flex-shrink-0 mt-1" />
              <div>
                <p className="font-medium text-gray-900 mb-1">Dead Stock Alert</p>
                <p className="text-sm text-gray-700">
                  {metrics.deadStockPercentage.toFixed(1)}% of inventory is out of stock or slow-moving.
                  Consider running discount campaigns or transferring stock between stores to free up capital.
                </p>
              </div>
            </div>
          </div>
          )}
          {metrics.revenueGrowth < 0 && (
          <div className="p-4 bg-white rounded-lg border border-purple-200">
            <div className="flex items-start gap-3">
              <Store className="w-5 h-5 text-red-600 flex-shrink-0 mt-1" />
              <div>
                <p className="font-medium text-gray-900 mb-1">Revenue Decline</p>
                <p className="text-sm text-gray-700">
                  Revenue has decreased by {Math.abs(metrics.revenueGrowth).toFixed(1)}% compared to the previous period.
                  Review store performance, staff productivity, and local market conditions.
                </p>
              </div>
            </div>
          </div>
          )}
          {metrics.averageOrderValue > 0 && (
          <div className="p-4 bg-white rounded-lg border border-purple-200">
            <div className="flex items-start gap-3">
              <TrendingUp className="w-5 h-5 text-green-600 flex-shrink-0 mt-1" />
              <div>
                <p className="font-medium text-gray-900 mb-1">Order Value Insight</p>
                <p className="text-sm text-gray-700">
                  Average order value is {formatCurrency(metrics.averageOrderValue)}.
                  Focus on upselling premium lenses and add-ons to increase per-transaction revenue.
                </p>
              </div>
            </div>
          </div>
          )}
          <div className="p-4 bg-white rounded-lg border border-purple-200">
            <div className="flex items-start gap-3">
              <DollarSign className="w-5 h-5 text-blue-600 flex-shrink-0 mt-1" />
              <div>
                <p className="font-medium text-gray-900 mb-1">Business Health</p>
                <p className="text-sm text-gray-700">
                  Total revenue: {formatCurrency(metrics.totalRevenue)} with {metrics.totalOrders} orders.
                  {metrics.revenueGrowth >= 0 ? ` Growth trend: +${metrics.revenueGrowth.toFixed(1)}%.` : ''} Review supplier payment terms and optimize collection cycles for better cash flow.
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
      )}
    </div>
  );
}
