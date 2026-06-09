// ============================================================================
// IMS 2.0 - N3 Footfall + conversion % (manual) page
// ============================================================================
// Replaces the hollow `pos/footfall` shell. Per-staff editable walk-in count,
// auto-computed conversion % (read-only, from the SC conversion-feed seam),
// walkouts today (read-only), a footfall capture status badge
// (PENDING / PARTIAL / COMPLETE), a date picker (today default; past dates for
// correction; future blocked), and a manager aggregate top-up for
// unattributed browse-and-leave customers.
//
// This page is READ-MOSTLY + a small manual write. It NEVER touches POS /
// order creation / payment. Conversion % is auto-computed -- never hand-edited
// here.
//
// Restrained / executive UI: neutral grays + a single bv-red accent for the
// primary action; status uses TEXT colour only (no bright card fills); amber
// text for PARTIAL / missing, green for COMPLETE, gray for PENDING.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowLeft, RefreshCw, Loader2, Plus, Users, Save, AlertCircle, Check,
} from 'lucide-react';
import { walkoutsApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import type {
  WalkinStatusResponse,
  ConversionFeedRow,
  PerStaffCard,
  FootfallEntryStatus,
} from '../../types';

const EDIT_ROLES = new Set([
  'SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER',
]);

function hasAnyRole(
  roles: readonly string[] | undefined,
  activeRole: string | undefined,
  allowed: Set<string>,
): boolean {
  if (activeRole && allowed.has(activeRole)) return true;
  if (!roles) return false;
  return roles.some(r => allowed.has(r));
}

function todayIso(): string {
  // Store-local (IST) day; the server validates against IST too.
  const ist = new Date(Date.now() + (5 * 60 + 30) * 60 * 1000);
  return ist.toISOString().slice(0, 10);
}

interface StaffRow {
  staff_id: string;
  name: string;
  walk_ins: number | null;   // null = no entry yet
  walkouts: number;
  conversion: number | null; // null = unscored (no footfall)
  footfall_missing: boolean;
}

const STATUS_TEXT: Record<FootfallEntryStatus, string> = {
  PENDING: 'text-gray-400',
  PARTIAL: 'text-amber-700',
  COMPLETE: 'text-green-700',
};

const STATUS_LABEL: Record<FootfallEntryStatus, string> = {
  PENDING: 'Pending',
  PARTIAL: 'Partial',
  COMPLETE: 'Complete',
};

export function FootfallPage() {
  const { user } = useAuth();
  const toast = useToast();

  const userRoles = (user as any)?.roles as string[] | undefined;
  const activeRole = (user as any)?.activeRole as string | undefined;
  const canEdit = hasAnyRole(userRoles, activeRole, EDIT_ROLES);
  const isToday = (d: string) => d === todayIso();

  const [date, setDate] = useState<string>(todayIso());
  const [status, setStatus] = useState<WalkinStatusResponse | null>(null);
  const [rows, setRows] = useState<StaffRow[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [savingId, setSavingId] = useState<string | null>(null);
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Manager aggregate topup (unattributed browse-and-leave).
  const [topupOpen, setTopupOpen] = useState(false);
  const [topupDelta, setTopupDelta] = useState(1);
  const [topupReason, setTopupReason] = useState('');
  const [topupBusy, setTopupBusy] = useState(false);

  const sid = user?.activeStoreId || undefined;

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    setRowErrors({});
    try {
      const [st, feed, perStaff] = await Promise.all([
        walkoutsApi.walkinsStatus(sid, date),
        walkoutsApi.conversionFeed(sid, date),
        walkoutsApi.dashboardPerStaff(sid),
      ]);
      setStatus(st);

      const feedById: Record<string, ConversionFeedRow> = {};
      feed.forEach(r => { feedById[r.sales_person_id] = r; });
      const nameById: Record<string, string> = {};
      (perStaff.items || []).forEach((c: PerStaffCard) => {
        nameById[c.sales_person_id] = c.sales_person_name || c.sales_person_id;
      });
      feed.forEach(r => {
        if (!nameById[r.sales_person_id]) {
          nameById[r.sales_person_id] = r.name || r.sales_person_id;
        }
      });

      const entered = new Map<string, number>(
        st.staff_with_data.map(s => [s.staff_id, s.walk_ins]),
      );
      const ids = Array.from(
        new Set<string>([
          ...st.staff_with_data.map(s => s.staff_id),
          ...st.staff_missing,
        ]),
      );
      const built: StaffRow[] = ids.map(id => {
        const f = feedById[id];
        const walkIns = entered.has(id) ? (entered.get(id) as number) : null;
        return {
          staff_id: id,
          name: nameById[id] || id,
          walk_ins: walkIns,
          walkouts: f?.walkouts_today ?? 0,
          conversion: f?.conversion_score ?? null,
          footfall_missing: walkIns === null,
        };
      });
      built.sort((a, b) => a.name.localeCompare(b.name));
      setRows(built);
      setDrafts(Object.fromEntries(
        built.map(r => [r.staff_id, r.walk_ins === null ? '' : String(r.walk_ins)]),
      ));
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Failed to load footfall';
      setError(typeof msg === 'string' ? msg : 'Failed to load footfall');
    } finally {
      setLoading(false);
    }
  }, [sid, date]);

  useEffect(() => { refresh(); }, [refresh]);

  const saveRow = async (staffId: string) => {
    const raw = (drafts[staffId] ?? '').trim();
    if (raw === '') {
      setRowErrors(p => ({ ...p, [staffId]: 'Enter a number (0 is valid)' }));
      return;
    }
    const n = Number(raw);
    if (!Number.isInteger(n) || n < 0) {
      setRowErrors(p => ({ ...p, [staffId]: 'Whole number >= 0' }));
      return;
    }
    setSavingId(staffId);
    setRowErrors(p => { const c = { ...p }; delete c[staffId]; return c; });
    try {
      await walkoutsApi.walkinsPerStaffUpdate(
        { staff_id: staffId, walk_ins: n, date_str: date }, sid,
      );
      toast.success('Walk-in count saved');
      await refresh();
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Save failed';
      setRowErrors(p => ({
        ...p, [staffId]: typeof msg === 'string' ? msg : 'Save failed',
      }));
    } finally {
      setSavingId(null);
    }
  };

  const submitTopup = async () => {
    if (topupDelta < 1 || !topupReason.trim()) {
      toast.error('Need both a count and a reason');
      return;
    }
    setTopupBusy(true);
    try {
      await walkoutsApi.walkinsManualTopup(
        { delta: topupDelta, reason: topupReason.trim() }, sid,
      );
      toast.success(`+${topupDelta} unattributed walk-in${topupDelta > 1 ? 's' : ''} logged`);
      setTopupOpen(false);
      setTopupDelta(1);
      setTopupReason('');
      await refresh();
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Topup failed';
      toast.error(typeof msg === 'string' ? msg : 'Topup failed');
    } finally {
      setTopupBusy(false);
    }
  };

  const totals = useMemo(() => {
    const walkIns = rows.reduce((a, r) => a + (r.walk_ins ?? 0), 0);
    const walkouts = rows.reduce((a, r) => a + r.walkouts, 0);
    const pct = walkIns > 0
      ? Math.round((100 * (walkIns - walkouts)) / walkIns)
      : null;
    return { walkIns, walkouts, pct };
  }, [rows]);

  const st: FootfallEntryStatus = status?.status ?? 'PENDING';

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <Link
            to="/walkouts/dashboard"
            className="inline-flex items-center gap-1 text-sm text-gray-600 hover:text-gray-900 mb-2"
          >
            <ArrowLeft className="w-4 h-4" /> Walkouts dashboard
          </Link>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <Users className="w-6 h-6 text-bv-red-500" />
            Footfall tracking
          </h1>
          <p className="text-gray-500 text-sm mt-1">
            Daily walk-in count per salesperson. Conversion % auto-computes from
            walk-ins vs walkouts.
          </p>
        </div>
        <div className="flex items-end gap-2">
          <label className="text-sm text-gray-600">
            <span className="block mb-1">Date</span>
            <input
              type="date"
              value={date}
              max={todayIso()}
              onChange={e => setDate(e.target.value || todayIso())}
              className="input-field px-3 py-1.5"
            />
          </label>
          {canEdit && (
            <button
              type="button"
              onClick={() => setTopupOpen(o => !o)}
              className="btn-secondary inline-flex items-center gap-2"
            >
              <Plus className="w-4 h-4" /> Unattributed
            </button>
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

      {/* Status badge */}
      <div className="flex items-center gap-3 text-sm">
        <span className="text-gray-500">Capture status:</span>
        <span className={`font-semibold ${STATUS_TEXT[st]}`}>
          {STATUS_LABEL[st]}
        </span>
        {st !== 'COMPLETE' && status && status.staff_missing.length > 0 && (
          <span className="text-gray-500">
            {status.staff_missing.length} staff missing a walk-in count
          </span>
        )}
        {st === 'COMPLETE' && (
          <Check className="w-4 h-4 text-green-700" />
        )}
      </div>

      {error && (
        <div className="card p-4 bg-amber-50 border border-amber-200 text-sm text-amber-800 flex items-center gap-2">
          <AlertCircle className="w-4 h-4" /> {error}
        </div>
      )}

      {/* Manager aggregate topup */}
      {canEdit && topupOpen && (
        <div className="card p-4 space-y-3">
          <h2 className="text-sm font-semibold text-gray-700">
            Unattributed walk-ins (browse-and-leave)
          </h2>
          <p className="text-xs text-gray-500">
            For customers who only browsed and were not attributed to a specific
            salesperson. Counts toward the store total, not any staff conversion.
          </p>
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-sm text-gray-600">
              <span className="block mb-1">Count</span>
              <input
                type="number"
                min={1}
                max={50}
                value={topupDelta}
                onChange={e => setTopupDelta(Math.max(1, Number(e.target.value) || 1))}
                className="input-field px-3 py-1.5 w-24"
              />
            </label>
            <label className="text-sm text-gray-600 flex-1 min-w-[200px]">
              <span className="block mb-1">Reason</span>
              <input
                type="text"
                value={topupReason}
                onChange={e => setTopupReason(e.target.value)}
                placeholder="e.g. walk-by browsers during the sale"
                className="input-field px-3 py-1.5 w-full"
              />
            </label>
            <button
              type="button"
              onClick={submitTopup}
              disabled={topupBusy}
              className="btn-primary inline-flex items-center gap-2"
            >
              {topupBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
              Add
            </button>
          </div>
        </div>
      )}

      {/* Per-staff table */}
      <section className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="text-left font-medium px-4 py-2.5">Salesperson</th>
              <th className="text-right font-medium px-4 py-2.5 w-40">Walk-ins</th>
              <th className="text-right font-medium px-4 py-2.5 w-28">Walkouts</th>
              <th className="text-right font-medium px-4 py-2.5 w-32">Conversion %</th>
              <th className="text-left font-medium px-4 py-2.5 w-28">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-gray-500">
                  {loading
                    ? <Loader2 className="w-5 h-5 animate-spin mx-auto" />
                    : 'No active salespeople found for this store.'}
                </td>
              </tr>
            ) : rows.map(r => {
              const draft = drafts[r.staff_id] ?? '';
              const dirty = draft !== (r.walk_ins === null ? '' : String(r.walk_ins));
              const rowErr = rowErrors[r.staff_id];
              return (
                <tr key={r.staff_id} className="hover:bg-gray-50">
                  <td className="px-4 py-2.5 text-gray-900">{r.name}</td>
                  <td className="px-4 py-2.5 text-right">
                    {canEdit ? (
                      <div className="flex items-center justify-end gap-1.5">
                        <input
                          type="number"
                          min={0}
                          value={draft}
                          placeholder="—"
                          onChange={e => setDrafts(p => ({ ...p, [r.staff_id]: e.target.value }))}
                          className="input-field px-2 py-1 w-20 text-right"
                        />
                        <button
                          type="button"
                          onClick={() => saveRow(r.staff_id)}
                          disabled={savingId === r.staff_id || !dirty}
                          className="p-1.5 text-bv-red-600 hover:bg-bv-red-50 rounded disabled:opacity-30 disabled:hover:bg-transparent"
                          title="Save"
                        >
                          {savingId === r.staff_id
                            ? <Loader2 className="w-4 h-4 animate-spin" />
                            : <Save className="w-4 h-4" />}
                        </button>
                      </div>
                    ) : (
                      <span className="text-gray-900">
                        {r.walk_ins === null ? '—' : r.walk_ins}
                      </span>
                    )}
                    {rowErr && (
                      <div className="text-xs text-amber-700 mt-1 text-right">{rowErr}</div>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-right text-gray-700">{r.walkouts}</td>
                  <td className="px-4 py-2.5 text-right">
                    {r.footfall_missing ? (
                      <span className="text-gray-400">No data</span>
                    ) : r.conversion === null ? (
                      <span className="text-gray-400">—</span>
                    ) : (
                      <span className="text-gray-900 font-medium">
                        {Math.round((r.conversion / 20) * 100)}%
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2.5">
                    {r.footfall_missing ? (
                      <span className="text-amber-700">Missing</span>
                    ) : (
                      <span className="text-gray-400">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
          {rows.length > 0 && (
            <tfoot className="bg-gray-50 text-gray-700 font-medium">
              <tr>
                <td className="px-4 py-2.5">Store total</td>
                <td className="px-4 py-2.5 text-right">{totals.walkIns}</td>
                <td className="px-4 py-2.5 text-right">{totals.walkouts}</td>
                <td className="px-4 py-2.5 text-right">
                  {totals.pct === null ? '—' : `${totals.pct}%`}
                </td>
                <td className="px-4 py-2.5" />
              </tr>
            </tfoot>
          )}
        </table>
      </section>

      <p className="text-xs text-gray-400">
        Conversion % is computed as (walk-ins - walkouts) / walk-ins and is read
        only here. A salesperson with no walk-in count for {isToday(date) ? 'today' : 'this date'} is
        left unscored (not zero) so the daily scorecard is not silently
        corrupted.
      </p>
    </div>
  );
}

export default FootfallPage;
