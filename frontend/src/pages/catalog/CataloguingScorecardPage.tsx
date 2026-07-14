// ============================================================================
// IMS 2.0 - Cataloguing Scorecard + QC review (attribution phase 2)
// ============================================================================
// Owner requirement: per-user cataloguing performance (volume, approvals,
// corrections received, QC error rate) plus a random-sample QC review loop.
// Manager-ladder gated (route guard in App.tsx mirrors the backend rbac rows).
// Muted house theme: neutral gray; green/amber/red only as -50/-700 semantic
// accents (QC error rate + verdict chips).

import { Fragment, useCallback, useEffect, useState } from 'react';
import {
  Loader2,
  RefreshCw,
  BarChart3,
  ClipboardCheck,
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Package,
  X,
} from 'lucide-react';
// Import DIRECTLY from the modules (not the api barrel) — TS2614.
import { productApi, type ScorecardRow } from '../../services/api/products';
import {
  cataloguingQcApi,
  QC_ERROR_FIELDS,
  QC_ERROR_FIELD_LABELS,
  type QcErrorField,
  type QcSampleItem,
  type QcBatchSummary,
} from '../../services/api/cataloguingQc';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import clsx from 'clsx';

const WINDOWS = [7, 30, 90] as const;

type SortKey = 'created' | 'approvals' | 'corrections' | 'error_rate';

