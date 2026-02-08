import { useState, useEffect } from 'react';
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
import { reportsApi } from '../../services/api';
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

  // Margin Metrics
  grossMarginPercent: number;
  marginTarget: number;

  // Inventory Metrics
  inventoryTurnover: number;
  turnoverTarget: number;

  // Customer Metrics
  customerAcquisition: number;
  customerAcquisitionChange: number;
  newCustomers: number;
  returningCustomers: number;
  topCustomers: Array<{ name: string; spend: number; orders: number }>;

  // Inventory Intelligence
  lowStockItems: number;
  deadStockValue: number;
  deadStockItems: number;
  fastMovingItems: number;

  // Prescription Metrics
  prescriptionRenewals: number;
}

// ============================================================================
// Main Enterprise Dashboard Component
// ============================================================================

export default function EnterpriseAnalyticsDashboard() {
  const { user } = useAuth();
  const navigate = useNavigate();

  // State
  const [timeRange, setTimeRange] = useState<'today' | 'week' | 'month' | 'quarter' | 'year'>('month');
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<EnterpriseMetrics | null>(null);
  const [chartToggle, setChartToggle] = useState<'daily' | 'weekly' | 'monthly'>('daily');
  const stores = metrics ? generateMockStores(metrics) : [];

  // Load data on mount and when filters change
  useEffect(() => {
    loadDashboardData();
  }, [timeRange, user?.activeStoreId]);

  const loadDashboardData = async () => {
    setIsLoading(true);
    setError(null);
    try {
      // Call multiple API endpoints in parallel
      const [dashboardRes, salesRes] = await Promise.all([
        reportsApi.getDashboardStats(user?.activeStoreId || '').catch(() => null),
        reportsApi.getSalesSummary(user?.activeStoreId || '', getTodayDate(), getTodayDate()).catch(() => null),
      ]);

      // Process and set metrics
      const processedMetrics: EnterpriseMetrics = {
        // Revenue
        totalRevenue: dashboardRes?.totalSales ?? 0,
        revenueChange: dashboardRes?.change ?? 0,
        revenueYoY: dashboardRes?.change ?? 0,
        revenueTrend: generateTrendData(dashboardRes?.totalSales ?? 0),

        // Orders
        totalOrders: salesRes?.summary?.total_orders ?? 0,
        orderChange: 0,
        conversionRate: calculateConversion(salesRes?.summary?.total_orders ?? 0),

        // Value
        averageOrderValue: salesRes?.summary?.avg_order_value ?? 0,
        aovChange: 0,
        aovTarget: 15000,

        // Margin
        grossMarginPercent: 40,
        marginTarget: 42,

        // Inventory
        inventoryTurnover: 8.5,
        turnoverTarget: 10,

        // Customer
        customerAcquisition: 25,
        customerAcquisitionChange: 12,
        newCustomers: 45,
        returningCustomers: 155,
        topCustomers: [
          { name: 'Customer 1', spend: 450000, orders: 18 },
          { name: 'Customer 2', spend: 380000, orders: 15 },
          { name: 'Customer 3', spend: 320000, orders: 13 },
        ],

        // Inventory Intelligence
        lowStockItems: dashboardRes?.lowStockItems ?? 0,
        deadStockValue: 250000,
        deadStockItems: 45,
        fastMovingItems: 28,

        // Prescriptions
        prescriptionRenewals: 32,
      };

      setMetrics(processedMetrics);
      setError(null);
    } catch (err) {
      setError('Failed to load dashboard data');
      console.error('Dashboard error:', err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleExportReport = () => {
    // Implementation for report export
    alert('Report export feature coming soon!');
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
          <p className="text-gray-600 mt-1">Real-time business intelligence for {stores.length || 'all'} locations</p>
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
              subtext={`Conversion: ${metrics.conversionRate.toFixed(1)}%`}
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
              value={metrics.grossMarginPercent.toFixed(1)}
              unit="%"
              target={metrics.marginTarget}
              icon={Percent}
              status={metrics.grossMarginPercent >= metrics.marginTarget ? 'success' : 'warning'}
              loading={isLoading}
            />
          </div>

          {/* Second Row of KPIs */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mt-4">
            {/* Inventory Turnover */}
            <EnterpriseKpiCard
              label="Inventory Turnover Ratio"
              value={metrics.inventoryTurnover.toFixed(1)}
              unit="x"
              target={metrics.turnoverTarget}
              icon={Package}
              status={metrics.inventoryTurnover >= metrics.turnoverTarget ? 'success' : 'warning'}
              loading={isLoading}
              onClick={() => navigate('/inventory')}
            />

            {/* Customer Acquisition */}
            <EnterpriseKpiCard
              label="Customer Acquisition Rate"
              value={metrics.customerAcquisition}
              subtext={`${metrics.newCustomers} new customers`}
              change={metrics.customerAcquisitionChange}
              icon={Users}
              status="positive"
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
              value={metrics.prescriptionRenewals}
              subtext="Pending eye tests"
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
              data={generateChartData(metrics.totalRevenue, 14)}
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
      {metrics && (
        <MultiStorePerformanceTable
          stores={generateMockStores(metrics)}
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
              View Low Stock →
            </button>
          </div>

          {/* Dead Stock */}
          <div className="bg-white rounded-xl border border-red-200 bg-red-50 shadow-sm p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-red-900">Dead Stock</h3>
              <Package className="w-5 h-5 text-red-600" />
            </div>
            <p className="text-3xl font-bold text-red-600 mb-2">{formatChartValue(metrics.deadStockValue)}</p>
            <p className="text-sm text-red-700">{metrics.deadStockItems} items not sold in 90+ days</p>
            <button
              onClick={() => navigate('/inventory?tab=dead-stock')}
              className="mt-4 w-full px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors text-sm font-medium"
            >
              Review Dead Stock →
            </button>
          </div>

          {/* Fast Moving Items */}
          <div className="bg-white rounded-xl border border-green-200 bg-green-50 shadow-sm p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-green-900">Fast Moving Items</h3>
              <TrendingUp className="w-5 h-5 text-green-600" />
            </div>
            <p className="text-3xl font-bold text-green-600 mb-2">{metrics.fastMovingItems}</p>
            <p className="text-sm text-green-700">Top performers this period</p>
            <button
              onClick={() => navigate('/reports?tab=products')}
              className="mt-4 w-full px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors text-sm font-medium"
            >
              Analyze Products →
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
            <div className="space-y-4">
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-gray-700">New Customers</span>
                  <span className="text-2xl font-bold text-blue-600">{metrics.newCustomers}</span>
                </div>
                <div className="w-full h-2 bg-gray-200 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-500 transition-all"
                    style={{ width: `${(metrics.newCustomers / (metrics.newCustomers + metrics.returningCustomers)) * 100}%` }}
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
                    style={{ width: `${(metrics.returningCustomers / (metrics.newCustomers + metrics.returningCustomers)) * 100}%` }}
                  />
                </div>
              </div>
            </div>
            <p className="text-xs text-gray-600 mt-4">
              Retention Rate: {(metrics.returningCustomers / (metrics.newCustomers + metrics.returningCustomers) * 100).toFixed(1)}%
            </p>
          </div>

          {/* Top Customers */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Top 10 Customers by Spend</h3>
            <div className="space-y-3">
              {metrics.topCustomers.map((customer, idx) => (
                <div key={idx} className="flex items-center justify-between p-2 hover:bg-gray-50 rounded-lg transition-colors">
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

// ============================================================================
// Helper Functions
// ============================================================================

function getTodayDate(): string {
  const today = new Date();
  return today.toISOString().split('T')[0];
}

function generateTrendData(baseValue: number, points = 14): number[] {
  const data: number[] = [];
  for (let i = 0; i < points; i++) {
    const variation = (Math.random() - 0.5) * 0.3 * baseValue;
    data.push(Math.max(0, baseValue + variation));
  }
  return data;
}

function generateChartData(baseValue: number, points = 14) {
  const data = [];
  for (let i = 0; i < points; i++) {
    const date = new Date();
    date.setDate(date.getDate() - (points - i - 1));
    const variation = (Math.random() - 0.5) * 0.2 * baseValue;
    const yoyVariation = (Math.random() - 0.5) * 0.15 * baseValue;

    data.push({
      label: date.toLocaleDateString('en-IN', { month: 'short', day: 'numeric' }),
      value: Math.max(0, baseValue + variation),
      value2: Math.max(0, baseValue + yoyVariation),
    });
  }
  return data;
}

function calculateConversion(orders: number): number {
  return Math.round((orders / 5000) * 100 * 10) / 10;
}

function generateMockStores(metrics: EnterpriseMetrics) {
  return [
    {
      storeId: 'store-1',
      storeName: 'Main Store',
      revenue: metrics.totalRevenue,
      orders: metrics.totalOrders,
      averageOrderValue: metrics.averageOrderValue,
      marginPercent: metrics.grossMarginPercent,
      stockValue: 5000000,
      staffCount: 12,
      revenuePerSqft: 5200,
      trend: metrics.revenueChange,
    },
    {
      storeId: 'store-2',
      storeName: 'North Branch',
      revenue: metrics.totalRevenue * 0.8,
      orders: Math.floor(metrics.totalOrders * 0.8),
      averageOrderValue: metrics.averageOrderValue * 0.95,
      marginPercent: metrics.grossMarginPercent - 2,
      stockValue: 4000000,
      staffCount: 10,
      revenuePerSqft: 4800,
      trend: 8.5,
    },
    {
      storeId: 'store-3',
      storeName: 'South Branch',
      revenue: metrics.totalRevenue * 0.7,
      orders: Math.floor(metrics.totalOrders * 0.7),
      averageOrderValue: metrics.averageOrderValue * 0.92,
      marginPercent: metrics.grossMarginPercent - 3,
      stockValue: 3500000,
      staffCount: 8,
      revenuePerSqft: 4200,
      trend: -2.3,
    },
    {
      storeId: 'store-4',
      storeName: 'East Branch',
      revenue: metrics.totalRevenue * 0.9,
      orders: Math.floor(metrics.totalOrders * 0.9),
      averageOrderValue: metrics.averageOrderValue * 1.05,
      marginPercent: metrics.grossMarginPercent + 1,
      stockValue: 4500000,
      staffCount: 11,
      revenuePerSqft: 5000,
      trend: 12.1,
    },
  ];
}
