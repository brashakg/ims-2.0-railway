'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  ChevronLeft,
  ChevronRight,
  Edit2,
  ExternalLink,
  Loader2,
  Trash2,
  Upload,
  X,
} from 'lucide-react';
import SearchableDropdown from '@/components/SearchableDropdown';
import Topbar from '@/components/Topbar';
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

interface SavedView {
  key: string;
  label: string;
  filter: { status?: string; lowStock?: boolean };
}

const SAVED_VIEWS: SavedView[] = [
  { key: 'all', label: 'All', filter: {} },
  { key: 'active', label: 'Active', filter: { status: 'PUBLISHED' } },
  { key: 'draft', label: 'Drafts', filter: { status: 'DRAFT' } },
  { key: 'archived', label: 'Archived', filter: { status: 'ARCHIVED' } },
];

// Polaris-styled status badge based on the product's lifecycle.
function StatusBadge({ status }: { status: Product['status'] }) {
  if (status === 'PUBLISHED') {
    return <span className="polaris-badge polaris-badge-success">Active</span>;
  }
  if (status === 'DRAFT') {
    return <span className="polaris-badge polaris-badge-warning">Draft</span>;
  }
  return <span className="polaris-badge">Archived</span>;
}

// Sync indicator. shopifyProductId presence is the source of truth — a
// failed last sync log on a product that already has a shopifyProductId
// just means the LAST attempt failed (e.g. throttle), not that the
// product never made it.
function SyncBadge({ product }: { product: Product }) {
  if (product.shopifyProductId) {
    return (
      <span className="polaris-badge polaris-badge-success">Synced</span>
    );
  }
  const lastFailed = product.syncLogs[0]?.status === 'FAILED';
  if (lastFailed) {
    return (
      <span className="polaris-badge polaris-badge-critical">Failed</span>
    );
  }
  return <span className="polaris-badge">Not synced</span>;
}

