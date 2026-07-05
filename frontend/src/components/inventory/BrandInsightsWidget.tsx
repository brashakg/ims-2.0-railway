// ============================================================================
// IMS 2.0 - Brand Insights (Inventory > Insights > Brands)
// ============================================================================
// KPI table per brand over GET /inventory/brand-insights: on-hand units,
// stock value (offer basis, mrp fallback), sold + revenue over the selected
// window, sell-through % and days of cover. KPI math is shared server-side
// with the Collections insights so the two tabs always agree.
// Styled after SellThroughAnalysisWidget (AdvancedInventoryFeatures.tsx).

import { useState, useEffect } from 'react';
import { BarChart3 } from 'lucide-react';
// Import DIRECT from the module (not the api barrel — TS2614).
import { inventoryApi, type BrandInsightRow } from '../../services/api/inventory';
import { useAuth } from '../../context/AuthContext';

/** Indian-locale rupees, no paise (same rendering rule as the Collections
 *  pages' `rupee` helper). */
function rupee(n?: number | null): string {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—';
  return `₹${Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
}

function fmtInt(n?: number | null): string {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—';
  return Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 });
}

/** Days-of-cover with the 999 backend cap rendered as "999+". */
function fmtCover(n?: number | null): string {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—';
  const v = Number(n);
  if (v >= 999) return '999+';
  return v.toLocaleString('en-IN', { maximumFractionDigits: 0 });
}

export function BrandInsightsWidget() {
  const { user } = useAuth();
  const [rows, setRows] = useState<BrandInsightRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(30);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const data = await inventoryApi.brandInsights(days, user?.activeStoreId || undefined);
        if (!cancelled) setRows(data?.brands || []);
      } catch {
        if (!cancelled) setRows([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [days, user?.activeStoreId]);

  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
      <div className="p-4 border-b border-gray-200 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BarChart3 className="w-5 h-5 text-blue-600" />
          <h3 className="font-semibold text-gray-900">Brand Insights ({days}d)</h3>
        </div>
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="px-2 py-1 bg-gray-100 border border-gray-300 rounded text-sm text-gray-700"
        >
          <option value={30}>30 days</option>
          <option value={60}>60 days</option>
          <option value={90}>90 days</option>
        </select>
      </div>

      {loading ? (
        <div className="p-4 text-center text-gray-500">Loading...</div>
      ) : rows.length === 0 ? (
        <div className="p-4 text-center text-gray-500">No data available</div>
      ) : (
        <div className="max-h-96 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="bg-white border-b border-gray-200 sticky top-0">
              <tr>
                <th className="px-4 py-2 text-left text-xs font-semibold text-gray-500">Brand</th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-500">On hand</th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-500">Stock value</th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-500">Sold {days}d</th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-500">Revenue {days}d</th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-500">Sell-through</th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-500">Days cover</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.brand} className="border-b border-gray-200 hover:bg-white">
                  <td className="px-4 py-2 text-gray-900 font-medium">{r.brand}</td>
                  <td className="px-4 py-2 text-right text-gray-700">{fmtInt(r.units_on_hand)}</td>
                  <td className="px-4 py-2 text-right text-gray-700">{rupee(r.stock_value)}</td>
                  <td className="px-4 py-2 text-right text-gray-700">{fmtInt(r.units_sold)}</td>
                  <td className="px-4 py-2 text-right text-gray-900 font-medium">{rupee(r.revenue)}</td>
                  <td className="px-4 py-2 text-right">
                    {r.sell_through_percent === null || r.sell_through_percent === undefined ? (
                      <span className="text-gray-400">—</span>
                    ) : (
                      <div className="flex items-center justify-end gap-2">
                        <div className="w-16 bg-gray-100 rounded-full h-1.5">
                          <div
                            className="bg-green-500 h-1.5 rounded-full"
                            style={{ width: `${Math.min(r.sell_through_percent, 100)}%` }}
                          />
                        </div>
                        <span className="text-green-600 font-semibold w-12 text-right">
                          {r.sell_through_percent.toFixed(1)}%
                        </span>
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-2 text-right text-gray-700">{fmtCover(r.days_cover)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
