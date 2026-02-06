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
import { useToast } from '../../context/ToastContext';

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

export function ExecutiveDashboard() {
  const toast = useToast();

  const [timeRange, setTimeRange] = useState<'today' | 'week' | 'month' | 'quarter' | 'year'>('month');
  const [isLoading, setIsLoading] = useState(true);
  const [metrics, setMetrics] = useState<BusinessMetrics | null>(null);
  const [stores, setStores] = useState<StorePerformance[]>([]);
  const [categories, setCategories] = useState<CategoryPerformance[]>([]);
  const [topProducts, setTopProducts] = useState<TopProduct[]>([]);

  useEffect(() => {
    loadDashboardData();
  }, [timeRange]);

  const loadDashboardData = async () => {
    setIsLoading(true);
    try {
      // Mock data - replace with actual API calls
      await new Promise(resolve => setTimeout(resolve, 1000));

      // Business Metrics
      setMetrics({
        totalRevenue: 45250000, // 4.52 cr
        totalProfit: 8145000, // 81.45 lakhs
        totalOrders: 3245,
        totalCustomers: 1823,
        averageOrderValue: 13942,
        inventoryTurnover: 4.2,
        deadStockPercentage: 18.5,
        cashFlow: -2340000, // Negative cash flow
        revenueGrowth: 12.5,
        profitGrowth: 8.3,
      });

      // Store Performance
      setStores([
        {
          storeId: '1',
          storeName: 'Better Vision - Main Branch',
          revenue: 18500000,
          orders: 1456,
          profit: 3515000,
          profitMargin: 19.0,
          inventoryValue: 8500000,
          deadStockValue: 1200000,
          salesPerSqFt: 4250,
          staffCount: 12,
          trend: 'up',
          trendPercentage: 15.2,
        },
        {
          storeId: '2',
          storeName: 'Better Vision - Mall Road',
          revenue: 15200000,
          orders: 1123,
          profit: 2736000,
          profitMargin: 18.0,
          inventoryValue: 6200000,
          deadStockValue: 980000,
          salesPerSqFt: 3890,
          staffCount: 10,
          trend: 'up',
          trendPercentage: 8.7,
        },
        {
          storeId: '3',
          storeName: 'Better Vision - City Center',
          revenue: 8450000,
          orders: 789,
          profit: 1435750,
          profitMargin: 17.0,
          inventoryValue: 5100000,
          deadStockValue: 1150000,
          salesPerSqFt: 2105,
          staffCount: 8,
          trend: 'stable',
          trendPercentage: 2.1,
        },
        {
          storeId: '4',
          storeName: 'Better Vision - Satellite',
          revenue: 2100000,
          orders: 456,
          profit: 231000,
          profitMargin: 11.0,
          inventoryValue: 3800000,
          deadStockValue: 1100000,
          salesPerSqFt: 850,
          staffCount: 6,
          trend: 'down',
          trendPercentage: -5.3,
        },
        {
          storeId: '5',
          storeName: 'Better Vision - Navrangpura',
          revenue: 1000000,
          orders: 421,
          profit: 90000,
          profitMargin: 9.0,
          inventoryValue: 3200000,
          deadStockValue: 950000,
          salesPerSqFt: 625,
          staffCount: 4,
          trend: 'down',
          trendPercentage: -12.8,
        },
      ]);

      // Category Performance
      setCategories([
        { category: 'Eyeglasses', revenue: 18500000, profit: 5180000, margin: 28.0, turnover: 5.2, trend: 'up' },
        { category: 'Sunglasses', revenue: 12300000, profit: 3567000, margin: 29.0, turnover: 6.1, trend: 'up' },
        { category: 'Contact Lenses', revenue: 8750000, profit: 1750000, margin: 20.0, turnover: 8.5, trend: 'stable' },
        { category: 'Hearing Aids', revenue: 3200000, profit: 960000, margin: 30.0, turnover: 2.3, trend: 'down' },
        { category: 'Accessories', revenue: 2500000, profit: 750000, margin: 30.0, turnover: 4.8, trend: 'stable' },
      ]);

      // Top Products
      setTopProducts([
        { id: '1', name: 'Ray-Ban Aviator Classic', category: 'Sunglasses', revenue: 1245000, quantity: 287, margin: 32.5 },
        { id: '2', name: 'Titan Eye+ Premium Frame', category: 'Eyeglasses', revenue: 985000, quantity: 156, margin: 28.0 },
        { id: '3', name: 'Acuvue Oasys Monthly', category: 'Contact Lenses', revenue: 875000, quantity: 1250, margin: 22.0 },
        { id: '4', name: 'Oakley Frogskins', category: 'Sunglasses', revenue: 756000, quantity: 98, margin: 35.0 },
        { id: '5', name: 'Carrera Progressive Lenses', category: 'Eyeglasses', revenue: 698000, quantity: 112, margin: 30.0 },
      ]);

    } catch (error: any) {
      toast.error('Failed to load dashboard data');
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
            onChange={(e) => setTimeRange(e.target.value as any)}
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

          {/* Total Profit */}
          <div className="card">
            <div className="flex items-center justify-between">
              <div className="flex-1">
                <p className="text-sm text-gray-600 mb-1">Total Profit</p>
                <p className="text-2xl font-bold text-gray-900">{formatCurrency(metrics.totalProfit)}</p>
                <div className="flex items-center gap-1 mt-2">
                  {metrics.profitGrowth >= 0 ? (
                    <ArrowUpRight className="w-4 h-4 text-green-600" />
                  ) : (
                    <ArrowDownRight className="w-4 h-4 text-red-600" />
                  )}
                  <span className={`text-sm font-medium ${metrics.profitGrowth >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                    {Math.abs(metrics.profitGrowth)}%
                  </span>
                  <span className="text-xs text-gray-500">Margin: {((metrics.totalProfit / metrics.totalRevenue) * 100).toFixed(1)}%</span>
                </div>
              </div>
              <div className="w-12 h-12 bg-purple-100 rounded-lg flex items-center justify-center">
                <TrendingUp className="w-6 h-6 text-purple-600" />
              </div>
            </div>
          </div>

          {/* Dead Stock Alert */}
          <div className="card border-2 border-orange-200 bg-orange-50">
            <div className="flex items-center justify-between">
              <div className="flex-1">
                <p className="text-sm text-orange-800 mb-1">Dead Stock</p>
                <p className="text-2xl font-bold text-orange-900">{metrics.deadStockPercentage}%</p>
                <div className="flex items-center gap-1 mt-2">
                  <AlertTriangle className="w-4 h-4 text-orange-600" />
                  <span className="text-xs text-orange-700">High - needs attention</span>
                </div>
              </div>
              <div className="w-12 h-12 bg-orange-200 rounded-lg flex items-center justify-center">
                <Package className="w-6 h-6 text-orange-700" />
              </div>
            </div>
          </div>

          {/* Cash Flow */}
          <div className="card border-2 border-red-200 bg-red-50">
            <div className="flex items-center justify-between">
              <div className="flex-1">
                <p className="text-sm text-red-800 mb-1">Cash Flow</p>
                <p className="text-2xl font-bold text-red-900">{formatCurrency(Math.abs(metrics.cashFlow))}</p>
                <div className="flex items-center gap-1 mt-2">
                  <TrendingDown className="w-4 h-4 text-red-600" />
                  <span className="text-xs text-red-700">Negative - monitor closely</span>
                </div>
              </div>
              <div className="w-12 h-12 bg-red-200 rounded-lg flex items-center justify-center">
                <Activity className="w-6 h-6 text-red-700" />
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Store Performance Comparison */}
      <div className="card">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
            <Store className="w-5 h-5 text-purple-600" />
            5-Store Performance Comparison
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
                    (store.deadStockValue / store.inventoryValue) * 100 > 20 ? 'text-red-600' : 'text-gray-900'
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
                    {(store.deadStockValue / store.inventoryValue) * 100 > 25 && (
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

      <div className="grid grid-cols-1 desktop:grid-cols-2 gap-6">
        {/* Category Performance */}
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 flex items-center gap-2 mb-4">
            <PieChart className="w-5 h-5 text-purple-600" />
            Category Performance
          </h2>
          <div className="space-y-3">
            {categories.map((cat) => (
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
            {topProducts.map((product, index) => (
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
      <div className="card border-2 border-purple-200 bg-purple-50">
        <h2 className="text-lg font-semibold text-purple-900 flex items-center gap-2 mb-4">
          <Activity className="w-5 h-5 text-purple-600" />
          AI-Powered Insights & Recommendations
        </h2>
        <div className="grid grid-cols-1 desktop:grid-cols-2 gap-4">
          <div className="p-4 bg-white rounded-lg border border-purple-200">
            <div className="flex items-start gap-3">
              <AlertTriangle className="w-5 h-5 text-orange-600 flex-shrink-0 mt-1" />
              <div>
                <p className="font-medium text-gray-900 mb-1">Dead Stock Crisis</p>
                <p className="text-sm text-gray-700">
                  18.5% dead stock is costing you ₹54.8L in locked capital. Transfer slow-moving inventory from
                  underperforming stores to main branches. Consider 20-30% discount campaigns.
                </p>
              </div>
            </div>
          </div>
          <div className="p-4 bg-white rounded-lg border border-purple-200">
            <div className="flex items-start gap-3">
              <Store className="w-5 h-5 text-red-600 flex-shrink-0 mt-1" />
              <div>
                <p className="font-medium text-gray-900 mb-1">Store Performance Gap</p>
                <p className="text-sm text-gray-700">
                  Satellite & Navrangpura stores underperforming significantly. Consider staff training,
                  inventory optimization, or location analysis. Sales/Sq.Ft 75% below top stores.
                </p>
              </div>
            </div>
          </div>
          <div className="p-4 bg-white rounded-lg border border-purple-200">
            <div className="flex items-start gap-3">
              <TrendingUp className="w-5 h-5 text-green-600 flex-shrink-0 mt-1" />
              <div>
                <p className="font-medium text-gray-900 mb-1">Category Opportunity</p>
                <p className="text-sm text-gray-700">
                  Sunglasses showing 29% margin with 6.1x turnover. Increase inventory allocation in peak season
                  (Mar-Jun) to capture market demand and improve overall margins.
                </p>
              </div>
            </div>
          </div>
          <div className="p-4 bg-white rounded-lg border border-purple-200">
            <div className="flex items-start gap-3">
              <DollarSign className="w-5 h-5 text-blue-600 flex-shrink-0 mt-1" />
              <div>
                <p className="font-medium text-gray-900 mb-1">Cash Flow Optimization</p>
                <p className="text-sm text-gray-700">
                  Negative ₹23.4L cash flow. Review payment terms with suppliers, improve collection cycles,
                  and reduce inventory holding period from current 87 days to target 60 days.
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
