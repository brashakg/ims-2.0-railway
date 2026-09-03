// ============================================================================
// IMS 2.0 - Inventory > Stock ledger  (/inventory/stock)
// ============================================================================
// The old InventoryPage `activeTab === 'catalog'` block on its own URL: the
// per-store product ledger with search / availability / cataloguer / category
// filters, CSV export + bulk CSV import, the barcode modal, the read-only
// detail drawer and the image lightbox. Data comes from inventoryQueries so
// the layout's stat strip and this table share one cache entry.
//
// Deep links: /inventory/stock?search=<sku> pre-fills the search box. The old
// page IGNORED the ?search= param even though QuickAdd's "Open the existing
// product" rescue linked it - that live bug is fixed here (deliberate).

import { useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Barcode,
  CheckCircle,
  Download,
  Eye,
  FileText,
  Globe,
  Loader2,
  Package,
  Plus,
  Search,
  Upload,
  X,
} from 'lucide-react';
import clsx from 'clsx';
import type { ProductCategory } from '../../types';
import { sameCategory } from '../../utils/categoryNormalize';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { useQueryClient } from '@tanstack/react-query';
// Product writes go through the SINGLE validated path (productApi -> /products).
// Imported DIRECTLY from the module (not the api barrel) to dodge the TS2614
// re-export resolution issue documented in CLAUDE.md.
import { productApi, type CreateProductPayload } from '../../services/api/products';
import type { DisplayFixture, } from '../../services/api/displayFixtures';
import type { DisplayPlacement } from '../../services/api/displayPlacements';
import { BarcodeManagementModal } from '../../components/inventory/BarcodeManagementModal';
import { Pagination } from '../../components/common/Pagination';
import { ImageLightbox } from '../../components/common/ImageLightbox';
import { useInventoryContext } from './InventoryLayout';
import {
  CATEGORIES,
  getOnlineFor,
  onlineStatusIds,
  useCataloguers,
  useFixturesMap,
  useOnlineStatus,
  usePlacements,
  useStock,
  type StockItem,
} from './inventoryQueries';

