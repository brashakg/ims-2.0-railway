// ============================================================================
// IMS 2.0 - Purchase Management System
// ============================================================================
// Complete purchase order, supplier, and cost tracking

import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  ShoppingBag,
  Plus,
  Search,
  FileText,
  CheckCircle,
  Clock,
  X as XIcon,
  TrendingUp,
  DollarSign,
  Package,
  Truck,
  User,
  Phone,
  Mail,
  MapPin,
  Edit,
  Download,
  Eye,
  Loader2,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';

type TabType = 'purchase-orders' | 'suppliers' | 'analytics';
type POStatus = 'DRAFT' | 'PENDING' | 'APPROVED' | 'ORDERED' | 'RECEIVED' | 'CANCELLED';

interface Supplier {
  id: string;
  name: string;
  code: string;
  contactPerson: string;
  phone: string;
  email: string;
  address: string;
  city: string;
  state: string;
  gstNumber: string;
  paymentTerms: number; // days
  creditLimit: number;
  currentOutstanding: number;
  rating: number; // 1-5
  totalPurchases: number;
  lastPurchaseDate: string;
  performance: {
    onTimeDelivery: number; // percentage
    qualityScore: number; // percentage
    priceCompetitiveness: number; // percentage
  };
}

interface PurchaseOrder {
  id: string;
  poNumber: string;
  supplierId: string;
  supplierName: string;
  date: string;
  expectedDelivery: string;
  status: POStatus;
  items: POItem[];
  subtotal: number;
  taxAmount: number;
  total: number;
  approvedBy?: string;
  receivedDate?: string;
  notes?: string;
}

interface POItem {
  productId: string;
  productName: string;
  sku: string;
  quantity: number;
  unitCost: number;
  taxRate: number;
  total: number;
}

