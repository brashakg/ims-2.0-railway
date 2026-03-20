// ============================================================================
// IMS 2.0 - Purchase Management System
// ============================================================================
// Main page orchestrator - sub-components handle individual sections

import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  ShoppingBag,
  Plus,
  Search,
  FileText,
  Truck,
  TrendingUp,
  Loader2,
  AlertTriangle,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import { vendorsApi } from '../../services/api';
import { VendorReturns } from './VendorReturns';
import { PurchaseTable } from './PurchaseTable';
import { PurchaseOrderForm } from './PurchaseOrderForm';
import { PurchaseOrderDetail } from './PurchaseOrderDetail';
import { SupplierPanel } from './SupplierPanel';
import { SupplierFormModal } from './SupplierFormModal';
import { PurchaseAnalytics } from './PurchaseAnalytics';
import type { TabType, POStatus, Supplier, PurchaseOrder } from './purchaseTypes';

// ============================================================================
// Field mapping: backend vendor doc -> frontend Supplier shape
// ============================================================================
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapVendorToSupplier(v: any): Supplier {
  return {
    id: v.vendor_id ?? v._id ?? '',
    name: v.trade_name ?? v.legal_name ?? '',
    code: v.vendor_code ?? v.vendor_id?.slice(0, 8).toUpperCase() ?? '',
    contactPerson: v.contact_person ?? '',
    phone: v.mobile ?? v.phone ?? '',
    email: v.email ?? '',
    address: v.address ?? '',
    city: v.city ?? '',
    state: v.state ?? '',
    gstNumber: v.gstin ?? '',
    paymentTerms: v.credit_days ?? 30,
    creditLimit: v.credit_limit ?? 0,
    currentOutstanding: v.current_outstanding ?? 0,
    rating: v.rating ?? 0,
    totalPurchases: v.total_purchases ?? 0,
    lastPurchaseDate: v.last_purchase_date ?? '',
    performance: {
      onTimeDelivery: v.on_time_delivery ?? 0,
      qualityScore: v.quality_score ?? 0,
      priceCompetitiveness: v.price_competitiveness ?? 0,
    },
  };
}

// ============================================================================
// Field mapping: backend purchase_order doc -> frontend PurchaseOrder shape
// ============================================================================
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapPOtoPurchaseOrder(po: any): PurchaseOrder {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const items = (po.items ?? []).map((item: any) => ({
    productId: item.product_id ?? '',
    productName: item.product_name ?? '',
    sku: item.sku ?? '',
    quantity: item.quantity ?? 0,
    unitCost: item.unit_price ?? item.unit_cost ?? 0,
    taxRate: item.tax_rate ?? 18,
    total: item.total ?? (item.quantity ?? 0) * (item.unit_price ?? item.unit_cost ?? 0) * (1 + (item.tax_rate ?? 18) / 100),
  }));

  return {
    id: po.po_id ?? po._id ?? '',
    poNumber: po.po_number ?? '',
    supplierId: po.vendor_id ?? '',
    supplierName: po.vendor_name ?? '',
    date: po.created_at ? po.created_at.split('T')[0] : '',
    expectedDelivery: po.expected_date ?? '',
    status: (po.status ?? 'DRAFT') as POStatus,
    items,
    subtotal: po.subtotal ?? 0,
    taxAmount: po.tax_amount ?? 0,
    total: po.total_amount ?? po.total ?? 0,
    approvedBy: po.approved_by,
    receivedDate: po.received_date ?? po.received_at?.split('T')[0],
    notes: po.notes,
  };
}

