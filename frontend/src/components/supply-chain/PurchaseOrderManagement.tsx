// ============================================================================
// IMS 2.0 - Purchase Order Management
// ============================================================================
// Automate purchase orders with vendor selection, approval workflows

import { useState } from 'react';
import { Plus, Search, Edit2, Trash2, CheckCircle, Clock, AlertCircle, FileText } from 'lucide-react';
import clsx from 'clsx';

export interface PurchaseOrderItem {
  id: string;
  productId: string;
  productName: string;
  quantity: number;
  unitPrice: number;
  totalPrice: number;
}

export interface PurchaseOrder {
  id: string;
  poNumber: string;
  vendorId: string;
  vendorName: string;
  items: PurchaseOrderItem[];
  orderDate: string;
  expectedDelivery: string;
  status: 'draft' | 'approved' | 'sent' | 'partial-received' | 'received' | 'cancelled';
  subtotal: number;
  tax: number;
  total: number;
  notes?: string;
  approvedBy?: string;
  createdAt: string;
}

interface PurchaseOrderManagementProps {
  orders: PurchaseOrder[];
  vendors: { id: string; name: string }[];
  onCreateOrder: (order: Omit<PurchaseOrder, 'id' | 'createdAt'>) => Promise<void>;
  onUpdateOrder: (order: PurchaseOrder) => Promise<void>;
  onDeleteOrder: (id: string) => Promise<void>;
  onApproveOrder: (id: string, approvedBy: string) => Promise<void>;
  onReceiveOrder: (id: string) => Promise<void>;
  loading?: boolean;
}

