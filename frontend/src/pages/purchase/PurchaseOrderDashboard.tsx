// ============================================================================
// IMS 2.0 - Purchase Order Management
// ============================================================================
// PO lifecycle: Draft → Approved → Sent → Partial Receipt → Received → Closed

import { useState } from 'react';
import { Plus, Edit2, Send, Check, Search } from 'lucide-react';
import clsx from 'clsx';

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

const MOCK_POS: PurchaseOrder[] = [
  {
    id: 'po-001',
    po_number: 'PO-2024-001',
    vendor_id: 'v-001',
    vendor_name: 'Optical Frames Ltd',
    items: [
      {
        product_id: 'prod-001',
        product_name: 'Frame Model A',
        quantity: 100,
        unit_price: 500,
        total_price: 50000,
      },
    ],
    status: 'approved',
    total_amount: 50000,
    gst_amount: 9000,
    net_amount: 59000,
    created_at: '2024-02-01T10:00:00Z',
    expected_delivery: '2024-02-15',
    approved_by: 'Manager',
    approved_at: '2024-02-03T14:00:00Z',
  },
  {
    id: 'po-002',
    po_number: 'PO-2024-002',
    vendor_id: 'v-002',
    vendor_name: 'Lens Manufacturers Inc',
    items: [
      {
        product_id: 'prod-002',
        product_name: 'Premium Lens Coating',
        quantity: 500,
        unit_price: 300,
        total_price: 150000,
      },
    ],
    status: 'received',
    total_amount: 150000,
    gst_amount: 27000,
    net_amount: 177000,
    created_at: '2024-02-02T09:30:00Z',
    expected_delivery: '2024-02-20',
    approved_by: 'Manager',
    approved_at: '2024-02-03T11:00:00Z',
  },
  {
    id: 'po-003',
    po_number: 'PO-2024-003',
    vendor_id: 'v-001',
    vendor_name: 'Optical Frames Ltd',
    items: [
      {
        product_id: 'prod-003',
        product_name: 'Frame Model B',
        quantity: 75,
        unit_price: 600,
        total_price: 45000,
      },
    ],
    status: 'draft',
    total_amount: 45000,
    gst_amount: 8100,
    net_amount: 53100,
    created_at: '2024-02-08T14:20:00Z',
    expected_delivery: '2024-02-25',
  },
];