/** "COLORED_CONTACT_LENS" -> "Colored contact lens" */
function prettyCategory(cat: string): string {
  const s = String(cat || '').replace(/_/g, ' ').toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

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
  const { user } = useAuth();
  const toast = useToast();

  const [activeTab, setActiveTab] = useState<'scorecard' | 'qc'>('scorecard');

  // ---- Scorecard state -----------------------------------------------------
  const [days, setDays] = useState<number>(30);
  const [rows, setRows] = useState<ScorecardRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>('created');
  const [sortDesc, setSortDesc] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [recent, setRecent] = useState<Record<string, RecentCreation[]>>({});

  // ---- QC state --------------------------------------------------------------
  const [qcItems, setQcItems] = useState<QcSampleItem[]>([]);
  const [qcBatches, setQcBatches] = useState<QcBatchSummary[]>([]);
  const [qcLoading, setQcLoading] = useState(false);
  const [qcStatusFilter, setQcStatusFilter] = useState<'ALL' | 'PENDING' | 'REVIEWED'>('PENDING');
  const [genDays, setGenDays] = useState(7);
  const [genPerUser, setGenPerUser] = useState(10);
  const [showGenConfirm, setShowGenConfirm] = useState(false);
  const [generating, setGenerating] = useState(false);
  // Inline error panel: which item is being marked ERROR + its draft state.
  const [errorPanel, setErrorPanel] = useState<string | null>(null);
  const [errorFields, setErrorFields] = useState<QcErrorField[]>([]);
  const [errorNote, setErrorNote] = useState('');
  const [savingVerdict, setSavingVerdict] = useState<string | null>(null);

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

  const loadQc = useCallback(async () => {
    setQcLoading(true);
    try {
      const res = await cataloguingQcApi.list(
        qcStatusFilter === 'ALL' ? undefined : { status: qcStatusFilter },
      );
      setQcItems(res.items || []);
      setQcBatches(res.batches || []);
    } catch {
      setQcItems([]);
      setQcBatches([]);
    } finally {
      setQcLoading(false);
    }
  }, [qcStatusFilter]);

  useEffect(() => {
    if (activeTab === 'qc') loadQc();
  }, [activeTab, loadQc]);

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

  // ---- QC actions ----------------------------------------------------------
  const runGenerate = async () => {
    setGenerating(true);
    try {
      const res = await cataloguingQcApi.generate({ days: genDays, per_user: genPerUser });
      if (res.total_items === 0) {
        toast.info('Nothing new to sample — no unreviewed creations in the window.');
      } else {
        toast.success(
          `Sampled ${res.total_items} product${res.total_items === 1 ? '' : 's'} across ${res.cataloguers.length} cataloguer${res.cataloguers.length === 1 ? '' : 's'}`,
        );
      }
      setShowGenConfirm(false);
      setQcStatusFilter('PENDING');
      await loadQc();
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        'Failed to generate the QC sample.';
      toast.error(typeof msg === 'string' ? msg : 'Failed to generate the QC sample.');
    } finally {
      setGenerating(false);
    }
  };

  const submitVerdict = async (
    item: QcSampleItem,
    verdict: 'OK' | 'ERROR',
    fields?: QcErrorField[],
    note?: string,
  ) => {
    setSavingVerdict(item.item_id);
    try {
      await cataloguingQcApi.verdict(item.item_id, {
        verdict,
        ...(verdict === 'ERROR' ? { error_fields: fields || [] } : {}),
        ...(note && note.trim() ? { note: note.trim() } : {}),
      });
      toast.success(verdict === 'OK' ? 'Marked OK' : 'Error recorded');
      setErrorPanel(null);
      setErrorFields([]);
      setErrorNote('');
      await loadQc();
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        'Failed to save the verdict.';
      toast.error(typeof msg === 'string' ? msg : 'Failed to save the verdict.');
    } finally {
      setSavingVerdict(null);
    }
  };

  // Pending items grouped by cataloguer (name asc, stable within group).
  const grouped: Array<{ cataloguer: string; items: QcSampleItem[] }> = [];
  {
    const byName: Record<string, QcSampleItem[]> = {};
    for (const it of qcItems) {
      const key = it.cataloguer_name || it.cataloguer_id;
      (byName[key] = byName[key] || []).push(it);
    }
    for (const name of Object.keys(byName).sort()) {
      grouped.push({ cataloguer: name, items: byName[name] });
    }
  }

  const newestBatch = qcBatches[0];

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
            Who catalogued what, how fast, and how cleanly — plus random-sample QC review.
          </p>
        </div>
        <button
          onClick={() => (activeTab === 'scorecard' ? loadScorecard() : loadQc())}
          disabled={loading || qcLoading}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-50"
        >
          {loading || qcLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
          Refresh
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-200">
        {(
          [
            { id: 'scorecard', label: 'Scorecard', icon: BarChart3 },
            { id: 'qc', label: 'QC review', icon: ClipboardCheck },
          ] as const
        ).map((t) => {
          const TabIcon = t.icon;
          return (
            <button
              key={t.id}
              onClick={() => setActiveTab(t.id)}
              className={clsx(
                'inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
                activeTab === t.id
                  ? 'border-gray-900 text-gray-900'
                  : 'border-transparent text-gray-500 hover:text-gray-700',
              )}
            >
              <TabIcon className="w-4 h-4" />
              {t.label}
            </button>
          );
        })}
      </div>

      {/* ================= Scorecard tab ================= */}
      {activeTab === 'scorecard' && (
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
                            title={`${r.corrections_classified} field-classified (pricing/stock edits never count) + ${r.corrections_approximate} approximate (online-store catalog edits, fields unknown until PR #911 adds history there)`}
                          >
                            {r.corrections_received}
                            {r.corrections_approximate > 0 && (
                              <span className="text-xs text-gray-400 ml-1">~</span>
                            )}
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
            Product edits are field-classified — pricing / stock / active-flag changes never
            count. Values marked ~ additionally include online-store catalog edits, whose
            exact fields the audit trail cannot see yet (field history there lands with PR #911).
          </p>
        </div>
      )}

      {/* ================= QC tab ================= */}
      {activeTab === 'qc' && (
        <div className="space-y-4">
          {/* Controls */}
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <label htmlFor="qc-days" className="block text-xs font-medium text-gray-500 uppercase mb-1">Window (days)</label>
              <input
                id="qc-days"
                type="number"
                min={1}
                max={90}
                value={genDays}
                onChange={(e) => setGenDays(Math.max(1, Math.min(90, Number(e.target.value) || 7)))}
                className="w-24 px-3 py-1.5 border border-gray-300 rounded-lg text-sm"
              />
            </div>
            <div>
              <label htmlFor="qc-per-user" className="block text-xs font-medium text-gray-500 uppercase mb-1">Per user</label>
              <input
                id="qc-per-user"
                type="number"
                min={1}
                max={50}
                value={genPerUser}
                onChange={(e) => setGenPerUser(Math.max(1, Math.min(50, Number(e.target.value) || 10)))}
                className="w-24 px-3 py-1.5 border border-gray-300 rounded-lg text-sm"
              />
            </div>
            <button
              onClick={() => setShowGenConfirm(true)}
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold bg-gray-900 text-white hover:bg-gray-800"
            >
              <ClipboardCheck className="w-4 h-4" /> Generate QC sample
            </button>
            <div className="ml-auto flex items-center gap-1.5">
              {(['PENDING', 'REVIEWED', 'ALL'] as const).map((s) => (
                <button
                  key={s}
                  onClick={() => setQcStatusFilter(s)}
                  className={clsx('ims-chip', qcStatusFilter === s && 'ims-chip--on')}
                >
                  {s === 'ALL' ? 'All' : s === 'PENDING' ? 'Pending' : 'Reviewed'}
                </button>
              ))}
            </div>
          </div>

          {/* Batch progress */}
          {newestBatch && (
            <div className="text-sm text-gray-600">
              Latest batch: <span className="font-medium text-gray-900">{newestBatch.reviewed} of {newestBatch.total} reviewed</span>
              {qcBatches.length > 1 && (
                <span className="text-xs text-gray-400 ml-2">({qcBatches.length} batches on record)</span>
              )}
            </div>
          )}

          {/* Items grouped by cataloguer */}
          <div className="space-y-4">
            {qcLoading ? (
              <div className="flex items-center justify-center py-12 bg-white border border-gray-200 rounded-xl">
                <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
              </div>
            ) : grouped.length === 0 ? (
              <div className="text-center py-12 text-gray-500 bg-white border border-gray-200 rounded-xl">
                <ClipboardCheck className="w-12 h-12 mx-auto mb-2 opacity-50" />
                <p>No {qcStatusFilter === 'ALL' ? '' : qcStatusFilter.toLowerCase() + ' '}QC items. Generate a sample to start reviewing.</p>
              </div>
            ) : (
              grouped.map((g) => (
                <div key={g.cataloguer} className="bg-white border border-gray-200 rounded-xl overflow-hidden">
                  <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-200 flex items-center justify-between">
                    <p className="text-sm font-semibold text-gray-800">{g.cataloguer}</p>
                    <p className="text-xs text-gray-500">
                      {g.items.filter((i) => i.status === 'REVIEWED').length} / {g.items.length} reviewed
                    </p>
                  </div>
                  <div className="divide-y divide-gray-100">
                    {g.items.map((item) => {
                      const mine = item.cataloguer_id === user?.id;
                      const busy = savingVerdict === item.item_id;
                      return (
                        <div key={item.item_id} className="px-4 py-3">
                          <div className="flex items-start gap-3">
                            {item.image_url ? (
                              <img
                                src={item.image_url}
                                alt={item.product_name}
                                loading="lazy"
                                referrerPolicy="no-referrer"
                                className="w-10 h-10 rounded-md border border-gray-200 object-contain bg-white flex-shrink-0"
                              />
                            ) : (
                              <div className="w-10 h-10 rounded-md border border-gray-100 bg-gray-50 flex items-center justify-center flex-shrink-0">
                                <Package className="w-4 h-4 text-gray-300" />
                              </div>
                            )}
                            <div className="min-w-0 flex-1">
                              <p className="font-medium text-gray-900 truncate">{item.product_name || item.product_id}</p>
                              <p className="text-xs text-gray-500">
                                {item.sku && <span className="font-mono mr-2">{item.sku}</span>}
                                {prettyCategory(item.category || '')}
                              </p>
                              {mine && item.status === 'PENDING' && (
                                <p className="text-xs text-amber-700 mt-1">
                                  You catalogued this — another manager must review it.
                                </p>
                              )}
                            </div>
                            {item.status === 'REVIEWED' ? (
                              <div className="text-right flex-shrink-0">
                                <span
                                  className={clsx(
                                    'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium',
                                    item.verdict === 'OK'
                                      ? 'bg-green-50 text-green-700'
                                      : 'bg-red-50 text-red-700',
                                  )}
                                >
                                  {item.verdict === 'OK' ? (
                                    <CheckCircle2 className="w-3 h-3" />
                                  ) : (
                                    <AlertTriangle className="w-3 h-3" />
                                  )}
                                  {item.verdict}
                                </span>
                                <p className="text-xs text-gray-400 mt-1">by {item.reviewed_by_name || item.reviewed_by}</p>
                                {item.verdict === 'ERROR' && (item.error_fields || []).length > 0 && (
                                  <p className="text-xs text-red-700 mt-0.5">
                                    {(item.error_fields || [])
                                      .map((f) => QC_ERROR_FIELD_LABELS[f as QcErrorField] || f)
                                      .join(', ')}
                                  </p>
                                )}
                                {item.note && <p className="text-xs text-gray-500 mt-0.5 max-w-[240px]">{item.note}</p>}
                              </div>
                            ) : (
                              <div className="flex items-center gap-1.5 flex-shrink-0">
                                <button
                                  disabled={mine || busy}
                                  onClick={() => submitVerdict(item, 'OK')}
                                  className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium bg-green-50 text-green-700 hover:bg-green-100 disabled:opacity-40 disabled:cursor-not-allowed"
                                >
                                  {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <CheckCircle2 className="w-3 h-3" />}
                                  OK
                                </button>
                                <button
                                  disabled={mine || busy}
                                  onClick={() => {
                                    setErrorPanel(errorPanel === item.item_id ? null : item.item_id);
                                    setErrorFields([]);
                                    setErrorNote('');
                                  }}
                                  className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium bg-red-50 text-red-700 hover:bg-red-100 disabled:opacity-40 disabled:cursor-not-allowed"
                                >
                                  <AlertTriangle className="w-3 h-3" /> Error
                                </button>
                              </div>
                            )}
                          </div>

                          {/* Inline error panel */}
                          {errorPanel === item.item_id && item.status === 'PENDING' && (
                            <div className="mt-3 p-3 rounded-lg bg-gray-50 border border-gray-200">
                              <p className="text-xs font-medium text-gray-600 mb-2">What is wrong? (tick all that apply)</p>
                              <div className="grid grid-cols-2 tablet:grid-cols-4 gap-1.5 mb-3">
                                {QC_ERROR_FIELDS.map((f) => (
                                  <label key={f} className="inline-flex items-center gap-1.5 text-sm text-gray-700">
                                    <input
                                      type="checkbox"
                                      checked={errorFields.includes(f)}
                                      onChange={(e) =>
                                        setErrorFields((prev) =>
                                          e.target.checked ? [...prev, f] : prev.filter((x) => x !== f),
                                        )
                                      }
                                      className="rounded border-gray-300"
                                    />
                                    {QC_ERROR_FIELD_LABELS[f]}
                                  </label>
                                ))}
                              </div>
                              <div className="flex items-center gap-2">
                                <input
                                  type="text"
                                  value={errorNote}
                                  onChange={(e) => setErrorNote(e.target.value)}
                                  placeholder="Note (optional)"
                                  className="flex-1 px-3 py-1.5 border border-gray-300 rounded-lg text-sm"
                                />
                                <button
                                  disabled={savingVerdict === item.item_id}
                                  onClick={() => submitVerdict(item, 'ERROR', errorFields, errorNote)}
                                  className="px-4 py-1.5 rounded-lg text-xs font-semibold bg-red-700 text-white hover:bg-red-800 disabled:opacity-50"
                                >
                                  Record error
                                </button>
                                <button
                                  onClick={() => setErrorPanel(null)}
                                  className="px-3 py-1.5 rounded-lg text-xs font-medium bg-gray-100 text-gray-600 hover:bg-gray-200"
                                >
                                  Cancel
                                </button>
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* Generate confirm dialog */}
      {showGenConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={() => setShowGenConfirm(false)}>
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-sm" onClick={(e) => e.stopPropagation()}>
            <div className="px-5 py-4 border-b border-gray-200 flex items-center justify-between">
              <h2 className="font-semibold text-gray-900">Generate QC sample?</h2>
              <button onClick={() => setShowGenConfirm(false)} className="text-gray-500 hover:text-gray-700" aria-label="Close" title="Close">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-5 text-sm text-gray-600">
              Up to <span className="font-semibold text-gray-900">{genPerUser}</span> random products per cataloguer
              from the last <span className="font-semibold text-gray-900">{genDays}</span> day{genDays === 1 ? '' : 's'} will
              be queued for review. Items already pending review are skipped.
            </div>
            <div className="px-5 py-3 border-t border-gray-200 flex justify-end gap-2">
              <button
                onClick={() => setShowGenConfirm(false)}
                className="px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 rounded-lg"
              >
                Cancel
              </button>
              <button
                onClick={runGenerate}
                disabled={generating}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold bg-gray-900 text-white hover:bg-gray-800 disabled:opacity-50"
              >
                {generating && <Loader2 className="w-4 h-4 animate-spin" />}
                Generate
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
