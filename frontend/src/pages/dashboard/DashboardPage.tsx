// ============================================================================
// IMS 2.0 - Enterprise Dashboard (Phase 6)
// ============================================================================
// SAP/Power BI style KPI dashboard with real-time analytics

import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { useModule, MODULE_CONFIGS, type ModuleId } from '../../context/ModuleContext';
import api from '../../services/api';
import {
  TrendingUp,
  TrendingDown,
  IndianRupee,
  AlertCircle,
  RefreshCw,
  ShoppingCart,
  DollarSign,
  Percent,
  Activity,
  Warehouse,
} from 'lucide-react';
import clsx from 'clsx';

interface EnterpriseKPIs {
  period: string;
  timestamp: string;
  store_id: string;
  revenue: {
    total: number;
    change_percent: number;
    avg_transaction_value: number;
    total_orders: number;
  };
  margins: {
    gross_margin_percent: number;
    net_margin_percent: number;
    gross_profit: number;
    net_profit: number;
  };
  customers: {
    footfall: number;
    avg_order_value: number;
  };
  inventory: {
    turnover_ratio: number;
    low_stock_items: number;
    total_items: number;
  };
  top_products: Array<{
    product_id: string;
    name: string;
    sku: string;
    units: number;
    revenue: number;
  }>;
  cash_register: {
    opening_balance: number;
    sales: number;
    expenses: number;
    closing_balance: number;
  };
  store_comparison: Array<{
    store_id: string;
    revenue: number;
    orders: number;
  }>;
}

// ============================================================================
// KPI Card Component
// ============================================================================

interface KpiCardProps {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  value: string | number;
  subtitle?: string;
  change?: number;
  changeType?: 'positive' | 'negative' | 'neutral';
  loading?: boolean;
  error?: boolean;
  suffix?: string;
}

function KpiCard({
  icon: Icon,
  title,
  value,
  subtitle,
  change,
  changeType,
  loading,
  error,
  suffix,
}: KpiCardProps) {
  return (
    <div
      className={clsx(
        'bg-white rounded-lg p-4 border shadow-sm',
        error ? 'border-red-200' : 'border-gray-200'
      )}
    >
      <div className="flex items-start justify-between mb-2">
        <div className={clsx('p-2 rounded-lg', error ? 'bg-red-50' : 'bg-blue-50')}>
          <Icon
            className={clsx('w-5 h-5', error ? 'text-red-600' : 'text-blue-600')}
          />
        </div>
        {change !== undefined && !loading && !error && (
          <div
            className={clsx(
              'flex items-center gap-1 text-xs font-medium',
              changeType === 'positive'
                ? 'text-green-600'
                : changeType === 'negative'
                  ? 'text-red-600'
                  : 'text-gray-500'
            )}
          >
            {changeType === 'positive' && <TrendingUp className="w-3 h-3" />}
            {changeType === 'negative' && <TrendingDown className="w-3 h-3" />}
            {Math.abs(change).toFixed(1)}%
          </div>
        )}
      </div>

      <p className="text-xs text-gray-600 mb-1">{title}</p>

      {loading ? (
        <div className="h-8 bg-gray-200 animate-pulse rounded w-24 mb-1" />
      ) : error ? (
        <p className="text-sm text-red-600 font-medium">Error loading</p>
      ) : (
        <p className="text-2xl font-bold text-gray-900">
          {typeof value === 'number' ? value.toLocaleString('en-IN') : value}
          {suffix && <span className="text-sm text-gray-500 ml-1">{suffix}</span>}
        </p>
      )}

      {subtitle && <p className="text-xs text-gray-500 mt-1">{subtitle}</p>}
    </div>
  );
}

// ============================================================================
// Period Selector Component
// ============================================================================

function PeriodSelector({
  period,
  onChange,
}: {
  period: string;
  onChange: (period: string) => void;
}) {
  const periods = [
    { value: 'today', label: 'Today' },
    { value: 'week', label: 'This Week' },
    { value: 'month', label: 'This Month' },
    { value: 'year', label: 'This Year' },
  ];

  return (
    <div className="flex gap-2">
      {periods.map((p) => (
        <button
          key={p.value}
          onClick={() => onChange(p.value)}
          className={clsx(
            'px-3 py-1 rounded-lg text-sm font-medium transition-colors',
            period === p.value
              ? 'bg-bv-gold-500 text-white'
              : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
          )}
        >
          {p.label}
        </button>
      ))}
    </div>
  );
}

