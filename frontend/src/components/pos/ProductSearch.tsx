// ============================================================================
// IMS 2.0 - Product Search Component
// ============================================================================
// NO MOCK DATA - Uses real API calls

import { useState, useCallback, useEffect } from 'react';
import { Search, Scan, Grid, List, FileText, Tag, Package, Loader2, RefreshCw } from 'lucide-react';
import type { ProductCategory } from '../../types';
import { inventoryApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import clsx from 'clsx';

interface Product {
  id: string;
  name: string;
  sku: string;
  category: ProductCategory;
  brand: string;
  mrp: number;
  offerPrice: number;
  stock: number;
  barcode?: string;
}

interface ProductSearchProps {
  onAddProduct: (product: {
    id: string;
    name: string;
    sku: string;
    category: ProductCategory;
    mrp: number;
    offerPrice: number;
    stockId?: string;
    barcode?: string;
  }) => void;
  onAddPrescription: () => void;
  hasPrescription: boolean;
}

// Category configuration
const CATEGORIES: { code: ProductCategory; label: string; icon: string; color: string }[] = [
  { code: 'FRAME', label: 'Frames', icon: '👓', color: 'bg-blue-100 text-blue-700' },
  { code: 'SUNGLASS', label: 'Sunglasses', icon: '🕶️', color: 'bg-amber-100 text-amber-700' },
  { code: 'READING_GLASSES', label: 'Reading', icon: '📖', color: 'bg-green-100 text-green-700' },
  { code: 'OPTICAL_LENS', label: 'Lenses', icon: '🔍', color: 'bg-purple-100 text-purple-700' },
  { code: 'CONTACT_LENS', label: 'Contacts', icon: '👁️', color: 'bg-cyan-100 text-cyan-700' },
  { code: 'COLORED_CONTACT_LENS', label: 'Color CL', icon: '🎨', color: 'bg-pink-100 text-pink-700' },
  { code: 'WATCH', label: 'Watches', icon: '⌚', color: 'bg-yellow-100 text-yellow-700' },
  { code: 'SMARTWATCH', label: 'Smart', icon: '📱', color: 'bg-indigo-100 text-indigo-700' },
  { code: 'SMARTGLASSES', label: 'Smart Glass', icon: '🥽', color: 'bg-violet-100 text-violet-700' },
  { code: 'WALL_CLOCK', label: 'Clocks', icon: '🕐', color: 'bg-orange-100 text-orange-700' },
  { code: 'ACCESSORIES', label: 'Accessories', icon: '🧴', color: 'bg-gray-100 text-gray-700' },
  { code: 'SERVICES', label: 'Services', icon: '🔧', color: 'bg-teal-100 text-teal-700' },
];

export function ProductSearch({ onAddProduct, onAddPrescription, hasPrescription }: ProductSearchProps) {
  const { user } = useAuth();
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<ProductCategory | null>(null);
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');
  const [barcodeMode, setBarcodeMode] = useState(false);

  // API state
  const [products, setProducts] = useState<Product[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Load products from API
  const loadProducts = useCallback(async () => {
    if (!user?.activeStoreId) return;

    setIsLoading(true);
    setError(null);

    try {
      const response = await inventoryApi.getStock(user.activeStoreId);

      if (response?.items && Array.isArray(response.items)) {
        const stockItems: Product[] = response.items.map((item: any) => ({
          id: item.stock_id || item.id,
          name: item.product_name || item.name || 'Unknown Product',
          sku: item.sku || item.barcode || '',
          category: item.category || 'ACCESSORIES',
          brand: item.brand || 'Generic',
          mrp: item.mrp || item.cost_price || 0,
          offerPrice: item.selling_price || item.mrp || 0,
          stock: item.quantity || 0,
          barcode: item.barcode,
        }));
        setProducts(stockItems);
      } else {
        setProducts([]);
      }
    } catch (err) {
      console.error('Failed to load products:', err);
      setError('Failed to load products');
      setProducts([]);
    } finally {
      setIsLoading(false);
    }
  }, [user?.activeStoreId]);

  // Load on mount
  useEffect(() => {
    loadProducts();
  }, [loadProducts]);

  // Filter products
  const filteredProducts = products.filter(product => {
    const matchesSearch = !searchQuery ||
      product.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      product.sku.toLowerCase().includes(searchQuery.toLowerCase()) ||
      product.brand.toLowerCase().includes(searchQuery.toLowerCase());

    const matchesCategory = !selectedCategory || product.category === selectedCategory;

    return matchesSearch && matchesCategory;
  });

  // Handle barcode scan
  const handleBarcodeScan = useCallback(async (barcode: string) => {
    if (!user?.activeStoreId) return;

    try {
      const stockUnit = await inventoryApi.getStockByBarcode(barcode);
      if (stockUnit) {
        onAddProduct({
          id: stockUnit.stock_id || stockUnit.id,
          name: stockUnit.product_name || stockUnit.name,
          sku: stockUnit.sku,
          category: stockUnit.category,
          mrp: stockUnit.mrp || 0,
          offerPrice: stockUnit.selling_price || stockUnit.mrp || 0,
          barcode,
          stockId: stockUnit.stock_id,
        });
        setSearchQuery('');
      }
    } catch (err) {
      console.error('Barcode lookup failed:', err);
      // Fall back to local search
      const product = products.find(p => p.sku === barcode || p.barcode === barcode);
      if (product) {
        onAddProduct({
          id: product.id,
          name: product.name,
          sku: product.sku,
          category: product.category,
          mrp: product.mrp,
          offerPrice: product.offerPrice,
          barcode,
        });
        setSearchQuery('');
      }
    }
  }, [user?.activeStoreId, products, onAddProduct]);

  // Handle search input (also handles barcode if in barcode mode)
  const handleSearchChange = (value: string) => {
    setSearchQuery(value);

    // If barcode mode and value looks like a barcode, process it
    if (barcodeMode && value.length >= 8) {
      handleBarcodeScan(value);
    }
  };

  return (
    <div className="h-full flex flex-col">
      {/* Search Header */}
      <div className="flex gap-2 mb-4">
        <div className="relative flex-1">
          {barcodeMode ? (
            <Scan className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-bv-gold-600" />
          ) : (
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
          )}
          <input
            type="text"
            value={searchQuery}
            onChange={e => handleSearchChange(e.target.value)}
            className={clsx(
              'input-field pl-10',
              barcodeMode && 'border-bv-gold-300 focus:border-bv-gold-500'
            )}
            placeholder={barcodeMode ? 'Scan barcode...' : 'Search products...'}
            autoFocus={barcodeMode}
          />
        </div>

        {/* Refresh Button */}
        <button
          onClick={loadProducts}
          disabled={isLoading}
          className="px-3 py-2 rounded-lg border border-gray-300 text-gray-600 hover:bg-gray-50"
          title="Refresh products"
        >
          <RefreshCw className={clsx('w-5 h-5', isLoading && 'animate-spin')} />
        </button>

        {/* Barcode Toggle */}
        <button
          onClick={() => setBarcodeMode(!barcodeMode)}
          className={clsx(
            'px-3 py-2 rounded-lg border transition-colors',
            barcodeMode
              ? 'bg-bv-gold-50 border-bv-gold-300 text-bv-gold-600'
              : 'border-gray-300 text-gray-600 hover:bg-gray-50'
          )}
          title="Toggle barcode mode"
        >
          <Scan className="w-5 h-5" />
        </button>

        {/* Prescription Button */}
        <button
          onClick={onAddPrescription}
          className={clsx(
            'px-3 py-2 rounded-lg border flex items-center gap-2 transition-colors',
            hasPrescription
              ? 'bg-green-50 border-green-300 text-green-600'
              : 'border-gray-300 text-gray-600 hover:bg-gray-50'
          )}
        >
          <FileText className="w-5 h-5" />
          <span className="hidden tablet:inline">
            {hasPrescription ? 'Rx Added' : 'Add Rx'}
          </span>
        </button>

        {/* View Toggle */}
        <div className="flex border border-gray-300 rounded-lg overflow-hidden">
          <button
            onClick={() => setViewMode('grid')}
            className={clsx(
              'px-3 py-2 transition-colors',
              viewMode === 'grid' ? 'bg-gray-100' : 'hover:bg-gray-50'
            )}
          >
            <Grid className="w-5 h-5 text-gray-600" />
          </button>
          <button
            onClick={() => setViewMode('list')}
            className={clsx(
              'px-3 py-2 transition-colors',
              viewMode === 'list' ? 'bg-gray-100' : 'hover:bg-gray-50'
            )}
          >
            <List className="w-5 h-5 text-gray-600" />
          </button>
        </div>
      </div>

      {/* Category Tabs */}
      <div className="flex gap-2 mb-4 overflow-x-auto scrollbar-hide pb-2">
        <button
          onClick={() => setSelectedCategory(null)}
          className={clsx(
            'px-3 py-1.5 rounded-full text-sm font-medium whitespace-nowrap transition-colors',
            !selectedCategory
              ? 'bg-bv-gold-600 text-white'
              : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
          )}
        >
          All
        </button>
        {CATEGORIES.map(cat => (
          <button
            key={cat.code}
            onClick={() => setSelectedCategory(cat.code)}
            className={clsx(
              'px-3 py-1.5 rounded-full text-sm font-medium whitespace-nowrap transition-colors flex items-center gap-1',
              selectedCategory === cat.code
                ? 'bg-bv-gold-600 text-white'
                : `${cat.color} hover:opacity-80`
            )}
          >
            <span>{cat.icon}</span>
            <span>{cat.label}</span>
          </button>
        ))}
      </div>

      {/* Loading State */}
      {isLoading ? (
        <div className="flex-1 flex flex-col items-center justify-center text-gray-400">
          <Loader2 className="w-8 h-8 mb-2 animate-spin" />
          <p>Loading products...</p>
        </div>
      ) : error ? (
        <div className="flex-1 flex flex-col items-center justify-center text-gray-400">
          <Package className="w-12 h-12 mb-2" />
          <p>{error}</p>
          <button onClick={loadProducts} className="text-bv-gold-600 text-sm mt-2 hover:underline">
            Try again
          </button>
        </div>
      ) : (
        /* Products Grid/List */
        <div className="flex-1 overflow-y-auto min-h-0">
          {filteredProducts.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-gray-400">
              <Package className="w-12 h-12 mb-2" />
              <p>No products found</p>
              {products.length === 0 && (
                <p className="text-sm mt-1">Add products in Settings &gt; Inventory</p>
              )}
            </div>
          ) : viewMode === 'grid' ? (
            <div className="grid grid-cols-2 tablet:grid-cols-3 laptop:grid-cols-4 gap-3">
              {filteredProducts.map(product => (
                <button
                  key={product.id}
                  onClick={() => onAddProduct({
                    id: product.id,
                    name: product.name,
                    sku: product.sku,
                    category: product.category,
                    mrp: product.mrp,
                    offerPrice: product.offerPrice,
                  })}
                  className="card p-3 text-left hover:border-bv-gold-300 hover:shadow-md transition-all touch-target"
                >
                  <div className="flex justify-between items-start mb-2">
                    <span className="text-xs font-medium text-gray-500">{product.brand}</span>
                    {product.stock <= 5 && (
                      <span className="badge-warning text-xs">Low: {product.stock}</span>
                    )}
                  </div>
                  <p className="font-medium text-gray-900 text-sm line-clamp-2 mb-1">
                    {product.name}
                  </p>
                  <p className="text-xs text-gray-500 mb-2">{product.sku}</p>
                  <div className="flex items-baseline gap-2">
                    <span className="font-bold text-bv-gold-600">
                      ₹{product.offerPrice.toLocaleString('en-IN')}
                    </span>
                    {product.offerPrice < product.mrp && (
                      <span className="text-xs text-gray-400 line-through">
                        ₹{product.mrp.toLocaleString('en-IN')}
                      </span>
                    )}
                  </div>
                </button>
              ))}
            </div>
          ) : (
            <div className="space-y-2">
              {filteredProducts.map(product => (
                <button
                  key={product.id}
                  onClick={() => onAddProduct({
                    id: product.id,
                    name: product.name,
                    sku: product.sku,
                    category: product.category,
                    mrp: product.mrp,
                    offerPrice: product.offerPrice,
                  })}
                  className="w-full flex items-center gap-4 p-3 bg-white border border-gray-200 rounded-lg hover:border-bv-gold-300 hover:shadow-sm transition-all"
                >
                  <div className="w-12 h-12 bg-gray-100 rounded-lg flex items-center justify-center">
                    <Tag className="w-6 h-6 text-gray-400" />
                  </div>
                  <div className="flex-1 text-left min-w-0">
                    <p className="font-medium text-gray-900 truncate">{product.name}</p>
                    <p className="text-sm text-gray-500">
                      {product.brand} • {product.sku}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="font-bold text-bv-gold-600">
                      ₹{product.offerPrice.toLocaleString('en-IN')}
                    </p>
                    {product.offerPrice < product.mrp && (
                      <p className="text-xs text-gray-400 line-through">
                        ₹{product.mrp.toLocaleString('en-IN')}
                      </p>
                    )}
                  </div>
                  {product.stock <= 5 && (
                    <span className="badge-warning">Stock: {product.stock}</span>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default ProductSearch;
