// ============================================================================
// IMS 2.0 - Offer Tally report (F11) — fired promos + margin impact
// ============================================================================
// Aggregates the promo_applications audit collection over a date window. Margin
// rows flagged "estimated COGS" are marked so the owner never mistakes an
// estimate for real margin. Negative net margin shown in red (the only colour
// used decoratively-for-meaning). Backend: /reports/promotions.

import { useCallback, useEffect, useState } from 'react';
import { RefreshCw, BarChart3, AlertTriangle } from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { promotionsApi, type PromoReport } from '../../services/api/promotions';

function rupees(n: number): string {
  return `Rs ${(n || 0).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
}

function todayISO(offsetDays = 0): string {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  return d.toISOString().slice(0, 10);
}

export default function PromotionsReportPage() {
  const { user } = useAuth();
  const toast = useToast();
  const storeId = user?.activeStoreId || undefined;

  const [start, setStart] = useState(todayISO(-30));
  const [end, setEnd] = useState(todayISO(0));
  const [report, setReport] = useState<PromoReport | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await promotionsApi.report({
        start_date: start,
        end_date: end,
        store_id: storeId,
      });
      setReport(res);
    } catch {
      toast.error('Could not load the promotions report');
    } finally {
      setLoading(false);
    }
  }, [start, end, storeId, toast]);

  useEffect(() => {
    load();
  }, [load]);

  const s = report?.summary;

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900 flex items-center gap-2">
            <BarChart3 className="w-6 h-6 text-bv-red-600" />
            Offer Tally
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            What promos fired, the discount given, and the margin impact.
          </p>
        </div>
        <div className="flex items-end gap-2">
          <div>
            <label className="block text-xs text-gray-500 mb-1">From</label>
            <input
              type="date"
              value={start}
              onChange={(e) => setStart(e.target.value)}
              className="px-3 py-2 border border-gray-200 rounded-lg text-sm"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">To</label>
            <input
              type="date"
              value={end}
              onChange={(e) => setEnd(e.target.value)}
              className="px-3 py-2 border border-gray-200 rounded-lg text-sm"
            />
          </div>
          <button
            onClick={load}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-sm text-gray-700 border border-gray-200 rounded-lg hover:bg-gray-50"
          >
            <RefreshCw className="w-4 h-4" /> Run
          </button>
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <div className="bg-white border border-gray-200 rounded-xl p-4">
          <div className="text-xs text-gray-500">Total discount given</div>
          <div className="text-xl font-semibold text-gray-900 mt-1">
            {rupees(s?.total_discount_given || 0)}
          </div>
        </div>
        <div className="bg-white border border-gray-200 rounded-xl p-4">
          <div className="text-xs text-gray-500">Orders with promos</div>
          <div className="text-xl font-semibold text-gray-900 mt-1">
            {s?.orders_with_promos ?? 0}
          </div>
        </div>
        <div className="bg-white border border-gray-200 rounded-xl p-4">
          <div className="text-xs text-gray-500">Distinct promos fired</div>
          <div className="text-xl font-semibold text-gray-900 mt-1">
            {s?.promos_fired ?? 0}
          </div>
        </div>
        <div className="bg-white border border-gray-200 rounded-xl p-4">
          <div className="text-xs text-gray-500">Net margin impact</div>
          <div
            className={clsx(
              'text-xl font-semibold mt-1',
              (s?.net_margin_impact || 0) < 0 ? 'text-red-600' : 'text-gray-900',
            )}
          >
            {rupees(s?.net_margin_impact || 0)}
          </div>
        </div>
      </div>

      {s?.any_cogs_estimated && (
        <div className="flex items-center gap-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mb-4">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          Some rows use an ESTIMATED cost of goods (60% fallback) because the line
          had no recorded cost. Estimated margin is not actual margin.
        </div>
      )}

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-500 text-xs uppercase">
            <tr>
              <th className="text-left font-medium px-4 py-3">Promo</th>
              <th className="text-left font-medium px-4 py-3">Type</th>
              <th className="text-right font-medium px-4 py-3">Orders</th>
              <th className="text-right font-medium px-4 py-3">Discount given</th>
              <th className="text-right font-medium px-4 py-3">Est. COGS</th>
              <th className="text-right font-medium px-4 py-3">Net margin impact</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-gray-400">
                  Loading...
                </td>
              </tr>
            ) : !report || report.promos.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-gray-400">
                  No promo applications in this window.
                </td>
              </tr>
            ) : (
              report.promos.map((r) => (
                <tr key={r.promo_id} className="hover:bg-gray-50">
                  <td className="px-4 py-3">
                    <div className="font-medium text-gray-900">{r.promo_name}</div>
                    {r.cogs_is_estimated && (
                      <span className="text-xs text-amber-600">estimated COGS</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-gray-600">{r.promo_type || '—'}</td>
                  <td className="px-4 py-3 text-right text-gray-700">
                    {r.orders_count}
                  </td>
                  <td className="px-4 py-3 text-right text-gray-900">
                    {rupees(r.total_discount_given)}
                  </td>
                  <td className="px-4 py-3 text-right text-gray-600">
                    {rupees(r.estimated_cogs)}
                  </td>
                  <td
                    className={clsx(
                      'px-4 py-3 text-right font-medium',
                      r.net_margin_after_promo < 0 ? 'text-red-600' : 'text-gray-900',
                    )}
                  >
                    {rupees(r.net_margin_after_promo)}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