// ============================================================================
// Module Card Component
// ============================================================================

interface ModuleCardProps {
  moduleId: ModuleId;
  title: string;
  subtitle: string;
  icon: React.ComponentType<{ className?: string }>;
  color: string;
  bgColor: string;
  onClick: () => void;
}

function ModuleCard({
  title,
  subtitle,
  icon: Icon,
  color,
  bgColor,
  onClick,
}: ModuleCardProps) {
  return (
    <div
      className="bg-white rounded-lg p-4 border border-gray-200 shadow-sm cursor-pointer hover:border-bv-gold-300 hover:shadow-md transition-all group"
      onClick={onClick}
    >
      <div
        className={clsx(
          'w-10 h-10 rounded-lg flex items-center justify-center mb-3',
          bgColor
        )}
      >
        <Icon className={clsx('w-5 h-5', color)} />
      </div>
      <h3 className="font-semibold text-gray-900 text-sm group-hover:text-bv-gold-600 transition-colors">
        {title}
      </h3>
      <p className="text-xs text-gray-500 mt-1">{subtitle}</p>
    </div>
  );
}

// ============================================================================
// Main Dashboard Component
// ============================================================================

export default function DashboardPage() {
  const { user } = useAuth();
  const { setActiveModule, getModulesForRole, goToDashboard } = useModule();
  const navigate = useNavigate();

  // Clear active module when dashboard loads
  useEffect(() => {
    goToDashboard();
  }, [goToDashboard]);

  const [period, setPeriod] = useState<string>('today');
  const [isLoading, setIsLoading] = useState(false);
  const [hasError, setHasError] = useState(false);
  const [kpis, setKpis] = useState<EnterpriseKPIs | null>(null);

  // Load KPIs when period changes
  useEffect(() => {
    loadKpis();
  }, [period]);

  const loadKpis = async () => {
    setIsLoading(true);
    setHasError(false);
    try {
      const response = await api.get(
        `/analytics/enterprise-kpis?period=${period}`
      );
      setKpis(response.data);
    } catch (error) {
      console.error('Error loading KPIs:', error);
      setHasError(true);
    } finally {
      setIsLoading(false);
    }
  };

  const handleModuleClick = (moduleId: ModuleId) => {
    setActiveModule(moduleId);
    const module = MODULE_CONFIGS.find((m) => m.id === moduleId);
    if (module && module.sidebarItems.length > 0) {
      navigate(module.sidebarItems[0].path);
    }
  };

  const availableModules = user ? getModulesForRole(user.activeRole) : [];

  const formatCurrency = (amount: number) => {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 0,
    }).format(amount);
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <div className="bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 tablet:px-6 py-6">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h1 className="text-3xl font-bold text-gray-900">Dashboard</h1>
              <p className="text-sm text-gray-500 mt-1">
                Welcome back, {user?.name}
              </p>
            </div>
            <button
              onClick={loadKpis}
              disabled={isLoading}
              className="p-2 rounded-lg hover:bg-gray-100 transition-colors disabled:opacity-50"
              title="Refresh"
            >
              <RefreshCw
                className={clsx(
                  'w-5 h-5 text-gray-600',
                  isLoading && 'animate-spin'
                )}
              />
            </button>
          </div>

          <PeriodSelector period={period} onChange={setPeriod} />
        </div>
      </div>

      {/* Main Content */}
      <div className="max-w-7xl mx-auto px-4 tablet:px-6 py-8">
        {hasError && (
          <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-lg flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-red-600" />
            <p className="text-sm text-red-700">
              Failed to load dashboard data. Please try again.
            </p>
          </div>
        )}

        {/* KPI Grid - Section 1: Revenue & Transactions */}
        <div className="mb-8">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">
            Revenue & Transactions
          </h2>
          <div className="grid grid-cols-1 tablet:grid-cols-2 laptop:grid-cols-4 gap-4">
            <KpiCard
              icon={IndianRupee}
              title="Total Revenue"
              value={formatCurrency(kpis?.revenue.total || 0)}
              change={kpis?.revenue.change_percent}
              changeType={
                (kpis?.revenue.change_percent || 0) > 0 ? 'positive' : 'negative'
              }
              loading={isLoading}
              error={hasError}
            />
            <KpiCard
              icon={ShoppingCart}
              title="Total Orders"
              value={kpis?.revenue.total_orders || 0}
              loading={isLoading}
              error={hasError}
            />
            <KpiCard
              icon={DollarSign}
              title="Avg Transaction Value"
              value={formatCurrency(kpis?.revenue.avg_transaction_value || 0)}
              loading={isLoading}
              error={hasError}
            />
            <KpiCard
              icon={Activity}
              title="Customer Footfall"
              value={kpis?.customers.footfall || 0}
              subtitle="unique customers"
              loading={isLoading}
              error={hasError}
            />
          </div>
        </div>

        {/* KPI Grid - Section 2: Margins & Profitability */}
        <div className="mb-8">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">
            Margins & Profitability
          </h2>
          <div className="grid grid-cols-1 tablet:grid-cols-2 laptop:grid-cols-4 gap-4">
            <KpiCard
              icon={Percent}
              title="Gross Margin %"
              value={(kpis?.margins.gross_margin_percent || 0).toFixed(1)}
              suffix="%"
              loading={isLoading}
              error={hasError}
            />
            <KpiCard
              icon={Percent}
              title="Net Margin %"
              value={(kpis?.margins.net_margin_percent || 0).toFixed(1)}
              suffix="%"
              loading={isLoading}
              error={hasError}
            />
            <KpiCard
              icon={IndianRupee}
              title="Gross Profit"
              value={formatCurrency(kpis?.margins.gross_profit || 0)}
              loading={isLoading}
              error={hasError}
            />
            <KpiCard
              icon={IndianRupee}
              title="Net Profit"
              value={formatCurrency(kpis?.margins.net_profit || 0)}
              loading={isLoading}
              error={hasError}
            />
          </div>
        </div>

        {/* KPI Grid - Section 3: Inventory & Operations */}
        <div className="mb-8">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">
            Inventory & Operations
          </h2>
          <div className="grid grid-cols-1 tablet:grid-cols-2 laptop:grid-cols-4 gap-4">
            <KpiCard
              icon={Warehouse}
              title="Inventory Turnover"
              value={(kpis?.inventory.turnover_ratio || 0).toFixed(2)}
              subtitle="times per period"
              loading={isLoading}
              error={hasError}
            />
            <KpiCard
              icon={AlertCircle}
              title="Low Stock Items"
              value={kpis?.inventory.low_stock_items || 0}
              subtitle={`of ${kpis?.inventory.total_items || 0} items`}
              loading={isLoading}
              error={hasError}
            />
            <KpiCard
              icon={IndianRupee}
              title="Cash Register Opening"
              value={formatCurrency(kpis?.cash_register.opening_balance || 0)}
              loading={isLoading}
              error={hasError}
            />
            <KpiCard
              icon={IndianRupee}
              title="Cash Register Closing"
              value={formatCurrency(kpis?.cash_register.closing_balance || 0)}
              loading={isLoading}
              error={hasError}
            />
          </div>
        </div>

        {/* Top Products */}
        {kpis?.top_products && kpis.top_products.length > 0 && (
          <div className="mb-8">
            <h2 className="text-lg font-semibold text-gray-900 mb-4">
              Top 5 Selling Products
            </h2>
            <div className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
              <table className="w-full">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600">
                      Product
                    </th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-gray-600">
                      Units Sold
                    </th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-gray-600">
                      Revenue
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {kpis.top_products.map((product) => (
                    <tr
                      key={product.product_id}
                      className="border-b border-gray-100 hover:bg-gray-50"
                    >
                      <td className="px-4 py-3">
                        <div>
                          <p className="text-sm font-medium text-gray-900">
                            {product.name}
                          </p>
                          <p className="text-xs text-gray-500">{product.sku}</p>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-right text-sm font-medium text-gray-900">
                        {product.units}
                      </td>
                      <td className="px-4 py-3 text-right text-sm font-medium text-gray-900">
                        {formatCurrency(product.revenue)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Quick Access Modules */}
        <div className="mb-8">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">
            Quick Access
          </h2>
          <div className="grid grid-cols-2 tablet:grid-cols-3 laptop:grid-cols-6 gap-3">
            {availableModules.slice(0, 6).map((module) => (
              <ModuleCard
                key={module.id}
                moduleId={module.id}
                title={module.title}
                subtitle={module.subtitle}
                icon={module.icon}
                color={module.color}
                bgColor={module.bgColor}
                onClick={() => handleModuleClick(module.id)}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
