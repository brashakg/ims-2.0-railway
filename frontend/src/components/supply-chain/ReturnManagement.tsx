// ============================================================================
// IMS 2.0 - Return Management (RMA)
// ============================================================================
// Manage product returns with RMA tracking and refunds

import { useState } from 'react';
import { Plus, Search, Edit2, Trash2, CheckCircle, AlertCircle, FileText, DollarSign } from 'lucide-react';
import clsx from 'clsx';

export interface RMAItem {
  id: string;
  invoiceNumber: string;
  originalPrice: number;
  quantity: number;
  reason: string;
}

export interface RMA {
  id: string;
  rmaNumber: string;
  customerId: string;
  customerName: string;
  items: RMAItem[];
  createdDate: string;
  approvedDate?: string;
  status: 'pending' | 'approved' | 'received' | 'inspected' | 'refunded' | 'rejected';
  refundAmount?: number;
  refundMethod?: 'credit-card' | 'store-credit' | 'exchange';
  inspectionNotes?: string;
  approvedBy?: string;
  createdAt: string;
}

interface ReturnManagementProps {
  returns: RMA[];
  onCreateReturn: (rma: Omit<RMA, 'id' | 'createdAt'>) => Promise<void>;
  onUpdateReturn: (rma: RMA) => Promise<void>;
  onDeleteReturn: (id: string) => Promise<void>;
  onApproveReturn: (id: string, approvedBy: string) => Promise<void>;
  onRejectReturn: (id: string, reason: string) => Promise<void>;
  onProcessRefund: (id: string, method: string) => Promise<void>;
  loading?: boolean;
}

