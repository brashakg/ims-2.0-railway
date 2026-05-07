// ============================================================================
// IMS 2.0 — Payout Snapshots List (Pune Incentive Module iii)
// ============================================================================
// All locked + paid snapshots for a year. Click a row to deep-link
// into the dashboard at that period (the dashboard shows the LOCKED
// view when a snapshot exists).

import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft, RefreshCw, Loader2, Download, Lock, Check } from 'lucide-react';
import { payoutApi } from '../../services/api';
import { useToast } from '../../context/ToastContext';
import type { PayoutSnapshot } from '../../types';

function inr(n: number | null | undefined): string {
  if (n == null) return '—';
  return '₹' + Math.round(n).toLocaleString('en-IN');
}

export function PayoutSnapshotsPage() {
  const toast = useToast();
  const [year, setYear] = useState(new Date().getFullYear());
  const [items, setItems] = useState<PayoutSnapshot[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const r = await payoutApi.list(year);
      setItems(r.items || []);
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Failed to load';
      toast.error(typeof msg === 'string' ? msg : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, [year, toast]);

  useEffect(() => { refresh(); }, [refresh]);

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <Link to="/incentive/payout" className="inline-flex items-center gap-1 text-sm text-gray-600 hover:text-gray-900 mb-2">
            <ArrowLeft className="w-4 h-4" /> Payout dashboard
          </Link>
          <h1 className="text-2xl font-bold text-gray-900 inline-flex items-center gap-2">
            <Lock className="w-6 h-6 text-bv-red-500" /> Payout snapshots
          </h1>
          <p className="text-gray-500 text-sm mt-1">
            Locked + paid monthly snapshots for {year}.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={year} onChange={e => setYear(Number(e.target.value))}
            className="px-3 py-2 border border-gray-300 rounded text-sm"
          >
            {[2025, 2026, 2027].map(y => <option key={y} value={y}>{y}</option>)}
          </select>
          <button
            type="button" onClick={refresh} disabled={loading}
            className="btn-secondary inline-flex items-center gap-2"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </button>
        </div>
      </div>

      <div className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b border-gray-200 text-xs uppercase tracking-wider text-gray-600">
            <tr>
              <th className="px-3 py-2 text-left">Snapshot</th>
              <th className="px-3 py-2 text-center">Period</th>
              <th className="px-3 py-2 text-center">Status</th>
              <th className="px-3 py-2 text-center">Best level</th>
              <th className="px-3 py-2 text-right">Pool</th>
              <th className="px-3 py-2 text-right">Grand total</th>
              <th className="px-3 py-2 text-right">Locked / Paid</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {items.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-3 py-12 text-center text-gray-500">
                  {loading ? <Loader2 className="w-5 h-5 animate-spin inline-block mr-2" /> : null}
                  {loading ? 'Loading…' : 'No snapshots locked for this year yet.'}
                </td>
              </tr>
            ) : items.map(s => (
              <tr key={s.snapshot_id}>
                <td className="px-3 py-2 font-mono text-xs text-gray-700">{s.snapshot_id}</td>
                <td className="px-3 py-2 text-center">{s.year}-{String(s.month).padStart(2, '0')}</td>
                <td className="px-3 py-2 text-center">
                  <span className={`text-xs px-2 py-0.5 rounded-full border ${
                    s.status === 'PAID' ? 'bg-emerald-50 text-emerald-700 border-emerald-200' :
                    s.status === 'LOCKED' ? 'bg-blue-50 text-blue-700 border-blue-200' :
                    'bg-gray-50 text-gray-600 border-gray-200'
                  }`}>
                    {s.status === 'PAID' && <Check className="w-3 h-3 inline mr-0.5" />}
                    {s.status}
                  </span>
                </td>
                <td className="px-3 py-2 text-center text-gray-700">{s.best_level_achieved || '—'}</td>
                <td className="px-3 py-2 text-right text-gray-700">{inr(s.total_team_pool)}</td>
                <td className="px-3 py-2 text-right font-semibold">{inr(s.grand_total?.all)}</td>
                <td className="px-3 py-2 text-right text-xs text-gray-500">
                  {s.locked_at ? new Date(s.locked_at).toLocaleDateString() : '—'}
                  {s.paid_at && <div>Paid {new Date(s.paid_at).toLocaleDateString()}</div>}
                </td>
                <td className="px-3 py-2 text-right">
                  <a
                    href={payoutApi.csvUrl(s.snapshot_id)}
                    className="text-xs text-bv-red-600 hover:underline inline-flex items-center gap-1"
                    target="_blank" rel="noreferrer"
                  >
                    <Download className="w-3 h-3" /> CSV
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default PayoutSnapshotsPage;
