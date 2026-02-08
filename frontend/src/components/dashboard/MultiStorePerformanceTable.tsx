import { useState, useMemo } from 'react';
import { ChevronDown, ChevronUp, TrendingUp, TrendingDown } from 'lucide-react';
import clsx from 'clsx';

export interface StoreMetrics {
  storeId: string;
  storeName: string;
  revenue: number;
  orders: number;
  averageOrderValue: number;
  marginPercent: number;
  stockValue: number;
  staffCount: number;
  revenuePerSqft: number;
  trend: number; // percentage change
  targets?: {
    margin?: number;
    revenue?: number;
    aov?: number;
  };
}

interface MultiStorePerformanceTableProps {
  stores: StoreMetrics[];
  onStoreClick?: (storeId: string) => void;
  loading?: boolean;
}

type SortKey = keyof StoreMetrics;
type SortDirection = 'asc' | 'desc';

export function MultiStorePerformanceTable({ stores, onStoreClick, loading }: MultiStorePerformanceTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>('revenue');
  const [sortDir, setSortDir] = useState<SortDirection>('desc');

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  const sortedStores = useMemo(() => {
    const sorted = [...stores].sort((a, b) => {
      const aVal = a[sortKey];
      const bVal = b[sortKey];

      if (typeof aVal === 'number' && typeof bVal === 'number') {
        return sortDir === 'asc' ? aVal - bVal : bVal - aVal;
      }

      const aStr = String(aVal);
      const bStr = String(bVal);
      return sortDir === 'asc' ? aStr.localeCompare(bStr) : bStr.localeCompare(aStr);
    });

    return sorted;
  }, [stores, sortKey, sortDir]);

  const getPerformanceStatus = (store: StoreMetrics): 'excellent' | 'good' | 'warning' | 'critical' => {
    const avgRevenue = stores.reduce((sum, s) => sum + s.revenue, 0) / stores.length;
    const revenueRatio = store.revenue / avgRevenue;

    if (revenueRatio > 1.2) return 'excellent';
    if (revenueRatio > 0.9) return 'good';
    if (revenueRatio > 0.7) return 'warning';
    return 'critical';
  };

  const getMarginColor = (margin: number, target?: number): string => {
    const threshold = target || 40;
    if (margin >= threshold) return 'text-green-600 bg-green-50';
    if (margin >= threshold - 5) return 'text-amber-600 bg-amber-50';
    return 'text-red-600 bg-red-50';
  };

  const SortIcon = ({ isActive, direction }: { isActive: boolean; direction: SortDirection }) => {
    if (!isActive) return <span className="text-gray-300">⬍</span>;
    return direction === 'asc' ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />;
  };

  return (
    <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
      {/* Header */}
      <div className="p-4 border-b border-gray-200 bg-gradient-to-r from-blue-50 to-purple-50">
        <h3 className="text-lg font-semibold text-gray-900">Multi-Store Performance Comparison</h3>
        <p className="text-xs text-gray-600 mt-1">All metrics for {stores.length} stores • Click to drill-down</p>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="bg-gray-50 border-b border-gray-200">
              <th className="px-4 py-3 text-left">
                <button
                  onClick={() => handleSort('storeName')}
                  className="flex items-center gap-2 font-semibold text-gray-700 text-sm hover:text-gray-900"
                >
                  Store Name
                  <SortIcon isActive={sortKey === 'storeName'} direction={sortDir} />
                </button>
              </th>
              <th className="px-4 py-3 text-right">
                <button
                  onClick={() => handleSort('revenue')}
                  className="flex items-center justify-end gap-2 font-semibold text-gray-700 text-sm hover:text-gray-900 ml-auto"
                >
                  Revenue
                  <SortIcon isActive={sortKey === 'revenue'} direction={sortDir} />
                </button>
              </th>
              <th className="px-4 py-3 text-right">
                <button
                  onClick={() => handleSort('orders')}
                  className="flex items-center justify-end gap-2 font-semibold text-gray-700 text-sm hover:text-gray-900 ml-auto"
                >
                  Orders
                  <SortIcon isActive={sortKey === 'orders'} direction={sortDir} />
                </button>
              </th>
              <th className="px-4 py-3 text-right">
                <button
                  onClick={() => handleSort('averageOrderValue')}
                  className="flex items-center justify-end gap-2 font-semibold text-gray-700 text-sm hover:text-gray-900 ml-auto"
                >
                  AOV
                  <SortIcon isActive={sortKey === 'averageOrderValue'} direction={sortDir} />
                </button>
              </th>
              <th className="px-4 py-3 text-right">
                <button
                  onClick={() => handleSort('marginPercent')}
                  className="flex items-center justify-end gap-2 font-semibold text-gray-700 text-sm hover:text-gray-900 ml-auto"
                >
                  Margin %
                  <SortIcon isActive={sortKey === 'marginPercent'} direction={sortDir} />
                </button>
              </th>
              <th className="px-4 py-3 text-right">
                <button
                  onClick={() => handleSort('stockValue')}
                  className="flex items-center justify-end gap-2 font-semibold text-gray-700 text-sm hover:text-gray-900 ml-auto"
                >
                  Stock Value
                  <SortIcon isActive={sortKey === 'stockValue'} direction={sortDir} />
                </button>
              </th>
              <th className="px-4 py-3 text-right">
                <button
                  onClick={() => handleSort('staffCount')}
                  className="flex items-center justify-end gap-2 font-semibold text-gray-700 text-sm hover:text-gray-900 ml-auto"
                >
                  Staff
                  <SortIcon isActive={sortKey === 'staffCount'} direction={sortDir} />
                </button>
              </th>
              <th className="px-4 py-3 text-right">
                <button
                  onClick={() => handleSort('revenuePerSqft')}
                  className="flex items-center justify-end gap-2 font-semibold text-gray-700 text-sm hover:text-gray-900 ml-auto"
                >
                  Rev/Sq.Ft
                  <SortIcon isActive={sortKey === 'revenuePerSqft'} direction={sortDir} />
                </button>
              </th>
              <th className="px-4 py-3 text-center font-semibold text-gray-700 text-sm">Trend</th>
            </tr>
          </thead>

          <tbody>
            {loading ? (
              <tr>
                <td colSpan={9} className="px-4 py-8 text-center">
                  <div className="flex items-center justify-center gap-2">
                    <div className="w-4 h-4 rounded-full bg-blue-600 animate-bounce" />
                    <span className="text-gray-600">Loading store data...</span>
                  </div>
                </td>
              </tr>
            ) : sortedStores.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-4 py-8 text-center text-gray-500">
                  No store data available
                </td>
              </tr>
            ) : (
              sortedStores.map((store, idx) => {
                const status = getPerformanceStatus(store);
                const marginColor = getMarginColor(store.marginPercent, store.targets?.margin);

                return (
                  <tr
                    key={store.storeId}
                    className={clsx(
                      'border-b border-gray-200 hover:bg-gray-50 transition-colors cursor-pointer',
                      idx % 2 === 0 ? 'bg-white' : 'bg-gray-50',
                    )}
                    onClick={() => onStoreClick?.(store.storeId)}
                  >
                    {/* Store Name */}
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <div className={clsx('w-2 h-2 rounded-full', {
                          'bg-green-500': status === 'excellent',
                          'bg-blue-500': status === 'good',
                          'bg-amber-500': status === 'warning',
                          'bg-red-500': status === 'critical',
                        })} />
                        <div>
                          <p className="font-medium text-gray-900">{store.storeName}</p>
                          <p className="text-xs text-gray-500">ID: {store.storeId.slice(0, 8)}</p>
                        </div>
                      </div>
                    </td>

                    {/* Revenue */}
                    <td className="px-4 py-3 text-right">
                      <div>
                        <p className="font-semibold text-gray-900">
                          ₹{(store.revenue / 100000).toFixed(1)}L
                        </p>
                        {store.targets?.revenue && (
                          <p className="text-xs text-gray-600">
                            Target: ₹{(store.targets.revenue / 100000).toFixed(1)}L
                          </p>
                        )}
                      </div>
                    </td>

                    {/* Orders */}
                    <td className="px-4 py-3 text-right">
                      <p className="font-semibold text-gray-900">{store.orders}</p>
                    </td>

                    {/* AOV */}
                    <td className="px-4 py-3 text-right">
                      <p className="font-semibold text-gray-900">
                        ₹{store.averageOrderValue.toLocaleString('en-IN')}
                      </p>
                    </td>

                    {/* Margin % */}
                    <td className="px-4 py-3 text-right">
                      <span className={clsx('px-3 py-1 rounded-lg font-semibold text-sm', marginColor)}>
                        {store.marginPercent.toFixed(1)}%
                      </span>
                    </td>

                    {/* Stock Value */}
                    <td className="px-4 py-3 text-right">
                      <p className="font-semibold text-gray-900">
                        ₹{(store.stockValue / 100000).toFixed(1)}L
                      </p>
                    </td>

                    {/* Staff Count */}
                    <td className="px-4 py-3 text-right">
                      <p className="font-semibold text-gray-900">{store.staffCount}</p>
                    </td>

                    {/* Revenue/Sq.Ft */}
                    <td className="px-4 py-3 text-right">
                      <p className="font-semibold text-gray-900">
                        ₹{store.revenuePerSqft.toLocaleString('en-IN')}
                      </p>
                    </td>

                    {/* Trend */}
                    <td className="px-4 py-3 text-center">
                      <div className="flex items-center justify-center gap-1">
                        {store.trend > 0 ? (
                          <TrendingUp className="w-4 h-4 text-green-600" />
                        ) : (
                          <TrendingDown className="w-4 h-4 text-red-600" />
                        )}
                        <span className={clsx(
                          'text-xs font-semibold',
                          store.trend > 0 ? 'text-green-600' : 'text-red-600'
                        )}>
                          {store.trend > 0 ? '+' : ''}{store.trend.toFixed(1)}%
                        </span>
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Summary Footer */}
      {stores.length > 0 && (
        <div className="px-4 py-3 bg-gray-50 border-t border-gray-200">
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-4 text-sm">
            <div>
              <p className="text-gray-600">Total Stores</p>
              <p className="text-lg font-semibold text-gray-900">{stores.length}</p>
            </div>
            <div>
              <p className="text-gray-600">Combined Revenue</p>
              <p className="text-lg font-semibold text-gray-900">
                ₹{(stores.reduce((s, st) => s + st.revenue, 0) / 10000000).toFixed(1)}Cr
              </p>
            </div>
            <div>
              <p className="text-gray-600">Avg. Margin</p>
              <p className="text-lg font-semibold text-gray-900">
                {(stores.reduce((s, st) => s + st.marginPercent, 0) / stores.length).toFixed(1)}%
              </p>
            </div>
            <div>
              <p className="text-gray-600">Total Staff</p>
              <p className="text-lg font-semibold text-gray-900">
                {stores.reduce((s, st) => s + st.staffCount, 0)}
              </p>
            </div>
            <div>
              <p className="text-gray-600">Stores Above Target</p>
              <p className="text-lg font-semibold text-green-600">
                {stores.filter(s => !s.targets?.margin || s.marginPercent >= s.targets.margin).length}
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default MultiStorePerformanceTable;
