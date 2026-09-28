// ============================================================================
// IMS 2.0 - Cataloguing Scorecard (attribution phase 2)
// ============================================================================
// Owner requirement: per-user cataloguing performance (volume, approvals,
// corrections received, QC error rate). The random-sample QC review loop
// lives on its own page, /catalog/qc (CataloguingQcPage).
// Manager-ladder gated (route guard in routes/catalogRoutes.tsx mirrors the
// backend rbac rows). Muted house theme: neutral gray; green/amber/red only
// as -50/-700 semantic accents (QC error rate).

import { Fragment, useCallback, useEffect, useState } from 'react';
import {
  Loader2,
  RefreshCw,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Package,
} from 'lucide-react';
// Import DIRECTLY from the modules (not the api barrel) — TS2614.
import { productApi, type ScorecardRow } from '../../../services/api/products';
import { prettyCategory } from './shared';
import clsx from 'clsx';

const WINDOWS = [7, 30, 90] as const;

type SortKey = 'created' | 'approvals' | 'corrections' | 'error_rate';

/** Top categories as a compact "Frames 40 · Sunglasses 12" line. */
function topCategories(coverage: Record<string, number>, max = 3): string {
  return Object.entries(coverage || {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, max)
    .map(([cat, n]) => `${prettyCategory(cat)} ${n}`)
    .join(' · ');
}

function errorRateTone(rate: number, sampled: number): string {
  if (!sampled) return 'text-gray-400';
  if (rate > 40) return 'bg-red-50 text-red-700';
  if (rate > 20) return 'bg-amber-50 text-amber-700';
  return 'bg-green-50 text-green-700';
}

interface RecentCreation {
  product_id: string;
  sku?: string;
  brand?: string;
  model?: string;
  category?: string;
  created_at?: string;
}

export default function CataloguingScorecardPage() {
  // ---- Scorecard state -----------------------------------------------------
  const [days, setDays] = useState<number>(30);
  const [rows, setRows] = useState<ScorecardRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>('created');
  const [sortDesc, setSortDesc] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [recent, setRecent] = useState<Record<string, RecentCreation[]>>({});

  const loadScorecard = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await productApi.getCataloguingScorecard(days);
      setRows(res.rows || []);
    } catch {
      setError('Failed to load the scorecard. Please try again.');
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    loadScorecard();
  }, [loadScorecard]);

  // ---- Scorecard sorting -----------------------------------------------------
  const sortValue = (r: ScorecardRow, key: SortKey): number => {
    if (key === 'created') return r.created_count;
    if (key === 'approvals') return r.approvals;
    if (key === 'corrections') return r.corrections_received;
    return r.qc.sampled ? r.qc.error_rate : -1;
  };
  const sortedRows = [...rows].sort((a, b) => {
    const d = sortValue(b, sortKey) - sortValue(a, sortKey);
    return sortDesc ? d : -d;
  });
  const clickSort = (key: SortKey) => {
    if (key === sortKey) setSortDesc((v) => !v);
    else {
      setSortKey(key);
      setSortDesc(true);
    }
  };

  const toggleExpand = async (uid: string) => {
    const next = expanded === uid ? null : uid;
    setExpanded(next);
    if (next && !recent[uid]) {
      try {
        const res = await productApi.getProducts({
          created_by: uid,
          limit: 10,
          is_active: 'all',
        });
        setRecent((m) => ({ ...m, [uid]: (res?.products || []) as RecentCreation[] }));
      } catch {
        setRecent((m) => ({ ...m, [uid]: [] }));
      }
    }
  };

  const sortHeader = (label: string, key: SortKey, align: string = 'text-right') => (
    <th
      className={clsx('px-4 py-3 text-xs font-medium text-gray-500 uppercase cursor-pointer select-none whitespace-nowrap', align)}
      onClick={() => clickSort(key)}
      title="Click to sort"
    >
      {label}
      {sortKey === key && <span className="ml-1">{sortDesc ? '↓' : '↑'}</span>}
    </th>
  );

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Cataloguing scorecard</h1>
          <p className="text-sm text-gray-500 mt-1">
            Who catalogued what, how fast, and how cleanly.
          </p>
        </div>
        <button
          onClick={loadScorecard}
          disabled={loading}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-50"
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
          Refresh
        </button>
      </div>

      <div className="space-y-4">
        {/* Window selector */}
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-gray-500 uppercase">Window</span>
          {WINDOWS.map((w) => (
            <button
              key={w}
              onClick={() => setDays(w)}
              className={clsx('ims-chip', days === w && 'ims-chip--on')}
            >
              {w} days
            </button>
          ))}
        </div>

        {error && (
          <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 text-red-700 text-sm">
            <AlertTriangle className="w-4 h-4" /> {error}
            <button onClick={loadScorecard} className="ml-auto underline">Retry</button>
          </div>
        )}

        <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
            </div>
          ) : sortedRows.length === 0 ? (
            <div className="text-center py-12 text-gray-500">
              <Package className="w-12 h-12 mx-auto mb-2 opacity-50" />
              <p>No cataloguing activity in the last {days} days</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Person</th>
                    {sortHeader('Catalogued', 'created')}
                    {sortHeader('Approvals', 'approvals')}
                    {sortHeader('Corrections', 'corrections')}
                    {sortHeader('QC error rate', 'error_rate', 'text-center')}
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Top categories</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {sortedRows.map((r) => (
                    <Fragment key={r.user_id}>
                      <tr
                        className="hover:bg-gray-50 cursor-pointer"
                        onClick={() => toggleExpand(r.user_id)}
                      >
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            {expanded === r.user_id ? (
                              <ChevronDown className="w-4 h-4 text-gray-400" />
                            ) : (
                              <ChevronRight className="w-4 h-4 text-gray-400" />
                            )}
                            <div>
                              <p className="font-medium text-gray-900">{r.name}</p>
                              {r.created_today > 0 && (
                                <p className="text-xs text-gray-400">{r.created_today} today</p>
                              )}
                            </div>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-right">
                          <span className="font-medium text-gray-900">{r.created_count}</span>
                          <span className="text-xs text-gray-400 ml-1.5">{r.per_day_rate}/day</span>
                        </td>
                        <td className="px-4 py-3 text-right text-sm text-gray-700">{r.approvals}</td>
                        <td
                          className="px-4 py-3 text-right text-sm text-gray-700"
                          title="Field-classified — pricing / stock / active-flag edits never count"
                        >
                          {r.corrections_received}
                        </td>
                        <td className="px-4 py-3 text-center">
                          {r.qc.sampled ? (
                            <span
                              className={clsx(
                                'inline-flex px-2 py-0.5 rounded-full text-xs font-medium',
                                errorRateTone(r.qc.error_rate, r.qc.sampled),
                              )}
                              title={`${r.qc.errors} error${r.qc.errors === 1 ? '' : 's'} in ${r.qc.sampled} reviewed sample${r.qc.sampled === 1 ? '' : 's'}`}
                            >
                              {r.qc.error_rate}%
                            </span>
                          ) : (
                            <span className="text-xs text-gray-400" title="No reviewed QC samples in this window">-</span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-600">
                          {topCategories(r.category_coverage) || <span className="text-gray-400">-</span>}
                        </td>
                      </tr>
                      {expanded === r.user_id && (
                        <tr className="bg-gray-50/60">
                          <td colSpan={6} className="px-6 py-4">
                            <div className="grid grid-cols-1 tablet:grid-cols-2 gap-6">
                              <div>
                                <p className="text-xs font-medium text-gray-500 uppercase mb-2">Category breakdown</p>
                                <div className="flex flex-wrap gap-1.5">
                                  {Object.entries(r.category_coverage)
                                    .sort((a, b) => b[1] - a[1])
                                    .map(([cat, n]) => (
                                      <span
                                        key={cat}
                                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-gray-100 text-gray-700 text-xs"
                                      >
                                        {prettyCategory(cat)}
                                        <span className="font-semibold">{n}</span>
                                      </span>
                                    ))}
                                  {Object.keys(r.category_coverage).length === 0 && (
                                    <span className="text-xs text-gray-400">No creations in window</span>
                                  )}
                                </div>
                              </div>
                              <div>
                                <p className="text-xs font-medium text-gray-500 uppercase mb-2">Recent creations</p>
                                {!recent[r.user_id] ? (
                                  <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
                                ) : recent[r.user_id].length === 0 ? (
                                  <p className="text-xs text-gray-400">Nothing found</p>
                                ) : (
                                  <ul className="space-y-1">
                                    {recent[r.user_id].map((p) => (
                                      <li key={p.product_id} className="text-sm text-gray-700">
                                        <span className="font-mono text-xs text-gray-400 mr-2">{p.sku}</span>
                                        {[p.brand, p.model].filter(Boolean).join(' ')}
                                        <span className="text-xs text-gray-400 ml-2">
                                          {prettyCategory(p.category || '')}
                                        </span>
                                      </li>
                                    ))}
                                  </ul>
                                )}
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        <p className="text-xs text-gray-400">
          Corrections = products edited by a different user within 30 days of creation.
          Every edit is field-classified — pricing / stock / active-flag changes never count.
        </p>
      </div>
    </div>
  );
}
