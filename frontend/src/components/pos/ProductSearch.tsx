// ============================================================================
// IMS 2.0 - Product Search Component
// ============================================================================

import { useState, useCallback } from 'react';
import { Search, Scan, Grid, List, FileText, Tag, Package } from 'lucide-react';
import type { ProductCategory } from '../../types';
import clsx from 'clsx';

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

// Mock products for demo
const mockProducts = [
  { id: 'prod-001', name: 'Ray-Ban RB5154 Clubmaster', sku: 'RB-5154-BLK', category: 'FRAME' as ProductCategory, brand: 'Ray-Ban', mrp: 8990, offerPrice: 6890, stock: 5 },
  { id: 'prod-002', name: 'Ray-Ban Aviator Classic', sku: 'RB-3025-GLD', category: 'SUNGLASS' as ProductCategory, brand: 'Ray-Ban', mrp: 12990, offerPrice: 9990, stock: 3 },
  { id: 'prod-003', name: 'Oakley Holbrook', sku: 'OAK-HOL-001', category: 'SUNGLASS' as ProductCategory, brand: 'Oakley', mrp: 15000, offerPrice: 12000, stock: 2 },
  { id: 'prod-004', name: 'Essilor Crizal Prevencia', sku: 'ESS-CP-STD', category: 'OPTICAL_LENS' as ProductCategory, brand: 'Essilor', mrp: 4500, offerPrice: 3500, stock: 20 },
  { id: 'prod-005', name: 'Zeiss DriveSafe', sku: 'ZS-DS-PRO', category: 'OPTICAL_LENS' as ProductCategory, brand: 'Zeiss', mrp: 8500, offerPrice: 7500, stock: 15 },
  { id: 'prod-006', name: 'Acuvue Oasys (6 pack)', sku: 'ACV-OAS-6', category: 'CONTACT_LENS' as ProductCategory, brand: 'Acuvue', mrp: 2100, offerPrice: 1800, stock: 50 },
  { id: 'prod-007', name: 'Bausch & Lomb SofLens', sku: 'BL-SL-6', category: 'CONTACT_LENS' as ProductCategory, brand: 'Bausch & Lomb', mrp: 1500, offerPrice: 1200, stock: 40 },
  { id: 'prod-008', name: 'FreshLook Colorblends - Blue', sku: 'FL-CB-BLU', category: 'COLORED_CONTACT_LENS' as ProductCategory, brand: 'FreshLook', mrp: 1800, offerPrice: 1500, stock: 25 },
  { id: 'prod-009', name: 'Titan Edge Ceramic', sku: 'TIT-EDG-CER', category: 'WATCH' as ProductCategory, brand: 'Titan', mrp: 15995, offerPrice: 13995, stock: 4 },
  { id: 'prod-010', name: 'Apple Watch Series 9', sku: 'APL-W9-45', category: 'SMARTWATCH' as ProductCategory, brand: 'Apple', mrp: 45900, offerPrice: 42900, stock: 2 },
  { id: 'prod-011', name: 'Ray-Ban Meta Smart Glasses', sku: 'RB-META-BLK', category: 'SMARTGLASSES' as ProductCategory, brand: 'Ray-Ban', mrp: 32990, offerPrice: 29990, stock: 1 },
  { id: 'prod-012', name: 'Reading Glasses +1.50', sku: 'RG-150-STD', category: 'READING_GLASSES' as ProductCategory, brand: 'Generic', mrp: 599, offerPrice: 499, stock: 30 },
  { id: 'prod-013', name: 'Lens Cleaning Kit', sku: 'ACC-LCK-01', category: 'ACCESSORIES' as ProductCategory, brand: 'Generic', mrp: 299, offerPrice: 199, stock: 100 },
  { id: 'prod-014', name: 'Eye Test Service', sku: 'SVC-EYE-001', category: 'SERVICES' as ProductCategory, brand: 'In-House', mrp: 500, offerPrice: 300, stock: 999 },
];

export function ProductSearch({ onAddProduct, onAddPrescription, hasPrescription }: ProductSearchProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<ProductCategory | null>(null);
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');
  const [barcodeMode, setBarcodeMode] = useState(false);

  // Filter products
  const filteredProducts = mockProducts.filter(product => {
    const matchesSearch = !searchQuery ||
      product.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      product.sku.toLowerCase().includes(searchQuery.toLowerCase()) ||
      product.brand.toLowerCase().includes(searchQuery.toLowerCase());

    const matchesCategory = !selectedCategory || product.category === selectedCategory;

    return matchesSearch && matchesCategory;
  });

  // Handle barcode scan
  const handleBarcodeScan = useCallback((barcode: string) => {
    // Find product by SKU (simulating barcode lookup)
    const product = mockProducts.find(p => p.sku === barcode);
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
  }, [onAddProduct]);

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
            <Scan className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-bv-red-600" />
          ) : (
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
          )}
          <input
            type="text"
            value={searchQuery}
            onChange={e => handleSearchChange(e.target.value)}
            className={clsx(
              'input-field pl-10',
              barcodeMode && 'border-bv-red-300 focus:border-bv-red-500'
            )}
            placeholder={barcodeMode ? 'Scan barcode...' : 'Search products...'}
            autoFocus={barcodeMode}
          />
        </div>

        {/* Barcode Toggle */}
        <button
          onClick={() => setBarcodeMode(!barcodeMode)}
          className={clsx(
            'px-3 py-2 rounded-lg border transition-colors',
            barcodeMode
              ? 'bg-bv-red-50 border-bv-red-300 text-bv-red-600'
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
              ? 'bg-bv-red-600 text-white'
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
                ? 'bg-bv-red-600 text-white'
                : `${cat.color} hover:opacity-80`
            )}
          >
            <span>{cat.icon}</span>
            <span>{cat.label}</span>
          </button>
        ))}
      </div>

      {/* Products Grid/List */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {filteredProducts.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-400">
            <Package className="w-12 h-12 mb-2" />
            <p>No products found</p>
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
                className="card p-3 text-left hover:border-bv-red-300 hover:shadow-md transition-all touch-target"
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
                  <span className="font-bold text-bv-red-600">
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
                className="w-full flex items-center gap-4 p-3 bg-white border border-gray-200 rounded-lg hover:border-bv-red-300 hover:shadow-sm transition-all"
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
                  <p className="font-bold text-bv-red-600">
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
    </div>
  );
}

export default ProductSearch;
