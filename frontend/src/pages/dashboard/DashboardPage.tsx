// ============================================================================
// IMS 2.0 - Modular Dashboard Page
// ============================================================================
// Role-based module dashboard with context-aware navigation

import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { useModule, MODULE_CONFIGS, type ModuleId } from '../../context/ModuleContext';
import { reportsApi, storeApi } from '../../services/api';
import {
  TrendingUp,
  TrendingDown,
  Clock,
  Calendar,
  AlertTriangle,
  ChevronRight,
  IndianRupee,
  FileText,
  Users,
  Package,
  Eye,
  ShoppingCart,
} from 'lucide-react';
import type { UserRole } from '../../types';
import clsx from 'clsx';

// ============================================================================
// Types
// ============================================================================

interface DashboardStats {
  todaySales: number;
  pendingOrders: number;
  urgentOrders: number;
  appointmentsToday: number;
  upcomingAppointments: number;
  lowStockItems: number;
  salesChange: number;
}

interface RecentActivity {
  id: string;
  type: 'order' | 'delivery' | 'customer' | 'payment';
  message: string;
  time: string;
}

interface TodaySummary {
  totalOrders: number;
  deliveries: number;
  eyeTests: number;
  newCustomers: number;
  paymentsReceived: number;
}

// ============================================================================
// KPI Card Component
// ============================================================================

interface KpiCardProps {
  icon: React.ComponentType<{ className?: string }>;
  iconBg: string;
  value: string | number;
  label: string;
  change?: number | string;
  changeType?: 'positive' | 'negative' | 'neutral';
  loading?: boolean;
  onClick?: () => void;
}

