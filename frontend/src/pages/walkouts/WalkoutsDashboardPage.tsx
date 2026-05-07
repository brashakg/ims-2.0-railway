// ============================================================================
// IMS 2.0 — Walkouts Dashboard (Pune Incentive Module i, Phase 4)
// ============================================================================
// Per-staff cards (walkouts MTD/today, walk-ins, conversion%, FU-due-today)
// + top-reasons bar + result-breakdown donut + FU-status per-round table
// + manual walk-in topup form (managers/admin/accountant only).

import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft, RefreshCw, Loader2, Plus, Users, TrendingUp, AlertCircle, BarChart3 } from 'lucide-react';
import { walkoutsApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import type {
  PerStaffCard,
  TopReasonsResponse,
  ResultBreakdownResponse,
  FUStatusResponse,
  WalkinTodayResponse,
} from '../../types';

const REATTRIBUTE_ROLES = new Set([
  'SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT',
]);

function hasAnyRole(roles: readonly string[] | undefined, activeRole: string | undefined, allowed: Set<string>): boolean {
  if (activeRole && allowed.has(activeRole)) return true;
  if (!roles) return false;
  return roles.some(r => allowed.has(r));
}

export function WalkoutsDashboardPage() {
  const { user } = useAuth();
  const toast = useToast();

  const userRoles = (user as any)?.roles as string[] | undefined;
  const activeRole = (user as any)?.activeRole as string | undefined;
  const canTopup = hasAnyRole(userRoles, activeRole, REATTRIBUTE_ROLES);

  const [days, setDays] = useState(30);
  const [perStaff, setPerStaff] = useState<PerStaffCard[]>([]);
  const [topReasons, setTopReasons] = useState<TopReasonsResponse | null>(null);
  const [breakdown, setBreakdown] = useState<ResultBreakdownResponse | null>(null);
  const [fuStatus, setFuStatus] = useState<FUStatusResponse | null>(null);
  const [today, setToday] = useState<WalkinTodayResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Topup form
  const [topupOpen, setTopupOpen] = useState(false);
  const [topupDelta, setTopupDelta] = useState(1);
  const [topupReason, setTopupReason] = useState('');
  const [topupBusy, setTopupBusy] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [ps, tr, br, fu, td] = await Promise.all([
        walkoutsApi.dashboardPerStaff(),
        walkoutsApi.dashboardTopReasons(days, 10),
        walkoutsApi.dashboardResultBreakdown(days),
        walkoutsApi.dashboardFuStatus(days),
        walkoutsApi.walkinsToday(),
      ]);
      setPerStaff(ps.items || []);
      setTopReasons(tr);
      setBreakdown(br);
      setFuStatus(fu);
      setToday(td);
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Failed to load dashboard';
      setError(typeof msg === 'string' ? msg : 'Failed to load dashboard');
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => { refresh(); }, [refresh]);

  const submitTopup = async () => {
    if (topupDelta < 1 || !topupReason.trim()) {
      toast.error('Need both a count and a reason');
      return;
    }
    setTopupBusy(true);
    try {
      const updated = await walkoutsApi.walkinsManualTopup({
        delta: topupDelta, reason: topupReason.trim(),
      });
      setToday(updated);
      toast.success(`+${topupDelta} walk-in${topupDelta > 1 ? 's' : ''} logged`);
      setTopupOpen(false);
      setTopupDelta(1);
      setTopupReason('');
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Topup failed';
      toast.error(typeof msg === 'string' ? msg : 'Topup failed');
    } finally {
      setTopupBusy(false);
    }
  };

  const totalToday = today?.total ?? 0;

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <Link to="/walkouts" className="inline-flex items-center gap-1 text-sm text-gray-600 hover:text-gray-900 mb-2">
            <ArrowLeft className="w-4 h-4" /> Back to walkouts
          </Link>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <BarChart3 className="w-6 h-6 text-bv-red-500" />
            Walkouts dashboard
          </h1>
          <p className="text-gray-500 text-sm mt-1">
            Per-staff conversion + reasons + follow-up status, last
            <select
              value={days}
              onChange={e => setDays(Number(e.target.value))}
              className="mx-2 px-2 py-0.5 text-sm border border-gray-300 rounded"
            >
              <option value={7}>7</option>
              <option value={30}>30</option>
              <option value={90}>90</option>
            </select>
            days.
          </p>
        </div>
        <div className="flex gap-2">
          {canTopup && (
            <button
              type="button"
              onClick={() => setTopupOpen(true)}
              className="btn-secondary inline-flex items-center gap-2"
            >
              <Plus className="w-4 h-4" /> Log walk-in
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

      {error && (
        <div className="card p-4 bg-amber-50 border border-amber-200 text-sm text-amber-800 flex items-center gap-2">
          <AlertCircle className="w-4 h-4" /> {error}
        </div>
      )}

      {/* KPI strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard
          label="Walk-ins today"
          value={totalToday}
          subtitle={today ? `${today.pos_auto_count} POS · ${today.manual_topup} manual` : ''}
          icon={<Users className="w-5 h-5 text-blue-500" />}
        />
        <KpiCard
          label={`Walkouts last ${days}d`}
          value={breakdown?.total ?? 0}
          icon={<AlertCircle className="w-5 h-5 text-amber-500" />}
        />
        <KpiCard
          label="Converted"
          value={breakdown?.buckets?.CONVERTED ?? 0}
          subtitle={breakdown?.total ? `${Math.round(100 * (breakdown.buckets.CONVERTED / breakdown.total))}% of walkouts` : ''}
          icon={<TrendingUp className="w-5 h-5 text-emerald-500" />}
        />
        <KpiCard
          label="Sales staff active"
          value={perStaff.length}
          icon={<Users className="w-5 h-5 text-bv-red-500" />}
        />
      </div>

      {/* Per-staff cards */}
      <section>
        <h2 className="text-sm font-semibold text-gray-700 mb-2">Per salesperson · MTD</h2>
        {perStaff.length === 0 ? (
          <div className="card p-6 text-center text-sm text-gray-500">
            {loading ? <Loader2 className="w-5 h-5 animate-spin mx-auto" /> : 'No salesperson activity yet this month.'}
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
            {perStaff.map(s => <StaffCard key={s.sales_person_id} card={s} />)}
          </div>
        )}
      </section>

      {/* Two-column: top reasons + result breakdown */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <section className="card p-5">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">Top reasons</h2>
          {(topReasons?.items?.length ?? 0) === 0 ? (
            <div className="text-sm text-gray-500 text-center py-6">No data.</div>
          ) : (
            <div className="space-y-2">
              {(() => {
                const max = Math.max(...(topReasons?.items?.map(i => i.count) || [1]));
                return topReasons?.items?.map(item => (
                  <div key={item.reason}>
                    <div className="flex justify-between text-xs text-gray-600 mb-1">
                      <span>{item.reason}</span>
                      <span className="font-medium text-gray-900">{item.count}</span>
                    </div>
                    <div className="h-2 bg-gray-100 rounded">
                      <div
                        className="h-2 bg-bv-red-500 rounded"
                        style={{ width: `${(100 * item.count / max).toFixed(1)}%` }}
                      />
                    </div>
                  </div>
                ));
              })()}
            </div>
          )}
        </section>

        <section className="card p-5">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">Outcome breakdown</h2>
          {breakdown ? (
            <div className="grid grid-cols-2 gap-3">
              <BucketChip label="Converted" count={breakdown.buckets.CONVERTED} total={breakdown.total} cls="bg-emerald-50 text-emerald-700 border-emerald-200" />
              <BucketChip label="Negative" count={breakdown.buckets.NEGATIVE} total={breakdown.total} cls="bg-rose-50 text-rose-700 border-rose-200" />
              <BucketChip label="Still due" count={breakdown.buckets.DUE} total={breakdown.total} cls="bg-blue-50 text-blue-700 border-blue-200" />
              <BucketChip label="No result yet" count={breakdown.buckets.no_result} total={breakdown.total} cls="bg-gray-50 text-gray-600 border-gray-200" />
            </div>
          ) : (
            <div className="text-sm text-gray-500">Loading…</div>
          )}
        </section>
      </div>

      {/* FU status */}
      <section className="card p-5">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">Follow-up status by round</h2>
        {fuStatus ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <FuStatusCard label="Round 1" data={fuStatus.fu1} />
            <FuStatusCard label="Round 2" data={fuStatus.fu2} />
          </div>
        ) : (
          <div className="text-sm text-gray-500">Loading…</div>
        )}
      </section>

      {/* Manual topup modal */}
      {topupOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-5">
            <h3 className="text-lg font-semibold text-gray-900 mb-2">Log walk-in</h3>
            <p className="text-xs text-gray-500 mb-3">
              For browse-and-leave customers who didn't reach the POS. Audited.
            </p>
            <label className="block text-xs text-gray-500 mb-1">How many?</label>
            <input
              type="number"
              min={1} max={50}
              value={topupDelta}
              onChange={e => setTopupDelta(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
              className="w-full px-3 py-2 border border-gray-300 rounded text-sm mb-3"
            />
            <label className="block text-xs text-gray-500 mb-1">Reason</label>
            <textarea
              rows={3}
              value={topupReason}
              onChange={e => setTopupReason(e.target.value)}
              placeholder="3 customers browsed sunglasses, no engagement"
              className="w-full px-3 py-2 border border-gray-300 rounded text-sm mb-4 resize-none"
            />
            <div className="flex justify-end gap-2">
              <button type="button" className="btn-secondary" disabled={topupBusy} onClick={() => setTopupOpen(false)}>Cancel</button>
              <button
                type="button"
                className="btn-primary inline-flex items-center gap-1"
                onClick={submitTopup}
                disabled={topupBusy || !topupReason.trim()}
              >
                {topupBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                Log
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function KpiCard({ label, value, subtitle, icon }: { label: string; value: number; subtitle?: string; icon?: React.ReactNode }) {
  return (
    <div className="card p-4">
      <div className="flex items-start justify-between">
        <div>
          <div className="text-xs text-gray-500 uppercase tracking-wider">{label}</div>
          <div className="text-2xl font-bold text-gray-900 mt-1">{value}</div>
          {subtitle && <div className="text-xs text-gray-500 mt-1">{subtitle}</div>}
        </div>
        {icon}
      </div>
    </div>
  );
}

function StaffCard({ card }: { card: PerStaffCard }) {
  const conv = card.conversion_pct_mtd;
  const convCls =
    conv >= 50 ? 'text-emerald-700' :
    conv >= 25 ? 'text-amber-700' :
    'text-rose-700';
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-2">
        <div className="font-medium text-gray-900 truncate">
          {card.sales_person_name || card.sales_person_id}
        </div>
        <div className={`text-sm font-semibold ${convCls}`}>{conv}%</div>
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <Stat label="Walkouts MTD" value={card.walkouts_mtd} />
        <Stat label="Walkouts today" value={card.walkouts_today} />
        <Stat label="Walk-ins today" value={card.walk_ins_today} />
        <Stat label="Walk-ins MTD" value={card.walk_ins_mtd} />
        <Stat label="Converted MTD" value={card.converted_mtd} />
        <Stat label="FU due today" value={card.fu_due_today} highlight={card.fu_due_today > 0} />
      </div>
    </div>
  );
}

function Stat({ label, value, highlight }: { label: string; value: number; highlight?: boolean }) {
  return (
    <div className={`px-2 py-1 rounded ${highlight ? 'bg-amber-50 text-amber-800' : 'bg-gray-50 text-gray-700'}`}>
      <div className="text-[10px] uppercase tracking-wider opacity-70">{label}</div>
      <div className="font-semibold">{value}</div>
    </div>
  );
}

function BucketChip({ label, count, total, cls }: { label: string; count: number; total: number; cls: string }) {
  const pct = total ? Math.round((100 * count) / total) : 0;
  return (
    <div className={`p-3 rounded border ${cls}`}>
      <div className="text-xs uppercase tracking-wider opacity-70">{label}</div>
      <div className="text-2xl font-bold mt-1">{count}</div>
      <div className="text-xs mt-1">{pct}% of {total}</div>
    </div>
  );
}

function FuStatusCard({ label, data }: { label: string; data: Record<string, number> }) {
  const total = Object.values(data).reduce((s, n) => s + n, 0);
  if (total === 0) {
    return (
      <div className="border border-gray-200 rounded p-3 text-sm text-gray-500">
        <div className="font-medium text-gray-700 mb-1">{label}</div>
        No follow-ups yet.
      </div>
    );
  }
  return (
    <div className="border border-gray-200 rounded p-3">
      <div className="font-medium text-gray-700 mb-2">{label} <span className="text-xs text-gray-400 font-normal">· {total} total</span></div>
      <div className="space-y-1">
        {Object.entries(data).map(([status, n]) => (
          <div key={status} className="flex items-center justify-between text-xs">
            <span className="text-gray-600">{status}</span>
            <span className="font-medium text-gray-900">{n}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default WalkoutsDashboardPage;