export function PurchaseManagementPage() {
  const toast = useToast();
  const [searchParams] = useSearchParams();

  const [activeTab, setActiveTab] = useState<TabType>('purchase-orders');
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<POStatus | 'ALL'>('ALL');
  const [isLoading, setIsLoading] = useState(true);

  const [purchaseOrders, setPurchaseOrders] = useState<PurchaseOrder[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [, setShowCreatePO] = useState(false);
  const [, setShowSupplierModal] = useState(false);
  const [, setSelectedPO] = useState<PurchaseOrder | null>(null);

  // Sync active tab from URL query params (e.g. /purchase?tab=suppliers)
  useEffect(() => {
    const tabParam = searchParams.get('tab');
    if (tabParam && tabParam !== activeTab) {
      const validTabs: TabType[] = ['purchase-orders', 'suppliers', 'analytics'];
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
      await new Promise(resolve => setTimeout(resolve, 800));

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

  const getStatusBadge = (status: POStatus) => {
    const config = {
      DRAFT: { label: 'Draft', color: 'bg-gray-100 text-gray-800', icon: FileText },
      PENDING: { label: 'Pending Approval', color: 'bg-yellow-100 text-yellow-800', icon: Clock },
      APPROVED: { label: 'Approved', color: 'bg-blue-100 text-blue-800', icon: CheckCircle },
      ORDERED: { label: 'Ordered', color: 'bg-purple-100 text-purple-800', icon: Truck },
      RECEIVED: { label: 'Received', color: 'bg-green-100 text-green-800', icon: CheckCircle },
      CANCELLED: { label: 'Cancelled', color: 'bg-red-100 text-red-800', icon: XIcon },
    };

    const { label, color, icon: Icon } = config[status];

    return (
      <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium ${color}`}>
        <Icon className="w-3 h-3" />
        {label}
      </span>
    );
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
        /* Purchase Orders List */
        <div className="space-y-4">
          {filteredPOs.map((po) => (
            <div key={po.id} className="card hover:shadow-lg transition-shadow">
              <div className="flex items-start justify-between mb-4">
                <div className="flex-1">
                  <div className="flex items-center gap-3 mb-2">
                    <h3 className="text-lg font-semibold text-gray-900">{po.poNumber}</h3>
                    {getStatusBadge(po.status)}
                  </div>
                  <p className="text-sm text-gray-600">{po.supplierName}</p>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setSelectedPO(po)}
                    className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
                  >
                    <Eye className="w-5 h-5 text-gray-600" />
                  </button>
                  <button className="p-2 hover:bg-gray-100 rounded-lg transition-colors">
                    <Download className="w-5 h-5 text-gray-600" />
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4 mb-4">
                <div>
                  <p className="text-xs text-gray-600 mb-1">Order Date</p>
                  <p className="text-sm font-medium text-gray-900">{new Date(po.date).toLocaleDateString()}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-600 mb-1">Expected Delivery</p>
                  <p className="text-sm font-medium text-gray-900">{new Date(po.expectedDelivery).toLocaleDateString()}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-600 mb-1">Items</p>
                  <p className="text-sm font-medium text-gray-900">{po.items.length} products</p>
                </div>
                <div>
                  <p className="text-xs text-gray-600 mb-1">Total Amount</p>
                  <p className="text-sm font-semibold text-gray-900">₹{po.total.toLocaleString()}</p>
                </div>
              </div>

              {/* Items Preview */}
              <div className="border-t border-gray-200 pt-3">
                <p className="text-xs text-gray-600 mb-2">Items:</p>
                <div className="space-y-1">
                  {po.items.map((item, idx) => (
                    <div key={idx} className="flex items-center justify-between text-sm">
                      <span className="text-gray-700">{item.productName} (x{item.quantity})</span>
                      <span className="font-medium text-gray-900">₹{item.total.toLocaleString()}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ))}

          {filteredPOs.length === 0 && (
            <div className="text-center py-12">
              <Package className="w-12 h-12 text-gray-400 mx-auto mb-3" />
              <p className="text-gray-500">No purchase orders found</p>
            </div>
          )}
        </div>
      ) : activeTab === 'suppliers' ? (
        /* Suppliers List */
        <div className="grid grid-cols-1 desktop:grid-cols-2 gap-4">
          {filteredSuppliers.map((supplier) => (
            <div key={supplier.id} className="card hover:shadow-lg transition-shadow">
              <div className="flex items-start justify-between mb-4">
                <div className="flex-1">
                  <div className="flex items-center gap-3 mb-2">
                    <h3 className="text-lg font-semibold text-gray-900">{supplier.name}</h3>
                    <span className="px-2 py-1 bg-gray-100 text-gray-700 text-xs rounded">
                      {supplier.code}
                    </span>
                  </div>
                  <div className="flex items-center gap-1 mb-2">
                    {[...Array(5)].map((_, i) => (
                      <svg
                        key={i}
                        className={`w-4 h-4 ${i < Math.floor(supplier.rating) ? 'text-yellow-400' : 'text-gray-300'}`}
                        fill="currentColor"
                        viewBox="0 0 20 20"
                      >
                        <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
                      </svg>
                    ))}
                    <span className="text-sm text-gray-600 ml-2">{supplier.rating}/5</span>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button className="p-2 hover:bg-gray-100 rounded-lg transition-colors">
                    <Edit className="w-5 h-5 text-gray-600" />
                  </button>
                </div>
              </div>

              <div className="space-y-2 mb-4">
                <div className="flex items-center gap-2 text-sm">
                  <User className="w-4 h-4 text-gray-400" />
                  <span className="text-gray-700">{supplier.contactPerson}</span>
                </div>
                <div className="flex items-center gap-2 text-sm">
                  <Phone className="w-4 h-4 text-gray-400" />
                  <span className="text-gray-700">{supplier.phone}</span>
                </div>
                <div className="flex items-center gap-2 text-sm">
                  <Mail className="w-4 h-4 text-gray-400" />
                  <span className="text-gray-700">{supplier.email}</span>
                </div>
                <div className="flex items-center gap-2 text-sm">
                  <MapPin className="w-4 h-4 text-gray-400" />
                  <span className="text-gray-700">{supplier.city}, {supplier.state}</span>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-3 p-3 bg-gray-50 rounded-lg mb-3">
                <div>
                  <p className="text-xs text-gray-600">On-Time Delivery</p>
                  <p className="text-sm font-semibold text-gray-900">{supplier.performance.onTimeDelivery}%</p>
                </div>
                <div>
                  <p className="text-xs text-gray-600">Quality Score</p>
                  <p className="text-sm font-semibold text-gray-900">{supplier.performance.qualityScore}%</p>
                </div>
                <div>
                  <p className="text-xs text-gray-600">Price Score</p>
                  <p className="text-sm font-semibold text-gray-900">{supplier.performance.priceCompetitiveness}%</p>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <p className="text-xs text-gray-600">Total Purchases</p>
                  <p className="font-semibold text-gray-900">₹{(supplier.totalPurchases / 100000).toFixed(1)}L</p>
                </div>
                <div>
                  <p className="text-xs text-gray-600">Outstanding</p>
                  <p className={`font-semibold ${supplier.currentOutstanding > supplier.creditLimit * 0.8 ? 'text-red-600' : 'text-gray-900'}`}>
                    ₹{(supplier.currentOutstanding / 100000).toFixed(1)}L
                  </p>
                </div>
              </div>
            </div>
          ))}

          {filteredSuppliers.length === 0 && (
            <div className="col-span-2 text-center py-12">
              <Truck className="w-12 h-12 text-gray-400 mx-auto mb-3" />
              <p className="text-gray-500">No suppliers found</p>
            </div>
          )}
        </div>
      ) : (
        /* Analytics */
        <div className="space-y-6">
          <div className="grid grid-cols-1 tablet:grid-cols-4 gap-4">
            <div className="card">
              <div className="flex items-center gap-3 mb-2">
                <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
                  <ShoppingBag className="w-5 h-5 text-blue-600" />
                </div>
                <div>
                  <p className="text-sm text-gray-600">Total POs</p>
                  <p className="text-2xl font-bold text-gray-900">{purchaseOrders.length}</p>
                </div>
              </div>
            </div>
            <div className="card">
              <div className="flex items-center gap-3 mb-2">
                <div className="w-10 h-10 bg-green-100 rounded-lg flex items-center justify-center">
                  <DollarSign className="w-5 h-5 text-green-600" />
                </div>
                <div>
                  <p className="text-sm text-gray-600">Total Value</p>
                  <p className="text-2xl font-bold text-gray-900">
                    ₹{(purchaseOrders.reduce((sum, po) => sum + po.total, 0) / 100000).toFixed(1)}L
                  </p>
                </div>
              </div>
            </div>
            <div className="card">
              <div className="flex items-center gap-3 mb-2">
                <div className="w-10 h-10 bg-purple-100 rounded-lg flex items-center justify-center">
                  <Truck className="w-5 h-5 text-purple-600" />
                </div>
                <div>
                  <p className="text-sm text-gray-600">Active Suppliers</p>
                  <p className="text-2xl font-bold text-gray-900">{suppliers.length}</p>
                </div>
              </div>
            </div>
            <div className="card">
              <div className="flex items-center gap-3 mb-2">
                <div className="w-10 h-10 bg-orange-100 rounded-lg flex items-center justify-center">
                  <Clock className="w-5 h-5 text-orange-600" />
                </div>
                <div>
                  <p className="text-sm text-gray-600">Pending Approval</p>
                  <p className="text-2xl font-bold text-gray-900">
                    {purchaseOrders.filter(po => po.status === 'PENDING').length}
                  </p>
                </div>
              </div>
            </div>
          </div>

          <div className="card">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Supplier Performance Ranking</h3>
            <div className="space-y-3">
              {suppliers
                .sort((a, b) => {
                  const scoreA = (a.performance.onTimeDelivery + a.performance.qualityScore + a.performance.priceCompetitiveness) / 3;
                  const scoreB = (b.performance.onTimeDelivery + b.performance.qualityScore + b.performance.priceCompetitiveness) / 3;
                  return scoreB - scoreA;
                })
                .map((supplier, index) => {
                  const avgScore = (supplier.performance.onTimeDelivery + supplier.performance.qualityScore + supplier.performance.priceCompetitiveness) / 3;
                  return (
                    <div key={supplier.id} className="flex items-center gap-4 p-3 bg-gray-50 rounded-lg">
                      <div className={`w-8 h-8 rounded-full flex items-center justify-center font-bold ${
                        index === 0 ? 'bg-yellow-100 text-yellow-800' :
                        index === 1 ? 'bg-gray-200 text-gray-700' :
                        index === 2 ? 'bg-orange-100 text-orange-700' :
                        'bg-gray-100 text-gray-600'
                      }`}>
                        {index + 1}
                      </div>
                      <div className="flex-1">
                        <p className="font-medium text-gray-900">{supplier.name}</p>
                        <div className="flex items-center gap-4 mt-1">
                          <span className="text-xs text-gray-600">Delivery: {supplier.performance.onTimeDelivery}%</span>
                          <span className="text-xs text-gray-600">Quality: {supplier.performance.qualityScore}%</span>
                          <span className="text-xs text-gray-600">Price: {supplier.performance.priceCompetitiveness}%</span>
                        </div>
                      </div>
                      <div className="text-right">
                        <p className="text-lg font-bold text-gray-900">{avgScore.toFixed(1)}%</p>
                        <p className="text-xs text-gray-600">Overall Score</p>
                      </div>
                    </div>
                  );
                })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
