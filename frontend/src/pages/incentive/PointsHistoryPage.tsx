// ============================================================================
// IMS 2.0 — Points History (Pune Incentive Module ii, Phase 3)
// ============================================================================
// One staff member's daily points across a date range.

import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, RefreshCw, Loader2, History } from 'lucide-react';
import { incentiveApi } from '../../services/api';
import { useToast } from '../../context/ToastContext';
import type { PointsLog } from '../../types';
import { EligibilityChip } from './DailyScorecardPage';

const CATEGORY_KEYS = [
  'attendance', 'conversion', 'task', 'visufit', 'punctuality',
  'behaviour', 'kicker_1', 'kicker_2', 'reviews',
] as const;

export function PointsHistoryPage() {
  const { staffId = '' } = useParams<{ staffId: string }>();
  const toast = useToast();

  const today = new Date().toISOString().slice(0, 10);
  const monthStart = today.slice(0, 7) + '-01';

  const [dateFrom, setDateFrom] = useState(monthStart);
  const [dateTo, setDateTo] = useState(today);
  const [items, setItems] = useState<PointsLog[]>([]);
  const [staffName, setStaffName] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!staffId) return;
    setLoading(true);
    try {
      const r = await incentiveApi.getStaffHistory(staffId, dateFrom, dateTo);
      setItems(r.items);
      if (r.items.length > 0) setStaffName(r.items[0].staff_name || null);
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Failed to load';
      toast.error(typeof msg === 'string' ? msg : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, [staffId, dateFrom, dateTo, toast]);

  useEffect(() => { refresh(); }, [refresh]);

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <Link to="/incentive/leaderboard" className="inline-flex items-center gap-1 text-sm text-gray-600 hover:text-gray-900 mb-2">
            <ArrowLeft className="w-4 h-4" /> Leaderboard
          </Link>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <History className="w-6 h-6 text-bv-red-500" />
            {staffName || staffId}
          </h1>
          <p className="text-xs text-gray-400 font-mono mt-1">{staffId}</p>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
            className="px-3 py-2 border border-gray-300 rounded text-sm"
          />
          <span className="text-gray-400">→</span>
          <input
            type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
            className="px-3 py-2 border border-gray-300 rounded text-sm"
          />
          <button
            type="button" onClick={refresh} disabled={loading}
            className="btn-secondary inline-flex items-center gap-2"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </button>
        </div>
      </div>

      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200 text-xs uppercase tracking-wider text-gray-600">
              <tr>
                <th className="px-3 py-2 text-left">Date</th>
                {CATEGORY_KEYS.map(k => (
                  <th key={k} className="px-2 py-2 text-center capitalize">
                    {k.replace('_', ' ')}
                  </th>
                ))}
                <th className="px-3 py-2 text-center bg-gray-100">Total</th>
                <th className="px-3 py-2 text-center bg-gray-100">Elig</th>
                <th className="px-3 py-2 text-center">Visufit gate</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {items.length === 0 ? (
                <tr>
                  <td colSpan={CATEGORY_KEYS.length + 4} className="px-3 py-12 text-center text-gray-500">
                    {loading ? <Loader2 className="w-5 h-5 animate-spin inline-block mr-2" /> : null}
                    {loading ? 'Loading…' : 'No points logged in this range.'}
                  </td>
                </tr>
              ) : items.map(row => (
                <tr key={row.log_id}>
                  <td className="px-3 py-2 text-gray-700 whitespace-nowrap">{row.date_str}</td>
                  {CATEGORY_KEYS.map(k => (
                    <td key={k} className="px-2 py-2 text-center text-gray-700">{(row as any)[k]}</td>
                  ))}
                  <td className="px-3 py-2 text-center font-semibold bg-gray-50/50">{row.total}</td>
                  <td className="px-3 py-2 text-center bg-gray-50/50">
                    <EligibilityChip value={row.eligibility} />
                  </td>
                  <td className="px-3 py-2 text-center">
                    {row.visufit_gate_applied ? (
                      <span className="text-xs px-2 py-0.5 rounded-full border bg-rose-50 text-rose-700 border-rose-200">
                        Applied
                      </span>
                    ) : (
                      <span className="text-xs text-gray-400">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export default PointsHistoryPage;
