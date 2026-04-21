'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import {
  Plus,
  Edit2,
  Trash2,
  ExternalLink,
  Loader2,
  ChevronLeft,
  ChevronRight,
  Upload,
  ChevronDown,
} from 'lucide-react';
import SearchableDropdown from '@/components/SearchableDropdown';
import { CATEGORIES as CATEGORY_DEFS } from '@/lib/categories';

interface Product {
  id: string;
  title: string;
  brand: string;
  productName: string;
  category: string;
  status: 'DRAFT' | 'PUBLISHED' | 'ARCHIVED';
  mrp: number;
  shopifyProductId: string | null;
  images: Array<{ url: string }>;
  locations: Array<{ quantity: number }>;
  syncLogs: Array<{ status: string; createdAt: string }>;
}

interface Location {
  id: string;
  name: string;
  code: string;
}

interface FiltersResponse {
  success: boolean;
  brands: string[];
  shapes: string[];
  frameMaterials: string[];
  genders: string[];
  categories: string[];
  statuses: string[];
}

const CATEGORIES = ['All', ...CATEGORY_DEFS.map((c) => c.label)];

const STATUS_SEGMENTS: Array<{ key: string; label: string }> = [
  { key: 'All', label: 'All' },
  { key: 'Published', label: 'Active' },
  { key: 'Draft', label: 'Draft' },
  { key: 'Archived', label: 'Archived' },
];

