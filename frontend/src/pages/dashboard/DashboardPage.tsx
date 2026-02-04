// ============================================================================
// IMS 2.0 - Dashboard Page
// ============================================================================
// NO MOCK DATA - All data from API

import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { reportsApi, orderApi, workshopApi, inventoryApi } from '../../services/api';
import {
  ShoppingCart,
  Package,
  Users,
  TrendingUp,
  AlertTriangle,
  Wrench,
  IndianRupee,
  Target,
  Loader2,
  RefreshCw,
} from 'lucide-react';

// Types
interface DashboardStats {
  todaySales: number;
  todayOrders: number;
  pendingJobs: number;
  lowStockItems: number;
  monthSales: number;
  monthTarget: number;
  salesChange?: string;
  pendingDeliveries?: number;
  urgentJobs?: number;
}

interface RecentOrder {
  id: string;
  orderNumber: string;
  customerName: string;
  grandTotal: number;
  orderStatus: string;
}

interface Alert {
  type: 'low_stock' | 'jobs_ready' | 'overdue' | 'pending_payment';
  title: string;
  message: string;
  severity: 'warning' | 'info' | 'error';
}

// Stat Card Component
interface StatCardProps {
  title: string;
  value: string | number;
  icon: React.ComponentType<{ className?: string }>;
  change?: string;
  changeType?: 'positive' | 'negative' | 'neutral';
  subtitle?: string;
  loading?: boolean;
}

function StatCard({ title, value, icon: Icon, change, changeType = 'neutral', subtitle, loading }: StatCardProps) {
  return (
    <div className="card">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm font-medium text-gray-500">{title}</p>
          {loading ? (
            <div className="h-8 w-20 bg-gray-200 animate-pulse rounded mt-1" />
          ) : (
            <p className="text-2xl font-bold text-gray-900 mt-1">{value}</p>
          )}
          {subtitle && <p className="text-xs text-gray-500 mt-1">{subtitle}</p>}
          {change && (
            <p
              className={`text-sm mt-1 ${
                changeType === 'positive'
                  ? 'text-green-600'
                  : changeType === 'negative'
                  ? 'text-red-600'
                  : 'text-gray-500'
              }`}
            >
              {change}
            </p>
          )}
        </div>
        <div className="p-3 bg-bv-red-50 rounded-lg">
          <Icon className="w-6 h-6 text-bv-red-600" />
        </div>
      </div>
    </div>
  );
}

// Quick Action Button
interface QuickActionProps {
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  onClick?: () => void;
}

function QuickAction({ label, icon: Icon, onClick }: QuickActionProps) {
  return (
    <button
      onClick={onClick}
      className="flex flex-col items-center gap-2 p-4 bg-white border border-gray-200 rounded-xl hover:border-bv-red-200 hover:bg-bv-red-50 transition-colors touch-target"
    >
      <div className="p-3 bg-gray-100 rounded-lg">
        <Icon className="w-6 h-6 text-gray-600" />
      </div>
      <span className="text-sm font-medium text-gray-700">{label}</span>
    </button>
  );
}

