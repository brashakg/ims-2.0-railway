// ============================================================================
// IMS 2.0 - Customer Analytics
// ============================================================================
// Analyze customer behavior, trends, and engagement metrics

import { useState } from 'react';
import { Users, Search, Filter, X } from 'lucide-react';
import clsx from 'clsx';

export interface CustomerMetrics {
  customerId: string;
  customerName: string;
  email: string;
  totalOrders: number;
  totalSpent: number;
  averageOrderValue: number;
  lastOrderDate?: string;
  firstOrderDate: string;
  status: 'active' | 'inactive' | 'at-risk' | 'vip';
  retentionScore: number; // 0-100
  purchaseFrequency: 'monthly' | 'quarterly' | 'yearly' | 'once';
  segmentId: string;
  churnRisk: boolean;
}

export interface Analytics {
  totalCustomers: number;
  activeCustomers: number;
  churnedCustomers: number;
  vipCustomers: number;
  totalRevenue: number;
  averageCustomerValue: number;
  repeatPurchaseRate: number;
  monthlyGrowth: number;
}

interface CustomerAnalyticsProps {
  metrics: CustomerMetrics[];
  analytics: Analytics;
  onExportMetrics: (format: 'csv' | 'pdf') => Promise<void>;
  loading?: boolean;
}

