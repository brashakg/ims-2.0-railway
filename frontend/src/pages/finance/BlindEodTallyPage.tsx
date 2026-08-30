// ============================================================================
// IMS 2.0 - F23 Blind End-of-Day cash tally & Z-Read
// ============================================================================
// A BLIND count: the cashier enters the physically-counted cash WITHOUT seeing
// the system-expected figure (no anchoring). Only after a manager LOCKS does the
// system reveal expected-vs-counted variance + the Z-Read. The day is then
// SOFT-LOCKED (transparent, reopenable with a reason). Money is paisa on the
// wire; rupees in the UI. Restrained/monochrome: single BV accent, colour only
// for semantic variance (over/short).
//
// Real API only (services/api/till). v2 aesthetic: bg-white border-gray-200
// rounded-lg. No expected figure is ever shown to a cashier pre-lock -- the
// backend redacts it too (blind enforcement at the data layer).

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Wallet,
  Loader2,
  Lock,
  Unlock,
  AlertTriangle,
  CheckCircle2,
  EyeOff,
  Globe,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useIsOnlineStore } from '../../hooks/useIsOnlineStore';
import { useToast } from '../../context/ToastContext';
import {
  tillApi,
  paisaToInr,
  type TillSession,
  type ZRead,
  type VarianceStatus,
} from '../../services/api/till';
import DenomGrid from '../../components/cash/DenominationGrid';
import FaceTallyTable from '../../components/cash/FaceTallyTable';
import {
  blankDenoms,
  denomTotalPaisa,
  setPieces as setRowPieces,
  hasCount,
  type DenomRow,
} from '../../utils/denominations';

function fmtDateTime(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
}

