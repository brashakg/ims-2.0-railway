// ============================================================================
// IMS 2.0 - Serial Number Tracker
// ============================================================================
// Track and manage serialized inventory items

import { useState, useEffect } from 'react';
import {
  Hash,
  Search,
  Package,
  Shield,
  AlertTriangle,
  CheckCircle,
  Loader2,
  Eye,
  Edit,
  Download,
  Filter,
  Calendar,
  MapPin,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { SerialNumberModal, type SerialNumberData } from './SerialNumberModal';

interface SerializedItem extends SerialNumberData {
  productName: string;
  productSku: string;
  productBrand: string;
  productCategory: string;
  soldToCustomer?: string;
  warrantyStatus: 'ACTIVE' | 'EXPIRED' | 'NONE';
}

type StatusFilter = 'all' | 'IN_STOCK' | 'SOLD' | 'WARRANTY_CLAIM' | 'DAMAGED' | 'LOST_STOLEN';

export function SerialNumberTracker() {
  const { user } = useAuth();
  const toast = useToast();

  const [items, setItems] = useState<SerializedItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [showModal, setShowModal] = useState(false);
  const [selectedItem, setSelectedItem] = useState<SerializedItem | null>(null);

  useEffect(() => {
    loadSerializedItems();
  }, [user?.activeStoreId]);

  const loadSerializedItems = async () => {
    setIsLoading(true);
    try {
      // Mock data - in production, fetch from API
      await new Promise(resolve => setTimeout(resolve, 1000));

      const mockItems: SerializedItem[] = [
        {
          id: '1',
          productId: 'p1',
          productName: 'Phonak Audeo Paradise P90-R',
          productSku: 'HA-001',
          productBrand: 'Phonak',
          productCategory: 'Hearing Aids',
          serialNumber: 'PHA-2025-001234',
          status: 'SOLD',
          soldDate: '2025-01-15',
          soldToCustomer: 'Mr. Rajesh Kumar',
          purchaseDate: '2024-12-01',
          warrantyMonths: 24,
          warrantyExpiryDate: '2026-12-01',
          warrantyStatus: 'ACTIVE',
          supplierBatch: 'BATCH-2024-Q4',
        },
        {
          id: '2',
          productId: 'p2',
          productName: 'Apple Watch Series 9 GPS 45mm',
          productSku: 'SMTWT-002',
          productBrand: 'Apple',
          productCategory: 'Smart Watches',
          serialNumber: 'AW-2025-567890',
          status: 'IN_STOCK',
          locationCode: 'C2-05',
          purchaseDate: '2025-01-01',
          warrantyMonths: 12,
          warrantyExpiryDate: '2026-01-01',
          warrantyStatus: 'ACTIVE',
          supplierBatch: 'BATCH-2025-001',
        },
        {
          id: '3',
          productId: 'p3',
          productName: 'Starkey Livio Edge AI 2400',
          productSku: 'HA-015',
          productBrand: 'Starkey',
          productCategory: 'Hearing Aids',
          serialNumber: 'STK-2024-998877',
          status: 'WARRANTY_CLAIM',
          soldDate: '2024-08-10',
          soldToCustomer: 'Mrs. Priya Sharma',
          purchaseDate: '2024-07-15',
          warrantyMonths: 36,
          warrantyExpiryDate: '2027-07-15',
          warrantyStatus: 'ACTIVE',
          notes: 'Customer reported intermittent connectivity issues',
        },
        {
          id: '4',
          productId: 'p4',
          productName: 'Samsung Galaxy Watch 6 Classic',
          productSku: 'SMTWT-008',
          productBrand: 'Samsung',
          productCategory: 'Smart Watches',
          serialNumber: 'GW6-2025-112233',
          status: 'IN_STOCK',
          locationCode: 'C2-08',
          purchaseDate: '2025-01-20',
          warrantyMonths: 12,
          warrantyExpiryDate: '2026-01-20',
          warrantyStatus: 'ACTIVE',
        },
        {
          id: '5',
          productId: 'p5',
          productName: 'Ray-Ban Meta Smart Glasses',
          productSku: 'SMTSG-003',
          productBrand: 'Ray-Ban',
          productCategory: 'Smart Sunglasses',
          serialNumber: 'RBM-2024-445566',
          status: 'DAMAGED',
          locationCode: 'DAMAGED-A',
          purchaseDate: '2024-11-10',
          warrantyMonths: 12,
          warrantyExpiryDate: '2025-11-10',
          warrantyStatus: 'EXPIRED',
          notes: 'Dropped by customer during trial, lens cracked',
        },
      ];

      setItems(mockItems);
    } catch (error: any) {
      toast.error('Failed to load serialized items');
    } finally {
      setIsLoading(false);
    }
  };

  const handleSaveSerial = async (data: SerialNumberData) => {
    try {
      // In production, save to API
      await new Promise(resolve => setTimeout(resolve, 500));

      // Update local state
      if (data.id) {
        // Edit existing
        setItems(
          items.map((item) =>
            item.id === data.id
              ? {
                  ...item,
                  ...data,
                  warrantyStatus:
                    data.warrantyExpiryDate && new Date(data.warrantyExpiryDate) > new Date()
                      ? 'ACTIVE'
                      : 'EXPIRED',
                }
              : item
          )
        );
      } else {
        // Add new - would need full product details from API
        toast.success('Serial number added successfully');
      }

      await loadSerializedItems();
    } catch (error: any) {
      throw new Error(error?.message || 'Failed to save serial number');
    }
  };

  const getStatusBadge = (status: SerializedItem['status']) => {
    const statusConfig = {
      IN_STOCK: { label: 'In Stock', color: 'bg-blue-100 text-blue-800', icon: Package },
      SOLD: { label: 'Sold', color: 'bg-green-100 text-green-800', icon: CheckCircle },
      WARRANTY_CLAIM: {
        label: 'Warranty Claim',
        color: 'bg-yellow-100 text-yellow-800',
        icon: Shield,
      },
      DAMAGED: { label: 'Damaged', color: 'bg-red-100 text-red-800', icon: AlertTriangle },
      LOST_STOLEN: {
        label: 'Lost/Stolen',
        color: 'bg-purple-100 text-purple-800',
        icon: AlertTriangle,
      },
    };

    const config = statusConfig[status];
    const Icon = config.icon;

    return (
      <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium ${config.color}`}>
        <Icon className="w-3.5 h-3.5" />
        {config.label}
      </span>
    );
  };

  const getFilteredItems = () => {
    return items.filter((item) => {
      const matchesStatus = statusFilter === 'all' || item.status === statusFilter;
      const matchesSearch =
        searchQuery === '' ||
        item.serialNumber.toLowerCase().includes(searchQuery.toLowerCase()) ||
        item.productName.toLowerCase().includes(searchQuery.toLowerCase()) ||
        item.productSku.toLowerCase().includes(searchQuery.toLowerCase()) ||
        item.soldToCustomer?.toLowerCase().includes(searchQuery.toLowerCase());

      return matchesStatus && matchesSearch;
    });
  };

  const filteredItems = getFilteredItems();

  // Stats
  const totalItems = items.length;
  const inStockCount = items.filter((i) => i.status === 'IN_STOCK').length;
  const soldCount = items.filter((i) => i.status === 'SOLD').length;
  const warrantyCount = items.filter((i) => i.status === 'WARRANTY_CLAIM').length;
  const activeWarranties = items.filter((i) => i.warrantyStatus === 'ACTIVE').length;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-gray-900 flex items-center gap-2">
            <Hash className="w-6 h-6 text-blue-600" />
            Serial Number Tracker
          </h2>
          <p className="text-sm text-gray-500 mt-1">
            Track high-value items by serial number
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => toast.info('Export feature coming soon')}
            className="btn-outline text-sm flex items-center gap-2"
          >
            <Download className="w-4 h-4" />
            Export
          </button>
        </div>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 tablet:grid-cols-5 gap-4">
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-purple-100 rounded-lg flex items-center justify-center">
              <Hash className="w-5 h-5 text-purple-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Total Items</p>
              <p className="text-2xl font-bold text-gray-900">{totalItems}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
              <Package className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">In Stock</p>
              <p className="text-2xl font-bold text-blue-600">{inStockCount}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-green-100 rounded-lg flex items-center justify-center">
              <CheckCircle className="w-5 h-5 text-green-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Sold</p>
              <p className="text-2xl font-bold text-green-600">{soldCount}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-yellow-100 rounded-lg flex items-center justify-center">
              <Shield className="w-5 h-5 text-yellow-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Warranty Claims</p>
              <p className="text-2xl font-bold text-yellow-600">{warrantyCount}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-emerald-100 rounded-lg flex items-center justify-center">
              <Shield className="w-5 h-5 text-emerald-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Active Warranties</p>
              <p className="text-2xl font-bold text-emerald-600">{activeWarranties}</p>
            </div>
          </div>
        </div>
      </div>

      {/* Search and Filters */}
      <div className="card">
        <div className="flex flex-col tablet:flex-row gap-4">
          {/* Search */}
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search by serial number, product, or customer..."
              className="input-field pl-10 w-full"
            />
          </div>

          {/* Status Filter */}
          <div className="flex items-center gap-2">
            <Filter className="w-5 h-5 text-gray-500" />
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
              className="input-field"
            >
              <option value="all">All Status</option>
              <option value="IN_STOCK">In Stock</option>
              <option value="SOLD">Sold</option>
              <option value="WARRANTY_CLAIM">Warranty Claim</option>
              <option value="DAMAGED">Damaged</option>
              <option value="LOST_STOLEN">Lost/Stolen</option>
            </select>
          </div>
        </div>
      </div>

      {/* Items Table */}
      {isLoading ? (
        <div className="card flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 animate-spin text-purple-600" />
        </div>
      ) : filteredItems.length === 0 ? (
        <div className="card text-center py-12 text-gray-500">
          <Hash className="w-12 h-12 mx-auto mb-2 opacity-50" />
          <p className="font-medium">No serialized items found</p>
          <p className="text-sm">Try adjusting your search or filters</p>
        </div>
      ) : (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Serial Number
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Product
                  </th>
                  <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">
                    Status
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Location/Customer
                  </th>
                  <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">
                    Warranty
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Purchase Date
                  </th>
                  <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {filteredItems.map((item) => (
                  <tr key={item.id} className="hover:bg-gray-50">
                    <td className="px-4 py-3">
                      <span className="font-mono text-sm font-medium text-purple-600">
                        {item.serialNumber}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <div>
                        <p className="font-medium text-gray-900">{item.productName}</p>
                        <p className="text-sm text-gray-500">
                          {item.productBrand} • {item.productSku}
                        </p>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-center">{getStatusBadge(item.status)}</td>
                    <td className="px-4 py-3">
                      {item.status === 'IN_STOCK' ? (
                        <div className="flex items-center gap-1.5 text-sm text-gray-600">
                          <MapPin className="w-4 h-4" />
                          {item.locationCode || 'Not assigned'}
                        </div>
                      ) : item.soldToCustomer ? (
                        <div className="text-sm text-gray-900">{item.soldToCustomer}</div>
                      ) : (
                        <span className="text-sm text-gray-400">-</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-center">
                      {item.warrantyStatus === 'ACTIVE' ? (
                        <div className="flex flex-col items-center gap-1">
                          <span className="px-2 py-1 bg-green-100 text-green-800 text-xs font-medium rounded-full">
                            Active
                          </span>
                          <span className="text-xs text-gray-500">
                            Until {item.warrantyExpiryDate && new Date(item.warrantyExpiryDate).toLocaleDateString()}
                          </span>
                        </div>
                      ) : item.warrantyStatus === 'EXPIRED' ? (
                        <span className="px-2 py-1 bg-gray-100 text-gray-600 text-xs font-medium rounded-full">
                          Expired
                        </span>
                      ) : (
                        <span className="text-sm text-gray-400">-</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1.5 text-sm text-gray-600">
                        <Calendar className="w-4 h-4" />
                        {item.purchaseDate && new Date(item.purchaseDate).toLocaleDateString()}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-center gap-2">
                        <button
                          onClick={() => {
                            setSelectedItem(item);
                            setShowModal(true);
                          }}
                          className="p-2 text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
                          title="Edit serial number"
                        >
                          <Edit className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Serial Number Modal */}
      {showModal && selectedItem && (
        <SerialNumberModal
          isOpen={showModal}
          onClose={() => {
            setShowModal(false);
            setSelectedItem(null);
          }}
          product={{
            id: selectedItem.productId,
            sku: selectedItem.productSku,
            name: selectedItem.productName,
            brand: selectedItem.productBrand,
            category: selectedItem.productCategory,
          }}
          editData={selectedItem}
          onSave={handleSaveSerial}
        />
      )}
    </div>
  );
}
