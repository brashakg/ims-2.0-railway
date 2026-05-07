// ============================================================================
// IMS 2.0 — MTD Leaderboard (Pune Incentive Module ii, Phase 3)
// ============================================================================
// Per-staff month-to-date performance ranking. Sorted by avg.total
// DESC, tie-broken by days_logged. Surfaces per-category averages so
// managers can see WHERE each staff is scoring well or poorly.

import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft, RefreshCw, Loader2, AlertCircle, Trophy, History } from 'lucide-react';
import { incentiveApi } from '../../services/api';
import type { MTDStaffEntry } from '../../types';
import { EligibilityChip } from './DailyScorecardPage';

const CATEGORY_KEYS = [
  'attendance', 'conversion', 'task', 'visufit', 'punctuality',
  'behaviour', 'kicker_1', 'kicker_2', 'reviews',
] as const;

const CATEGORY_LABELS: Record<typeof CATEGORY_KEYS[number], string> = {
  attendance: 'Att', conversion: 'Conv', task: 'Task', visufit: 'Visu',
  punctuality: 'Punct', behaviour: 'Behav',
  kicker_1: 'K1', kicker_2: 'K2', reviews: 'Rev',
};

const CATEGORY_MAX: Record<typeof CATEGORY_KEYS[number], number> = {
  attendance: 10, conversion: 20, task: 10, visufit: 10, punctuality: 10,
  behaviour: 10, kicker_1: 10, kicker_2: 10, reviews: 10,
};

export function MTDLeaderboardPage() {
  const [items, setItems] = useState<MTDStaffEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState<7 | 30 | 90>(30);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await incentiveApi.getLeaderboard(days);
      setItems(resp.items || []);
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Failed to load';
      setError(typeof msg === 'string' ? msg : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => { refresh(); }, [refresh]);

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <Link to="/incentive" className="inline-flex items-center gap-1 text-sm text-gray-600 hover:text-gray-900 mb-2">
            <ArrowLeft className="w-4 h-4" /> Daily scorecard
          </Link>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <Trophy className="w-6 h-6 text-bv-red-500" />
            MTD leaderboard
          </h1>
          <p className="text-gray-500 text-sm mt-1">
            Last
            <select
              value={days}
              onChange={e => setDays(Number(e.target.value) as 7|30|90)}
              className="mx-2 px-2 py-0.5 text-sm border border-gray-300 rounded"
            >
              <option value={7}>7</option>
              <option value={30}>30</option>
              <option value={90}>90</option>
            </select>
            days, sorted by avg total. Tie-broken by days logged.
          </p>
        </div>
        <button
          type="button"
          onClick={refresh}
          disabled={loading}
          className="btn-secondary inline-flex items-center gap-2"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> Refresh
        </button>
      </div>

      {error && (
        <div className="card p-3 bg-amber-50 border border-amber-200 text-sm text-amber-800 inline-flex items-center gap-2">
          <AlertCircle className="w-4 h-4" /> {error}
        </div>
      )}

      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200 text-xs uppercase tracking-wider text-gray-600">
              <tr>
                <th className="px-3 py-2 text-left">Rank</th>
                <th className="px-3 py-2 text-left">Staff</th>
                <th className="px-3 py-2 text-center">Days</th>
                {CATEGORY_KEYS.map(k => (
                  <th key={k} className="px-2 py-2 text-center" title={`Max ${CATEGORY_MAX[k]}`}>
                    {CATEGORY_LABELS[k]}
                  </th>
                ))}
                <th className="px-3 py-2 text-center bg-gray-100">Total avg</th>
                <th className="px-3 py-2 text-center bg-gray-100">Elig avg</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {items.length === 0 ? (
                <tr>
                  <td colSpan={CATEGORY_KEYS.length + 6} className="px-3 py-12 text-center text-gray-500">
                    {loading ? <Loader2 className="w-5 h-5 animate-spin inline-block mr-2" /> : null}
                    {loading ? 'Loading…' : 'No data yet for this period.'}
                  </td>
                </tr>
              ) : items.map((row, idx) => (
                <tr key={row.staff_id} className={idx === 0 ? 'bg-amber-50/30' : ''}>
                  <td className="px-3 py-2 font-semibold text-gray-700">#{idx + 1}</td>
                  <td className="px-3 py-2">
                    <div className="font-medium text-gray-900">{row.staff_name || row.staff_id}</div>
                    <div className="text-[10px] text-gray-400 font-mono">{row.staff_id}</div>
                  </td>
                  <td className="px-3 py-2 text-center text-gray-700">{row.days_logged}</td>
                  {CATEGORY_KEYS.map(k => (
                    <td key={k} className="px-2 py-2 text-center text-xs">
                      <CategoryBar value={row.avg[k]} max={CATEGORY_MAX[k]} />
                    </td>
                  ))}
                  <td className="px-3 py-2 text-center font-semibold bg-gray-50/50">{row.avg.total.toFixed(1)}</td>
                  <td className="px-3 py-2 text-center bg-gray-50/50">
                    <EligibilityChip value={row.eligibility_avg} />
                  </td>
                  <td className="px-3 py-2 text-right">
                    <Link
                      to={`/incentive/staff/${row.staff_id}`}
                      className="text-xs text-bv-red-600 hover:underline inline-flex items-center gap-1"
                    >
                      <History className="w-3 h-3" /> History
                    </Link>
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

function CategoryBar({ value, max }: { value: number; max: number }) {
  const pct = max ? Math.min(100, (value / max) * 100) : 0;
  const colorCls =
    pct >= 80 ? 'bg-emerald-500' :
    pct >= 60 ? 'bg-blue-500' :
    pct >= 40 ? 'bg-amber-500' :
    'bg-rose-400';
  return (
    <div title={`${value.toFixed(1)} / ${max}`} className="flex flex-col items-center gap-0.5">
      <div className="w-12 h-1.5 bg-gray-100 rounded overflow-hidden">
        <div className={`h-full ${colorCls}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] text-gray-600">{value.toFixed(1)}</span>
    </div>
  );
}

export default MTDLeaderboardPage;