export function DashboardPage() {
  const { user, hasRole } = useAuth();
  const navigate = useNavigate();

  // Data state
  const [stats, setStats] = useState<DashboardStats>({
    todaySales: 0,
    todayOrders: 0,
    pendingJobs: 0,
    lowStockItems: 0,
    monthSales: 0,
    monthTarget: 0,
  });
  const [recentOrders, setRecentOrders] = useState<RecentOrder[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);

  // Loading state
  const [isLoading, setIsLoading] = useState(true);

  // Load dashboard data
  useEffect(() => {
    loadDashboardData();
  }, [user?.activeStoreId]);

  const loadDashboardData = async () => {
    if (!user?.activeStoreId) return;

    setIsLoading(true);
    try {
      // Fetch all dashboard data in parallel
      const [dashboardStats, ordersData, jobsData, lowStockData] = await Promise.all([
        reportsApi.getDashboardStats(user.activeStoreId).catch(() => null),
        orderApi.getOrders({ storeId: user.activeStoreId, date: 'today', limit: 5 }).catch(() => ({ orders: [] })),
        workshopApi.getJobs(user.activeStoreId).catch(() => ({ jobs: [] })),
        inventoryApi.getLowStock(user.activeStoreId).catch(() => ({ items: [] })),
      ]);

      // Process dashboard stats
      if (dashboardStats) {
        setStats({
          todaySales: dashboardStats.todaySales || 0,
          todayOrders: dashboardStats.todayOrders || 0,
          pendingJobs: dashboardStats.pendingJobs || 0,
          lowStockItems: dashboardStats.lowStockItems || 0,
          monthSales: dashboardStats.monthSales || 0,
          monthTarget: dashboardStats.monthTarget || 0,
          salesChange: dashboardStats.salesChange,
          pendingDeliveries: dashboardStats.pendingDeliveries,
          urgentJobs: dashboardStats.urgentJobs,
        });
      } else {
        // Calculate from individual API calls if dashboard stats not available
        const orders = ordersData?.orders || ordersData || [];
        const jobs = jobsData?.jobs || jobsData || [];
        const lowStock = lowStockData?.items || lowStockData || [];

        const todayOrders = Array.isArray(orders) ? orders : [];
        const todaySales = todayOrders.reduce((sum: number, o: { grandTotal?: number }) => sum + (o.grandTotal || 0), 0);
        const activeJobs = Array.isArray(jobs) ? jobs.filter((j: { status?: string }) =>
          !['DELIVERED', 'CANCELLED'].includes(j.status || '')
        ) : [];

        setStats(prev => ({
          ...prev,
          todaySales,
          todayOrders: todayOrders.length,
          pendingJobs: activeJobs.length,
          lowStockItems: Array.isArray(lowStock) ? lowStock.length : 0,
        }));
      }

      // Set recent orders
      const orders = ordersData?.orders || ordersData || [];
      setRecentOrders(Array.isArray(orders) ? orders.slice(0, 3) : []);

      // Build alerts
      const newAlerts: Alert[] = [];
      const lowStock = lowStockData?.items || lowStockData || [];
      if (Array.isArray(lowStock) && lowStock.length > 0) {
        newAlerts.push({
          type: 'low_stock',
          title: 'Low Stock Alert',
          message: `${lowStock.length} items below minimum stock level`,
          severity: 'warning',
        });
      }

      const jobs = jobsData?.jobs || jobsData || [];
      if (Array.isArray(jobs)) {
        const readyJobs = jobs.filter((j: { status?: string }) => j.status === 'READY');
        if (readyJobs.length > 0) {
          newAlerts.push({
            type: 'jobs_ready',
            title: 'Jobs Ready',
            message: `${readyJobs.length} jobs ready for customer pickup`,
            severity: 'info',
          });
        }
      }

      setAlerts(newAlerts);
    } catch {
      // Error loading dashboard data - fail silently for now
    } finally {
      setIsLoading(false);
    }
  };

  const targetAchievement = stats.monthTarget > 0
    ? Math.round((stats.monthSales / stats.monthTarget) * 100)
    : 0;

  const formatCurrency = (amount: number) => {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 0,
    }).format(amount);
  };

  const getStatusBadge = (status: string) => {
    const badges: Record<string, string> = {
      CONFIRMED: 'badge-success',
      PENDING: 'badge-warning',
      DRAFT: 'bg-gray-100 text-gray-600',
      IN_PROGRESS: 'badge-warning',
      READY: 'badge-success',
      DELIVERED: 'bg-emerald-100 text-emerald-600',
      CANCELLED: 'badge-error',
    };
    return badges[status] || 'bg-gray-100 text-gray-600';
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">
            Good {getGreeting()}, {user?.name?.split(' ')[0]}!
          </h1>
          <p className="text-gray-500 mt-1">
            Here's what's happening at your store today
          </p>
        </div>
        <button
          onClick={loadDashboardData}
          disabled={isLoading}
          className="btn-outline flex items-center gap-2"
        >
          {isLoading ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <RefreshCw className="w-4 h-4" />
          )}
          Refresh
        </button>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-2 laptop:grid-cols-4 gap-4">
        <StatCard
          title="Today's Sales"
          value={formatCurrency(stats.todaySales)}
          icon={IndianRupee}
          change={stats.salesChange}
          changeType={stats.salesChange?.startsWith('+') ? 'positive' : stats.salesChange?.startsWith('-') ? 'negative' : 'neutral'}
          loading={isLoading}
        />
        <StatCard
          title="Today's Orders"
          value={stats.todayOrders}
          icon={ShoppingCart}
          subtitle={stats.pendingDeliveries ? `${stats.pendingDeliveries} pending delivery` : undefined}
          loading={isLoading}
        />
        <StatCard
          title="Pending Jobs"
          value={stats.pendingJobs}
          icon={Wrench}
          subtitle={stats.urgentJobs ? `${stats.urgentJobs} urgent` : undefined}
          loading={isLoading}
        />
        <StatCard
          title="Low Stock Items"
          value={stats.lowStockItems}
          icon={AlertTriangle}
          changeType={stats.lowStockItems > 10 ? 'negative' : 'neutral'}
          loading={isLoading}
        />
      </div>

      {/* Target Progress */}
      {hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER']) && (
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <Target className="w-5 h-5 text-bv-red-600" />
              <h2 className="font-semibold text-gray-900">Monthly Target</h2>
            </div>
            {isLoading ? (
              <div className="h-8 w-16 bg-gray-200 animate-pulse rounded" />
            ) : (
              <span className="text-2xl font-bold text-gray-900">{targetAchievement}%</span>
            )}
          </div>
          <div className="h-3 bg-gray-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-bv-red-600 rounded-full transition-all duration-500"
              style={{ width: `${Math.min(targetAchievement, 100)}%` }}
            />
          </div>
          <div className="flex justify-between mt-2 text-sm text-gray-500">
            <span>{formatCurrency(stats.monthSales)} achieved</span>
            <span>Target: {formatCurrency(stats.monthTarget)}</span>
          </div>
        </div>
      )}

      {/* Quick Actions */}
      <div>
        <h2 className="font-semibold text-gray-900 mb-4">Quick Actions</h2>
        <div className="grid grid-cols-3 tablet:grid-cols-6 gap-3">
          {hasRole(['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'SALES_CASHIER', 'SALES_STAFF']) && (
            <QuickAction label="New Sale" icon={ShoppingCart} onClick={() => navigate('/pos')} />
          )}
          {hasRole(['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'SALES_CASHIER', 'SALES_STAFF']) && (
            <QuickAction label="Add Customer" icon={Users} onClick={() => navigate('/customers')} />
          )}
          {hasRole(['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'CATALOG_MANAGER']) && (
            <QuickAction label="Stock In" icon={Package} onClick={() => navigate('/inventory')} />
          )}
          {hasRole(['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'WORKSHOP_STAFF']) && (
            <QuickAction label="Workshop" icon={Wrench} onClick={() => navigate('/workshop')} />
          )}
          {hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']) && (
            <QuickAction label="Reports" icon={TrendingUp} onClick={() => navigate('/reports')} />
          )}
        </div>
      </div>

      {/* Recent Activity / Alerts */}
      <div className="grid tablet:grid-cols-2 gap-6">
        {/* Recent Orders */}
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-semibold text-gray-900">Recent Orders</h2>
            <button
              onClick={() => navigate('/orders')}
              className="text-sm text-bv-red-600 hover:text-bv-red-700"
            >
              View All
            </button>
          </div>
          {isLoading ? (
            <div className="space-y-3">
              {[1, 2, 3].map((i) => (
                <div key={i} className="flex items-center justify-between py-2 border-b border-gray-100 last:border-0">
                  <div className="space-y-2">
                    <div className="h-4 w-24 bg-gray-200 animate-pulse rounded" />
                    <div className="h-3 w-32 bg-gray-200 animate-pulse rounded" />
                  </div>
                  <div className="space-y-2 text-right">
                    <div className="h-4 w-16 bg-gray-200 animate-pulse rounded" />
                    <div className="h-5 w-20 bg-gray-200 animate-pulse rounded" />
                  </div>
                </div>
              ))}
            </div>
          ) : recentOrders.length === 0 ? (
            <p className="text-gray-500 text-center py-6">No orders today</p>
          ) : (
            <div className="space-y-3">
              {recentOrders.map((order) => (
                <div
                  key={order.id}
                  className="flex items-center justify-between py-2 border-b border-gray-100 last:border-0 cursor-pointer hover:bg-gray-50 -mx-2 px-2 rounded"
                  onClick={() => navigate('/orders')}
                >
                  <div>
                    <p className="font-medium text-gray-900">{order.orderNumber}</p>
                    <p className="text-sm text-gray-500">{order.customerName}</p>
                  </div>
                  <div className="text-right">
                    <p className="font-medium text-gray-900">{formatCurrency(order.grandTotal)}</p>
                    <span className={getStatusBadge(order.orderStatus)}>
                      {order.orderStatus}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Alerts */}
        <div className="card">
          <h2 className="font-semibold text-gray-900 mb-4">Alerts</h2>
          {isLoading ? (
            <div className="space-y-3">
              <div className="h-16 bg-gray-200 animate-pulse rounded-lg" />
              <div className="h-16 bg-gray-200 animate-pulse rounded-lg" />
            </div>
          ) : alerts.length === 0 ? (
            <p className="text-gray-500 text-center py-6">No alerts</p>
          ) : (
            <div className="space-y-3">
              {alerts.map((alert, index) => {
                const bgColor = alert.severity === 'warning' ? 'bg-yellow-50' :
                               alert.severity === 'error' ? 'bg-red-50' : 'bg-blue-50';
                const iconColor = alert.severity === 'warning' ? 'text-yellow-600' :
                                 alert.severity === 'error' ? 'text-red-600' : 'text-blue-600';
                const textColor = alert.severity === 'warning' ? 'text-yellow-800' :
                                 alert.severity === 'error' ? 'text-red-800' : 'text-blue-800';
                const subTextColor = alert.severity === 'warning' ? 'text-yellow-700' :
                                    alert.severity === 'error' ? 'text-red-700' : 'text-blue-700';
                const Icon = alert.type === 'low_stock' ? AlertTriangle :
                            alert.type === 'jobs_ready' ? Wrench : AlertTriangle;

                return (
                  <div key={index} className={`flex items-start gap-3 p-3 ${bgColor} rounded-lg`}>
                    <Icon className={`w-5 h-5 ${iconColor} flex-shrink-0`} />
                    <div>
                      <p className={`font-medium ${textColor}`}>{alert.title}</p>
                      <p className={`text-sm ${subTextColor}`}>{alert.message}</p>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Helper function for greeting
function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return 'Morning';
  if (hour < 17) return 'Afternoon';
  return 'Evening';
}

export default DashboardPage;
