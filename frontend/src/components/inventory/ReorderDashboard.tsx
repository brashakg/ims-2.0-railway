// ============================================================================
// IMS 2.0 - Reorder Dashboard
// ============================================================================
// Monitor products requiring reorder and generate purchase orders

import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { CostCell } from '../common/CostCell';
import {
  TrendingDown,
  AlertTriangle,
  Package,
  ShoppingCart,
  Loader2,
  Settings,
  FileText,
  Calendar,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { inventoryApi, vendorsApi, reorderApi } from '../../services/api/inventory';
import { typedLevel } from '../../utils/reorderLevel';
import { REORDER_LEVEL_ROLES } from '../../pages/inventory/inventoryRoles';
import { ReorderPointModal, type ReorderPointData } from './ReorderPointModal';

const PICK_A_SHOP = 'Pick a shop first - a reorder level belongs to one shop.';

type StockStatus = 'out-of-stock' | 'critical' | 'low' | 'healthy';

// Every row of the low-stock feed IS low (the server's list); the band says how.
const toStatus = (v: unknown): StockStatus =>
  v === 'out-of-stock' || v === 'critical' || v === 'healthy' ? v : 'low';

interface Product {
  id: string;
  sku: string;
  name: string;
  brand: string;
  category: string;
  currentStock: number;
  reservedStock: number;
  // This shop's level; null = not set (no low-stock alert, shown as 'not set').
  reorderPoint: number | null;
  // The SERVER's verdict for this shop (reorder_policy.stock_status) - this
  // screen never decides low / critical itself.
  status: StockStatus;
  // Real reorder_quantity from the product master. null = never configured
  // (legacy-enabled, nothing to show). <= 0 (the -1 sentinel) = the owner
  // explicitly DISABLED auto-reorder for this product.
  reorderQuantity: number | null;
  autoReorderDisabled: boolean;
  maxStock: number;
  leadTimeDays: number;
  averageSalesPerDay: number;
  lastOrderDate?: string;
  supplierId?: string;
  supplierName?: string;
  unitCost?: number;
}

// Auto-reorder is OFF when the product master says so explicitly (value
// present and <= 0, i.e. the -1 sentinel) or the low-stock feed flagged it.
const isAutoReorderOff = (p: Product) => p.autoReorderDisabled;

// A row that auto-reorder can actually order: enabled AND a real qty >= 1.
const hasOrderableQty = (p: Product) =>
  !isAutoReorderOff(p) && p.reorderQuantity != null && p.reorderQuantity >= 1;

export function ReorderDashboard() {
  const { user, hasRole } = useAuth();
  // The reorder LEVEL is this shop's (owner ruling D12); the quantity / max /
  // lead time stay product-wide, edited only by the product-edit roles.
  const canEditProduct = hasRole(['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER']);
  // Who may set a shop's level (the server's gate, one shared list): a
  // catalogue manager edits the product-wide fields only.
  const canSetShopLevel = hasRole(REORDER_LEVEL_ROLES);
  const toast = useToast();
  const navigate = useNavigate();

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
    if (!user?.activeStoreId) return;
    setIsLoading(true);
    try {
      const storeId = user.activeStoreId;

      // Fetch low-stock items and full stock list in parallel
      const [lowStockData, stockData] = await Promise.all([
        inventoryApi.getLowStock(storeId).catch(() => ({ items: [] })),
        inventoryApi.getStock(storeId).catch(() => ({ items: [] })),
      ]);

      // getLowStock returns { items: [{ _id: productId, quantity, reorder_point, auto_reorder_disabled }] }
      // -- only products with a SET level at or under it (reorder_policy).
      const lowStockItems: Array<{ _id: string; quantity: number; reorder_point: number; stock_status?: string; auto_reorder_disabled?: boolean }> =
        Array.isArray(lowStockData) ? lowStockData : lowStockData?.items ?? [];

      // getStock returns { items: [...stock unit docs] }
      const stockUnits: Array<Record<string, any>> =
        Array.isArray(stockData) ? stockData : stockData?.items ?? [];

      // Build a map of product_id -> aggregated counts from stock units
      const stockByProduct = new Map<string, { available: number; reserved: number; raw: Record<string, any> }>();
      for (const unit of stockUnits) {
        const pid: string = unit.product_id ?? unit._id ?? '';
        if (!pid) continue;
        const existing = stockByProduct.get(pid) ?? { available: 0, reserved: 0, raw: unit };
        const qty = Number(unit.quantity ?? 1);
        if (unit.status === 'RESERVED' || unit.is_reserved) {
          existing.reserved += qty;
        } else {
          existing.available += qty;
        }
        stockByProduct.set(pid, existing);
      }

      // Combine low-stock items with stock unit details
      // Use low-stock list as the primary source of "products needing reorder"
      const mapped: Product[] = lowStockItems.map((item) => {
        const pid = item._id ?? '';
        const stockEntry = stockByProduct.get(pid);
        const raw = stockEntry?.raw ?? {};

        // The server's on-hand count (reorder_policy), never a client re-sum.
        const currentStock = Number(item.quantity ?? 0);
        const reservedStock = stockEntry?.reserved ?? 0;

        // REAL reorder_quantity only (ledger rows now pass it through from
        // the product master; null when never configured). NO fabricated
        // fallback -- a fake 20 here used to become a real PO line.
        const rawReorderQty = raw.reorder_quantity ?? raw.reorder_qty;
        const reorderQuantity =
          rawReorderQty == null || Number.isNaN(Number(rawReorderQty))
            ? null
            : Number(rawReorderQty);
        const autoReorderDisabled =
          (reorderQuantity != null && reorderQuantity <= 0) ||
          item.auto_reorder_disabled === true;

        return {
          id: pid,
          sku: raw.sku ?? raw.barcode ?? pid.slice(-8).toUpperCase(),
          name: raw.product_name ?? raw.name ?? raw.title ?? 'Unknown Product',
          brand: raw.brand ?? raw.brand_name ?? '',
          category: raw.category ?? '',
          currentStock,
          reservedStock,
          reorderPoint: typedLevel(item.reorder_point),
          status: toStatus(item.stock_status),
          reorderQuantity,
          autoReorderDisabled,
          maxStock: Number(raw.max_stock ?? raw.maximum_stock ?? 50),
          leadTimeDays: Number(raw.lead_time_days ?? raw.lead_time ?? 7),
          averageSalesPerDay: Number(raw.average_sales_per_day ?? raw.avg_daily_sales ?? 0),
          lastOrderDate: raw.last_order_date ?? raw.last_purchase_date ?? undefined,
          supplierId: raw.supplier_id ?? raw.vendor_id ?? undefined,
          supplierName: raw.supplier_name ?? raw.vendor_name ?? undefined,
          // The ledger row's cost_price, present only for the product-cost roles
          // (backend cost_mask). Never the MRP: that priced the estimate AND the
          // generated PO lines at retail, and a PO rate becomes the product's cost.
          unitCost: raw.cost_price ?? undefined,
        };
      });

      setProducts(mapped);
    } catch (error: any) {
      toast.error('Failed to load products');
    } finally {
      setIsLoading(false);
    }
  };

  const handleSaveReorderPoint = async (data: ReorderPointData) => {
    // Two independent writes, each only for the roles the server lets make it:
    // this shop's level, and the product-wide fields. One failing never blocks
    // or hides the other.
    const failures: string[] = [];
    let levelSaved = false;
    let productSaved = false;
    // The level is sent only when it changed (a product-wide save with the level
    // untouched never needs a shop) and only for a shop.
    const levelChanged =
      data.reorderPoint !== (products.find(p => p.id === data.productId)?.reorderPoint ?? null);
    if (canSetShopLevel && levelChanged) {
      if (!user?.activeStoreId) {
        // Never send an empty shop (the server answers 422).
        failures.push(PICK_A_SHOP);
      } else {
        try {
          await reorderApi.setShopLevel(data.productId, user.activeStoreId, data.reorderPoint);
          levelSaved = true;
        } catch (error: any) {
          failures.push(error?.message || 'Could not save the reorder level');
        }
      }
    }
    if (canEditProduct) {
      try {
        await reorderApi.updateReorderSettings(data.productId, {
          reorder_quantity: data.reorderQuantity,
          max_stock: data.maxStock,
          lead_time_days: data.leadTimeDays,
        });
        productSaved = true;
      } catch (error: any) {
        failures.push(error?.message || 'Could not save the product settings');
      }
    }

    // Update local state to reflect what actually saved
    if (levelSaved || productSaved) {
      setProducts(products.map(p =>
        p.id === data.productId
          ? {
              ...p,
                            ...(productSaved
                ? {
                    reorderQuantity: data.reorderQuantity,
                    autoReorderDisabled: data.reorderQuantity <= 0,
                    maxStock: data.maxStock,
                    leadTimeDays: data.leadTimeDays,
                  }
                : {}),
            }
          : p
      ));
    }

    // The verdict (low / critical) is the server's: re-read it after a level change.
    if (levelSaved) void loadProducts();

    if (failures.length > 0) {
      throw new Error(failures.join('; '));
    }
    toast.success('Reorder point updated successfully');
  };

  const handleGeneratePO = async () => {
    if (selectedProducts.size === 0) {
      toast.error('Please select at least one product');
      return;
    }

    const selectedItems = products.filter(p => selectedProducts.has(p.id));

    // NEVER order a product the owner explicitly opted out of (-1 sentinel).
    const disabledItems = selectedItems.filter(isAutoReorderOff);
    if (disabledItems.length > 0) {
      toast.error(
        `${disabledItems.length} product(s) skipped - auto-reorder is turned off for them. ` +
        `Enable it via the settings icon to order.`
      );
    }

    // A row with no configured order quantity has nothing honest to order
    // (we no longer fabricate a 20). Ask the user to set one first.
    const noQtyItems = selectedItems.filter(p => !isAutoReorderOff(p) && !hasOrderableQty(p));
    if (noQtyItems.length > 0) {
      toast.error(
        `${noQtyItems.length} product(s) have no order quantity configured. ` +
        `Set one via the settings icon first.`
      );
    }

    const orderableItems = selectedItems.filter(hasOrderableQty);
    if (orderableItems.length === 0) return;

    // Separate products with and without a known supplier
    const withSupplier = orderableItems.filter(p => p.supplierId);
    const withoutSupplier = orderableItems.filter(p => !p.supplierId);

    if (withoutSupplier.length > 0) {
      toast.error(
        `${withoutSupplier.length} product(s) have no supplier assigned. Assign a vendor first.`
      );
      if (withSupplier.length === 0) return;
    }

    if (withSupplier.length === 0) {
      // Nothing to create — navigate to purchase orders so user can create manually
      navigate('/purchase/orders');
      return;
    }

    try {
      // Group by supplier, create one PO per supplier
      const bySupplier = new Map<string, typeof withSupplier>();
      for (const item of withSupplier) {
        const sid = item.supplierId!;
        if (!bySupplier.has(sid)) bySupplier.set(sid, []);
        bySupplier.get(sid)!.push(item);
      }

      const storeId = user?.activeStoreId ?? '';
      let createdCount = 0;

      for (const [vendorId, items] of bySupplier.entries()) {
        await vendorsApi.createPurchaseOrder({
          vendor_id: vendorId,
          delivery_store_id: storeId,
          items: items.map(p => ({
            product_id: p.id,
            product_name: p.name,
            sku: p.sku,
            // Safe: only hasOrderableQty rows reach here (real qty >= 1).
            quantity: p.reorderQuantity as number,
            unit_price: p.unitCost ?? 0,
          })),
          notes: `Auto-generated from Reorder Dashboard`,
        });
        createdCount++;
      }

      const totalCost = withSupplier.reduce(
        (sum, p) => sum + ((p.unitCost ?? 0) * (p.reorderQuantity ?? 0)),
        0
      );

      toast.success(
        `${createdCount} Purchase Order(s) created for ${withSupplier.length} product(s)` +
        (totalCost > 0 ? ` (Est. \u20B9${totalCost.toLocaleString('en-IN')})` : '')
      );

      setSelectedProducts(new Set());
      navigate('/purchase/orders');
    } catch (error: any) {
      toast.error(error?.message || 'Failed to generate purchase order');
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

  const getStockStatus = (product: Product): StockStatus => product.status;

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
  // Only rows auto-reorder can actually order contribute to the estimate.
  const totalValue = filteredProducts.reduce(
    (sum, p) => sum + (hasOrderableQty(p) ? (p.unitCost || 0) * (p.reorderQuantity ?? 0) : 0),
    0
  );

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
                <CostCell value={totalValue} />
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
            <span className="text-gray-700">|</span>
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
                          <p className="text-xs text-gray-500">{product.brand}</p>
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
                        {product.reorderPoint ?? 'not set'}
                      </td>
                      <td className="px-4 py-3 text-center">
                        {isAutoReorderOff(product) ? (
                          // Owner explicitly disabled auto-reorder (-1 sentinel).
                          <span className="px-2 py-1 bg-gray-100 text-gray-500 text-xs font-medium rounded-full whitespace-nowrap">
                            Auto-reorder off
                          </span>
                        ) : product.reorderQuantity == null ? (
                          // Never configured - show an honest dash, not a fake 20.
                          <span className="text-gray-400">&mdash;</span>
                        ) : (
                          <>
                            <span className="font-medium text-purple-600">{product.reorderQuantity}</span>
                            {product.unitCost && (
                              <p className="text-xs text-gray-500">
                                {/* F35: cost masked to "-" for non-cost-visible roles */}
                                <CostCell value={product.unitCost * product.reorderQuantity} />
                              </p>
                            )}
                          </>
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
                          <p className="text-sm text-gray-900">{product.supplierName ?? <span className="text-gray-500 italic">None assigned</span>}</p>
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
            stockStatus: selectedProduct.status,
            reorderQuantity: selectedProduct.reorderQuantity,
            maxStock: selectedProduct.maxStock,
            averageSalesPerDay: selectedProduct.averageSalesPerDay,
            leadTimeDays: selectedProduct.leadTimeDays,
          }}
          onSave={handleSaveReorderPoint}
          productWideLocked={!canEditProduct}
          shopLevelLocked={!canSetShopLevel || !user?.activeStoreId}
          shopLevelNotice={canSetShopLevel && !user?.activeStoreId ? PICK_A_SHOP : undefined}
        />
      )}
    </div>
  );
}