function KpiCard({ icon: Icon, iconBg, value, label, change, changeType, loading, onClick }: KpiCardProps) {
  return (
    <div
      className={clsx(
        'bg-white rounded-xl p-4 flex items-center gap-4 border border-gray-100 shadow-sm',
        onClick && 'cursor-pointer hover:border-bv-gold-200 transition-colors'
      )}
      onClick={onClick}
    >
      <div className={clsx('p-3 rounded-xl', iconBg)}>
        <Icon className="w-6 h-6" />
      </div>
      <div className="flex-1">
        {loading ? (
          <div className="h-7 w-16 bg-gray-200 animate-pulse rounded" />
        ) : (
          <p className="text-2xl font-bold text-gray-900">{value}</p>
        )}
        <p className="text-sm text-gray-500">{label}</p>
      </div>
      {change !== undefined && (
        <div className={clsx(
          'text-sm flex items-center gap-1',
          changeType === 'positive' ? 'text-green-600' :
          changeType === 'negative' ? 'text-red-600' :
          'text-gray-500'
        )}>
          {changeType === 'positive' && <TrendingUp className="w-4 h-4" />}
          {changeType === 'negative' && <TrendingDown className="w-4 h-4" />}
          {change}
        </div>
      )}
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

function ModuleCard({ title, subtitle, icon: Icon, color, bgColor, onClick }: ModuleCardProps) {
  return (
    <div
      className="bg-white rounded-xl p-5 border border-gray-100 shadow-sm cursor-pointer hover:border-bv-gold-300 hover:shadow-md transition-all group"
      onClick={onClick}
    >
      <div className={clsx('w-12 h-12 rounded-xl flex items-center justify-center mb-3', bgColor)}>
        <Icon className={clsx('w-6 h-6', color)} />
      </div>
      <h3 className="font-semibold text-gray-900 group-hover:text-bv-gold-600 transition-colors">{title}</h3>
      <p className="text-sm text-gray-500 mt-1">{subtitle}</p>
    </div>
  );
}

// ============================================================================
// Activity Item Component
// ============================================================================

function ActivityItem({ activity }: { activity: RecentActivity }) {
  const icons = {
    order: FileText,
    delivery: Package,
    customer: Users,
    payment: IndianRupee,
  };
  const Icon = icons[activity.type];

  return (
    <div className="flex items-center gap-3 py-3 border-b border-gray-100 last:border-0">
      <div className="p-2 bg-gray-100 rounded-lg">
        <Icon className="w-4 h-4 text-gray-500" />
      </div>
      <div className="flex-1">
        <p className="text-sm text-gray-900">{activity.message}</p>
        <p className="text-xs text-gray-500">{activity.time}</p>
      </div>
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

  // Clear active module when dashboard loads (ensures sidebar is hidden on dashboard)
  useEffect(() => {
    goToDashboard();
  }, [goToDashboard]);

  const [isLoading, setIsLoading] = useState(true);
  const [stats, setStats] = useState<DashboardStats>({
    todaySales: 0,
    pendingOrders: 0,
    urgentOrders: 0,
    appointmentsToday: 0,
    upcomingAppointments: 0,
    lowStockItems: 0,
    salesChange: 0,
  });

  const [recentActivity, setRecentActivity] = useState<RecentActivity[]>([]);
  const [todaySummary, setTodaySummary] = useState<TodaySummary>({
    totalOrders: 0,
    deliveries: 0,
    eyeTests: 0,
    newCustomers: 0,
    paymentsReceived: 0,
  });

  const [storeName, setStoreName] = useState<string>('');

  // Get modules available for user's role
  const availableModules = user ? getModulesForRole(user.activeRole) : [];

  // Load dashboard data when store changes
  useEffect(() => {
    loadDashboardData();
  }, [user?.activeStoreId]);

  const loadDashboardData = async () => {
    setIsLoading(true);
    try {
      // Load store name for display
      storeApi.getStores().then((stores: any) => {
        const storesArr = stores?.stores || stores || [];
        if (Array.isArray(storesArr)) {
          const active = storesArr.find((s: any) => s.id === user?.activeStoreId);
          if (active?.storeName) setStoreName(active.storeName);
        }
      }).catch(() => {});

      const salesRes = await reportsApi.getDashboardStats(user?.activeStoreId || '').catch(() => null);

      if (salesRes) {
        setStats({
          todaySales: salesRes.totalSales ?? 0,
          pendingOrders: salesRes.pendingOrders ?? 0,
          urgentOrders: salesRes.urgentOrders ?? 0,
          appointmentsToday: salesRes.appointmentsToday ?? 0,
          upcomingAppointments: salesRes.upcomingAppointments ?? 0,
          lowStockItems: salesRes.lowStockItems ?? 0,
          salesChange: salesRes.change ?? 0,
        });

        setTodaySummary({
          totalOrders: salesRes.today_orders ?? 0,
          deliveries: salesRes.today_deliveries ?? 0,
          eyeTests: salesRes.appointments_today ?? 0,
          newCustomers: salesRes.new_customers_today ?? 0,
          paymentsReceived: salesRes.total_sales ?? 0,
        });

        setRecentActivity(salesRes.recentActivity ?? []);
      } else {
        // No data available - show zeros
        setStats({
          todaySales: 0,
          pendingOrders: 0,
          urgentOrders: 0,
          appointmentsToday: 0,
          upcomingAppointments: 0,
          lowStockItems: 0,
          salesChange: 0,
        });
        setTodaySummary({
          totalOrders: 0,
          deliveries: 0,
          eyeTests: 0,
          newCustomers: 0,
          paymentsReceived: 0,
        });
        setRecentActivity([]);
      }
    } catch {
      setStats({
        todaySales: 0,
        pendingOrders: 0,
        urgentOrders: 0,
        appointmentsToday: 0,
        upcomingAppointments: 0,
        lowStockItems: 0,
        salesChange: 0,
      });
      setTodaySummary({
        totalOrders: 0,
        deliveries: 0,
        eyeTests: 0,
        newCustomers: 0,
        paymentsReceived: 0,
      });
      setRecentActivity([]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleModuleClick = (moduleId: ModuleId) => {
    setActiveModule(moduleId);
    // Navigate to the first item in the module's sidebar
    const module = MODULE_CONFIGS.find(m => m.id === moduleId);
    if (module && module.sidebarItems.length > 0) {
      navigate(module.sidebarItems[0].path);
    }
  };

  // Format currency
  const formatCurrency = (amount: number) => {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 0,
    }).format(amount);
  };

  // Get current date
  const today = new Date();
  const dateString = today.toLocaleDateString('en-IN', {
    weekday: 'long',
    day: 'numeric',
    month: 'short',
  });

  // Get financial year
  const currentMonth = today.getMonth();
  const currentYear = today.getFullYear();
  const financialYear = currentMonth >= 3
    ? `${currentYear}-${(currentYear + 1).toString().slice(-2)}`
    : `${currentYear - 1}-${currentYear.toString().slice(-2)}`;

  // Role-based KPI selection
  const getRoleKpis = (role?: UserRole): Omit<KpiCardProps, 'loading'>[] => {
    const salesKpi: Omit<KpiCardProps, 'loading'> = {
      icon: IndianRupee,
      iconBg: 'bg-green-100 text-green-600',
      value: formatCurrency(stats.todaySales),
      label: "Today's Sales",
      change: stats.salesChange > 0 ? `+${stats.salesChange}%` : undefined,
      changeType: 'positive',
      onClick: () => handleModuleClick('reports'),
    };
    const pendingKpi: Omit<KpiCardProps, 'loading'> = {
      icon: Clock,
      iconBg: 'bg-orange-100 text-orange-600',
      value: stats.pendingOrders,
      label: 'Pending Orders',
      change: stats.urgentOrders > 0 ? `${stats.urgentOrders} urgent` : undefined,
      changeType: stats.urgentOrders > 0 ? 'negative' : 'neutral',
      onClick: () => handleModuleClick('pos'),
    };
    const appointmentKpi: Omit<KpiCardProps, 'loading'> = {
      icon: Calendar,
      iconBg: 'bg-blue-100 text-blue-600',
      value: stats.appointmentsToday,
      label: 'Appointments Today',
      change: stats.upcomingAppointments > 0 ? `${stats.upcomingAppointments} upcoming` : undefined,
      changeType: 'neutral',
      onClick: () => handleModuleClick('clinic'),
    };
    const lowStockKpi: Omit<KpiCardProps, 'loading'> = {
      icon: AlertTriangle,
      iconBg: 'bg-red-100 text-red-600',
      value: stats.lowStockItems,
      label: 'Low Stock Items',
      change: stats.lowStockItems > 0 ? 'Action needed' : undefined,
      changeType: stats.lowStockItems > 0 ? 'negative' : 'neutral',
      onClick: () => handleModuleClick('inventory'),
    };
    const ordersKpi: Omit<KpiCardProps, 'loading'> = {
      icon: ShoppingCart,
      iconBg: 'bg-purple-100 text-purple-600',
      value: todaySummary.totalOrders,
      label: "Today's Orders",
      onClick: () => handleModuleClick('pos'),
    };
    const eyeTestsKpi: Omit<KpiCardProps, 'loading'> = {
      icon: Eye,
      iconBg: 'bg-purple-100 text-purple-600',
      value: todaySummary.eyeTests,
      label: 'Eye Tests Today',
      onClick: () => handleModuleClick('clinic'),
    };
    const customersKpi: Omit<KpiCardProps, 'loading'> = {
      icon: Users,
      iconBg: 'bg-teal-100 text-teal-600',
      value: todaySummary.newCustomers,
      label: 'New Customers',
      onClick: () => handleModuleClick('customers'),
    };
    const deliveriesKpi: Omit<KpiCardProps, 'loading'> = {
      icon: Package,
      iconBg: 'bg-emerald-100 text-emerald-600',
      value: todaySummary.deliveries,
      label: 'Deliveries Today',
      onClick: () => handleModuleClick('pos'),
    };

    switch (role) {
      case 'OPTOMETRIST':
        return [appointmentKpi, eyeTestsKpi, pendingKpi, customersKpi];
      case 'SALES_CASHIER':
      case 'SALES_STAFF':
        return [salesKpi, ordersKpi, pendingKpi, customersKpi];
      case 'WORKSHOP_STAFF':
        return [pendingKpi, deliveriesKpi, lowStockKpi, ordersKpi];
      case 'CATALOG_MANAGER':
        return [lowStockKpi, ordersKpi, salesKpi, pendingKpi];
      case 'ACCOUNTANT':
        return [salesKpi, pendingKpi, ordersKpi, lowStockKpi];
      default:
        // SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER
        return [salesKpi, pendingKpi, appointmentKpi, lowStockKpi];
    }
  };

  return (
    <div className="space-y-6">
      {/* Welcome Header */}
      <div className="bg-gradient-to-r from-bv-gold-500 to-bv-gold-600 rounded-2xl p-6 text-white">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">
              Welcome back, {user?.name || 'User'}!
            </h1>
            <p className="text-bv-gold-100 mt-1">
              {storeName || user?.activeStoreId || 'Main Store'} • Financial Year {financialYear}
            </p>
          </div>
          <div className="text-right">
            <p className="text-sm text-bv-gold-200">Today</p>
            <p className="text-xl font-semibold">{dateString}</p>
          </div>
        </div>
      </div>

      {/* Role-based KPI Stats Row */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {getRoleKpis(user?.activeRole).map((kpi, index) => (
          <KpiCard key={index} {...kpi} loading={isLoading} />
        ))}
      </div>

      {/* Modules Section */}
      <div>
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Modules</h2>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
          {availableModules.map(module => (
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

      {/* Recent Activity & Summary Row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Recent Activity */}
        <div className="lg:col-span-2 bg-white rounded-xl border border-gray-100 shadow-sm">
          <div className="p-4 border-b border-gray-100 flex items-center justify-between">
            <h3 className="font-semibold text-gray-900">Recent Activity</h3>
            <button className="text-sm text-bv-gold-600 hover:underline flex items-center gap-1">
              View All
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
          <div className="p-4">
            {isLoading ? (
              <div className="space-y-3">
                {[1, 2, 3, 4].map(i => (
                  <div key={i} className="flex items-center gap-3">
                    <div className="w-10 h-10 bg-gray-200 rounded-lg animate-pulse" />
                    <div className="flex-1 space-y-2">
                      <div className="h-4 bg-gray-200 rounded animate-pulse w-3/4" />
                      <div className="h-3 bg-gray-200 rounded animate-pulse w-1/4" />
                    </div>
                  </div>
                ))}
              </div>
            ) : recentActivity.length === 0 ? (
              <p className="text-center text-gray-500 py-8">No recent activity</p>
            ) : (
              recentActivity.map(activity => (
                <ActivityItem key={activity.id} activity={activity} />
              ))
            )}
          </div>
        </div>

        {/* Today's Summary */}
        <div className="bg-white rounded-xl border border-gray-100 shadow-sm">
          <div className="p-4 border-b border-gray-100">
            <h3 className="font-semibold text-gray-900">Today's Summary</h3>
          </div>
          <div className="p-4 space-y-3">
            {isLoading ? (
              <div className="space-y-3">
                {[1, 2, 3, 4, 5].map(i => (
                  <div key={i} className="flex justify-between">
                    <div className="h-4 bg-gray-200 rounded animate-pulse w-1/2" />
                    <div className="h-4 bg-gray-200 rounded animate-pulse w-1/4" />
                  </div>
                ))}
              </div>
            ) : (
              <>
                <div className="flex justify-between py-2 border-b border-gray-100">
                  <span className="text-gray-600">Total Orders</span>
                  <span className="font-semibold text-gray-900">{todaySummary.totalOrders}</span>
                </div>
                <div className="flex justify-between py-2 border-b border-gray-100">
                  <span className="text-gray-600">Deliveries</span>
                  <span className="font-semibold text-gray-900">{todaySummary.deliveries}</span>
                </div>
                <div className="flex justify-between py-2 border-b border-gray-100">
                  <span className="text-gray-600">Eye Tests</span>
                  <span className="font-semibold text-gray-900">{todaySummary.eyeTests}</span>
                </div>
                <div className="flex justify-between py-2 border-b border-gray-100">
                  <span className="text-gray-600">New Customers</span>
                  <span className="font-semibold text-gray-900">{todaySummary.newCustomers}</span>
                </div>
                <div className="flex justify-between py-2">
                  <span className="text-gray-600">Payments Received</span>
                  <span className="font-semibold text-bv-gold-600">{formatCurrency(todaySummary.paymentsReceived)}</span>
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Quick Actions (hidden on mobile) */}
      {/* This could be added as floating action buttons on mobile */}
    </div>
  );
}