export function CustomerAnalytics({
  metrics,
  analytics,
  onExportMetrics,
  loading = false,
}: CustomerAnalyticsProps) {
  const [searchTerm, setSearchTerm] = useState('');
  const [filterStatus, setFilterStatus] = useState<string>('');
  const [sortBy, setSortBy] = useState<'totalSpent' | 'totalOrders' | 'retentionScore'>('totalSpent');
  const [showFilters, setShowFilters] = useState(false);

  const filteredMetrics = metrics
    .filter(m =>
      m.customerName.toLowerCase().includes(searchTerm.toLowerCase()) ||
      m.email.toLowerCase().includes(searchTerm.toLowerCase())
    )
    .filter(m => !filterStatus || m.status === filterStatus)
    .sort((a, b) => {
      if (sortBy === 'totalSpent') return b.totalSpent - a.totalSpent;
      if (sortBy === 'totalOrders') return b.totalOrders - a.totalOrders;
      if (sortBy === 'retentionScore') return b.retentionScore - a.retentionScore;
      return 0;
    });

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'vip':
        return 'bg-purple-100 text-purple-700';
      case 'active':
        return 'bg-green-100 text-green-700';
      case 'inactive':
        return 'bg-gray-100 text-gray-700';
      case 'at-risk':
        return 'bg-red-100 text-red-700';
      default:
        return 'bg-gray-100 text-gray-700';
    }
  };

  const getRetentionColor = (score: number) => {
    if (score >= 80) return 'text-green-600';
    if (score >= 60) return 'text-blue-600';
    if (score >= 40) return 'text-yellow-600';
    return 'text-red-600';
  };

  return (
    <div className="space-y-6">
      {/* KPI Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800 p-4">
          <p className="text-sm font-medium text-gray-600 dark:text-gray-400 mb-2">Total Customers</p>
          <p className="text-3xl font-bold text-gray-900 dark:text-white">{analytics.totalCustomers}</p>
          <p className="text-xs text-gray-500 dark:text-gray-500 mt-2">
            {analytics.activeCustomers} active
          </p>
        </div>
        <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800 p-4">
          <p className="text-sm font-medium text-gray-600 dark:text-gray-400 mb-2">Total Revenue</p>
          <p className="text-3xl font-bold text-green-600 dark:text-green-400">${(analytics.totalRevenue / 1000).toFixed(0)}k</p>
          <p className="text-xs text-gray-500 dark:text-gray-500 mt-2">
            Avg: ${analytics.averageCustomerValue.toFixed(0)}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800 p-4">
          <p className="text-sm font-medium text-gray-600 dark:text-gray-400 mb-2">Repeat Rate</p>
          <p className="text-3xl font-bold text-blue-600 dark:text-blue-400">{analytics.repeatPurchaseRate}%</p>
          <p className="text-xs text-gray-500 dark:text-gray-500 mt-2">
            Churn: {analytics.churnedCustomers}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800 p-4">
          <p className="text-sm font-medium text-gray-600 dark:text-gray-400 mb-2">Monthly Growth</p>
          <p className={clsx('text-3xl font-bold', analytics.monthlyGrowth >= 0 ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400')}>
            {analytics.monthlyGrowth >= 0 ? '+' : ''}{analytics.monthlyGrowth}%
          </p>
          <p className="text-xs text-gray-500 dark:text-gray-500 mt-2">
            VIP: {analytics.vipCustomers}
          </p>
        </div>
      </div>

      {/* Customer Metrics Table */}
      <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800">
        {/* Header */}
        <div className="p-6 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
          <h2 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
            <Users className="w-5 h-5" />
            Customer Segments
          </h2>
          <button
            onClick={() => setShowFilters(!showFilters)}
            className={clsx(
              'inline-flex items-center gap-2 px-3 py-2 rounded-lg font-medium transition-colors',
              showFilters
                ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/20 dark:text-blue-400'
                : 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300 hover:bg-gray-200'
            )}
          >
            <Filter className="w-4 h-4" />
            Filters
          </button>
        </div>

        {/* Filters */}
        {showFilters && (
          <div className="p-4 border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-800 space-y-3">
            <div className="relative">
              <Search className="absolute left-3 top-3 w-4 h-4 text-gray-400" />
              <input
                type="text"
                placeholder="Search customer name or email..."
                value={searchTerm}
                onChange={e => setSearchTerm(e.target.value)}
                className="w-full pl-10 pr-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-900 text-gray-900 dark:text-white"
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <select
                value={filterStatus}
                onChange={e => setFilterStatus(e.target.value)}
                className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-900 text-gray-900 dark:text-white text-sm"
              >
                <option value="">All Statuses</option>
                <option value="vip">VIP</option>
                <option value="active">Active</option>
                <option value="at-risk">At Risk</option>
                <option value="inactive">Inactive</option>
              </select>

              <select
                value={sortBy}
                onChange={e => setSortBy(e.target.value as any)}
                className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-900 text-gray-900 dark:text-white text-sm"
              >
                <option value="totalSpent">Sort by Spending</option>
                <option value="totalOrders">Sort by Orders</option>
                <option value="retentionScore">Sort by Retention</option>
              </select>
            </div>

            {(searchTerm || filterStatus) && (
              <button
                onClick={() => {
                  setSearchTerm('');
                  setFilterStatus('');
                }}
                className="text-sm text-blue-600 dark:text-blue-400 hover:text-blue-700 font-medium flex items-center gap-1"
              >
                <X className="w-4 h-4" />
                Clear Filters
              </button>
            )}
          </div>
        )}

        {/* Table */}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-800">
                <th className="px-6 py-3 text-left font-semibold text-gray-900 dark:text-white">Customer</th>
                <th className="px-6 py-3 text-left font-semibold text-gray-900 dark:text-white">Status</th>
                <th className="px-6 py-3 text-right font-semibold text-gray-900 dark:text-white">Orders</th>
                <th className="px-6 py-3 text-right font-semibold text-gray-900 dark:text-white">Total Spent</th>
                <th className="px-6 py-3 text-center font-semibold text-gray-900 dark:text-white">Retention</th>
                <th className="px-6 py-3 text-center font-semibold text-gray-900 dark:text-white">Last Order</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-800">
              {loading ? (
                <tr>
                  <td colSpan={6} className="px-6 py-8 text-center text-gray-500 dark:text-gray-400">
                    Loading customer data...
                  </td>
                </tr>
              ) : filteredMetrics.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-6 py-8 text-center text-gray-500 dark:text-gray-400">
                    No customers found
                  </td>
                </tr>
              ) : (
                filteredMetrics.map(metric => (
                  <tr key={metric.customerId} className="hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
                    <td className="px-6 py-3">
                      <div>
                        <p className="font-medium text-gray-900 dark:text-white">{metric.customerName}</p>
                        <p className="text-xs text-gray-500 dark:text-gray-400">{metric.email}</p>
                      </div>
                    </td>
                    <td className="px-6 py-3">
                      <span className={clsx('px-2 py-1 rounded-full text-xs font-medium', getStatusColor(metric.status))}>
                        {metric.status}
                      </span>
                    </td>
                    <td className="px-6 py-3 text-right">
                      <span className="font-medium text-gray-900 dark:text-white">{metric.totalOrders}</span>
                    </td>
                    <td className="px-6 py-3 text-right">
                      <span className="font-medium text-gray-900 dark:text-white">${metric.totalSpent.toFixed(2)}</span>
                    </td>
                    <td className="px-6 py-3 text-center">
                      <div className="flex items-center justify-center gap-2">
                        <div className="w-full max-w-xs bg-gray-200 dark:bg-gray-700 rounded-full h-2">
                          <div
                            className={clsx(
                              'h-2 rounded-full transition-all',
                              metric.retentionScore >= 80 ? 'bg-green-600' : metric.retentionScore >= 60 ? 'bg-blue-600' : metric.retentionScore >= 40 ? 'bg-yellow-600' : 'bg-red-600'
                            )}
                            style={{ width: `${metric.retentionScore}%` }}
                          />
                        </div>
                        <span className={clsx('text-xs font-semibold', getRetentionColor(metric.retentionScore))}>
                          {metric.retentionScore}%
                        </span>
                      </div>
                    </td>
                    <td className="px-6 py-3 text-center">
                      <span className="text-xs text-gray-500 dark:text-gray-400">
                        {metric.lastOrderDate ? new Date(metric.lastOrderDate).toLocaleDateString() : 'N/A'}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Export */}
        {filteredMetrics.length > 0 && (
          <div className="p-4 border-t border-gray-200 dark:border-gray-800 flex justify-end gap-2">
            <button
              onClick={() => onExportMetrics('csv')}
              className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 font-medium text-sm"
            >
              Export as CSV
            </button>
            <button
              onClick={() => onExportMetrics('pdf')}
              className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 font-medium text-sm"
            >
              Export as PDF
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
