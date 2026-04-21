// ============================================================================
// IMS 2.0 - Vendor Returns / Debit Notes
// ============================================================================
// Create and manage vendor returns for defective/damaged products

import { useState, useEffect, startTransition } from 'react';
import {
  Plus,
  X as XIcon,
  Package,
  DollarSign,
  Calendar,
  ChevronDown,
  Clock,
} from 'lucide-react';
import clsx from 'clsx';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import api from '../../services/api/client';

interface ReturnItem {
  product_id: string;
  product_name: string;
  quantity: number;
  reason: string;
  unit_price: number;
}

interface VendorReturn {
  return_id: string;
  vendor_id: string;
  vendor_name: string;
  store_id: string;
  items: ReturnItem[];
  return_type: 'credit_note' | 'replacement';
  status: string;
  total_value: number;
  credit_note_number: string | null;
  credit_note_amount: number | null;
  created_at: string;
  created_by: string;
  notes: string;
}

interface Vendor {
  vendor_id: string;
  legal_name: string;
  trade_name: string;
  mobile: string;
}

const RETURN_REASONS = [
  { value: 'defective', label: 'Defective' },
  { value: 'wrong_item', label: 'Wrong Item' },
  { value: 'expired', label: 'Expired' },
  { value: 'damaged_in_transit', label: 'Damaged in Transit' },
  { value: 'quality_issue', label: 'Quality Issue' },
  { value: 'not_as_ordered', label: 'Not As Ordered' },
  { value: 'other', label: 'Other' },
];

const STATUS_COLORS: Record<string, string> = {
  created: 'bg-blue-50 text-blue-700',
  approved: 'bg-cyan-50 text-cyan-700',
  shipped: 'bg-purple-50 text-purple-700',
  received_by_vendor: 'bg-orange-50 text-orange-700',
  credit_issued: 'bg-green-50 text-green-700',
  replaced: 'bg-green-50 text-green-700',
  cancelled: 'bg-red-50 text-red-700',
};

const STATUS_LABELS: Record<string, string> = {
  created: 'Created',
  approved: 'Approved',
  shipped: 'Shipped',
  received_by_vendor: 'Received by Vendor',
  credit_issued: 'Credit Issued',
  replaced: 'Replaced',
  cancelled: 'Cancelled',
};

