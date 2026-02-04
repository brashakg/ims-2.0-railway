// ============================================================================
// IMS 2.0 - Product Search Modal for POS
// ============================================================================
// Searches and selects products from inventory via API
// Category-aware search with different layouts per category
// NO MOCK DATA - uses real API calls

import { useState, useEffect, useCallback } from 'react';
import { X, Search, Package, Barcode, Check, Glasses, Sun, Eye, Watch, Ear, Wrench, BookOpen, Cpu, Sparkles, Clock, Smartphone, RefreshCw, AlertCircle } from 'lucide-react';
import type { ProductCategory } from '../../types';
import { productApi, inventoryApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';

interface ProductSearchModalProps {
  onClose: () => void;
  onSelect: (product: SearchResultProduct) => void;
  category: ProductCategory | string;
  categoryLabel: string;
}

export interface SearchResultProduct {
  id: string;
  productId: string;
  productName: string;
  sku: string;
  barcode?: string;
  brand: string;
  model: string;
  color?: string;
  size?: string;
  mrp: number;
  offerPrice: number;
  category: ProductCategory | string;
  quantity: number;
  locationCode?: string;
  // Category-specific attributes
  attributes?: Record<string, string | number | boolean>;
  // Contact lens specific
  power?: string;
  baseCurve?: string;
  diameter?: string;
  boxCount?: number;
  expiryDate?: string;
  // Watch specific
  strapType?: string;
  dialSize?: string;
  // Service specific
  serviceType?: string;
  estimatedTime?: string;
}

// Category code mapping for API
const CATEGORY_CODE_MAP: Record<string, string> = {
  'spectacles': 'FR',
  'sunglasses': 'SG',
  'contact-lens': 'CL',
  'reading-glasses': 'RG',
  'smart-glasses': 'SMTFR',
  'smart-sunglasses': 'SMTSG',
  'watch': 'WT',
  'smart-watch': 'SMTWT',
  'clock': 'CK',
  'hearing-aid': 'HA',
  'accessories': 'ACC',
  'repair': 'SVC',
  // Direct codes
  'FR': 'FR',
  'SG': 'SG',
  'CL': 'CL',
  'RG': 'RG',
  'SMTFR': 'SMTFR',
  'SMTSG': 'SMTSG',
  'WT': 'WT',
  'SMTWT': 'SMTWT',
  'CK': 'CK',
  'HA': 'HA',
  'ACC': 'ACC',
  'SVC': 'SVC',
};

const getCategoryIcon = (category: string) => {
  const code = CATEGORY_CODE_MAP[category] || category;
  switch (code) {
    case 'FR': return Glasses;
    case 'SG': return Sun;
    case 'CL': return Eye;
    case 'RG': return BookOpen;
    case 'WT': return Watch;
    case 'SMTWT': return Smartphone;
    case 'CK': return Clock;
    case 'HA': return Ear;
    case 'SMTSG': return Sparkles;
    case 'SMTFR': return Cpu;
    case 'ACC': return Package;
    case 'SVC': return Wrench;
    default: return Package;
  }
};

export function ProductSearchModal({ onClose, onSelect, category, categoryLabel }: ProductSearchModalProps) {
  const { user } = useAuth();
  const [searchQuery, setSearchQuery] = useState('');
  const [products, setProducts] = useState<SearchResultProduct[]>([]);
  const [filteredProducts, setFilteredProducts] = useState<SearchResultProduct[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedBrand, setSelectedBrand] = useState<string>('');
  const [brands, setBrands] = useState<string[]>([]);

  // Get category code for API
  const categoryCode = CATEGORY_CODE_MAP[category] || category;

  // Load products on mount
  useEffect(() => {
    loadProducts();
  }, [categoryCode]);

  const loadProducts = async () => {
    setIsLoading(true);
    setError(null);

    try {
      // Get stock for current store and category
      const storeId = user?.activeStoreId;
      if (!storeId) {
        setError('No store selected');
        setIsLoading(false);
        return;
      }

      // Try to get products from inventory API
      const response = await inventoryApi.getStock(storeId);

      // Transform API response to our format
      let stockItems: SearchResultProduct[] = [];

      if (response?.items && Array.isArray(response.items)) {
        stockItems = response.items
          .filter((item: any) => {
            // Filter by category if specified
            if (categoryCode && item.category) {
              return item.category === categoryCode ||
                     item.category_code === categoryCode ||
                     item.product?.category === categoryCode;
            }
            return true;
          })
          .map((item: any) => transformStockItem(item));
      } else if (Array.isArray(response)) {
        stockItems = response
          .filter((item: any) => {
            if (categoryCode && item.category) {
              return item.category === categoryCode ||
                     item.category_code === categoryCode;
            }
            return true;
          })
          .map((item: any) => transformStockItem(item));
      }

      setProducts(stockItems);
      setFilteredProducts(stockItems);

      // Extract unique brands
      const uniqueBrands = [...new Set(stockItems.map(p => p.brand).filter(Boolean))];
      setBrands(uniqueBrands);

    } catch (err) {
      console.error('Failed to load products:', err);
      setError(err instanceof Error ? err.message : 'Failed to load products');
      setProducts([]);
      setFilteredProducts([]);
    } finally {
      setIsLoading(false);
    }
  };

  // Transform API stock item to our format
  const transformStockItem = (item: any): SearchResultProduct => {
    return {
      id: item.id || item.stock_unit_id || item._id,
      productId: item.product_id || item.productId || item.id,
      productName: item.product_name || item.productName || item.name || 'Unknown Product',
      sku: item.sku || item.SKU || '',
      barcode: item.barcode || item.serial_number,
      brand: item.brand || item.brand_name || '',
      model: item.model || item.model_no || item.modelNo || '',
      color: item.color || item.colour_code || item.colourCode || '',
      size: item.size || item.lens_size || '',
      mrp: parseFloat(item.mrp || item.MRP || item.price || 0),
      offerPrice: parseFloat(item.offer_price || item.offerPrice || item.selling_price || item.mrp || item.price || 0),
      category: item.category || item.category_code || categoryCode,
      quantity: parseInt(item.quantity || item.stock_quantity || item.available_qty || 0),
      locationCode: item.location_code || item.locationCode || item.location || '',
      attributes: item.attributes || {},
      // Contact lens
      power: item.power,
      baseCurve: item.base_curve || item.baseCurve,
      diameter: item.diameter,
      boxCount: item.box_count || item.pack,
      expiryDate: item.expiry_date || item.expiryDate,
      // Watch
      strapType: item.strap_type || item.belt_material,
      dialSize: item.dial_size || item.dialSize,
      // Service
      serviceType: item.service_type || item.serviceType,
      estimatedTime: item.estimated_time || item.estimatedTime,
    };
  };

  // Search products via API
  const handleSearch = useCallback(async (query: string) => {
    if (!query.trim()) {
      setFilteredProducts(products);
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      // Try API search first
      const response = await productApi.searchProducts(query, categoryCode);

      if (response?.items && Array.isArray(response.items)) {
        const searchResults = response.items.map((item: any) => transformStockItem(item));
        setFilteredProducts(searchResults);
      } else if (Array.isArray(response)) {
        const searchResults = response.map((item: any) => transformStockItem(item));
        setFilteredProducts(searchResults);
      } else {
        // Fallback to local filter
        const queryLower = query.toLowerCase();
        const filtered = products.filter(p =>
          p.productName.toLowerCase().includes(queryLower) ||
          p.sku.toLowerCase().includes(queryLower) ||
          p.brand.toLowerCase().includes(queryLower) ||
          p.model?.toLowerCase().includes(queryLower) ||
          p.barcode?.toLowerCase().includes(queryLower)
        );
        setFilteredProducts(filtered);
      }
    } catch (err) {
      console.error('Search failed, using local filter:', err);
      // Fallback to local filter
      const queryLower = query.toLowerCase();
      const filtered = products.filter(p =>
        p.productName.toLowerCase().includes(queryLower) ||
        p.sku.toLowerCase().includes(queryLower) ||
        p.brand.toLowerCase().includes(queryLower) ||
        p.model?.toLowerCase().includes(queryLower)
      );
      setFilteredProducts(filtered);
    } finally {
      setIsLoading(false);
    }
  }, [products, categoryCode]);

  // Filter products based on search and brand
  useEffect(() => {
    let filtered = products;

    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      filtered = filtered.filter(p =>
        p.productName.toLowerCase().includes(query) ||
        p.sku.toLowerCase().includes(query) ||
        p.brand.toLowerCase().includes(query) ||
        p.model?.toLowerCase().includes(query) ||
        p.barcode?.toLowerCase().includes(query)
      );
    }

    if (selectedBrand) {
      filtered = filtered.filter(p => p.brand === selectedBrand);
    }

    setFilteredProducts(filtered);
  }, [searchQuery, selectedBrand, products]);

  const formatCurrency = (amount: number) => `₹${amount.toLocaleString('en-IN')}`;

  const CategoryIcon = getCategoryIcon(category);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl w-full max-w-4xl max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200 bg-gray-50">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-bv-red-100 rounded-lg">
              <CategoryIcon className="w-5 h-5 text-bv-red-600" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Select {categoryLabel}</h2>
              <p className="text-xs text-gray-500">{products.length} products in inventory</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={loadProducts}
              className="p-2 hover:bg-gray-200 rounded-lg transition-colors"
              title="Refresh"
            >
              <RefreshCw className={`w-4 h-4 text-gray-500 ${isLoading ? 'animate-spin' : ''}`} />
            </button>
            <button onClick={onClose} className="p-2 hover:bg-gray-200 rounded-lg transition-colors">
              <X className="w-5 h-5 text-gray-500" />
            </button>
          </div>
        </div>

        {/* Search & Filters */}
        <div className="p-4 border-b border-gray-100 space-y-3">
          <div className="flex gap-3">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    handleSearch(searchQuery);
                  }
                }}
                placeholder={`Search ${categoryLabel.toLowerCase()} by name, SKU, brand, barcode...`}
                className="w-full pl-10 pr-4 py-2.5 text-sm border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
                autoFocus
              />
            </div>
            {brands.length > 1 && (
              <select
                value={selectedBrand}
                onChange={(e) => setSelectedBrand(e.target.value)}
                className="px-3 py-2 text-sm border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
              >
                <option value="">All Brands</option>
                {brands.map(brand => (
                  <option key={brand} value={brand}>{brand}</option>
                ))}
              </select>
            )}
          </div>
        </div>

        {/* Error Banner */}
        {error && (
          <div className="mx-4 mt-4 p-3 bg-red-50 border border-red-200 rounded-lg flex items-center gap-2">
            <AlertCircle className="w-5 h-5 text-red-500" />
            <span className="text-sm text-red-700">{error}</span>
            <button onClick={loadProducts} className="ml-auto text-sm text-red-600 hover:underline">
              Retry
            </button>
          </div>
        )}

        {/* Product List */}
        <div className="flex-1 overflow-y-auto p-4">
          {isLoading ? (
            <div className="flex items-center justify-center h-48">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-bv-red-600"></div>
            </div>
          ) : filteredProducts.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 text-gray-400">
              <Package className="w-12 h-12 mb-3 opacity-50" />
              <p>No products found</p>
              <p className="text-sm">Try adjusting your search or filters</p>
              {products.length === 0 && !error && (
                <p className="text-xs mt-2">No inventory data available for this category</p>
              )}
            </div>
          ) : (
            <div className="grid gap-3">
              {filteredProducts.map((product) => (
                <button
                  key={product.id}
                  onClick={() => onSelect(product)}
                  disabled={product.quantity < 1}
                  className={`flex items-center gap-4 p-4 border border-gray-200 rounded-lg hover:border-bv-red-300 hover:bg-bv-red-50 transition-all text-left group ${
                    product.quantity < 1 ? 'opacity-50 cursor-not-allowed' : ''
                  }`}
                >
                  {/* Product Image Placeholder */}
                  <div className="w-16 h-16 bg-gray-100 rounded-lg flex items-center justify-center flex-shrink-0 group-hover:bg-white">
                    <CategoryIcon className="w-8 h-8 text-gray-400" />
                  </div>

                  {/* Product Details */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <p className="font-medium text-gray-900 truncate">{product.productName}</p>
                        <p className="text-sm text-gray-500">
                          {product.brand} • {product.model}
                          {product.color && ` • ${product.color}`}
                          {product.size && ` • ${product.size}`}
                        </p>
                      </div>
                      <div className="text-right flex-shrink-0">
                        <p className="font-semibold text-bv-red-600">{formatCurrency(product.offerPrice)}</p>
                        {product.mrp > product.offerPrice && (
                          <p className="text-xs text-gray-400 line-through">{formatCurrency(product.mrp)}</p>
                        )}
                      </div>
                    </div>

                    {/* Additional Info based on category */}
                    <div className="flex items-center gap-4 mt-2 text-xs text-gray-500 flex-wrap">
                      <span className="flex items-center gap-1">
                        <Barcode className="w-3 h-3" />
                        {product.sku}
                      </span>
                      {product.quantity > 0 ? (
                        <span className="text-green-600">In Stock ({product.quantity})</span>
                      ) : (
                        <span className="text-red-600">Out of Stock</span>
                      )}
                      {product.locationCode && (
                        <span>Loc: {product.locationCode}</span>
                      )}
                      {/* Contact lens specific */}
                      {product.power && <span>Power: {product.power}</span>}
                      {product.boxCount && <span>{product.boxCount} lenses/box</span>}
                      {product.expiryDate && (
                        <span className="text-amber-600">Exp: {new Date(product.expiryDate).toLocaleDateString()}</span>
                      )}
                      {/* Watch specific */}
                      {product.strapType && <span>{product.strapType}</span>}
                      {product.dialSize && <span>{product.dialSize}</span>}
                      {/* Service specific */}
                      {product.serviceType && <span>{product.serviceType}</span>}
                      {product.estimatedTime && <span>~{product.estimatedTime}</span>}
                    </div>
                  </div>

                  {/* Select Indicator */}
                  <div className="w-8 h-8 rounded-full border-2 border-gray-200 group-hover:border-bv-red-500 flex items-center justify-center transition-colors">
                    <Check className="w-4 h-4 text-gray-300 group-hover:text-bv-red-500" />
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-gray-200 bg-gray-50 flex items-center justify-between">
          <p className="text-sm text-gray-500">
            Showing {filteredProducts.length} of {products.length} products
          </p>
          <button onClick={onClose} className="btn-outline">
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

export default ProductSearchModal;
