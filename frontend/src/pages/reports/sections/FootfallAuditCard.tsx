// ============================================================================
// IMS 2.0 — TechCherry R1.1 — Footfall Audit
// ============================================================================
// Cross-references walk-in counters, walkouts, and orders to surface hidden
// sales (orders without a logged walk-in). Spec: docs/TECHCHERRY_PORT_SCOPE.md §5.1.

import { useEffect, useState } from 'react';
import { AlertTriangle, Loader2, TrendingUp } from 'lucide-react';
import { reportsApi } from '../../../services/api';

type Data = Awaited<ReturnType<typeof reportsApi.getFootfallAudit>>;

function pct(n: number, places = 0): string {
  return (n * 100).toFixed(places) + '%';
}

export function FootfallAuditCard({ storeId }: { storeId?: string }) {
  const [data, setData] = useState<Data | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    reportsApi.getFootfallAudit(storeId, 6)
      .then(setData)
      .catch(e => setError(e?.response?.data?.detail || e?.message || 'Failed to load'))
      .finally(() => setLoading(false));
  }, [storeId]);

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-gray-900 inline-flex items-center gap-2">
          <TrendingUp className="w-4 h-4 text-bv-red-600" />
          Footfall Audit
        </h3>
        <span className="text-xs text-gray-400">last 6 months</span>
      </div>
      <p className="text-xs text-gray-500 mb-4 max-w-3xl">
        Cross-reference walk-in counters, walkouts, and orders. <strong>Hidden sales</strong> = orders
        without a logged walk-in. Large gap = staff isn't logging foot traffic.
      </p>

      {loading && (
        <div className="h-32 flex items-center justify-center">
          <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
        </div>
      )}
      {error && (
        <div className="flex items-start gap-2 text-sm text-red-700 bg-red-50 border border-red-200 rounded p-3">
          <AlertTriangle className="w-4 h-4 mt-0.5" /> {error}
        </div>
      )}
      {data && !loading && (
        <>
          <div className="grid grid-cols-2 tablet:grid-cols-4 gap-3 mb-4">
            <div className="bg-white rounded p-3 border border-gray-100">
              <p className="text-xs text-gray-500">Walk-ins (6m)</p>
              <p className="figure text-lg text-gray-900 mt-1">{data.rolling.walkins_total}</p>
            </div>
            <div className="bg-white rounded p-3 border border-gray-100">
              <p className="text-xs text-gray-500">Orders (6m)</p>
              <p className="figure text-lg text-gray-900 mt-1">{data.rolling.orders_total}</p>
            </div>
            <div className="bg-white rounded p-3 border border-gray-100">
              <p className="text-xs text-gray-500">Hidden sales</p>
              <p className={`figure text-lg mt-1 ${
                data.rolling.hidden_sales_pct > 0.5 ? 'text-red-600' :
                data.rolling.hidden_sales_pct > 0.25 ? 'text-amber-600' : 'text-gray-900'
              }`}>
                {data.rolling.hidden_sales} <span className="text-xs text-gray-400">({pct(data.rolling.hidden_sales_pct)})</span>
              </p>
            </div>
            <div className="bg-white rounded p-3 border border-gray-100">
              <p className="text-xs text-gray-500">True / Reported conv.</p>
              <p className="figure text-lg text-gray-900 mt-1">
                {pct(data.rolling.true_conversion_pct)} <span className="text-xs text-gray-400">/ {pct(data.rolling.staff_reported_conversion_pct)}</span>
              </p>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs text-gray-500 border-b">
                <tr>
                  <th className="text-left py-2 px-2 font-medium">Month</th>
                  <th className="text-right py-2 px-2 font-medium">Walk-ins</th>
                  <th className="text-right py-2 px-2 font-medium">Walkouts</th>
                  <th className="text-right py-2 px-2 font-medium">Converted</th>
                  <th className="text-right py-2 px-2 font-medium">Orders</th>
                  <th className="text-right py-2 px-2 font-medium">Hidden</th>
                  <th className="text-right py-2 px-2 font-medium">True conv.</th>
                </tr>
              </thead>
              <tbody>
                {data.months.length === 0 && (
                  <tr><td colSpan={7} className="py-3 text-center text-gray-400 text-xs">No data yet — log walk-ins and orders to populate.</td></tr>
                )}
                {data.months.map(m => (
                  <tr key={m.month} className="border-b last:border-b-0">
                    <td className="py-1.5 px-2 font-mono text-xs">{m.month}</td>
                    <td className="py-1.5 px-2 text-right tabular-nums">{m.walkins_total}</td>
                    <td className="py-1.5 px-2 text-right tabular-nums text-gray-500">{m.walkouts_total}</td>
                    <td className="py-1.5 px-2 text-right tabular-nums text-gray-500">{m.walkouts_converted}</td>
                    <td className="py-1.5 px-2 text-right tabular-nums">{m.orders_total}</td>
                    <td className={`py-1.5 px-2 text-right tabular-nums ${
                      m.hidden_sales_pct > 0.5 ? 'text-red-600' :
                      m.hidden_sales_pct > 0.25 ? 'text-amber-600' : 'text-gray-700'
                    }`}>
                      {m.hidden_sales} <span className="text-[10px] text-gray-400">({pct(m.hidden_sales_pct)})</span>
                    </td>
                    <td className="py-1.5 px-2 text-right tabular-nums">{pct(m.true_conversion_pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