export function InventoryStockPage() {
  const { hasRole } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const { storeId, isOnlineStoreView } = useInventoryContext();

  // UI state. ?search= seeds the box (QuickAdd's rescue popup links it).
  const [searchQuery, setSearchQuery] = useState(() => searchParams.get('search') || '');
  const [selectedCategory, setSelectedCategory] = useState<ProductCategory | null>(null);
  const [availabilityFilter, setAvailabilityFilter] = useState<'all' | 'online' | 'offline'>('all');
  const [cataloguerFilter, setCataloguerFilter] = useState<string>('');
  const [currentPage, setCurrentPage] = useState(1);
  const pageSize = 50;

  // Owner 2026-07-05: click a row thumbnail -> full-size image lightbox.
  const [lightbox, setLightbox] = useState<{ images: string[]; alt: string } | null>(null);
  const [showBarcodeModal, setShowBarcodeModal] = useState(false);
  const [selectedProduct, setSelectedProduct] = useState<StockItem | null>(null);
  const [detailItem, setDetailItem] = useState<StockItem | null>(null);

  // CSV Import state. csvRows = ALL parsed rows (sent to the validated
  // bulk-create endpoint); csvPreview = first 10, for the preview table only.
  const [showCSVImport, setShowCSVImport] = useState(false);
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [csvRows, setCsvRows] = useState<Array<Record<string, string>>>([]);
  const [csvPreview, setCsvPreview] = useState<Array<Record<string, string>>>([]);
  const [isImporting, setIsImporting] = useState(false);

  // Role-based permissions (each list is used only by this page now).
  const canAddProduct = hasRole(['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER']);
  const canExport = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']);
  const canManageBarcode = hasRole(['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER', 'STORE_MANAGER']);
  // Mirrors the backend gate on GET /products/cataloguers (manager ladder).
  const canSeeCataloguers = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER']);

  // Data (shared cache with the layout's stat strip).
  const stockQ = useStock(storeId || undefined, cataloguerFilter || undefined);
  const inventory = stockQ.data ?? [];
  const isLoading = stockQ.isPending;
  const onlineStatusQ = useOnlineStatus(onlineStatusIds(inventory));
  const onlineStatus = onlineStatusQ.data;
  const cataloguersQ = useCataloguers(canSeeCataloguers);
  const cataloguers = cataloguersQ.data ?? [];
  const placementsQ = usePlacements(storeId || undefined);
  const placements = placementsQ.data ?? [];
  const fixturesQ = useFixturesMap(storeId || undefined);
  const fixturesMap = fixturesQ.data ?? {};

  const getOnline = (item: StockItem) => getOnlineFor(item, onlineStatus);

  // v2-2b: pick the primary placement for each SKU (or first if none flagged).
  const primaryPlacementBySku = useMemo(() => {
    const m: Record<string, DisplayPlacement> = {};
    for (const p of placements) {
      const existing = m[p.sku];
      if (!existing) {
        m[p.sku] = p;
        continue;
      }
      if (p.is_primary && !existing.is_primary) m[p.sku] = p;
    }
    return m;
  }, [placements]);

  const getZone = (sku: string): { fixture: DisplayFixture; placement: DisplayPlacement } | undefined => {
    const placement = primaryPlacementBySku[sku];
    if (!placement) return undefined;
    const fixture = fixturesMap[placement.fixture_id];
    if (!fixture) return undefined;
    return { fixture, placement };
  };

  // Filter inventory locally
  const filteredInventory = inventory.filter(item => {
    const matchesSearch = !searchQuery ||
      item.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      item.sku?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      item.brand?.toLowerCase().includes(searchQuery.toLowerCase());

    const matchesCategory = !selectedCategory || sameCategory(item.category, selectedCategory);

    const isOnline = !!getOnline(item)?.online;
    const matchesAvailability =
      availabilityFilter === 'all' ? true : availabilityFilter === 'online' ? isOnline : !isOnline;

    return matchesSearch && matchesCategory && matchesAvailability;
  });

  // Paginate filtered results (page resets whenever a filter changes).
  const filterKey = `${searchQuery}|${selectedCategory}|${availabilityFilter}|${storeId}|${cataloguerFilter}`;
  const [lastFilterKey, setLastFilterKey] = useState(filterKey);
  if (filterKey !== lastFilterKey) {
    setLastFilterKey(filterKey);
    setCurrentPage(1);
  }
  const paginatedInventory = filteredInventory.slice(
    (currentPage - 1) * pageSize,
    currentPage * pageSize
  );

  const getStockStatus = (item: StockItem) => {
    const threshold = item.lowStockThreshold || item.minStock || 5;
    if (item.stock === 0) return { label: 'Out of Stock', class: 'badge-error' };
    if (item.stock <= threshold) return { label: 'Low Stock', class: 'badge-warning' };
    return { label: 'In Stock', class: 'badge-success' };
  };

  const formatCurrency = (amount: number) => {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 0,
    }).format(amount);
  };

  const reloadInventory = () =>
    queryClient.invalidateQueries({ queryKey: ['inventory', 'stock'] });

  // Client-side CSV export of the currently-loaded ledger rows (respects the
  // active search / category / availability filters). Empty -> toast no-op.
  const exportInventoryCsv = () => {
    const rows = filteredInventory;
    if (rows.length === 0) {
      toast.info('Nothing to export for the current filters.');
      return;
    }
    const headers = [
      'Product', 'Brand', 'SKU', 'Barcode', 'Category',
      'MRP', 'Offer Price', 'In Stock', 'Reserved', 'Available',
      'Online', 'Online Stock', 'Location', 'Status',
    ];
    // RFC-4180 escaping: wrap in quotes and double any embedded quotes.
    const esc = (v: unknown) => {
      const s = v == null ? '' : String(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const lines = [headers.join(',')];
    for (const item of rows) {
      const category = CATEGORIES.find(c => sameCategory(c.code, item.category))?.label || item.category;
      const online = getOnline(item);
      const status = getStockStatus(item).label;
      const available = (item.stock || 0) - (item.reserved || 0);
      lines.push([
        esc(item.name),
        esc(item.brand),
        esc(item.sku),
        esc(item.barcode || ''),
        esc(category),
        esc(item.mrp ?? ''),
        esc(item.offerPrice ?? item.mrp ?? ''),
        esc(item.stock ?? 0),
        esc(item.reserved ?? 0),
        esc(available),
        esc(online?.online ? 'Yes' : 'No'),
        esc(online?.online ? (online.online_stock ?? '') : ''),
        esc(item.location || ''),
        esc(status),
      ].join(','));
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const stamp = new Date().toISOString().slice(0, 10);
    a.download = `inventory_${stamp}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    toast.success(`Exported ${rows.length} row${rows.length === 1 ? '' : 's'} to CSV`);
  };

  // Handle CSV import through the SINGLE validated path
  // (`POST /products/bulk-create`) - per-row results, invalid rows reported.
  const handleImportProducts = async () => {
    if (!csvFile || csvRows.length === 0) {
      toast.error('Please select a CSV file first');
      return;
    }

    const num = (v: string | undefined): number | undefined => {
      if (v === undefined || String(v).trim() === '') return undefined;
      const n = Number(String(v).replace(/[^0-9.-]/g, ''));
      return Number.isFinite(n) ? n : undefined;
    };
    const products: CreateProductPayload[] = csvRows.map((row) => {
      const mrp = num(row.mrp) ?? 0;
      const offer = num(row.offer_price);
      return {
        category: (row.category || '').trim(),
        sku: (row.sku || '').trim(),
        brand: (row.brand || '').trim(),
        model: (row.model || row.name || '').trim(),
        attributes: {},
        mrp,
        // Backend ProductCreate requires offer_price > 0; default to MRP when
        // the CSV omits it (i.e. sell at MRP, no discount).
        offer_price: offer && offer > 0 ? offer : mrp,
        ...(row.hsn_code && row.hsn_code.trim() ? { hsn_code: row.hsn_code.trim() } : {}),
        ...(row.description && row.description.trim() ? { description: row.description.trim() } : {}),
      };
    });

    setIsImporting(true);
    try {
      const result = await productApi.bulkCreateProducts(products);
      const created = result?.summary?.created ?? 0;
      const failed = result?.summary?.failed ?? 0;
      if (created > 0) {
        toast.success(`Imported ${created} product${created === 1 ? '' : 's'}${failed ? ` (${failed} skipped)` : ''}`);
      }
      if (failed > 0) {
        const firstErr = result.results.find(r => !r.ok);
        const reason = firstErr?.errors?.[0] ? `: ${firstErr.errors[0]}` : '';
        toast.error(`${failed} row${failed === 1 ? '' : 's'} skipped${reason}`);
      }
      if (created === 0 && failed === 0) {
        toast.error('No products were imported. Check the CSV format and try again.');
      }
      if (created > 0) {
        setShowCSVImport(false);
        setCsvFile(null);
        setCsvPreview([]);
        setCsvRows([]);
        await reloadInventory();
      }
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Import failed. Check CSV format and try again.';
      toast.error(msg);
    } finally {
      setIsImporting(false);
    }
  };

  // Handle barcode save through the SINGLE validated product-update path.
  const handleSaveBarcode = async (barcode: string) => {
    if (!selectedProduct) return;
    try {
      await productApi.updateProduct(selectedProduct.id, { barcode });
      toast.success(`Barcode saved for ${selectedProduct.name}`);
      await reloadInventory();
    } catch {
      toast.error('Failed to save barcode. Please try again.');
      throw new Error('Failed to save barcode');
    }
  };

  const openBarcodeModal = (item: StockItem) => {
    setSelectedProduct(item);
    setShowBarcodeModal(true);
  };

  return (
    <>
      {/* Search and Filters */}
      <div className="card">
        <div className="flex flex-col tablet:flex-row gap-4 mb-4">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              className="input-field pl-10"
              placeholder="Search by name, SKU, or brand..."
              list="inv-search-suggestions"
            />
            {searchQuery.length >= 2 && (
              <datalist id="inv-search-suggestions">
                {inventory.filter(i =>
                  i.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
                  i.sku?.toLowerCase().includes(searchQuery.toLowerCase()) ||
                  i.brand?.toLowerCase().includes(searchQuery.toLowerCase())
                ).slice(0, 8).map((item) => (
                  <option key={item.id || item.sku} value={item.name}>{item.sku} · {item.brand} · ₹{Math.round(item.mrp || 0)}</option>
                ))}
              </datalist>
            )}
          </div>
          {/* Ledger actions (were in the page header before the split - they
              act on THIS table, so they live next to it now). Hidden on an
              ONLINE store: it owns no ledger to export or import into. */}
          {!isOnlineStoreView && (
            <div className="flex gap-2 shrink-0">
              {canExport && (
                <button onClick={exportInventoryCsv} className="btn sm">
                  <Download className="w-4 h-4" /> Export
                </button>
              )}
              {canAddProduct && (
                <button onClick={() => setShowCSVImport(true)} className="btn sm">
                  <Upload className="w-4 h-4" /> CSV import
                </button>
              )}
            </div>
          )}
        </div>

        {/* Availability (online / offline) + cataloguer filter */}
        <div className="flex flex-wrap items-center gap-2 mb-4">
          <span className="text-xs font-medium text-gray-500 uppercase mr-1">Stock</span>
          {(['all', 'online', 'offline'] as const).map(f => (
            <button
              key={f}
              onClick={() => setAvailabilityFilter(f)}
              className={clsx('ims-chip', availabilityFilter === f && 'ims-chip--on')}
            >
              {f === 'online' && <Globe className="w-3.5 h-3.5" strokeWidth={1.6} />}
              {f === 'all' ? 'All' : f === 'online' ? 'Online' : 'Offline'}
            </button>
          ))}
          <div className="ml-auto flex items-center gap-2 flex-wrap">
            {/* Cataloguer attribution: which user catalogued what. Only shown
                when the roster loaded (manager ladder; fail-soft otherwise). */}
            {canSeeCataloguers && cataloguers.length > 0 && (
              <>
                <label htmlFor="inv-cataloguer" className="text-xs font-medium text-gray-500 uppercase">Catalogued by</label>
                <select
                  id="inv-cataloguer"
                  value={cataloguerFilter}
                  onChange={e => setCataloguerFilter(e.target.value)}
                  className="input-field text-sm py-1.5 w-48"
                >
                  <option value="">All users</option>
                  {cataloguers.map(c => (
                    <option key={c.user_id} value={c.user_id}>
                      {c.name} ({c.created_count})
                    </option>
                  ))}
                </select>
              </>
            )}
          </div>
        </div>

        {/* Category Filters - compact ims-chip pills on a single wrapping row */}
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => setSelectedCategory(null)}
            className={clsx('ims-chip', !selectedCategory && 'ims-chip--on')}
          >
            All
          </button>
          {CATEGORIES.map(cat => {
            const IconCmp = cat.icon;
            const selected = selectedCategory === cat.code;
            return (
              <button
                key={cat.code}
                onClick={() => setSelectedCategory(cat.code)}
                className={clsx('ims-chip', selected && 'ims-chip--on')}
              >
                <IconCmp className="w-3.5 h-3.5" strokeWidth={1.6} />
                <span>{cat.label}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Inventory Table */}
      <div className="card overflow-hidden">
        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
          </div>
        ) : filteredInventory.length === 0 ? (
          isOnlineStoreView && !(searchQuery || selectedCategory || availabilityFilter !== 'all') ? (
            // ONLINE store owns no physical stock, so the on-hand ledger is
            // empty by design - point to the website catalogue instead.
            <div className="text-center py-12 text-gray-500">
              <Globe className="w-12 h-12 mx-auto mb-2 opacity-50" />
              <p className="font-medium text-gray-700">This online store holds no stock of its own.</p>
              <p className="text-sm mt-1">It sells from every shop's pooled stock. Manage what's listed on the website in the Online Store module.</p>
              <button onClick={() => navigate('/online-store')} className="btn sm primary mt-4 inline-flex">
                <Globe className="w-4 h-4" /> Open online store
              </button>
            </div>
          ) : (
            <div className="text-center py-12 text-gray-500">
              <Package className="w-12 h-12 mx-auto mb-2 opacity-50" />
              <p>{searchQuery || selectedCategory || availabilityFilter !== 'all' ? 'No products found matching your filters' : 'No products in inventory'}</p>
            </div>
          )
        ) : (
          <>
          {/* min-width forces the table to SCROLL horizontally inside this
              container (never squash a column to one-word-per-line). */}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1100px]">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase whitespace-nowrap min-w-[240px]">Product</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase whitespace-nowrap">SKU</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase whitespace-nowrap">Barcode</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase whitespace-nowrap">Category</th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase whitespace-nowrap">MRP</th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase whitespace-nowrap">Offer</th>
                  {/* Physical-only columns: an ONLINE store owns no stock and
                      has no on-floor zone, so these are hidden there. */}
                  {!isOnlineStoreView && (
                    <>
                      <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase whitespace-nowrap">In-Store</th>
                      <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase whitespace-nowrap">Zone</th>
                    </>
                  )}
                  <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase whitespace-nowrap">Online</th>
                  <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase whitespace-nowrap">Location</th>
                  <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase whitespace-nowrap">Status</th>
                  <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase whitespace-nowrap">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {paginatedInventory.map((item, i) => {
                  const status = getStockStatus(item);
                  const category = CATEGORIES.find(c => sameCategory(c.code, item.category));
                  return (
                    <tr key={item.id || item.sku || `row-${i}`} className="hover:bg-gray-50">
                      <td className="px-4 py-3">
                        <div className="flex items-start gap-3">
                          {/* Owner 2026-07-05: row thumbnail; click -> lightbox. */}
                          {item.image_url ? (
                            <button
                              type="button"
                              className="flex-shrink-0 w-10 h-10 rounded-md border border-gray-200 bg-white overflow-hidden cursor-zoom-in hover:ring-2 hover:ring-blue-300"
                              title="View full-size image"
                              onClick={() =>
                                setLightbox({
                                  images:
                                    item.images && item.images.length > 0
                                      ? item.images
                                      : [item.image_url as string],
                                  alt: item.name,
                                })
                              }
                            >
                              <img
                                src={item.image_url}
                                alt={item.name}
                                loading="lazy"
                                referrerPolicy="no-referrer"
                                className="w-full h-full object-contain"
                              />
                            </button>
                          ) : (
                            <div className="flex-shrink-0 w-10 h-10 rounded-md border border-gray-100 bg-gray-50 flex items-center justify-center">
                              <Package className="w-4 h-4 text-gray-300" strokeWidth={1.6} />
                            </div>
                          )}
                          <div>
                            <p className="font-medium text-gray-900">{item.name}</p>
                            <p className="text-sm text-gray-500">{item.brand}</p>
                            {/* Cataloguer attribution (manager-only). */}
                            {canSeeCataloguers && item.created_by_name && (
                              <p className="text-xs text-gray-400 mt-0.5" title="Catalogued by">
                                by {item.created_by_name}
                              </p>
                            )}
                            {/* Procurement Phase 1: latest ACCEPTED GRN chip. */}
                            {item.last_grn?.grn_number && (item.last_grn.qty ?? 0) > 0 && (
                              <p
                                className="text-xs text-gray-400 mt-0.5"
                                title="Most recent goods receipt for this product at this store"
                              >
                                +{item.last_grn.qty} via {item.last_grn.grn_number}
                                {item.last_grn.date ? `, ${item.last_grn.date}` : ''}
                              </p>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-600">{item.sku}</td>
                      <td className="px-4 py-3">
                        {item.barcode ? (
                          <span className="text-xs font-mono text-gray-700 bg-gray-100 px-2 py-1 rounded">
                            {item.barcode}
                          </span>
                        ) : (
                          <span className="text-xs text-gray-500">Not set</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <span className="text-sm inline-flex items-center gap-1.5">
                          {category?.icon ? (
                            (() => {
                              const Cmp = category.icon;
                              return <Cmp className="w-3.5 h-3.5 text-gray-500" strokeWidth={1.6} />;
                            })()
                          ) : (
                            <Package className="w-3.5 h-3.5 text-gray-400" strokeWidth={1.6} />
                          )}
                          {category?.label || item.category}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right text-sm text-gray-500">
                        {formatCurrency(item.mrp || 0)}
                      </td>
                      <td className="px-4 py-3 text-right text-sm font-medium text-gray-900">
                        {formatCurrency(item.offerPrice || item.mrp || 0)}
                      </td>
                      {/* Physical-only cells (In-Store on-hand + on-floor Zone). */}
                      {!isOnlineStoreView && (
                        <>
                          <td className="px-4 py-3 text-center">
                            <span className="font-medium">{item.stock - (item.reserved || 0)}</span>
                            {item.reserved > 0 && (
                              <span className="text-xs text-amber-600 ml-1">+{item.reserved} reserved</span>
                            )}
                          </td>
                          {/* v2-2b: Zone column - primary placement. Cell click
                              deep-links to the Display layout SECTION URL with
                              the fixture pre-selected. */}
                          <td className="px-4 py-3 text-center">
                            {(() => {
                              const z = getZone(item.sku);
                              if (!z) {
                                return (
                                  <span className="text-xs text-gray-400" title="Not placed yet">-</span>
                                );
                              }
                              return (
                                <button
                                  type="button"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    navigate(`/inventory/display-layout?fixture=${encodeURIComponent(z.fixture.fixture_id)}`);
                                  }}
                                  className={'zone-chip' + (z.fixture.lockable ? ' warn' : '')}
                                  title={`${z.fixture.name}${z.placement.position ? ' . ' + z.placement.position : ''} . click to open`}
                                >
                                  {z.fixture.code}
                                  <span style={{ color: 'var(--ink-4)', fontWeight: 500, marginLeft: 2 }}>
                                    . {z.fixture.zone}
                                  </span>
                                </button>
                              );
                            })()}
                          </td>
                        </>
                      )}
                      <td className="px-4 py-3 text-center">
                        {(() => {
                          const o = getOnline(item);
                          if (!o?.online) {
                            return <span className="text-xs text-gray-400">In-store only</span>;
                          }
                          return (
                            <div className="flex flex-col items-center gap-0.5">
                              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-green-50 text-green-700">
                                <Globe className="w-3 h-3" strokeWidth={2} />
                                Online
                              </span>
                              {typeof o.online_stock === 'number' && (
                                <span className="text-xs text-gray-600">{o.online_stock} online</span>
                              )}
                            </div>
                          );
                        })()}
                      </td>
                      <td className="px-4 py-3 text-center text-sm text-gray-600">{item.location || '-'}</td>
                      <td className="px-4 py-3 text-center">
                        <span className={status.class}>{status.label}</span>
                      </td>
                      <td className="px-4 py-3 text-center">
                        <div className="flex items-center justify-center gap-1">
                          {canManageBarcode && (
                            <button
                              onClick={() => openBarcodeModal(item)}
                              className="p-2 text-gray-500 hover:text-blue-600 transition-colors"
                              title="Manage Barcode"
                              aria-label="Manage Barcode"
                            >
                              <Barcode className="w-4 h-4" />
                            </button>
                          )}
                          <button
                            onClick={() => setDetailItem(item)}
                            className="p-2 text-gray-500 hover:text-bv-red-600 transition-colors"
                            title="View Details"
                            aria-label="View Details"
                          >
                            <Eye className="w-4 h-4" />
                          </button>
                          {/* Procurement Phase 1: spawn a variant of this
                              product in Quick Add (?variant= deep link). */}
                          {canAddProduct && item.id && (
                            <button
                              onClick={() => navigate(`/catalog/add?variant=${encodeURIComponent(item.id)}`)}
                              className="inline-flex items-center gap-0.5 px-1.5 py-1 text-xs font-medium text-gray-500 hover:text-blue-600 transition-colors"
                              title="Add a variant of this product"
                              aria-label={`Add a variant of ${item.name}`}
                            >
                              <Plus className="w-3.5 h-3.5" />
                              Variant
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <Pagination
            currentPage={currentPage}
            totalItems={filteredInventory.length}
            pageSize={pageSize}
            onPageChange={setCurrentPage}
          />
          </>
        )}
      </div>

      {/* Barcode Management Modal */}
      {selectedProduct && (
        <BarcodeManagementModal
          isOpen={showBarcodeModal}
          onClose={() => {
            setShowBarcodeModal(false);
            setSelectedProduct(null);
          }}
          productId={selectedProduct.id}
          productName={selectedProduct.name}
          currentBarcode={selectedProduct.barcode}
          price={selectedProduct.offerPrice || selectedProduct.mrp}
          onSave={handleSaveBarcode}
        />
      )}

      {/* Product Detail Drawer - read-only snapshot of the row's real fields.
          No backend call: every value shown is already loaded in the row. */}
      {detailItem && (() => {
        const cat = CATEGORIES.find(c => sameCategory(c.code, detailItem.category));
        const online = getOnline(detailItem);
        const status = getStockStatus(detailItem);
        const available = (detailItem.stock || 0) - (detailItem.reserved || 0);
        const rows: Array<[string, string]> = [
          ['SKU', detailItem.sku || '-'],
          ['Barcode', detailItem.barcode || 'Not set'],
          ['Category', cat?.label || detailItem.category],
          ['MRP', formatCurrency(detailItem.mrp || 0)],
          ['Offer price', formatCurrency(detailItem.offerPrice || detailItem.mrp || 0)],
          ['In stock', String(detailItem.stock ?? 0)],
          ['Reserved', String(detailItem.reserved ?? 0)],
          ['Available', String(available)],
          [
            'Online',
            online?.online
              ? typeof online.online_stock === 'number'
                ? `Yes (${online.online_stock} online)`
                : 'Yes'
              : 'In-store only',
          ],
          ['Location', detailItem.location || '-'],
          // Attribution is a manager surface (mirrors the backend gate).
          ...(canSeeCataloguers
            ? ([['Catalogued by', detailItem.created_by_name || '-']] as Array<[string, string]>)
            : []),
        ];
        return (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={() => setDetailItem(null)}>
            <div className="bg-white rounded-xl shadow-2xl w-full max-w-md" onClick={(e) => e.stopPropagation()}>
              <div className="px-5 py-4 border-b border-gray-200 flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="font-semibold text-gray-900 truncate">{detailItem.name}</h2>
                  <p className="text-sm text-gray-500">{detailItem.brand}</p>
                </div>
                <button onClick={() => setDetailItem(null)} className="text-gray-500 hover:text-gray-700 shrink-0" aria-label="Close" title="Close">
                  <X className="w-5 h-5" />
                </button>
              </div>
              <div className="p-5">
                <div className="mb-4">
                  <span className={status.class}>{status.label}</span>
                </div>
                <dl className="divide-y divide-gray-100">
                  {rows.map(([label, value]) => (
                    <div key={label} className="flex items-center justify-between py-2 text-sm">
                      <dt className="text-gray-500">{label}</dt>
                      <dd className="text-gray-900 font-medium text-right">{value}</dd>
                    </div>
                  ))}
                </dl>
              </div>
              <div className="px-5 py-3 border-t border-gray-200 flex justify-end gap-2">
                {canManageBarcode && (
                  <button
                    onClick={() => { openBarcodeModal(detailItem); setDetailItem(null); }}
                    className="px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 rounded-lg flex items-center gap-1.5"
                  >
                    <Barcode className="w-4 h-4" /> Manage barcode
                  </button>
                )}
                <button
                  onClick={() => setDetailItem(null)}
                  className="px-4 py-2 bg-bv-red-600 hover:bg-bv-red-700 text-white rounded-lg text-sm font-semibold"
                >
                  Close
                </button>
              </div>
            </div>
          </div>
        );
      })()}

      {/* CSV Import Modal */}
      {showCSVImport && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-white border border-gray-200 rounded-xl w-full max-w-2xl max-h-[80dvh] overflow-hidden flex flex-col">
            <div className="flex items-center justify-between p-5 border-b border-gray-200">
              <div className="flex items-center gap-3">
                <Upload className="w-5 h-5 text-blue-600" />
                <div>
                  <h2 className="text-lg font-semibold text-gray-900">Bulk CSV Product Import</h2>
                  <p className="text-sm text-gray-500">Upload a CSV file with product data</p>
                </div>
              </div>
              <button onClick={() => { setShowCSVImport(false); setCsvFile(null); setCsvPreview([]); setCsvRows([]); }} className="text-gray-500 hover:text-gray-900" aria-label="Close" title="Close">
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="p-5 space-y-4 overflow-y-auto flex-1">
              {/* Template Download */}
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
                <div className="flex items-start gap-3">
                  <FileText className="w-5 h-5 text-blue-600 mt-0.5" />
                  <div>
                    <p className="text-sm text-blue-700 font-medium">CSV Format Required</p>
                    <p className="text-xs text-blue-600 mt-1">
                      Columns: name, sku, category, brand, mrp, offer_price, hsn_code, opening_stock
                    </p>
                    <button
                      onClick={() => {
                        const template = 'name,sku,category,brand,mrp,offer_price,hsn_code,opening_stock\nRay-Ban Aviator Classic,FR-RAYB-3025-GLD,FRAMES,Ray-Ban,12990,12990,900311,5\nEssilor Crizal Alize 1.67,RX-ESSL-CRZL-167,RX_LENSES,Essilor,8500,7200,900150,10';
                        const blob = new Blob([template], { type: 'text/csv' });
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement('a'); a.href = url; a.download = 'product_import_template.csv'; a.click();
                        URL.revokeObjectURL(url);
                      }}
                      className="text-xs text-blue-600 underline mt-2 inline-block hover:text-blue-800"
                    >
                      Download template CSV
                    </button>
                  </div>
                </div>
              </div>

              {/* File Upload */}
              <div className="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center hover:border-gray-400 transition-colors">
                <input
                  type="file"
                  accept=".csv,.tsv"
                  className="hidden"
                  id="csv-upload"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (!file) return;
                    setCsvFile(file);
                    const reader = new FileReader();
                    reader.onload = (ev) => {
                      const text = ev.target?.result as string;
                      const lines = text.split('\n').filter(l => l.trim());
                      if (lines.length < 2) { toast.error('CSV file is empty or has no data rows'); return; }
                      const headers = lines[0].split(',').map(h => h.trim().toLowerCase());
                      const rows = lines.slice(1).map(line => {
                        const values = line.split(',');
                        const row: Record<string, string> = {};
                        headers.forEach((h, i) => { row[h] = values[i]?.trim() || ''; });
                        return row;
                      });
                      setCsvRows(rows);                 // ALL rows -> bulk-create
                      setCsvPreview(rows.slice(0, 10)); // Preview first 10
                      toast.success(`Parsed ${rows.length} product${rows.length === 1 ? '' : 's'} from CSV`);
                    };
                    reader.readAsText(file);
                  }}
                />
                <label htmlFor="csv-upload" className="cursor-pointer">
                  <Upload className="w-8 h-8 text-gray-500 mx-auto mb-2" />
                  <p className="text-sm text-gray-600">{csvFile ? csvFile.name : 'Click to select CSV file'}</p>
                  <p className="text-xs text-gray-500 mt-1">Supports .csv and .tsv files</p>
                </label>
              </div>

              {/* Preview Table */}
              {csvPreview.length > 0 && (
                <div>
                  <h3 className="text-sm font-medium text-gray-700 mb-2">Preview ({csvPreview.length} rows shown)</h3>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead className="bg-gray-50 text-gray-500">
                        <tr>
                          {Object.keys(csvPreview[0]).map(h => (
                            <th key={h} className="px-2 py-2 text-left">{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-200">
                        {csvPreview.map((row, i) => (
                          <tr key={i} className="text-gray-700">
                            {Object.values(row).map((v, j) => (
                              <td key={j} className="px-2 py-1.5 truncate max-w-[120px]">{v}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>

            <div className="p-5 border-t border-gray-200 flex justify-between items-center">
              <p className="text-xs text-gray-500">
                {csvFile ? `${csvRows.length} product${csvRows.length === 1 ? '' : 's'} ready to import` : 'Select a CSV file to begin'}
              </p>
              <div className="flex gap-2">
                <button onClick={() => { setShowCSVImport(false); setCsvFile(null); setCsvPreview([]); setCsvRows([]); }} className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg text-sm hover:bg-gray-200">
                  Cancel
                </button>
                <button
                  onClick={handleImportProducts}
                  disabled={!csvFile || isImporting}
                  className="px-6 py-2 bg-bv-red-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2 hover:bg-bv-red-700 transition-colors"
                >
                  {isImporting ? (
                    <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  ) : (
                    <CheckCircle className="w-4 h-4" />
                  )}
                  {isImporting ? 'Importing...' : 'Import Products'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Full-size product image viewer (owner 2026-07-05) */}
      {lightbox && (
        <ImageLightbox
          images={lightbox.images}
          alt={lightbox.alt}
          onClose={() => setLightbox(null)}
        />
      )}
    </>
  );
}

export default InventoryStockPage;
