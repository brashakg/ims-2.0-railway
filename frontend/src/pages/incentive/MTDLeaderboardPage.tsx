// ============================================================================
// IMS 2.0 — MTD Leaderboard (Pune Incentive Module ii, Phase 3 + F33)
// ============================================================================
// Per-staff month-to-date performance ranking. Sorted by avg.total
// DESC, tie-broken by days_logged. Surfaces per-category averages so
// managers can see WHERE each staff is scoring well or poorly.
//
// F33 — gamified display layer (restrained / executive): podium band for
// the top 3, tier chips, earned titles + badges as subtle text chips,
// rank-delta arrows, and a scope toggle (store/area/org) for managers.
// All presentation fields are SERVER-computed; junior roles never receive
// rupee fields (server-side strip).

import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowLeft, RefreshCw, Loader2, AlertCircle, Trophy, History,
  ArrowUp, ArrowDown, Minus,
} from 'lucide-react';
import { incentiveApi } from '../../services/api';
import type { MTDStaffEntry, LeaderboardScope, LeaderboardTier } from '../../types';
import { useAuth } from '../../context/AuthContext';
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

// F33 — roles that may widen the board beyond their own store.
const SCOPE_ROLES = new Set(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER']);

const SCOPE_OPTIONS: Array<{ value: LeaderboardScope; label: string }> = [
  { value: 'store', label: 'Store' },
  { value: 'area', label: 'Area' },
  { value: 'org', label: 'Org' },
];

const BADGE_LABELS: Record<string, string> = {
  eligibility_100: 'Full eligibility',
  logged_every_day: 'Logged every day',
  top_riser: 'Top riser',
  consistent_90: '90+ average',
};

const TIER_STYLES: Record<LeaderboardTier, string> = {
  PODIUM: 'border-gray-900 text-gray-900',
  CONTENDER: 'border-gray-400 text-gray-600',
  BUILDING: 'border-gray-200 text-gray-400',
};

export function MTDLeaderboardPage() {
  const { user } = useAuth();
  const userRoles = ((user as any)?.roles as string[] | undefined) || [];
  const activeRole = (user as any)?.activeRole as string | undefined;
  const canWidenScope = userRoles.some(r => SCOPE_ROLES.has(r))
    || (activeRole ? SCOPE_ROLES.has(activeRole) : false);

  const [items, setItems] = useState<MTDStaffEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState<7 | 30 | 90>(30);
  const [scope, setScope] = useState<LeaderboardScope>('store');

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await incentiveApi.getLeaderboard(days, undefined, scope);
      setItems(resp.items || []);
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Failed to load';
      setError(typeof msg === 'string' ? msg : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, [days, scope]);

  useEffect(() => { refresh(); }, [refresh]);

  const podium = items.slice(0, 3);

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
              aria-label="Leaderboard window in days"
              title="Leaderboard window in days"
            >
              <option value={7}>7</option>
              <option value={30}>30</option>
              <option value={90}>90</option>
            </select>
            days, sorted by avg total. Tie-broken by days logged.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {canWidenScope && (
            <div className="inline-flex rounded border border-gray-300 overflow-hidden" role="group" aria-label="Leaderboard scope">
              {SCOPE_OPTIONS.map(opt => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setScope(opt.value)}
                  className={`px-3 py-1.5 text-xs font-medium ${
                    scope === opt.value
                      ? 'bg-gray-900 text-white'
                      : 'bg-white text-gray-600 hover:bg-gray-50'
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          )}
          <button
            type="button"
            onClick={refresh}
            disabled={loading}
            className="btn-secondary inline-flex items-center gap-2"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="card p-3 bg-amber-50 border border-amber-200 text-sm text-amber-800 inline-flex items-center gap-2">
          <AlertCircle className="w-4 h-4" /> {error}
        </div>
      )}

      {/* F33 — podium band (restrained: neutral cards, single accent on #1) */}
      {podium.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {podium.map((row, idx) => (
            <div
              key={row.staff_id}
              className={`card p-4 border border-gray-200 ${
                idx === 0 ? 'border-t-2 border-t-bv-red-500' : ''
              }`}
            >
              <div className="flex items-start justify-between">
                <span className="text-3xl font-bold text-gray-300 leading-none">
                  {row.rank ?? idx + 1}
                </span>
                <div className="flex items-center gap-2">
                  <RankDelta delta={row.rank_delta} />
                  <TierChip tier={row.tier_label} />
                </div>
              </div>
              <div className="mt-2 font-semibold text-gray-900 truncate">
                {row.staff_name || row.staff_id}
              </div>
              {row.title_earned && (
                <div className="text-xs text-gray-500 mt-0.5">{row.title_earned}</div>
              )}
              <div className="mt-2 flex items-baseline gap-1">
                <span className="text-xl font-bold text-gray-900">{row.avg.total.toFixed(1)}</span>
                <span className="text-[11px] text-gray-400">avg / {row.days_logged}d</span>
              </div>
              <BadgeChips keys={row.badge_keys} className="mt-2" />
            </div>
          ))}
        </div>
      )}

      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200 text-xs uppercase tracking-wider text-gray-600">
              <tr>
                <th className="px-3 py-2 text-left">Rank</th>
                <th className="px-3 py-2 text-left">Staff</th>
                <th className="px-3 py-2 text-left">Tier</th>
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
                  <td colSpan={CATEGORY_KEYS.length + 7} className="px-3 py-12 text-center text-gray-500">
                    {loading ? <Loader2 className="w-5 h-5 animate-spin inline-block mr-2" /> : null}
                    {loading ? 'Loading…' : 'No data yet for this period.'}
                  </td>
                </tr>
              ) : items.map((row, idx) => (
                <tr key={row.staff_id} className={idx === 0 ? 'bg-gray-50/60' : ''}>
                  <td className="px-3 py-2 font-semibold text-gray-700 whitespace-nowrap">
                    #{row.rank ?? idx + 1}
                    <RankDelta delta={row.rank_delta} className="ml-1.5" />
                  </td>
                  <td className="px-3 py-2">
                    <div className="font-medium text-gray-900">{row.staff_name || row.staff_id}</div>
                    <div className="text-[10px] text-gray-400 font-mono">{row.staff_id}</div>
                    {(row.title_earned || (row.badge_keys && row.badge_keys.length > 0)) && (
                      <div className="flex flex-wrap items-center gap-1 mt-1">
                        {row.title_earned && (
                          <span className="text-[10px] text-gray-500">{row.title_earned}</span>
                        )}
                        <BadgeChips keys={row.badge_keys} />
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2"><TierChip tier={row.tier_label} /></td>
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

// F33 — tier chip: neutral outline, no fills (executive restraint).
function TierChip({ tier }: { tier?: LeaderboardTier }) {
  if (!tier) return null;
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded border text-[10px] font-medium uppercase tracking-wide ${TIER_STYLES[tier]}`}>
      {tier}
    </span>
  );
}

// F33 — rank movement vs the previous period. Neutral grays only.
function RankDelta({ delta, className = '' }: { delta?: number | null; className?: string }) {
  if (delta === null || delta === undefined) return null;
  if (delta === 0) {
    return <Minus className={`w-3 h-3 text-gray-300 inline-block ${className}`} aria-label="No rank change" />;
  }
  const up = delta > 0;
  const Icon = up ? ArrowUp : ArrowDown;
  return (
    <span
      className={`inline-flex items-center gap-0.5 text-[10px] font-medium ${up ? 'text-gray-700' : 'text-gray-400'} ${className}`}
      title={`${up ? 'Up' : 'Down'} ${Math.abs(delta)} since previous period`}
    >
      <Icon className="w-3 h-3" />{Math.abs(delta)}
    </span>
  );
}

// F33 — badges as subtle text chips.
function BadgeChips({ keys, className = '' }: { keys?: string[]; className?: string }) {
  if (!keys || keys.length === 0) return null;
  return (
    <span className={`inline-flex flex-wrap gap-1 ${className}`}>
      {keys.map(k => (
        <span
          key={k}
          className="inline-block px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 text-[10px]"
        >
          {BADGE_LABELS[k] || k.replace(/_/g, ' ')}
        </span>
      ))}
    </span>
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
