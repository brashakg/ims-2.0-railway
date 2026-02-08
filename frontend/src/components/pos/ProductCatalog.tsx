import { useState, useMemo } from 'react';
import { Search, Plus, AlertCircle } from 'lucide-react';
import clsx from 'clsx';

interface Product {
  id: string;
  sku: string;
  name: string;
  brand: string;
  category: string;
  price: number;
  stock: number;
  image_url?: string;
  is_optical?: boolean;
}

const MOCK_PRODUCTS: Product[] = [
  // Frames
  { id: 'f1', sku: 'FR-RAY-001', name: 'Ray-Ban RB5383', brand: 'Ray-Ban', category: 'Frames', price: 8500, stock: 12, is_optical: true },
  { id: 'f2', sku: 'FR-TOM-001', name: 'Tom Ford TF5607', brand: 'Tom Ford', category: 'Frames', price: 12500, stock: 8, is_optical: true },
  { id: 'f3', sku: 'FR-GUC-001', name: 'Gucci GG0397O', brand: 'Gucci', category: 'Frames', price: 15000, stock: 5, is_optical: true },
  { id: 'f4', sku: 'FR-LV-001', name: 'Louis Vuitton LV5407', brand: 'Louis Vuitton', category: 'Frames', price: 18000, stock: 3, is_optical: true },

  // Lenses
  { id: 'l1', sku: 'LN-CRYSRAL-001', name: 'Crystal Single Vision', brand: 'Essilor', category: 'Lenses', price: 2500, stock: 50, is_optical: true },
  { id: 'l2', sku: 'LN-PRGRSS-001', name: 'Progressive Lens Standard', brand: 'Zeiss', category: 'Lenses', price: 6000, stock: 30, is_optical: true },
  { id: 'l3', sku: 'LN-BLUCUT-001', name: 'Blue Cut Premium', brand: 'Crizal', category: 'Lenses', price: 4500, stock: 25, is_optical: true },
  { id: 'l4', sku: 'LN-PHOTO-001', name: 'Photochromic Lens', brand: 'Essilor', category: 'Lenses', price: 7500, stock: 15, is_optical: true },

  // Contact Lenses
  { id: 'cl1', sku: 'CL-ACUVUE-001', name: 'Acuvue Oasys Monthly', brand: 'Johnson & Johnson', category: 'Contact Lenses', price: 1200, stock: 100, is_optical: true },
  { id: 'cl2', sku: 'CL-AIR-001', name: 'Air Optix Plus', brand: 'Alcon', category: 'Contact Lenses', price: 1500, stock: 80, is_optical: true },
  { id: 'cl3', sku: 'CL-FRESHLOOK-001', name: 'FreshLook Colorblends', brand: 'Alcon', category: 'Contact Lenses', price: 1800, stock: 60, is_optical: true },

  // Sunglasses
  { id: 's1', sku: 'SG-AVIATOR-001', name: 'Classic Aviator', brand: 'Ray-Ban', category: 'Sunglasses', price: 6500, stock: 20, is_optical: false },
  { id: 's2', sku: 'SG-WAYFARER-001', name: 'Wayfarer Black', brand: 'Ray-Ban', category: 'Sunglasses', price: 5500, stock: 18, is_optical: false },
  { id: 's3', sku: 'SG-CAT-001', name: 'Cat Eye UV Protection', brand: 'Generic', category: 'Sunglasses', price: 2500, stock: 40, is_optical: false },

  // Accessories
  { id: 'a1', sku: 'AC-CASE-001', name: 'Premium Hard Case', brand: 'Generic', category: 'Accessories', price: 800, stock: 100 },
  { id: 'a2', sku: 'AC-CLOTH-001', name: 'Microfiber Cleaning Cloth', brand: 'Generic', category: 'Accessories', price: 100, stock: 200 },
  { id: 'a3', sku: 'AC-SOLUTION-001', name: 'Contact Lens Solution 500ml', brand: 'Bausch & Lomb', category: 'Accessories', price: 450, stock: 75 },

  // Services
  { id: 'sv1', sku: 'SV-EXAM-001', name: 'Eye Exam (Standard)', brand: 'Better Vision', category: 'Services', price: 500, stock: 999 },
  { id: 'sv2', sku: 'SV-FITTING-001', name: 'Contact Lens Fitting', brand: 'Better Vision', category: 'Services', price: 300, stock: 999 },
  { id: 'sv3', sku: 'SV-ADJUST-001', name: 'Frame Adjustment & Fitting', brand: 'Better Vision', category: 'Services', price: 200, stock: 999 },
];

const CATEGORIES = ['Frames', 'Lenses', 'Contact Lenses', 'Sunglasses', 'Accessories', 'Services'];

interface ProductCatalogProps {
  onAddToCart: (product: Product) => void;
  barcodeFilter?: string;
}

