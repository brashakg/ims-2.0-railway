// ============================================================================
// IMS 2.0 - Customer Segmentation Page
// ============================================================================
// RFM Analysis: Champions, Loyal, Big Spenders, At Risk, Lost customer segments

import { useState } from 'react';
import { Users, Target, Mail, Filter } from 'lucide-react';
import clsx from 'clsx';

interface Segment {
  id: string;
  name: string;
  color: string;
  bgColor: string;
  customerCount: number;
  avgLifetimeValue: number;
  avgOrderValue: number;
  description: string;
  actionLabel: string;
  metrics: {
    label: string;
    value: string;
  }[];
}

const SEGMENTS: Segment[] = [
  {
    id: 'champions',
    name: 'Champions',
    color: 'text-green-400',
    bgColor: 'bg-green-900/30 border-green-700',
    customerCount: 145,
    avgLifetimeValue: 85000,
    avgOrderValue: 8500,
    description: 'Recent, frequent, high-value customers. Top priorities.',
    actionLabel: 'VIP Engagement',
    metrics: [
      { label: 'Frequency', value: '4.2x/month' },
      { label: 'Retention', value: '94%' },
      { label: 'ROI', value: '320%' },
    ],
  },
  {
    id: 'loyal',
    name: 'Loyal Customers',
    color: 'text-blue-400',
    bgColor: 'bg-blue-900/30 border-blue-700',
    customerCount: 312,
    avgLifetimeValue: 42000,
    avgOrderValue: 4200,
    description: 'Consistent repeat buyers with steady engagement.',
    actionLabel: 'Retention Program',
    metrics: [
      { label: 'Frequency', value: '2.1x/month' },
      { label: 'Retention', value: '87%' },
      { label: 'Churn Risk', value: '8%' },
    ],
  },
  {
    id: 'big_spenders',
    name: 'Big Spenders',
    color: 'text-yellow-400',
    bgColor: 'bg-yellow-900/30 border-yellow-700',
    customerCount: 89,
    avgLifetimeValue: 125000,
    avgOrderValue: 12500,
    description: 'High-value customers regardless of recency.',
    actionLabel: 'Premium Services',
    metrics: [
      { label: 'Avg Order', value: '₹12,500' },
      { label: 'LTV', value: '₹125K' },
      { label: 'Growth', value: '15%' },
    ],
  },
  {
    id: 'at_risk',
    name: 'At Risk',
    color: 'text-orange-400',
    bgColor: 'bg-orange-900/30 border-orange-700',
    customerCount: 234,
    avgLifetimeValue: 18000,
    avgOrderValue: 1800,
    description: 'Were regular, now declining engagement.',
    actionLabel: 'Win-Back Campaign',
    metrics: [
      { label: 'Days Inactive', value: '120+' },
      { label: 'Churn Risk', value: '72%' },
      { label: 'Recovery', value: '35%' },
    ],
  },
  {
    id: 'lost',
    name: 'Lost Customers',
    color: 'text-red-400',
    bgColor: 'bg-red-900/30 border-red-700',
    customerCount: 156,
    avgLifetimeValue: 5200,
    avgOrderValue: 520,
    description: 'No activity in 12+ months.',
    actionLabel: 'Re-activation',
    metrics: [
      { label: 'Inactivity', value: '300+ days' },
      { label: 'LTV', value: '₹5.2K' },
      { label: 'Revival Rate', value: '12%' },
    ],
  },
];

