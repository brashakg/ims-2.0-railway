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
  EyeOff,
  Globe,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useIsOnlineStore } from '../../hooks/useIsOnlineStore';
import { useToast } from '../../context/ToastContext';
import {
  cashRegisterApi,
  type CashRegisterSession,
  type ExpectedPreview,
  type SessionsResponse,
} from '../../services/api/cashRegister';
import { OFF_TILL_EXPENSE_NOTICE } from './offTillExpenseCopy';
import DenominationGrid from '../../components/cash/DenominationGrid';
import {
  blankDenoms,
  denomTotal,
  setPieces as setRowPieces,
  hasCount,
  type DenomRow,
} from '../../utils/denominations';

const inr = (n?: number | null) => `₹${Math.round(Number(n) || 0).toLocaleString('en-IN')}`;

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
  // W1.4 / OS-030: an ONLINE store has no cash drawer — hide the till
  // open/close controls (the backend rejects /cash-register/open with 400 too).
  const onlineStore = useIsOnlineStore(storeId);

  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<SessionsResponse | null>(null);
  const [busy, setBusy] = useState(false);

  // Open-form state
  const [openDenoms, setOpenDenoms] = useState<DenomRow[]>(blankDenoms());
  const [shift, setShift] = useState('PM');

  // Close-form state. The closer-typed tolerance input was DELETED (owner
  // ruling 2026-08-25): the band is the ONE store-scopable policy the server
  // reads (`till.variance_tolerance_paisa`, default Rs 100) — never a figure
  // whoever is closing gets to choose.
  const [closeDenoms, setCloseDenoms] = useState<DenomRow[]>(blankDenoms());
  const [bankDeposit, setBankDeposit] = useState('');
  const [closeNote, setCloseNote] = useState('');

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

  // BLIND WHILE COUNTING (owner ruling 2026-08-25: blind is THE day-end). The
  // live "Expected in drawer" figure and its running variance are NOT shown
  // while a count is being typed — showing the target anchors the count, the
  // exact thing the blind flow exists to prevent. Expected/variance appear
  // only AFTER the close, on the response and in the history table.

  const setOpenPieces = (i: number, pieces: number) =>
    setOpenDenoms((rows) => setRowPieces(rows, i, pieces));
  const setClosePieces = (i: number, pieces: number) =>
    setCloseDenoms((rows) => setRowPieces(rows, i, pieces));

  const handleOpen = async () => {
    setBusy(true);
    try {
      await cashRegisterApi.open({
        store_id: storeId || undefined,
        shift,
        denominations: openDenoms.filter((r) => r.pieces > 0),
        // An untouched grid is NOT a float of nothing. Say which it was.
        opening_count_state: hasCount(openDenoms) ? 'COUNTED' : 'NOT_CAPTURED',
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
        closing_count_state: hasCount(closeDenoms) ? 'COUNTED' : 'NOT_CAPTURED',
        bank_deposit: parseFloat(bankDeposit) || 0,
        note: closeNote.trim() || undefined,
      });
      const v = closed.variance ?? 0;
      if (closed.variance_status === 'NOT_COUNTED') {
        // Blank is not zero. Say what was recorded, so nobody goes looking for
        // a shortfall that was never measured.
        toast.warning(
          'Closed without a count — recorded as NOT COUNTED, not as an empty drawer. No variance was calculated.',
        );
      } else if (closed.counted_from_shared_record) {
        // The drawer was already counted at POS Day-End. That count stands --
        // say so, because the figure now on this screen is not the one that was
        // just typed into the grid, and a silently swapped number is how the
        // two screens came to disagree in the first place.
        toast.warning(
          `This drawer was already counted at Day-End (${inr(closed.counted)}). That count stands — your grid has been kept alongside it.`,
        );
      } else if (closed.variance_status === 'BALANCED') {
        toast.success('Cash register closed — drawer balanced');
      } else {
        toast.warning(
          `Closed with ${closed.variance_status === 'OVER' ? 'excess' : 'shortfall'} of ${inr(Math.abs(v))}`,
        );
      }
      setCloseDenoms(blankDenoms());
      setBankDeposit('');
      setCloseNote('');
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
          {onlineStore ? (
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-5 flex items-start gap-3">
              <Globe className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5" />
              <div className="text-sm text-blue-900">
                <p className="font-semibold mb-1">This is an online store — there is no till.</p>
                <p className="text-blue-800">
                  Payments for website orders settle via the payment gateway, so
                  there is no cash drawer to open or reconcile. Switch to a
                  physical store from the header dropdown to manage its register.
                </p>
              </div>
            </div>
          ) : openSession ? (
            <ReconcileView
              session={openSession}
              preview={preview}
              closeDenoms={closeDenoms}
              onCountChange={setClosePieces}
              countedTotal={countedTotal}
              bankDeposit={bankDeposit}
              setBankDeposit={setBankDeposit}
              closeNote={closeNote}
              setCloseNote={setCloseNote}
              onClose={handleClose}
              busy={busy}
            />
          ) : (
            <OpenView
              shift={shift}
              setShift={setShift}
              openDenoms={openDenoms}
              onCountChange={setOpenPieces}
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
                        {s.status !== 'CLOSED' ? (
                          '—'
                        ) : s.counted == null ? (
                          <span className="text-gray-500 text-xs">Not counted</span>
                        ) : (
                          inr(s.counted)
                        )}
                        {s.counted_from_shared_record && (
                          <span
                            data-testid="counted-at-day-end"
                            title="Counted at POS Day-End. One drawer, one count."
                            className="ml-1 text-[10px] font-medium text-amber-700"
                          >
                            Day-End
                          </span>
                        )}
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
  onCountChange,
  openTotal,
  onOpen,
  busy,
}: {
  shift: string;
  setShift: (s: string) => void;
  openDenoms: DenomRow[];
  onCountChange: (index: number, pieces: number) => void;
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
      <DenominationGrid rows={openDenoms} onChange={onCountChange} showHeader />
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
// BLIND WHILE COUNTING (owner ruling 2026-08-25: blind is THE day-end). This
// panel used to show "Expected in drawer", the full expected breakdown and a
// live running variance NEXT TO the count grid — anchoring the count to the
// target, the exact thing the blind flow exists to prevent. Those figures are
// gone until the close is submitted; the variance verdict arrives on the close
// response. The advisories stay: none of them carries the expected figure.
function ReconcileView({
  session,
  preview,
  closeDenoms,
  onCountChange,
  countedTotal,
  bankDeposit,
  setBankDeposit,
  closeNote,
  setCloseNote,
  onClose,
  busy,
}: {
  session: CashRegisterSession;
  preview: ExpectedPreview | null;
  closeDenoms: DenomRow[];
  onCountChange: (index: number, pieces: number) => void;
  countedTotal: number;
  bankDeposit: string;
  setBankDeposit: (s: string) => void;
  closeNote: string;
  setCloseNote: (s: string) => void;
  onClose: () => void;
  busy: boolean;
}) {
  return (
    <>
      {/* KPI strip — only what the counter already knows (no system figures) */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-4">
        <Stat label="Open since" value={fmtTime(session.opened_at)} sub={session.shift || ''} />
        <Stat label="Opening float" value={inr(session.opening_float)} />
        <Stat label="Expected cash" value="Hidden" sub="revealed after the count is submitted" />
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        {/* Denomination count */}
        <div className="bg-white border border-gray-200 rounded-lg p-5">
          <h2 className="text-sm font-semibold text-gray-700 flex items-center gap-1.5 mb-4">
            <Lock className="w-4 h-4 text-bv" /> Count the drawer
          </h2>
          <DenominationGrid rows={closeDenoms} onChange={onCountChange} showHeader />
          <div className="flex items-center justify-between mt-4 pt-4 border-t border-gray-100">
            <span className="text-sm text-gray-500">Counted cash</span>
            <span className="text-2xl font-semibold text-gray-900 tabular-nums">
              {inr(countedTotal)}
            </span>
          </div>
        </div>

        {/* Close */}
        <div className="bg-white border border-gray-200 rounded-lg p-5">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">Close the day</h2>
          <div className="rounded-lg px-3 py-2 bg-gray-50 border border-gray-200 text-gray-600 text-xs flex items-start gap-2">
            <EyeOff className="w-4 h-4 flex-shrink-0 mt-0.5" />
            <span>
              The expected figure stays hidden while you count — the variance is
              revealed when you close. Count what is actually in the drawer.
            </span>
          </div>
          <div className="flex items-center justify-between gap-3 mt-4 text-sm">
            <span className="text-gray-500">− Bank deposit</span>
            <span className="flex items-center gap-1">
              <span className="text-gray-400">₹</span>
              <input
                type="number"
                value={bankDeposit}
                onChange={(e) => setBankDeposit(e.target.value)}
                placeholder="0"
                className="w-28 text-right border border-gray-300 rounded px-2 py-1 text-sm tabular-nums"
              />
            </span>
          </div>

          {/* Double-count advisory: a recorded cash refund AND a manual cash
              payout/expense in the window may be the same money entered twice. */}
          {preview?.refund_double_entry_advisory && (
            <div className="mt-3 rounded-lg px-3 py-2 bg-amber-50 border border-amber-200 text-amber-800 text-xs flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <span>{preview.refund_double_entry_advisory.message}</span>
            </div>
          )}
          {preview?.negative_expected_advisory && (
            <div className="mt-3 rounded-lg px-3 py-2 bg-amber-50 border border-amber-200 text-amber-800 text-xs flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <span>{preview.negative_expected_message}</span>
            </div>
          )}
          {/* Something booked at this store this period is NOT paid from the
              till (salaries / advances / PF-ESI never are), so it is not in
              the expected-cash figure. Never adjust a figure a person counts
              money against without telling them.

              The backend supplies the wording and is authoritative; the local
              constant is a FALLBACK so a flagged-but-textless response renders
              a sentence rather than an empty amber box, which would read as a
              warning with nothing in it. */}
          {preview?.off_till_expense_advisory && (
            <div
              data-testid="off-till-expense-advisory"
              className="mt-3 rounded-lg px-3 py-2 bg-amber-50 border border-amber-200 text-amber-800 text-xs flex items-start gap-2"
            >
              <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <span>{preview.off_till_expense_message || OFF_TILL_EXPENSE_NOTICE}</span>
            </div>
          )}

          {/* Mandatory beyond the band: the server refuses an over/short close
              (beyond the Rs 100 policy band) without a written explanation. */}
          <div className="mt-4">
            <label className="text-xs text-gray-500 block mb-1">
              Closing note — required if the drawer is over/short beyond the allowed band
            </label>
            <textarea
              value={closeNote}
              onChange={(e) => setCloseNote(e.target.value)}
              placeholder="Explain any expected difference (e.g. change given from the safe)"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm h-16 resize-none"
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

function VarianceChip({ session }: { session: CashRegisterSession }) {
  const status = session.variance_status;
  // A drawer nobody counted has no variance to show. Showing "−₹2,000" here
  // was the whole day reported missing because the grid was left untouched.
  if (status === 'NOT_COUNTED' || session.variance == null) {
    return (
      <span
        title="Closed without a count. Not counted is not an empty drawer."
        className="inline-block rounded px-2 py-0.5 text-xs font-medium bg-gray-100 text-gray-600"
      >
        Not counted
      </span>
    );
  }
  const v = session.variance;
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
