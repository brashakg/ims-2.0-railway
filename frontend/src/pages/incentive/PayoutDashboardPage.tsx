// ============================================================================
// IMS 2.0 — Payout Dashboard (Pune Incentive Module iii)
// ============================================================================
// Mirrors the Excel "Payout Dashboard" sheet:
//
//   ┌──────────── Inputs ────────────┐  ┌── Pool sizing ──┐
//   │ Last-year sale      ₹18,38,000 │  │ Best level: L3 │
//   │ This-year sale      ₹26,00,000 │  │ Multiplier 1.5×│
//   │ Avg discount %      10.00%     │  │ Pool ₹58,500   │
//   │ Visufit usage       94.0%      │  └────────────────┘
//   └────────────────────────────────┘
//
//   Targets table (achieved/missed)
//   Per-staff payouts grid
//   Manager bonuses
//   Grand totals
//   [Lock month] [Mark paid] [Export CSV]
//
// Inputs editable inline; preview re-runs as the user types.
// SUPERADMIN sees Lock + Mark-Paid buttons.

import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Calculator, Lock, Check, Download, Loader2, RefreshCw,
  AlertCircle, IndianRupee, TrendingUp, AlertTriangle, Settings,
} from 'lucide-react';
import { payoutApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import type { PayoutEnvelope, PayoutSnapshot } from '../../types';

function inr(n: number | null | undefined): string {
  if (n == null) return '—';
  return '₹' + Math.round(n).toLocaleString('en-IN');
}

function pct(n: number | null | undefined, places = 2): string {
  if (n == null) return '—';
  return (n * 100).toFixed(places) + '%';
}

export function PayoutDashboardPage() {
  const { user } = useAuth();
  const toast = useToast();

  const userRoles = (user as any)?.roles as string[] | undefined;
  const activeRole = (user as any)?.activeRole as string | undefined;
  const isSuperadmin = (userRoles || []).includes('SUPERADMIN')
    || activeRole === 'SUPERADMIN';

  const today = new Date();
  const [year, setYear] = useState(today.getFullYear());
  const [month, setMonth] = useState(today.getMonth() + 1);

  // Manual overrides
  const [lastYear, setLastYear] = useState<string>('');
  const [thisYear, setThisYear] = useState<string>('');
  const [avgDisc, setAvgDisc] = useState<string>('');
  const [visufit, setVisufit] = useState<string>('');

  const [envelope, setEnvelope] = useState<PayoutEnvelope | null>(null);
  const [snapshot, setSnapshot] = useState<PayoutSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Look for an existing snapshot for this (year, month) first
      const list = await payoutApi.list(year);
      const existing = list.items.find(i => i.month === month);
      if (existing) {
        setSnapshot(existing);
      } else {
        setSnapshot(null);
      }
      const params: any = { year, month };
      if (lastYear) params.last_year_sale = Number(lastYear);
      if (thisYear) params.this_year_sale = Number(thisYear);
      if (avgDisc) params.avg_discount_pct = Number(avgDisc) / 100;
      if (visufit) params.visufit_usage_pct = Number(visufit) / 100;
      const env = await payoutApi.preview(params);
      setEnvelope(env);
      // Hydrate the input fields from preview if user hasn't overridden
      if (!lastYear) setLastYear(String(env.inputs.last_year_sale || ''));
      if (!thisYear) setThisYear(String(env.inputs.this_year_sale || ''));
      if (!avgDisc) setAvgDisc(String((env.inputs.avg_discount_pct * 100).toFixed(2)));
      if (!visufit) setVisufit(String((env.inputs.visufit_usage_pct * 100).toFixed(1)));
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Failed to load payout';
      setError(typeof msg === 'string' ? msg : 'Failed to load payout');
      setEnvelope(null);
    } finally {
      setLoading(false);
    }
  }, [year, month, lastYear, thisYear, avgDisc, visufit]);

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [year, month]);

  const handleLock = async () => {
    if (!isSuperadmin) {
      toast.error('Only SUPERADMIN can lock a snapshot');
      return;
    }
    if (!confirm(`Lock payout for ${year}-${String(month).padStart(2, '0')}? This is immutable.`)) return;
    setBusy(true);
    try {
      const payload: any = { year, month };
      if (lastYear) payload.last_year_sale = Number(lastYear);
      if (thisYear) payload.this_year_sale = Number(thisYear);
      if (avgDisc) payload.avg_discount_pct = Number(avgDisc) / 100;
      if (visufit) payload.visufit_usage_pct = Number(visufit) / 100;
      const saved = await payoutApi.lock(payload);
      setSnapshot(saved);
      toast.success(`Locked: ${saved.snapshot_id}`);
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Lock failed';
      toast.error(typeof msg === 'string' ? msg : 'Lock failed');
    } finally {
      setBusy(false);
    }
  };

  const handleMarkPaid = async () => {
    if (!snapshot || !isSuperadmin) return;
    const note = window.prompt('Note for the audit log (optional):') || '';
    setBusy(true);
    try {
      const updated = await payoutApi.markPaid(snapshot.snapshot_id, note);
      setSnapshot(updated);
      toast.success('Marked as paid');
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Mark-paid failed';
      toast.error(typeof msg === 'string' ? msg : 'Mark-paid failed');
    } finally {
      setBusy(false);
    }
  };

  // Display source: snapshot if locked, envelope otherwise
  const view = snapshot || envelope;
  const isLocked = !!snapshot;

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <Link
            to="/incentive"
            className="inline-flex items-center gap-1 text-sm text-gray-600 hover:text-gray-900 mb-2"
          >
            ← Daily scorecard
          </Link>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <Calculator className="w-6 h-6 text-bv-red-500" />
            Payout dashboard
          </h1>
          <p className="text-gray-500 text-sm mt-1 inline-flex items-center gap-2">
            Pool sizing • per-staff allocation • manager bonus
            {snapshot && (
              <span className={`text-xs px-2 py-0.5 rounded-full border ${
                snapshot.status === 'PAID' ? 'bg-emerald-50 text-emerald-700 border-emerald-200' :
                snapshot.status === 'LOCKED' ? 'bg-blue-50 text-blue-700 border-blue-200' :
                'bg-gray-50 text-gray-600 border-gray-200'
              }`}>
                {snapshot.status}
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <PeriodPicker year={year} month={month} onChange={(y, m) => { setYear(y); setMonth(m); }} />
          <Link to="/incentive/payouts" className="btn-secondary text-sm">All snapshots</Link>
          <Link to="/incentive/settings" className="btn-secondary text-sm inline-flex items-center gap-1.5">
            <Settings className="w-4 h-4" /> Settings
          </Link>
          <button onClick={refresh} disabled={loading} className="btn-secondary inline-flex items-center gap-2">
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </button>
          {snapshot && (
            <a
              href={payoutApi.csvUrl(snapshot.snapshot_id)}
              className="btn-secondary inline-flex items-center gap-2"
              target="_blank" rel="noreferrer"
            >
              <Download className="w-4 h-4" /> CSV
            </a>
          )}
          {isSuperadmin && !isLocked && (
            <button
              onClick={handleLock}
              disabled={busy || loading || !envelope || envelope.total_team_pool === 0}
              className="btn-primary inline-flex items-center gap-2 disabled:opacity-50"
            >
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Lock className="w-4 h-4" />}
              Lock month
            </button>
          )}
          {isSuperadmin && snapshot?.status === 'LOCKED' && (
            <button
              onClick={handleMarkPaid}
              disabled={busy}
              className="btn-primary inline-flex items-center gap-2"
            >
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
              Mark paid
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="card p-3 bg-amber-50 border border-amber-200 text-sm text-amber-800 inline-flex items-center gap-2">
          <AlertCircle className="w-4 h-4" /> {error}
        </div>
      )}

      {/* Inputs + Pool sizing strip */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <section className="card p-4 lg:col-span-2">
          <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">Inputs</h2>
          <div className="grid grid-cols-2 gap-3">
            <InputField
              label="Last-year sale (₹)" value={lastYear} disabled={isLocked}
              onChange={setLastYear} type="number"
              hint="Manual override of last_year_sale (defaults to aggregated last-year actuals)"
            />
            <InputField
              label="This-year sale (₹)" value={thisYear} disabled={isLocked}
              onChange={setThisYear} type="number"
              hint="Auto-derived from orders if blank"
            />
            <InputField
              label="Avg discount %" value={avgDisc} disabled={isLocked}
              onChange={setAvgDisc} type="number" step="0.01"
              hint="Floor-rounded into the multiplier tier"
            />
            <InputField
              label="Visufit usage %" value={visufit} disabled={isLocked}
              onChange={setVisufit} type="number" step="0.1"
              hint="MTD Visufit-90 metric (Module ii gate)"
            />
          </div>
          {!isLocked && (
            <div className="mt-3 text-right">
              <button onClick={refresh} disabled={loading} className="btn-primary text-xs px-3 py-1">
                Recompute
              </button>
            </div>
          )}
        </section>

        <PoolSizingCard env={view} />
      </div>

      {/* Targets row */}
      {view && (
        <section className="card p-4">
          <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">Targets</h2>
          <div className="grid grid-cols-1 tablet:grid-cols-3 gap-3">
            {(['L1', 'L2', 'L3'] as const).map(lvl => {
              const t = view.targets[lvl];
              const achieved = t.achieved;
              const isBest = view.best_level_achieved === lvl;
              return (
                <div
                  key={lvl}
                  className={`p-3 rounded border ${
                    isBest ? 'bg-emerald-50 border-emerald-300' :
                    achieved ? 'bg-blue-50 border-blue-200' :
                    'bg-gray-50 border-gray-200'
                  }`}
                >
                  <div className="text-xs uppercase tracking-wider text-gray-500">
                    {lvl} • {pct(t.growth, 0)} growth
                  </div>
                  <div className="text-xl font-bold text-gray-900 mt-1">{inr(t.target)}</div>
                  <div className={`text-xs mt-1 ${achieved ? 'text-emerald-700' : 'text-gray-500'}`}>
                    {achieved ? '✓ Achieved' : '— Not achieved'}
                    {isBest && ' (best)'}
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* Per-staff payouts */}
      {view && (
        <section className="card p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Per-staff payouts</h2>
            <span className="text-xs text-gray-500">
              Pool × weightage × eligibility (eligibility from Module ii MTD)
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200 text-xs uppercase tracking-wider text-gray-600">
                <tr>
                  <th className="px-3 py-2 text-left">Staff</th>
                  <th className="px-3 py-2 text-center">Weightage</th>
                  <th className="px-3 py-2 text-center">MTD avg</th>
                  <th className="px-3 py-2 text-center">Eligibility</th>
                  <th className="px-3 py-2 text-right">L1</th>
                  <th className="px-3 py-2 text-right">L2</th>
                  <th className="px-3 py-2 text-right">L3</th>
                  <th className="px-3 py-2 text-right bg-gray-100">Total</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {view.staff_payouts.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-3 py-6 text-center text-gray-500">
                      No staff weightages configured yet.
                    </td>
                  </tr>
                ) : view.staff_payouts.map(s => (
                  <tr key={s.user_id}>
                    <td className="px-3 py-2">
                      <div className="font-medium text-gray-900">{s.name || s.user_id}</div>
                      <div className="text-[10px] text-gray-400 font-mono">{s.user_id}</div>
                    </td>
                    <td className="px-3 py-2 text-center text-gray-700">{(s.weightage * 100).toFixed(0)}%</td>
                    <td className="px-3 py-2 text-center text-gray-700">{s.mtd_avg_total ?? '—'}</td>
                    <td className="px-3 py-2 text-center">{(s.eligibility * 100).toFixed(0)}%</td>
                    <td className="px-3 py-2 text-right text-gray-700">{inr(s.payout_by_level.L1)}</td>
                    <td className="px-3 py-2 text-right text-gray-700">{inr(s.payout_by_level.L2)}</td>
                    <td className="px-3 py-2 text-right text-gray-700">{inr(s.payout_by_level.L3)}</td>
                    <td className="px-3 py-2 text-right font-semibold bg-gray-50/50">{inr(s.total_payout)}</td>
                  </tr>
                ))}
                <tr className="bg-gray-50 font-semibold border-t-2 border-gray-200">
                  <td colSpan={7} className="px-3 py-2 text-right text-gray-700">Subtotal staff</td>
                  <td className="px-3 py-2 text-right">{inr(view.grand_total.staff)}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Manager bonuses */}
      {view && view.manager_bonuses.length > 0 && (
        <section className="card p-4">
          <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">
            Manager bonuses (stack with individual payout)
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200 text-xs uppercase tracking-wider text-gray-600">
                <tr>
                  <th className="px-3 py-2 text-left">Manager</th>
                  <th className="px-3 py-2 text-center">Role</th>
                  <th className="px-3 py-2 text-center">Eligibility</th>
                  <th className="px-3 py-2 text-right">L1 ({(view.manager_bonuses[0]?.bonus_pct.L1 * 100).toFixed(0)}%)</th>
                  <th className="px-3 py-2 text-right">L2 ({(view.manager_bonuses[0]?.bonus_pct.L2 * 100).toFixed(0)}%)</th>
                  <th className="px-3 py-2 text-right">L3 ({(view.manager_bonuses[0]?.bonus_pct.L3 * 100).toFixed(0)}%)</th>
                  <th className="px-3 py-2 text-right bg-gray-100">Total</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {view.manager_bonuses.map(m => (
                  <tr key={m.user_id}>
                    <td className="px-3 py-2 font-medium text-gray-900">{m.name || m.user_id}</td>
                    <td className="px-3 py-2 text-center text-xs text-gray-500">{m.role || '—'}</td>
                    <td className="px-3 py-2 text-center">{(m.eligibility * 100).toFixed(0)}%</td>
                    <td className="px-3 py-2 text-right text-gray-700">{inr(m.bonus_by_level.L1)}</td>
                    <td className="px-3 py-2 text-right text-gray-700">{inr(m.bonus_by_level.L2)}</td>
                    <td className="px-3 py-2 text-right text-gray-700">{inr(m.bonus_by_level.L3)}</td>
                    <td className="px-3 py-2 text-right font-semibold bg-gray-50/50">{inr(m.total_bonus)}</td>
                  </tr>
                ))}
                <tr className="bg-gray-50 font-semibold border-t-2 border-gray-200">
                  <td colSpan={6} className="px-3 py-2 text-right text-gray-700">Subtotal manager</td>
                  <td className="px-3 py-2 text-right">{inr(view.grand_total.manager)}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Grand total banner */}
      {view && (
        <section className="card p-5 bg-gradient-to-r from-bv-red-50 to-amber-50 border-2 border-bv-red-200">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs uppercase tracking-wider text-gray-600 mb-1">Grand total</div>
              <div className="text-3xl font-bold text-bv-red-700 inline-flex items-center gap-2">
                <IndianRupee className="w-6 h-6" />
                {Math.round(view.grand_total.all).toLocaleString('en-IN')}
              </div>
              <div className="text-xs text-gray-600 mt-1">
                Staff {inr(view.grand_total.staff)} • Manager {inr(view.grand_total.manager)}
              </div>
            </div>
            <div className="text-right text-xs text-gray-500">
              {view.discount_kill_active && (
                <div className="text-rose-700 font-medium inline-flex items-center gap-1 mb-1">
                  <AlertTriangle className="w-3 h-3" /> Discount kill active — pool zeroed
                </div>
              )}
              <div>Period: {view.year}-{String(view.month).padStart(2, '0')}</div>
              <div>Store: {view.store_id}</div>
            </div>
          </div>
        </section>
      )}

      {!isSuperadmin && (
        <div className="text-xs text-gray-500 inline-flex items-center gap-1">
          <Settings className="w-3 h-3" /> Lock month + Mark paid require SUPERADMIN.
        </div>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------
// Sub-components
// ----------------------------------------------------------------------------

function InputField({
  label, value, onChange, type = 'text', step, disabled, hint,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
  step?: string;
  disabled?: boolean;
  hint?: string;
}) {
  return (
    <label className="block">
      <span className="text-xs text-gray-500 mb-1 block">{label}</span>
      <input
        type={type} value={value} step={step} disabled={disabled}
        onChange={e => onChange(e.target.value)}
        className="w-full px-3 py-2 border border-gray-300 rounded text-sm disabled:bg-gray-50 disabled:text-gray-500"
      />
      {hint && <div className="text-[10px] text-gray-400 mt-0.5">{hint}</div>}
    </label>
  );
}

function PoolSizingCard({ env }: { env: PayoutEnvelope | null }) {
  if (!env) {
    return (
      <section className="card p-4 flex items-center justify-center text-sm text-gray-500">
        <Loader2 className="w-4 h-4 animate-spin mr-2" /> Computing…
      </section>
    );
  }
  return (
    <section className="card p-4 bg-gradient-to-br from-blue-50/60 to-emerald-50/60 border-blue-200">
      <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3 inline-flex items-center gap-1">
        <TrendingUp className="w-3 h-3" /> Pool sizing
      </h2>
      <div className="space-y-2">
        <Stat label="Best level achieved" value={
          <span className={`font-semibold ${
            env.best_level_achieved === 'L3' ? 'text-emerald-700' :
            env.best_level_achieved === 'L2' ? 'text-blue-700' :
            env.best_level_achieved === 'L1' ? 'text-amber-700' :
            'text-gray-500'
          }`}>
            {env.best_level_achieved || '—'}
          </span>
        } />
        <Stat label="Multiplier tier" value={env.multiplier_tier} />
        <Stat label="Multiplier" value={`${env.multiplier}×`} />
        <div className="pt-2 mt-2 border-t border-blue-200">
          <div className="text-xs uppercase tracking-wider text-gray-600">Total team pool</div>
          <div className="text-2xl font-bold text-bv-red-700 inline-flex items-center">
            <IndianRupee className="w-5 h-5" />
            {Math.round(env.total_team_pool).toLocaleString('en-IN')}
          </div>
        </div>
      </div>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between text-sm">
      <span className="text-gray-600">{label}</span>
      <span>{value}</span>
    </div>
  );
}

function PeriodPicker({
  year, month, onChange,
}: { year: number; month: number; onChange: (y: number, m: number) => void }) {
  return (
    <div className="inline-flex items-center gap-1">
      <select
        value={year} onChange={e => onChange(Number(e.target.value), month)}
        className="px-2 py-1.5 border border-gray-300 rounded text-sm"
      >
        {[2025, 2026, 2027].map(y => <option key={y} value={y}>{y}</option>)}
      </select>
      <select
        value={month} onChange={e => onChange(year, Number(e.target.value))}
        className="px-2 py-1.5 border border-gray-300 rounded text-sm"
      >
        {[1,2,3,4,5,6,7,8,9,10,11,12].map(m => (
          <option key={m} value={m}>
            {new Date(2000, m - 1).toLocaleString('default', { month: 'short' })}
          </option>
        ))}
      </select>
    </div>
  );
}

export default PayoutDashboardPage;
