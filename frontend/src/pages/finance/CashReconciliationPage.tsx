// ============================================================================
// IMS 2.0 - Cash-register vs blind-EOD reconciliation console (#7)
// ============================================================================
// Manager-facing, READ-ONLY view across BOTH day-close flows in one place:
//   - CASH_REGISTER : the manual close-by-denomination sessions
//                     (cash_register_sessions, status CLOSED)
//   - BLIND_EOD     : the blind Z-Read sessions (till_sessions, status LOCKED)
// One normalised row per closed session, expected vs counted variance,
// colour-coded BALANCED / OVERAGE / SHORTAGE so an owner / store-manager can
// spot a cash disparity at a glance. Date-range + store filter, per-row
// drill-down (per-tender net), a totals row, CSV export, and an optional
// manager sign-off marker per session.
//
// Real API only (services/api/cashReconciliation, imported directly per the
// barrel-bypass convention). BV brand tokens; v2 aesthetic (bg-white
// border-gray-200 rounded-lg). Route-level role gate in App.tsx restricts to
// ADMIN / AREA_MANAGER / STORE_MANAGER / ACCOUNTANT / SUPERADMIN.

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Calculator,
  Loader2,
  AlertTriangle,
  CheckCircle2,
  Download,
  ChevronDown,
  ChevronRight,
  RefreshCw,
  Lock,
  Wallet,
  CheckCheck,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { storeApi } from '../../services/api/stores';
import {
  cashReconciliationApi,
  type CashReconSummary,
  type ReconRow,
  type ReconStatus,
} from '../../services/api/cashReconciliation';

const inr = (n?: number | null) => `₹${Math.round(Number(n) || 0).toLocaleString('en-IN')}`;

// Signed rupees: used for variance so an overage shows "+₹120", a shortage "−₹80".
function signedInr(n?: number | null): string {
  const v = Math.round(Number(n) || 0);
  if (v === 0) return '₹0';
  const sign = v > 0 ? '+' : '−';
  return `${sign}₹${Math.abs(v).toLocaleString('en-IN')}`;
}

