// ============================================================================
// IMS 2.0 — Daily Scorecard (Pune Incentive Module ii)
// ============================================================================
// Mirrors the Excel "Daily Points Entry" sheet: one row per staff
// member, 9 score cells per row, total + eligibility chip computed
// client-side as the user types (server still re-computes on save).
//
// Conversion column auto-shows "AUTO" badge when the date is today —
// the server will fetch from /walkouts/conversion-feed if the cell is
// left null. Past dates require an explicit number.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Save, Loader2, RefreshCw, AlertCircle, Trash2, BarChart3, History, ChevronRight, Calculator } from 'lucide-react';
import { incentiveApi } from '../../services/api';
import { adminUserApi } from '../../services/api/stores';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import type {
  CreateDailyPointsRequest,
  DailyScores,
  PointsLog,
  IncentiveSettings,
  EligibilityBand,
} from '../../types';

const CATEGORIES: Array<{ key: keyof DailyScores; label: string; max: number; auto?: boolean }> = [
  { key: 'attendance', label: 'Attendance', max: 10 },
  { key: 'conversion', label: 'Conversion', max: 20, auto: true },
  { key: 'task', label: 'Task', max: 10 },
  { key: 'visufit', label: 'Visufit', max: 10 },
  { key: 'punctuality', label: 'Punctuality', max: 10 },
  { key: 'behaviour', label: 'Behaviour', max: 10 },
  { key: 'kicker_1', label: 'Kicker 1', max: 10 },
  { key: 'kicker_2', label: 'Kicker 2', max: 10 },
  { key: 'reviews', label: 'Reviews', max: 10 },
];

const ELEVATED_ROLES = new Set([
  'SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT',
]);

function emptyScores(): DailyScores {
  return {
    attendance: 0, conversion: 0, task: 0, visufit: 0, punctuality: 0,
    behaviour: 0, kicker_1: 0, kicker_2: 0, reviews: 0,
  };
}

function scoreTotal(s: DailyScores): number {
  return [
    s.attendance, s.conversion ?? 0, s.task, s.visufit, s.punctuality,
    s.behaviour, s.kicker_1, s.kicker_2, s.reviews,
  ].reduce((a, b) => a + b, 0);
}

function bandValue(total: number, bands: EligibilityBand[]): number {
  for (const b of bands || []) if (total >= b.min && total < b.max) return b.value;
  return 0;
}

interface StaffRow {
  staff_id: string;
  staff_name: string;
  scores: DailyScores;
  conversionAuto: boolean;        // true → leave null on save (server auto-fills)
  visufitUsage?: number;
  saved?: PointsLog;
  log_id?: string;
}

