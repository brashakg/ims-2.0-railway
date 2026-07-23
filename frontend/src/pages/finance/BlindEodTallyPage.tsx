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
  Banknote,
  Coins,
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
  type DenomKind,
  type VarianceStatus,
} from '../../services/api/till';

const NOTE_FACES = [500, 200, 100, 50, 20, 10];
const COIN_FACES = [20, 10, 5, 2, 1];

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

// Sum the grid in PAISA (face is rupees; *100 once at the boundary).
function denomTotalPaisa(rows: DenomRow[]): number {
  return rows.reduce((sum, r) => sum + r.face * 100 * (r.pieces || 0), 0);
}

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

function DenomGrid({
  rows,
  onChange,
  disabled,
}: {
  rows: DenomRow[];
  onChange: (i: number, pieces: number) => void;
  disabled?: boolean;
}) {
  return (
    <div className="divide-y divide-gray-100 border border-gray-200 rounded-lg overflow-hidden">
      {rows.map((r, i) => (
        <div key={`${r.kind}-${r.face}`} className="flex items-center justify-between px-3 py-1.5 text-sm">
          <span className="flex items-center gap-2 text-gray-700">
            {r.kind === 'note' ? <Banknote className="w-4 h-4 text-gray-400" /> : <Coins className="w-4 h-4 text-gray-400" />}
            <span className="tabular-nums">{`₹${r.face}`}</span>
            <span className="text-gray-400 text-xs uppercase">{r.kind}</span>
          </span>
          <span className="flex items-center gap-3">
            <input
              type="number"
              min={0}
              value={r.pieces || ''}
              disabled={disabled}
              onChange={(e) => onChange(i, Math.max(0, parseInt(e.target.value, 10) || 0))}
              className="w-20 px-2 py-1 border border-gray-300 rounded text-right tabular-nums focus:outline-none focus:ring-1 focus:ring-bv disabled:bg-gray-50"
            />
            <span className="w-24 text-right text-gray-500 tabular-nums">
              {paisaToInr(r.face * 100 * (r.pieces || 0))}
            </span>
          </span>
        </div>
      ))}
    </div>
  );
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

  // Open-phase state
  const [openDenoms, setOpenDenoms] = useState<DenomRow[]>(blankDenoms());
  const [shift, setShift] = useState('PM');

  // Blind-close state
  const [blindDenoms, setBlindDenoms] = useState<DenomRow[]>(blankDenoms());
  const [payouts, setPayouts] = useState('');
  const [confirming, setConfirming] = useState(false);

  // Reopen state
  const [reopenReason, setReopenReason] = useState('');

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
    setOpenDenoms((d) => d.map((r, idx) => (idx === i ? { ...r, pieces } : r)));
  const setBlindPieces = (i: number, pieces: number) =>
    setBlindDenoms((d) => d.map((r, idx) => (idx === i ? { ...r, pieces } : r)));

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
        blind_count_paisa: blindTotalPaisa,
        cash_payouts_paisa: Math.round((parseFloat(payouts) || 0) * 100),
        idempotency_key: `${activeSession.session_id}:blind`,
      });
      toast.success('Count submitted. Awaiting manager review.');
      setConfirming(false);
      setBlindDenoms(blankDenoms());
      setPayouts('');
      await load();
    } catch (e: any) {
      toast.error('Could not submit count');
    } finally {
      setBusy(false);
    }
  };

  const onLock = async (s: TillSession) => {
    setBusy(true);
    try {
      await tillApi.lock(s.session_id);
      toast.success('Z-Read locked');
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
          <div className="flex items-center gap-3 mt-3">
            <label className="text-sm text-gray-600">Cash paid out (₹)</label>
            <input
              type="number"
              min={0}
              value={payouts}
              disabled={confirming}
              onChange={(e) => setPayouts(e.target.value)}
              className="w-28 px-2 py-1 border border-gray-300 rounded text-right tabular-nums focus:outline-none focus:ring-1 focus:ring-bv disabled:bg-gray-50"
            />
          </div>
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
                      disabled={busy}
                      className="inline-flex items-center gap-2 px-3 py-1.5 bg-bv text-white rounded-lg text-sm font-medium hover:bg-bv-600 disabled:opacity-50"
                    >
                      <Lock className="w-4 h-4" /> Lock Z-Read
                    </button>
                  )}
                </div>
                <div className="grid grid-cols-3 gap-3 mt-3 text-sm">
                  <Figure label="Expected" value={paisaToInr(s.expected_cash_paisa)} />
                  <Figure label="Counted" value={paisaToInr(s.blind_count_paisa)} />
                  <Figure
                    label="Variance"
                    value={paisaToInr(s.variance_paisa)}
                    tone={varianceTone(s.variance_status ?? null)}
                    badge={s.variance_status ?? undefined}
                  />
                </div>
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
                <div className="grid grid-cols-3 gap-3 mt-3 text-sm">
                  <Figure label="Expected" value={paisaToInr(s.expected_cash_paisa)} />
                  <Figure label="Counted" value={paisaToInr(s.blind_count_paisa)} />
                  <Figure
                    label="Variance"
                    value={paisaToInr(s.variance_paisa)}
                    tone={varianceTone(s.variance_status ?? null)}
                    badge={s.variance_status ?? undefined}
                  />
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
