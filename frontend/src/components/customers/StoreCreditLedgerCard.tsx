// ============================================================================
// IMS 2.0 - Store-credit / credit-note ledger card
// ============================================================================
// Shows a customer's running store-credit balance + auditable history, with
// issue/redeem actions for finance/manager roles. Backed by
// /customers/{id}/store-credit/{ledger,issue,redeem}.

import { useCallback, useEffect, useState } from 'react';
import { Wallet, Plus, Minus, Loader2 } from 'lucide-react';
import { customerApi } from '../../services/api/customers';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { formatDateTimeIST } from '../../utils/datetime';
import clsx from 'clsx';
import { getErrorMessage, getErrorStatus } from '../../utils/errorHandler';

interface Entry {
  entry_id: string; type: string; amount: number; delta: number;
  balance_after: number; reason?: string; ref?: string | null; created_at?: string;
}

const CAN_EDIT = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'];
// After an UNCLEAR answer the form's key is kept (sessionStorage, per customer
// and mode) so closing and reopening the form -- or a reload -- resumes the
// SAME line, never pays twice. Dropped once the ledger shows the line, on a
// success, or once the server's reconciler window (PENDING_TIMEOUT_SECONDS,
// 10 minutes) has passed and the line is settled either way.
const KEEP_MS = 10 * 60_000;
type Mode = 'issue' | 'redeem';
interface Kept { key: string; at: number; amount: string; reason: string; note: string }
const keptSlot = (cid: string, mode: Mode) => `ims_sc_key:${cid}:${mode}`;
const lineIdOf = (cid: string, mode: Mode, key: string) =>
  `${mode === 'issue' ? 'MANUAL' : 'MANUAL-REDEEM'}:${cid}:${key.slice(0, 80)}`;
function readKept(cid: string, mode: Mode): Kept | null {
  try {
    const v = JSON.parse(sessionStorage.getItem(keptSlot(cid, mode)) || 'null');
    if (v && v.key && typeof v.at === 'number' && Date.now() - v.at <= KEEP_MS) return v;
    sessionStorage.removeItem(keptSlot(cid, mode));
  } catch { /* storage unavailable: a fresh key */ }
  return null;
}
function dropKept(cid: string, mode: Mode) {
  try { sessionStorage.removeItem(keptSlot(cid, mode)); } catch { /* nothing kept */ }
}

const fc = (n: number) => `₹${Math.round(n || 0).toLocaleString('en-IN')}`;

