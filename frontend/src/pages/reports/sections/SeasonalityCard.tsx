// ============================================================================
// IMS 2.0 — TechCherry R1.4 — Seasonality
// ============================================================================
// Day-of-week × month-of-year revenue patterns over last N years.
// Spec: docs/TECHCHERRY_PORT_SCOPE.md §5.4.

import { useEffect, useState } from 'react';
import { AlertTriangle, CalendarDays, Loader2 } from 'lucide-react';
import { reportsApi } from '../../../services/api';

type Data = Awaited<ReturnType<typeof reportsApi.getSeasonality>>;

function inr(n: number): string {
  if (n >= 10000000) return '₹' + (n / 10000000).toFixed(2) + 'Cr';
  if (n >= 100000) return '₹' + (n / 100000).toFixed(2) + 'L';
  if (n >= 1000) return '₹' + (n / 1000).toFixed(1) + 'K';
  return '₹' + Math.round(n).toLocaleString('en-IN');
}

function pct(n: number, places = 0): string {
  return (n * 100).toFixed(places) + '%';
}

function HBar({ label, value, max }: { label: string; value: number; max: number }) {
  const w = max > 0 ? Math.max(2, (value / max) * 100) : 0;
  return (
    <div className="flex items-center gap-2 py-1">
      <span className="w-9 text-xs text-gray-500 font-mono">{label}</span>
      <div className="flex-1 bg-gray-100 rounded h-4 relative overflow-hidden">
        <div
          className="bg-bv-red-500 h-full rounded transition-all"
          style={{ width: `${w}%` }}
        />
      </div>
      <span className="w-16 text-xs text-right tabular-nums text-gray-700">{inr(value)}</span>
    </div>
  );
}

export function SeasonalityCard({ storeId }: { storeId?: string }) {
  const [data, setData] = useState<Data | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    reportsApi.getSeasonality(storeId, 2)
      .then(setData)
      .catch(e => setError(e?.response?.data?.detail || e?.message || 'Failed to load'))
      .finally(() => setLoading(false));
  }, [storeId]);

  const maxDow = data ? Math.max(...data.day_of_week.map(d => d.revenue), 0) : 0;
  const maxMoy = data ? Math.max(...data.month_of_year.map(m => m.revenue), 0) : 0;

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-gray-900 inline-flex items-center gap-2">
          <CalendarDays className="w-4 h-4 text-bv-red-600" />
          Seasonality
        </h3>
        <span className="text-xs text-gray-400">last 24 months</span>
      </div>
      <p className="text-xs text-gray-500 mb-4 max-w-3xl">
        When do customers actually buy? Heaviest day of week and month of year, by revenue.
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
          {data.total_orders === 0 ? (
            <p className="text-xs text-gray-400 text-center py-4">No orders in the last 24 months yet.</p>
          ) : (
            <>
              <div className="grid grid-cols-2 tablet:grid-cols-4 gap-3 mb-4">
                <div className="bg-white rounded p-3 border border-gray-100">
                  <p className="text-xs text-gray-500">Peak day</p>
                  <p className="text-lg font-bold text-gray-900 mt-1">{data.peak_dow || '—'}</p>
                  <p className="text-[11px] text-emerald-700 mt-0.5">
                    +{pct(data.peak_dow_lift_pct, 0)} vs avg
                  </p>
                </div>
                <div className="bg-white rounded p-3 border border-gray-100">
                  <p className="text-xs text-gray-500">Trough day</p>
                  <p className="text-lg font-bold text-gray-900 mt-1">{data.trough_dow || '—'}</p>
                </div>
                <div className="bg-white rounded p-3 border border-gray-100">
                  <p className="text-xs text-gray-500">Peak month</p>
                  <p className="text-lg font-bold text-gray-900 mt-1">{data.peak_month || '—'}</p>
                </div>
                <div className="bg-white rounded p-3 border border-gray-100">
                  <p className="text-xs text-gray-500">Trough month</p>
                  <p className="text-lg font-bold text-gray-900 mt-1">{data.trough_month || '—'}</p>
                </div>
              </div>

              <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
                <div className="bg-white rounded p-3 border border-gray-100">
                  <p className="text-xs font-medium text-gray-700 mb-2">By day of week</p>
                  {data.day_of_week.map(d => (
                    <HBar key={d.dow} label={d.dow} value={d.revenue} max={maxDow} />
                  ))}
                </div>
                <div className="bg-white rounded p-3 border border-gray-100">
                  <p className="text-xs font-medium text-gray-700 mb-2">By month of year</p>
                  {data.month_of_year.map(m => (
                    <HBar key={m.month} label={m.month} value={m.revenue} max={maxMoy} />
                  ))}
                </div>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