export default function ProductsPage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [limit] = useState(10);
  const [total, setTotal] = useState(0);

  // Filters
  const [category, setCategory] = useState('All');
  const [brand, setBrand] = useState('');
  const [status, setStatus] = useState('All');
  const [location, setLocation] = useState('');
  const [search, setSearch] = useState('');
  const [shape, setShape] = useState('');
  const [frameMaterial, setFrameMaterial] = useState('');
  const [gender, setGender] = useState('');
  const [showMoreFilters, setShowMoreFilters] = useState(false);

  const [brands, setBrands] = useState<string[]>([]);
  const [shapes, setShapes] = useState<string[]>([]);
  const [frameMaterials, setFrameMaterials] = useState<string[]>([]);
  const [genders, setGenders] = useState<string[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [selectedProducts, setSelectedProducts] = useState<Set<string>>(
    new Set()
  );
  const [syncLoading, setSyncLoading] = useState(false);
  const [statusCounts, setStatusCounts] = useState<{
    total: number;
    published: number;
    draft: number;
    archived: number;
  } | null>(null);

  // Fetch status counts for segment tab badges
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch('/api/products/stats');
        const data = await res.json();
        if (data.success && data.data) {
          setStatusCounts({
            total: data.data.total || 0,
            published: data.data.published || 0,
            draft: data.data.draft || 0,
            archived: data.data.archived || 0,
          });
        }
      } catch (err) {
        console.error('Error fetching product stats:', err);
      }
    })();
  }, [page]);

  // Fetch locations
  useEffect(() => {
    const fetchLocations = async () => {
      try {
        const res = await fetch('/api/locations?excludeSynthetic=true');
        const data = await res.json();
        // Handle both array and { data: [...] } response formats
        setLocations(Array.isArray(data) ? data : (data?.data || []));
      } catch (error) {
        console.error('Error fetching locations:', error);
      }
    };
    fetchLocations();
  }, []);

  // Fetch filter options from new /api/products/filters endpoint
  useEffect(() => {
    const fetchFilters = async () => {
      try {
        const res = await fetch('/api/products/filters');
        const data: FiltersResponse = await res.json();
        if (data.success) {
          setBrands(data.brands || []);
          setShapes(data.shapes || []);
          setFrameMaterials(data.frameMaterials || []);
          setGenders(data.genders || []);
        }
      } catch (error) {
        console.error('Error fetching filters:', error);
      }
    };
    fetchFilters();
  }, []);

  // Fetch products
  useEffect(() => {
    const fetchProducts = async () => {
      setLoading(true);
      try {
        const params = new URLSearchParams({
          page: page.toString(),
          limit: limit.toString(),
          ...(category !== 'All' && { category }),
          ...(brand && { brand }),
          ...(status !== 'All' && { status: status.toUpperCase() }),
          ...(location && { location }),
          ...(search && { search }),
          ...(shape && { shape }),
          ...(frameMaterial && { frameMaterial }),
          ...(gender && { gender }),
        });

        const res = await fetch(`/api/products?${params}`);
        const data = await res.json();
        setProducts(data.data || []);
        setTotal(data.pagination?.total || 0);
      } catch (error) {
        console.error('Error fetching products:', error);
      } finally {
        setLoading(false);
      }
    };

    fetchProducts();
  }, [page, limit, category, brand, status, location, search, shape, frameMaterial, gender]);

  const handleSelectProduct = (id: string) => {
    const newSelected = new Set(selectedProducts);
    if (newSelected.has(id)) {
      newSelected.delete(id);
    } else {
      newSelected.add(id);
    }
    setSelectedProducts(newSelected);
  };

  const handleSelectAll = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.checked) {
      setSelectedProducts(new Set(products.map((p) => p.id)));
    } else {
      setSelectedProducts(new Set());
    }
  };

  const handleSyncSelected = async () => {
    if (selectedProducts.size === 0) return;

    setSyncLoading(true);
    try {
      const res = await fetch('/api/shopify/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ productIds: Array.from(selectedProducts) }),
      });
      const result = await res.json();
      if (!result.success) {
        alert(`Sync failed: ${result.error || 'Unknown error'}`);
      } else {
        const { summary } = result;
        alert(
          `Sync complete: ${summary.success} succeeded, ${summary.failed} failed, ${summary.skipped} skipped`
        );
      }
      setSelectedProducts(new Set());
      // Refresh products
      setPage(1);
    } catch (error) {
      console.error('Error syncing products:', error);
      alert('Sync request failed. Please try again.');
    } finally {
      setSyncLoading(false);
    }
  };

  const handleArchiveSelected = async () => {
    if (selectedProducts.size === 0) return;
    if (!confirm(`Archive ${selectedProducts.size} product(s)?`)) return;

    try {
      const results = await Promise.allSettled(
        Array.from(selectedProducts).map((productId) =>
          fetch(`/api/products/${productId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: 'ARCHIVED' }),
          })
        )
      );
      const failed = results.filter((r) => r.status === 'rejected').length;
      if (failed > 0) {
        alert(`${failed} product(s) failed to archive.`);
      }
      setSelectedProducts(new Set());
      setPage(1);
    } catch (error) {
      console.error('Error archiving products:', error);
      alert('Failed to archive products. Please try again.');
    }
  };

  const handleDeleteProduct = async (id: string) => {
    if (!confirm('Are you sure you want to delete this product?')) return;

    try {
      await fetch(`/api/products/${id}`, { method: 'DELETE' });
      setPage(1);
    } catch (error) {
      console.error('Error deleting product:', error);
    }
  };

  const handleBulkDelete = async () => {
    if (selectedProducts.size === 0) return;
    if (!confirm(`Permanently delete ${selectedProducts.size} product(s)? This cannot be undone.`)) return;

    try {
      const results = await Promise.allSettled(
        Array.from(selectedProducts).map((productId) =>
          fetch(`/api/products/${productId}`, { method: 'DELETE' })
        )
      );
      const failed = results.filter((r) => r.status === 'rejected').length;
      if (failed > 0) alert(`${failed} product(s) failed to delete.`);
      setSelectedProducts(new Set());
      setPage(1);
    } catch (error) {
      console.error('Error deleting products:', error);
    }
  };

  const handleBulkStatusChange = async (newStatus: string) => {
    if (selectedProducts.size === 0) return;
    const label = newStatus === 'PUBLISHED' ? 'publish' : newStatus === 'DRAFT' ? 'set to draft' : newStatus.toLowerCase();
    if (!confirm(`${label} ${selectedProducts.size} product(s)?`)) return;

    try {
      const results = await Promise.allSettled(
        Array.from(selectedProducts).map((productId) =>
          fetch(`/api/products/${productId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: newStatus }),
          })
        )
      );
      const failed = results.filter((r) => r.status === 'rejected').length;
      if (failed > 0) alert(`${failed} product(s) failed to update.`);
      setSelectedProducts(new Set());
      setPage(1);
    } catch (error) {
      console.error('Error updating products:', error);
    }
  };

  const getTotalStock = (product: Product) => {
    return product.locations.reduce((sum, loc) => sum + loc.quantity, 0);
  };

  const getLastSyncStatus = (product: Product) => {
    // If product has a Shopify ID, it's synced (either pushed or pulled)
    if (product.shopifyProductId) return '✓ Synced';
    const lastSync = product.syncLogs[0];
    if (!lastSync) return 'Not synced';
    return lastSync.status === 'SUCCESS' ? '✓ Synced' : '✗ Failed';
  };

  const pages = Math.ceil(total / limit);

  const hasActiveMoreFilters = shape || frameMaterial || gender;

  return (
    <div className="p-4 sm:p-6 bg-gray-50 min-h-screen">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-6">
          <h1 className="text-2xl sm:text-3xl font-bold text-gray-900">Products</h1>
          <Link
            href="/dashboard/products/new"
            className="flex items-center gap-2 bg-blue-600 text-white px-4 py-3 min-h-[44px] rounded-lg hover:bg-blue-700 transition-colors text-sm"
          >
            <Plus className="w-4 h-4" />
            Add Product
          </Link>
        </div>

        {/* Status segment tabs (matches Shopify Products layout) */}
        <div className="bg-white rounded-lg shadow p-3 mb-4">
          <div className="flex items-center gap-2 flex-wrap">
            {STATUS_SEGMENTS.map((s) => {
              const count = !statusCounts
                ? null
                : s.key === 'All'
                  ? statusCounts.total
                  : s.key === 'Published'
                    ? statusCounts.published
                    : s.key === 'Draft'
                      ? statusCounts.draft
                      : statusCounts.archived;
              return (
                <button
                  key={s.key}
                  onClick={() => {
                    setStatus(s.key);
                    setPage(1);
                  }}
                  className={`inline-flex items-center gap-2 px-3 py-1.5 text-sm rounded-lg border transition-colors ${
                    status === s.key
                      ? 'bg-blue-600 text-white border-blue-600'
                      : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-50'
                  }`}
                >
                  <span>{s.label}</span>
                  {count !== null && (
                    <span
                      className={`text-[11px] px-1.5 rounded-full ${
                        status === s.key
                          ? 'bg-white/20 text-white'
                          : 'bg-slate-100 text-slate-600'
                      }`}
                    >
                      {count}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>

        {/* Filters */}
        <div className="bg-white rounded-lg shadow p-4 mb-6">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
            <SearchableDropdown
              label="Product Type"
              options={CATEGORIES}
              value={category}
              onChange={setCategory}
            />
            <SearchableDropdown
              label="Brand"
              options={brands}
              value={brand}
              onChange={setBrand}
            />
            <SearchableDropdown
              label="Location"
              options={['All', ...locations.map((l) => l.name)]}
              value={location ? locations.find((l) => l.id === location)?.name || '' : 'All'}
              onChange={(val) => {
                if (val === 'All') {
                  setLocation('');
                } else {
                  const loc = locations.find((l) => l.name === val);
                  setLocation(loc?.id || '');
                }
              }}
            />
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Search
              </label>
              <input
                type="text"
                placeholder="Product name, brand..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full px-3 py-3 min-h-[44px] border border-gray-300 rounded-lg focus:outline-none focus:border-blue-500"
              />
            </div>
          </div>

          {/* More Filters Collapsible Section */}
          <div className="mt-4 border-t pt-4">
            <button
              onClick={() => setShowMoreFilters(!showMoreFilters)}
              className="flex items-center gap-2 text-sm font-medium text-gray-700 hover:text-gray-900"
            >
              <ChevronDown
                className={`w-4 h-4 transition-transform ${
                  showMoreFilters ? 'rotate-180' : ''
                }`}
              />
              More Filters
              {hasActiveMoreFilters && (
                <span className="ml-2 px-2 py-1 bg-blue-100 text-blue-700 rounded text-xs font-semibold">
                  {[shape, frameMaterial, gender].filter(Boolean).length} active
                </span>
              )}
            </button>

            {showMoreFilters && (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 sm:gap-4 mt-4">
                <SearchableDropdown
                  label="Shape"
                  options={shapes}
                  value={shape}
                  onChange={setShape}
                />
                <SearchableDropdown
                  label="Frame Material"
                  options={frameMaterials}
                  value={frameMaterial}
                  onChange={setFrameMaterial}
                />
                <SearchableDropdown
                  label="Gender"
                  options={genders}
                  value={gender}
                  onChange={setGender}
                />
              </div>
            )}
          </div>
        </div>

        {/* Bulk Actions */}
        {selectedProducts.size > 0 && (
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-6 flex items-center justify-between">
            <span className="text-sm text-blue-900">
              {selectedProducts.size} product(s) selected
            </span>
            <div className="flex gap-2">
              <button
                onClick={handleSyncSelected}
                disabled={syncLoading}
                className="flex items-center gap-2 bg-green-600 text-white px-4 py-3 min-h-[44px] rounded-lg hover:bg-green-700 transition-colors disabled:opacity-50 text-sm"
              >
                {syncLoading ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Upload className="w-4 h-4" />
                )}
                Sync to Shopify
              </button>
              <button
                onClick={() => handleBulkStatusChange('PUBLISHED')}
                className="bg-blue-600 text-white px-4 py-3 min-h-[44px] rounded-lg hover:bg-blue-700 transition-colors text-sm"
              >
                Publish
              </button>
              <button
                onClick={() => handleBulkStatusChange('DRAFT')}
                className="bg-yellow-600 text-white px-4 py-3 min-h-[44px] rounded-lg hover:bg-yellow-700 transition-colors text-sm"
              >
                Draft
              </button>
              <button
                onClick={handleArchiveSelected}
                className="bg-gray-600 text-white px-4 py-3 min-h-[44px] rounded-lg hover:bg-gray-700 transition-colors text-sm"
              >
                Archive
              </button>
              <button
                onClick={handleBulkDelete}
                className="bg-red-600 text-white px-4 py-3 min-h-[44px] rounded-lg hover:bg-red-700 transition-colors text-sm"
              >
                Delete
              </button>
            </div>
          </div>
        )}

        {/* Products Table */}
        <div className="bg-white rounded-lg shadow overflow-hidden">
          {loading ? (
            <div className="p-8 text-center">
              <Loader2 className="w-8 h-8 animate-spin mx-auto text-blue-600" />
            </div>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full text-xs sm:text-sm lg:text-base">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="px-2 sm:px-6 py-3 text-left">
                        <input
                          type="checkbox"
                          onChange={handleSelectAll}
                          checked={selectedProducts.size === products.length && products.length > 0}
                          className="w-4 h-4 rounded border-gray-300"
                        />
                      </th>
                      <th className="px-2 sm:px-6 py-3 text-left text-xs sm:text-sm font-semibold text-gray-700">
                        Image
                      </th>
                      <th className="px-2 sm:px-6 py-3 text-left text-xs sm:text-sm font-semibold text-gray-700">
                        Brand
                      </th>
                      <th className="px-2 sm:px-6 py-3 text-left text-xs sm:text-sm font-semibold text-gray-700">
                        Title
                      </th>
                      <th className="px-2 sm:px-6 py-3 text-left text-xs sm:text-sm font-semibold text-gray-700">
                        Product Type
                      </th>
                      <th className="px-2 sm:px-6 py-3 text-left text-xs sm:text-sm font-semibold text-gray-700">
                        MRP
                      </th>
                      <th className="px-2 sm:px-6 py-3 text-left text-xs sm:text-sm font-semibold text-gray-700">
                        Status
                      </th>
                      <th className="px-2 sm:px-6 py-3 text-left text-xs sm:text-sm font-semibold text-gray-700">
                        Stock
                      </th>
                      <th className="px-2 sm:px-6 py-3 text-left text-xs sm:text-sm font-semibold text-gray-700">
                        Sync
                      </th>
                      <th className="px-2 sm:px-6 py-3 text-left text-xs sm:text-sm font-semibold text-gray-700">
                        Actions
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {products.map((product) => (
                      <tr key={product.id} className="hover:bg-gray-50">
                        <td className="px-2 sm:px-6 py-4">
                          <input
                            type="checkbox"
                            checked={selectedProducts.has(product.id)}
                            onChange={() => handleSelectProduct(product.id)}
                            className="w-4 h-4 rounded border-gray-300"
                          />
                        </td>
                        <td className="px-2 sm:px-6 py-4">
                          {product.images[0] ? (
                            <img
                              src={product.images[0].url}
                              alt={product.title}
                              className="w-10 h-10 rounded object-cover"
                            />
                          ) : (
                            <div className="w-10 h-10 rounded bg-gray-200" />
                          )}
                        </td>
                        <td className="px-2 sm:px-6 py-4 text-xs sm:text-sm font-medium text-gray-900">
                          {product.brand}
                        </td>
                        <td className="px-2 sm:px-6 py-4 text-xs sm:text-sm max-w-xs truncate">
                          <Link
                            href={`/dashboard/products/edit/${product.id}`}
                            className="text-blue-700 hover:underline font-medium"
                          >
                            {product.title}
                          </Link>
                        </td>
                        <td className="px-2 sm:px-6 py-4 text-xs sm:text-sm text-gray-600">
                          {product.category}
                        </td>
                        <td className="px-2 sm:px-6 py-4 text-xs sm:text-sm font-medium text-gray-900">
                          ₹{product.mrp}
                        </td>
                        <td className="px-2 sm:px-6 py-4 text-xs sm:text-sm">
                          <span
                            className={`inline-block px-2.5 py-0.5 rounded-full text-[11px] font-medium border ${
                              product.status === 'PUBLISHED'
                                ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                                : product.status === 'DRAFT'
                                ? 'bg-amber-50 text-amber-700 border-amber-200'
                                : 'bg-slate-100 text-slate-700 border-slate-200'
                            }`}
                          >
                            {product.status === 'PUBLISHED' ? 'Active' : product.status === 'DRAFT' ? 'Draft' : 'Archived'}
                          </span>
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-900">
                          {getTotalStock(product)}
                        </td>
                        <td className="px-2 sm:px-6 py-4 text-xs sm:text-sm text-gray-600">
                          {getLastSyncStatus(product)}
                        </td>
                        <td className="px-6 py-4 text-sm flex gap-2">
                          <Link
                            href={`/dashboard/products/edit/${product.id}`}
                            className="text-blue-600 hover:text-blue-800"
                          >
                            <Edit2 className="w-4 h-4" />
                          </Link>
                          {product.status === 'PUBLISHED' && (
                            <a
                              href="#"
                              className="text-blue-600 hover:text-blue-800"
                              title="View on Shopify"
                            >
                              <ExternalLink className="w-4 h-4" />
                            </a>
                          )}
                          <button
                            onClick={() => handleDeleteProduct(product.id)}
                            className="text-red-600 hover:text-red-800"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Pagination */}
              <div className="px-6 py-4 border-t border-gray-200 flex items-center justify-between">
                <span className="text-sm text-gray-600">
                  Page {page} of {pages} | Total: {total} products
                </span>
                <div className="flex gap-2">
                  <button
                    onClick={() => setPage(Math.max(1, page - 1))}
                    disabled={page === 1}
                    className="p-2 border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <ChevronLeft className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => setPage(Math.min(pages, page + 1))}
                    disabled={page === pages}
                    className="p-2 border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <ChevronRight className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
