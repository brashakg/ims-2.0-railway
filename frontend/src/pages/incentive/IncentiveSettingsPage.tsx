// ============================================================================
// IMS 2.0 — Incentive Settings (Pune Incentive Module iii configuration)
// ============================================================================
// Source-of-truth UI for `incentive_settings` per store. Wraps the backend
// PATCH endpoints on points.py:
//   PATCH /incentive/points/settings/eligibility   (eligibility bands)
//   PATCH /incentive/points/settings/payout        (weightages, growth, rates,
//                                                   multipliers, supervisors)
//   PATCH /incentive/points/settings/visufit-gate  (threshold + enabled)
//   POST  /incentive/points/inputs/last-year-sale  (per-month override)
//
// Each section saves independently — partial failures stay isolated. The
// "Save" button on each card PATCHes only its own slice (exclude_unset
// semantics on the backend), so other sections aren't touched.
//
// SUPERADMIN only. Other roles see a read-only banner.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Save, Plus, Trash2, AlertTriangle, Loader2, RefreshCw, Lock,
  ArrowLeft, Info, Check,
} from 'lucide-react';
import { incentiveApi } from '../../services/api';
import { adminUserApi } from '../../services/api/stores';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import type {
  IncentiveSettings,
  EligibilityBand,
  SupervisorBonus,
} from '../../types';

type StaffOpt = { user_id: string; name: string };

const LEVELS = ['L1', 'L2', 'L3'] as const;
type Level = typeof LEVELS[number];

function pct(n: number | null | undefined, places = 2): string {
  if (n == null || Number.isNaN(n)) return '—';
  return (n * 100).toFixed(places) + '%';
}

function clampNum(v: string): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

// ----------------------------------------------------------------------------
// SectionCard — common wrapper with title, optional description, save button
// ----------------------------------------------------------------------------

function SectionCard({
  title, description, children, onSave, saving, dirty, disabled, warning,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
  onSave?: () => void;
  saving?: boolean;
  dirty?: boolean;
  disabled?: boolean;
  warning?: string | null;
}) {
  return (
    <section className="card p-5 mb-4">
      <header className="flex items-start justify-between gap-3 mb-4">
        <div>
          <h2 className="text-base font-semibold text-gray-900">{title}</h2>
          {description && (
            <p className="text-xs text-gray-500 mt-1 max-w-xl">{description}</p>
          )}
          {warning && (
            <div className="mt-2 flex items-start gap-1.5 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1.5 max-w-xl">
              <AlertTriangle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
              <span>{warning}</span>
            </div>
          )}
        </div>
        {onSave && (
          <button
            type="button"
            onClick={onSave}
            disabled={disabled || saving || !dirty}
            className="btn-primary text-xs px-3 py-1.5 inline-flex items-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed flex-shrink-0"
          >
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            {saving ? 'Saving…' : dirty ? 'Save changes' : 'Saved'}
          </button>
        )}
      </header>
      {children}
    </section>
  );
}

// ============================================================================
// Page
// ============================================================================

