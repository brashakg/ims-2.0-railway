// ============================================================================
// IMS 2.0 — TechCherry R1.2 — Price Band Analysis
// ============================================================================
// Segment invoices into 11 bands (₹<1K → ₹1.5L+) and track customer movement
// across financial years. Spec: docs/TECHCHERRY_PORT_SCOPE.md §5.2.

import { Fragment, useEffect, useState } from 'react';
import { AlertTriangle, ArrowDownRight, ArrowUpRight, BarChart3, Loader2 } from 'lucide-react';
import { reportsApi } from '../../../services/api';

type Data = Awaited<ReturnType<typeof reportsApi.getPriceBands>>;

function inr(n: number): string {
  if (n >= 10000000) return '₹' + (n / 10000000).toFixed(2) + 'Cr';
  if (n >= 100000) return '₹' + (n / 100000).toFixed(2) + 'L';
  if (n >= 1000) return '₹' + (n / 1000).toFixed(1) + 'K';
  return '₹' + Math.round(n).toLocaleString('en-IN');
}

function pct(n: number): string {
  return (n * 100).toFixed(0) + '%';
}

export function PriceBandCard({ storeId }: { storeId?: string }) {
  const [data, setData] = useState<Data | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    reportsApi.getPriceBands(storeId, 3, 4)
      .then(setData)
      .catch(e => setError(e?.response?.data?.detail || e?.message || 'Failed to load'))
      .finally(() => setLoading(false));
  }, [storeId]);

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-gray-900 inline-flex items-center gap-2">
          <BarChart3 className="w-4 h-4 text-bv-red-600" />
          Price Band Analysis
        </h3>
        <span className="text-xs text-gray-400">last 3 FYs</span>
      </div>
      <p className="text-xs text-gray-500 mb-4 max-w-3xl">
        Invoice net amount bucketed into 11 bands. Movement signal shows how many repeat customers
        moved up (premiumized), stayed, or moved down.
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
          {/* Movement summary */}
          <div className="grid grid-cols-3 gap-3 mb-4">
            <div className="bg-emerald-50 rounded p-3 border border-emerald-100">
              <p className="text-xs text-emerald-700 inline-flex items-center gap-1">
                <ArrowUpRight className="w-3 h-3" /> Premiumized
              </p>
              <p className="text-lg font-bold text-emerald-700 mt-1 tabular-nums">
                {pct(data.movement_summary.premiumized_pct)}
              </p>
            </div>
            <div className="bg-gray-50 rounded p-3 border border-gray-100">
              <p className="text-xs text-gray-500">Stable</p>
              <p className="text-lg font-bold text-gray-700 mt-1 tabular-nums">
                {pct(data.movement_summary.stable_pct)}
              </p>
            </div>
            <div className="bg-red-50 rounded p-3 border border-red-100">
              <p className="text-xs text-red-700 inline-flex items-center gap-1">
                <ArrowDownRight className="w-3 h-3" /> Downgraded
              </p>
              <p className="text-lg font-bold text-red-700 mt-1 tabular-nums">
                {pct(data.movement_summary.downgraded_pct)}
              </p>
            </div>
          </div>
          <p className="text-[11px] text-gray-400 mb-3">
            Based on {data.movement_summary.compared_customers} customers active in both the
            current and previous FY.
          </p>

          {/* Invoices by band per FY */}
          {data.by_fy.length === 0 ? (
            <p className="text-xs text-gray-400 text-center py-3">No invoices yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-xs text-gray-500 border-b">
                  <tr>
                    <th className="text-left py-2 px-2 font-medium">Band (net ₹)</th>
                    {data.by_fy.map(fy => (
                      <th key={fy.fy} className="text-right py-2 px-2 font-medium" colSpan={2}>{fy.fy}</th>
                    ))}
                  </tr>
                  <tr className="text-[10px] text-gray-400">
                    <th></th>
                    {data.by_fy.map(fy => (
                      <Fragment key={fy.fy}>
                        <th className="text-right py-1 px-2 font-normal">invoices</th>
                        <th className="text-right py-1 px-2 font-normal">revenue</th>
                      </Fragment>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.bands.map((band, bi) => (
                    <tr key={band} className="border-b last:border-b-0">
                      <td className="py-1.5 px-2 font-mono text-xs">{band}</td>
                      {data.by_fy.map(fy => (
                        <Fragment key={fy.fy}>
                          <td className="py-1.5 px-2 text-right tabular-nums text-gray-700">
                            {fy.invoices_by_band[bi]}
                          </td>
                          <td className="py-1.5 px-2 text-right tabular-nums">
                            {fy.revenue_by_band[bi] > 0 ? inr(fy.revenue_by_band[bi]) : '—'}
                          </td>
                        </Fragment>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
