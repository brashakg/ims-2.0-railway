// ============================================================================
// IMS 2.0 - Smart Alerts & Recommendations Center
// ============================================================================
// Proactive business intelligence and actionable insights

import { useState, useEffect } from 'react';
import {
  Bell,
  AlertTriangle,
  TrendingDown,
  TrendingUp,
  Package,
  DollarSign,
  Users,
  ShoppingCart,
  Clock,
  Target,
  Zap,
  CheckCircle,
  X,
  Loader2,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';

type AlertSeverity = 'CRITICAL' | 'WARNING' | 'INFO' | 'SUCCESS';
type AlertCategory = 'INVENTORY' | 'SALES' | 'CUSTOMER' | 'FINANCE' | 'OPERATIONS' | 'EMPLOYEE';

interface SmartAlert {
  id: string;
  title: string;
  message: string;
  severity: AlertSeverity;
  category: AlertCategory;
  timestamp: string;
  isRead: boolean;
  actionRequired: boolean;
  actionText?: string;
  actionUrl?: string;
  data?: any;
  recommendations?: string[];
}

export function SmartAlertsCenter() {
  const toast = useToast();

  const [alerts, setAlerts] = useState<SmartAlert[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [severityFilter, setSeverityFilter] = useState<AlertSeverity | 'ALL'>('ALL');
  const [categoryFilter, setCategoryFilter] = useState<AlertCategory | 'ALL'>('ALL');
  const [showOnlyUnread, setShowOnlyUnread] = useState(true);

  useEffect(() => {
    loadAlerts();
  }, []);

  const loadAlerts = async () => {
    setIsLoading(true);
    try {
      await new Promise(resolve => setTimeout(resolve, 800));

      // Generate smart alerts based on business intelligence
      const generatedAlerts: SmartAlert[] = [
        // CRITICAL ALERTS
        {
          id: '1',
          title: 'Dead Stock Crisis - Immediate Action Required',
          message: '18.5% of inventory (₹54.8L) is dead stock aging over 180 days. This is costing ₹4.6L/month in locked capital and storage.',
          severity: 'CRITICAL',
          category: 'INVENTORY',
          timestamp: new Date().toISOString(),
          isRead: false,
          actionRequired: true,
          actionText: 'View Dead Stock Report',
          actionUrl: '/inventory?tab=aging',
          recommendations: [
            'Transfer slow-moving items from Satellite & Navrangpura stores to Main Branch',
            'Launch 25-30% discount campaign for items aging > 120 days',
            'Negotiate return/exchange with suppliers for unopened stock',
            'Bundle slow movers with fast-moving products',
          ],
        },
        {
          id: '2',
          title: 'Negative Cash Flow Alert',
          message: 'Current cash flow is -₹23.4L. Accounts receivable aging at 67 days average, while payables are at 32 days.',
          severity: 'CRITICAL',
          category: 'FINANCE',
          timestamp: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
          isRead: false,
          actionRequired: true,
          actionText: 'View Cash Flow Analysis',
          recommendations: [
            'Extend supplier payment terms from 30 to 45 days',
            'Implement advance payment policy (50% minimum) for all orders',
            'Offer 2% early payment discount to customers for 7-day payment',
            'Reduce inventory holding from 87 days to target 60 days',
          ],
        },
        {
          id: '3',
          title: 'Store Performance Crisis - 2 Underperforming Locations',
          message: 'Satellite & Navrangpura stores combined revenue: ₹3.1Cr vs cost: ₹3.8Cr. Operating at 19% loss.',
          severity: 'CRITICAL',
          category: 'OPERATIONS',
          timestamp: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
          isRead: false,
          actionRequired: true,
          actionText: 'View Store Comparison',
          actionUrl: '/dashboard/executive',
          recommendations: [
            'Conduct location feasibility analysis - consider relocation or closure',
            'Transfer high-margin inventory to performing stores',
            'Reduce staff count from 10 to 6 (combine roles)',
            'Implement performance-based compensation for staff',
            'Analyze foot traffic and marketing spend effectiveness',
          ],
        },

        // WARNING ALERTS
        {
          id: '4',
          title: 'Reorder Point Reached - 12 Products',
          message: '12 fast-moving products have reached reorder points. Stock will run out in 3-5 days at current sales velocity.',
          severity: 'WARNING',
          category: 'INVENTORY',
          timestamp: new Date(Date.now() - 4 * 60 * 60 * 1000).toISOString(),
          isRead: false,
          actionRequired: true,
          actionText: 'View Reorder Dashboard',
          actionUrl: '/inventory?tab=reorders',
          data: {
            products: [
              { name: 'Ray-Ban Aviator Classic', stock: 8, reorderPoint: 10, dailySales: 2.1 },
              { name: 'Acuvue Oasys Monthly', stock: 45, reorderPoint: 50, dailySales: 8.3 },
            ],
          },
        },
        {
          id: '5',
          title: 'Customer Delivery Delays - 15 Pending Orders',
          message: '15 customer orders are past expected delivery date by 2-7 days. Risk of negative reviews and refunds.',
          severity: 'WARNING',
          category: 'CUSTOMER',
          timestamp: new Date(Date.now() - 6 * 60 * 60 * 1000).toISOString(),
          isRead: true,
          actionRequired: true,
          actionText: 'View Delayed Orders',
          actionUrl: '/orders?filter=delayed',
          recommendations: [
            'Call each customer personally to apologize and provide update',
            'Offer 10% discount or free accessories for inconvenience',
            'Expedite production with lab - pay rush fee if needed',
            'Review supplier lead times and adjust delivery promises',
          ],
        },
        {
          id: '6',
          title: 'Employee Task Completion Rate Dropping',
          message: 'Team task completion rate dropped from 94% to 87% this week. 8 overdue tasks across 3 stores.',
          severity: 'WARNING',
          category: 'EMPLOYEE',
          timestamp: new Date(Date.now() - 8 * 60 * 60 * 1000).toISOString(),
          isRead: false,
          actionRequired: true,
          actionText: 'View Task Analytics',
          actionUrl: '/tasks?tab=analytics',
          recommendations: [
            'Schedule one-on-one meetings with employees having >3 overdue tasks',
            'Review if tasks are realistic and properly distributed',
            'Provide additional training on time management',
            'Implement daily task review stand-up meetings',
          ],
        },

        // INFO ALERTS
        {
          id: '7',
          title: 'Seasonal Opportunity - Sunglasses Demand Rising',
          message: 'Historical data shows sunglasses sales increase 45% in March-June. Current stock may be insufficient.',
          severity: 'INFO',
          category: 'INVENTORY',
          timestamp: new Date(Date.now() - 12 * 60 * 60 * 1000).toISOString(),
          isRead: true,
          actionRequired: false,
          recommendations: [
            'Increase sunglasses inventory allocation by 40% for peak season',
            'Stock up on Ray-Ban, Oakley, and premium brands (highest margin)',
            'Plan marketing campaign for summer collection launch',
            'Train staff on sunglasses features and UV protection benefits',
          ],
        },
        {
          id: '8',
          title: 'Payment Terms Negotiation Opportunity',
          message: 'Supplier "Titan Eyewear" offers 45-day terms if monthly order exceeds ₹5L. Current: 30 days at ₹4.2L/month.',
          severity: 'INFO',
          category: 'FINANCE',
          timestamp: new Date(Date.now() - 18 * 60 * 60 * 1000).toISOString(),
          isRead: true,
          actionRequired: false,
          recommendations: [
            'Increase Titan monthly order from ₹4.2L to ₹5L',
            'Additional ₹80K investment unlocks 15 extra days cash flow',
            'Improved terms worth ₹22K/month in working capital benefit',
          ],
        },

        // SUCCESS ALERTS
        {
          id: '9',
          title: 'Sales Milestone Achieved - ₹1Cr This Month',
          message: 'Main Branch crossed ₹1Cr monthly sales for the first time. 23% growth vs last month.',
          severity: 'SUCCESS',
          category: 'SALES',
          timestamp: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString(),
          isRead: false,
          actionRequired: false,
        },
        {
          id: '10',
          title: 'Customer Retention Improving',
          message: 'Repeat customer rate increased from 34% to 42% after loyalty program launch.',
          severity: 'SUCCESS',
          category: 'CUSTOMER',
          timestamp: new Date(Date.now() - 36 * 60 * 60 * 1000).toISOString(),
          isRead: true,
          actionRequired: false,
          recommendations: [
            'Invest more in loyalty program rewards',
            'Launch referral incentive (refer 3 friends, get 20% off)',
          ],
        },
      ];

      setAlerts(generatedAlerts);

    } catch (error: any) {
      toast.error('Failed to load alerts');
    } finally {
      setIsLoading(false);
    }
  };

  const getSeverityConfig = (severity: AlertSeverity) => {
    const configs = {
      CRITICAL: {
        icon: AlertTriangle,
        bgColor: 'bg-red-50',
        borderColor: 'border-red-300',
        iconColor: 'text-red-600',
        badgeColor: 'bg-red-100 text-red-800',
      },
      WARNING: {
        icon: TrendingDown,
        bgColor: 'bg-orange-50',
        borderColor: 'border-orange-300',
        iconColor: 'text-orange-600',
        badgeColor: 'bg-orange-100 text-orange-800',
      },
      INFO: {
        icon: Zap,
        bgColor: 'bg-blue-50',
        borderColor: 'border-blue-300',
        iconColor: 'text-blue-600',
        badgeColor: 'bg-blue-100 text-blue-800',
      },
      SUCCESS: {
        icon: TrendingUp,
        bgColor: 'bg-green-50',
        borderColor: 'border-green-300',
        iconColor: 'text-green-600',
        badgeColor: 'bg-green-100 text-green-800',
      },
    };

    return configs[severity];
  };

  const getCategoryIcon = (category: AlertCategory) => {
    const icons = {
      INVENTORY: Package,
      SALES: ShoppingCart,
      CUSTOMER: Users,
      FINANCE: DollarSign,
      OPERATIONS: Target,
      EMPLOYEE: Users,
    };

    return icons[category];
  };

  const markAsRead = async (alertId: string) => {
    setAlerts(prev => prev.map(alert =>
      alert.id === alertId ? { ...alert, isRead: true } : alert
    ));
  };

  const dismissAlert = async (alertId: string) => {
    setAlerts(prev => prev.filter(alert => alert.id !== alertId));
    toast.success('Alert dismissed');
  };

  const filteredAlerts = alerts.filter(alert => {
    const matchesSeverity = severityFilter === 'ALL' || alert.severity === severityFilter;
    const matchesCategory = categoryFilter === 'ALL' || alert.category === categoryFilter;
    const matchesReadStatus = !showOnlyUnread || !alert.isRead;
    return matchesSeverity && matchesCategory && matchesReadStatus;
  });

  const unreadCount = alerts.filter(a => !a.isRead).length;
  const criticalCount = alerts.filter(a => a.severity === 'CRITICAL' && !a.isRead).length;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <Bell className="w-7 h-7 text-purple-600" />
            Smart Alerts & Insights
            {unreadCount > 0 && (
              <span className="px-3 py-1 bg-red-100 text-red-800 text-sm font-medium rounded-full">
                {unreadCount} new
              </span>
            )}
          </h1>
          <p className="text-gray-500 mt-1">AI-powered business intelligence and actionable recommendations</p>
        </div>
      </div>

      {/* Quick Stats */}
      <div className="grid grid-cols-1 tablet:grid-cols-4 gap-4">
        <div className="card border-2 border-red-200 bg-red-50">
          <div className="flex items-center gap-3">
            <AlertTriangle className="w-8 h-8 text-red-600" />
            <div>
              <p className="text-sm text-red-700">Critical Alerts</p>
              <p className="text-2xl font-bold text-red-900">{criticalCount}</p>
            </div>
          </div>
        </div>
        <div className="card border-2 border-orange-200 bg-orange-50">
          <div className="flex items-center gap-3">
            <TrendingDown className="w-8 h-8 text-orange-600" />
            <div>
              <p className="text-sm text-orange-700">Warnings</p>
              <p className="text-2xl font-bold text-orange-900">
                {alerts.filter(a => a.severity === 'WARNING' && !a.isRead).length}
              </p>
            </div>
          </div>
        </div>
        <div className="card border-2 border-blue-200 bg-blue-50">
          <div className="flex items-center gap-3">
            <Zap className="w-8 h-8 text-blue-600" />
            <div>
              <p className="text-sm text-blue-700">Opportunities</p>
              <p className="text-2xl font-bold text-blue-900">
                {alerts.filter(a => a.severity === 'INFO').length}
              </p>
            </div>
          </div>
        </div>
        <div className="card border-2 border-green-200 bg-green-50">
          <div className="flex items-center gap-3">
            <TrendingUp className="w-8 h-8 text-green-600" />
            <div>
              <p className="text-sm text-green-700">Wins</p>
              <p className="text-2xl font-bold text-green-900">
                {alerts.filter(a => a.severity === 'SUCCESS').length}
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-4 flex-wrap">
        <select
          value={severityFilter}
          onChange={(e) => setSeverityFilter(e.target.value as any)}
          className="input-field w-auto"
        >
          <option value="ALL">All Severity</option>
          <option value="CRITICAL">Critical</option>
          <option value="WARNING">Warning</option>
          <option value="INFO">Info</option>
          <option value="SUCCESS">Success</option>
        </select>

        <select
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value as any)}
          className="input-field w-auto"
        >
          <option value="ALL">All Categories</option>
          <option value="INVENTORY">Inventory</option>
          <option value="SALES">Sales</option>
          <option value="CUSTOMER">Customer</option>
          <option value="FINANCE">Finance</option>
          <option value="OPERATIONS">Operations</option>
          <option value="EMPLOYEE">Employee</option>
        </select>

        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={showOnlyUnread}
            onChange={(e) => setShowOnlyUnread(e.target.checked)}
            className="w-4 h-4 text-purple-600 rounded focus:ring-purple-500"
          />
          <span className="text-sm text-gray-700">Show only unread</span>
        </label>
      </div>

      {/* Alerts List */}
      {isLoading ? (
        <div className="flex items-center justify-center h-96">
          <Loader2 className="w-8 h-8 animate-spin text-purple-600" />
        </div>
      ) : (
        <div className="space-y-4">
          {filteredAlerts.map((alert) => {
            const severityConfig = getSeverityConfig(alert.severity);
            const Icon = severityConfig.icon;
            const CategoryIcon = getCategoryIcon(alert.category);

            return (
              <div
                key={alert.id}
                className={`card border-2 ${severityConfig.borderColor} ${severityConfig.bgColor} ${
                  !alert.isRead ? 'shadow-lg' : ''
                }`}
              >
                <div className="flex items-start gap-4">
                  <div className={`w-12 h-12 rounded-lg flex items-center justify-center flex-shrink-0 ${
                    alert.severity === 'CRITICAL' ? 'bg-red-200' :
                    alert.severity === 'WARNING' ? 'bg-orange-200' :
                    alert.severity === 'INFO' ? 'bg-blue-200' :
                    'bg-green-200'
                  }`}>
                    <Icon className={`w-6 h-6 ${severityConfig.iconColor}`} />
                  </div>

                  <div className="flex-1">
                    <div className="flex items-start justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <h3 className="text-lg font-semibold text-gray-900">{alert.title}</h3>
                        {!alert.isRead && (
                          <span className="w-2 h-2 bg-purple-600 rounded-full"></span>
                        )}
                      </div>
                      <button
                        onClick={() => dismissAlert(alert.id)}
                        className="p-1 hover:bg-gray-200 rounded transition-colors"
                      >
                        <X className="w-4 h-4 text-gray-500" />
                      </button>
                    </div>

                    <div className="flex items-center gap-2 mb-3">
                      <span className={`px-2 py-1 rounded text-xs font-medium ${severityConfig.badgeColor}`}>
                        {alert.severity}
                      </span>
                      <span className="px-2 py-1 bg-gray-100 text-gray-700 rounded text-xs font-medium flex items-center gap-1">
                        <CategoryIcon className="w-3 h-3" />
                        {alert.category}
                      </span>
                      <span className="text-xs text-gray-500 flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {new Date(alert.timestamp).toLocaleString()}
                      </span>
                    </div>

                    <p className="text-gray-800 mb-4">{alert.message}</p>

                    {alert.recommendations && alert.recommendations.length > 0 && (
                      <div className="mb-4 p-3 bg-white rounded-lg border border-gray-200">
                        <p className="text-sm font-semibold text-gray-900 mb-2">💡 Recommended Actions:</p>
                        <ul className="space-y-1">
                          {alert.recommendations.map((rec, idx) => (
                            <li key={idx} className="text-sm text-gray-700 flex items-start gap-2">
                              <CheckCircle className="w-4 h-4 text-green-600 flex-shrink-0 mt-0.5" />
                              <span>{rec}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}

                    <div className="flex items-center gap-3">
                      {alert.actionRequired && alert.actionText && (
                        <button
                          onClick={() => {
                            markAsRead(alert.id);
                            // Navigate to action URL
                          }}
                          className="btn-primary text-sm"
                        >
                          {alert.actionText}
                        </button>
                      )}
                      {!alert.isRead && (
                        <button
                          onClick={() => markAsRead(alert.id)}
                          className="btn-outline text-sm"
                        >
                          Mark as Read
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}

          {filteredAlerts.length === 0 && (
            <div className="text-center py-12">
              <CheckCircle className="w-12 h-12 text-green-400 mx-auto mb-3" />
              <p className="text-gray-500">No alerts to show. All caught up!</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
