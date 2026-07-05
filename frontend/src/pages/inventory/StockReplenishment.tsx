// ============================================================================
// IMS 2.0 - Stock Replenishment
// ============================================================================
// Auto-replenishment dashboard + dead-stock review + Create-PO into the real
// vendor PO pipeline. EOQ / ABC-XYZ remain FORMULA REFERENCES only — the
// backend does not yet compute per-SKU EOQ/ABC, so we never fabricate those
// numbers (Fail Loudly: show a real value or an honest dash).

import { useState, useEffect } from 'react';
import { Plus, TrendingDown, Zap, AlertTriangle, Loader2, X } from 'lucide-react';
import clsx from 'clsx';
import { inventoryApi, vendorsApi, reportsApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

interface ReplenishmentItem {
  product_id: string;
  product_name: string;
  sku: string;
  current_stock: number;
  reorder_level: number;
  // Real reorder gap (reorder_level - current_stock, floored at 1) — used as
  // the default PO quantity. NOT a fabricated EOQ.
  reorder_qty: number;
  preferred_vendor_id: string;
  preferred_vendor_name: string | null;
  estimated_cost: number;
  last_purchase_price: number;
  stock_status: 'critical' | 'low' | 'normal' | 'excess';
}

interface DeadStockItem {
  product_id: string | null;
  product_name: string;
  sku: string | null;
  current_stock: number;
  last_sold_at: string | null;
  days_since_sold: number | null;
  never_sold: boolean;
  estimated_value: number;
}

interface VendorOption {
  id: string;
  name: string;
}


const getStockStatusColor = (status: string) => {
  switch (status) {
    case 'critical':
      return 'bg-red-50 text-red-700';
    case 'low':
      return 'bg-orange-50 text-orange-700';
    case 'normal':
      return 'bg-green-50 text-green-700';
    case 'excess':
      return 'bg-blue-50 text-blue-700';
    default:
      return 'bg-gray-100 text-gray-700';
  }
};

export function StockReplenishment() {
  const { user } = useAuth();
  const toast = useToast();
  const [activeTab, setActiveTab] = useState<'suggestions' | 'abc-analysis' | 'dead-stock' | 'eoq'>('suggestions');
  const [selectedItems, setSelectedItems] = useState<string[]>([]);
  const [suggestions, setSuggestions] = useState<ReplenishmentItem[]>([]);
  // Low-stock products the owner explicitly opted OUT of auto-reorder
  // (reorder_quantity = -1) — dropped from suggestions, counted for honesty.
  const [autoReorderOffCount, setAutoReorderOffCount] = useState(0);
  const [deadStock, setDeadStock] = useState<DeadStockItem[]>([]);
  const [deadStockLoading, setDeadStockLoading] = useState(false);
  const [, setIsLoading] = useState(true);

  // Create-PO modal: holds the items the user wants to order. Empty = closed.
  const [poItems, setPoItems] = useState<ReplenishmentItem[]>([]);

  // Load replenishment suggestions on mount
  useEffect(() => {
    const loadSuggestions = async () => {
      try {
        setIsLoading(true);
        const storeId = user?.activeStoreId || '';
        const response = await inventoryApi.getLowStock(storeId);
        const raw = response?.items ?? (Array.isArray(response) ? response : response.data || []);
        // The low-stock feed flags products whose auto-reorder is explicitly
        // OFF (product master reorder_quantity <= 0, the -1 sentinel). Those
        // must never be SUGGESTED for a PO — drop them here, count them for
        // the honest note below.
        const feedRows = raw as Array<any & { auto_reorder_disabled?: boolean }>;
        const skippedOff = feedRows.filter((item) => item.auto_reorder_disabled === true);
        setAutoReorderOffCount(skippedOff.length);
        const transformedSuggestions: ReplenishmentItem[] = feedRows
          .filter((item) => item.auto_reorder_disabled !== true)
          .map((item: any) => {
          const currentStock = item.current_stock ?? item.stock ?? item.quantity ?? 0;
          const reorderLevel = item.reorder_level ?? item.lowStockThreshold ?? item.minStock ?? item.min_stock ?? 0;
          // Real gap to refill back up to the reorder point; at least 1.
          const reorderQty = Math.max(1, reorderLevel - currentStock);
          return {
            product_id: item.product_id || item.id || '',
            product_name: item.product_name || item.name || 'Unknown Product',
            sku: item.sku || '',
            current_stock: currentStock,
            reorder_level: reorderLevel,
            reorder_qty: reorderQty,
            preferred_vendor_id: item.preferred_vendor_id || '',
            // Only a REAL vendor name — never a fabricated "Unknown Vendor".
            preferred_vendor_name: item.preferred_vendor_name || null,
            estimated_cost: item.estimated_cost || (item.last_purchase_price || 0) * reorderQty,
            last_purchase_price: item.last_purchase_price || 0,
            stock_status: item.stock_status || (currentStock === 0 ? 'critical' : 'low'),
          };
        });
        setSuggestions(transformedSuggestions);
      } catch (error) {
        toast.error('Failed to load replenishment suggestions');
      } finally {
        setIsLoading(false);
      }
    };

    loadSuggestions();
  }, [user?.activeStoreId]);

  // Load dead stock when the Dead-Stock tab is first opened (or store changes).
  // Backed by the REAL non-moving-stock report (cash tied up in shelves) —
  // "no sales in N days". The tab copy says 6+ months, so we ask for 180 days.
  useEffect(() => {
    if (activeTab !== 'dead-stock') return;
    let cancelled = false;
    const loadDeadStock = async () => {
      setDeadStockLoading(true);
      try {
        const storeId = user?.activeStoreId || undefined;
        const res = await reportsApi.getNonMovingStock(storeId, 180, 200);
        if (cancelled) return;
        const rows: DeadStockItem[] = (res?.data || []).map((r) => ({
          product_id: r.product_id,
          product_name: [r.brand, r.model].filter(Boolean).join(' ') || r.sku || 'Unknown Product',
          sku: r.sku,
          current_stock: 0, // report does not carry on-hand; left out of display
          last_sold_at: r.last_sold_at,
          days_since_sold: r.days_since_sold,
          never_sold: r.never_sold,
          estimated_value: r.mrp || 0,
        }));
        setDeadStock(rows);
      } catch (error) {
        if (!cancelled) toast.error('Failed to load dead-stock report');
      } finally {
        if (!cancelled) setDeadStockLoading(false);
      }
    };
    loadDeadStock();
    return () => { cancelled = true; };
  }, [activeTab, user?.activeStoreId]);

  const toggleSelection = (productId: string) => {
    setSelectedItems(prev =>
      prev.includes(productId) ? prev.filter(id => id !== productId) : [...prev, productId]
    );
  };

  const selectedSuggestions = suggestions.filter(s => selectedItems.includes(s.product_id));
  const totalEstimatedCost = selectedSuggestions.reduce((sum, s) => sum + s.estimated_cost, 0);

  const criticalItems = suggestions.filter(s => s.stock_status === 'critical');
  const lowItems = suggestions.filter(s => s.stock_status === 'low');

  // Open the Create-PO modal for a set of replenishment lines.
  const openCreatePO = (items: ReplenishmentItem[]) => {
    if (items.length === 0) return;
    setPoItems(items);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Stock Replenishment</h1>
          <p className="text-gray-500">Auto-replenishment suggestions and inventory optimization</p>
        </div>
        {selectedItems.length > 0 && (
          <button
            onClick={() => openCreatePO(selectedSuggestions)}
            className="px-4 py-2 bg-bv-red-600 hover:bg-bv-red-700 text-white rounded-lg font-semibold flex items-center gap-2"
          >
            <Plus className="w-5 h-5" />
            Create PO for {selectedItems.length} items
          </button>
        )}
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-4 gap-4">
        <div className="bg-white rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">Critical Items</p>
          <p className="text-2xl font-bold text-red-600">{criticalItems.length}</p>
        </div>
        <div className="bg-white rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">Low Stock Items</p>
          <p className="text-2xl font-bold text-orange-600">{lowItems.length}</p>
        </div>
        <div className="bg-white rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">Dead Stock Items</p>
          <p className="text-2xl font-bold text-purple-600">{deadStock.length}</p>
        </div>
        <div className="bg-white rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">Est. Replenish Cost</p>
          <p className="text-2xl font-bold text-green-600">₹{(totalEstimatedCost / 100000).toFixed(1)}L</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-200">
        {(['suggestions', 'abc-analysis', 'dead-stock', 'eoq'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={clsx(
              'px-4 py-3 font-medium border-b-2 transition-colors',
              activeTab === tab
                ? 'border-blue-500 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            )}
          >
            {tab === 'suggestions' ? 'Suggestions' : tab === 'abc-analysis' ? 'ABC/XYZ Analysis' : tab === 'dead-stock' ? 'Dead Stock' : 'EOQ Calculation'}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {activeTab === 'suggestions' && (
        <div className="space-y-4">
          {autoReorderOffCount > 0 && (
            <p className="text-sm text-gray-500">
              {autoReorderOffCount} product{autoReorderOffCount === 1 ? '' : 's'} skipped (auto-reorder off)
            </p>
          )}
          {criticalItems.length > 0 && (
            <div className="bg-red-50 border border-red-700 rounded-lg p-4 flex items-start gap-3">
              <AlertTriangle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
              <div>
                <p className="text-red-700 font-semibold">{criticalItems.length} Critical Items</p>
                <p className="text-red-700 text-sm">These items are below critical stock level and require immediate replenishment.</p>
              </div>
            </div>
          )}

          {suggestions.length === 0 ? (
            <div className="bg-white rounded-lg border border-gray-200 p-10 text-center text-gray-500">
              <AlertTriangle className="w-10 h-10 mx-auto mb-3 text-gray-300" />
              <p className="font-medium text-gray-700">No replenishment suggestions</p>
              <p className="text-sm mt-1">Every SKU is above its reorder point for this store.</p>
            </div>
          ) : (
          <div className="space-y-3">
            {suggestions.map((item) => (
              <div
                key={item.product_id}
                className={clsx(
                  'rounded-lg p-4 border transition-colors cursor-pointer',
                  selectedItems.includes(item.product_id)
                    ? 'bg-blue-50 border-blue-600'
                    : 'bg-white border-gray-200 hover:border-gray-300'
                )}
                onClick={() => toggleSelection(item.product_id)}
              >
                <div className="flex items-start gap-4">
                  <input
                    type="checkbox"
                    checked={selectedItems.includes(item.product_id)}
                    onChange={() => toggleSelection(item.product_id)}
                    className="w-5 h-5 rounded border-gray-500 mt-1"
                  />

                  <div className="flex-1">
                    <div className="flex items-start justify-between mb-3">
                      <div>
                        <p className="text-gray-900 font-semibold">{item.product_name}</p>
                        <p className="text-gray-500 text-sm">{item.sku ? `SKU: ${item.sku}` : `Product ID: ${item.product_id}`}</p>
                      </div>
                      <span className={clsx('px-2 py-1 rounded text-xs font-semibold', getStockStatusColor(item.stock_status))}>
                        {item.stock_status.charAt(0).toUpperCase() + item.stock_status.slice(1)}
                      </span>
                    </div>

                    {/* Only REAL fields are shown. EOQ / ABC-XYZ are not
                        computed by the backend, so they are intentionally
                        omitted rather than fabricated. */}
                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-3 pb-3 border-b border-gray-200">
                      <div>
                        <p className="text-gray-500 text-xs mb-1">Current Stock</p>
                        <p className="text-gray-900 font-semibold">{item.current_stock}</p>
                      </div>
                      <div>
                        <p className="text-gray-500 text-xs mb-1">Reorder Level</p>
                        <p className="text-gray-900 font-semibold">{item.reorder_level}</p>
                      </div>
                      <div>
                        <p className="text-gray-500 text-xs mb-1">Suggested Qty</p>
                        <p className="text-gray-900 font-semibold">{item.reorder_qty}</p>
                      </div>
                      <div>
                        <p className="text-gray-500 text-xs mb-1">Est. Cost</p>
                        <p className="text-green-600 font-semibold">
                          {item.estimated_cost > 0 ? `₹${(item.estimated_cost / 1000).toFixed(0)}K` : '—'}
                        </p>
                      </div>
                    </div>

                    <div className="flex items-center justify-between">
                      <div>
                        {item.preferred_vendor_name && (
                          <p className="text-gray-500 text-sm">{item.preferred_vendor_name}</p>
                        )}
                        <p className="text-gray-500 text-xs">
                          {item.last_purchase_price > 0 ? `Last price: ₹${item.last_purchase_price}` : 'No purchase history'}
                        </p>
                      </div>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          openCreatePO([item]);
                        }}
                        className="px-3 py-1 bg-bv-red-600 hover:bg-bv-red-700 text-white text-sm rounded font-semibold"
                      >
                        Create PO
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
          )}
        </div>
      )}

      {activeTab === 'abc-analysis' && (
        <div className="space-y-4">
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-sm text-blue-800">
            ABC/XYZ classification is a planned analytics feature. The per-SKU
            value (ABC) and demand-variability (XYZ) buckets are not yet computed
            by the backend, so no item counts are shown here — only the
            methodology reference below.
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* ABC methodology */}
            <div className="lg:col-span-2 space-y-4">
              <h3 className="text-lg font-semibold text-gray-900 mb-4">ABC Classification</h3>
              {[
                { category: 'A', color: 'text-red-600', desc: 'High-value items (~80% value, ~20% of items)' },
                { category: 'B', color: 'text-yellow-600', desc: 'Medium-value items (~15% value, ~30% of items)' },
                { category: 'C', color: 'text-green-600', desc: 'Low-value items (~5% value, ~50% of items)' },
              ].map((cat) => (
                <div key={cat.category} className="rounded-lg p-4 border border-gray-200 bg-white">
                  <p className={clsx('font-semibold mb-1', cat.color)}>Category {cat.category}</p>
                  <p className="text-gray-500 text-sm">{cat.desc}</p>
                </div>
              ))}
            </div>

            {/* XYZ methodology */}
            <div className="space-y-4">
              <h3 className="text-lg font-semibold text-gray-900 mb-4">XYZ Classification</h3>
              {[
                { category: 'X', label: 'Predictable', desc: 'Stable demand' },
                { category: 'Y', label: 'Moderate', desc: 'Variable demand' },
                { category: 'Z', label: 'Unpredictable', desc: 'Uncertain demand' },
              ].map((cat) => (
                <div key={cat.category} className="bg-white rounded-lg p-4 border border-gray-200">
                  <p className="text-gray-900 font-semibold">{cat.category} - {cat.label}</p>
                  <p className="text-gray-500 text-xs mt-1">{cat.desc}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {activeTab === 'dead-stock' && (
        <div className="space-y-4">
          <div className="bg-purple-50/30 border border-purple-700 rounded-lg p-4 flex items-start gap-3">
            <TrendingDown className="w-5 h-5 text-purple-600 flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-purple-700 font-semibold">Dead Stock Identification</p>
              <p className="text-purple-700 text-sm">Items with no sales activity in the last 180 days</p>
            </div>
          </div>

          {deadStockLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 animate-spin text-purple-600" />
            </div>
          ) : deadStock.length === 0 ? (
            <div className="bg-white rounded-lg border border-gray-200 p-10 text-center text-gray-500">
              <TrendingDown className="w-10 h-10 mx-auto mb-3 text-gray-300" />
              <p className="font-medium text-gray-700">No dead stock</p>
              <p className="text-sm mt-1">Every active SKU has sold within the last 180 days.</p>
            </div>
          ) : (
            <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase">Product</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase">SKU</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase">Last Sold</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-gray-500 uppercase">Days Idle</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-gray-500 uppercase">MRP</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {deadStock.map((item, idx) => (
                      <tr key={item.product_id || item.sku || `dead-${idx}`} className="hover:bg-gray-50">
                        <td className="px-4 py-3">
                          <p className="text-gray-900 font-medium">{item.product_name}</p>
                          {item.never_sold && (
                            <span className="inline-block mt-0.5 px-2 py-0.5 rounded text-xs font-semibold bg-red-50 text-red-700">
                              Never sold
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-gray-600 font-mono text-xs">{item.sku || '—'}</td>
                        <td className="px-4 py-3 text-gray-600">
                          {item.last_sold_at ? new Date(item.last_sold_at).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : 'Never'}
                        </td>
                        <td className="px-4 py-3 text-right font-semibold text-purple-600">
                          {item.days_since_sold != null ? item.days_since_sold : '—'}
                        </td>
                        <td className="px-4 py-3 text-right text-gray-900">
                          {item.estimated_value > 0 ? `₹${item.estimated_value.toLocaleString('en-IN')}` : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {activeTab === 'eoq' && (
        <div className="space-y-4">
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-sm text-blue-800">
            Per-SKU Economic Order Quantity is a planned feature — it needs annual
            demand, ordering cost and holding cost inputs that the system does not
            track yet. The formula reference is shown below; the Suggestions tab
            uses the real reorder gap (reorder level minus current stock) as the
            order quantity in the meantime.
          </div>
          <div className="bg-white rounded-lg p-6 border border-gray-200 max-w-xl">
            <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
              <Zap className="w-5 h-5" />
              Economic Order Quantity (EOQ)
            </h3>
            <div className="space-y-4">
              <p className="text-gray-500 text-sm">EOQ = √(2DS/H) where:</p>
              <ul className="text-gray-700 text-sm space-y-2">
                <li><span className="text-blue-600">D</span> = Annual demand</li>
                <li><span className="text-blue-600">S</span> = Ordering cost per order</li>
                <li><span className="text-blue-600">H</span> = Holding cost per unit</li>
              </ul>
              <p className="text-gray-500 text-sm pt-4 border-t border-gray-200">
                EOQ minimises total inventory cost by balancing ordering costs against holding costs.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Create-PO modal — submits to the REAL vendor PO pipeline. */}
      {poItems.length > 0 && (
        <CreatePOModal
          items={poItems}
          onClose={() => setPoItems([])}
          onCreated={() => {
            setPoItems([]);
            setSelectedItems([]);
          }}
        />
      )}
    </div>
  );
}

// ============================================================================
// Create-PO modal — pick a vendor, confirm lines, submit a real PO.
// Reuses vendorsApi.createPurchaseOrder (same contract the Reorder Dashboard
// uses). Only real line fields are sent (product_id / name / sku / qty / unit
// price); nothing is fabricated.
// ============================================================================

function CreatePOModal({
  items, onClose, onCreated,
}: {
  items: ReplenishmentItem[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const { user } = useAuth();
  const toast = useToast();
  const [vendors, setVendors] = useState<VendorOption[]>([]);
  const [vendorId, setVendorId] = useState('');
  const [loadingVendors, setLoadingVendors] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    vendorsApi.getVendors({ is_active: true })
      .then((res: any) => {
        if (cancelled) return;
        const list: any[] = res?.vendors ?? (Array.isArray(res) ? res : res?.data ?? []);
        const opts = list.map((v) => ({
          id: String(v.vendor_id || v.id || v._id || ''),
          name: String(v.legal_name || v.trade_name || v.name || v.vendor_id || ''),
        })).filter((v) => v.id);
        setVendors(opts);
        // Pre-select the line's preferred vendor when present.
        const preferred = items.find((i) => i.preferred_vendor_id)?.preferred_vendor_id || '';
        if (preferred && opts.some((o) => o.id === preferred)) setVendorId(preferred);
      })
      .catch(() => { if (!cancelled) setVendors([]); })
      .finally(() => { if (!cancelled) setLoadingVendors(false); });
    return () => { cancelled = true; };
  }, []);

  const estTotal = items.reduce((sum, i) => sum + i.last_purchase_price * i.reorder_qty, 0);

  const submit = async () => {
    if (!vendorId) {
      toast.error('Select a vendor for this purchase order');
      return;
    }
    const storeId = user?.activeStoreId || '';
    if (!storeId) {
      toast.error('No active store — cannot create a purchase order');
      return;
    }
    setSubmitting(true);
    try {
      await vendorsApi.createPurchaseOrder({
        vendor_id: vendorId,
        delivery_store_id: storeId,
        items: items.map((i) => ({
          product_id: i.product_id,
          product_name: i.product_name,
          sku: i.sku,
          quantity: i.reorder_qty,
          unit_price: i.last_purchase_price,
        })),
        notes: 'Created from Stock Replenishment',
      });
      toast.success(
        `Purchase order created for ${items.length} item${items.length === 1 ? '' : 's'}` +
        (estTotal > 0 ? ` (est. ₹${estTotal.toLocaleString('en-IN')})` : '')
      );
      onCreated();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        || (err instanceof Error ? err.message : 'Failed to create purchase order');
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg" onClick={(e) => e.stopPropagation()}>
        <div className="px-5 py-4 border-b border-gray-200 flex items-center justify-between">
          <h2 className="font-semibold text-gray-900">Create Purchase Order</h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-700">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          {/* Vendor picker */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Vendor *</label>
            {loadingVendors ? (
              <div className="flex items-center gap-2 text-sm text-gray-500 py-2">
                <Loader2 className="w-4 h-4 animate-spin" /> Loading vendors…
              </div>
            ) : vendors.length === 0 ? (
              <p className="text-sm text-orange-600">
                No vendors found. Add a vendor under Supply Chain &rarr; Suppliers first.
              </p>
            ) : (
              <select
                value={vendorId}
                onChange={(e) => setVendorId(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm text-gray-900"
              >
                <option value="">Select a vendor…</option>
                {vendors.map((v) => (
                  <option key={v.id} value={v.id}>{v.name}</option>
                ))}
              </select>
            )}
          </div>

          {/* Line items */}
          <div>
            <p className="text-sm font-medium text-gray-700 mb-2">Items ({items.length})</p>
            <div className="border border-gray-200 rounded-lg divide-y divide-gray-100 max-h-60 overflow-y-auto">
              {items.map((i) => (
                <div key={i.product_id} className="flex items-center justify-between px-3 py-2 text-sm">
                  <div className="min-w-0">
                    <p className="text-gray-900 truncate">{i.product_name}</p>
                    <p className="text-gray-500 text-xs">{i.sku || i.product_id}</p>
                  </div>
                  <div className="text-right shrink-0 pl-3">
                    <p className="text-gray-900 font-medium">Qty {i.reorder_qty}</p>
                    <p className="text-gray-500 text-xs">
                      {i.last_purchase_price > 0 ? `@ ₹${i.last_purchase_price}` : 'price TBD'}
                    </p>
                  </div>
                </div>
              ))}
            </div>
            {estTotal > 0 && (
              <p className="text-right text-sm text-gray-600 mt-2">
                Est. total: <span className="font-semibold text-gray-900">₹{estTotal.toLocaleString('en-IN')}</span>
              </p>
            )}
          </div>
        </div>

        <div className="px-5 py-3 border-t border-gray-200 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 rounded-lg"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={submitting || loadingVendors || !vendorId}
            className="px-4 py-2 bg-bv-red-600 hover:bg-bv-red-700 text-white rounded-lg text-sm font-semibold flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
            Create PO
          </button>
        </div>
      </div>
    </div>
  );
}

export default StockReplenishment;
