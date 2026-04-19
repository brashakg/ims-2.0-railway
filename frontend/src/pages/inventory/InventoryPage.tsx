// ============================================================================
// IMS 2.0 - Inventory Page
// ============================================================================
// NO MOCK DATA - All data from API

import { useState, useEffect } from 'react';
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
} from 'lucide-react';
import type { ProductCategory } from '../../types';
import { inventoryApi, adminProductApi } from '../../services/api';
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
import { ContactLensExpiryWidget, LensPowerGridWidget, SellThroughAnalysisWidget, OverstockAnalysisWidget } from '../../components/inventory/AdvancedInventoryFeatures';
import { Pagination } from '../../components/common/Pagination';
import clsx from 'clsx';

// Category configuration
const CATEGORIES: { code: ProductCategory; label: string; icon: string }[] = [
  { code: 'FR', label: 'Frames', icon: '👓' },
  { code: 'SG', label: 'Sunglasses', icon: '🕶️' },
  { code: 'RG', label: 'Reading Glasses', icon: '📖' },
  { code: 'LS', label: 'Optical Lenses', icon: '🔍' },
  { code: 'CL', label: 'Contact Lenses', icon: '👁️' },
  { code: 'WT', label: 'Watches', icon: '⌚' },
  { code: 'SMTWT', label: 'Smartwatches', icon: '📱' },
  { code: 'SMTSG', label: 'Smart Sunglasses', icon: '🥽' },
  { code: 'SMTFR', label: 'Smart Frames', icon: '🤓' },
  { code: 'CK', label: 'Wall Clocks', icon: '🕐' },
  { code: 'ACC', label: 'Accessories', icon: '🧴' },
  { code: 'HA', label: 'Hearing Aids', icon: '👂' },
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

type ViewTab = 'alerts' | 'catalog' | 'low-stock' | 'reorders' | 'serial-numbers' | 'aging' | 'transfers' | 'movements' | 'non-moving' | 'stock-count' | 'contact-lens' | 'power-grid' | 'sell-through' | 'overstock';

export function InventoryPage() {
  const { user, hasRole } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  // Data state
  const [inventory, setInventory] = useState<StockItem[]>([]);
  const [lowStockItems, setLowStockItems] = useState<StockItem[]>([]);
  const [movements, setMovements] = useState<StockMovement[]>([]);
  const [movementFilter, setMovementFilter] = useState<StockMovement['type'] | 'ALL'>('ALL');
  const [movementSearch, setMovementSearch] = useState('');

  // UI state
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<ProductCategory | null>(null);
  const [activeTab, setActiveTab] = useState<ViewTab>('catalog');

  // Pagination state
  const [currentPage, setCurrentPage] = useState(1);
  const pageSize = 50;

  // Sync active tab from URL query params (e.g. /inventory?tab=transfers)
  useEffect(() => {
    const tabParam = searchParams.get('tab');
    if (tabParam && tabParam !== activeTab) {
      const validTabs: ViewTab[] = ['alerts', 'catalog', 'low-stock', 'reorders', 'serial-numbers', 'aging', 'transfers', 'movements', 'non-moving', 'stock-count', 'contact-lens', 'power-grid', 'sell-through', 'overstock'];
      if (validTabs.includes(tabParam as ViewTab)) {
        setActiveTab(tabParam as ViewTab);
      }
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
  const [csvPreview, setCsvPreview] = useState<Array<Record<string, string>>>([]);
  const [isImporting, setIsImporting] = useState(false);


  // Role-based permissions
  const canTransfer = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER']);
  const canAddProduct = hasRole(['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER']);
  const canExport = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']);
  const canManageBarcode = hasRole(['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER', 'STORE_MANAGER']);

  // Load data on mount
  useEffect(() => {
    loadInventory();
  }, [user?.activeStoreId]);

  const loadInventory = async () => {
    if (!user?.activeStoreId) return;

    setIsLoading(true);
    setError(null);

    try {
      // Fetch inventory and low stock in parallel
      const [stockData, lowStockData] = await Promise.all([
        inventoryApi.getStock(user.activeStoreId).catch(() => ({ items: [] })),
        inventoryApi.getLowStock(user.activeStoreId).catch(() => ({ items: [] })),
      ]);

      // Process stock data
      const items = stockData?.items || stockData || [];
      setInventory(Array.isArray(items) ? items.map((item: StockItem) => ({
        ...item,
        name: item.name || item.productName || 'Unknown Product',
        stock: item.stock || item.quantity || 0,
        lowStockThreshold: item.lowStockThreshold || item.minStock || 5,
        reserved: item.reserved || 0,
      })) : []);

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
  }, [searchQuery, selectedCategory]);

  // Filter inventory locally
  const filteredInventory = inventory.filter(item => {
    const matchesSearch = !searchQuery ||
      item.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      item.sku?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      item.brand?.toLowerCase().includes(searchQuery.toLowerCase());

    const matchesCategory = !selectedCategory || item.category === selectedCategory;

    return matchesSearch && matchesCategory;
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

  const formatCurrency = (amount: number) => {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 0,
    }).format(amount);
  };

  // Handle CSV import
  const handleImportProducts = async () => {
    if (!csvFile) {
      toast.error('Please select a CSV file first');
      return;
    }
    // Detect category from parsed preview rows if possible
    const detectedCategory = csvPreview.length > 0 ? (csvPreview[0].category || 'FR') : 'FR';
    setIsImporting(true);
    try {
      const result = await adminProductApi.bulkImportProducts(csvFile, detectedCategory);
      const count = result?.imported ?? result?.count ?? csvPreview.length;
      toast.success(`Successfully imported ${count} product${count === 1 ? '' : 's'}`);
      setShowCSVImport(false);
      setCsvFile(null);
      setCsvPreview([]);
      await loadInventory();
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
      await adminProductApi.updateProduct(selectedProduct.id, { barcode });
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
    { id: 'low-stock',      label: 'Low stock',       icon: AlertTriangle, count: lowStockCount },
    { id: 'reorders',       label: 'Reorders',        icon: ShoppingCart },
    { id: 'serial-numbers', label: 'Serial numbers',  icon: Hash },
    { id: 'aging',          label: 'Stock aging',     icon: Clock },
    { id: 'transfers',      label: 'Transfers',       icon: ArrowRightLeft },
    { id: 'movements',      label: 'Movements',       icon: Eye },
    { id: 'non-moving',     label: 'Non-moving',      icon: TrendingDown },
    { id: 'stock-count',    label: 'Stock count',     icon: Barcode },
    { id: 'contact-lens',   label: 'CL expiry',       icon: Eye },
    { id: 'power-grid',     label: 'Lens power grid', icon: BarChart3 },
    { id: 'sell-through',   label: 'Sell-through',    icon: TrendingDown },
    { id: 'overstock',      label: 'Overstock',       icon: Boxes },
  ];

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
              <button onClick={() => navigate('/settings?tab=products')} className="btn sm primary">
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

      {/* 5-cell stat strip */}
      <div className="stat-strip">
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

      {/* Tabs — underline style, mono count */}
      <div className="inv-tabs">
        {tabList.map(tab => {
          const TabIcon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={activeTab === tab.id ? 'on' : ''}
            >
              <TabIcon className="w-4 h-4" />
              {tab.label}
              {typeof tab.count === 'number' && <span className="count">· {tab.count}</span>}
            </button>
          );
        })}
      </div>

      {/* Search and Filters */}
      {activeTab !== 'alerts' && activeTab !== 'transfers' && activeTab !== 'reorders' && activeTab !== 'serial-numbers' && activeTab !== 'aging' && (
      <div className="card">
        <div className="flex flex-col tablet:flex-row gap-4 mb-4">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
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
          {CATEGORIES.map(cat => (
            <button
              key={cat.code}
              onClick={() => setSelectedCategory(cat.code)}
              className={clsx(
                'px-3 py-1.5 rounded-full text-sm font-medium whitespace-nowrap transition-colors flex items-center gap-1',
                selectedCategory === cat.code
                  ? 'bg-bv-red-600 text-white'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              )}
            >
              <span>{cat.icon}</span>
              <span>{cat.label}</span>
            </button>
          ))}
        </div>
      </div>
      )}

      {/* Stock Alerts Tab */}
      {activeTab === 'alerts' && (
        <div className="card">
          <StockAlertsOverview />
        </div>
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
              <p>{searchQuery || selectedCategory ? 'No products found matching your filters' : 'No products in inventory'}</p>
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
                    <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Stock</th>
                    <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Location</th>
                    <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Status</th>
                    <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {paginatedInventory.map(item => {
                    const status = getStockStatus(item);
                    const category = CATEGORIES.find(c => c.code === item.category);
                    return (
                      <tr key={item.id} className="hover:bg-gray-50">
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
                            <span className="text-xs text-gray-400">Not set</span>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          <span className="text-sm">
                            {category?.icon || '📦'}{' '}
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
                        <td className="px-4 py-3 text-center text-sm text-gray-600">{item.location || '-'}</td>
                        <td className="px-4 py-3 text-center">
                          <span className={status.class}>{status.label}</span>
                        </td>
                        <td className="px-4 py-3 text-center">
                          <div className="flex items-center justify-center gap-1">
                            {canManageBarcode && (
                              <button
                                onClick={() => openBarcodeModal(item)}
                                className="p-2 text-gray-400 hover:text-blue-600 transition-colors"
                                title="Manage Barcode"
                              >
                                <Barcode className="w-4 h-4" />
                              </button>
                            )}
                            <button
                              onClick={() => toast.info(`View details for ${item.name}`)}
                              className="p-2 text-gray-400 hover:text-bv-red-600 transition-colors"
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
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
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
                          <div className="text-xs text-gray-400">
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

      {/* Contact Lens Expiry Tab */}
      {activeTab === 'contact-lens' && (
        <ContactLensExpiryWidget />
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
              <button onClick={() => { setShowCSVImport(false); setCsvFile(null); setCsvPreview([]); }} className="text-gray-500 hover:text-gray-900">
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
                      setCsvPreview(rows.slice(0, 10)); // Preview first 10
                      toast.success(`Parsed ${rows.length} product${rows.length === 1 ? '' : 's'} from CSV`);
                    };
                    reader.readAsText(file);
                  }}
                />
                <label htmlFor="csv-upload" className="cursor-pointer">
                  <Upload className="w-8 h-8 text-gray-400 mx-auto mb-2" />
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
                {csvFile ? `${csvPreview.length}+ products ready to import` : 'Select a CSV file to begin'}
              </p>
              <div className="flex gap-2">
                <button onClick={() => { setShowCSVImport(false); setCsvFile(null); setCsvPreview([]); }} className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg text-sm hover:bg-gray-200">
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