const getStatusColor = (status: string) => {
  switch (status) {
    case 'draft':
      return 'bg-gray-700 text-gray-100';
    case 'approved':
      return 'bg-blue-900 text-blue-300';
    case 'sent':
      return 'bg-purple-900 text-purple-300';
    case 'partial_receipt':
      return 'bg-yellow-900 text-yellow-300';
    case 'received':
      return 'bg-green-900 text-green-300';
    case 'closed':
      return 'bg-gray-800 text-gray-300';
    default:
      return 'bg-gray-700 text-gray-100';
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
  const [activeTab, setActiveTab] = useState<'all' | 'pending' | 'received' | 'closed'>('all');
  const [searchTerm, setSearchTerm] = useState('');
  const [filterStatus, setFilterStatus] = useState<string>('all');

  const filteredPOs = MOCK_POS.filter((po) => {
    const matchesSearch = po.po_number.toLowerCase().includes(searchTerm.toLowerCase()) ||
      po.vendor_name.toLowerCase().includes(searchTerm.toLowerCase());

    if (filterStatus === 'all') return matchesSearch;
    return matchesSearch && po.status === filterStatus;
  });

  const pendingPOs = MOCK_POS.filter(po => ['draft', 'approved', 'sent', 'partial_receipt'].includes(po.status));

  const totalValue = MOCK_POS.reduce((sum, po) => sum + po.net_amount, 0);
  const pendingValue = pendingPOs.reduce((sum, po) => sum + po.net_amount, 0);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Purchase Orders</h1>
          <p className="text-gray-400">Manage purchase orders and vendor supplies</p>
        </div>
        <button className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-semibold flex items-center gap-2">
          <Plus className="w-5 h-5" />
          Create PO
        </button>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-4 gap-4">
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Total POs</p>
          <p className="text-2xl font-bold text-white">{MOCK_POS.length}</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Pending POs</p>
          <p className="text-2xl font-bold text-yellow-400">{pendingPOs.length}</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Total Value</p>
          <p className="text-2xl font-bold text-green-400">₹{(totalValue / 100000).toFixed(1)}L</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Pending Value</p>
          <p className="text-2xl font-bold text-blue-400">₹{(pendingValue / 100000).toFixed(1)}L</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-700">
        {(['all', 'pending', 'received', 'closed'] as const).map((tab) => (
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
            {tab === 'all' ? 'All' : tab === 'pending' ? 'Pending' : tab === 'received' ? 'Received' : 'Closed'}
          </button>
        ))}
      </div>

      {/* Search and Filter */}
      <div className="flex gap-4 items-center">
        <div className="flex-1 relative">
          <Search className="absolute left-3 top-3 w-5 h-5 text-gray-400" />
          <input
            type="text"
            placeholder="Search by PO number or vendor..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
          />
        </div>
        <select
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
          className="px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white text-sm"
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
          <div key={po.id} className="bg-gray-800 rounded-lg p-4 border border-gray-700 hover:border-gray-600 transition-colors">
            <div className="flex items-start justify-between mb-3">
              <div className="flex-1">
                <p className="text-white font-semibold">{po.po_number}</p>
                <p className="text-gray-400 text-sm">{po.vendor_name}</p>
              </div>
              <span className={clsx('px-3 py-1 rounded-full text-xs font-semibold', getStatusColor(po.status))}>
                {getStatusLabel(po.status)}
              </span>
            </div>

            <div className="grid grid-cols-5 gap-4 mb-3 pb-3 border-b border-gray-700">
              <div>
                <p className="text-gray-400 text-xs mb-1">Items</p>
                <p className="text-white font-semibold">{po.items.length}</p>
              </div>
              <div>
                <p className="text-gray-400 text-xs mb-1">Quantity</p>
                <p className="text-white font-semibold">{po.items.reduce((sum, i) => sum + i.quantity, 0)}</p>
              </div>
              <div>
                <p className="text-gray-400 text-xs mb-1">Amount</p>
                <p className="text-green-400 font-semibold">₹{po.net_amount.toLocaleString('en-IN')}</p>
              </div>
              <div>
                <p className="text-gray-400 text-xs mb-1">Expected Delivery</p>
                <p className="text-white font-semibold">{new Date(po.expected_delivery).toLocaleDateString()}</p>
              </div>
              <div className="text-right">
                <p className="text-gray-400 text-xs mb-1">Created</p>
                <p className="text-white font-semibold">{new Date(po.created_at).toLocaleDateString()}</p>
              </div>
            </div>

            <div className="flex gap-2">
              {po.status === 'draft' && (
                <>
                  <button className="px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded font-semibold flex items-center gap-1">
                    <Check className="w-4 h-4" /> Approve
                  </button>
                  <button className="px-3 py-1 bg-gray-700 hover:bg-gray-600 text-white text-sm rounded font-semibold flex items-center gap-1">
                    <Edit2 className="w-4 h-4" /> Edit
                  </button>
                </>
              )}
              {po.status === 'approved' && (
                <button className="px-3 py-1 bg-purple-600 hover:bg-purple-700 text-white text-sm rounded font-semibold flex items-center gap-1">
                  <Send className="w-4 h-4" /> Send to Vendor
                </button>
              )}
              {['sent', 'partial_receipt'].includes(po.status) && (
                <button className="px-3 py-1 bg-green-600 hover:bg-green-700 text-white text-sm rounded font-semibold flex items-center gap-1">
                  <Check className="w-4 h-4" /> Record Receipt
                </button>
              )}
              <button className="px-3 py-1 bg-gray-700 hover:bg-gray-600 text-white text-sm rounded font-semibold">
                View Details
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default PurchaseOrderDashboard;
