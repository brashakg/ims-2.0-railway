// ============================================================================
// IMS 2.0 - Cash Register / EOD reconciliation (Accounts)
// ============================================================================
// Open a till session with an opening float counted by denomination, then at
// end of day count the drawer and reconcile counted vs expected. Expected cash
// = opening float + POS CASH sales for the session - cash refunds - cash
// payouts - bank deposit. Variance is colour-coded over/short.
//
// Real API only (services/api/cashRegister) -- no mock data. Empty states when
// no session is open and no history exists. BV brand tokens (bv / bv-600 /
// bv-50) only; v2 aesthetic (bg-white border-gray-200 rounded-lg).

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Wallet,
  Loader2,
  Lock,
  Unlock,
  AlertTriangle,
  CheckCircle2,
  Banknote,
  Coins,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import {
  cashRegisterApi,
  type CashRegisterSession,
  type DenomKind,
  type ExpectedPreview,
  type SessionsResponse,
} from '../../services/api/cashRegister';

const inr = (n?: number | null) => `₹${Math.round(Number(n) || 0).toLocaleString('en-IN')}`;

// Indian currency in circulation (no Rs 2000 -- RBI withdrew it).
const NOTE_FACES = [500, 200, 100, 50, 20, 10];
const COIN_FACES = [10, 5, 2, 1];

interface DenomRow {
  face: number;
  kind: DenomKind;
  pieces: number;
}

function blankDenoms(): DenomRow[] {
  return [
    ...NOTE_FACES.map((face) => ({ face, kind: 'note' as DenomKind, pieces: 0 })),
    ...COIN_FACES.map((face) => ({ face, kind: 'coin' as DenomKind, pieces: 0 })),
  ];
}

