// ============================================================================
// IMS 2.0 - Inventory > Movements  (/inventory/movements)
// ============================================================================
// The old InventoryPage `activeTab === 'movements'` block on its own URL: the
// signed stock-movement ledger (RECEIVED / SOLD / TRANSFER_IN / TRANSFER_OUT /
// OPENING_STOCK) from GET /inventory/movements, server-paged via ?skip= with
// a Load-more button and a server-side type filter.
//
// The fetch stays local useState (not inventoryQueries): it is load-more
// paged and this page is its only consumer, so a shared cache buys nothing.
// The old page's top search-and-filter card is gone from this section - its
// search box never filtered movements (this ledger has its own).

import { useEffect, useState } from 'react';
import { ArrowRightLeft, Loader2, Search } from 'lucide-react';
import clsx from 'clsx';
import { inventoryApi } from '../../services/api';
// Movements-ledger entry type comes DIRECT from the module (TS2614 barrel dodge).
import { type StockMovementEntry } from '../../services/api/inventory';
import { useInventoryContext } from './InventoryLayout';

type StockMovement = StockMovementEntry;

// Page size for the load-more paging (?skip=).
const MOVEMENTS_PAGE_SIZE = 50;

export function InventoryMovementsPage() {
  const { storeId } = useInventoryContext();

  const [movements, setMovements] = useState<StockMovement[]>([]);
  const [movementFilter, setMovementFilter] = useState<StockMovement['type'] | 'ALL'>('ALL');
  const [movementSearch, setMovementSearch] = useState('');
  const [movementsLoading, setMovementsLoading] = useState(false);
  const [movementsTotal, setMovementsTotal] = useState(0);
  const [movementsHasMore, setMovementsHasMore] = useState(false);

  // skip=0 replaces the list; skip>0 appends (the Load-more path).
  const loadMovements = async (skip = 0) => {
    if (!storeId) return;
    setMovementsLoading(true);
    try {
      const res = await inventoryApi.getMovements({
        store_id: storeId,
        type: movementFilter === 'ALL' ? undefined : movementFilter,
        limit: MOVEMENTS_PAGE_SIZE,
        skip,
      });
      const items = res.items || [];
      setMovements(prev => (skip === 0 ? items : [...prev, ...items]));
      setMovementsTotal(res.total || 0);
      setMovementsHasMore(Boolean(res.has_more));
    } catch {
      if (skip === 0) {
        setMovements([]);
        setMovementsTotal(0);
      }
      setMovementsHasMore(false);
    } finally {
      setMovementsLoading(false);
    }
  };

  // Refetch page 1 whenever the viewed store or the type filter changes.
  // Stale data from a previous store never lingers because every trigger goes
  // through skip=0 (full replace).
  useEffect(() => {
    loadMovements(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storeId, movementFilter]);

  // Type filtering is server-side (?type=); only the free-text search narrows
  // the loaded rows client-side.
  const filteredMovements = movements.filter(m => {
    const q = movementSearch.toLowerCase();
    return !q ||
      (m.product_name || '').toLowerCase().includes(q) ||
      (m.sku || '').toLowerCase().includes(q) ||
      (m.ref || '').toLowerCase().includes(q) ||
      (m.detail || '').toLowerCase().includes(q);
  });
  const movementStats = {
    totalIn: movements.filter(m => m.qty > 0).reduce((s, m) => s + m.qty, 0),
    totalOut: movements.filter(m => m.qty < 0).reduce((s, m) => s - m.qty, 0),
    transfers: movements.filter(m => m.type === 'TRANSFER_IN' || m.type === 'TRANSFER_OUT').length,
    sales: movements.filter(m => m.type === 'SOLD').length,
  };
  const typeConfig: Record<StockMovement['type'], { label: string; color: string; bg: string; prefix: string }> = {
    RECEIVED: { label: 'Received', color: 'text-green-700', bg: 'bg-green-100', prefix: '+' },
    SOLD: { label: 'Sold', color: 'text-red-700', bg: 'bg-red-100', prefix: '-' },
    TRANSFER_IN: { label: 'Transfer In', color: 'text-blue-700', bg: 'bg-blue-100', prefix: '+' },
    TRANSFER_OUT: { label: 'Transfer Out', color: 'text-amber-700', bg: 'bg-amber-100', prefix: '-' },
    OPENING_STOCK: { label: 'Opening stock', color: 'text-gray-700', bg: 'bg-gray-100', prefix: '+' },
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
          <p className="text-2xl font-bold text-amber-600">{movementStats.sales}</p>
          <p className="text-xs text-amber-600">Sales</p>
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
            placeholder="Search product, SKU, or reference..."
            className="input-field pl-10 text-sm"
          />
        </div>
        <div className="flex gap-1">
          {(['ALL', 'RECEIVED', 'SOLD', 'TRANSFER_IN', 'TRANSFER_OUT', 'OPENING_STOCK'] as const).map(t => (
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
        {movementsLoading && movements.length === 0 ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
          </div>
        ) : filteredMovements.length === 0 ? (
          <div className="text-center py-12 text-gray-500">
            <ArrowRightLeft className="w-12 h-12 mx-auto mb-2 opacity-50" />
            <p className="font-medium">No stock movements in the last 90 days</p>
            <p className="text-sm mt-1">GRN receipts, sales and transfers will appear here as they happen</p>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-[auto_1fr_120px_80px_120px_100px] gap-2 px-4 py-2 bg-gray-50 border-b text-xs font-medium text-gray-500 uppercase">
              <div className="w-8">Type</div>
              <div>Product / Detail</div>
              <div>SKU</div>
              <div className="text-right">Qty</div>
              <div>Ref</div>
              <div>Time</div>
            </div>
            <div className="divide-y divide-gray-100 max-h-[500px] overflow-y-auto">
              {filteredMovements.map(movement => {
                const tc = typeConfig[movement.type];
                return (
                  <div key={movement.id} className={clsx(
                    'grid grid-cols-[auto_1fr_120px_80px_120px_100px] gap-2 px-4 py-3 items-center text-sm',
                    movement.qty > 0 ? 'bg-green-50/30' : 'bg-red-50/30'
                  )}>
                    <div>
                      <span className={clsx('inline-flex items-center justify-center w-8 h-8 rounded-full text-xs font-bold', tc.bg, tc.color)}>
                        {tc.prefix}
                      </span>
                    </div>
                    <div>
                      <p className="font-medium text-gray-900">{movement.product_name || movement.product_id}</p>
                      <p className="text-xs text-gray-500">{movement.detail}</p>
                    </div>
                    <div className="text-xs text-gray-500 font-mono">{movement.sku}</div>
                    <div className={clsx('text-right font-bold', movement.qty > 0 ? 'text-green-700' : 'text-red-700')}>
                      {movement.qty > 0 ? `+${movement.qty}` : movement.qty}
                    </div>
                    <div className="text-xs text-gray-600 font-mono truncate" title={movement.ref}>{movement.ref}</div>
                    <div className="text-xs text-gray-500">
                      {new Date(movement.at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
                      <br />
                      {new Date(movement.at).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' })}
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="px-4 py-2 bg-gray-50 border-t text-xs text-gray-500 flex items-center justify-between">
              <span>
                Showing {filteredMovements.length} of {movementsTotal} movements
                {movementFilter !== 'ALL' && ' (filtered)'}
                {movementSearch && ' (search)'}
              </span>
              {movementsHasMore && (
                <button
                  onClick={() => loadMovements(movements.length)}
                  disabled={movementsLoading}
                  className="px-3 py-1 rounded-lg text-xs font-medium bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-50"
                >
                  {movementsLoading ? 'Loading...' : 'Load more'}
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default InventoryMovementsPage;
