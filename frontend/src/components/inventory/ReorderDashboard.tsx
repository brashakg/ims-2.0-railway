// ============================================================================
// IMS 2.0 - Reorder Dashboard
// ============================================================================
// Monitor products requiring reorder and generate purchase orders

import { useState, useEffect } from 'react';
import {
  TrendingDown,
  AlertTriangle,
  Package,
  ShoppingCart,
  Loader2,
  Eye,
  Settings,
  FileText,
  Check,
  X,
  Calendar,
  BarChart3,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { ReorderPointModal, type ReorderPointData } from './ReorderPointModal';

interface Product {
  id: string;
  sku: string;
  name: string;
  brand: string;
  category: string;
  currentStock: number;
  reservedStock: number;
  reorderPoint: number;
  reorderQuantity: number;
  maxStock: number;
  leadTimeDays: number;
  averageSalesPerDay: number;
  lastOrderDate?: string;
  supplierId?: string;
  supplierName?: string;
  unitCost?: number;
}

export function ReorderDashboard() {
  const { user } = useAuth();
  const toast = useToast();

  const [products, setProducts] = useState<Product[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedProduct, setSelectedProduct] = useState<Product | null>(null);
  const [showConfigModal, setShowConfigModal] = useState(false);
  const [selectedProducts, setSelectedProducts] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState<'all' | 'critical' | 'low'>('all');

  useEffect(() => {
    loadProducts();
  }, [user?.activeStoreId]);

  const loadProducts = async () => {
    setIsLoading(true);
    try {
      // Mock data - in production, fetch from API
      await new Promise(resolve => setTimeout(resolve, 1000));

      const mockProducts: Product[] = [
        {
          id: '1',
          sku: 'FR-001',
          name: 'Ray-Ban Aviator Classic',
          brand: 'Ray-Ban',
          category: 'Frames',
          currentStock: 5,
          reservedStock: 2,
          reorderPoint: 10,
          reorderQuantity: 50,
          maxStock: 60,
          leadTimeDays: 7,
          averageSalesPerDay: 2.5,
          lastOrderDate: '2025-01-15',
          supplierName: 'Luxottica India',
          unitCost: 1200,
        },
        {
          id: '2',
          sku: 'CL-045',
          name: 'Acuvue Oasys Monthly',
          brand: 'Acuvue',
          category: 'Contact Lenses',
          currentStock: 8,
          reservedStock: 3,
          reorderPoint: 15,
          reorderQuantity: 100,
          maxStock: 115,
          leadTimeDays: 5,
          averageSalesPerDay: 4.2,
          lastOrderDate: '2025-01-20',
          supplierName: 'Johnson & Johnson',
          unitCost: 850,
        },
        {
          id: '3',
          sku: 'SG-112',
          name: 'Oakley Holbrook',
          brand: 'Oakley',
          category: 'Sunglasses',
          currentStock: 12,
          reservedStock: 1,
          reorderPoint: 12,
          reorderQuantity: 40,
          maxStock: 52,
          leadTimeDays: 10,
          averageSalesPerDay: 1.8,
          lastOrderDate: '2025-01-10',
          supplierName: 'Luxottica India',
          unitCost: 2500,
        },
        {
          id: '4',
          sku: 'ACC-023',
          name: 'Lens Cleaning Solution 120ml',
          brand: 'Opticare',
          category: 'Accessories',
          currentStock: 3,
          reservedStock: 0,
          reorderPoint: 20,
          reorderQuantity: 200,
          maxStock: 220,
          leadTimeDays: 3,
          averageSalesPerDay: 6.5,
          lastOrderDate: '2025-01-28',
          supplierName: 'Optics Wholesale',
          unitCost: 85,
        },
      ];

      setProducts(mockProducts);
    } catch (error: any) {
      toast.error('Failed to load products');
    } finally {
      setIsLoading(false);
    }
  };

  const handleSaveReorderPoint = async (data: ReorderPointData) => {
    try {
      // In production, save to API
      await new Promise(resolve => setTimeout(resolve, 500));

      // Update local state
      setProducts(products.map(p =>
        p.id === data.productId
          ? {
              ...p,
              reorderPoint: data.reorderPoint,
              reorderQuantity: data.reorderQuantity,
              maxStock: data.maxStock,
              leadTimeDays: data.leadTimeDays,
            }
          : p
      ));

      toast.success('Reorder point updated successfully');
    } catch (error: any) {
      throw new Error(error?.message || 'Failed to save reorder point');
    }
  };

  const handleGeneratePO = async () => {
    if (selectedProducts.size === 0) {
      toast.error('Please select at least one product');
      return;
    }

    try {
      const selectedItems = products.filter(p => selectedProducts.has(p.id));
      const totalCost = selectedItems.reduce((sum, p) => sum + ((p.unitCost || 0) * p.reorderQuantity), 0);

      // In production, create PO via API
      await new Promise(resolve => setTimeout(resolve, 1000));

      toast.success(`Purchase Order created for ${selectedProducts.size} products (₹${totalCost.toLocaleString('en-IN')})`);
      setSelectedProducts(new Set());
      await loadProducts();
    } catch (error: any) {
      toast.error('Failed to generate purchase order');
    }
  };

  const toggleProductSelection = (productId: string) => {
    const newSelection = new Set(selectedProducts);
    if (newSelection.has(productId)) {
      newSelection.delete(productId);
    } else {
      newSelection.add(productId);
    }
    setSelectedProducts(newSelection);
  };

  const selectAll = () => {
    const filteredIds = getFilteredProducts().map(p => p.id);
    setSelectedProducts(new Set(filteredIds));
  };

  const deselectAll = () => {
    setSelectedProducts(new Set());
  };

  const getStockStatus = (product: Product) => {
    const availableStock = product.currentStock - product.reservedStock;
    if (availableStock <= 0) return 'out-of-stock';
    if (availableStock <= product.reorderPoint * 0.5) return 'critical';
    if (availableStock <= product.reorderPoint) return 'low';
    return 'healthy';
  };

  const getDaysUntilStockout = (product: Product) => {
    const availableStock = product.currentStock - product.reservedStock;
    if (product.averageSalesPerDay === 0) return Infinity;
    return Math.floor(availableStock / product.averageSalesPerDay);
  };

  const getFilteredProducts = () => {
    return products.filter(p => {
      const status = getStockStatus(p);
      if (filter === 'critical') return status === 'critical' || status === 'out-of-stock';
      if (filter === 'low') return status === 'low';
      return status === 'critical' || status === 'out-of-stock' || status === 'low';
    });
  };

  const filteredProducts = getFilteredProducts();
  const criticalCount = products.filter(p => ['critical', 'out-of-stock'].includes(getStockStatus(p))).length;
  const lowCount = products.filter(p => getStockStatus(p) === 'low').length;
  const totalValue = filteredProducts.reduce((sum, p) => sum + ((p.unitCost || 0) * p.reorderQuantity), 0);

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-gray-900 flex items-center gap-2">
            <TrendingDown className="w-6 h-6 text-orange-600" />
            Reorder Dashboard
          </h2>
          <p className="text-sm text-gray-500 mt-1">
            Monitor stock levels and generate purchase orders
          </p>
        </div>
        {selectedProducts.size > 0 && (
          <button
            onClick={handleGeneratePO}
            className="btn-primary flex items-center gap-2"
          >
            <ShoppingCart className="w-4 h-4" />
            Generate PO ({selectedProducts.size})
          </button>
        )}
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 tablet:grid-cols-4 gap-4">
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-red-100 rounded-lg flex items-center justify-center">
              <AlertTriangle className="w-5 h-5 text-red-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Critical</p>
              <p className="text-2xl font-bold text-red-600">{criticalCount}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-yellow-100 rounded-lg flex items-center justify-center">
              <TrendingDown className="w-5 h-5 text-yellow-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Low Stock</p>
              <p className="text-2xl font-bold text-yellow-600">{lowCount}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
              <Package className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Total Products</p>
              <p className="text-2xl font-bold text-blue-600">{filteredProducts.length}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-green-100 rounded-lg flex items-center justify-center">
              <FileText className="w-5 h-5 text-green-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Est. PO Value</p>
              <p className="text-2xl font-bold text-green-600">
                ₹{(totalValue / 100000).toFixed(1)}L
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Filters */}
      <div className="flex items-center justify-between">
        <div className="flex bg-gray-100 rounded-lg p-1">
          <button
            onClick={() => setFilter('all')}
            className={`px-4 py-2 text-sm font-medium rounded-md transition-colors ${
              filter === 'all'
                ? 'bg-white text-purple-600 shadow-sm'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            All ({filteredProducts.length})
          </button>
          <button
            onClick={() => setFilter('critical')}
            className={`px-4 py-2 text-sm font-medium rounded-md transition-colors ${
              filter === 'critical'
                ? 'bg-white text-purple-600 shadow-sm'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            Critical ({criticalCount})
          </button>
          <button
            onClick={() => setFilter('low')}
            className={`px-4 py-2 text-sm font-medium rounded-md transition-colors ${
              filter === 'low'
                ? 'bg-white text-purple-600 shadow-sm'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            Low Stock ({lowCount})
          </button>
        </div>

        {filteredProducts.length > 0 && (
          <div className="flex items-center gap-2">
            <button onClick={selectAll} className="text-sm text-purple-600 hover:text-purple-700">
              Select All
            </button>
            <span className="text-gray-300">|</span>
            <button onClick={deselectAll} className="text-sm text-gray-600 hover:text-gray-700">
              Deselect All
            </button>
          </div>
        )}
      </div>

      {/* Products Table */}
      {isLoading ? (
        <div className="card flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 animate-spin text-purple-600" />
        </div>
      ) : filteredProducts.length === 0 ? (
        <div className="card text-center py-12 text-gray-500">
          <Package className="w-12 h-12 mx-auto mb-2 opacity-50" />
          <p className="font-medium">All products are well-stocked!</p>
          <p className="text-sm">No products require reordering at this time</p>
        </div>
      ) : (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="px-4 py-3 text-left">
                    <input
                      type="checkbox"
                      checked={filteredProducts.length > 0 && selectedProducts.size === filteredProducts.length}
                      onChange={(e) => e.target.checked ? selectAll() : deselectAll()}
                      className="rounded text-purple-600 focus:ring-purple-500"
                    />
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Product
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Category
                  </th>
                  <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">
                    Current
                  </th>
                  <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">
                    Reorder At
                  </th>
                  <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">
                    Order Qty
                  </th>
                  <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">
                    Status
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Supplier
                  </th>
                  <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {filteredProducts.map((product) => {
                  const status = getStockStatus(product);
                  const daysUntilStockout = getDaysUntilStockout(product);
                  const isSelected = selectedProducts.has(product.id);

                  return (
                    <tr key={product.id} className={`hover:bg-gray-50 ${isSelected ? 'bg-purple-50' : ''}`}>
                      <td className="px-4 py-3">
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleProductSelection(product.id)}
                          className="rounded text-purple-600 focus:ring-purple-500"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <div>
                          <p className="font-medium text-gray-900">{product.name}</p>
                          <p className="text-sm text-gray-500">SKU: {product.sku}</p>
                          <p className="text-xs text-gray-400">{product.brand}</p>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-600">{product.category}</td>
                      <td className="px-4 py-3 text-center">
                        <div className="flex flex-col items-center">
                          <span className="font-medium text-gray-900">{product.currentStock}</span>
                          {product.reservedStock > 0 && (
                            <span className="text-xs text-orange-600">
                              ({product.reservedStock} reserved)
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-center text-sm text-gray-900">
                        {product.reorderPoint}
                      </td>
                      <td className="px-4 py-3 text-center">
                        <span className="font-medium text-purple-600">{product.reorderQuantity}</span>
                        {product.unitCost && (
                          <p className="text-xs text-gray-500">
                            ₹{(product.unitCost * product.reorderQuantity).toLocaleString('en-IN')}
                          </p>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-col items-center gap-1">
                          {status === 'out-of-stock' && (
                            <span className="px-2 py-1 bg-red-100 text-red-800 text-xs font-medium rounded-full whitespace-nowrap">
                              Out of Stock
                            </span>
                          )}
                          {status === 'critical' && (
                            <span className="px-2 py-1 bg-red-100 text-red-800 text-xs font-medium rounded-full whitespace-nowrap">
                              Critical
                            </span>
                          )}
                          {status === 'low' && (
                            <span className="px-2 py-1 bg-yellow-100 text-yellow-800 text-xs font-medium rounded-full whitespace-nowrap">
                              Low Stock
                            </span>
                          )}
                          {daysUntilStockout < 30 && daysUntilStockout > 0 && (
                            <span className="text-xs text-gray-500 flex items-center gap-1">
                              <Calendar className="w-3 h-3" />
                              {daysUntilStockout}d left
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <div>
                          <p className="text-sm text-gray-900">{product.supplierName}</p>
                          <p className="text-xs text-gray-500">
                            Lead: {product.leadTimeDays}d
                          </p>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-center gap-2">
                          <button
                            onClick={() => {
                              setSelectedProduct(product);
                              setShowConfigModal(true);
                            }}
                            className="p-2 text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
                            title="Configure reorder point"
                          >
                            <Settings className="w-4 h-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Reorder Point Configuration Modal */}
      {selectedProduct && (
        <ReorderPointModal
          isOpen={showConfigModal}
          onClose={() => {
            setShowConfigModal(false);
            setSelectedProduct(null);
          }}
          product={{
            id: selectedProduct.id,
            sku: selectedProduct.sku,
            name: selectedProduct.name,
            brand: selectedProduct.brand,
            currentStock: selectedProduct.currentStock,
            reorderPoint: selectedProduct.reorderPoint,
            reorderQuantity: selectedProduct.reorderQuantity,
            maxStock: selectedProduct.maxStock,
            averageSalesPerDay: selectedProduct.averageSalesPerDay,
            leadTimeDays: selectedProduct.leadTimeDays,
          }}
          onSave={handleSaveReorderPoint}
        />
      )}
    </div>
  );
}
