// ============================================================================
// IMS 2.0 - Stock Audit Management
// ============================================================================
// Physical stock count, variance analysis, shrinkage tracking
// Wired to /inventory/stock-count API endpoints

import { useState, useEffect } from 'react';
import { Plus, BarChart3, CheckCircle, Clock, Loader2, RefreshCw } from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { inventoryApi } from '../../services/api';

interface StockAudit {
  count_id: string;
  audit_number: string;
  category: string;
  zone?: string;
  status: 'in_progress' | 'completed';
  created_at: string;
  created_by_name: string;
  items_counted: number;
  variance_percentage?: number;
  shrinkage_percentage?: number;
  variances?: AuditVariance[];
}

interface AuditVariance {
  product_id: string;
  product_name: string;
  sku: string;
  system_quantity: number;
  physical_quantity: number;
  variance: number;
  variance_percentage: number;
}

const getStatusColor = (status: string) => {
  switch (status) {
    case 'in_progress':
      return 'bg-blue-100 text-blue-800';
    case 'completed':
      return 'bg-green-100 text-green-800';
    default:
      return 'bg-gray-100 text-gray-800';
  }
};

const getStatusIcon = (status: string) => {
  switch (status) {
    case 'in_progress':
      return <BarChart3 className="w-4 h-4" />;
    case 'completed':
      return <CheckCircle className="w-4 h-4" />;
    default:
      return <Clock className="w-4 h-4" />;
  }
};

