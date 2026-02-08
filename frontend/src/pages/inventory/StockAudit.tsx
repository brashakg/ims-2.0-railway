// ============================================================================
// IMS 2.0 - Stock Audit Management
// ============================================================================
// Physical stock count, cycle count scheduling, variance analysis, shrinkage tracking

import { useState } from 'react';
import { Plus, BarChart3, AlertCircle, CheckCircle, Clock } from 'lucide-react';
import clsx from 'clsx';

interface StockAudit {
  id: string;
  audit_number: string;
  category: string;
  zone?: string;
  status: 'scheduled' | 'in_progress' | 'completed';
  scheduled_date: string;
  created_at: string;
  created_by: string;
  items_counted: number;
  variance_percentage?: number;
  shrinkage_percentage?: number;
}

interface AuditVariance {
  product_id: string;
  product_name: string;
  system_quantity: number;
  physical_quantity: number;
  variance: number;
  variance_percentage: number;
}

const MOCK_AUDITS: StockAudit[] = [
  {
    id: 'audit-001',
    audit_number: 'AUDIT-2024-001',
    category: 'Frames',
    zone: 'Zone A',
    status: 'completed',
    scheduled_date: '2024-02-01',
    created_at: '2024-01-28T10:00:00Z',
    created_by: 'Admin',
    items_counted: 245,
    variance_percentage: 2.86,
    shrinkage_percentage: 0.8,
  },
  {
    id: 'audit-002',
    audit_number: 'AUDIT-2024-002',
    category: 'Lenses',
    zone: 'Zone B',
    status: 'in_progress',
    scheduled_date: '2024-02-05',
    created_at: '2024-02-02T14:30:00Z',
    created_by: 'Admin',
    items_counted: 128,
  },
  {
    id: 'audit-003',
    audit_number: 'AUDIT-2024-003',
    category: 'Accessories',
    zone: 'Zone C',
    status: 'scheduled',
    scheduled_date: '2024-02-15',
    created_at: '2024-02-08T09:00:00Z',
    created_by: 'Admin',
    items_counted: 0,
  },
];

const AUDIT_VARIANCES: AuditVariance[] = [
  {
    product_id: 'prod-001',
    product_name: 'Frame Model A',
    system_quantity: 100,
    physical_quantity: 97,
    variance: -3,
    variance_percentage: -3.0,
  },
  {
    product_id: 'prod-003',
    product_name: 'Lens Case',
    system_quantity: 145,
    physical_quantity: 141,
    variance: -4,
    variance_percentage: -2.76,
  },
];

const getStatusColor = (status: string) => {
  switch (status) {
    case 'scheduled':
      return 'bg-gray-700 text-gray-300';
    case 'in_progress':
      return 'bg-blue-900 text-blue-300';
    case 'completed':
      return 'bg-green-900 text-green-300';
    default:
      return 'bg-gray-700 text-gray-300';
  }
};

const getStatusIcon = (status: string) => {
  switch (status) {
    case 'scheduled':
      return <Clock className="w-4 h-4" />;
    case 'in_progress':
      return <BarChart3 className="w-4 h-4" />;
    case 'completed':
      return <CheckCircle className="w-4 h-4" />;
    default:
      return <Clock className="w-4 h-4" />;
  }
};