export function PurchaseOrderManagement({
  orders,
  vendors,
  onCreateOrder,
  onUpdateOrder,
  onDeleteOrder,
  onApproveOrder,
  onReceiveOrder,
  loading = false,
}: PurchaseOrderManagementProps) {
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formData, setFormData] = useState<Partial<PurchaseOrder>>({});

  const filteredOrders = orders.filter(order =>
    order.poNumber.toLowerCase().includes(searchTerm.toLowerCase()) ||
    order.vendorName.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const handleSave = async () => {
    if (!formData.poNumber || !formData.vendorId || !formData.items || formData.items.length === 0) {
      alert('Please fill in all required fields');
      return;
    }

    const subtotal = formData.items.reduce((sum, item) => sum + item.totalPrice, 0);
    const tax = subtotal * 0.1;
    const total = subtotal + tax;

    if (editingId) {
      await Promise.resolve(onUpdateOrder({
        ...formData,
        id: editingId,
        createdAt: formData.createdAt || '',
        subtotal,
        tax,
        total,
      } as PurchaseOrder));
    } else {
      await Promise.resolve(onCreateOrder({
        ...formData,
        createdAt: new Date().toISOString(),
        poNumber: `PO-${Date.now()}`,
        subtotal,
        tax,
        total,
      } as any));
    }

    setFormData({});
    setEditingId(null);
    setShowCreateModal(false);
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'draft':
        return 'bg-gray-100 text-gray-700';
      case 'approved':
        return 'bg-blue-100 text-blue-700';
      case 'sent':
        return 'bg-purple-100 text-purple-700';
      case 'partial-received':
        return 'bg-yellow-100 text-yellow-700';
      case 'received':
        return 'bg-green-100 text-green-700';
      case 'cancelled':
        return 'bg-red-100 text-red-700';
      default:
        return 'bg-gray-100 text-gray-700';
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'draft':
        return <FileText className="w-4 h-4" />;
      case 'approved':
      case 'sent':
        return <CheckCircle className="w-4 h-4" />;
      case 'partial-received':
        return <AlertCircle className="w-4 h-4" />;
      case 'received':
        return <CheckCircle className="w-4 h-4" />;
      default:
        return <Clock className="w-4 h-4" />;
    }
  };

  return (
    <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800">
      {/* Header */}
      <div className="p-6 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
            <FileText className="w-5 h-5" />
            Purchase Orders
          </h2>
          <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
            {filteredOrders.length} of {orders.length} purchase orders
          </p>
        </div>
        <button
          onClick={() => {
            setEditingId(null);
            setFormData({ items: [], status: 'draft' });
            setShowCreateModal(true);
          }}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium"
        >
          <Plus className="w-4 h-4" />
          New PO
        </button>
      </div>

      {/* Search */}
      <div className="p-4 border-b border-gray-200 dark:border-gray-800">
        <div className="relative">
          <Search className="absolute left-3 top-3 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search by PO number or vendor..."
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
          />
        </div>
      </div>

      {/* Orders List */}
      <div className="divide-y divide-gray-200 dark:divide-gray-800">
        {loading ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <p>Loading purchase orders...</p>
          </div>
        ) : filteredOrders.length === 0 ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <FileText className="w-12 h-12 mx-auto mb-3 opacity-50" />
            <p>No purchase orders found</p>
          </div>
        ) : (
          filteredOrders.map(order => (
            <div key={order.id} className="p-4 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
              <div className="flex items-start justify-between gap-4 mb-3">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <h3 className="font-semibold text-gray-900 dark:text-white">{order.poNumber}</h3>
                    <span className={clsx('inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-medium', getStatusColor(order.status))}>
                      {getStatusIcon(order.status)}
                      {order.status}
                    </span>
                  </div>
                  <p className="text-sm text-gray-600 dark:text-gray-400">Vendor: {order.vendorName}</p>
                </div>
                <div className="text-right">
                  <p className="font-semibold text-gray-900 dark:text-white">${order.total.toFixed(2)}</p>
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    Expected: {new Date(order.expectedDelivery).toLocaleDateString()}
                  </p>
                </div>
              </div>

              {/* Items Summary */}
              <div className="text-sm text-gray-600 dark:text-gray-400 mb-3">
                <p>{order.items.length} item(s)</p>
              </div>

              {/* Actions */}
              <div className="flex items-center gap-2">
                {order.status === 'draft' && (
                  <button
                    onClick={() => onApproveOrder(order.id, 'current_user')}
                    className="px-3 py-1 bg-green-600 text-white rounded text-sm hover:bg-green-700 font-medium"
                  >
                    Approve
                  </button>
                )}
                {order.status === 'approved' && (
                  <button
                    onClick={() => Promise.resolve(onUpdateOrder({ ...order, status: 'sent' }))}
                    className="px-3 py-1 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 font-medium"
                  >
                    Mark Sent
                  </button>
                )}
                {(order.status === 'sent' || order.status === 'partial-received') && (
                  <button
                    onClick={() => onReceiveOrder(order.id)}
                    className="px-3 py-1 bg-purple-600 text-white rounded text-sm hover:bg-purple-700 font-medium"
                  >
                    Receive
                  </button>
                )}
                <button
                  onClick={() => {
                    setFormData(order);
                    setEditingId(order.id);
                    setShowCreateModal(true);
                  }}
                  className="p-2 hover:bg-amber-100 dark:hover:bg-amber-900/20 rounded-lg text-amber-600 dark:text-amber-400"
                  title="Edit"
                >
                  <Edit2 className="w-4 h-4" />
                </button>
                <button
                  onClick={() => {
                    if (confirm(`Delete PO ${order.poNumber}?`)) {
                      onDeleteOrder(order.id);
                    }
                  }}
                  className="p-2 hover:bg-red-100 dark:hover:bg-red-900/20 rounded-lg text-red-600 dark:text-red-400"
                  title="Delete"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Create/Edit Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" onClick={() => setShowCreateModal(false)}>
          <div className="bg-white dark:bg-gray-900 rounded-lg shadow-lg p-6 max-w-2xl w-full max-h-96 overflow-y-auto" onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-4">
              {editingId ? 'Edit Purchase Order' : 'Create New Purchase Order'}
            </h2>

            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <input
                  type="text"
                  placeholder="PO Number *"
                  value={formData.poNumber || ''}
                  onChange={e => setFormData({ ...formData, poNumber: e.target.value })}
                  disabled={editingId !== null}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white disabled:opacity-50"
                />
                <select
                  value={formData.vendorId || ''}
                  onChange={e => {
                    const vendor = vendors.find(v => v.id === e.target.value);
                    setFormData({
                      ...formData,
                      vendorId: e.target.value,
                      vendorName: vendor?.name || '',
                    });
                  }}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                >
                  <option value="">Select Vendor *</option>
                  {vendors.map(vendor => (
                    <option key={vendor.id} value={vendor.id}>
                      {vendor.name}
                    </option>
                  ))}
                </select>
                <input
                  type="date"
                  placeholder="Order Date"
                  value={formData.orderDate || ''}
                  onChange={e => setFormData({ ...formData, orderDate: e.target.value })}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                />
                <input
                  type="date"
                  placeholder="Expected Delivery"
                  value={formData.expectedDelivery || ''}
                  onChange={e => setFormData({ ...formData, expectedDelivery: e.target.value })}
                  className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                />
              </div>

              <textarea
                placeholder="Notes"
                value={formData.notes || ''}
                onChange={e => setFormData({ ...formData, notes: e.target.value })}
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
                  {editingId ? 'Update' : 'Create'} Order
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
