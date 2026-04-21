// ============================================================================
// IMS 2.0 - Purchase Order Management
// ============================================================================
// PO lifecycle: Draft → Approved → Sent → Partial Receipt → Received → Closed

import { useState, useEffect, startTransition } from 'react';
import { Plus, Edit2, Send, Check, Search } from 'lucide-react';
import clsx from 'clsx';
import { vendorsApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

interface POLineItem {
  product_id: string;
  product_name: string;
  quantity: number;
  unit_price: number;
  total_price: number;
}

interface PurchaseOrder {
  id: string;
  po_number: string;
  vendor_id: string;
  vendor_name: string;
  items: POLineItem[];
  status: 'draft' | 'approved' | 'sent' | 'partial_receipt' | 'received' | 'closed';
  total_amount: number;
  gst_amount: number;
  net_amount: number;
  created_at: string;
  expected_delivery: string;
  approved_by?: string;
  approved_at?: string;
}


const getStatusColor = (status: string) => {
  switch (status) {
    case 'draft':
      return 'bg-gray-100 text-gray-100';
    case 'approved':
      return 'bg-blue-50 text-blue-700';
    case 'sent':
      return 'bg-purple-50 text-purple-700';
    case 'partial_receipt':
      return 'bg-yellow-50 text-yellow-700';
    case 'received':
      return 'bg-green-50 text-green-700';
    case 'closed':
      return 'bg-white text-gray-700';
    default:
      return 'bg-gray-100 text-gray-100';
  }
};

const getStatusLabel = (status: string) => {
  const labels: Record<string, string> = {
    draft: 'Draft',
    approved: 'Approved',
    sent: 'Sent',
    partial_receipt: 'Partial Receipt',
    received: 'Received',
    closed: 'Closed',
  };
  return labels[status] || status;
};

export function PurchaseOrderDashboard() {
  const { user } = useAuth();
  const toast = useToast();
  const [activeTab, setActiveTab] = useState<'all' | 'pending' | 'received' | 'closed'>('all');
  const [searchTerm, setSearchTerm] = useState('');
  const [filterStatus, setFilterStatus] = useState<string>('all');
  const [purchaseOrders, setPurchaseOrders] = useState<PurchaseOrder[]>([]);
  const [, setIsLoading] = useState(true);
  const [expandedPO, setExpandedPO] = useState<string | null>(null);

  // Load purchase orders on mount
  useEffect(() => {
    const loadPurchaseOrders = async () => {
      try {
        setIsLoading(true);
        const storeId = user?.activeStoreId || '';
        const response = await vendorsApi.getPurchaseOrders({ store_id: storeId });
        const poList = Array.isArray(response) ? response : response.data || [];
        const transformedPOs = poList.map((po: any) => ({
          id: po.id || po._id,
          po_number: po.po_number,
          vendor_id: po.vendor_id,
          vendor_name: po.vendor_name || 'Unknown Vendor',
          items: po.items || [],
          status: po.status || 'draft',
          total_amount: po.total_amount || 0,
          gst_amount: po.gst_amount || 0,
          net_amount: po.net_amount || po.total_amount || 0,
          created_at: po.created_at,
          expected_delivery: po.expected_delivery || '',
          approved_by: po.approved_by,
          approved_at: po.approved_at,
        }));
        setPurchaseOrders(transformedPOs);
      } catch (error) {
        toast.error('Failed to load purchase orders');
      } finally {
        setIsLoading(false);
      }
    };

    loadPurchaseOrders();
  }, [user?.activeStoreId]);

  const filteredPOs = purchaseOrders.filter((po) => {
    const matchesSearch = po.po_number.toLowerCase().includes(searchTerm.toLowerCase()) ||
      po.vendor_name.toLowerCase().includes(searchTerm.toLowerCase());

    if (filterStatus === 'all') return matchesSearch;
    return matchesSearch && po.status === filterStatus;
  });

  const pendingPOs = purchaseOrders.filter(po => ['draft', 'approved', 'sent', 'partial_receipt'].includes(po.status));

  const totalValue = purchaseOrders.reduce((sum, po) => sum + po.net_amount, 0);
  const pendingValue = pendingPOs.reduce((sum, po) => sum + po.net_amount, 0);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Purchase Orders</h1>
          <p className="text-gray-500">Manage purchase orders and vendor supplies</p>
        </div>
        <button className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-semibold flex items-center gap-2">
          <Plus className="w-5 h-5" />
          Create PO
        </button>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-4 gap-4">
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">Total POs</p>
          <p className="text-2xl font-bold text-gray-900">{purchaseOrders.length}</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">Pending POs</p>
          <p className="text-2xl font-bold text-yellow-600">{pendingPOs.length}</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">Total Value</p>
          <p className="text-2xl font-bold text-green-600">₹{(totalValue / 100000).toFixed(1)}L</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">Pending Value</p>
          <p className="text-2xl font-bold text-blue-600">₹{(pendingValue / 100000).toFixed(1)}L</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-300">
        {(['all', 'pending', 'received', 'closed'] as const).map((tab) => (
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
            {tab === 'all' ? 'All' : tab === 'pending' ? 'Pending' : tab === 'received' ? 'Received' : 'Closed'}
          </button>
        ))}
      </div>

      {/* Search and Filter */}
      <div className="flex gap-4 items-center">
        <div className="flex-1 relative">
          <Search className="absolute left-3 top-3 w-5 h-5 text-gray-500" />
          <input
            type="text"
            placeholder="Search by PO number or vendor..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-white border border-gray-300 rounded-lg text-gray-900 placeholder-gray-500 focus:outline-none focus:border-blue-500"
          />
        </div>
        <select
          value={filterStatus}
          onChange={(e) => startTransition(() => setFilterStatus(e.target.value))}
          className="px-4 py-2 bg-white border border-gray-300 rounded-lg text-gray-900 text-sm"
        >
          <option value="all">All Status</option>
          <option value="draft">Draft</option>
          <option value="approved">Approved</option>
          <option value="sent">Sent</option>
          <option value="partial_receipt">Partial Receipt</option>
          <option value="received">Received</option>
          <option value="closed">Closed</option>
        </select>
      </div>

      {/* PO List */}
      <div className="space-y-3">
        {filteredPOs.map((po) => (
          <div key={po.id}>
            <div className="bg-white border border-gray-200 rounded-lg p-4 hover:border-gray-300 transition-colors">
            <div className="flex items-start justify-between mb-3">
              <div className="flex-1">
                <p className="text-gray-900 font-semibold">{po.po_number}</p>
                <p className="text-gray-500 text-sm">{po.vendor_name}</p>
              </div>
              <span className={clsx('px-3 py-1 rounded-full text-xs font-semibold', getStatusColor(po.status))}>
                {getStatusLabel(po.status)}
              </span>
            </div>

            <div className="grid grid-cols-5 gap-4 mb-3 pb-3 border-b border-gray-300">
              <div>
                <p className="text-gray-500 text-xs mb-1">Items</p>
                <p className="text-gray-900 font-semibold">{po.items.length}</p>
              </div>
              <div>
                <p className="text-gray-500 text-xs mb-1">Quantity</p>
                <p className="text-gray-900 font-semibold">{po.items.reduce((sum, i) => sum + i.quantity, 0)}</p>
              </div>
              <div>
                <p className="text-gray-500 text-xs mb-1">Amount</p>
                <p className="text-green-600 font-semibold">₹{po.net_amount.toLocaleString('en-IN')}</p>
              </div>
              <div>
                <p className="text-gray-500 text-xs mb-1">Expected Delivery</p>
                <p className="text-gray-900 font-semibold">{new Date(po.expected_delivery).toLocaleDateString()}</p>
              </div>
              <div className="text-right">
                <p className="text-gray-500 text-xs mb-1">Created</p>
                <p className="text-gray-900 font-semibold">{new Date(po.created_at).toLocaleDateString()}</p>
              </div>
            </div>

            <div className="flex gap-2">
              {po.status === 'draft' && (
                <>
                  <button className="px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded font-semibold flex items-center gap-1">
                    <Check className="w-4 h-4" /> Approve
                  </button>
                  <button className="px-3 py-1 bg-gray-100 hover:bg-gray-200 text-gray-700 text-sm rounded font-semibold flex items-center gap-1 border border-gray-300">
                    <Edit2 className="w-4 h-4" /> Edit
                  </button>
                </>
              )}
              {po.status === 'approved' && (
                <button className="px-3 py-1 bg-purple-600 hover:bg-purple-700 text-gray-900 text-sm rounded font-semibold flex items-center gap-1">
                  <Send className="w-4 h-4" /> Send to Vendor
                </button>
              )}
              {['sent', 'partial_receipt'].includes(po.status) && (
                <button className="px-3 py-1 bg-green-600 hover:bg-green-700 text-white text-sm rounded font-semibold flex items-center gap-1">
                  <Check className="w-4 h-4" /> Record Receipt
                </button>
              )}
              <button
                onClick={() => startTransition(() => setExpandedPO(expandedPO === po.id ? null : po.id))}
                className="px-3 py-1 bg-gray-100 hover:bg-gray-200 text-gray-700 text-sm rounded font-semibold border border-gray-300"
              >
                {expandedPO === po.id ? 'Hide Details' : 'View Details'}
              </button>
            </div>

            {expandedPO === po.id && (
              <div className="bg-gray-50 rounded-b-lg p-4 border border-t-0 border-gray-200">
                <h4 className="text-gray-900 font-semibold mb-3">Line Items</h4>
                {po.items && po.items.length > 0 ? (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-gray-300">
                          <th className="text-left px-3 py-2 text-gray-500">Product</th>
                          <th className="text-right px-3 py-2 text-gray-500">Qty</th>
                          <th className="text-right px-3 py-2 text-gray-500">Unit Price</th>
                          <th className="text-right px-3 py-2 text-gray-500">Total</th>
                        </tr>
                      </thead>
                      <tbody>
                        {po.items.map((item, idx) => (
                          <tr key={idx} className="border-b border-gray-200 hover:bg-gray-100">
                            <td className="px-3 py-2 text-gray-900">{item.product_name}</td>
                            <td className="text-right px-3 py-2 text-gray-900">{item.quantity}</td>
                            <td className="text-right px-3 py-2 text-gray-900">₹{item.unit_price.toLocaleString('en-IN')}</td>
                            <td className="text-right px-3 py-2 text-green-600 font-semibold">₹{item.total_price.toLocaleString('en-IN')}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="text-gray-500 text-sm">No line items</p>
                )}
              </div>
            )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default PurchaseOrderDashboard;
