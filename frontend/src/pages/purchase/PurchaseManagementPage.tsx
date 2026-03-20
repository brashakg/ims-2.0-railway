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
import { VendorReturns } from './VendorReturns';
import { PurchaseTable } from './PurchaseTable';
import { PurchaseOrderForm } from './PurchaseOrderForm';
import { PurchaseOrderDetail } from './PurchaseOrderDetail';
import { SupplierPanel } from './SupplierPanel';
import { SupplierFormModal } from './SupplierFormModal';
import { PurchaseAnalytics } from './PurchaseAnalytics';
import type { TabType, POStatus, Supplier, PurchaseOrder } from './purchaseTypes';

export function PurchaseManagementPage() {
  const toast = useToast();
  const [searchParams] = useSearchParams();

  const [activeTab, setActiveTab] = useState<TabType>('purchase-orders');
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<POStatus | 'ALL'>('ALL');
  const [isLoading, setIsLoading] = useState(true);

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
    try {
      // TODO: Wire to GET /api/v1/vendors and GET /api/v1/orders?type=purchase
      // once purchase order and vendor API endpoints are implemented.

      // Mock suppliers
      setSuppliers([
        {
          id: '1',
          name: 'Titan Eyewear Suppliers Pvt Ltd',
          code: 'SUP001',
          contactPerson: 'Rajesh Kumar',
          phone: '+91 98765 43210',
          email: 'rajesh@titaneyewear.com',
          address: 'Plot 45, Industrial Area Phase 2',
          city: 'New Delhi',
          state: 'Delhi',
          gstNumber: '07AAAAA1234A1Z5',
          paymentTerms: 30,
          creditLimit: 5000000,
          currentOutstanding: 1250000,
          rating: 4.5,
          totalPurchases: 12500000,
          lastPurchaseDate: '2024-01-25',
          performance: {
            onTimeDelivery: 92,
            qualityScore: 95,
            priceCompetitiveness: 88,
          },
        },
        {
          id: '2',
          name: 'Ray-Ban India Distribution',
          code: 'SUP002',
          contactPerson: 'Amit Sharma',
          phone: '+91 98765 43211',
          email: 'amit@rayban.in',
          address: '12th Floor, Business Tower',
          city: 'Mumbai',
          state: 'Maharashtra',
          gstNumber: '27BBBBB5678B2Z6',
          paymentTerms: 45,
          creditLimit: 8000000,
          currentOutstanding: 2100000,
          rating: 5.0,
          totalPurchases: 18750000,
          lastPurchaseDate: '2024-01-28',
          performance: {
            onTimeDelivery: 98,
            qualityScore: 99,
            priceCompetitiveness: 85,
          },
        },
        {
          id: '3',
          name: 'Contact Lens Solutions Inc',
          code: 'SUP003',
          contactPerson: 'Priya Patel',
          phone: '+91 98765 43212',
          email: 'priya@clsolutions.com',
          address: 'Warehouse 7, Logistics Park',
          city: 'Ahmedabad',
          state: 'Gujarat',
          gstNumber: '24CCCCC9012C3Z7',
          paymentTerms: 30,
          creditLimit: 3000000,
          currentOutstanding: 850000,
          rating: 4.2,
          totalPurchases: 6200000,
          lastPurchaseDate: '2024-01-20',
          performance: {
            onTimeDelivery: 87,
            qualityScore: 91,
            priceCompetitiveness: 90,
          },
        },
      ]);

      // Mock purchase orders
      setPurchaseOrders([
        {
          id: '1',
          poNumber: 'PO-2024-001',
          supplierId: '1',
          supplierName: 'Titan Eyewear Suppliers Pvt Ltd',
          date: '2024-02-01',
          expectedDelivery: '2024-02-10',
          status: 'APPROVED',
          items: [
            { productId: '1', productName: 'Titan Eye+ Premium Frame', sku: 'TIT-001', quantity: 50, unitCost: 1200, taxRate: 18, total: 70800 },
            { productId: '2', productName: 'Titan Progressive Lenses', sku: 'TIT-002', quantity: 30, unitCost: 2500, taxRate: 18, total: 88500 },
          ],
          subtotal: 135000,
          taxAmount: 24300,
          total: 159300,
          approvedBy: 'Admin',
        },
        {
          id: '2',
          poNumber: 'PO-2024-002',
          supplierId: '2',
          supplierName: 'Ray-Ban India Distribution',
          date: '2024-02-02',
          expectedDelivery: '2024-02-12',
          status: 'PENDING',
          items: [
            { productId: '3', productName: 'Ray-Ban Aviator Classic', sku: 'RB-3025', quantity: 25, unitCost: 3500, taxRate: 18, total: 103250 },
          ],
          subtotal: 87500,
          taxAmount: 15750,
          total: 103250,
        },
        {
          id: '3',
          poNumber: 'PO-2024-003',
          supplierId: '3',
          supplierName: 'Contact Lens Solutions Inc',
          date: '2024-01-28',
          expectedDelivery: '2024-02-05',
          status: 'RECEIVED',
          items: [
            { productId: '4', productName: 'Acuvue Oasys Monthly (6 pack)', sku: 'ACU-OASYS-6', quantity: 100, unitCost: 650, taxRate: 12, total: 72800 },
          ],
          subtotal: 65000,
          taxAmount: 7800,
          total: 72800,
          receivedDate: '2024-02-04',
        },
      ]);

    } catch (error: any) {
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
  const handlePOAction = (po: PurchaseOrder, action: string) => {
    let newStatus: POStatus = po.status;
    let message = '';

    switch (action) {
      case 'submit':
        newStatus = 'PENDING';
        message = `${po.poNumber} submitted for approval`;
        break;
      case 'approve':
        newStatus = 'APPROVED';
        message = `${po.poNumber} approved`;
        break;
      case 'reject':
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

      {/* Demo Data Banner */}
      <div className="p-4 bg-blue-50 border border-blue-200 rounded-lg flex items-start gap-3">
        <AlertTriangle className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5" />
        <div className="flex-1">
          <p className="text-sm font-medium text-blue-900">Using Demo Data</p>
          <p className="text-xs text-blue-700 mt-1">This module is currently displaying sample suppliers and purchase orders for demonstration purposes. Connect to your actual vendor database to manage real purchase orders.</p>
        </div>
      </div>

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
            onChange={(e) => setStatusFilter(e.target.value as any)}
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