export function VendorReturns() {
  const toast = useToast();
  const { user } = useAuth();
  const activeStoreId = user?.activeStoreId || '';

  const [activeTab, setActiveTab] = useState<'active' | 'history'>('active');
  const [isLoading, setIsLoading] = useState(true);
  const [returns, setReturns] = useState<VendorReturn[]>([]);
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [expandedReturn, setExpandedReturn] = useState<string | null>(null);

  // Form state
  const [selectedVendor, setSelectedVendor] = useState('');
  const [returnType, setReturnType] = useState<'credit_note' | 'replacement'>('credit_note');
  const [items, setItems] = useState<ReturnItem[]>([
    { product_id: '', product_name: '', quantity: 1, reason: 'defective', unit_price: 0 },
  ]);
  const [notes, setNotes] = useState('');

  // Load data on mount
  useEffect(() => {
    const loadData = async () => {
      try {
        setIsLoading(true);

        // Fetch vendor returns and vendors in parallel
        const [returnsResp, vendorsResp] = await Promise.all([
          api.get('/vendor-returns/', {
            params: { store_id: activeStoreId || undefined, limit: 100 },
          }),
          api.get('/vendors/', { params: { limit: 100 } }),
        ]);

        setReturns(returnsResp.data.returns || []);
        setVendors(vendorsResp.data.vendors || vendorsResp.data.items || []);
      } catch {
        toast.error('Failed to load vendor returns');
      } finally {
        setIsLoading(false);
      }
    };

    loadData();
  }, [activeStoreId]);

  const handleAddItem = () => {
    setItems([
      ...items,
      { product_id: '', product_name: '', quantity: 1, reason: 'defective', unit_price: 0 },
    ]);
  };

  const handleRemoveItem = (idx: number) => {
    setItems(items.filter((_, i) => i !== idx));
  };

  const handleItemChange = (idx: number, field: keyof ReturnItem, value: any) => {
    const newItems = [...items];
    newItems[idx] = { ...newItems[idx], [field]: value };
    setItems(newItems);
  };

  const handleCreateReturn = async () => {
    if (!selectedVendor || items.some(i => !i.product_name.trim() || i.quantity <= 0)) {
      toast.error('Please fill all required fields');
      return;
    }

    const vendor = vendors.find(v => v.vendor_id === selectedVendor);

    try {
      await api.post('/vendor-returns/', {
        vendor_id: selectedVendor,
        vendor_name: vendor?.legal_name || selectedVendor,
        store_id: activeStoreId,
        items: items.map(i => ({
          ...i,
          product_id: i.product_id || i.product_name.toLowerCase().replace(/\s+/g, '-'),
        })),
        return_type: returnType,
        notes,
      });

      toast.success('Vendor return created successfully');
      setShowModal(false);
      setSelectedVendor('');
      setReturnType('credit_note');
      setItems([{ product_id: '', product_name: '', quantity: 1, reason: 'defective', unit_price: 0 }]);
      setNotes('');

      // Refresh the list
      const refreshResp = await api.get('/vendor-returns/', {
        params: { store_id: activeStoreId || undefined, limit: 100 },
      });
      setReturns(refreshResp.data.returns || []);
    } catch {
      toast.error('Failed to create vendor return');
    }
  };

  const totalValue = items.reduce((sum, item) => sum + item.quantity * item.unit_price, 0);
  const activeReturns = returns.filter(r => !['credit_issued', 'replaced', 'cancelled'].includes(r.status));
  const historyReturns = returns.filter(r => ['credit_issued', 'replaced', 'cancelled'].includes(r.status));

  const displayReturns = activeTab === 'active' ? activeReturns : historyReturns;

  const handleUpdateStatus = async (returnId: string, newStatus: string) => {
    try {
      const resp = await api.patch(`/vendor-returns/${returnId}/status`, { status: newStatus });
      const updated: VendorReturn = resp.data.return;
      setReturns(prev => prev.map(r => r.return_id === returnId ? updated : r));
      toast.success(`Status updated to ${STATUS_LABELS[newStatus] || newStatus}`);
    } catch {
      toast.error('Failed to update return status');
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Vendor Returns</h1>
          <p className="text-gray-500">Manage defective products and debit notes</p>
        </div>
        <button
          onClick={() => setShowModal(true)}
          className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg transition"
        >
          <Plus className="w-5 h-5" />
          Create Return
        </button>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-4 gap-4">
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-gray-500 text-sm">Total Returns</p>
            <Package className="w-5 h-5 text-gray-500" />
          </div>
          <p className="text-2xl font-bold text-gray-900">{returns.length}</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-gray-500 text-sm">Pending Credits</p>
            <Clock className="w-5 h-5 text-yellow-500" />
          </div>
          <p className="text-2xl font-bold text-yellow-600">
            {returns.filter(r => r.status === 'received_by_vendor' || r.status === 'approved').length}
          </p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-gray-500 text-sm">Credit Value</p>
            <DollarSign className="w-5 h-5 text-green-500" />
          </div>
          <p className="text-2xl font-bold text-green-600">
            ₹{returns.reduce((sum, r) => sum + (r.credit_note_amount || 0), 0).toLocaleString()}
          </p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-gray-500 text-sm">This Month</p>
            <Calendar className="w-5 h-5 text-blue-500" />
          </div>
          <p className="text-2xl font-bold text-blue-600">
            {returns.filter(r => new Date(r.created_at).getMonth() === new Date().getMonth()).length}
          </p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-300">
        {(['active', 'history'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => startTransition(() => setActiveTab(tab))}
            className={clsx(
              'px-4 py-3 font-medium border-b-2 transition-colors',
              activeTab === tab
                ? 'border-blue-500 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            )}
          >
            {tab === 'active' ? 'Active Returns' : 'History'}
          </button>
        ))}
      </div>

      {/* Returns List */}
      <div className="space-y-4">
        {isLoading ? (
          <div className="text-center py-8 text-gray-500">Loading returns...</div>
        ) : displayReturns.length === 0 ? (
          <div className="text-center py-8 text-gray-500">
            {activeTab === 'active' ? 'No active returns' : 'No history'}
          </div>
        ) : (
          displayReturns.map((ret) => (
            <div
              key={ret.return_id}
              className="bg-white border border-gray-200 rounded-lg overflow-hidden hover:border-gray-300 transition"
            >
              {/* Header */}
              <button
                onClick={() => setExpandedReturn(expandedReturn === ret.return_id ? null : ret.return_id)}
                className="w-full px-6 py-4 flex items-center justify-between hover:bg-gray-750 transition text-left"
              >
                <div className="flex items-center gap-4 flex-1">
                  <div className="flex-1">
                    <div className="flex items-center gap-3 mb-1">
                      <h3 className="text-lg font-semibold text-gray-900">{ret.vendor_name}</h3>
                      <span className={clsx(
                        'px-2 py-1 rounded text-xs font-semibold',
                        STATUS_COLORS[ret.status]
                      )}>
                        {STATUS_LABELS[ret.status]}
                      </span>
                      <span className={clsx(
                        'px-2 py-1 rounded text-xs font-semibold',
                        ret.return_type === 'credit_note' ? 'bg-blue-50 text-blue-700' : 'bg-purple-50 text-purple-700'
                      )}>
                        {ret.return_type === 'credit_note' ? 'Credit Note' : 'Replacement'}
                      </span>
                    </div>
                    <p className="text-gray-500 text-sm">Return ID: {ret.return_id}</p>
                  </div>
                  <div className="text-right">
                    <p className="text-lg font-bold text-gray-900">₹{ret.total_value.toLocaleString()}</p>
                    <p className="text-gray-500 text-sm">{ret.items.length} item(s)</p>
                  </div>
                </div>
                <ChevronDown
                  className={clsx(
                    'w-5 h-5 text-gray-500 transition-transform',
                    expandedReturn === ret.return_id && 'rotate-180'
                  )}
                />
              </button>

              {/* Expanded Details */}
              {expandedReturn === ret.return_id && (
                <div className="border-t border-gray-200 px-6 py-4 bg-gray-50 space-y-4">
                  {/* Items */}
                  <div>
                    <h4 className="font-semibold text-gray-900 mb-2">Items</h4>
                    <div className="space-y-2">
                      {ret.items.map((item, idx) => (
                        <div key={idx} className="flex justify-between items-center text-sm">
                          <div>
                            <p className="text-gray-600">{item.product_name}</p>
                            <p className="text-gray-500 text-xs">Qty: {item.quantity} @ ₹{item.unit_price}</p>
                          </div>
                          <div className="text-right">
                            <p className="text-gray-600 font-medium">₹{item.quantity * item.unit_price}</p>
                            <p className="text-gray-500 text-xs">{RETURN_REASONS.find(r => r.value === item.reason)?.label}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Notes & Credit Info */}
                  <div className="grid grid-cols-2 gap-4">
                    {ret.notes && (
                      <div>
                        <p className="text-gray-500 text-sm mb-1">Notes</p>
                        <p className="text-gray-600 text-sm">{ret.notes}</p>
                      </div>
                    )}
                    {ret.credit_note_number && (
                      <div>
                        <p className="text-gray-500 text-sm mb-1">Credit Note</p>
                        <p className="text-green-600 font-semibold">{ret.credit_note_number}</p>
                      </div>
                    )}
                  </div>

                  {/* Action Buttons */}
                  {activeTab === 'active' && (
                    <div className="flex gap-2 pt-4 border-t border-gray-300">
                      {ret.status === 'created' && (
                        <button
                          onClick={() => handleUpdateStatus(ret.return_id, 'approved')}
                          className="flex-1 bg-green-600 hover:bg-green-700 text-white px-3 py-2 rounded text-sm font-medium transition"
                        >
                          Approve
                        </button>
                      )}
                      {ret.status === 'approved' && (
                        <button
                          onClick={() => handleUpdateStatus(ret.return_id, 'shipped')}
                          className="flex-1 bg-blue-600 hover:bg-blue-700 text-white px-3 py-2 rounded text-sm font-medium transition"
                        >
                          Mark as Shipped
                        </button>
                      )}
                      {ret.status === 'shipped' && (
                        <button
                          onClick={() => handleUpdateStatus(ret.return_id, 'received_by_vendor')}
                          className="flex-1 bg-purple-600 hover:bg-purple-700 text-gray-900 px-3 py-2 rounded text-sm font-medium transition"
                        >
                          Received by Vendor
                        </button>
                      )}
                      {ret.status === 'received_by_vendor' && (
                        <>
                          <button
                            onClick={() => handleUpdateStatus(ret.return_id, 'credit_issued')}
                            className="flex-1 bg-green-600 hover:bg-green-700 text-white px-3 py-2 rounded text-sm font-medium transition"
                          >
                            Issue Credit
                          </button>
                          <button
                            onClick={() => handleUpdateStatus(ret.return_id, 'replaced')}
                            className="flex-1 bg-blue-600 hover:bg-blue-700 text-white px-3 py-2 rounded text-sm font-medium transition"
                          >
                            Mark as Replaced
                          </button>
                        </>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))
        )}
      </div>

      {/* Create Return Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg w-full max-w-2xl max-h-[90vh] overflow-y-auto border border-gray-200 shadow-2xl">
            {/* Modal Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
              <h2 className="text-xl font-bold text-gray-900">Create Vendor Return</h2>
              <button
                onClick={() => setShowModal(false)}
                className="text-gray-500 hover:text-gray-700 transition"
              >
                <XIcon className="w-6 h-6" />
              </button>
            </div>

            {/* Modal Content */}
            <div className="p-6 space-y-4">
              {/* Vendor Selection */}
              <div>
                <label className="block text-sm font-medium text-gray-900 mb-2">Vendor *</label>
                <select
                  value={selectedVendor}
                  onChange={(e) => setSelectedVendor(e.target.value)}
                  className="w-full px-3 py-2 bg-white border border-gray-300 rounded-lg text-gray-900 hover:bg-gray-100 focus:border-blue-500 outline-none transition"
                >
                  <option value="">Select Vendor...</option>
                  {vendors.map((v) => (
                    <option key={v.vendor_id} value={v.vendor_id}>
                      {v.legal_name}
                    </option>
                  ))}
                </select>
              </div>

              {/* Return Type */}
              <div>
                <label className="block text-sm font-medium text-gray-900 mb-2">Return Type *</label>
                <div className="flex gap-4">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      value="credit_note"
                      checked={returnType === 'credit_note'}
                      onChange={(e) => setReturnType(e.target.value as 'credit_note')}
                      className="w-4 h-4"
                    />
                    <span className="text-gray-900">Credit Note</span>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      value="replacement"
                      checked={returnType === 'replacement'}
                      onChange={(e) => setReturnType(e.target.value as 'replacement')}
                      className="w-4 h-4"
                    />
                    <span className="text-gray-900">Replacement</span>
                  </label>
                </div>
              </div>

              {/* Items */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="block text-sm font-medium text-gray-900">Items *</label>
                  <button
                    onClick={handleAddItem}
                    className="text-blue-600 hover:text-blue-700 text-sm font-medium"
                  >
                    + Add Item
                  </button>
                </div>
                <div className="space-y-3">
                  {items.map((item, idx) => (
                    <div key={idx} className="bg-gray-50 border border-gray-200 rounded-lg p-4 space-y-3">
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <label className="block text-xs text-gray-500 mb-1">Product Name</label>
                          <input
                            type="text"
                            value={item.product_name}
                            onChange={(e) => handleItemChange(idx, 'product_name', e.target.value)}
                            placeholder="Enter product name"
                            className="w-full px-3 py-2 bg-white border border-gray-300 rounded text-gray-900 text-sm placeholder-gray-500 outline-none focus:border-blue-500"
                          />
                        </div>
                        <div>
                          <label className="block text-xs text-gray-500 mb-1">Quantity</label>
                          <input
                            type="number"
                            min="1"
                            value={item.quantity}
                            onChange={(e) => handleItemChange(idx, 'quantity', parseInt(e.target.value) || 0)}
                            className="w-full px-3 py-2 bg-white border border-gray-300 rounded text-gray-900 text-sm outline-none focus:border-blue-500"
                          />
                        </div>
                      </div>
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <label className="block text-xs text-gray-500 mb-1">Unit Price</label>
                          <input
                            type="number"
                            min="0"
                            value={item.unit_price}
                            onChange={(e) => handleItemChange(idx, 'unit_price', parseFloat(e.target.value) || 0)}
                            className="w-full px-3 py-2 bg-white border border-gray-300 rounded text-gray-900 text-sm outline-none focus:border-blue-500"
                          />
                        </div>
                        <div>
                          <label className="block text-xs text-gray-500 mb-1">Reason</label>
                          <select
                            value={item.reason}
                            onChange={(e) => handleItemChange(idx, 'reason', e.target.value)}
                            className="w-full px-3 py-2 bg-white border border-gray-300 rounded text-gray-900 text-sm outline-none focus:border-blue-500"
                          >
                            {RETURN_REASONS.map((r) => (
                              <option key={r.value} value={r.value}>
                                {r.label}
                              </option>
                            ))}
                          </select>
                        </div>
                      </div>
                      {items.length > 1 && (
                        <button
                          onClick={() => handleRemoveItem(idx)}
                          className="text-red-600 hover:text-red-700 text-sm font-medium"
                        >
                          Remove Item
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {/* Notes */}
              <div>
                <label className="block text-sm font-medium text-gray-900 mb-2">Notes</label>
                <textarea
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="Add any additional notes..."
                  rows={3}
                  className="w-full px-3 py-2 bg-white border border-gray-300 rounded-lg text-gray-900 text-sm placeholder-gray-500 outline-none focus:border-blue-500 resize-none"
                />
              </div>

              {/* Total */}
              <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
                <div className="flex justify-between items-center">
                  <p className="text-gray-600 font-medium">Total Return Value</p>
                  <p className="text-2xl font-bold text-gray-900">₹{totalValue.toLocaleString()}</p>
                </div>
              </div>
            </div>

            {/* Modal Footer */}
            <div className="flex gap-3 px-6 py-4 border-t border-gray-200 bg-gray-50">
              <button
                onClick={() => setShowModal(false)}
                className="flex-1 px-4 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg font-medium transition border border-gray-300"
              >
                Cancel
              </button>
              <button
                onClick={handleCreateReturn}
                className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium transition"
              >
                Create Return
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