function fmtDateTime(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function fmtDay(day?: string | null): string {
  if (!day) return '—';
  const d = new Date(`${day}T00:00:00`);
  if (Number.isNaN(d.getTime())) return day;
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function monthStartIso(): string {
  const d = new Date();
  return `${d.toISOString().slice(0, 7)}-01`;
}

interface StoreOpt {
  store_id: string;
  store_name?: string;
  store_code?: string;
}

// Colour bands for each variance status (chip + row tint).
const STATUS_CHIP: Record<ReconStatus, string> = {
  BALANCED: 'bg-green-50 text-green-700',
  OVERAGE: 'bg-amber-50 text-amber-700',
  SHORTAGE: 'bg-red-50 text-red-700',
};
const STATUS_ROW: Record<ReconStatus, string> = {
  BALANCED: '',
  OVERAGE: 'bg-amber-50/40',
  SHORTAGE: 'bg-red-50/40',
};

const HQ_ROLES = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'ACCOUNTANT'];

export default function CashReconciliationPage() {
  const { user } = useAuth();
  const toast = useToast();

  const roles: string[] = useMemo(() => user?.roles ?? [], [user]);
  // A store-scoped role (Store Manager only) cannot choose another store; the
  // backend already enforces this, but we hide the picker to avoid confusion.
  const canPickStore = useMemo(() => roles.some((r) => HQ_ROLES.includes(r)), [roles]);

  const [from, setFrom] = useState<string>(monthStartIso());
  const [to, setTo] = useState<string>(todayIso());
  const [storeId, setStoreId] = useState<string>('');

  const [stores, setStores] = useState<StoreOpt[]>([]);
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<CashReconSummary | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [signingId, setSigningId] = useState<string | null>(null);

  // Store list for the filter (HQ roles only).
  useEffect(() => {
    if (!canPickStore) return;
    storeApi
      .getStores()
      .then((r) => setStores(((r?.stores as StoreOpt[]) || []) as StoreOpt[]))
      .catch(() => setStores([]));
  }, [canPickStore]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await cashReconciliationApi.summary({
        from: from || undefined,
        to: to || undefined,
        store_id: storeId || undefined,
      });
      setData(res);
    } catch (e: unknown) {
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (e instanceof Error ? e.message : 'Failed to load reconciliation');
      toast.error(msg);
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [from, to, storeId, toast]);

  useEffect(() => {
    load();
  }, [load]);

  const rows = data?.rows ?? [];
  const totals = data?.totals;

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleSignoff = async (row: ReconRow) => {
    if (!row.session_id) return;
    setSigningId(row.session_id);
    try {
      await cashReconciliationApi.signoff({
        session_id: row.session_id,
        source: row.source,
      });
      toast.success('Session marked as reviewed');
      await load();
    } catch (e: unknown) {
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (e instanceof Error ? e.message : 'Could not record sign-off');
      toast.error(msg);
    } finally {
      setSigningId(null);
    }
  };

  const exportCsv = () => {
    if (rows.length === 0) {
      toast.info('Nothing to export');
      return;
    }
    const headers = [
      'Date',
      'Store',
      'Source',
      'Shift',
      'Opening Float',
      'Cash Sales',
      'Cash Refunds',
      'Cash Expenses',
      'Bank Deposit',
      'Expected Cash',
      'Counted Cash',
      'Variance',
      'Status',
      'Closed By',
      'Closed At',
      'Z-Read',
      'Reviewed',
    ];
    const esc = (v: unknown) => {
      const s = v === null || v === undefined ? '' : String(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const lines = rows.map((r) =>
      [
        r.session_date,
        r.store_name,
        r.source,
        r.shift || '',
        r.opening_float,
        r.cash_sales,
        r.cash_refunds,
        r.cash_expenses,
        r.bank_deposit,
        r.expected_cash,
        r.counted_cash,
        r.variance,
        r.variance_status,
        r.closed_by_name || r.closed_by || '',
        r.closed_at || '',
        r.zread_number || '',
        r.signoff?.reviewed ? 'YES' : 'NO',
      ]
        .map(esc)
        .join(','),
    );
    const csv = [headers.join(','), ...lines].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `cash-reconciliation_${from}_to_${to}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const netVarianceTone =
    !totals || Math.abs(totals.variance) < 0.005
      ? 'text-gray-900'
      : totals.variance > 0
        ? 'text-amber-700'
        : 'text-red-600';

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-1">
        <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
          <Calculator className="w-5 h-5" /> Cash Reconciliation
        </h1>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={load}
            disabled={loading}
            className="inline-flex items-center gap-1.5 text-sm text-gray-600 border border-gray-300 rounded-lg px-3 py-1.5 hover:bg-gray-50 disabled:opacity-60"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </button>
          <button
            type="button"
            onClick={exportCsv}
            className="inline-flex items-center gap-1.5 text-sm font-medium text-white bg-bv hover:bg-bv-600 rounded-lg px-3 py-1.5"
          >
            <Download className="w-4 h-4" /> Export CSV
          </button>
        </div>
      </div>
      <p className="text-sm text-gray-500 mb-5">
        Expected-vs-counted cash across the manual cash-register close and the blind end-of-day
        (Z-Read) tally. Overages and shortfalls are flagged so a disparity is easy to spot.
      </p>

      {/* === Filters === */}
      <div className="bg-white border border-gray-200 rounded-lg p-4 mb-4 flex flex-wrap items-end gap-4">
        <div>
          <label className="block text-xs text-gray-500 mb-1">From</label>
          <input
            type="date"
            value={from}
            max={to || undefined}
            onChange={(e) => setFrom(e.target.value)}
            className="text-sm border border-gray-300 rounded px-2 py-1.5 bg-white"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">To</label>
          <input
            type="date"
            value={to}
            min={from || undefined}
            onChange={(e) => setTo(e.target.value)}
            className="text-sm border border-gray-300 rounded px-2 py-1.5 bg-white"
          />
        </div>
        {canPickStore && (
          <div>
            <label className="block text-xs text-gray-500 mb-1">Store</label>
            <select
              value={storeId}
              onChange={(e) => setStoreId(e.target.value)}
              className="text-sm border border-gray-300 rounded px-2 py-1.5 bg-white min-w-[12rem]"
            >
              <option value="">All stores</option>
              {stores.map((s) => (
                <option key={s.store_id} value={s.store_id}>
                  {s.store_name || s.store_code || s.store_id}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {/* === Totals strip === */}
      {totals && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
          <Stat label="Closed sessions" value={String(totals.sessions)} />
          <Stat label="Balanced" value={String(totals.balanced)} tone="good" />
          <Stat
            label="Overages"
            value={`${totals.overage} · ${inr(totals.overage_amount)}`}
            tone={totals.overage > 0 ? 'warn' : undefined}
          />
          <Stat
            label="Shortages"
            value={`${totals.shortage} · ${inr(totals.shortage_amount)}`}
            tone={totals.shortage > 0 ? 'bad' : undefined}
          />
          <Stat label="Net variance" value={signedInr(totals.variance)} valueClass={netVarianceTone} />
        </div>
      )}

      {/* === Table === */}
      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs text-gray-500 bg-gray-50">
              <tr>
                <th className="w-8 px-2 py-2" />
                <th className="text-left px-3 py-2">Date</th>
                <th className="text-left px-3 py-2">Store</th>
                <th className="text-left px-3 py-2">Source</th>
                <th className="text-right px-3 py-2">Opening</th>
                <th className="text-right px-3 py-2">Cash sales</th>
                <th className="text-right px-3 py-2">Expected</th>
                <th className="text-right px-3 py-2">Counted</th>
                <th className="text-right px-3 py-2">Variance</th>
                <th className="text-left px-3 py-2">Closed by</th>
                <th className="text-center px-3 py-2">Review</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {loading ? (
                <tr>
                  <td colSpan={11} className="px-4 py-10 text-center text-gray-400">
                    <Loader2 className="w-4 h-4 animate-spin inline mr-2" /> Loading…
                  </td>
                </tr>
              ) : rows.length === 0 ? (
                <tr>
                  <td colSpan={11} className="px-4 py-10 text-center text-gray-400">
                    No closed cash sessions in this range.
                  </td>
                </tr>
              ) : (
                rows.map((r) => {
                  const open = expanded.has(r.session_id);
                  return (
                    <RowGroup
                      key={r.session_id}
                      row={r}
                      open={open}
                      onToggle={() => toggle(r.session_id)}
                      onSignoff={() => handleSignoff(r)}
                      signing={signingId === r.session_id}
                    />
                  );
                })
              )}
            </tbody>
            {totals && rows.length > 0 && (
              <tfoot className="bg-gray-50 text-sm font-medium text-gray-800 border-t border-gray-200">
                <tr>
                  <td className="px-2 py-2" />
                  <td className="px-3 py-2" colSpan={3}>
                    Totals · {totals.sessions} session{totals.sessions === 1 ? '' : 's'}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{inr(totals.opening_float)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{inr(totals.cash_sales)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{inr(totals.expected_cash)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{inr(totals.counted_cash)}</td>
                  <td className={`px-3 py-2 text-right tabular-nums ${netVarianceTone}`}>
                    {signedInr(totals.variance)}
                  </td>
                  <td className="px-3 py-2" colSpan={2} />
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// One session row + its expandable drill-down.
// ----------------------------------------------------------------------------
function RowGroup({
  row,
  open,
  onToggle,
  onSignoff,
  signing,
}: {
  row: ReconRow;
  open: boolean;
  onToggle: () => void;
  onSignoff: () => void;
  signing: boolean;
}) {
  const status = row.variance_status;
  const modes = Object.entries(row.by_mode || {});
  const reviewed = row.signoff?.reviewed;

  return (
    <>
      <tr className={`tabular-nums ${STATUS_ROW[status] || ''}`}>
        <td className="px-2 py-2 align-top">
          <button
            type="button"
            onClick={onToggle}
            aria-label={open ? 'Collapse' : 'Expand'}
            className="text-gray-400 hover:text-gray-700"
          >
            {open ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          </button>
        </td>
        <td className="px-3 py-2 text-gray-700">{fmtDay(row.session_date)}</td>
        <td className="px-3 py-2 text-gray-700">{row.store_name}</td>
        <td className="px-3 py-2">
          <SourceChip source={row.source} blind={row.blind} />
        </td>
        <td className="px-3 py-2 text-right text-gray-600">{inr(row.opening_float)}</td>
        <td className="px-3 py-2 text-right text-gray-600">{inr(row.cash_sales)}</td>
        <td className="px-3 py-2 text-right text-gray-700">{inr(row.expected_cash)}</td>
        <td className="px-3 py-2 text-right text-gray-900 font-medium">{inr(row.counted_cash)}</td>
        <td className="px-3 py-2 text-right">
          <VarianceChip status={status} variance={row.variance} />
        </td>
        <td className="px-3 py-2 text-gray-500">{row.closed_by_name || row.closed_by || '—'}</td>
        <td className="px-3 py-2 text-center">
          {reviewed ? (
            <span
              className="inline-flex items-center gap-1 text-xs text-green-700"
              title={`Reviewed by ${row.signoff?.reviewed_by_name || '—'} · ${fmtDateTime(
                row.signoff?.reviewed_at,
              )}`}
            >
              <CheckCheck className="w-3.5 h-3.5" /> Reviewed
            </span>
          ) : (
            <button
              type="button"
              onClick={onSignoff}
              disabled={signing}
              className="inline-flex items-center gap-1 text-xs text-bv hover:text-bv-600 disabled:opacity-60"
            >
              {signing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5" />}
              Mark reviewed
            </button>
          )}
        </td>
      </tr>

      {open && (
        <tr className="bg-gray-50/60">
          <td />
          <td colSpan={10} className="px-3 pb-4 pt-1">
            <div className="grid md:grid-cols-2 gap-4">
              {/* Cash build-up */}
              <div className="bg-white border border-gray-200 rounded-lg p-3">
                <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                  Cash build-up
                </h3>
                <dl className="text-sm space-y-1.5">
                  <DRow label="Opening float" value={inr(row.opening_float)} />
                  <DRow label="+ Cash sales" value={inr(row.cash_sales)} tone="good" />
                  {row.cash_refunds > 0 && (
                    <DRow label="− Cash refunds" value={inr(row.cash_refunds)} tone="bad" />
                  )}
                  {row.cash_expenses > 0 && (
                    <DRow label="− Cash expenses / payouts" value={inr(row.cash_expenses)} tone="bad" />
                  )}
                  {row.bank_deposit > 0 && (
                    <DRow label="− Bank deposit" value={inr(row.bank_deposit)} tone="bad" />
                  )}
                  <div className="border-t border-gray-100 pt-1.5">
                    <DRow label="Expected in drawer" value={inr(row.expected_cash)} strong />
                  </div>
                  <DRow label="Counted (physical)" value={inr(row.counted_cash)} strong />
                  <DRow
                    label="Variance"
                    value={signedInr(row.variance)}
                    tone={status === 'SHORTAGE' ? 'bad' : status === 'OVERAGE' ? 'warn' : undefined}
                    strong
                  />
                  {row.tolerance > 0 && (
                    <p className="text-xs text-gray-400 pt-1">Tolerance ±{inr(row.tolerance)}</p>
                  )}
                </dl>
              </div>

              {/* Per-tender + meta */}
              <div className="bg-white border border-gray-200 rounded-lg p-3">
                <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                  By tender
                </h3>
                {modes.length === 0 ? (
                  <p className="text-sm text-gray-400">No per-tender breakdown recorded.</p>
                ) : (
                  <dl className="text-sm space-y-1.5">
                    {modes.map(([mode, v]) => (
                      <DRow
                        key={mode}
                        label={`${mode}${v.count ? ` (${v.count})` : ''}`}
                        value={inr(v.net)}
                      />
                    ))}
                  </dl>
                )}
                <div className="border-t border-gray-100 mt-3 pt-2 text-xs text-gray-500 space-y-1">
                  <div className="flex justify-between gap-2">
                    <span>Session</span>
                    <span className="font-mono text-gray-600">{row.session_id}</span>
                  </div>
                  {row.shift && (
                    <div className="flex justify-between gap-2">
                      <span>Shift</span>
                      <span>{row.shift}</span>
                    </div>
                  )}
                  {row.zread_number && (
                    <div className="flex justify-between gap-2">
                      <span>Z-Read</span>
                      <span className="font-mono text-gray-600">{row.zread_number}</span>
                    </div>
                  )}
                  <div className="flex justify-between gap-2">
                    <span>Closed at</span>
                    <span>{fmtDateTime(row.closed_at)}</span>
                  </div>
                  <div className="flex justify-between gap-2">
                    <span>Closed by</span>
                    <span>{row.closed_by_name || row.closed_by || '—'}</span>
                  </div>
                </div>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// ----------------------------------------------------------------------------
// Small presentational helpers
// ----------------------------------------------------------------------------
function Stat({
  label,
  value,
  sub,
  tone,
  valueClass,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: 'good' | 'warn' | 'bad';
  valueClass?: string;
}) {
  const toneClass =
    valueClass ||
    (tone === 'good'
      ? 'text-green-700'
      : tone === 'warn'
        ? 'text-amber-700'
        : tone === 'bad'
          ? 'text-red-600'
          : 'text-gray-900');
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-3">
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className={`text-lg font-semibold tabular-nums ${toneClass}`}>{value}</p>
      {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
    </div>
  );
}

function DRow({
  label,
  value,
  tone,
  strong,
}: {
  label: string;
  value: string;
  tone?: 'good' | 'bad' | 'warn';
  strong?: boolean;
}) {
  const c =
    tone === 'good'
      ? 'text-green-700'
      : tone === 'bad'
        ? 'text-red-600'
        : tone === 'warn'
          ? 'text-amber-700'
          : 'text-gray-900';
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className={`text-gray-500 ${strong ? 'font-medium text-gray-700' : ''}`}>{label}</dt>
      <dd className={`tabular-nums ${c} ${strong ? 'font-semibold' : ''}`}>{value}</dd>
    </div>
  );
}

function VarianceChip({ status, variance }: { status: ReconStatus; variance: number }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium tabular-nums ${STATUS_CHIP[status]}`}
    >
      {status === 'BALANCED' ? (
        <CheckCircle2 className="w-3 h-3" />
      ) : (
        <AlertTriangle className="w-3 h-3" />
      )}
      {status === 'BALANCED' ? '₹0' : signedInr(variance)}
    </span>
  );
}

function SourceChip({ source, blind }: { source: string; blind: boolean }) {
  return (
    <span className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium bg-gray-100 text-gray-600">
      {blind ? <Lock className="w-3 h-3" /> : <Wallet className="w-3 h-3" />}
      {source === 'BLIND_EOD' ? 'Blind EOD' : 'Cash register'}
    </span>
  );
}