export function StockAudit() {
  const [activeTab, setActiveTab] = useState<'audits' | 'variance' | 'shrinkage' | 'schedule'>('audits');
  const [selectedAudit, setSelectedAudit] = useState<string | null>(null);

  const completedAudits = MOCK_AUDITS.filter(a => a.status === 'completed');
  const inProgressAudits = MOCK_AUDITS.filter(a => a.status === 'in_progress');

  const avgShrinkage = (completedAudits.reduce((sum, a) => sum + (a.shrinkage_percentage || 0), 0) / completedAudits.length).toFixed(2);
  const totalVariance = AUDIT_VARIANCES.reduce((sum, v) => sum + Math.abs(v.variance), 0);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Stock Audit</h1>
          <p className="text-gray-400">Physical stock count and variance analysis</p>
        </div>
        <button className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-semibold flex items-center gap-2">
          <Plus className="w-5 h-5" />
          Schedule Audit
        </button>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-4 gap-4">
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Total Audits</p>
          <p className="text-2xl font-bold text-white">{MOCK_AUDITS.length}</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">In Progress</p>
          <p className="text-2xl font-bold text-blue-400">{inProgressAudits.length}</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Completed</p>
          <p className="text-2xl font-bold text-green-400">{completedAudits.length}</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Avg Shrinkage</p>
          <p className="text-2xl font-bold text-orange-400">{avgShrinkage}%</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-700">
        {(['audits', 'variance', 'shrinkage', 'schedule'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={clsx(
              'px-4 py-3 font-medium border-b-2 transition-colors',
              activeTab === tab
                ? 'border-blue-500 text-blue-400'
                : 'border-transparent text-gray-400 hover:text-gray-300'
            )}
          >
            {tab === 'audits' ? 'Audits' : tab === 'variance' ? 'Variance Analysis' : tab === 'shrinkage' ? 'Shrinkage Report' : 'Audit Schedule'}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {activeTab === 'audits' && (
        <div className="space-y-4">
          {MOCK_AUDITS.map((audit) => (
            <div
              key={audit.id}
              onClick={() => setSelectedAudit(selectedAudit === audit.id ? null : audit.id)}
              className={clsx(
                'rounded-lg p-4 border transition-colors cursor-pointer',
                selectedAudit === audit.id
                  ? 'bg-blue-900/20 border-blue-600'
                  : 'bg-gray-800 border-gray-700 hover:border-gray-600'
              )}
            >
              <div className="flex items-start justify-between mb-3">
                <div className="flex-1">
                  <p className="text-white font-semibold">{audit.audit_number}</p>
                  <p className="text-gray-400 text-sm">
                    {audit.category} {audit.zone && `- ${audit.zone}`}
                  </p>
                </div>
                <span className={clsx('px-3 py-1 rounded-full text-xs font-semibold flex items-center gap-1', getStatusColor(audit.status))}>
                  {getStatusIcon(audit.status)}
                  {audit.status === 'scheduled' ? 'Scheduled' : audit.status === 'in_progress' ? 'In Progress' : 'Completed'}
                </span>
              </div>

              <div className="grid grid-cols-4 gap-4 mb-3 pb-3 border-b border-gray-700">
                <div>
                  <p className="text-gray-400 text-xs mb-1">Scheduled Date</p>
                  <p className="text-white font-semibold">{new Date(audit.scheduled_date).toLocaleDateString()}</p>
                </div>
                <div>
                  <p className="text-gray-400 text-xs mb-1">Items Counted</p>
                  <p className="text-white font-semibold">{audit.items_counted}</p>
                </div>
                <div>
                  <p className="text-gray-400 text-xs mb-1">Created By</p>
                  <p className="text-white font-semibold">{audit.created_by}</p>
                </div>
                <div>
                  <p className="text-gray-400 text-xs mb-1">Created Date</p>
                  <p className="text-white font-semibold">{new Date(audit.created_at).toLocaleDateString()}</p>
                </div>
              </div>

              {selectedAudit === audit.id && (
                <div className="space-y-3 pt-3">
                  {audit.status === 'completed' && (
                    <>
                      <div className="grid grid-cols-2 gap-3">
                        <div className="bg-gray-700 rounded p-3">
                          <p className="text-gray-400 text-xs mb-1">Variance</p>
                          <p className={clsx('font-semibold', Math.abs(audit.variance_percentage || 0) > 5 ? 'text-orange-400' : 'text-green-400')}>
                            {audit.variance_percentage?.toFixed(2)}%
                          </p>
                        </div>
                        <div className="bg-gray-700 rounded p-3">
                          <p className="text-gray-400 text-xs mb-1">Shrinkage</p>
                          <p className={clsx('font-semibold', (audit.shrinkage_percentage || 0) > 1 ? 'text-orange-400' : 'text-green-400')}>
                            {audit.shrinkage_percentage?.toFixed(2)}%
                          </p>
                        </div>
                      </div>
                    </>
                  )}

                  {audit.status === 'in_progress' && (
                    <button className="w-full px-3 py-2 bg-green-600 hover:bg-green-700 text-white rounded text-sm font-semibold flex items-center justify-center gap-2">
                      <CheckCircle className="w-4 h-4" />
                      Complete Count
                    </button>
                  )}

                  {audit.status === 'scheduled' && (
                    <button className="w-full px-3 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded text-sm font-semibold flex items-center justify-center gap-2">
                      <BarChart3 className="w-4 h-4" />
                      Start Audit
                    </button>
                  )}

                  {audit.status === 'completed' && (
                    <button className="w-full px-3 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded text-sm font-semibold">
                      View Full Report
                    </button>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {activeTab === 'variance' && (
        <div className="space-y-4">
          <div className="bg-orange-900/30 border border-orange-700 rounded-lg p-4 flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-orange-400 flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-orange-300 font-semibold">Variance Analysis</p>
              <p className="text-orange-300 text-sm">Discrepancies between system stock and physical count</p>
            </div>
          </div>

          <div className="bg-gray-800 rounded-lg p-6 border border-gray-700">
            <h3 className="text-lg font-semibold text-white mb-4">Latest Audit Variance - AUDIT-2024-001</h3>

            <div className="grid grid-cols-3 gap-4 mb-6 pb-6 border-b border-gray-700">
              <div>
                <p className="text-gray-400 text-sm mb-1">Total Items (System)</p>
                <p className="text-white font-semibold text-xl">245</p>
              </div>
              <div>
                <p className="text-gray-400 text-sm mb-1">Total Items (Physical)</p>
                <p className="text-white font-semibold text-xl">238</p>
              </div>
              <div>
                <p className="text-gray-400 text-sm mb-1">Variance</p>
                <p className="text-orange-400 font-semibold text-xl">-7 items (2.86%)</p>
              </div>
            </div>

            <div className="space-y-3">
              {AUDIT_VARIANCES.map((variance) => (
                <div key={variance.product_id} className="bg-gray-700 rounded p-4">
                  <div className="flex items-start justify-between mb-2">
                    <div>
                      <p className="text-white font-semibold">{variance.product_name}</p>
                      <p className="text-gray-400 text-xs">Product ID: {variance.product_id}</p>
                    </div>
                    <span className={clsx(
                      'px-2 py-1 rounded text-xs font-semibold',
                      variance.variance < 0 ? 'bg-red-900 text-red-300' : 'bg-green-900 text-green-300'
                    )}>
                      {variance.variance > 0 ? '+' : ''}{variance.variance} ({variance.variance_percentage > 0 ? '+' : ''}{variance.variance_percentage.toFixed(2)}%)
                    </span>
                  </div>

                  <div className="flex items-center justify-between text-sm">
                    <span className="text-gray-400">System: {variance.system_quantity} | Physical: {variance.physical_quantity}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {activeTab === 'shrinkage' && (
        <div className="space-y-4">
          <div className="bg-red-900/30 border border-red-700 rounded-lg p-4 flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-red-300 font-semibold">Shrinkage Report</p>
              <p className="text-red-300 text-sm">Inventory loss due to damage, theft, or miscounting</p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            {completedAudits.map((audit) => (
              <div key={audit.id} className="bg-gray-800 rounded-lg p-4 border border-gray-700">
                <p className="text-white font-semibold mb-3">{audit.audit_number}</p>

                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-gray-400 text-sm">Shrinkage %</span>
                    <span className={clsx(
                      'font-semibold',
                      (audit.shrinkage_percentage || 0) > 1 ? 'text-orange-400' : 'text-green-400'
                    )}>
                      {audit.shrinkage_percentage?.toFixed(2)}%
                    </span>
                  </div>

                  <div className="w-full bg-gray-700 rounded-full h-2 overflow-hidden">
                    <div
                      className={clsx(
                        'h-full',
                        (audit.shrinkage_percentage || 0) > 1 ? 'bg-orange-500' : 'bg-green-500'
                      )}
                      style={{ width: `${Math.min((audit.shrinkage_percentage || 0) * 10, 100)}%` }}
                    />
                  </div>

                  <div className="flex items-center justify-between text-xs">
                    <span className="text-gray-400">Category: {audit.category}</span>
                    <span className="text-gray-400">{audit.zone}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className="bg-gray-800 rounded-lg p-6 border border-gray-700">
            <h3 className="text-lg font-semibold text-white mb-4">Shrinkage Analysis</h3>
            <div className="space-y-3">
              <div className="flex items-center justify-between p-3 bg-gray-700 rounded">
                <span className="text-gray-400">Average Shrinkage Rate</span>
                <span className="text-white font-semibold">{avgShrinkage}%</span>
              </div>
              <div className="flex items-center justify-between p-3 bg-gray-700 rounded">
                <span className="text-gray-400">Total Variance Units</span>
                <span className="text-white font-semibold">{totalVariance}</span>
              </div>
              <div className="flex items-center justify-between p-3 bg-gray-700 rounded">
                <span className="text-gray-400">Estimated Loss Value</span>
                <span className="text-orange-400 font-semibold">₹{(totalVariance * 500).toLocaleString('en-IN')}</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'schedule' && (
        <div className="space-y-4">
          <div className="bg-blue-900/30 border border-blue-700 rounded-lg p-4">
            <p className="text-blue-300 text-sm font-semibold">Annual Audit Schedule</p>
            <p className="text-blue-300 text-sm mt-1">
              Scheduled monthly cycle counts for continuous stock validation and variance detection
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {[
              { month: 'February', category: 'Frames', zone: 'Zone A-B', status: 'completed' },
              { month: 'February', category: 'Lenses', zone: 'Zone C-D', status: 'in_progress' },
              { month: 'March', category: 'Accessories', zone: 'Zone E', status: 'scheduled' },
              { month: 'March', category: 'Frames', zone: 'Zone A-B', status: 'scheduled' },
              { month: 'April', category: 'Lenses', zone: 'Zone C-D', status: 'scheduled' },
              { month: 'April', category: 'Accessories', zone: 'Zone E', status: 'scheduled' },
            ].map((schedule, idx) => (
              <div key={idx} className="bg-gray-800 rounded-lg p-4 border border-gray-700">
                <div className="flex items-start justify-between mb-2">
                  <div>
                    <p className="text-white font-semibold">{schedule.month}</p>
                    <p className="text-gray-400 text-sm">{schedule.category}</p>
                  </div>
                  <span className={clsx('px-2 py-1 rounded text-xs font-semibold', getStatusColor(schedule.status))}>
                    {schedule.status === 'completed' ? 'Done' : schedule.status === 'in_progress' ? 'In Progress' : 'Scheduled'}
                  </span>
                </div>
                <p className="text-gray-400 text-xs">{schedule.zone}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default StockAudit;