export function StoreCreditLedgerCard({ customerId }: { customerId: string }) {
  const { user } = useAuth();
  const toast = useToast();
  const canEdit = (user?.roles || []).some((r) => CAN_EDIT.includes(r));

  const [balance, setBalance] = useState(0);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<Mode | null>(null);
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);
  // One Idempotency-Key per open form: pressing again (or a replay) resumes
  // the same line on the server, never pays or debits twice. New on each
  // open. Once a press has SENT it, the amount and reason are locked to what
  // that key carried (the server refuses other content under it): only
  // Cancel or a success closes the form and drops the key.
  const [formKey, setFormKey] = useState('');
  const [sent, setSent] = useState(false);
  // The server's own sentence after an unconfirmed answer: the ONE
  // instruction shown (no second, contradicting line of ours).
  const [unclearNote, setUnclearNote] = useState<string | null>(null);
  useEffect(() => {
    const kept = mode ? readKept(customerId, mode) : null;
    if (kept) {
      // An earlier press of this form went unanswered: resume it, locked.
      setFormKey(kept.key); setAmount(kept.amount); setReason(kept.reason);
      setSent(true); setUnclearNote(kept.note);
      return;
    }
    if (mode) setFormKey(typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`);
    setSent(false);
    setUnclearNote(null);
  }, [mode, customerId]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await customerApi.getStoreCreditLedger(customerId);
      setBalance(r.balance || 0);
      setEntries(r.entries || []);
      // A kept key whose line the ledger shows FINAL (landed, or voided)
      // is settled: drop it. A line still ISSUING (being confirmed) keeps it.
      const ids = new Set((r.entries || []).filter((e: Entry) => e.type !== 'ISSUING').map((e: Entry) => e.entry_id));
      for (const m of ['issue', 'redeem'] as Mode[]) {
        const kept = readKept(customerId, m);
        if (kept && ids.has(lineIdOf(customerId, m, kept.key))) dropKept(customerId, m);
      }
    } catch {
      /* fail-soft: leave empty */
    } finally {
      setLoading(false);
    }
  }, [customerId]);

  useEffect(() => { load(); }, [load]);

  const submit = async () => {
    const amt = parseFloat(amount);
    if (!amt || amt <= 0) { toast.error('Enter a valid amount'); return; }
    setSaving(true);
    setSent(true);
    try {
      if (mode === 'issue') await customerApi.issueStoreCredit(customerId, amt, reason, undefined, formKey);
      else await customerApi.redeemStoreCredit(customerId, amt, reason, undefined, formKey);
      toast.success(mode === 'issue' ? 'Credit issued' : 'Credit redeemed');
      if (mode) dropKept(customerId, mode);
      setMode(null); setAmount(''); setReason('');
      await load();
    } catch (e: any) {
      const status = getErrorStatus(e);
      const msg = getErrorMessage(e, 'Action failed');
      if (status !== undefined && status < 500 && !unclearNote) {
        // A definite refusal: nothing was recorded under this key, so the
        // form unlocks for a correction.
        setSent(false);
        toast.error(msg);
      } else {
        setUnclearNote(msg); // it may have landed: locked, the server's words only
        if (mode) {
          try {
            sessionStorage.setItem(keptSlot(customerId, mode), JSON.stringify(
              { key: formKey, at: readKept(customerId, mode)?.at ?? Date.now(), amount, reason, note: msg } satisfies Kept));
          } catch { /* storage unavailable: the open form still holds the key */ }
        }
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
          <Wallet className="w-4 h-4" /> Store credit
        </h3>
        <span className="text-lg font-bold text-emerald-700">{fc(balance)}</span>
      </div>

      {canEdit && (
        mode ? (
          <div className="mb-4 p-3 bg-gray-50 rounded-lg border border-gray-200 space-y-2">
            <div className="text-xs font-medium text-gray-600">{mode === 'issue' ? 'Issue credit' : 'Redeem credit'}</div>
            <input type="number" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="Amount"
              className="input-field" readOnly={sent} aria-readonly={sent} />
            <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (e.g. return CN-123)"
              className="input-field" readOnly={sent} aria-readonly={sent} />
            {unclearNote && <p role="status" className="text-xs text-amber-700">{unclearNote}</p>}
            <div className="flex gap-2 justify-end">
              <button onClick={() => { setMode(null); setAmount(''); setReason(''); }} className="px-3 py-1.5 text-xs rounded-md text-gray-600 hover:bg-gray-100">Cancel</button>
              <button onClick={submit} disabled={saving} className="px-3 py-1.5 text-xs rounded-md bg-bv-red-600 text-white disabled:opacity-50">
                {saving ? 'Saving…' : 'Confirm'}
              </button>
            </div>
          </div>
        ) : (
          <div className="flex gap-2 mb-4">
            <button onClick={() => setMode('issue')} className="px-3 py-1.5 text-xs rounded-md bg-emerald-600 text-white inline-flex items-center gap-1 hover:bg-emerald-700">
              <Plus className="w-3.5 h-3.5" /> Issue
            </button>
            <button onClick={() => setMode('redeem')} disabled={balance <= 0}
              className="px-3 py-1.5 text-xs rounded-md bg-gray-100 text-gray-700 inline-flex items-center gap-1 hover:bg-gray-200 disabled:opacity-50">
              <Minus className="w-3.5 h-3.5" /> Redeem
            </button>
          </div>
        )
      )}

      {loading ? (
        <div className="py-6 text-center"><Loader2 className="w-5 h-5 animate-spin text-gray-400 mx-auto" /></div>
      ) : entries.length === 0 ? (
        <p className="text-sm text-gray-400 py-2">No credit history.</p>
      ) : (
        <ul className="divide-y divide-gray-100 max-h-64 overflow-y-auto">
          {entries.map((e) => (
            <li key={e.entry_id} className="py-2 flex items-start justify-between gap-3">
              <div className="min-w-0">
                <span className={clsx('text-xs font-medium px-1.5 py-0.5 rounded',
                  e.type === 'ISSUED' ? 'bg-emerald-50 text-emerald-700'
                  : e.type === 'REDEEMED' ? 'bg-red-50 text-red-700' : 'bg-gray-100 text-gray-600')}>
                  {e.type}
                </span>
                {e.reason && <span className="text-sm text-gray-600 ml-2">{e.reason}</span>}
                <div className="text-xs text-gray-400">{formatDateTimeIST(e.created_at)}{e.ref ? ` · ${e.ref}` : ''}</div>
              </div>
              <div className="text-right flex-shrink-0">
                <div className={clsx('text-sm font-semibold', e.delta >= 0 ? 'text-emerald-700' : 'text-red-700')}>
                  {e.delta >= 0 ? '+' : ''}{fc(e.delta)}
                </div>
                <div className="text-[11px] text-gray-400">bal {fc(e.balance_after)}</div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default StoreCreditLedgerCard;