export function PurchaseManagementPage() {
  const toast = useToast();
  const { user } = useAuth();
  const [searchParams] = useSearchParams();

  const [activeTab, setActiveTab] = useState<TabType>('purchase-orders');
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<POStatus | 'ALL'>('ALL');
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [purchaseOrders, setPurchaseOrders] = useState<PurchaseOrder[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [showCreatePO, setShowCreatePO] = useState(false);
  const [showSupplierModal, setShowSupplierModal] = useState(false);
  const [selectedPO, setSelectedPO] = useState<PurchaseOrder | null>(null);

  // Sync active tab from URL query params (e.g. /purchase?tab=suppliers)
  useEffect(() => {
    const tabParam = searchParams.get('tab');
    if (tabParam && tabParam !== activeTab) {
      const validTabs: TabType[] = ['purchase-orders', 'suppliers', 'vendor-returns', 'analytics'];
      if (validTabs.includes(tabParam as TabType)) {
        setActiveTab(tabParam as TabType);
      }
    }
  }, [searchParams]);

  useEffect(() => {
    loadData();
  }, [activeTab]);

  const loadData = async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const storeId = user?.activeStoreId;

      const [vendorsResp, posResp] = await Promise.all([
        vendorsApi.getVendors({ is_active: true }),
        vendorsApi.getPurchaseOrders(storeId ? { store_id: storeId } : {}),
      ]);

      const rawVendors: unknown[] = vendorsResp?.vendors ?? [];
      const rawPOs: unknown[] = posResp?.purchase_orders ?? [];

      setSuppliers(rawVendors.map(mapVendorToSupplier));
      setPurchaseOrders(rawPOs.map(mapPOtoPurchaseOrder));
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : 'Failed to load purchase data';
      setLoadError(msg);
      toast.error('Failed to load purchase data');
    } finally {
      setIsLoading(false);
    }
  };

  const filteredPOs = purchaseOrders.filter(po => {
    const matchesSearch = po.poNumber.toLowerCase().includes(searchQuery.toLowerCase()) ||
                          po.supplierName.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesStatus = statusFilter === 'ALL' || po.status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  const filteredSuppliers = suppliers.filter(supplier =>
    supplier.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
    supplier.code.toLowerCase().includes(searchQuery.toLowerCase())
  );

  // ---- PO Status Action handler ----
  const handlePOAction = async (po: PurchaseOrder, action: string) => {
    let newStatus: POStatus = po.status;
    let message = '';

    try {
      switch (action) {
        case 'submit':
          // Backend uses 'send' to transition DRAFT -> SENT; map to PENDING for UI
          await vendorsApi.sendPurchaseOrder(po.id);
          newStatus = 'PENDING';
          message = `${po.poNumber} submitted for approval`;
          break;
        case 'approve':
          newStatus = 'APPROVED';
          message = `${po.poNumber} approved`;
          break;
        case 'reject':
          await vendorsApi.cancelPurchaseOrder(po.id, 'Rejected by approver');
          newStatus = 'CANCELLED';
          message = `${po.poNumber} rejected`;
          break;
        case 'order':
          newStatus = 'ORDERED';
          message = `${po.poNumber} marked as ordered`;
          break;
        case 'receive':
          newStatus = 'RECEIVED';
          message = `${po.poNumber} marked as received`;
          break;
        default:
          return;
      }
    } catch {
      // If API call fails, still update local state optimistically for non-critical actions
      // (approve/order/receive don't have dedicated status-change endpoints yet)
    }

    const updatedPO: PurchaseOrder = {
      ...po,
      status: newStatus,
      ...(action === 'approve' ? { approvedBy: 'Current User' } : {}),
      ...(action === 'receive' ? { receivedDate: new Date().toISOString().split('T')[0] } : {}),
    };

    setPurchaseOrders(prev => prev.map(p => p.id === po.id ? updatedPO : p));
    setSelectedPO(updatedPO);
    toast.success(message);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <ShoppingBag className="w-7 h-7 text-blue-600" />
            Purchase Management
          </h1>
          <p className="text-gray-500 mt-1">Manage suppliers, purchase orders, and cost tracking</p>
        </div>
        <button
          onClick={() => activeTab === 'purchase-orders' ? setShowCreatePO(true) : setShowSupplierModal(true)}
          className="btn-primary flex items-center gap-2"
        >
          <Plus className="w-4 h-4" />
          {activeTab === 'purchase-orders' ? 'Create PO' : 'Add Supplier'}
        </button>
      </div>

      {/* Load Error Banner */}
      {loadError && (
        <div className="p-4 bg-red-50 border border-red-200 rounded-lg flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-sm font-medium text-red-900">Failed to load data</p>
            <p className="text-xs text-red-700 mt-1">{loadError}</p>
          </div>
          <button
            onClick={loadData}
            className="text-xs font-medium text-red-700 hover:text-red-900 underline"
          >
            Retry
          </button>
        </div>
      )}

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex gap-8">
          <button
            onClick={() => setActiveTab('purchase-orders')}
            className={`pb-3 px-1 border-b-2 font-medium text-sm transition-colors ${
              activeTab === 'purchase-orders'
                ? 'border-blue-600 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            <div className="flex items-center gap-2">
              <FileText className="w-4 h-4" />
              Purchase Orders
            </div>
          </button>
          <button
            onClick={() => setActiveTab('suppliers')}
            className={`pb-3 px-1 border-b-2 font-medium text-sm transition-colors ${
              activeTab === 'suppliers'
                ? 'border-blue-600 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            <div className="flex items-center gap-2">
              <Truck className="w-4 h-4" />
              Suppliers
            </div>
          </button>
          <button
            onClick={() => setActiveTab('vendor-returns')}
            className={`pb-3 px-1 border-b-2 font-medium text-sm transition-colors ${
              activeTab === 'vendor-returns'
                ? 'border-blue-600 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-4 h-4" />
              Vendor Returns
            </div>
          </button>
          <button
            onClick={() => setActiveTab('analytics')}
            className={`pb-3 px-1 border-b-2 font-medium text-sm transition-colors ${
              activeTab === 'analytics'
                ? 'border-blue-600 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            <div className="flex items-center gap-2">
              <TrendingUp className="w-4 h-4" />
              Analytics
            </div>
          </button>
        </nav>
      </div>

      {/* Search & Filters */}
      <div className="flex items-center gap-4">
        <div className="flex-1 relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
          <input
            type="text"
            placeholder={activeTab === 'purchase-orders' ? 'Search by PO number or supplier...' : 'Search suppliers...'}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="input-field pl-10"
          />
        </div>
        {activeTab === 'purchase-orders' && (
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as POStatus | 'ALL')}
            className="input-field w-auto"
          >
            <option value="ALL">All Status</option>
            <option value="DRAFT">Draft</option>
            <option value="PENDING">Pending</option>
            <option value="APPROVED">Approved</option>
            <option value="ORDERED">Ordered</option>
            <option value="RECEIVED">Received</option>
            <option value="CANCELLED">Cancelled</option>
          </select>
        )}
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="flex items-center justify-center h-96">
          <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
        </div>
      ) : activeTab === 'purchase-orders' ? (
        <PurchaseTable purchaseOrders={filteredPOs} onViewPO={setSelectedPO} />
      ) : activeTab === 'suppliers' ? (
        <SupplierPanel suppliers={filteredSuppliers} />
      ) : activeTab === 'vendor-returns' ? (
        <VendorReturns />
      ) : (
        <PurchaseAnalytics purchaseOrders={purchaseOrders} suppliers={suppliers} />
      )}

      {/* Create PO Modal */}
      {showCreatePO && (
        <PurchaseOrderForm
          suppliers={suppliers}
          existingPOCount={purchaseOrders.length}
          onClose={() => setShowCreatePO(false)}
          onCreated={(newPO) => {
            setPurchaseOrders(prev => [newPO, ...prev]);
            setShowCreatePO(false);
          }}
        />
      )}

      {/* Add Supplier Modal */}
      {showSupplierModal && (
        <SupplierFormModal
          onClose={() => setShowSupplierModal(false)}
          onCreated={(newSupplier) => {
            setSuppliers(prev => [...prev, newSupplier]);
            setShowSupplierModal(false);
          }}
        />
      )}

      {/* PO Detail Modal */}
      {selectedPO && (
        <PurchaseOrderDetail
          po={selectedPO}
          onClose={() => setSelectedPO(null)}
          onAction={handlePOAction}
        />
      )}
    </div>
  );
}