function denomTotal(rows: DenomRow[]): number {
  return rows.reduce((sum, r) => sum + r.face * (r.pieces || 0), 0);
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

export default function CashRegisterPage() {
  const { user } = useAuth();
  const toast = useToast();
  const storeId = user?.activeStoreId || user?.storeIds?.[0] || '';

  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<SessionsResponse | null>(null);
  const [busy, setBusy] = useState(false);

  // Open-form state
  const [openDenoms, setOpenDenoms] = useState<DenomRow[]>(blankDenoms());
  const [shift, setShift] = useState('PM');

  // Close-form state
  const [closeDenoms, setCloseDenoms] = useState<DenomRow[]>(blankDenoms());
  const [bankDeposit, setBankDeposit] = useState('');
  const [tolerance, setTolerance] = useState('200');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await cashRegisterApi.sessions({ store_id: storeId || undefined });
      setData(res);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load cash register');
      setData({ sessions: [], open_session: null, expected_preview: null });
    } finally {
      setLoading(false);
    }
  }, [storeId, toast]);

  useEffect(() => {
    load();
  }, [load]);

  const openSession: CashRegisterSession | null = data?.open_session ?? null;
  const preview: ExpectedPreview | null = data?.expected_preview ?? null;

  const openTotal = useMemo(() => denomTotal(openDenoms), [openDenoms]);
  const countedTotal = useMemo(() => denomTotal(closeDenoms), [closeDenoms]);

  // Live close-side reconciliation preview (counted - expected).
  const liveExpected = useMemo(() => {
    if (!preview) return 0;
    const deposit = parseFloat(bankDeposit) || 0;
    return Math.round((preview.expected - deposit) * 100) / 100;
  }, [preview, bankDeposit]);
  const liveVariance = countedTotal - liveExpected;
  const tol = Math.abs(parseFloat(tolerance) || 0);
  const liveStatus: 'BALANCED' | 'OVER' | 'SHORT' =
    Math.abs(liveVariance) <= tol ? 'BALANCED' : liveVariance > 0 ? 'OVER' : 'SHORT';

  const setPieces = (
    setter: React.Dispatch<React.SetStateAction<DenomRow[]>>,
    idx: number,
    value: string,
  ) => {
    const n = Math.max(0, parseInt(value, 10) || 0);
    setter((rows) => rows.map((r, i) => (i === idx ? { ...r, pieces: n } : r)));
  };

  const handleOpen = async () => {
    setBusy(true);
    try {
      await cashRegisterApi.open({
        store_id: storeId || undefined,
        shift,
        denominations: openDenoms.filter((r) => r.pieces > 0),
      });
      toast.success('Cash register opened');
      setOpenDenoms(blankDenoms());
      await load();
    } catch (e: unknown) {
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (e instanceof Error ? e.message : 'Could not open session');
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  };

  const handleClose = async () => {
    if (!openSession) return;
    setBusy(true);
    try {
      const closed = await cashRegisterApi.close({
        session_id: openSession.session_id,
        denominations: closeDenoms.filter((r) => r.pieces > 0),
        bank_deposit: parseFloat(bankDeposit) || 0,
        tolerance: tol,
      });
      const v = closed.variance ?? 0;
      if (closed.variance_status === 'BALANCED') {
        toast.success('Cash register closed — drawer balanced');
      } else {
        toast.warning(
          `Closed with ${closed.variance_status === 'OVER' ? 'excess' : 'shortfall'} of ${inr(Math.abs(v))}`,
        );
      }
      setCloseDenoms(blankDenoms());
      setBankDeposit('');
      await load();
    } catch (e: unknown) {
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (e instanceof Error ? e.message : 'Could not close session');
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2 mb-1">
        <Wallet className="w-5 h-5" /> Cash Register
      </h1>
      <p className="text-sm text-gray-500 mb-5">
        Open the till with a counted float, then reconcile the drawer against expected cash at
        end of day. Expected = opening float + cash sales − refunds − cash payouts − bank deposit.
      </p>

      {loading ? (
        <div className="flex items-center gap-2 text-gray-500">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading...
        </div>
      ) : (
        <>
          {/* === Live session state === */}
          {openSession ? (
            <ReconcileView
              session={openSession}
              preview={preview}
              closeDenoms={closeDenoms}
              setCloseDenoms={setCloseDenoms}
              setPieces={setPieces}
              countedTotal={countedTotal}
              liveExpected={liveExpected}
              liveVariance={liveVariance}
              liveStatus={liveStatus}
              bankDeposit={bankDeposit}
              setBankDeposit={setBankDeposit}
              tolerance={tolerance}
              setTolerance={setTolerance}
              onClose={handleClose}
              busy={busy}
            />
          ) : (
            <OpenView
              shift={shift}
              setShift={setShift}
              openDenoms={openDenoms}
              setOpenDenoms={setOpenDenoms}
              setPieces={setPieces}
              openTotal={openTotal}
              onOpen={handleOpen}
              busy={busy}
            />
          )}

          {/* === Session history === */}
          <h2 className="text-sm font-semibold text-gray-700 mt-8 mb-2">Session history</h2>
          <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead className="text-xs text-gray-500 bg-gray-50">
                <tr>
                  <th className="text-left px-4 py-2">Session</th>
                  <th className="text-left px-4 py-2">Shift</th>
                  <th className="text-left px-4 py-2">Opened</th>
                  <th className="text-left px-4 py-2">Closed</th>
                  <th className="text-right px-4 py-2">Opening</th>
                  <th className="text-right px-4 py-2">Counted</th>
                  <th className="text-right px-4 py-2">Expected</th>
                  <th className="text-right px-4 py-2">Variance</th>
                  <th className="text-left px-4 py-2">By</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {(data?.sessions?.length ?? 0) === 0 ? (
                  <tr>
                    <td colSpan={9} className="px-4 py-8 text-center text-gray-400">
                      No cash register sessions yet. Open the till to start one.
                    </td>
                  </tr>
                ) : (
                  data!.sessions.map((s) => (
                    <tr key={s.session_id} className="tabular-nums">
                      <td className="px-4 py-2 font-mono text-xs text-gray-600">
                        {s.session_id}
                      </td>
                      <td className="px-4 py-2 text-gray-500">{s.shift || '—'}</td>
                      <td className="px-4 py-2 text-gray-500">{fmtDateTime(s.opened_at)}</td>
                      <td className="px-4 py-2 text-gray-500">{fmtDateTime(s.closed_at)}</td>
                      <td className="px-4 py-2 text-right">{inr(s.opening_float)}</td>
                      <td className="px-4 py-2 text-right">
                        {s.status === 'CLOSED' ? inr(s.counted) : '—'}
                      </td>
                      <td className="px-4 py-2 text-right text-gray-500">
                        {s.status === 'CLOSED' ? inr(s.expected) : '—'}
                      </td>
                      <td className="px-4 py-2 text-right">
                        {s.status === 'CLOSED' ? <VarianceChip session={s} /> : '—'}
                      </td>
                      <td className="px-4 py-2 text-gray-500">
                        {s.closed_by_name || s.opened_by_name || '—'}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------
// Open view (no session live)
// ----------------------------------------------------------------------------
function OpenView({
  shift,
  setShift,
  openDenoms,
  setOpenDenoms,
  setPieces,
  openTotal,
  onOpen,
  busy,
}: {
  shift: string;
  setShift: (s: string) => void;
  openDenoms: DenomRow[];
  setOpenDenoms: React.Dispatch<React.SetStateAction<DenomRow[]>>;
  setPieces: (
    setter: React.Dispatch<React.SetStateAction<DenomRow[]>>,
    idx: number,
    value: string,
  ) => void;
  openTotal: number;
  onOpen: () => void;
  busy: boolean;
}) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-5">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-gray-700 flex items-center gap-1.5">
          <Unlock className="w-4 h-4 text-bv" /> Open till — count the opening float
        </h2>
        <div className="flex items-center gap-2">
          <label className="text-xs text-gray-500">Shift</label>
          <select
            value={shift}
            onChange={(e) => setShift(e.target.value)}
            className="text-sm border border-gray-300 rounded px-2 py-1 bg-white"
          >
            <option value="AM">AM</option>
            <option value="PM">PM</option>
            <option value="FULL">Full day</option>
          </select>
        </div>
      </div>
      <DenominationGrid rows={openDenoms} setter={setOpenDenoms} setPieces={setPieces} />
      <div className="flex items-center justify-between mt-4 pt-4 border-t border-gray-100">
        <span className="text-sm text-gray-500">Opening float</span>
        <span className="text-2xl font-semibold text-gray-900 tabular-nums">
          {inr(openTotal)}
        </span>
      </div>
      <button
        type="button"
        onClick={onOpen}
        disabled={busy}
        className="mt-4 w-full inline-flex items-center justify-center gap-1.5 text-sm font-medium text-white bg-bv hover:bg-bv-600 rounded-lg px-4 py-2.5 disabled:opacity-60"
      >
        {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Unlock className="w-4 h-4" />}
        Open Cash Register
      </button>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Reconcile view (session live -> close)
// ----------------------------------------------------------------------------
function ReconcileView({
  session,
  preview,
  closeDenoms,
  setCloseDenoms,
  setPieces,
  countedTotal,
  liveExpected,
  liveVariance,
  liveStatus,
  bankDeposit,
  setBankDeposit,
  tolerance,
  setTolerance,
  onClose,
  busy,
}: {
  session: CashRegisterSession;
  preview: ExpectedPreview | null;
  closeDenoms: DenomRow[];
  setCloseDenoms: React.Dispatch<React.SetStateAction<DenomRow[]>>;
  setPieces: (
    setter: React.Dispatch<React.SetStateAction<DenomRow[]>>,
    idx: number,
    value: string,
  ) => void;
  countedTotal: number;
  liveExpected: number;
  liveVariance: number;
  liveStatus: 'BALANCED' | 'OVER' | 'SHORT';
  bankDeposit: string;
  setBankDeposit: (s: string) => void;
  tolerance: string;
  setTolerance: (s: string) => void;
  onClose: () => void;
  busy: boolean;
}) {
  return (
    <>
      {/* KPI strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <Stat label="Open since" value={fmtTime(session.opened_at)} sub={session.shift || ''} />
        <Stat label="Opening float" value={inr(session.opening_float)} />
        <Stat label="Cash sales (session)" value={inr(preview?.cash_sales)} tone="good" />
        <Stat
          label="Expected in drawer"
          value={inr(liveExpected)}
          sub="after bank deposit"
        />
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        {/* Denomination count */}
        <div className="bg-white border border-gray-200 rounded-lg p-5">
          <h2 className="text-sm font-semibold text-gray-700 flex items-center gap-1.5 mb-4">
            <Lock className="w-4 h-4 text-bv" /> Count the drawer
          </h2>
          <DenominationGrid rows={closeDenoms} setter={setCloseDenoms} setPieces={setPieces} />
          <div className="flex items-center justify-between mt-4 pt-4 border-t border-gray-100">
            <span className="text-sm text-gray-500">Counted cash</span>
            <span className="text-2xl font-semibold text-gray-900 tabular-nums">
              {inr(countedTotal)}
            </span>
          </div>
        </div>

        {/* Reconciliation */}
        <div className="bg-white border border-gray-200 rounded-lg p-5">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">Reconciliation</h2>
          <dl className="text-sm space-y-2">
            <Row label="Opening float" value={inr(preview?.opening_float)} />
            <Row label="+ Cash sales" value={inr(preview?.cash_sales)} tone="good" />
            <Row label="− Cash refunds" value={inr(preview?.cash_refunds)} tone="bad" />
            <Row label="− Cash payouts / expenses" value={inr(preview?.cash_expenses)} tone="bad" />
            <div className="flex items-center justify-between gap-3">
              <dt className="text-gray-500">− Bank deposit</dt>
              <dd className="flex items-center gap-1">
                <span className="text-gray-400">₹</span>
                <input
                  type="number"
                  value={bankDeposit}
                  onChange={(e) => setBankDeposit(e.target.value)}
                  placeholder="0"
                  className="w-28 text-right border border-gray-300 rounded px-2 py-1 text-sm tabular-nums"
                />
              </dd>
            </div>
            <div className="flex items-center justify-between pt-2 border-t border-gray-100">
              <dt className="font-medium text-gray-700">Expected in drawer</dt>
              <dd className="font-semibold text-gray-900 tabular-nums">{inr(liveExpected)}</dd>
            </div>
            <div className="flex items-center justify-between">
              <dt className="font-medium text-gray-700">Counted</dt>
              <dd className="font-semibold text-gray-900 tabular-nums">{inr(countedTotal)}</dd>
            </div>
          </dl>

          {/* Variance banner */}
          <div
            className={`mt-4 rounded-lg px-3 py-3 flex items-center gap-2 text-sm font-medium ${
              liveStatus === 'BALANCED'
                ? 'bg-green-50 text-green-700'
                : liveStatus === 'OVER'
                  ? 'bg-amber-50 text-amber-700'
                  : 'bg-red-50 text-red-700'
            }`}
            role="status"
            aria-live="polite"
          >
            {liveStatus === 'BALANCED' ? (
              <>
                <CheckCircle2 className="w-4 h-4" /> Drawer balanced
                {Math.abs(liveVariance) > 0 && ` (within ±${inr(Math.abs(parseFloat(tolerance) || 0))})`}
              </>
            ) : (
              <>
                <AlertTriangle className="w-4 h-4" />
                {liveStatus === 'OVER' ? 'Cash excess: ' : 'Cash short: '}
                {inr(Math.abs(liveVariance))}
              </>
            )}
          </div>

          <div className="flex items-center gap-2 mt-3">
            <label className="text-xs text-gray-500">Tolerance ±₹</label>
            <input
              type="number"
              value={tolerance}
              onChange={(e) => setTolerance(e.target.value)}
              className="w-24 text-right border border-gray-300 rounded px-2 py-1 text-sm tabular-nums"
            />
          </div>

          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="mt-4 w-full inline-flex items-center justify-center gap-1.5 text-sm font-medium text-white bg-bv hover:bg-bv-600 rounded-lg px-4 py-2.5 disabled:opacity-60"
          >
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Lock className="w-4 h-4" />}
            Close & Reconcile
          </button>
        </div>
      </div>
    </>
  );
}

// ----------------------------------------------------------------------------
// Shared bits
// ----------------------------------------------------------------------------
function DenominationGrid({
  rows,
  setter,
  setPieces,
}: {
  rows: DenomRow[];
  setter: React.Dispatch<React.SetStateAction<DenomRow[]>>;
  setPieces: (
    setter: React.Dispatch<React.SetStateAction<DenomRow[]>>,
    idx: number,
    value: string,
  ) => void;
}) {
  return (
    <div className="space-y-1.5">
      <div className="grid grid-cols-[1fr_auto_auto] gap-3 text-xs text-gray-400 uppercase tracking-wide px-1">
        <span>Denomination</span>
        <span className="text-center w-24">Pieces</span>
        <span className="text-right w-24">Amount</span>
      </div>
      {rows.map((r, idx) => (
        <div
          key={`${r.kind}-${r.face}`}
          className="grid grid-cols-[1fr_auto_auto] gap-3 items-center px-1 py-1 rounded hover:bg-gray-50"
        >
          <span className="flex items-center gap-1.5 text-sm text-gray-700">
            {r.kind === 'coin' ? (
              <Coins className="w-3.5 h-3.5 text-gray-400" />
            ) : (
              <Banknote className="w-3.5 h-3.5 text-gray-400" />
            )}
            ₹{r.face}
            <span className="text-xs text-gray-400">{r.kind}</span>
          </span>
          <input
            type="number"
            min={0}
            value={r.pieces || ''}
            onChange={(e) => setPieces(setter, idx, e.target.value)}
            placeholder="0"
            className="w-24 text-center border border-gray-300 rounded px-2 py-1 text-sm tabular-nums"
          />
          <span className="w-24 text-right text-sm text-gray-600 tabular-nums">
            {inr(r.face * (r.pieces || 0))}
          </span>
        </div>
      ))}
    </div>
  );
}

function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: 'good';
}) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-3">
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className={`text-lg font-semibold tabular-nums ${tone === 'good' ? 'text-green-700' : 'text-gray-900'}`}>
        {value}
      </p>
      {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
    </div>
  );
}

function Row({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: 'good' | 'bad';
}) {
  const c = tone === 'good' ? 'text-green-700' : tone === 'bad' ? 'text-red-600' : 'text-gray-900';
  return (
    <div className="flex items-center justify-between">
      <dt className="text-gray-500">{label}</dt>
      <dd className={`tabular-nums ${c}`}>{value}</dd>
    </div>
  );
}

function VarianceChip({ session }: { session: CashRegisterSession }) {
  const v = session.variance ?? 0;
  const status = session.variance_status;
  const cls =
    status === 'BALANCED'
      ? 'bg-green-50 text-green-700'
      : status === 'OVER'
        ? 'bg-amber-50 text-amber-700'
        : 'bg-red-50 text-red-700';
  const sign = v > 0 ? '+' : '';
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium tabular-nums ${cls}`}>
      {status === 'BALANCED' ? inr(0) : `${sign}${inr(v)}`}
    </span>
  );
}

function fmtTime(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
}