export function CustomerSegmentation() {
  const [selectedSegment, setSelectedSegment] = useState<string | null>(null);
  const [filterStore, setFilterStore] = useState('all');

  const totalCustomers = SEGMENTS.reduce((sum, s) => sum + s.customerCount, 0);
  const totalValue = SEGMENTS.reduce((sum, s) => sum + (s.avgLifetimeValue * s.customerCount), 0);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="space-y-2">
        <h1 className="text-3xl font-bold">Customer Segmentation</h1>
        <p className="text-gray-400">RFM Analysis: Identify customer segments and target campaigns</p>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Total Customers</p>
          <p className="text-2xl font-bold text-white">{totalCustomers}</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Total Value</p>
          <p className="text-2xl font-bold text-green-400">₹{(totalValue / 100000).toFixed(1)}L</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Avg Customer LTV</p>
          <p className="text-2xl font-bold text-blue-400">₹{(totalValue / totalCustomers).toLocaleString('en-IN', { maximumFractionDigits: 0 })}</p>
        </div>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3">
        <Filter className="w-5 h-5 text-gray-400" />
        <select
          value={filterStore}
          onChange={(e) => setFilterStore(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-white text-sm"
        >
          <option value="all">All Stores</option>
          <option value="main">Main Store</option>
          <option value="downtown">Downtown</option>
          <option value="mall">Mall Location</option>
        </select>
      </div>

      {/* Segment Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {SEGMENTS.map((segment) => (
          <div
            key={segment.id}
            onClick={() => setSelectedSegment(selectedSegment === segment.id ? null : segment.id)}
            className={clsx(
              'rounded-lg p-6 border transition-all cursor-pointer',
              selectedSegment === segment.id
                ? `${segment.bgColor} ring-2 ring-offset-2 ring-offset-gray-900`
                : 'bg-gray-800 border-gray-700 hover:border-gray-600'
            )}
          >
            {/* Header */}
            <div className="flex items-start justify-between mb-4">
              <div>
                <h3 className={clsx('text-xl font-bold mb-1', segment.color)}>{segment.name}</h3>
                <p className="text-gray-400 text-sm">{segment.description}</p>
              </div>
              <Users className={clsx('w-6 h-6', segment.color)} />
            </div>

            {/* Main Metrics */}
            <div className="grid grid-cols-2 gap-4 mb-4 pb-4 border-b border-gray-700">
              <div>
                <p className="text-gray-400 text-xs mb-1">Customers</p>
                <p className="text-2xl font-bold text-white">{segment.customerCount}</p>
                <p className={clsx('text-xs', segment.color)}>
                  {((segment.customerCount / totalCustomers) * 100).toFixed(1)}% of total
                </p>
              </div>
              <div>
                <p className="text-gray-400 text-xs mb-1">Avg LTV</p>
                <p className="text-2xl font-bold text-green-400">₹{(segment.avgLifetimeValue / 1000).toFixed(0)}K</p>
                <p className="text-xs text-gray-400">per customer</p>
              </div>
            </div>

            {/* Sub-metrics */}
            <div className="space-y-2 mb-4">
              {segment.metrics.map((metric, idx) => (
                <div key={idx} className="flex items-center justify-between">
                  <span className="text-gray-400 text-sm">{metric.label}</span>
                  <span className="text-white font-semibold">{metric.value}</span>
                </div>
              ))}
            </div>

            {/* Actions */}
            {selectedSegment === segment.id && (
              <div className="space-y-2 pt-4 border-t border-gray-700">
                <button className={clsx(
                  'w-full py-2 rounded text-sm font-semibold flex items-center justify-center gap-2 transition-colors',
                  segment.id === 'champions' ? 'bg-green-600 hover:bg-green-700 text-white' :
                  segment.id === 'loyal' ? 'bg-blue-600 hover:bg-blue-700 text-white' :
                  segment.id === 'big_spenders' ? 'bg-yellow-600 hover:bg-yellow-700 text-white' :
                  segment.id === 'at_risk' ? 'bg-orange-600 hover:bg-orange-700 text-white' :
                  'bg-red-600 hover:bg-red-700 text-white'
                )}>
                  <Target className="w-4 h-4" />
                  {segment.actionLabel}
                </button>
                <button className="w-full py-2 rounded bg-gray-700 hover:bg-gray-600 text-white text-sm font-semibold flex items-center justify-center gap-2">
                  <Mail className="w-4 h-4" />
                  View Customers
                </button>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Segment Distribution Chart */}
      <div className="bg-gray-800 rounded-lg p-6">
        <h3 className="text-lg font-semibold text-white mb-4">Customer Distribution</h3>
        <div className="space-y-3">
          {SEGMENTS.map((segment) => {
            const percentage = (segment.customerCount / totalCustomers) * 100;
            return (
              <div key={segment.id}>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm text-gray-400">{segment.name}</span>
                  <span className={clsx('text-sm font-semibold', segment.color)}>
                    {segment.customerCount} ({percentage.toFixed(1)}%)
                  </span>
                </div>
                <div className="w-full bg-gray-700 rounded-full h-2 overflow-hidden">
                  <div
                    className={clsx('h-full',
                      segment.id === 'champions' ? 'bg-green-500' :
                      segment.id === 'loyal' ? 'bg-blue-500' :
                      segment.id === 'big_spenders' ? 'bg-yellow-500' :
                      segment.id === 'at_risk' ? 'bg-orange-500' :
                      'bg-red-500'
                    )}
                    style={{ width: `${percentage}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export default CustomerSegmentation;
