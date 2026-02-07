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
import { reportsApi } from '../../services/api';

interface StorePerformance {
  storeId: string;
  storeName: string;
  revenue: number;
  orders: number;
  profit: number;
  profitMargin: number;
  inventoryValue: number;
  deadStockValue: number;
  salesPerSqFt: number;
  staffCount: number;
  trend: 'up' | 'down' | 'stable';
  trendPercentage: number;
}

interface BusinessMetrics {
  totalRevenue: number;
  totalProfit: number;
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
  let start = new Date(now);

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
  }, [timeRange]);

  const loadDashboardData = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const storeId = user?.activeStoreId || '';
      const { startDate, endDate } = getDateRange(timeRange);

      const [dashboardRes, inventoryRes, salesRes] = await Promise.all([
        reportsApi.getDashboardStats(storeId).catch(() => null),
        reportsApi.getInventoryReport(storeId).catch(() => null),
        reportsApi.getSalesSummary(storeId, startDate, endDate).catch(() => null),
      ]);

      // Business Metrics
      const salesSummary = salesRes?.summary;
      const totalItems = inventoryRes?.totalItems ?? 0;
      const outOfStock = inventoryRes?.outOfStock ?? 0;

      setMetrics({
        totalRevenue: salesSummary?.total_sales ?? dashboardRes?.totalSales ?? 0,
        totalProfit: 0,
        totalOrders: salesSummary?.total_orders ?? 0,
        totalCustomers: 0,
        averageOrderValue: salesSummary?.avg_order_value ?? 0,
        inventoryTurnover: 0,
        deadStockPercentage: totalItems > 0 ? (outOfStock / totalItems) * 100 : 0,
        cashFlow: 0,
        revenueGrowth: dashboardRes?.change ?? 0,
        profitGrowth: 0,
      });

      // Store Performance - no multi-store API available
      setStores([]);

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

      // Top Products - no top-products API available
      setTopProducts([]);

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
        <AlertTriangle className="w-12 h-12 text-red-400 mb-4" />
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
          >
            <option value="today">Today</option>
            <option value="week">This Week</option>
            <option value="month">This Month</option>
            <option value="quarter">This Quarter</option>
            <option value="year">This Year</option>
          </select>
          <button
            onClick={loadDashboardData}
            className="btn-outline flex items-center gap-2"
          >
            <RefreshCw className="w-4 h-4" />
            Refresh
          </button>
        </div>
      </div>

      {/* Key Metrics */}
      {metrics && (
        <div className="grid grid-cols-1 tablet:grid-cols-2 desktop:grid-cols-4 gap-4">
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
                    <p className="text-sm text-gray-600">{store.staffCount} staff members</p>
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
                  <p className="text-xs text-gray-600">Profit Margin</p>
                  <p className="text-sm font-semibold text-gray-900">{store.profitMargin}%</p>
                </div>
                <div>
                  <p className="text-xs text-gray-600">Sales/Sq.Ft</p>
                  <p className="text-sm font-semibold text-gray-900">₹{store.salesPerSqFt}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-600">Dead Stock</p>
                  <p className={`text-sm font-semibold ${
                    store.inventoryValue > 0 && (store.deadStockValue / store.inventoryValue) * 100 > 20 ? 'text-red-600' : 'text-gray-900'
                  }`}>
                    {formatCurrency(store.deadStockValue)}
                  </p>
                </div>
              </div>

              {/* Problem Indicators for underperforming stores */}
              {index >= 3 && (
                <div className="mt-3 p-3 bg-white rounded border border-red-200">
                  <p className="text-xs font-medium text-red-800 mb-2">Performance Issues:</p>
                  <div className="flex flex-wrap gap-2">
                    {store.profitMargin < 15 && (
                      <span className="px-2 py-1 bg-red-100 text-red-800 text-xs rounded">Low Margin</span>
                    )}
                    {store.salesPerSqFt < 1500 && (
                      <span className="px-2 py-1 bg-red-100 text-red-800 text-xs rounded">Low Sales Density</span>
                    )}
                    {store.inventoryValue > 0 && (store.deadStockValue / store.inventoryValue) * 100 > 25 && (
                      <span className="px-2 py-1 bg-red-100 text-red-800 text-xs rounded">High Dead Stock</span>
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
                  <p className="text-xs text-gray-600">{product.quantity} units • {product.margin}% margin</p>
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