export default function ProductsPage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [limit] = useState(20);
  const [total, setTotal] = useState(0);

  // Filters
  const [category, setCategory] = useState('All');
  const [brand, setBrand] = useState('');
  const [status, setStatus] = useState('All'); // matches saved-view key
  const [location, setLocation] = useState('');
  const [search, setSearch] = useState('');
  const [shape, setShape] = useState('');
  const [frameMaterial, setFrameMaterial] = useState('');
  const [gender, setGender] = useState('');

  // Filter option lists
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

  // Fetch status counts for the saved-view chip rail.
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

  // Locations
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch('/api/locations?excludeSynthetic=true');
        const data = await res.json();
        setLocations(Array.isArray(data) ? data : data?.data || []);
      } catch (err) {
        console.error('Error fetching locations:', err);
      }
    })();
  }, []);

  // Filter options
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch('/api/products/filters');
        const data: FiltersResponse = await res.json();
        if (data.success) {
          setBrands(data.brands || []);
          setShapes(data.shapes || []);
          setFrameMaterials(data.frameMaterials || []);
          setGenders(data.genders || []);
        }
      } catch (err) {
        console.error('Error fetching filters:', err);
      }
    })();
  }, []);

  // Products list
  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const params = new URLSearchParams({
          page: String(page),
          limit: String(limit),
          ...(category !== 'All' && { category }),
          ...(brand && { brand }),
          ...(status !== 'All' && { status }),
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
      } catch (err) {
        console.error('Error fetching products:', err);
      } finally {
        setLoading(false);
      }
    })();
  }, [
    page,
    limit,
    category,
    brand,
    status,
    location,
    search,
    shape,
    frameMaterial,
    gender,
  ]);

  const toggleSelect = (id: string) => {
    setSelectedProducts((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = (e: React.ChangeEvent<HTMLInputElement>) => {
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
      setPage(1);
    } catch (err) {
      console.error(err);
      alert('Sync request failed.');
    } finally {
      setSyncLoading(false);
    }
  };

  const handleBulkStatusChange = async (newStatus: string) => {
    if (selectedProducts.size === 0) return;
    const verb =
      newStatus === 'PUBLISHED'
        ? 'publish'
        : newStatus === 'DRAFT'
          ? 'set to draft'
          : newStatus === 'ARCHIVED'
            ? 'archive'
            : newStatus.toLowerCase();
    if (!confirm(`${verb} ${selectedProducts.size} product(s)?`)) return;
    try {
      const results = await Promise.allSettled(
        Array.from(selectedProducts).map((id) =>
          fetch(`/api/products/${id}`, {
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
    } catch (err) {
      console.error(err);
    }
  };

  const handleBulkDelete = async () => {
    if (selectedProducts.size === 0) return;
    if (
      !confirm(
        `Permanently delete ${selectedProducts.size} product(s)? This cannot be undone.`
      )
    )
      return;
    try {
      const results = await Promise.allSettled(
        Array.from(selectedProducts).map((id) =>
          fetch(`/api/products/${id}`, { method: 'DELETE' })
        )
      );
      const failed = results.filter((r) => r.status === 'rejected').length;
      if (failed > 0) alert(`${failed} product(s) failed to delete.`);
      setSelectedProducts(new Set());
      setPage(1);
    } catch (err) {
      console.error(err);
    }
  };

  const handleDeleteOne = async (id: string) => {
    if (!confirm('Delete this product?')) return;
    try {
      await fetch(`/api/products/${id}`, { method: 'DELETE' });
      setPage(1);
    } catch (err) {
      console.error(err);
    }
  };

  const totalStock = (p: Product) =>
    p.locations.reduce((s, l) => s + l.quantity, 0);

  const pages = Math.max(1, Math.ceil(total / limit));

  const activeFilters = [
    brand && { key: 'brand', label: `Brand: ${brand}`, clear: () => setBrand('') },
    category !== 'All' && {
      key: 'category',
      label: `Category: ${category}`,
      clear: () => setCategory('All'),
    },
    location && {
      key: 'location',
      label: `Location: ${locations.find((l) => l.id === location)?.name || ''}`,
      clear: () => setLocation(''),
    },
    shape && { key: 'shape', label: `Shape: ${shape}`, clear: () => setShape('') },
    frameMaterial && {
      key: 'fm',
      label: `Frame Material: ${frameMaterial}`,
      clear: () => setFrameMaterial(''),
    },
    gender && {
      key: 'gender',
      label: `Gender: ${gender}`,
      clear: () => setGender(''),
    },
  ].filter(Boolean) as Array<{
    key: string;
    label: string;
    clear: () => void;
  }>;

  return (
    <>
      <Topbar
        title="Products"
        subtitle={total ? `${total.toLocaleString()} total` : undefined}
        breadcrumb={[{ label: 'Home', href: '/dashboard' }, { label: 'Products' }]}
      />

      <div style={{ padding: 24, maxWidth: 1400, margin: '0 auto' }}>
        {/* ─── Saved-views chip rail ───────────────────── */}
        <div className="flex items-center gap-2 flex-wrap mb-3">
          {SAVED_VIEWS.map((v) => {
            const active = status === (v.filter.status ?? 'All');
            const count = !statusCounts
              ? null
              : v.key === 'all'
                ? statusCounts.total
                : v.key === 'active'
                  ? statusCounts.published
                  : v.key === 'draft'
                    ? statusCounts.draft
                    : statusCounts.archived;
            return (
              <button
                key={v.key}
                type="button"
                onClick={() => {
                  setStatus(v.filter.status ?? 'All');
                  setPage(1);
                }}
                className="polaris-btn polaris-btn-sm"
                style={{
                  background: active ? 'var(--text)' : 'var(--bg-surface)',
                  color: active ? 'white' : 'var(--text)',
                  borderColor: active
                    ? 'var(--text)'
                    : 'var(--border-strong)',
                  fontWeight: active ? 600 : 500,
                }}
              >
                {v.label}
                {count !== null && (
                  <span
                    style={{
                      marginLeft: 4,
                      padding: '0 5px',
                      fontSize: 10,
                      borderRadius: 999,
                      background: active
                        ? 'rgba(255,255,255,0.2)'
                        : 'var(--bg-surface-tertiary)',
                      color: active ? 'rgba(255,255,255,0.9)' : 'var(--text-tertiary)',
                    }}
                  >
                    {count.toLocaleString()}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* ─── Filter rail (search + 4 dropdowns) ──────── */}
        <div
          className="polaris-card mb-3"
          style={{ padding: 12, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}
        >
          <div style={{ flex: '2 1 280px', minWidth: 220 }}>
            <label
              style={{
                display: 'block',
                fontSize: 11,
                color: 'var(--text-tertiary)',
                marginBottom: 4,
                textTransform: 'uppercase',
                letterSpacing: 0.4,
              }}
            >
              Search
            </label>
            <input
              type="text"
              placeholder="Title, brand, model number…"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
              style={{
                width: '100%',
                padding: '6px 10px',
                border: '1px solid var(--border-strong)',
                borderRadius: 8,
                fontSize: 13,
                height: 32,
              }}
            />
          </div>
          <div style={{ flex: '1 1 160px', minWidth: 140 }}>
            <SearchableDropdown
              label="Brand"
              options={brands}
              value={brand}
              onChange={(v) => {
                setBrand(v);
                setPage(1);
              }}
            />
          </div>
          <div style={{ flex: '1 1 160px', minWidth: 140 }}>
            <SearchableDropdown
              label="Category"
              options={CATEGORIES}
              value={category}
              onChange={(v) => {
                setCategory(v);
                setPage(1);
              }}
            />
          </div>
          <div style={{ flex: '1 1 160px', minWidth: 140 }}>
            <SearchableDropdown
              label="Location"
              options={['All', ...locations.map((l) => l.name)]}
              value={
                location
                  ? locations.find((l) => l.id === location)?.name || ''
                  : 'All'
              }
              onChange={(val) => {
                if (val === 'All') {
                  setLocation('');
                } else {
                  const loc = locations.find((l) => l.name === val);
                  setLocation(loc?.id || '');
                }
                setPage(1);
              }}
            />
          </div>
          <div style={{ flex: '1 1 140px', minWidth: 130 }}>
            <SearchableDropdown
              label="Shape"
              options={shapes}
              value={shape}
              onChange={(v) => {
                setShape(v);
                setPage(1);
              }}
            />
          </div>
          <div style={{ flex: '1 1 160px', minWidth: 140 }}>
            <SearchableDropdown
              label="Frame material"
              options={frameMaterials}
              value={frameMaterial}
              onChange={(v) => {
                setFrameMaterial(v);
                setPage(1);
              }}
            />
          </div>
          <div style={{ flex: '1 1 140px', minWidth: 130 }}>
            <SearchableDropdown
              label="Gender"
              options={genders}
              value={gender}
              onChange={(v) => {
                setGender(v);
                setPage(1);
              }}
            />
          </div>
        </div>

        {/* Active-filter chips (clearable) */}
        {activeFilters.length > 0 && (
          <div
            className="flex items-center gap-2 flex-wrap mb-3"
            style={{ fontSize: 12 }}
          >
            <span style={{ color: 'var(--text-tertiary)' }}>Filters:</span>
            {activeFilters.map((f) => (
              <button
                key={f.key}
                type="button"
                onClick={f.clear}
                className="polaris-badge"
                style={{
                  cursor: 'pointer',
                  height: 20,
                  background: 'var(--brand-bg)',
                  color: 'var(--brand-text)',
                }}
              >
                {f.label}
                <X size={10} />
              </button>
            ))}
            <button
              type="button"
              onClick={() => {
                setBrand('');
                setCategory('All');
                setLocation('');
                setShape('');
                setFrameMaterial('');
                setGender('');
                setPage(1);
              }}
              className="polaris-btn polaris-btn-plain polaris-btn-sm"
            >
              Clear all
            </button>
          </div>
        )}

        {/* ─── Products table ──────────────────────────── */}
        <div className="polaris-card" style={{ overflow: 'hidden' }}>
          {loading ? (
            <div
              style={{
                padding: 32,
                textAlign: 'center',
                color: 'var(--text-tertiary)',
                fontSize: 13,
              }}
            >
              <Loader2
                className="animate-spin inline-block mr-2"
                size={14}
              />
              Loading…
            </div>
          ) : products.length === 0 ? (
            <div
              style={{
                padding: 32,
                textAlign: 'center',
                color: 'var(--text-tertiary)',
                fontSize: 13,
              }}
            >
              No products match the current filters.
            </div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table className="polaris-table">
                <thead>
                  <tr>
                    <th style={{ width: 36 }}>
                      <input
                        type="checkbox"
                        onChange={toggleSelectAll}
                        checked={
                          selectedProducts.size === products.length &&
                          products.length > 0
                        }
                        style={{
                          width: 14,
                          height: 14,
                          accentColor: 'var(--text)',
                          cursor: 'pointer',
                        }}
                      />
                    </th>
                    <th style={{ width: 44 }}></th>
                    <th>Title</th>
                    <th>Brand</th>
                    <th>Category</th>
                    <th>Status</th>
                    <th
                      className="tabular-nums"
                      style={{ textAlign: 'right' }}
                    >
                      MRP
                    </th>
                    <th
                      className="tabular-nums"
                      style={{ textAlign: 'right' }}
                    >
                      Stock
                    </th>
                    <th>Sync</th>
                    <th style={{ textAlign: 'right' }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {products.map((p) => {
                    const stock = totalStock(p);
                    const lowStock = stock < 5;
                    return (
                      <tr key={p.id}>
                        <td>
                          <input
                            type="checkbox"
                            checked={selectedProducts.has(p.id)}
                            onChange={() => toggleSelect(p.id)}
                            style={{
                              width: 14,
                              height: 14,
                              accentColor: 'var(--text)',
                              cursor: 'pointer',
                            }}
                          />
                        </td>
                        <td>
                          {p.images[0] ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img
                              src={p.images[0].url}
                              alt={p.title}
                              style={{
                                width: 36,
                                height: 36,
                                borderRadius: 4,
                                objectFit: 'cover',
                                border: '1px solid var(--border-subdued)',
                              }}
                            />
                          ) : (
                            <div
                              style={{
                                width: 36,
                                height: 36,
                                borderRadius: 4,
                                background: 'var(--bg-surface-tertiary)',
                              }}
                            />
                          )}
                        </td>
                        <td style={{ maxWidth: 320 }}>
                          <Link
                            href={`/dashboard/products/edit/${p.id}`}
                            style={{
                              fontWeight: 500,
                              color: 'var(--text)',
                              textDecoration: 'none',
                            }}
                            className="hover:underline"
                          >
                            <div
                              style={{
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap',
                              }}
                            >
                              {p.title || '(untitled)'}
                            </div>
                          </Link>
                        </td>
                        <td style={{ color: 'var(--text-secondary)' }}>
                          {p.brand || '—'}
                        </td>
                        <td style={{ color: 'var(--text-secondary)' }}>
                          {p.category || '—'}
                        </td>
                        <td>
                          <StatusBadge status={p.status} />
                        </td>
                        <td
                          className="tabular-nums"
                          style={{ textAlign: 'right' }}
                        >
                          ₹{p.mrp.toLocaleString('en-IN')}
                        </td>
                        <td
                          className="tabular-nums"
                          style={{
                            textAlign: 'right',
                            color: lowStock ? 'var(--critical)' : 'var(--text)',
                            fontWeight: lowStock ? 600 : 500,
                          }}
                        >
                          {stock}
                        </td>
                        <td>
                          <SyncBadge product={p} />
                        </td>
                        <td style={{ textAlign: 'right' }}>
                          <Link
                            href={`/dashboard/products/edit/${p.id}`}
                            title="Edit"
                            style={{
                              padding: 5,
                              display: 'inline-flex',
                              color: 'var(--text-secondary)',
                              marginRight: 4,
                            }}
                          >
                            <Edit2 size={14} />
                          </Link>
                          {p.shopifyProductId && (
                            <a
                              href="#"
                              title="Open on Shopify"
                              style={{
                                padding: 5,
                                display: 'inline-flex',
                                color: 'var(--text-secondary)',
                                marginRight: 4,
                              }}
                            >
                              <ExternalLink size={14} />
                            </a>
                          )}
                          <button
                            type="button"
                            onClick={() => handleDeleteOne(p.id)}
                            title="Delete"
                            style={{
                              padding: 5,
                              border: 'none',
                              background: 'transparent',
                              color: 'var(--critical)',
                              cursor: 'pointer',
                            }}
                          >
                            <Trash2 size={14} />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* Pagination */}
          {!loading && products.length > 0 && (
            <div
              style={{
                padding: '8px 16px',
                borderTop: '1px solid var(--border-subdued)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                fontSize: 12,
                color: 'var(--text-tertiary)',
              }}
            >
              <span>
                Page {page} of {pages} · {total.toLocaleString()} products
              </span>
              <div className="flex gap-1">
                <button
                  type="button"
                  onClick={() => setPage(Math.max(1, page - 1))}
                  disabled={page === 1}
                  className="polaris-btn polaris-btn-icon"
                >
                  <ChevronLeft size={14} />
                </button>
                <button
                  type="button"
                  onClick={() => setPage(Math.min(pages, page + 1))}
                  disabled={page === pages}
                  className="polaris-btn polaris-btn-icon"
                >
                  <ChevronRight size={14} />
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ─── Sticky bulk-action bar (black, only when selected) ── */}
      {selectedProducts.size > 0 && (
        <div
          style={{
            position: 'sticky',
            bottom: 0,
            zIndex: 20,
            margin: '0 24px 16px',
            padding: '8px 14px',
            background: 'var(--text)',
            color: 'white',
            borderRadius: 10,
            boxShadow: 'var(--shadow-lg)',
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            flexWrap: 'wrap',
          }}
        >
          <span style={{ fontSize: 13, fontWeight: 500 }}>
            {selectedProducts.size} selected
          </span>
          <div style={{ flex: 1 }} />
          <button
            type="button"
            onClick={handleSyncSelected}
            disabled={syncLoading}
            className="polaris-btn polaris-btn-sm"
            style={{
              background: 'rgba(255,255,255,0.1)',
              color: 'white',
              borderColor: 'rgba(255,255,255,0.2)',
              boxShadow: 'none',
            }}
          >
            {syncLoading ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <Upload size={12} />
            )}
            Sync to Shopify
          </button>
          <button
            type="button"
            onClick={() => handleBulkStatusChange('PUBLISHED')}
            className="polaris-btn polaris-btn-sm"
            style={{
              background: 'rgba(255,255,255,0.1)',
              color: 'white',
              borderColor: 'rgba(255,255,255,0.2)',
              boxShadow: 'none',
            }}
          >
            Publish
          </button>
          <button
            type="button"
            onClick={() => handleBulkStatusChange('DRAFT')}
            className="polaris-btn polaris-btn-sm"
            style={{
              background: 'rgba(255,255,255,0.1)',
              color: 'white',
              borderColor: 'rgba(255,255,255,0.2)',
              boxShadow: 'none',
            }}
          >
            Set draft
          </button>
          <button
            type="button"
            onClick={() => handleBulkStatusChange('ARCHIVED')}
            className="polaris-btn polaris-btn-sm"
            style={{
              background: 'rgba(255,255,255,0.1)',
              color: 'white',
              borderColor: 'rgba(255,255,255,0.2)',
              boxShadow: 'none',
            }}
          >
            Archive
          </button>
          <button
            type="button"
            onClick={handleBulkDelete}
            className="polaris-btn polaris-btn-sm"
            style={{
              background: 'var(--critical)',
              color: 'white',
              borderColor: 'var(--critical)',
              boxShadow: 'none',
            }}
          >
            Delete
          </button>
          <button
            type="button"
            onClick={() => setSelectedProducts(new Set())}
            className="polaris-btn polaris-btn-icon"
            title="Clear selection"
            style={{
              background: 'transparent',
              color: 'rgba(255,255,255,0.7)',
              borderColor: 'transparent',
              boxShadow: 'none',
            }}
          >
            <X size={14} />
          </button>
        </div>
      )}
    </>
  );
}