export function ReturnManagement({
  returns,
  onCreateReturn,
  onUpdateReturn,
  onDeleteReturn,
  onApproveReturn,
  onRejectReturn,
  onProcessRefund,
  loading = false,
}: ReturnManagementProps) {
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showRefundModal, setShowRefundModal] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [selectedForRefund, setSelectedForRefund] = useState<RMA | null>(null);
  const [refundMethod, setRefundMethod] = useState<'credit-card' | 'store-credit' | 'exchange'>('credit-card');
  const [formData, setFormData] = useState<Partial<RMA>>({});

  const filteredReturns = returns.filter(rma =>
    rma.rmaNumber.toLowerCase().includes(searchTerm.toLowerCase()) ||
    rma.customerName.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const handleSave = async () => {
    if (!formData.rmaNumber || !formData.customerId || !formData.items || formData.items.length === 0) {
      alert('Please fill in all required fields');
      return;
    }

    if (editingId) {
      await Promise.resolve(onUpdateReturn({
        ...formData,
        id: editingId,
        createdAt: formData.createdAt || '',
      } as RMA));
    } else {
      await Promise.resolve(onCreateReturn({
        ...formData,
        createdAt: new Date().toISOString(),
        rmaNumber: `RMA-${Date.now()}`,
      } as any));
    }

    setFormData({});
    setEditingId(null);
    setShowCreateModal(false);
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'pending':
        return 'bg-yellow-100 text-yellow-700';
      case 'approved':
        return 'bg-blue-100 text-blue-700';
      case 'received':
        return 'bg-purple-100 text-purple-700';
      case 'inspected':
        return 'bg-indigo-100 text-indigo-700';
      case 'refunded':
        return 'bg-green-100 text-green-700';
      case 'rejected':
        return 'bg-red-100 text-red-700';
      default:
        return 'bg-gray-100 text-gray-700';
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'pending':
        return <AlertCircle className="w-4 h-4" />;
      case 'approved':
      case 'received':
      case 'inspected':
      case 'refunded':
        return <CheckCircle className="w-4 h-4" />;
      case 'rejected':
        return <AlertCircle className="w-4 h-4" />;
      default:
        return <FileText className="w-4 h-4" />;
    }
  };

  const calculateTotalRefund = (rma: RMA) => {
    return rma.items.reduce((sum, item) => sum + item.originalPrice * item.quantity, 0);
  };

  return (
    <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800">
      {/* Header */}
      <div className="p-6 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
            <FileText className="w-5 h-5" />
            Returns Management
          </h2>
          <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
            {filteredReturns.length} of {returns.length} RMAs
          </p>
        </div>
        <button
          onClick={() => {
            setEditingId(null);
            setFormData({ items: [], status: 'pending' });
            setShowCreateModal(true);
          }}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium"
        >
          <Plus className="w-4 h-4" />
          New Return
        </button>
      </div>

      {/* Search */}
      <div className="p-4 border-b border-gray-200 dark:border-gray-800">
        <div className="relative">
          <Search className="absolute left-3 top-3 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search by RMA number or customer..."
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
          />
        </div>
      </div>

      {/* Returns List */}
      <div className="divide-y divide-gray-200 dark:divide-gray-800">
        {loading ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <p>Loading returns...</p>
          </div>
        ) : filteredReturns.length === 0 ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <FileText className="w-12 h-12 mx-auto mb-3 opacity-50" />
            <p>No returns found</p>
          </div>
        ) : (
          filteredReturns.map(rma => {
            const refundAmount = calculateTotalRefund(rma);
            return (
              <div key={rma.id} className="p-4 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
                <div className="flex items-start justify-between gap-4 mb-3">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <h3 className="font-semibold text-gray-900 dark:text-white">{rma.rmaNumber}</h3>
                      <span className={clsx('inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-medium', getStatusColor(rma.status))}>
                        {getStatusIcon(rma.status)}
                        {rma.status}
                      </span>
                    </div>
                    <p className="text-sm text-gray-600 dark:text-gray-400">Customer: {rma.customerName}</p>
                  </div>
                  <div className="text-right">
                    <p className="flex items-center gap-1 font-semibold text-gray-900 dark:text-white">
                      <DollarSign className="w-4 h-4" />
                      ${refundAmount.toFixed(2)}
                    </p>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {rma.items.length} item(s)
                    </p>
                  </div>
                </div>

                {/* Items */}
                <div className="text-sm text-gray-600 dark:text-gray-400 mb-3 pl-4 border-l-2 border-gray-200 dark:border-gray-700">
                  {rma.items.slice(0, 2).map((item, idx) => (
                    <p key={idx}>Invoice {item.invoiceNumber}: ×{item.quantity} @ ${item.originalPrice.toFixed(2)}</p>
                  ))}
                  {rma.items.length > 2 && <p>+{rma.items.length - 2} more item(s)</p>}
                </div>

                {/* Actions */}
                <div className="flex items-center gap-2 flex-wrap">
                  {rma.status === 'pending' && (
                    <>
                      <button
                        onClick={() => onApproveReturn(rma.id, 'current_user')}
                        className="px-3 py-1 bg-green-600 text-white rounded text-sm hover:bg-green-700 font-medium"
                      >
                        Approve
                      </button>
                      <button
                        onClick={() => onRejectReturn(rma.id, 'Customer request')}
                        className="px-3 py-1 bg-red-600 text-white rounded text-sm hover:bg-red-700 font-medium"
                      >
                        Reject
                      </button>
                    </>
                  )}
                  {rma.status === 'inspected' && (
                    <button
                      onClick={() => {
                        setSelectedForRefund(rma);
                        setShowRefundModal(true);
                      }}
                      className="px-3 py-1 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 font-medium"
                    >
                      Process Refund
                    </button>
                  )}
                  <button
                    onClick={() => {
                      setFormData(rma);
                      setEditingId(rma.id);
                      setShowCreateModal(true);
                    }}
                    className="p-2 hover:bg-amber-100 dark:hover:bg-amber-900/20 rounded-lg text-amber-600 dark:text-amber-400"
                    title="Edit"
                  >
                    <Edit2 className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => {
                      if (confirm(`Delete RMA ${rma.rmaNumber}?`)) {
                        onDeleteReturn(rma.id);
                      }
                    }}
                    className="p-2 hover:bg-red-100 dark:hover:bg-red-900/20 rounded-lg text-red-600 dark:text-red-400"
                    title="Delete"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Create/Edit Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" onClick={() => setShowCreateModal(false)}>
          <div className="bg-white dark:bg-gray-900 rounded-lg shadow-lg p-6 max-w-2xl w-full max-h-96 overflow-y-auto" onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-4">
              {editingId ? 'Edit Return' : 'Create New Return'}
            </h2>

            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <input
                  type="text"
                  placeholder="RMA Number *"
                  value={formData.rmaNumber || ''}
                  onChange={e => setFormData({ ...formData, rmaNumber: e.target.value })}
                  disabled={editingId !== null}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white disabled:opacity-50"
                />
                <input
                  type="text"
                  placeholder="Customer Name *"
                  value={formData.customerName || ''}
                  onChange={e => setFormData({ ...formData, customerName: e.target.value })}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                />
                <input
                  type="text"
                  placeholder="Customer ID"
                  value={formData.customerId || ''}
                  onChange={e => setFormData({ ...formData, customerId: e.target.value })}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                />
                <input
                  type="date"
                  placeholder="Return Date"
                  value={formData.createdDate || ''}
                  onChange={e => setFormData({ ...formData, createdDate: e.target.value })}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                />
              </div>

              <textarea
                placeholder="Reason for return"
                value={formData.inspectionNotes || ''}
                onChange={e => setFormData({ ...formData, inspectionNotes: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                rows={3}
              />

              <div className="flex gap-2">
                <button
                  onClick={() => setShowCreateModal(false)}
                  className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSave}
                  className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
                >
                  {editingId ? 'Update' : 'Create'} Return
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Refund Modal */}
      {showRefundModal && selectedForRefund && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" onClick={() => setShowRefundModal(false)}>
          <div className="bg-white dark:bg-gray-900 rounded-lg shadow-lg p-6 max-w-md w-full" onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-4 flex items-center gap-2">
              <DollarSign className="w-5 h-5" />
              Process Refund
            </h2>

            <div className="space-y-4">
              <div className="p-3 bg-blue-50 dark:bg-blue-900/20 rounded-lg">
                <p className="text-sm text-gray-700 dark:text-gray-300">Refund Amount:</p>
                <p className="text-2xl font-bold text-blue-600 dark:text-blue-400">
                  ${calculateTotalRefund(selectedForRefund).toFixed(2)}
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Refund Method *
                </label>
                <select
                  value={refundMethod}
                  onChange={e => setRefundMethod(e.target.value as any)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                >
                  <option value="credit-card">Credit Card Refund</option>
                  <option value="store-credit">Store Credit</option>
                  <option value="exchange">Exchange for Product</option>
                </select>
              </div>

              <div className="flex gap-2">
                <button
                  onClick={() => setShowRefundModal(false)}
                  className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
                >
                  Cancel
                </button>
                <button
                  onClick={() => {
                    onProcessRefund(selectedForRefund.id, refundMethod);
                    setShowRefundModal(false);
                    setSelectedForRefund(null);
                  }}
                  className="flex-1 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 font-medium"
                >
                  Process Refund
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