export function StockAudit() {
  const { user } = useAuth();
  const toast = useToast();

  const [audits, setAudits] = useState<StockAudit[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedAudit, setSelectedAudit] = useState<string | null>(null);
  const [showNewAuditModal, setShowNewAuditModal] = useState(false);
  const [newCategory, setNewCategory] = useState('');
  const [newZone, setNewZone] = useState('');
  const [starting, setStarting] = useState(false);

  const storeId = user?.activeStoreId || '';

  useEffect(() => {
    if (storeId) loadAudits();
  }, [storeId]);

  const loadAudits = async () => {
    setIsLoading(true);
    try {
      const result = await inventoryApi.getStockCounts(storeId);
      const counts: StockAudit[] = (result?.counts || []).map((c: any) => ({
        count_id: c.count_id || c.id || '',
        audit_number: c.audit_number || '',
        category: c.category || 'All',
        zone: c.zone,
        status: c.status || 'in_progress',
        created_at: c.created_at || '',
        created_by_name: c.created_by_name || c.created_by || '',
        items_counted: c.items_counted || 0,
        variance_percentage: c.variance_percentage,
        shrinkage_percentage: c.shrinkage_percentage,
        variances: c.variances || [],
      }));
      setAudits(counts);
    } catch {
      toast.error('Failed to load stock counts');
    } finally {
      setIsLoading(false);
    }
  };

  const handleStartAudit = async () => {
    setStarting(true);
    try {
      await inventoryApi.startStockCount({
        category: newCategory || undefined,
        zone: newZone || undefined,
      });
      toast.success('Stock count started!');
      setShowNewAuditModal(false);
      setNewCategory('');
      setNewZone('');
      loadAudits();
    } catch {
      toast.error('Failed to start stock count');
    } finally {
      setStarting(false);
    }
  };

  const handleCompleteAudit = async (countId: string) => {
    try {
      const result = await inventoryApi.completeStockCount(countId);
      toast.success(`Stock count completed! Variance: ${result.variance_percentage || 0}%`);
      loadAudits();
    } catch {
      toast.error('Failed to complete stock count');
    }
  };

  const completedAudits = audits.filter(a => a.status === 'completed');
  const inProgressAudits = audits.filter(a => a.status === 'in_progress');

  const avgShrinkage = completedAudits.length > 0
    ? (completedAudits.reduce((sum, a) => sum + (a.shrinkage_percentage || 0), 0) / completedAudits.length).toFixed(2)
    : '0.00';

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Stock Audit</h1>
          <p className="text-sm text-gray-500">Physical stock count and variance analysis</p>
        </div>
        <div className="flex gap-2">
          <button onClick={loadAudits} disabled={isLoading} className="btn-outline text-sm flex items-center gap-2">
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            Refresh
          </button>
          <button
            onClick={() => setShowNewAuditModal(true)}
            className="px-4 py-2 bg-bv-gold-500 hover:bg-bv-gold-600 text-white rounded-lg font-semibold flex items-center gap-2"
          >
            <Plus className="w-5 h-5" />
            New Stock Count
          </button>
        </div>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
        <div className="card">
          <p className="text-sm text-gray-500 mb-1">Total Counts</p>
          <p className="text-2xl font-bold text-gray-900">{audits.length}</p>
        </div>
        <div className="card">
          <p className="text-sm text-gray-500 mb-1">In Progress</p>
          <p className="text-2xl font-bold text-blue-600">{inProgressAudits.length}</p>
        </div>
        <div className="card">
          <p className="text-sm text-gray-500 mb-1">Completed</p>
          <p className="text-2xl font-bold text-green-600">{completedAudits.length}</p>
        </div>
        <div className="card">
          <p className="text-sm text-gray-500 mb-1">Avg Shrinkage</p>
          <p className="text-2xl font-bold text-orange-600">{avgShrinkage}%</p>
        </div>
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="card flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 animate-spin text-bv-gold-500" />
        </div>
      )}

      {/* Empty state */}
      {!isLoading && audits.length === 0 && (
        <div className="card text-center py-12">
          <BarChart3 className="w-12 h-12 mx-auto mb-3 text-gray-300" />
          <p className="text-gray-500 font-medium">No stock counts yet</p>
          <p className="text-sm text-gray-400 mt-1">Start a new physical stock count to track inventory accuracy</p>
        </div>
      )}

      {/* Audit List */}
      {!isLoading && audits.length > 0 && (
        <div className="space-y-3">
          <h3 className="font-semibold text-gray-900">Stock Count Sessions</h3>
          {audits.map((audit) => (
            <div
              key={audit.count_id}
              onClick={() => setSelectedAudit(selectedAudit === audit.count_id ? null : audit.count_id)}
              className={clsx(
                'card cursor-pointer transition-all',
                selectedAudit === audit.count_id ? 'ring-2 ring-bv-gold-400' : 'hover:shadow-md'
              )}
            >
              <div className="flex items-start justify-between mb-3">
                <div>
                  <p className="font-semibold text-gray-900">{audit.audit_number}</p>
                  <p className="text-sm text-gray-500">
                    {audit.category || 'All Categories'} {audit.zone && `· ${audit.zone}`}
                  </p>
                </div>
                <span className={clsx('px-3 py-1 rounded-full text-xs font-semibold flex items-center gap-1', getStatusColor(audit.status))}>
                  {getStatusIcon(audit.status)}
                  {audit.status === 'in_progress' ? 'In Progress' : 'Completed'}
                </span>
              </div>

              <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4 text-sm">
                <div>
                  <p className="text-gray-500 text-xs">Created</p>
                  <p className="font-medium text-gray-900">{audit.created_at ? new Date(audit.created_at).toLocaleDateString('en-IN') : '-'}</p>
                </div>
                <div>
                  <p className="text-gray-500 text-xs">Items Counted</p>
                  <p className="font-medium text-gray-900">{audit.items_counted}</p>
                </div>
                <div>
                  <p className="text-gray-500 text-xs">By</p>
                  <p className="font-medium text-gray-900">{audit.created_by_name || '-'}</p>
                </div>
                {audit.status === 'completed' && (
                  <div>
                    <p className="text-gray-500 text-xs">Variance</p>
                    <p className={clsx('font-medium', Math.abs(audit.variance_percentage || 0) > 5 ? 'text-red-600' : 'text-green-600')}>
                      {audit.variance_percentage?.toFixed(2)}%
                    </p>
                  </div>
                )}
              </div>

              {/* Expanded details */}
              {selectedAudit === audit.count_id && (
                <div className="mt-4 pt-4 border-t border-gray-200 space-y-3">
                  {audit.status === 'completed' && (
                    <>
                      <div className="grid grid-cols-2 gap-3">
                        <div className="bg-gray-50 rounded-lg p-3">
                          <p className="text-xs text-gray-500">Overall Variance</p>
                          <p className={clsx('font-bold text-lg', Math.abs(audit.variance_percentage || 0) > 5 ? 'text-red-600' : 'text-green-600')}>
                            {audit.variance_percentage?.toFixed(2)}%
                          </p>
                        </div>
                        <div className="bg-gray-50 rounded-lg p-3">
                          <p className="text-xs text-gray-500">Shrinkage</p>
                          <p className={clsx('font-bold text-lg', (audit.shrinkage_percentage || 0) > 1 ? 'text-orange-600' : 'text-green-600')}>
                            {audit.shrinkage_percentage?.toFixed(2)}%
                          </p>
                        </div>
                      </div>

                      {/* Variance details */}
                      {audit.variances && audit.variances.length > 0 && (
                        <div>
                          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-2">Item Variances</p>
                          <div className="space-y-1">
                            {audit.variances.filter((v: any) => v.variance !== 0).map((v: any, i: number) => (
                              <div key={v.product_id || i} className="flex items-center justify-between text-sm bg-gray-50 rounded px-3 py-2">
                                <div>
                                  <span className="font-medium text-gray-900">{v.product_name || v.sku}</span>
                                  <span className="text-gray-400 ml-2 text-xs">Sys: {v.system_quantity} | Count: {v.physical_quantity}</span>
                                </div>
                                <span className={clsx('font-semibold', v.variance < 0 ? 'text-red-600' : 'text-green-600')}>
                                  {v.variance > 0 ? '+' : ''}{v.variance}
                                </span>
                              </div>
                            ))}
                            {audit.variances.filter((v: any) => v.variance !== 0).length === 0 && (
                              <p className="text-sm text-gray-400 italic">No variances found — perfect match!</p>
                            )}
                          </div>
                        </div>
                      )}
                    </>
                  )}

                  {audit.status === 'in_progress' && (
                    <button
                      onClick={(e) => { e.stopPropagation(); handleCompleteAudit(audit.count_id); }}
                      className="w-full px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg text-sm font-semibold flex items-center justify-center gap-2"
                    >
                      <CheckCircle className="w-4 h-4" />
                      Complete Count & Calculate Variances
                    </button>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* New Audit Modal */}
      {showNewAuditModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-md">
            <div className="p-6">
              <h3 className="text-lg font-bold text-gray-900 mb-4">Start New Stock Count</h3>

              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Category (optional)</label>
                  <select value={newCategory} onChange={(e) => setNewCategory(e.target.value)} className="input-field w-full">
                    <option value="">All Categories</option>
                    <option value="FRAMES">Frames</option>
                    <option value="SUNGLASSES">Sunglasses</option>
                    <option value="RX_LENSES">Rx Lenses</option>
                    <option value="CONTACT_LENSES">Contact Lenses</option>
                    <option value="WRIST_WATCHES">Watches</option>
                    <option value="ACCESSORIES">Accessories</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Zone (optional)</label>
                  <input
                    type="text"
                    value={newZone}
                    onChange={(e) => setNewZone(e.target.value)}
                    placeholder="e.g., Zone A, Display Wall, Shelf 1"
                    className="input-field w-full"
                  />
                </div>
              </div>

              <div className="mt-6 flex gap-3">
                <button
                  onClick={() => setShowNewAuditModal(false)}
                  className="flex-1 px-4 py-2 border border-gray-200 text-gray-700 rounded-lg hover:bg-gray-50"
                >
                  Cancel
                </button>
                <button
                  onClick={handleStartAudit}
                  disabled={starting}
                  className="flex-1 px-4 py-2 bg-bv-gold-500 hover:bg-bv-gold-600 text-white rounded-lg font-semibold flex items-center justify-center gap-2"
                >
                  {starting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                  Start Count
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default StockAudit;
