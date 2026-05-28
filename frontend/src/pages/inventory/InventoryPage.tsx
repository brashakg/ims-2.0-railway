// ============================================================================
// IMS 2.0 - Inventory Page
// ============================================================================
// NO MOCK DATA - All data from API

import { useState, useEffect, useMemo } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import {
  Search,
  Package,
  AlertTriangle,
  ArrowRightLeft,
  Plus,
  Download,
  BarChart3,
  Boxes,
  TrendingDown,
  Eye,
  Loader2,
  RefreshCw,
  Barcode,
  Upload,
  FileText,
  CheckCircle,
  X,
  ShoppingCart,
  Hash,
  Clock,
  Glasses,
  Sun,
  BookOpen,
  Search as Lens,
  Watch,
  Smartphone,
  Headphones,
  Sparkles,
  Ear,
  Globe,
  LayoutGrid,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { ProductCategory } from '../../types';
import { inventoryApi, catalogApi, storeApi, type OnlineStatus } from '../../services/api';
// Product writes go through the SINGLE validated path (productApi -> /products).
// Imported DIRECTLY from the module (not the api barrel) to dodge the TS2614
// re-export resolution issue documented in CLAUDE.md.
import { productApi, type CreateProductPayload } from '../../services/api/products';
// v2-2b: import the new display API modules DIRECTLY (not via the api barrel)
// to dodge the TS2614 re-export resolution issue documented in CLAUDE.md.
import { displayPlacementsApi, type DisplayPlacement } from '../../services/api/displayPlacements';
import { displayFixturesApi, type DisplayFixture } from '../../services/api/displayFixtures';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { BarcodeManagementModal } from '../../components/inventory/BarcodeManagementModal';
import { StockTransferModal } from '../../components/inventory/StockTransferModal';
import { StockTransferManagement } from '../../components/inventory/StockTransferManagement';
import { ReorderDashboard } from '../../components/inventory/ReorderDashboard';
import { SerialNumberTracker } from '../../components/inventory/SerialNumberTracker';
import { StockAgingReport } from '../../components/inventory/StockAgingReport';
import { StockAlertsOverview } from '../../components/inventory/StockAlertsOverview';
import { NonMovingStockWidget } from '../../components/inventory/NonMovingStockWidget';
import { StockCountScanningInterface } from '../../components/inventory/StockCountScanningInterface';
import { ContactLensInventoryWidget, ContactLensExpiryWidget, LensPowerGridWidget, SellThroughAnalysisWidget, OverstockAnalysisWidget, TransferRecommendationsWidget } from '../../components/inventory/AdvancedInventoryFeatures';
import { DisplayLayoutPanel } from '../../components/inventory/DisplayLayoutPanel';
import { Pagination } from '../../components/common/Pagination';
import clsx from 'clsx';

// Category configuration
// DELTAS Critical #3: replaced emoji icons with the line-icon set.
// Brand has no emoji; chips render Lucide icons inline at 14px.
const CATEGORIES: { code: ProductCategory; label: string; icon: LucideIcon }[] = [
  { code: 'FR', label: 'Frames', icon: Glasses },
  { code: 'SG', label: 'Sunglasses', icon: Sun },
  { code: 'RG', label: 'Reading Glasses', icon: BookOpen },
  { code: 'LS', label: 'Optical Lenses', icon: Lens },
  { code: 'CL', label: 'Contact Lenses', icon: Eye },
  { code: 'WT', label: 'Watches', icon: Watch },
  { code: 'SMTWT', label: 'Smartwatches', icon: Smartphone },
  { code: 'SMTSG', label: 'Smart Sunglasses', icon: Sparkles },
  { code: 'SMTFR', label: 'Smart Frames', icon: Sparkles },
  { code: 'CK', label: 'Wall Clocks', icon: Clock },
  { code: 'ACC', label: 'Accessories', icon: Headphones },
  { code: 'HA', label: 'Hearing Aids', icon: Ear },
];

// Stock item type
interface StockItem {
  id: string;
  sku: string;
  name: string;
  productName?: string;
  category: ProductCategory;
  brand: string;
  mrp: number;
  offerPrice: number;
  stock: number;
  quantity?: number;
  reserved: number;
  location?: string;
  lowStockThreshold?: number;
  minStock?: number;
  barcode?: string;
  storeBarcode?: string;
}

// Stock movement type
interface StockMovement {
  id: string;
  type: 'IN' | 'OUT' | 'TRANSFER' | 'ADJUSTMENT';
  productName: string;
  sku: string;
  quantity: number;
  reason: string;
  createdAt: string;
  createdBy: string;
}

type ViewTab = 'alerts' | 'catalog' | 'display-layout' | 'low-stock' | 'reorders' | 'serial-numbers' | 'aging' | 'transfers' | 'movements' | 'non-moving' | 'stock-count' | 'contact-lens' | 'power-grid' | 'sell-through' | 'overstock' | 'rebalance';

export function InventoryPage() {
  const { user, hasRole } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  // Data state
  const [inventory, setInventory] = useState<StockItem[]>([]);
  const [lowStockItems, setLowStockItems] = useState<StockItem[]>([]);
  const [movements, setMovements] = useState<StockMovement[]>([]);
  // Online (Shopify/e-commerce) status keyed by sku and/or barcode.
  const [onlineStatus, setOnlineStatus] = useState<Record<string, OnlineStatus>>({});
  const [movementFilter, setMovementFilter] = useState<StockMovement['type'] | 'ALL'>('ALL');
  const [movementSearch, setMovementSearch] = useState('');

  // v2-2b: placements + fixtures map for the Zone column on the Stock ledger.
  // Batched: one list call for placements + one for fixtures per visible store
  // -- NEVER N+1 per row. Memoised below.
  const [placements, setPlacements] = useState<DisplayPlacement[]>([]);
  const [fixturesMap, setFixturesMap] = useState<Record<string, DisplayFixture>>({});

  // UI state
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<ProductCategory | null>(null);
  const [activeTab, setActiveTab] = useState<ViewTab>('catalog');

  // Online/offline availability filter + cross-store stock view.
  const [availabilityFilter, setAvailabilityFilter] = useState<'all' | 'online' | 'offline'>('all');
  const [storeFilter, setStoreFilter] = useState<string>(user?.activeStoreId || '');
  const [stores, setStores] = useState<{ id: string; name: string }[]>([]);

  // Pagination state
  const [currentPage, setCurrentPage] = useState(1);
  const pageSize = 50;

  // Deep-link target for Display Layout (cleared once consumed inside the panel).
  const [pendingFixtureId, setPendingFixtureId] = useState<string | null>(null);

  // Sync active tab from URL query params (e.g. /inventory?tab=transfers).
  // Also accepts ?fixture={fixture_id} as a deep-link from the Stock Ledger
  // Zone cell -- handed down to <DisplayLayoutPanel /> as initialFixtureId.
  useEffect(() => {
    const tabParam = searchParams.get('tab');
    const fixtureParam = searchParams.get('fixture');
    if (tabParam && tabParam !== activeTab) {
      const validTabs: ViewTab[] = ['alerts', 'catalog', 'display-layout', 'low-stock', 'reorders', 'serial-numbers', 'aging', 'transfers', 'movements', 'non-moving', 'stock-count', 'contact-lens', 'power-grid', 'sell-through', 'overstock', 'rebalance'];
      if (validTabs.includes(tabParam as ViewTab)) {
        setActiveTab(tabParam as ViewTab);
      }
    }
    if (fixtureParam) {
      setPendingFixtureId(fixtureParam);
    }
  }, [searchParams]);

  // Loading state
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Barcode modal state
  const [showBarcodeModal, setShowBarcodeModal] = useState(false);
  const [selectedProduct, setSelectedProduct] = useState<StockItem | null>(null);

  // Transfer modal state
  const [showTransferModal, setShowTransferModal] = useState(false);

  // CSV Import state
  const [showCSVImport, setShowCSVImport] = useState(false);
  const [csvFile, setCsvFile] = useState<File | null>(null);
  // csvRows = ALL parsed rows (sent to the validated bulk-create endpoint).
  // csvPreview = first 10 rows, for the on-screen preview table only.
  const [csvRows, setCsvRows] = useState<Array<Record<string, string>>>([]);
  const [csvPreview, setCsvPreview] = useState<Array<Record<string, string>>>([]);
  const [isImporting, setIsImporting] = useState(false);


  // Role-based permissions
  const canTransfer = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER']);
  const canAddProduct = hasRole(['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER']);
  const canExport = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']);
  const canManageBarcode = hasRole(['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER', 'STORE_MANAGER']);

  // Load data on mount + whenever the viewed store changes
  useEffect(() => {
    loadInventory();
  }, [storeFilter]);

  // v2-2b: load fixtures + placements for the Zone column. Two batched list
  // calls per store -- never per-row. Fail-soft: a missing backend just
  // means the column stays muted ('-') and the deep-link is a no-op.
  useEffect(() => {
    const sid = storeFilter || user?.activeStoreId;
    if (!sid) {
      setPlacements([]);
      setFixturesMap({});
      return;
    }
    let cancelled = false;
    Promise.all([
      displayPlacementsApi.list({ store_id: sid }).catch(() => ({ placements: [], total: 0 })),
      displayFixturesApi.list({ store_id: sid, active: true }).catch(() => ({ fixtures: [], total: 0 })),
    ])
      .then(([pRes, fRes]) => {
        if (cancelled) return;
        setPlacements(pRes.placements || []);
        const m: Record<string, DisplayFixture> = {};
        for (const f of fRes.fixtures || []) m[f.fixture_id] = f;
        setFixturesMap(m);
      })
      .catch(() => {
        if (cancelled) return;
        setPlacements([]);
        setFixturesMap({});
      });
    return () => { cancelled = true; };
  }, [storeFilter, user?.activeStoreId]);

  // Stores the user may view (multi-store roles see all; others see their own).
  useEffect(() => {
    const allStoreAccess = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER']);
    storeApi.getStores().then((res: any) => {
      const list = res?.stores || res || [];
      const mapped = (Array.isArray(list) ? list : [])
        .map((s: any) => ({
          id: String(s.store_id || s.id || s._id || ''),
          name: String(s.store_name || s.storeName || s.name || s.store_id || s.id || ''),
        }))
        .filter((s: { id: string }) => s.id && (allStoreAccess || (user?.storeIds || []).includes(s.id)));
      setStores(mapped);
    }).catch(() => setStores([]));
  }, [user?.storeIds]);

  // Follow the global active store when it is switched elsewhere (e.g. topbar).
  useEffect(() => {
    if (user?.activeStoreId) setStoreFilter(user.activeStoreId);
  }, [user?.activeStoreId]);

  const loadInventory = async () => {
    const storeId = storeFilter || user?.activeStoreId;
    if (!storeId) return;

    setIsLoading(true);
    setError(null);

    try {
      // Fetch inventory and low stock in parallel (for the viewed store)
      const [stockData, lowStockData] = await Promise.all([
        inventoryApi.getStock(storeId).catch(() => ({ items: [] })),
        inventoryApi.getLowStock(storeId).catch(() => ({ items: [] })),
      ]);

      // Process stock data
      const items = stockData?.items || stockData || [];
      const normalized: StockItem[] = Array.isArray(items) ? items.map((item: StockItem) => ({
        ...item,
        name: item.name || item.productName || 'Unknown Product',
        stock: item.stock || item.quantity || 0,
        lowStockThreshold: item.lowStockThreshold || item.minStock || 5,
        reserved: item.reserved || 0,
      })) : [];
      setInventory(normalized);

      // Tag which SKUs are online (in Shopify via the e-commerce catalog) and
      // how much online stock exists. Fail-soft: bridge off -> {} -> no badges.
      const ids = Array.from(new Set(
        normalized.flatMap(i => [i.sku, (i as any).barcode, (i as any).storeBarcode])
          .map(v => String(v || '').trim())
          .filter(Boolean)
      ));
      if (ids.length > 0) {
        catalogApi.getOnlineStatus(ids)
          .then(setOnlineStatus)
          .catch(() => setOnlineStatus({}));
      } else {
        setOnlineStatus({});
      }

      // Process low stock data
      const lowItems = lowStockData?.items || lowStockData || [];
      setLowStockItems(Array.isArray(lowItems) ? lowItems.map((item: StockItem) => ({
        ...item,
        name: item.name || item.productName || 'Unknown Product',
        stock: item.stock || item.quantity || 0,
        lowStockThreshold: item.lowStockThreshold || item.minStock || 5,
      })) : []);

      // Stock movements are recorded by the backend when inventory changes occur.
      // No mock data is generated here - movements will populate as real events are logged.
      setMovements([]);
    } catch {
      setError('Failed to load inventory. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  // Reset page when filters change
  useEffect(() => {
    setCurrentPage(1);
  }, [searchQuery, selectedCategory, availabilityFilter, storeFilter]);

  // Online status for an item, matched by sku -> barcode -> storeBarcode.
  const getOnline = (item: StockItem): OnlineStatus | undefined => {
    const keys = [item.sku, item.barcode, item.storeBarcode]
      .map(v => String(v || '').trim())
      .filter(Boolean);
    for (const k of keys) {
      if (onlineStatus[k]) return onlineStatus[k];
    }
    return undefined;
  };

  // v2-2b: pick the primary placement for each SKU (or first if none flagged).
  // Memoised so a re-render from search/category typing doesn't recompute
  // a thousand-row map.
  const primaryPlacementBySku = useMemo(() => {
    const m: Record<string, DisplayPlacement> = {};
    for (const p of placements) {
      const existing = m[p.sku];
      if (!existing) {
        m[p.sku] = p;
        continue;
      }
      // Prefer is_primary=true over anything else; otherwise keep the first.
      if (p.is_primary && !existing.is_primary) m[p.sku] = p;
    }
    return m;
  }, [placements]);

  // Resolved {fixture, placement} for a SKU. Returns undefined when not placed.
  const getZone = (sku: string): { fixture: DisplayFixture; placement: DisplayPlacement } | undefined => {
    const placement = primaryPlacementBySku[sku];
    if (!placement) return undefined;
    const fixture = fixturesMap[placement.fixture_id];
    if (!fixture) return undefined;
    return { fixture, placement };
  };

  // Deep-link helper: hop to Display Layout with the fixture pre-selected and
  // clean the URL once consumed (so re-navigation works).
  const openFixtureInLayout = (fixtureId: string) => {
    setActiveTab('display-layout');
    setPendingFixtureId(fixtureId);
    if (typeof window !== 'undefined' && window.history?.replaceState) {
      const url = new URL(window.location.href);
      url.searchParams.set('tab', 'display-layout');
      url.searchParams.set('fixture', fixtureId);
      window.history.replaceState({}, '', url.toString());
    }
  };

  // Filter inventory locally
  const filteredInventory = inventory.filter(item => {
    const matchesSearch = !searchQuery ||
      item.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      item.sku?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      item.brand?.toLowerCase().includes(searchQuery.toLowerCase());

    const matchesCategory = !selectedCategory || item.category === selectedCategory;

    const isOnline = !!getOnline(item)?.online;
    const matchesAvailability =
      availabilityFilter === 'all' ? true : availabilityFilter === 'online' ? isOnline : !isOnline;

    return matchesSearch && matchesCategory && matchesAvailability;
  });

  // Paginate filtered results
  const paginatedInventory = filteredInventory.slice(
    (currentPage - 1) * pageSize,
    currentPage * pageSize
  );

  // Calculate stats
  const totalSKUs = inventory.length;
  const totalValue = inventory.reduce((sum, item) => sum + ((item.offerPrice || item.mrp || 0) * (item.stock || 0)), 0);
  const lowStockCount = lowStockItems.length;

  const getStockStatus = (item: StockItem) => {
    const threshold = item.lowStockThreshold || item.minStock || 5;
    if (item.stock === 0) return { label: 'Out of Stock', class: 'badge-error' };
    if (item.stock <= threshold) return { label: 'Low Stock', class: 'badge-warning' };
    return { label: 'In Stock', class: 'badge-success' };
  };

  // Count of inventory rows currently online.
  const onlineCount = inventory.reduce((n, i) => (getOnline(i)?.online ? n + 1 : n), 0);

  const formatCurrency = (amount: number) => {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 0,
    }).format(amount);
  };

  // Handle CSV import.
  // Parses the (already-parsed) CSV rows into the canonical CreateProductPayload
  // shape and creates them through the SINGLE validated path
  // (`POST /products/bulk-create`). The backend runs the SAME validators as the
  // single-create endpoint (category 422 guard, MRP >= offer_price, canonical
  // GST/HSN derivation) and returns a per-row result -- valid rows are created,
  // invalid rows are skipped and reported with reasons. Previously this posted
  // the raw file to the unvalidated `/admin/products/bulk-import`, which only
  // stashed the file and never actually created any product (a silent no-op),
  // so the "imported N" toast was misleading.
  const handleImportProducts = async () => {
    if (!csvFile || csvRows.length === 0) {
      toast.error('Please select a CSV file first');
      return;
    }

    // Map CSV rows -> CreateProductPayload. Required canonical fields:
    // category, sku, brand, model, mrp (+ offer_price, which the backend
    // requires > 0). `name` maps to `model`; opening_stock is intentionally
    // ignored (stock is created via GRN, not at product-create time).
    const num = (v: string | undefined): number | undefined => {
      if (v === undefined || String(v).trim() === '') return undefined;
      const n = Number(String(v).replace(/[^0-9.\-]/g, ''));
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
        // Surface the first few row errors so a bad category / price / dup SKU
        // is actionable rather than silently dropped.
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
        await loadInventory();
      }
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Import failed. Check CSV format and try again.';
      toast.error(msg);
    } finally {
      setIsImporting(false);
    }
  };

  // Handle barcode save
  const handleSaveBarcode = async (barcode: string) => {
    if (!selectedProduct) return;

    try {
      // Write the barcode through the SINGLE validated product-update path
      // (`PUT /products/{id}`). selectedProduct.id is the canonical product_id
      // (the /inventory/stock aggregate returns id == product_id). Previously
      // this hit the now-retired, unvalidated `PUT /admin/products/{id}`.
      await productApi.updateProduct(selectedProduct.id, { barcode });
      toast.success(`Barcode saved for ${selectedProduct.name}`);
      await loadInventory();
    } catch {
      toast.error('Failed to save barcode. Please try again.');
      throw new Error('Failed to save barcode');
    }
  };

  // Open barcode modal for a product
  const openBarcodeModal = (item: StockItem) => {
    setSelectedProduct(item);
    setShowBarcodeModal(true);
  };

  const tabList: Array<{ id: ViewTab; label: string; icon: typeof AlertTriangle; count?: number }> = [
    { id: 'alerts',         label: 'Alerts',          icon: AlertTriangle },
    { id: 'catalog',        label: 'Catalog',         icon: Package, count: totalSKUs },
    { id: 'display-layout', label: 'Display layout',  icon: LayoutGrid, count: Object.keys(fixturesMap).length || undefined },
    { id: 'low-stock',      label: 'Low stock',       icon: AlertTriangle, count: lowStockCount },
    { id: 'reorders',       label: 'Reorders',        icon: ShoppingCart },
    { id: 'serial-numbers', label: 'Serial numbers',  icon: Hash },
    { id: 'aging',          label: 'Stock aging',     icon: Clock },
    { id: 'transfers',      label: 'Transfers',       icon: ArrowRightLeft },
    { id: 'movements',      label: 'Movements',       icon: Eye },
    { id: 'non-moving',     label: 'Non-moving',      icon: TrendingDown },
    { id: 'stock-count',    label: 'Stock count',     icon: Barcode },
    { id: 'contact-lens',   label: 'Contact lens',    icon: Eye },
    { id: 'power-grid',     label: 'Lens power grid', icon: BarChart3 },
    { id: 'sell-through',   label: 'Sell-through',    icon: TrendingDown },
    { id: 'overstock',      label: 'Overstock',       icon: Boxes },
    { id: 'rebalance',      label: 'Rebalance',       icon: ArrowRightLeft },
  ];

  /**
   * DELTAS punch-list: collapse 14 tabs into 5 functional groups.
   * Catalog stays a top-level entry (primary read view); everything
   * else clusters by intent — health checks, day-to-day operations,
   * optical-specific surfaces, and read-only analytics.
   *
   * Sub-nav appears only when the active group has >1 child.
   *
   * v2-2b: Display Layout joins the Catalog group so the "where is this SKU
   * physically?" question lives one tab-click away from the stock ledger.
   */
  type GroupId = 'catalog' | 'health' | 'ops' | 'optical' | 'insights';
  const tabGroups: Array<{
    id: GroupId;
    label: string;
    icon: typeof AlertTriangle;
    members: ViewTab[];
  }> = [
    { id: 'catalog',  label: 'Catalog',     icon: Package,         members: ['catalog', 'display-layout'] },
    { id: 'health',   label: 'Stock health',icon: AlertTriangle,   members: ['low-stock', 'non-moving', 'aging', 'alerts'] },
    { id: 'ops',      label: 'Operations',  icon: ShoppingCart,    members: ['reorders', 'transfers', 'rebalance', 'movements', 'stock-count'] },
    { id: 'optical',  label: 'Optical',     icon: Eye,             members: ['serial-numbers', 'contact-lens', 'power-grid'] },
    { id: 'insights', label: 'Insights',    icon: BarChart3,       members: ['sell-through', 'overstock'] },
  ];
  const activeGroupId: GroupId =
    tabGroups.find((g) => g.members.includes(activeTab))?.id ?? 'catalog';
  const activeGroup = tabGroups.find((g) => g.id === activeGroupId)!;
  const subTabs = tabList.filter((t) => activeGroup.members.includes(t.id));
  const groupCount = (g: typeof tabGroups[number]): number | undefined => {
    // Surface a count badge on the group when one of its child tabs has one.
    const child = tabList.find((t) => g.members.includes(t.id) && typeof t.count === 'number');
    return child?.count;
  };

  return (
    <div className="inv-body">
      {/* Editorial header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Inventory</div>
          <h1>What's on the floor.</h1>
          <div className="hint">Live stock by SKU across {CATEGORIES.length} categories · cycle count · transfers · non-moving flags.</div>
        </div>
        <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
          <button
            onClick={loadInventory}
            disabled={isLoading}
            className="btn sm"
          >
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            Refresh
          </button>
          {canExport && (
            <button onClick={() => toast.info('Export feature coming soon')} className="btn sm">
              <Download className="w-4 h-4" /> Export
            </button>
          )}
          {canTransfer && (
            <button onClick={() => setShowTransferModal(true)} className="btn sm">
              <ArrowRightLeft className="w-4 h-4" /> New transfer
            </button>
          )}
          {canAddProduct && (
            <>
              <button onClick={() => setShowCSVImport(true)} className="btn sm">
                <Upload className="w-4 h-4" /> CSV import
              </button>
              <button onClick={() => navigate('/catalog/add')} className="btn sm primary">
                <Plus className="w-4 h-4" /> Add product
              </button>
            </>
          )}
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="s-section" style={{ padding: 12, borderColor: 'var(--err-50)', background: 'var(--err-50)', display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
          <AlertTriangle className="w-5 h-5" style={{ color: 'var(--err)' }} />
          <span style={{ color: 'var(--err)' }}>{error}</span>
          <button onClick={loadInventory} className="btn sm" style={{ marginLeft: 'auto' }}>Retry</button>
        </div>
      )}

      {/* 6-cell stat strip (incl. Online) */}
      <div className="stat-strip stat-strip-6">
        <div>
          <div className="l">Total SKUs</div>
          <div className="v">{totalSKUs.toLocaleString('en-IN')}</div>
          <div className="d">across {CATEGORIES.length} categories</div>
        </div>
        <div>
          <div className="l">Stock value</div>
          <div className="v">₹ {(totalValue / 100000).toFixed(1)}L</div>
          <div className="d">total landed inventory</div>
        </div>
        <div>
          <div className="l">Low stock</div>
          <div className="v" style={{ color: lowStockCount > 0 ? 'var(--err)' : 'var(--ink)' }}>{lowStockCount}</div>
          <div className={'d ' + (lowStockCount > 0 ? 'bad' : 'good')}>
            {lowStockCount > 0 ? 'needs reorder' : 'all above reorder pt'}
          </div>
        </div>
        <div>
          <div className="l">Online</div>
          <div className="v" style={{ color: onlineCount > 0 ? 'var(--ok, #059669)' : 'var(--ink)' }}>{onlineCount}</div>
          <div className="d">{onlineCount > 0 ? 'listed in Shopify' : 'none synced online'}</div>
        </div>
        <div>
          <div className="l">Categories</div>
          <div className="v">{CATEGORIES.length}</div>
          <div className="d">incl. lenses, frames, CL</div>
        </div>
        <div>
          <div className="l">View</div>
          <div className="v" style={{ fontSize: 22 }}>{tabList.find(t => t.id === activeTab)?.label ?? '—'}</div>
          <div className="d">active tab</div>
        </div>
      </div>

      {/* Primary tab groups (5) — Catalog · Stock health · Operations · Optical · Insights */}
      <div className="inv-tabs">
        {tabGroups.map((g) => {
          const GIcon = g.icon;
          const cnt = groupCount(g);
          return (
            <button
              key={g.id}
              onClick={() => {
                if (g.id === activeGroupId) return;
                // Default each group to its first child tab.
                setActiveTab(g.members[0]);
              }}
              className={activeGroupId === g.id ? 'on' : ''}
            >
              <GIcon className="w-4 h-4" />
              {g.label}
              {typeof cnt === 'number' && <span className="count">· {cnt}</span>}
            </button>
          );
        })}
      </div>

      {/* Sub-nav for the active group — only renders when there is >1 child */}
      {subTabs.length > 1 && (
        <div className="inv-tabs" style={{ marginTop: -6, paddingLeft: 4, gap: 14 }}>
          {subTabs.map((tab) => {
            const TabIcon = tab.icon;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={activeTab === tab.id ? 'on' : ''}
                style={{ fontSize: 13 }}
              >
                <TabIcon className="w-3.5 h-3.5" />
                {tab.label}
                {typeof tab.count === 'number' && <span className="count">· {tab.count}</span>}
              </button>
            );
          })}
        </div>
      )}

      {/* Search and Filters */}
      {activeTab !== 'alerts' && activeTab !== 'transfers' && activeTab !== 'reorders' && activeTab !== 'serial-numbers' && activeTab !== 'aging' && activeTab !== 'display-layout' && (
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
                ).slice(0, 8).map((item: any) => (
                  <option key={item.id || item.sku} value={item.name}>{item.sku} · {item.brand} · ₹{Math.round(item.mrp || 0)}</option>
                ))}
              </datalist>
            )}
          </div>
        </div>

        {/* Availability (online / offline) + store filter */}
        <div className="flex flex-wrap items-center gap-2 mb-4">
          <span className="text-xs font-medium text-gray-500 uppercase mr-1">Stock</span>
          {(['all', 'online', 'offline'] as const).map(f => (
            <button
              key={f}
              onClick={() => setAvailabilityFilter(f)}
              className={clsx(
                'px-3 py-1.5 rounded-full text-sm font-medium whitespace-nowrap transition-colors inline-flex items-center gap-1.5 border',
                availabilityFilter === f
                  ? 'bg-bv-red-50 text-gray-900 border-bv-red-500'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200 border-transparent',
              )}
            >
              {f === 'online' && <Globe className="w-3.5 h-3.5" strokeWidth={1.6} />}
              {f === 'all' ? 'All' : f === 'online' ? 'Online' : 'Offline'}
            </button>
          ))}
          {stores.length > 1 && (
            <div className="ml-auto flex items-center gap-2">
              <label htmlFor="inv-store" className="text-xs font-medium text-gray-500 uppercase">Store</label>
              <select
                id="inv-store"
                value={storeFilter}
                onChange={e => setStoreFilter(e.target.value)}
                className="input-field text-sm py-1.5 w-48"
              >
                {stores.map(s => (
                  <option key={s.id} value={s.id}>
                    {s.name}{s.id === user?.activeStoreId ? ' (current)' : ''}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>

        {/* Category Filters */}
        <div className="flex gap-2 overflow-x-auto pb-2 scrollbar-hide">
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
          {CATEGORIES.map(cat => {
            const IconCmp = cat.icon;
            const selected = selectedCategory === cat.code;
            return (
              <button
                key={cat.code}
                onClick={() => setSelectedCategory(cat.code)}
                className={clsx(
                  'px-3 py-1.5 rounded-full text-sm font-medium whitespace-nowrap transition-colors inline-flex items-center gap-1.5 border',
                  /* DELTAS Critical #4: canonical selected state is
                     bv-50 fill + bv-500 border + ink text (mirrors
                     the Returns Exchange card). Solid red was
                     reserved for one hero CTA per screen. */
                  selected
                    ? 'bg-bv-red-50 text-gray-900 border-bv-red-500'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200 border-transparent',
                )}
              >
                <IconCmp className="w-3.5 h-3.5" strokeWidth={1.6} />
                <span>{cat.label}</span>
              </button>
            );
          })}
        </div>
      </div>
      )}

      {/* Stock Alerts Tab */}
      {activeTab === 'alerts' && (
        <div className="card">
          <StockAlertsOverview />
        </div>
      )}

      {/* v2-2b: Display layout tab — floor map of fixtures + side detail panel. */}
      {activeTab === 'display-layout' && (
        <DisplayLayoutPanel
          initialFixtureId={pendingFixtureId}
          onFixtureSelectionConsumed={() => {
            setPendingFixtureId(null);
            // Drop ?fixture= from the URL so re-navigation is clean.
            if (typeof window !== 'undefined' && window.history?.replaceState) {
              const url = new URL(window.location.href);
              url.searchParams.delete('fixture');
              window.history.replaceState({}, '', url.toString());
            }
          }}
        />
      )}

      {/* Inventory Table */}
      {activeTab === 'catalog' && (
        <div className="card overflow-hidden">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
            </div>
          ) : filteredInventory.length === 0 ? (
            <div className="text-center py-12 text-gray-500">
              <Package className="w-12 h-12 mx-auto mb-2 opacity-50" />
              <p>{searchQuery || selectedCategory || availabilityFilter !== 'all' ? 'No products found matching your filters' : 'No products in inventory'}</p>
            </div>
          ) : (
            <>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Product</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">SKU</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Barcode</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Category</th>
                    <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">MRP</th>
                    <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">Offer</th>
                    <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">In-Store</th>
                    <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Zone</th>
                    <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Online</th>
                    <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Location</th>
                    <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Status</th>
                    <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {paginatedInventory.map((item, i) => {
                    const status = getStockStatus(item);
                    const category = CATEGORIES.find(c => c.code === item.category);
                    return (
                      <tr key={item.id || item.sku || `row-${i}`} className="hover:bg-gray-50">
                        <td className="px-4 py-3">
                          <div>
                            <p className="font-medium text-gray-900">{item.name}</p>
                            <p className="text-sm text-gray-500">{item.brand}</p>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-600">{item.sku}</td>
                        <td className="px-4 py-3">
                          {(item as any).barcode ? (
                            <span className="text-xs font-mono text-gray-700 bg-gray-100 px-2 py-1 rounded">
                              {(item as any).barcode}
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
                        <td className="px-4 py-3 text-center">
                          <span className="font-medium">{item.stock - (item.reserved || 0)}</span>
                          {item.reserved > 0 && (
                            <span className="text-xs text-orange-500 ml-1">+{item.reserved} reserved</span>
                          )}
                        </td>
                        {/* v2-2b: Zone column — primary placement (fixture code + zone). Cell
                            click deep-links to the Display layout tab with the fixture pre-selected. */}
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
                                onClick={(e) => { e.stopPropagation(); openFixtureInLayout(z.fixture.fixture_id); }}
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
                        <td className="px-4 py-3 text-center">
                          {(() => {
                            const o = getOnline(item);
                            if (!o?.online) {
                              return <span className="text-xs text-gray-400">In-store only</span>;
                            }
                            return (
                              <div className="flex flex-col items-center gap-0.5">
                                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-100 text-emerald-700">
                                  <Globe className="w-3 h-3" strokeWidth={2} />
                                  Online
                                </span>
                                <span className="text-xs text-gray-600">{o.online_stock} online</span>
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
                              >
                                <Barcode className="w-4 h-4" />
                              </button>
                            )}
                            <button
                              onClick={() => toast.info(`View details for ${item.name}`)}
                              className="p-2 text-gray-500 hover:text-bv-red-600 transition-colors"
                              title="View Details"
                            >
                              <Eye className="w-4 h-4" />
                            </button>
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
      )}

      {/* Low Stock Tab */}
      {activeTab === 'low-stock' && (
        <div className="card">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
            </div>
          ) : lowStockItems.length === 0 ? (
            <div className="text-center py-12 text-gray-500">
              <Package className="w-12 h-12 mx-auto mb-2 opacity-50" />
              <p>No low stock items</p>
            </div>
          ) : (
            <div className="space-y-3">
              {lowStockItems.map(item => (
                <div
                  key={item.id}
                  className="flex items-center justify-between p-4 bg-yellow-50 border border-yellow-200 rounded-lg"
                >
                  <div className="flex items-center gap-4">
                    <div className="w-10 h-10 bg-yellow-100 rounded-lg flex items-center justify-center">
                      <AlertTriangle className="w-5 h-5 text-yellow-600" />
                    </div>
                    <div>
                      <p className="font-medium text-gray-900">{item.name}</p>
                      <p className="text-sm text-gray-500">{item.sku} • {item.brand}</p>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="text-lg font-bold text-yellow-600">{item.stock} left</p>
                    <p className="text-xs text-gray-500">Min: {item.lowStockThreshold || item.minStock || 5}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Reorders Tab */}
      {activeTab === 'reorders' && (
        <div className="space-y-4">
          <ReorderDashboard />
        </div>
      )}

      {/* Serial Numbers Tab */}
      {activeTab === 'serial-numbers' && (
        <div className="space-y-4">
          <SerialNumberTracker />
        </div>
      )}

      {/* Stock Aging Tab */}
      {activeTab === 'aging' && (
        <div className="space-y-4">
          <StockAgingReport />
        </div>
      )}

      {/* Transfers Tab */}
      {activeTab === 'transfers' && (
        <div className="space-y-4">
          <StockTransferManagement />
        </div>
      )}

      {/* Movements Tab - Double-Entry Stock Audit Trail */}
      {activeTab === 'movements' && (() => {
        const filteredMovements = movements.filter(m => {
          const matchesType = movementFilter === 'ALL' || m.type === movementFilter;
          const matchesSearch = !movementSearch ||
            m.productName.toLowerCase().includes(movementSearch.toLowerCase()) ||
            m.sku.toLowerCase().includes(movementSearch.toLowerCase()) ||
            m.reason.toLowerCase().includes(movementSearch.toLowerCase());
          return matchesType && matchesSearch;
        });
        const movementStats = {
          totalIn: movements.filter(m => m.type === 'IN').reduce((s, m) => s + m.quantity, 0),
          totalOut: movements.filter(m => m.type === 'OUT').reduce((s, m) => s + m.quantity, 0),
          transfers: movements.filter(m => m.type === 'TRANSFER').length,
          adjustments: movements.filter(m => m.type === 'ADJUSTMENT').length,
        };
        const typeConfig: Record<StockMovement['type'], { label: string; color: string; bg: string; prefix: string }> = {
          IN: { label: 'Stock In', color: 'text-green-700', bg: 'bg-green-100', prefix: '+' },
          OUT: { label: 'Stock Out', color: 'text-red-700', bg: 'bg-red-100', prefix: '-' },
          TRANSFER: { label: 'Transfer', color: 'text-blue-700', bg: 'bg-blue-100', prefix: '→' },
          ADJUSTMENT: { label: 'Adjustment', color: 'text-amber-700', bg: 'bg-amber-100', prefix: '±' },
        };
        return (
          <div className="space-y-4">
            {/* Movement Summary */}
            <div className="grid grid-cols-2 tablet:grid-cols-4 gap-3">
              <div className="bg-green-50 rounded-lg border border-green-200 p-3">
                <p className="text-2xl font-bold text-green-600">+{movementStats.totalIn}</p>
                <p className="text-xs text-green-600">Total Stock In</p>
              </div>
              <div className="bg-red-50 rounded-lg border border-red-200 p-3">
                <p className="text-2xl font-bold text-red-600">-{movementStats.totalOut}</p>
                <p className="text-xs text-red-600">Total Stock Out</p>
              </div>
              <div className="bg-blue-50 rounded-lg border border-blue-200 p-3">
                <p className="text-2xl font-bold text-blue-600">{movementStats.transfers}</p>
                <p className="text-xs text-blue-600">Transfers</p>
              </div>
              <div className="bg-amber-50 rounded-lg border border-amber-200 p-3">
                <p className="text-2xl font-bold text-amber-600">{movementStats.adjustments}</p>
                <p className="text-xs text-amber-600">Adjustments</p>
              </div>
            </div>

            {/* Filters */}
            <div className="flex flex-wrap gap-3 items-center">
              <div className="relative flex-1 min-w-[200px]">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                <input
                  type="text"
                  value={movementSearch}
                  onChange={e => setMovementSearch(e.target.value)}
                  placeholder="Search product, SKU, or reason..."
                  className="input-field pl-10 text-sm"
                />
              </div>
              <div className="flex gap-1">
                {(['ALL', 'IN', 'OUT', 'TRANSFER', 'ADJUSTMENT'] as const).map(t => (
                  <button
                    key={t}
                    onClick={() => setMovementFilter(t)}
                    className={clsx(
                      'px-3 py-1.5 rounded-lg text-xs font-medium transition-colors',
                      movementFilter === t
                        ? 'bg-bv-red-600 text-white'
                        : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                    )}
                  >
                    {t === 'ALL' ? 'All' : typeConfig[t].label}
                  </button>
                ))}
              </div>
            </div>

            {/* Movements Table */}
            <div className="card overflow-hidden">
              {isLoading ? (
                <div className="flex items-center justify-center py-12">
                  <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
                </div>
              ) : filteredMovements.length === 0 ? (
                <div className="text-center py-12 text-gray-500">
                  <ArrowRightLeft className="w-12 h-12 mx-auto mb-2 opacity-50" />
                  <p className="font-medium">No stock movements recorded yet</p>
                  <p className="text-sm mt-1">Stock movements will appear here as inventory changes are recorded</p>
                </div>
              ) : (
                <>
                  <div className="grid grid-cols-[auto_1fr_120px_80px_100px_100px] gap-2 px-4 py-2 bg-gray-50 border-b text-xs font-medium text-gray-500 uppercase">
                    <div className="w-8">Type</div>
                    <div>Product / Reason</div>
                    <div>SKU</div>
                    <div className="text-right">Qty</div>
                    <div>By</div>
                    <div>Time</div>
                  </div>
                  <div className="divide-y divide-gray-100 max-h-[500px] overflow-y-auto">
                    {filteredMovements.map(movement => {
                      const tc = typeConfig[movement.type];
                      return (
                        <div key={movement.id} className={clsx(
                          'grid grid-cols-[auto_1fr_120px_80px_100px_100px] gap-2 px-4 py-3 items-center text-sm',
                          movement.type === 'IN' ? 'bg-green-50/30' :
                          movement.type === 'OUT' ? 'bg-red-50/30' : ''
                        )}>
                          <div>
                            <span className={clsx('inline-flex items-center justify-center w-8 h-8 rounded-full text-xs font-bold', tc.bg, tc.color)}>
                              {tc.prefix}
                            </span>
                          </div>
                          <div>
                            <p className="font-medium text-gray-900">{movement.productName}</p>
                            <p className="text-xs text-gray-500">{movement.reason}</p>
                          </div>
                          <div className="text-xs text-gray-500 font-mono">{movement.sku}</div>
                          <div className={clsx('text-right font-bold', tc.color)}>
                            {tc.prefix}{movement.quantity}
                          </div>
                          <div className="text-xs text-gray-600">{movement.createdBy}</div>
                          <div className="text-xs text-gray-500">
                            {new Date(movement.createdAt).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
                            <br />
                            {new Date(movement.createdAt).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' })}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                  <div className="px-4 py-2 bg-gray-50 border-t text-xs text-gray-500">
                    Showing {filteredMovements.length} of {movements.length} movements
                    {movementFilter !== 'ALL' && ' (filtered)'}
                  </div>
                </>
              )}
            </div>
          </div>
        );
      })()}


      {/* Non-Moving Stock Tab */}
      {activeTab === 'non-moving' && (
        <NonMovingStockWidget />
      )}

      {/* Stock Count Scanning Tab */}
      {activeTab === 'stock-count' && (
        <StockCountScanningInterface />
      )}

      {/* Contact Lens Inventory + Expiry Tab */}
      {activeTab === 'contact-lens' && (
        <div className="space-y-4">
          <ContactLensInventoryWidget />
          <ContactLensExpiryWidget />
        </div>
      )}

      {/* Lens Power Grid Tab */}
      {activeTab === 'power-grid' && (
        <LensPowerGridWidget />
      )}

      {/* Sell-Through Analysis Tab */}
      {activeTab === 'sell-through' && (
        <SellThroughAnalysisWidget />
      )}

      {/* Overstock Analysis Tab */}
      {activeTab === 'overstock' && (
        <OverstockAnalysisWidget />
      )}

      {/* Rebalance: inter-store transfer suggestions + stock accountability */}
      {activeTab === 'rebalance' && (
        <TransferRecommendationsWidget />
      )}

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
          currentBarcode={(selectedProduct as any).barcode}
          price={selectedProduct.offerPrice || selectedProduct.mrp}
          onSave={handleSaveBarcode}
        />
      )}

      {/* Stock Transfer Modal */}
      <StockTransferModal
        isOpen={showTransferModal}
        onClose={() => setShowTransferModal(false)}
        onTransferCreated={() => {
          setShowTransferModal(false);
          if (activeTab === 'transfers') {
            setActiveTab('transfers');
          }
        }}
      />

      {/* CSV Import Modal */}
      {showCSVImport && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-white border border-gray-200 rounded-xl w-full max-w-2xl max-h-[80vh] overflow-hidden flex flex-col">
            <div className="flex items-center justify-between p-5 border-b border-gray-200">
              <div className="flex items-center gap-3">
                <Upload className="w-5 h-5 text-blue-600" />
                <div>
                  <h2 className="text-lg font-semibold text-gray-900">Bulk CSV Product Import</h2>
                  <p className="text-sm text-gray-500">Upload a CSV file with product data</p>
                </div>
              </div>
              <button onClick={() => { setShowCSVImport(false); setCsvFile(null); setCsvPreview([]); setCsvRows([]); }} className="text-gray-500 hover:text-gray-900">
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
                  className="px-6 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2 hover:bg-blue-700 transition-colors"
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
    </div>
  );
}

export default InventoryPage;