export function ProductCatalog({ onAddToCart, barcodeFilter }: ProductCatalogProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);

  // Filter products
  const filteredProducts = useMemo(() => {
    let products = MOCK_PRODUCTS;

    // Filter by barcode/SKU if provided
    if (barcodeFilter) {
      products = products.filter(p =>
        p.sku.toLowerCase().includes(barcodeFilter.toLowerCase()) ||
        p.name.toLowerCase().includes(barcodeFilter.toLowerCase())
      );
      // Auto-select first match on barcode
      if (products.length === 1) {
        onAddToCart(products[0]);
      }
    }

    // Filter by category
    if (selectedCategory) {
      products = products.filter(p => p.category === selectedCategory);
    }

    // Filter by search query
    if (searchQuery) {
      products = products.filter(p =>
        p.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        p.brand.toLowerCase().includes(searchQuery.toLowerCase()) ||
        p.sku.toLowerCase().includes(searchQuery.toLowerCase())
      );
    }

    return products;
  }, [searchQuery, selectedCategory, barcodeFilter, onAddToCart]);

  return (
    <div className="space-y-6">
      {/* Search Bar */}
      <div className="sticky top-0 bg-gray-900 z-10">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-500" />
          <input
            type="text"
            placeholder="Search by name, brand, or SKU..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full bg-gray-800 text-white border border-gray-700 rounded-lg pl-10 pr-4 py-2 text-sm focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>

      {/* Category Filter Tabs */}
      <div className="flex gap-2 overflow-x-auto pb-2">
        <button
          onClick={() => setSelectedCategory(null)}
          className={clsx(
            'px-4 py-2 rounded-lg whitespace-nowrap font-medium text-sm transition-colors',
            !selectedCategory
              ? 'bg-blue-600 text-white'
              : 'bg-gray-800 text-gray-300 hover:bg-gray-700'
          )}
        >
          All
        </button>
        {CATEGORIES.map(category => (
          <button
            key={category}
            onClick={() => setSelectedCategory(category)}
            className={clsx(
              'px-4 py-2 rounded-lg whitespace-nowrap font-medium text-sm transition-colors',
              selectedCategory === category
                ? 'bg-blue-600 text-white'
                : 'bg-gray-800 text-gray-300 hover:bg-gray-700'
            )}
          >
            {category}
          </button>
        ))}
      </div>

      {/* Product Grid */}
      {filteredProducts.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12">
          <AlertCircle className="w-12 h-12 text-gray-600 mb-4" />
          <p className="text-gray-400 text-center">
            {searchQuery || barcodeFilter ? 'No products found matching your search' : 'No products available'}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {filteredProducts.map(product => (
            <div
              key={product.id}
              className="bg-gray-800 border border-gray-700 rounded-lg overflow-hidden hover:border-blue-500 transition-colors"
            >
              {/* Product Image Placeholder */}
              <div className="bg-gray-900 h-40 flex items-center justify-center">
                <div className="text-center">
                  <div className="w-12 h-12 bg-gradient-to-br from-blue-600 to-purple-600 rounded-lg mx-auto mb-2" />
                  {product.is_optical && (
                    <span className="inline-block px-2 py-1 bg-blue-900 text-blue-300 text-xs rounded font-medium">
                      Optical
                    </span>
                  )}
                </div>
              </div>

              {/* Product Details */}
              <div className="p-4">
                <p className="text-xs text-gray-500 mb-1">{product.sku}</p>
                <h3 className="font-semibold text-white mb-1">{product.name}</h3>
                <p className="text-sm text-gray-400 mb-3">{product.brand}</p>

                {/* Price & Stock */}
                <div className="flex justify-between items-center mb-3">
                  <span className="text-lg font-bold text-green-500">₹{product.price.toLocaleString('en-IN')}</span>
                  <span className={clsx(
                    'text-xs font-medium px-2 py-1 rounded',
                    product.stock > 10 ? 'bg-green-900 text-green-300' :
                    product.stock > 0 ? 'bg-amber-900 text-amber-300' :
                    'bg-red-900 text-red-300'
                  )}>
                    {product.stock > 0 ? `${product.stock} in stock` : 'Out of stock'}
                  </span>
                </div>

                {/* Add to Cart Button */}
                <button
                  onClick={() => onAddToCart(product)}
                  disabled={product.stock === 0}
                  className={clsx(
                    'w-full py-2 rounded-lg font-medium transition-colors flex items-center justify-center gap-2',
                    product.stock === 0
                      ? 'bg-gray-700 text-gray-500 cursor-not-allowed'
                      : 'bg-blue-600 hover:bg-blue-700 text-white'
                  )}
                >
                  <Plus className="w-4 h-4" />
                  Add to Cart
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default ProductCatalog;