export function DailyScorecardPage() {
  const { user } = useAuth();
  const toast = useToast();

  const userId = (user as any)?.id || (user as any)?.user_id || '';
  const userRoles = (user as any)?.roles as string[] | undefined;
  const activeRole = (user as any)?.activeRole as string | undefined;
  const canEditAny = (userRoles || []).some(r => ELEVATED_ROLES.has(r))
    || (activeRole && ELEVATED_ROLES.has(activeRole));

  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [staffList, setStaffList] = useState<Array<{ user_id: string; name: string }>>([]);
  const [rows, setRows] = useState<StaffRow[]>([]);
  const [savedByStaff, setSavedByStaff] = useState<Record<string, PointsLog>>({});
  const [settings, setSettings] = useState<IncentiveSettings | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isToday = useMemo(
    () => date === new Date().toISOString().slice(0, 10),
    [date],
  );
  const bands = settings?.eligibility_bands || [];

  // Load staff (managers see whole store; sales staff see only self)
  const loadStaff = useCallback(async () => {
    if (canEditAny) {
      try {
        const resp: any = await (adminUserApi as any).getUsers?.({
          storeId: (user as any)?.activeStoreId,
        });
        const list = resp?.users || resp || [];
        const mapped = (Array.isArray(list) ? list : []).map((u: any) => ({
          user_id: u.user_id || u.id,
          name: u.name || u.full_name || u.username || u.user_id,
        }));
        setStaffList(mapped);
      } catch {
        setStaffList([]);
      }
    } else if (userId) {
      const userName = (user as any)?.name || userId;
      setStaffList([{ user_id: userId, name: userName }]);
    }
  }, [canEditAny, user, userId]);

  const loadDay = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, dayList] = await Promise.all([
        incentiveApi.getSettings(),
        incentiveApi.listDaily(date),
      ]);
      setSettings(s);
      const map: Record<string, PointsLog> = {};
      for (const row of dayList.items) map[row.staff_id] = row;
      setSavedByStaff(map);
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Failed to load';
      setError(typeof msg === 'string' ? msg : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, [date]);

  useEffect(() => { loadStaff(); }, [loadStaff]);
  useEffect(() => { loadDay(); }, [loadDay]);

  // Hydrate `rows` from staffList + savedByStaff every change
  useEffect(() => {
    setRows(staffList.map(s => {
      const saved = savedByStaff[s.user_id];
      const scores: DailyScores = saved
        ? { ...emptyScores(), ...saved, conversion: saved.conversion }
        : emptyScores();
      return {
        staff_id: s.user_id, staff_name: s.name, scores,
        conversionAuto: !saved && isToday,
        visufitUsage: saved?.visufit_usage_pct_mtd ?? undefined,
        saved, log_id: saved?.log_id,
      };
    }));
  }, [staffList, savedByStaff, isToday]);

  const updateRow = (idx: number, patch: Partial<StaffRow>) => {
    setRows(prev => prev.map((r, i) => i === idx ? { ...r, ...patch } : r));
  };
  const updateScore = (idx: number, key: keyof DailyScores, value: number) => {
    setRows(prev => prev.map((r, i) => {
      if (i !== idx) return r;
      const max = CATEGORIES.find(c => c.key === key)?.max ?? 10;
      const clamped = Math.max(0, Math.min(max, value));
      return { ...r, scores: { ...r.scores, [key]: clamped } };
    }));
  };

  const handleSaveAll = async () => {
    if (!settings) return;
    const payloadRows: CreateDailyPointsRequest[] = rows
      .filter(r => !r.saved) // skip already-saved (use delete + re-save instead)
      .map(r => {
        const scores: DailyScores = {
          ...r.scores,
          conversion: r.conversionAuto ? null : r.scores.conversion,
        };
        return {
          date,
          staff_id: r.staff_id,
          scores,
          visufit_usage_pct_mtd: r.visufitUsage ?? null,
        };
      });
    if (payloadRows.length === 0) {
      toast.info('Nothing to save — delete existing rows first to overwrite.');
      return;
    }
    setSaving(true);
    try {
      const resp = await incentiveApi.createBulk({ rows: payloadRows });
      if (resp.failed_count > 0) {
        toast.warning(`${resp.saved_count} saved, ${resp.failed_count} failed (likely duplicates).`);
      } else {
        toast.success(`${resp.saved_count} row${resp.saved_count === 1 ? '' : 's'} saved`);
      }
      await loadDay();
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Save failed';
      toast.error(typeof msg === 'string' ? msg : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteRow = async (logId: string) => {
    const reason = window.prompt('Reason for delete (audit trail)?');
    if (!reason || !reason.trim()) return;
    try {
      await incentiveApi.deleteDaily(logId, reason.trim());
      toast.success('Row deleted — you can re-save now.');
      await loadDay();
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Delete failed';
      toast.error(typeof msg === 'string' ? msg : 'Delete failed');
    }
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <BarChart3 className="w-6 h-6 text-bv-red-500" />
            Daily scorecard
          </h1>
          <p className="text-gray-500 text-sm mt-1">
            9 categories × {staffList.length || 0} staff. Conversion auto-fills from walkouts when you log today's row.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="date"
            value={date}
            onChange={e => setDate(e.target.value)}
            className="px-3 py-2 border border-gray-300 rounded text-sm"
          />
          <Link
            to="/incentive/leaderboard"
            className="btn-secondary inline-flex items-center gap-2"
          >
            <BarChart3 className="w-4 h-4" /> Leaderboard
          </Link>
          <Link
            to="/incentive/payout"
            className="btn-secondary inline-flex items-center gap-2"
          >
            <Calculator className="w-4 h-4" /> Payout
          </Link>
          <button
            type="button"
            onClick={loadDay}
            className="btn-secondary inline-flex items-center gap-2"
            disabled={loading}
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </button>
          <button
            type="button"
            onClick={handleSaveAll}
            disabled={saving || rows.every(r => !!r.saved)}
            className="btn-primary inline-flex items-center gap-2 disabled:opacity-50"
          >
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            Save all
          </button>
        </div>
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
                <th className="px-3 py-2 text-left sticky left-0 bg-gray-50">Staff</th>
                {CATEGORIES.map(c => (
                  <th key={c.key} className="px-3 py-2 text-center" title={`Max ${c.max}`}>
                    {c.label}
                    <div className="text-[10px] text-gray-400 font-normal normal-case">/ {c.max}</div>
                  </th>
                ))}
                <th className="px-3 py-2 text-center bg-gray-100">Total</th>
                <th className="px-3 py-2 text-center bg-gray-100">Eligibility</th>
                <th className="px-3 py-2 text-right">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={CATEGORIES.length + 4} className="px-3 py-12 text-center text-gray-500">
                    {loading ? <Loader2 className="w-5 h-5 animate-spin inline-block mr-2" /> : null}
                    {loading ? 'Loading…' : 'No staff to score for this store.'}
                  </td>
                </tr>
              ) : rows.map((row, idx) => {
                const total = scoreTotal(row.scores);
                const elig = bandValue(total, bands);
                const locked = !!row.saved;
                return (
                  <tr key={row.staff_id} className={locked ? 'bg-emerald-50/30' : ''}>
                    <td className="px-3 py-2 font-medium text-gray-900 sticky left-0 bg-inherit">
                      {row.staff_name}
                      <div className="text-[10px] text-gray-400 font-mono">{row.staff_id}</div>
                    </td>
                    {CATEGORIES.map(c => {
                      const val = (row.scores as any)[c.key] ?? 0;
                      const showAuto =
                        c.auto && row.conversionAuto && !locked && isToday;
                      return (
                        <td key={c.key} className="px-2 py-1.5 text-center">
                          {showAuto ? (
                            <button
                              type="button"
                              onClick={() => updateRow(idx, { conversionAuto: false })}
                              className="text-[11px] px-2 py-1 rounded bg-blue-50 text-blue-700 border border-blue-200 hover:bg-blue-100"
                              title="Server will auto-fill from walkouts. Click to override."
                            >
                              AUTO
                            </button>
                          ) : (
                            <input
                              type="number"
                              min={0} max={c.max}
                              value={val}
                              disabled={locked}
                              onChange={e => updateScore(idx, c.key, Number(e.target.value))}
                              className="w-14 px-1 py-1 text-center border border-gray-300 rounded text-sm disabled:bg-gray-50 disabled:text-gray-500"
                            />
                          )}
                        </td>
                      );
                    })}
                    <td className="px-3 py-2 text-center font-semibold bg-gray-50/50">{total}</td>
                    <td className="px-3 py-2 text-center bg-gray-50/50">
                      <EligibilityChip value={elig} />
                    </td>
                    <td className="px-3 py-2 text-right">
                      {locked ? (
                        <div className="inline-flex items-center gap-1">
                          <span className="text-[11px] text-emerald-700">Saved</span>
                          {row.log_id && (
                            <button
                              type="button"
                              onClick={() => handleDeleteRow(row.log_id!)}
                              className="text-rose-600 hover:bg-rose-50 p-1 rounded"
                              title="Delete (frees the slot for re-save)"
                            >
                              <Trash2 className="w-3 h-3" />
                            </button>
                          )}
                          <Link
                            to={`/incentive/staff/${row.staff_id}`}
                            className="text-gray-500 hover:bg-gray-100 p-1 rounded"
                            title="History"
                          >
                            <History className="w-3 h-3" />
                          </Link>
                        </div>
                      ) : (
                        <span className="text-xs text-gray-400">Pending</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {settings?.visufit_gate_enabled && (
        <div className="text-xs text-gray-500 flex items-center gap-1">
          <ChevronRight className="w-3 h-3" /> Visufit gate active —
          if MTD usage &lt; {(settings.visufit_gate_threshold * 100).toFixed(0)}% the
          Visufit category snaps to 0 on save.
        </div>
      )}
    </div>
  );
}

export function EligibilityChip({ value }: { value: number }) {
  const cls =
    value >= 1.0 ? 'bg-emerald-50 text-emerald-700 border-emerald-200' :
    value >= 0.8 ? 'bg-blue-50 text-blue-700 border-blue-200' :
    value >= 0.6 ? 'bg-amber-50 text-amber-700 border-amber-200' :
    'bg-rose-50 text-rose-700 border-rose-200';
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full border ${cls}`}>
      {value.toFixed(2)}
    </span>
  );
}

export default DailyScorecardPage;