export function IncentiveSettingsPage() {
  const { user } = useAuth();
  const toast = useToast();

  const userRoles = (user as any)?.roles as string[] | undefined;
  const activeRole = (user as any)?.activeRole as string | undefined;
  const isSuperadmin = (userRoles || []).includes('SUPERADMIN')
    || activeRole === 'SUPERADMIN';
  const storeId = (user as any)?.activeStoreId as string | undefined;

  const [settings, setSettings] = useState<IncentiveSettings | null>(null);
  const [staff, setStaff] = useState<StaffOpt[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Section-local edit state — strings so users can type freely without
  // tripping NaN during partial input. Parsed on save.
  const [weights, setWeights] = useState<Record<string, string>>({});
  const [bands, setBands] = useState<EligibilityBand[]>([]);
  const [growth, setGrowth] = useState<Record<Level, string>>({ L1: '', L2: '', L3: '' });
  const [rates, setRates] = useState<Record<Level, string>>({ L1: '', L2: '', L3: '' });
  const [killThr, setKillThr] = useState<string>('');
  const [mults, setMults] = useState<Array<{ max_pct: string; multiplier: string }>>([]);
  const [vfEnabled, setVfEnabled] = useState(true);
  const [vfThreshold, setVfThreshold] = useState<string>('');
  const [supervisors, setSupervisors] = useState<SupervisorBonus[]>([]);

  // Last-year-sale per-month input — uses today by default
  const today = new Date();
  const [lysYear, setLysYear] = useState<number>(today.getFullYear());
  const [lysMonth, setLysMonth] = useState<number>(today.getMonth() + 1);
  const [lysAmount, setLysAmount] = useState<string>('');

  // Dirty flags per section
  const [dirty, setDirty] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState<Record<string, boolean>>({});

  const setDirtyFor = (key: string) => setDirty(d => ({ ...d, [key]: true }));
  const clearDirty = (key: string) => setDirty(d => ({ ...d, [key]: false }));
  const setSavingFor = (key: string, v: boolean) =>
    setSaving(s => ({ ...s, [key]: v }));

  // -------- Hydration --------

  const hydrate = useCallback((s: IncentiveSettings) => {
    setSettings(s);
    setWeights(
      Object.fromEntries(
        Object.entries(s.staff_weightages || {}).map(([k, v]) => [k, String(v ?? 0)]),
      ),
    );
    setBands((s.eligibility_bands || []).map(b => ({ ...b })));
    setGrowth({
      L1: String(s.growth_targets?.L1 ?? 0),
      L2: String(s.growth_targets?.L2 ?? 0),
      L3: String(s.growth_targets?.L3 ?? 0),
    });
    setRates({
      L1: String(s.base_rates?.L1 ?? 0),
      L2: String(s.base_rates?.L2 ?? 0),
      L3: String(s.base_rates?.L3 ?? 0),
    });
    setKillThr(String(s.discount_kill_threshold ?? 0));
    setMults(
      (s.discount_multipliers || []).map(m => ({
        max_pct: String(m.max_pct),
        multiplier: String(m.multiplier),
      })),
    );
    setVfEnabled(!!s.visufit_gate_enabled);
    setVfThreshold(String(s.visufit_gate_threshold ?? 0));
    setSupervisors((s.supervisor_bonuses || []).map(sb => ({
      user_id: sb.user_id,
      role: sb.role,
      bonus_pct: { ...sb.bonus_pct },
    })));
    setDirty({});
  }, []);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, usersResp] = await Promise.all([
        incentiveApi.getSettings(),
        (adminUserApi as any).getUsers?.({ storeId }).catch(() => null),
      ]);
      hydrate(s);
      const list: any[] = usersResp?.users || usersResp || [];
      setStaff(
        (Array.isArray(list) ? list : []).map((u: any) => ({
          user_id: u.user_id || u.id,
          name: u.name || u.full_name || u.username || u.user_id,
        })),
      );
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Failed to load settings';
      setError(typeof msg === 'string' ? msg : 'Failed to load settings');
    } finally {
      setLoading(false);
    }
  }, [storeId, hydrate]);

  useEffect(() => { loadAll(); }, [loadAll]);

  // -------- Weightage helpers --------

  const weightsSum = useMemo(
    () => Object.values(weights).reduce((a, b) => a + clampNum(b), 0),
    [weights],
  );
  const weightsSumOk = Math.abs(weightsSum - 1) < 0.001;

  const addStaffWeight = (uid: string) => {
    if (!uid || weights[uid] != null) return;
    setWeights(w => ({ ...w, [uid]: '0' }));
    setDirtyFor('weights');
  };
  const removeStaffWeight = (uid: string) => {
    setWeights(w => {
      const n = { ...w };
      delete n[uid];
      return n;
    });
    setDirtyFor('weights');
  };

  const saveWeights = async () => {
    if (!weightsSumOk) {
      toast.error(`Weightages must sum to 100% (currently ${pct(weightsSum)})`);
      return;
    }
    setSavingFor('weights', true);
    try {
      const payload: Record<string, number> = Object.fromEntries(
        Object.entries(weights).map(([k, v]) => [k, clampNum(v)]),
      );
      const next = await incentiveApi.updatePayoutSettings({ staff_weightages: payload });
      hydrate(next);
      toast.success('Staff weightages saved');
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Save failed');
    } finally {
      setSavingFor('weights', false);
    }
  };

  // -------- Eligibility bands helpers --------

  const addBand = () => {
    setBands(b => [...b, { min: 0, max: 100, value: 0 }]);
    setDirtyFor('bands');
  };
  const removeBand = (idx: number) => {
    setBands(b => b.filter((_, i) => i !== idx));
    setDirtyFor('bands');
  };
  const updateBand = (idx: number, field: keyof EligibilityBand, value: number) => {
    setBands(b => b.map((row, i) => i === idx ? { ...row, [field]: value } : row));
    setDirtyFor('bands');
  };
  const saveBands = async () => {
    setSavingFor('bands', true);
    try {
      const next = await incentiveApi.updateEligibility(bands);
      hydrate(next);
      toast.success('Eligibility bands saved');
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Save failed');
    } finally {
      setSavingFor('bands', false);
    }
  };

  // -------- Growth + rates --------

  const saveGrowthAndRates = async () => {
    setSavingFor('targets', true);
    try {
      const next = await incentiveApi.updatePayoutSettings({
        growth_targets: { L1: clampNum(growth.L1), L2: clampNum(growth.L2), L3: clampNum(growth.L3) },
        base_rates: { L1: clampNum(rates.L1), L2: clampNum(rates.L2), L3: clampNum(rates.L3) },
      });
      hydrate(next);
      toast.success('Targets and base rates saved');
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Save failed');
    } finally {
      setSavingFor('targets', false);
    }
  };

  // -------- Discount multipliers --------

  const addMult = () => {
    setMults(m => [...m, { max_pct: '0.15', multiplier: '1.0' }]);
    setDirtyFor('mults');
  };
  const removeMult = (idx: number) => {
    setMults(m => m.filter((_, i) => i !== idx));
    setDirtyFor('mults');
  };
  const updateMult = (idx: number, field: 'max_pct' | 'multiplier', value: string) => {
    setMults(m => m.map((row, i) => i === idx ? { ...row, [field]: value } : row));
    setDirtyFor('mults');
  };
  const saveMults = async () => {
    setSavingFor('mults', true);
    try {
      const next = await incentiveApi.updatePayoutSettings({
        discount_kill_threshold: clampNum(killThr),
        discount_multipliers: mults
          .map(m => ({ max_pct: clampNum(m.max_pct), multiplier: clampNum(m.multiplier) }))
          .sort((a, b) => a.max_pct - b.max_pct),
      });
      hydrate(next);
      toast.success('Discount multipliers saved');
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Save failed');
    } finally {
      setSavingFor('mults', false);
    }
  };

  // -------- Visufit gate --------

  const saveVisufit = async () => {
    setSavingFor('visufit', true);
    try {
      const next = await incentiveApi.updateVisufitGate({
        threshold: clampNum(vfThreshold),
        enabled: vfEnabled,
      });
      hydrate(next);
      toast.success('Visufit gate saved');
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Save failed');
    } finally {
      setSavingFor('visufit', false);
    }
  };

  // -------- Supervisors --------

  const addSupervisor = () => {
    setSupervisors(s => [
      ...s,
      { user_id: '', role: 'STORE_MANAGER', bonus_pct: { L1: 0.25, L2: 0.30, L3: 0.35 } },
    ]);
    setDirtyFor('supervisors');
  };
  const removeSupervisor = (idx: number) => {
    setSupervisors(s => s.filter((_, i) => i !== idx));
    setDirtyFor('supervisors');
  };
  const updateSupervisor = <K extends keyof SupervisorBonus>(
    idx: number, field: K, value: SupervisorBonus[K],
  ) => {
    setSupervisors(s => s.map((row, i) => i === idx ? { ...row, [field]: value } : row));
    setDirtyFor('supervisors');
  };
  const updateSupervisorBonus = (idx: number, lvl: Level, value: number) => {
    setSupervisors(s => s.map((row, i) =>
      i === idx ? { ...row, bonus_pct: { ...row.bonus_pct, [lvl]: value } } : row,
    ));
    setDirtyFor('supervisors');
  };
  const saveSupervisors = async () => {
    const bad = supervisors.find(s => !s.user_id);
    if (bad) {
      toast.error('Every supervisor row needs a user selected');
      return;
    }
    setSavingFor('supervisors', true);
    try {
      const next = await incentiveApi.updatePayoutSettings({
        supervisor_bonuses: supervisors.map(s => ({
          user_id: s.user_id,
          role: s.role,
          bonus_pct: {
            L1: Number(s.bonus_pct.L1) || 0,
            L2: Number(s.bonus_pct.L2) || 0,
            L3: Number(s.bonus_pct.L3) || 0,
          },
        })),
      });
      hydrate(next);
      toast.success('Supervisor bonuses saved');
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Save failed');
    } finally {
      setSavingFor('supervisors', false);
    }
  };

  // -------- Last-year sale --------

  const saveLastYearSale = async () => {
    const amt = clampNum(lysAmount);
    if (amt <= 0) {
      toast.error('Enter a positive amount');
      return;
    }
    setSavingFor('lys', true);
    try {
      await incentiveApi.setLastYearSale({
        year: lysYear, month: lysMonth, last_year_sale: amt,
      });
      toast.success(`Last-year sale saved for ${lysYear}-${String(lysMonth).padStart(2, '0')}`);
      setLysAmount('');
      clearDirty('lys');
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Save failed');
    } finally {
      setSavingFor('lys', false);
    }
  };

  // -------- Helpers --------

  const staffName = (uid: string) => staff.find(s => s.user_id === uid)?.name || uid;
  const availableStaff = staff.filter(s => !(s.user_id in weights));

  // ==========================================================================
  // Render
  // ==========================================================================

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="mb-5 flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-xs text-gray-500 mb-1">
            <Link to="/incentive/payout" className="hover:text-gray-700 inline-flex items-center gap-1">
              <ArrowLeft className="w-3 h-3" /> Payout dashboard
            </Link>
          </div>
          <h1 className="text-xl font-semibold text-gray-900">Incentive Settings</h1>
          <p className="text-xs text-gray-500 mt-1">
            Configure staff weightages, eligibility bands, growth targets, multipliers, and bonuses for the Pune-incentive model.
            Each card saves independently.
          </p>
        </div>
        <button
          type="button"
          onClick={loadAll}
          className="btn-secondary text-xs px-3 py-1.5 inline-flex items-center gap-1.5"
          disabled={loading}
        >
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
          Refresh
        </button>
      </div>

      {!isSuperadmin && (
        <div className="mb-4 flex items-start gap-2 text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded p-3">
          <Lock className="w-4 h-4 mt-0.5 flex-shrink-0" />
          <div>
            <strong>Read-only.</strong> Only SUPERADMIN can change incentive settings. Inputs below are disabled.
          </div>
        </div>
      )}

      {error && (
        <div className="mb-4 flex items-start gap-2 text-sm text-red-800 bg-red-50 border border-red-200 rounded p-3">
          <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {loading && !settings && (
        <div className="card p-8 text-center text-gray-500 text-sm">
          <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />
          Loading settings…
        </div>
      )}

      {settings && (
        <>
          {/* ===== Staff Weightages ===== */}
          <SectionCard
            title="Staff Weightages"
            description="Share of the team pool each sales staff earns. Must sum to 100%."
            onSave={saveWeights}
            saving={!!saving['weights']}
            dirty={!!dirty['weights']}
            disabled={!isSuperadmin}
            warning={
              !weightsSumOk
                ? `Current total is ${pct(weightsSum)} — must be 100% before saving.`
                : null
            }
          >
            <table className="w-full text-sm">
              <thead className="text-xs text-gray-500 border-b">
                <tr>
                  <th className="text-left py-2 px-2 font-medium">Staff</th>
                  <th className="text-right py-2 px-2 font-medium w-32">Weight (%)</th>
                  <th className="w-8" />
                </tr>
              </thead>
              <tbody>
                {Object.entries(weights).length === 0 && (
                  <tr><td colSpan={3} className="py-4 text-center text-gray-400 text-xs">No staff added yet.</td></tr>
                )}
                {Object.entries(weights).map(([uid, val]) => (
                  <tr key={uid} className="border-b last:border-b-0">
                    <td className="py-1.5 px-2">
                      <div className="text-gray-900">{staffName(uid)}</div>
                      <div className="text-[10px] text-gray-400 font-mono">{uid}</div>
                    </td>
                    <td className="py-1.5 px-2">
                      <input
                        type="number"
                        step="0.01"
                        min="0"
                        max="1"
                        value={val}
                        onChange={e => {
                          setWeights(w => ({ ...w, [uid]: e.target.value }));
                          setDirtyFor('weights');
                        }}
                        disabled={!isSuperadmin}
                        className="input-field w-full text-right py-1 text-sm tabular-nums"
                      />
                      <div className="text-[10px] text-gray-400 text-right">= {pct(clampNum(val))}</div>
                    </td>
                    <td className="py-1.5 px-1">
                      <button
                        type="button"
                        onClick={() => removeStaffWeight(uid)}
                        disabled={!isSuperadmin}
                        className="text-gray-400 hover:text-red-600 disabled:opacity-30 p-1"
                        aria-label={`Remove ${staffName(uid)}`}
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
                <tr className="bg-gray-50">
                  <td className="py-2 px-2 text-xs font-medium text-gray-700">Total</td>
                  <td className={`py-2 px-2 text-right text-sm font-semibold tabular-nums ${weightsSumOk ? 'text-emerald-700' : 'text-red-700'}`}>
                    {pct(weightsSum)} {weightsSumOk && <Check className="inline w-3 h-3 ml-1" />}
                  </td>
                  <td />
                </tr>
              </tbody>
            </table>
            {isSuperadmin && availableStaff.length > 0 && (
              <div className="mt-3 flex items-center gap-2">
                <select
                  className="input-field text-xs py-1 max-w-xs"
                  defaultValue=""
                  onChange={e => {
                    if (e.target.value) {
                      addStaffWeight(e.target.value);
                      e.target.value = '';
                    }
                  }}
                >
                  <option value="">+ Add staff…</option>
                  {availableStaff.map(s => (
                    <option key={s.user_id} value={s.user_id}>{s.name}</option>
                  ))}
                </select>
                <span className="text-[10px] text-gray-400">
                  {availableStaff.length} staff available
                </span>
              </div>
            )}
          </SectionCard>

          {/* ===== Eligibility Bands ===== */}
          <SectionCard
            title="Eligibility Bands"
            description="Score → eligibility multiplier. e.g. score 80-95 = 80% payout. Snapshot semantics: existing daily logs keep their write-time eligibility."
            onSave={saveBands}
            saving={!!saving['bands']}
            dirty={!!dirty['bands']}
            disabled={!isSuperadmin}
          >
            <table className="w-full text-sm">
              <thead className="text-xs text-gray-500 border-b">
                <tr>
                  <th className="text-left py-2 px-2 font-medium w-24">Min score</th>
                  <th className="text-left py-2 px-2 font-medium w-24">Max score</th>
                  <th className="text-left py-2 px-2 font-medium w-32">Eligibility</th>
                  <th />
                  <th className="w-8" />
                </tr>
              </thead>
              <tbody>
                {bands.map((b, i) => (
                  <tr key={i} className="border-b last:border-b-0">
                    <td className="py-1.5 px-2">
                      <input
                        type="number"
                        value={b.min}
                        onChange={e => updateBand(i, 'min', Number(e.target.value))}
                        disabled={!isSuperadmin}
                        className="input-field w-24 text-sm py-1 tabular-nums"
                      />
                    </td>
                    <td className="py-1.5 px-2">
                      <input
                        type="number"
                        value={b.max}
                        onChange={e => updateBand(i, 'max', Number(e.target.value))}
                        disabled={!isSuperadmin}
                        className="input-field w-24 text-sm py-1 tabular-nums"
                      />
                    </td>
                    <td className="py-1.5 px-2">
                      <input
                        type="number"
                        step="0.01"
                        value={b.value}
                        onChange={e => updateBand(i, 'value', Number(e.target.value))}
                        disabled={!isSuperadmin}
                        className="input-field w-24 text-sm py-1 tabular-nums"
                      />
                    </td>
                    <td className="px-2 text-xs text-gray-500">= {pct(b.value)} payout</td>
                    <td className="py-1.5 px-1">
                      <button
                        type="button"
                        onClick={() => removeBand(i)}
                        disabled={!isSuperadmin}
                        className="text-gray-400 hover:text-red-600 disabled:opacity-30 p-1"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {isSuperadmin && (
              <button
                type="button"
                onClick={addBand}
                className="mt-3 text-xs text-blue-700 hover:text-blue-900 inline-flex items-center gap-1"
              >
                <Plus className="w-3.5 h-3.5" /> Add band
              </button>
            )}
          </SectionCard>

          {/* ===== Growth Targets + Base Rates ===== */}
          <SectionCard
            title="Growth Targets & Base Rates"
            description="L1/L2/L3 growth targets (as fraction; 0.20 = 20%) and the base pool rate (fraction of sales) at each level."
            onSave={saveGrowthAndRates}
            saving={!!saving['targets']}
            dirty={!!dirty['targets']}
            disabled={!isSuperadmin}
          >
            <div className="grid grid-cols-2 gap-6">
              <div>
                <div className="text-xs font-medium text-gray-700 mb-2">Growth Targets</div>
                <div className="grid grid-cols-1 tablet:grid-cols-3 gap-3">
                  {LEVELS.map(lvl => (
                    <label key={lvl} className="block">
                      <div className="text-xs text-gray-500 mb-1">{lvl}</div>
                      <input
                        type="number"
                        step="0.01"
                        value={growth[lvl]}
                        onChange={e => {
                          setGrowth(g => ({ ...g, [lvl]: e.target.value }));
                          setDirtyFor('targets');
                        }}
                        disabled={!isSuperadmin}
                        className="input-field w-full text-sm tabular-nums"
                      />
                      <div className="text-[10px] text-gray-400 mt-0.5">= {pct(clampNum(growth[lvl]))}</div>
                    </label>
                  ))}
                </div>
              </div>
              <div>
                <div className="text-xs font-medium text-gray-700 mb-2">Base Rates</div>
                <div className="grid grid-cols-1 tablet:grid-cols-3 gap-3">
                  {LEVELS.map(lvl => (
                    <label key={lvl} className="block">
                      <div className="text-xs text-gray-500 mb-1">{lvl}</div>
                      <input
                        type="number"
                        step="0.0001"
                        value={rates[lvl]}
                        onChange={e => {
                          setRates(r => ({ ...r, [lvl]: e.target.value }));
                          setDirtyFor('targets');
                        }}
                        disabled={!isSuperadmin}
                        className="input-field w-full text-sm tabular-nums"
                      />
                      <div className="text-[10px] text-gray-400 mt-0.5">= {pct(clampNum(rates[lvl]), 3)}</div>
                    </label>
                  ))}
                </div>
              </div>
            </div>
          </SectionCard>

          {/* ===== Discount Multipliers ===== */}
          <SectionCard
            title="Discount Multipliers"
            description="Pool multiplier based on average discount. Floor-rounded — 11.99% hits the 11% bracket. Pool is killed if avg discount exceeds the kill threshold."
            onSave={saveMults}
            saving={!!saving['mults']}
            dirty={!!dirty['mults']}
            disabled={!isSuperadmin}
          >
            <label className="block mb-4">
              <div className="text-xs font-medium text-gray-700 mb-1">Discount kill threshold</div>
              <div className="flex items-center gap-2">
                <input
                  type="number"
                  step="0.01"
                  value={killThr}
                  onChange={e => { setKillThr(e.target.value); setDirtyFor('mults'); }}
                  disabled={!isSuperadmin}
                  className="input-field w-28 text-sm tabular-nums"
                />
                <span className="text-xs text-gray-500">= {pct(clampNum(killThr))} — pool goes to ₹0 if avg discount exceeds this.</span>
              </div>
            </label>
            <table className="w-full text-sm">
              <thead className="text-xs text-gray-500 border-b">
                <tr>
                  <th className="text-left py-2 px-2 font-medium w-28">Max % (≤)</th>
                  <th className="text-left py-2 px-2 font-medium w-28">Multiplier</th>
                  <th />
                  <th className="w-8" />
                </tr>
              </thead>
              <tbody>
                {mults.map((m, i) => (
                  <tr key={i} className="border-b last:border-b-0">
                    <td className="py-1.5 px-2">
                      <input
                        type="number"
                        step="0.01"
                        value={m.max_pct}
                        onChange={e => updateMult(i, 'max_pct', e.target.value)}
                        disabled={!isSuperadmin}
                        className="input-field w-24 text-sm tabular-nums"
                      />
                    </td>
                    <td className="py-1.5 px-2">
                      <input
                        type="number"
                        step="0.1"
                        value={m.multiplier}
                        onChange={e => updateMult(i, 'multiplier', e.target.value)}
                        disabled={!isSuperadmin}
                        className="input-field w-24 text-sm tabular-nums"
                      />
                    </td>
                    <td className="px-2 text-xs text-gray-500">
                      Up to {pct(clampNum(m.max_pct))} → {clampNum(m.multiplier).toFixed(1)}× pool
                    </td>
                    <td className="py-1.5 px-1">
                      <button
                        type="button"
                        onClick={() => removeMult(i)}
                        disabled={!isSuperadmin}
                        className="text-gray-400 hover:text-red-600 disabled:opacity-30 p-1"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {isSuperadmin && (
              <button
                type="button"
                onClick={addMult}
                className="mt-3 text-xs text-blue-700 hover:text-blue-900 inline-flex items-center gap-1"
              >
                <Plus className="w-3.5 h-3.5" /> Add tier
              </button>
            )}
          </SectionCard>

          {/* ===== Visufit Gate ===== */}
          <SectionCard
            title="Visufit Gate"
            description="If MTD Visufit usage falls below the threshold, the Visufit category writes as 0 for every staff that month."
            onSave={saveVisufit}
            saving={!!saving['visufit']}
            dirty={!!dirty['visufit']}
            disabled={!isSuperadmin}
          >
            <div className="flex items-center gap-6">
              <label className="inline-flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={vfEnabled}
                  onChange={e => { setVfEnabled(e.target.checked); setDirtyFor('visufit'); }}
                  disabled={!isSuperadmin}
                  className="rounded"
                />
                Gate enabled
              </label>
              <label className="flex items-center gap-2 text-sm">
                <span className="text-xs text-gray-500">Threshold</span>
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  max="1"
                  value={vfThreshold}
                  onChange={e => { setVfThreshold(e.target.value); setDirtyFor('visufit'); }}
                  disabled={!isSuperadmin}
                  className="input-field w-24 text-sm tabular-nums"
                />
                <span className="text-xs text-gray-500">= {pct(clampNum(vfThreshold))}</span>
              </label>
            </div>
          </SectionCard>

          {/* ===== Supervisor Bonuses ===== */}
          <SectionCard
            title="Supervisor Bonuses"
            description="Per-supervisor bonus % stacked on top of the staff pool. Each supervisor row is eligibility-gated separately via their daily points."
            onSave={saveSupervisors}
            saving={!!saving['supervisors']}
            dirty={!!dirty['supervisors']}
            disabled={!isSuperadmin}
          >
            <table className="w-full text-sm">
              <thead className="text-xs text-gray-500 border-b">
                <tr>
                  <th className="text-left py-2 px-2 font-medium">Supervisor</th>
                  <th className="text-left py-2 px-2 font-medium w-36">Role</th>
                  {LEVELS.map(lvl => (
                    <th key={lvl} className="text-left py-2 px-2 font-medium w-24">{lvl} bonus</th>
                  ))}
                  <th className="w-8" />
                </tr>
              </thead>
              <tbody>
                {supervisors.length === 0 && (
                  <tr><td colSpan={6} className="py-4 text-center text-gray-400 text-xs">No supervisors configured.</td></tr>
                )}
                {supervisors.map((sb, i) => (
                  <tr key={i} className="border-b last:border-b-0">
                    <td className="py-1.5 px-2">
                      <select
                        value={sb.user_id}
                        onChange={e => updateSupervisor(i, 'user_id', e.target.value)}
                        disabled={!isSuperadmin}
                        className="input-field w-full text-sm py-1"
                      >
                        <option value="">— select —</option>
                        {staff.map(s => (
                          <option key={s.user_id} value={s.user_id}>{s.name}</option>
                        ))}
                      </select>
                    </td>
                    <td className="py-1.5 px-2">
                      <input
                        type="text"
                        value={sb.role}
                        onChange={e => updateSupervisor(i, 'role', e.target.value)}
                        disabled={!isSuperadmin}
                        className="input-field w-full text-sm py-1"
                      />
                    </td>
                    {LEVELS.map(lvl => (
                      <td key={lvl} className="py-1.5 px-2">
                        <input
                          type="number"
                          step="0.01"
                          value={sb.bonus_pct[lvl] ?? 0}
                          onChange={e => updateSupervisorBonus(i, lvl, Number(e.target.value))}
                          disabled={!isSuperadmin}
                          className="input-field w-20 text-sm py-1 tabular-nums"
                        />
                        <div className="text-[10px] text-gray-400">= {pct(sb.bonus_pct[lvl])}</div>
                      </td>
                    ))}
                    <td className="py-1.5 px-1">
                      <button
                        type="button"
                        onClick={() => removeSupervisor(i)}
                        disabled={!isSuperadmin}
                        className="text-gray-400 hover:text-red-600 disabled:opacity-30 p-1"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {isSuperadmin && (
              <button
                type="button"
                onClick={addSupervisor}
                className="mt-3 text-xs text-blue-700 hover:text-blue-900 inline-flex items-center gap-1"
              >
                <Plus className="w-3.5 h-3.5" /> Add supervisor
              </button>
            )}
          </SectionCard>

          {/* ===== Per-month last-year sale ===== */}
          <SectionCard
            title="Per-month input: Last-year sale"
            description="Override the last-year sale for a specific (year, month). Used by the payout calculator to compute targets when the aggregated actuals are wrong or missing."
            onSave={saveLastYearSale}
            saving={!!saving['lys']}
            dirty={!!dirty['lys']}
            disabled={!isSuperadmin}
          >
            <div className="flex flex-wrap items-end gap-3">
              <label className="block">
                <div className="text-xs text-gray-500 mb-1">Year</div>
                <input
                  type="number"
                  value={lysYear}
                  onChange={e => { setLysYear(Number(e.target.value)); setDirtyFor('lys'); }}
                  disabled={!isSuperadmin}
                  className="input-field w-24 text-sm tabular-nums"
                />
              </label>
              <label className="block">
                <div className="text-xs text-gray-500 mb-1">Month</div>
                <select
                  value={lysMonth}
                  onChange={e => { setLysMonth(Number(e.target.value)); setDirtyFor('lys'); }}
                  disabled={!isSuperadmin}
                  className="input-field w-32 text-sm"
                >
                  {Array.from({ length: 12 }, (_, i) => i + 1).map(m => (
                    <option key={m} value={m}>{new Date(2024, m - 1, 1).toLocaleString('en-US', { month: 'long' })}</option>
                  ))}
                </select>
              </label>
              <label className="block flex-1 min-w-[200px]">
                <div className="text-xs text-gray-500 mb-1">Last-year sale (₹)</div>
                <input
                  type="number"
                  value={lysAmount}
                  onChange={e => { setLysAmount(e.target.value); setDirtyFor('lys'); }}
                  disabled={!isSuperadmin}
                  placeholder="e.g. 1838000"
                  className="input-field w-full text-sm tabular-nums"
                />
              </label>
            </div>
            <div className="mt-2 text-[11px] text-gray-500 inline-flex items-center gap-1">
              <Info className="w-3 h-3" />
              Saving creates a new override; existing months can be overwritten by re-saving.
            </div>
          </SectionCard>

          {settings.updated_at && (
            <div className="mt-4 text-xs text-gray-400 text-right">
              Last updated {new Date(settings.updated_at).toLocaleString()} by {settings.updated_by || 'system'}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default IncentiveSettingsPage;