function MANAGER_PLUS(roles: string[]): boolean {
  return roles.some((r) => ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'].includes(r));
}
function CAN_LOCK(roles: string[]): boolean {
  return roles.some((r) => ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'].includes(r));
}

function varianceTone(status: VarianceStatus): string {
  if (status === 'BALANCED') return 'text-emerald-700';
  return 'text-red-700';
}

// Out-of-band = |variance| beyond the band the verdict was judged against
// (owner ruling 2026-08-25: Rs 100 by default, store-scopable in Settings).
// Locking such a day REQUIRES a written explanation; the server enforces it.
function needsVarianceNote(s: TillSession): boolean {
  if (s.variance_paisa == null) return false;
  return Math.abs(s.variance_paisa) > Math.abs(s.tolerance_paisa ?? 0);
}

export default function BlindEodTallyPage() {
  const { user } = useAuth();
  const toast = useToast();
  const roles: string[] = user?.roles || [];
  const storeId = user?.activeStoreId || user?.storeIds?.[0] || '';
  const isManager = MANAGER_PLUS(roles);
  const canLock = CAN_LOCK(roles);
  // W1.4 / OS-030: an ONLINE store has no till — hide the blind-EOD workflow
  // (backend rejects the till open with 400 too).
  const onlineStore = useIsOnlineStore(storeId);

  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [sessions, setSessions] = useState<TillSession[]>([]);
  // Per-face tallies, fetched on demand per locked Z-Read. `null` means the
  // fetch failed -- shown as such, never as a clean tally.
  const [ledgers, setLedgers] = useState<Record<string, ZRead | null>>({});

  // Open-phase state
  const [openDenoms, setOpenDenoms] = useState<DenomRow[]>(blankDenoms());
  const [shift, setShift] = useState('PM');

  // Blind-close state. The hand-typed "cash paid out" box was DELETED (owner
  // ruling 2026-08-25): the payouts leg is auto-pulled from the expenses book
  // server-side, so there is nothing left for a cashier to key twice.
  const [blindDenoms, setBlindDenoms] = useState<DenomRow[]>(blankDenoms());
  const [confirming, setConfirming] = useState(false);

  // Reopen state
  const [reopenReason, setReopenReason] = useState('');
  // Mandatory note for an out-of-band lock (owner ruling 2026-08-25): the
  // server refuses the lock without it when |variance| is beyond the band.
  const [lockNote, setLockNote] = useState('');

  const load = useCallback(async () => {
    if (!storeId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      // Managers can list (reveals figures); cashiers cannot -- fall through to
      // their own active session via a 403-tolerant call.
      if (isManager) {
        const rows = await tillApi.list({ store_id: storeId, limit: 30 });
        setSessions(rows);
      } else {
        setSessions([]);
      }
    } catch {
      setSessions([]);
    } finally {
      setLoading(false);
    }
  }, [storeId, isManager]);

  useEffect(() => {
    void load();
  }, [load]);

  // The session this user is actively working (OPEN or BLIND_SUBMITTED), if any.
  const activeSession = useMemo(
    () => sessions.find((s) => s.status === 'OPEN' || s.status === 'BLIND_SUBMITTED') || null,
    [sessions],
  );

  const openTotalPaisa = denomTotalPaisa(openDenoms);
  const blindTotalPaisa = denomTotalPaisa(blindDenoms);

  const setOpenPieces = (i: number, pieces: number) =>
    setOpenDenoms((d) => setRowPieces(d, i, pieces));
  const setBlindPieces = (i: number, pieces: number) =>
    setBlindDenoms((d) => setRowPieces(d, i, pieces));

  const onOpen = async () => {
    if (!storeId) {
      toast.error('No store context');
      return;
    }
    setBusy(true);
    try {
      // ONE SHARED DRAWER PER STORE: a second open for today returns the EXISTING
      // shared session (already_open=true) instead of spawning a second drawer.
      const res = await tillApi.open({
        store_id: storeId,
        shift,
        opening_denominations: openDenoms.filter((r) => r.pieces > 0),
        // An untouched grid is NOT a float of nothing. Say which it was.
        opening_count_state: hasCount(openDenoms) ? 'COUNTED' : 'NOT_CAPTURED',
        opening_float_paisa: openTotalPaisa,
      });
      toast.success(res?.already_open ? "Today's drawer is already open — joined it" : 'Till opened');
      setOpenDenoms(blankDenoms());
      await load();
    } catch {
      toast.error('Could not open till');
    } finally {
      setBusy(false);
    }
  };

  const onBlindSubmit = async () => {
    if (!activeSession) return;
    setBusy(true);
    try {
      await tillApi.blindSubmit(activeSession.session_id, {
        blind_denominations: blindDenoms.filter((r) => r.pieces > 0),
        closing_count_state: hasCount(blindDenoms) ? 'COUNTED' : 'NOT_CAPTURED',
        blind_count_paisa: blindTotalPaisa,
        idempotency_key: `${activeSession.session_id}:blind`,
      });
      toast.success('Count submitted. Awaiting manager review.');
      setConfirming(false);
      setBlindDenoms(blankDenoms());
      await load();
    } catch (e: any) {
      toast.error('Could not submit count');
    } finally {
      setBusy(false);
    }
  };

  const onLock = async (s: TillSession) => {
    // Out-of-band variance needs the written explanation BEFORE the lock —
    // the server refuses it anyway (400 variance_note_required); this just
    // asks plainly instead of bouncing.
    if (needsVarianceNote(s) && !lockNote.trim()) {
      toast.error('This variance is beyond the allowed band — a note explaining it is required to lock.');
      return;
    }
    setBusy(true);
    try {
      await tillApi.lock(s.session_id, lockNote.trim() || undefined);
      toast.success('Z-Read locked');
      setLockNote('');
      await load();
    } catch {
      toast.error('Could not lock');
    } finally {
      setBusy(false);
    }
  };

  const onReopen = async (s: TillSession) => {
    if (!reopenReason.trim()) {
      toast.error('A reason is required to reopen');
      return;
    }
    setBusy(true);
    try {
      await tillApi.reopen(s.session_id, reopenReason.trim());
      toast.success('Z-Read reopened');
      setReopenReason('');
      await load();
    } catch {
      toast.error('Could not reopen');
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24 text-gray-400">
        <Loader2 className="w-6 h-6 animate-spin" />
      </div>
    );
  }

  if (!storeId) {
    return <div className="p-6 text-gray-500">No store context for your account.</div>;
  }

  // W1.4 / OS-030: online stores have no drawer to count — friendly note
  // instead of the till workflow (all hooks above ran unconditionally).
  if (onlineStore) {
    return (
      <div className="max-w-5xl mx-auto p-6">
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-5 flex items-start gap-3">
          <Globe className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5" />
          <div className="text-sm text-blue-900">
            <p className="font-semibold mb-1">This is an online store — there is no till.</p>
            <p className="text-blue-800">
              Payments for website orders settle via the payment gateway, so
              there is no drawer to count or Z-Read to lock. Switch to a
              physical store from the header dropdown to run its blind EOD.
            </p>
          </div>
        </div>
      </div>
    );
  }

  const lockedToday = sessions.filter((s) => s.status === 'LOCKED');

  const loadLedger = async (sessionId: string) => {
    try {
      const z = await tillApi.zread(sessionId);
      setLedgers((m) => ({ ...m, [sessionId]: z }));
    } catch {
      // A failed read is reported as a failed read. A missing tally must never
      // render as a drawer that tallied.
      setLedgers((m) => ({ ...m, [sessionId]: null }));
    }
  };
  const awaitingReview = sessions.filter((s) => s.status === 'BLIND_SUBMITTED');

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-6">
      <header className="flex items-center gap-3">
        <Wallet className="w-6 h-6 text-bv" />
        <div>
          <h1 className="text-xl font-semibold text-gray-900">Blind EOD Cash Tally</h1>
          <p className="text-sm text-gray-500">
            Count the drawer blind — the expected figure stays hidden until a manager locks the Z-Read.
          </p>
        </div>
      </header>

      {/* OPEN phase (no active session) */}
      {!activeSession && (
        <section className="bg-white border border-gray-200 rounded-lg p-5">
          <h2 className="text-sm font-semibold text-gray-900 mb-3">Open the till</h2>
          <div className="flex items-center gap-3 mb-3">
            <label className="text-sm text-gray-600">Shift</label>
            <select
              value={shift}
              onChange={(e) => setShift(e.target.value)}
              className="px-2 py-1 border border-gray-300 rounded text-sm focus:outline-none focus:ring-1 focus:ring-bv"
            >
              <option value="AM">Morning</option>
              <option value="PM">Evening</option>
              <option value="FULL">Full day</option>
            </select>
          </div>
          <DenomGrid rows={openDenoms} onChange={setOpenPieces} />
          <div className="flex items-center justify-between mt-3">
            <span className="text-sm text-gray-600">
              Opening float: <span className="font-semibold text-gray-900 tabular-nums">{paisaToInr(openTotalPaisa)}</span>
            </span>
            <button
              onClick={onOpen}
              disabled={busy}
              className="inline-flex items-center gap-2 px-4 py-2 bg-bv text-white rounded-lg text-sm font-medium hover:bg-bv-600 disabled:opacity-50"
            >
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Unlock className="w-4 h-4" />}
              Open Till
            </button>
          </div>
        </section>
      )}

      {/* BLIND-CLOSE phase (active session, OPEN) */}
      {activeSession && activeSession.status === 'OPEN' && (
        <section className="bg-white border border-gray-200 rounded-lg p-5">
          <div className="flex items-center gap-2 mb-1">
            <h2 className="text-sm font-semibold text-gray-900">Blind close</h2>
            <span className="inline-flex items-center gap-1 text-xs text-gray-500 bg-gray-50 border border-gray-200 rounded px-2 py-0.5">
              <EyeOff className="w-3 h-3" /> expected figure hidden
            </span>
          </div>
          <p className="text-xs text-gray-500 mb-3">
            Count every note and coin in the drawer. You will <span className="font-medium">not</span> see the
            system figure — your manager reveals the variance when they lock.
          </p>
          <DenomGrid rows={blindDenoms} onChange={setBlindPieces} disabled={confirming} />
          {/* The hand-typed payouts box is GONE (owner ruling 2026-08-25):
              petty-cash / vendor payouts come from the Expenses screen and
              customer refunds from the Returns screen — both are deducted
              automatically, so there is nothing left to key twice here. */}
          <p className="text-xs text-gray-500 mt-2">
            Cash payouts (petty cash / vendor, from the Expenses screen) and customer refunds
            (from the Returns screen) are deducted automatically — record them there, not here.
          </p>
          <div className="flex items-center justify-between mt-4">
            <span className="text-sm text-gray-600">
              Counted: <span className="font-semibold text-gray-900 tabular-nums">{paisaToInr(blindTotalPaisa)}</span>
            </span>
            {!confirming ? (
              <button
                onClick={() => setConfirming(true)}
                disabled={busy || blindTotalPaisa <= 0}
                className="inline-flex items-center gap-2 px-4 py-2 bg-bv text-white rounded-lg text-sm font-medium hover:bg-bv-600 disabled:opacity-50"
              >
                <Lock className="w-4 h-4" /> Submit Count
              </button>
            ) : (
              <div className="flex items-center gap-2">
                <span className="text-xs text-amber-700">Once submitted you cannot edit. Confirm?</span>
                <button onClick={() => setConfirming(false)} className="px-3 py-1.5 text-sm border border-gray-300 rounded-lg">
                  Cancel
                </button>
                <button
                  onClick={onBlindSubmit}
                  disabled={busy}
                  className="inline-flex items-center gap-2 px-4 py-2 bg-bv text-white rounded-lg text-sm font-medium hover:bg-bv-600 disabled:opacity-50"
                >
                  {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Lock className="w-4 h-4" />}
                  Confirm Submit
                </button>
              </div>
            )}
          </div>
        </section>
      )}

      {/* Cashier waiting state */}
      {activeSession && activeSession.status === 'BLIND_SUBMITTED' && !isManager && (
        <section className="bg-white border border-gray-200 rounded-lg p-5">
          <p className="text-sm text-gray-700">
            Count submitted (<span className="tabular-nums">{paisaToInr(activeSession.blind_count_paisa)}</span>).
            Awaiting manager review and lock.
          </p>
        </section>
      )}

      {/* MANAGER reveal panel for sessions awaiting review */}
      {isManager && awaitingReview.length > 0 && (
        <section className="bg-white border border-gray-200 rounded-lg p-5">
          <h2 className="text-sm font-semibold text-gray-900 mb-3">Awaiting lock</h2>
          <div className="space-y-3">
            {awaitingReview.map((s) => (
              <div key={s.session_id} className="border border-gray-200 rounded-lg p-4">
                <div className="flex items-center justify-between text-sm">
                  <span className="text-gray-700">
                    {s.cashier_name || s.cashier_id || 'Cashier'} · {s.shift || '—'} · opened {fmtDateTime(s.opened_at)}
                  </span>
                  {canLock && (
                    <button
                      onClick={() => onLock(s)}
                      disabled={busy || (needsVarianceNote(s) && !lockNote.trim())}
                      className="inline-flex items-center gap-2 px-3 py-1.5 bg-bv text-white rounded-lg text-sm font-medium hover:bg-bv-600 disabled:opacity-50"
                    >
                      <Lock className="w-4 h-4" /> Lock Z-Read
                    </button>
                  )}
                </div>
                {/* Mandatory above the band (owner ruling 2026-08-25): the
                    server refuses an out-of-band lock without an explanation. */}
                {canLock && needsVarianceNote(s) && (
                  <div className="mt-3">
                    <label className="text-xs text-amber-700 block mb-1 font-medium">
                      This variance is beyond the allowed band — explain it to lock
                    </label>
                    <input
                      type="text"
                      value={lockNote}
                      onChange={(e) => setLockNote(e.target.value)}
                      placeholder="e.g. Rs 200 change given from the safe, slip attached"
                      className="w-full px-2 py-1.5 border border-amber-300 rounded text-sm focus:outline-none focus:ring-1 focus:ring-bv"
                    />
                  </div>
                )}
                {/* Z-Read identity, visible so a manager can foot it by eye:
                    opening + cash sales - cash refunds - cash paid out = expected.
                    cash_sales_paisa is GROSS; recorded customer cash refunds are
                    their OWN line, never merged into the manual payout figure. */}
                <div className="grid grid-cols-3 tablet:grid-cols-6 gap-3 mt-3 text-sm">
                  <Figure label="Opening float" value={paisaToInr(s.opening_float_paisa)} />
                  <Figure label="Cash sales (gross)" value={paisaToInr(s.cash_sales_paisa)} />
                  <Figure label="Cash refunds (recorded)" value={paisaToInr(s.cash_refunds_paisa)} />
                  <Figure label="Cash payouts (auto)" value={paisaToInr(s.cash_payouts_paisa)} />
                  <Figure label="Expected" value={paisaToInr(s.expected_cash_paisa)} />
                  <Figure label="Counted" value={paisaToInr(s.blind_count_paisa)} />
                </div>
                <div className="grid grid-cols-1 gap-3 mt-3 text-sm">
                  <Figure
                    label="Variance"
                    value={paisaToInr(s.variance_paisa)}
                    tone={varianceTone(s.variance_status ?? null)}
                    badge={s.variance_status ?? undefined}
                  />
                </div>
                {s.variance_status === 'NEGATIVE_EXPECTED' && (
                  <div className="mt-2 rounded-lg px-3 py-2 bg-amber-50 border border-amber-200 text-amber-800 text-xs flex items-start gap-2">
                    <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                    <span>
                      More cash was refunded than this drawer took in - a cash-in is missing
                      (e.g. a refund funded from the safe). Record the cash-in before trusting this variance.
                    </span>
                  </div>
                )}
                {s.refund_double_entry_advisory && (
                  <div className="mt-2 rounded-lg px-3 py-2 bg-amber-50 border border-amber-200 text-amber-800 text-xs flex items-start gap-2">
                    <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                    <span>
                      This session has BOTH recorded cash refunds ({paisaToInr(s.cash_refunds_paisa)}) and a
                      manual cash payout ({paisaToInr(s.cash_payouts_paisa)}). If they are the same money, the
                      refund has been counted twice - check before signing off.
                    </span>
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* LOCKED Z-Reads (today) */}
      {isManager && lockedToday.length > 0 && (
        <section className="bg-white border border-gray-200 rounded-lg p-5">
          <h2 className="text-sm font-semibold text-gray-900 mb-3">Locked Z-Reads</h2>
          <div className="space-y-3">
            {lockedToday.map((s) => (
              <div key={s.session_id} className="border border-gray-200 rounded-lg p-4">
                <div className="flex items-center justify-between text-sm">
                  <span className="flex items-center gap-2 text-gray-700">
                    <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                    <span className="font-medium">{s.zread_number || s.session_id}</span>
                    <span className="text-gray-400">· locked {fmtDateTime(s.locked_at)} by {s.locked_by_name || '—'}</span>
                    {(s.reopen_count || 0) > 0 && (
                      <span className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-1.5 py-0.5">
                        reopened {s.reopen_count}x
                      </span>
                    )}
                  </span>
                </div>
                {/* Z-Read identity, visible so a manager can foot it by eye:
                    opening + cash sales - cash refunds - cash paid out = expected.
                    cash_sales_paisa is GROSS; recorded customer cash refunds are
                    their OWN line, never merged into the manual payout figure. */}
                <div className="grid grid-cols-3 tablet:grid-cols-6 gap-3 mt-3 text-sm">
                  <Figure label="Opening float" value={paisaToInr(s.opening_float_paisa)} />
                  <Figure label="Cash sales (gross)" value={paisaToInr(s.cash_sales_paisa)} />
                  <Figure label="Cash refunds (recorded)" value={paisaToInr(s.cash_refunds_paisa)} />
                  <Figure label="Cash payouts (auto)" value={paisaToInr(s.cash_payouts_paisa)} />
                  <Figure label="Expected" value={paisaToInr(s.expected_cash_paisa)} />
                  <Figure label="Counted" value={paisaToInr(s.blind_count_paisa)} />
                </div>
                <div className="grid grid-cols-1 gap-3 mt-3 text-sm">
                  <Figure
                    label="Variance"
                    value={paisaToInr(s.variance_paisa)}
                    tone={varianceTone(s.variance_status ?? null)}
                    badge={s.variance_status ?? undefined}
                  />
                </div>
                {s.variance_status === 'NEGATIVE_EXPECTED' && (
                  <div className="mt-2 rounded-lg px-3 py-2 bg-amber-50 border border-amber-200 text-amber-800 text-xs flex items-start gap-2">
                    <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                    <span>
                      More cash was refunded than this drawer took in - a cash-in is missing
                      (e.g. a refund funded from the safe). Record the cash-in before trusting this variance.
                    </span>
                  </div>
                )}
                {s.refund_double_entry_advisory && (
                  <div className="mt-2 rounded-lg px-3 py-2 bg-amber-50 border border-amber-200 text-amber-800 text-xs flex items-start gap-2">
                    <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                    <span>
                      This session has BOTH recorded cash refunds ({paisaToInr(s.cash_refunds_paisa)}) and a
                      manual cash payout ({paisaToInr(s.cash_payouts_paisa)}). If they are the same money, the
                      refund has been counted twice - check before signing off.
                    </span>
                  </div>
                )}
                {/* WHERE THE DIFFERENCE CAME FROM. A drawer can balance to
                    the rupee and still hide two mistakes that cancelled out;
                    this is the only view that shows them. */}
                <div className="mt-3">
                  {ledgers[s.session_id] === undefined ? (
                    <button
                      type="button"
                      onClick={() => loadLedger(s.session_id)}
                      className="text-sm font-medium text-bv hover:underline"
                    >
                      Show the note-by-note tally
                    </button>
                  ) : ledgers[s.session_id] === null ? (
                    <p className="text-sm text-gray-500">
                      Could not load the note-by-note tally.{' '}
                      <button
                        type="button"
                        onClick={() => loadLedger(s.session_id)}
                        className="font-medium text-bv hover:underline"
                      >
                        Try again
                      </button>
                    </p>
                  ) : ledgers[s.session_id]?.face_ledger ? (
                    <FaceTallyTable ledger={ledgers[s.session_id]!.face_ledger!} />
                  ) : (
                    <p className="text-sm text-gray-500">
                      This day carries no note-by-note record.
                    </p>
                  )}
                </div>
                {canLock && (
                  <div className="flex items-center gap-2 mt-3">
                    <input
                      type="text"
                      placeholder="Reason to reopen"
                      value={reopenReason}
                      onChange={(e) => setReopenReason(e.target.value)}
                      className="flex-1 px-2 py-1 border border-gray-300 rounded text-sm focus:outline-none focus:ring-1 focus:ring-bv"
                    />
                    <button
                      onClick={() => onReopen(s)}
                      disabled={busy}
                      className="inline-flex items-center gap-2 px-3 py-1.5 border border-gray-300 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50"
                    >
                      <Unlock className="w-4 h-4" /> Reopen
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {isManager && sessions.length === 0 && (
        <div className="flex items-center gap-2 text-sm text-gray-400 py-6">
          <AlertTriangle className="w-4 h-4" /> No till sessions today.
        </div>
      )}
    </div>
  );
}

function Figure({ label, value, tone, badge }: { label: string; value: string; tone?: string; badge?: string }) {
  return (
    <div className="border border-gray-100 rounded-lg p-3">
      <div className="text-xs uppercase tracking-wide text-gray-400">{label}</div>
      <div className={`text-base font-semibold tabular-nums ${tone || 'text-gray-900'}`}>{value}</div>
      {badge && <div className={`text-xs mt-0.5 ${tone || 'text-gray-500'}`}>{badge}</div>}
    </div>
  );
}
