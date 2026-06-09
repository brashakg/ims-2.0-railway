// ============================================================================
// IMS 2.0 - F17/#25 Maker-checker Journal Entries panel
// ============================================================================
// A maker (Accountant / Admin) drafts a balanced double-entry voucher and
// submits it for approval. A DIFFERENT checker (Admin / Superadmin) PIN-approves
// (the maker can never approve their own -- enforced server-side by E4), then
// posts it to the ledger. Restrained, light-only UI: neutral base + a single
// accent; colour used only for status meaning.

import { useEffect, useMemo, useState } from 'react';
import { Plus, X, Loader2, RefreshCw } from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { financeApi } from '../../services/api/finance';
import type { JournalEntry, JeStatus, ChartAccount } from './financeTypes';

const STATUS_STYLE: Record<JeStatus, string> = {
  DRAFT: 'bg-gray-100 text-gray-700',
  SUBMITTED: 'bg-amber-100 text-amber-800',
  APPROVED: 'bg-blue-100 text-blue-800',
  POSTED: 'bg-green-100 text-green-800',
  REJECTED: 'bg-red-100 text-red-800',
  REVERSED: 'bg-slate-100 text-slate-600',
};

// paisa -> "1,234.56"
const rupee = (paisa: number): string =>
  (Number(paisa || 0) / 100).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

interface DraftLine {
  account_code: string;
  amount: string;   // rupees, as typed
  side: 'debit' | 'credit';
  narration: string;
}

const blankLine = (): DraftLine => ({ account_code: '', amount: '', side: 'debit', narration: '' });

export default function JournalEntriesPanel() {
  const { user } = useAuth();
  const toast = useToast();
  const roles = useMemo(() => new Set(user?.roles || []), [user?.roles]);
  const isMaker = roles.has('ACCOUNTANT') || roles.has('ADMIN') || roles.has('SUPERADMIN');
  const isChecker = roles.has('ADMIN') || roles.has('SUPERADMIN');

  const [entries, setEntries] = useState<JournalEntry[]>([]);
  const [accounts, setAccounts] = useState<ChartAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<string>('ALL');

  // New-JE drawer state
  const [showDrawer, setShowDrawer] = useState(false);
  const [description, setDescription] = useState('');
  const [reference, setReference] = useState('');
  const [entryDate, setEntryDate] = useState(new Date().toISOString().split('T')[0]);
  const [lines, setLines] = useState<DraftLine[]>([blankLine(), blankLine()]);
  const [saving, setSaving] = useState(false);

  // Detail + action state
  const [selected, setSelected] = useState<JournalEntry | null>(null);
  const [pin, setPin] = useState('');
  const [rejectNote, setRejectNote] = useState('');
  const [acting, setActing] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [list, coa] = await Promise.allSettled([
        financeApi.listJournalEntries(statusFilter === 'ALL' ? undefined : { status: statusFilter }),
        financeApi.getChartOfAccounts({ manual_only: true }),
      ]);
      setEntries(list.status === 'fulfilled' ? (list.value?.journal_entries || []) : []);
      setAccounts(coa.status === 'fulfilled' ? (coa.value?.accounts || []) : []);
    } catch {
      toast.error('Failed to load journal entries');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter]);

  // ---- Draft totals (rupees, client-side, for the BALANCED check) ----------
  const totals = useMemo(() => {
    let debit = 0;
    let credit = 0;
    for (const ln of lines) {
      const amt = parseFloat(ln.amount);
      if (!Number.isFinite(amt) || amt <= 0) continue;
      if (ln.side === 'debit') debit += amt;
      else credit += amt;
    }
    return { debit: Math.round(debit * 100) / 100, credit: Math.round(credit * 100) / 100 };
  }, [lines]);

  const balanced = totals.debit > 0 && Math.abs(totals.debit - totals.credit) < 0.005;
  const enoughLines = lines.filter((l) => parseFloat(l.amount) > 0 && l.account_code).length >= 2;
  const canSubmit = balanced && enoughLines && description.trim().length > 0;

  const resetDraft = () => {
    setDescription('');
    setReference('');
    setEntryDate(new Date().toISOString().split('T')[0]);
    setLines([blankLine(), blankLine()]);
  };

  const saveDraft = async (thenSubmit: boolean) => {
    if (!canSubmit) return;
    setSaving(true);
    try {
      const payload = {
        description: description.trim(),
        reference: reference.trim() || undefined,
        entry_date: entryDate,
        lines: lines
          .filter((l) => parseFloat(l.amount) > 0 && l.account_code)
          .map((l) => ({
            account_code: l.account_code,
            debit: l.side === 'debit' ? parseFloat(l.amount) : 0,
            credit: l.side === 'credit' ? parseFloat(l.amount) : 0,
            narration: l.narration.trim() || undefined,
          })),
      };
      const created = await financeApi.createJournalEntry(payload);
      if (thenSubmit && created?.je?.je_id) {
        await financeApi.submitJournalEntry(created.je.je_id);
        toast.success('Journal entry submitted for approval');
      } else {
        toast.success('Journal entry saved as draft');
      }
      setShowDrawer(false);
      resetDraft();
      load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail?.error || e?.response?.data?.detail || 'Could not save journal entry');
    } finally {
      setSaving(false);
    }
  };

  const submitExisting = async (je: JournalEntry) => {
    setActing(true);
    try {
      await financeApi.submitJournalEntry(je.je_id);
      toast.success('Submitted for approval');
      setSelected(null);
      load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail?.error || 'Submit failed');
    } finally {
      setActing(false);
    }
  };

  const doApprove = async (je: JournalEntry) => {
    if (pin.length < 4) { toast.error('Enter your approval PIN'); return; }
    setActing(true);
    try {
      await financeApi.approveJournalEntry(je.je_id, pin);
      toast.success('Approved');
      setPin('');
      setSelected(null);
      load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail?.error || 'Approve failed');
    } finally {
      setActing(false);
    }
  };

  const doReject = async (je: JournalEntry) => {
    if (pin.length < 4) { toast.error('Enter your approval PIN'); return; }
    if (rejectNote.trim().length < 10) { toast.error('A rejection note of at least 10 characters is required'); return; }
    setActing(true);
    try {
      await financeApi.rejectJournalEntry(je.je_id, pin, rejectNote.trim());
      toast.success('Rejected');
      setPin('');
      setRejectNote('');
      setSelected(null);
      load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail?.error || 'Reject failed');
    } finally {
      setActing(false);
    }
  };

  const doPost = async (je: JournalEntry) => {
    setActing(true);
    try {
      await financeApi.postJournalEntry(je.je_id);
      toast.success('Posted to ledger');
      setSelected(null);
      load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail?.error || 'Post failed');
    } finally {
      setActing(false);
    }
  };

  const doReverse = async (je: JournalEntry) => {
    setActing(true);
    try {
      await financeApi.reverseJournalEntry(je.je_id);
      toast.success('Reversal voucher posted');
      setSelected(null);
      load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail?.error || 'Reverse failed');
    } finally {
      setActing(false);
    }
  };

  const isOwnEntry = (je: JournalEntry) => je.maker_id === user?.id;

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 text-gray-500">
        <Loader2 className="w-6 h-6 animate-spin mr-2" /> Loading journal entries...
      </div>
    );
  }

  return (
    <div>
      {/* Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <div className="flex items-center gap-2">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="border border-gray-200 rounded px-3 py-2 text-sm bg-white text-gray-900 focus:outline-none focus:border-bv-red-400"
          >
            <option value="ALL">All statuses</option>
            <option value="DRAFT">Draft</option>
            <option value="SUBMITTED">Submitted</option>
            <option value="APPROVED">Approved</option>
            <option value="POSTED">Posted</option>
            <option value="REJECTED">Rejected</option>
            <option value="REVERSED">Reversed</option>
          </select>
          <button onClick={load} className="btn-secondary inline-flex items-center gap-1.5 text-sm">
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
        </div>
        {isMaker && (
          <button
            onClick={() => { resetDraft(); setShowDrawer(true); }}
            className="btn-primary inline-flex items-center gap-1.5 text-sm"
          >
            <Plus className="w-4 h-4" /> New Journal Entry
          </button>
        )}
      </div>

      {/* List */}
      <div className="card overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="px-3 py-2 text-left">JE Number</th>
              <th className="px-3 py-2 text-left">Date</th>
              <th className="px-3 py-2 text-left">Description</th>
              <th className="px-3 py-2 text-right">Amount</th>
              <th className="px-3 py-2 text-left">Status</th>
              <th className="px-3 py-2 text-left">Maker</th>
            </tr>
          </thead>
          <tbody>
            {entries.length === 0 && (
              <tr><td colSpan={6} className="px-3 py-8 text-center text-gray-400">No journal entries.</td></tr>
            )}
            {entries.map((je) => (
              <tr
                key={je.je_id}
                onClick={() => { setSelected(je); setPin(''); setRejectNote(''); }}
                className="border-t border-gray-100 cursor-pointer hover:bg-gray-50"
              >
                <td className="px-3 py-2 font-medium text-gray-900">{je.je_number}</td>
                <td className="px-3 py-2 text-gray-600">{(je.entry_date || '').slice(0, 10)}</td>
                <td className="px-3 py-2 text-gray-700">{je.description}</td>
                <td className="px-3 py-2 text-right tabular-nums">&#8377;{rupee(je.total_debit)}</td>
                <td className="px-3 py-2">
                  <span className={clsx('inline-block px-2 py-0.5 rounded text-xs font-medium', STATUS_STYLE[je.status])}>
                    {je.status}
                  </span>
                </td>
                <td className="px-3 py-2 text-gray-600">{je.maker_name || je.maker_id}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* New-JE drawer */}
      {showDrawer && (
        <div className="fixed inset-0 z-40 flex justify-end bg-black/30" onClick={() => setShowDrawer(false)}>
          <div
            className="w-full max-w-xl bg-white h-full overflow-y-auto shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="sticky top-0 bg-white border-b border-gray-200 px-5 py-3 flex items-center justify-between">
              <h3 className="font-semibold text-gray-900">New Journal Entry</h3>
              <button onClick={() => setShowDrawer(false)} className="text-gray-400 hover:text-gray-700"><X className="w-5 h-5" /></button>
            </div>
            <div className="p-5 space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <label className="text-sm">
                  <span className="block text-gray-600 mb-1">Entry date</span>
                  <input type="date" value={entryDate} onChange={(e) => setEntryDate(e.target.value)}
                    className="w-full border border-gray-200 rounded px-3 py-2 text-sm focus:outline-none focus:border-bv-red-400" />
                </label>
                <label className="text-sm">
                  <span className="block text-gray-600 mb-1">Reference (optional)</span>
                  <input value={reference} onChange={(e) => setReference(e.target.value)}
                    className="w-full border border-gray-200 rounded px-3 py-2 text-sm focus:outline-none focus:border-bv-red-400" />
                </label>
              </div>
              <label className="text-sm block">
                <span className="block text-gray-600 mb-1">Description</span>
                <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500}
                  placeholder="e.g. Depreciation for May 2026"
                  className="w-full border border-gray-200 rounded px-3 py-2 text-sm focus:outline-none focus:border-bv-red-400" />
              </label>

              {/* Lines */}
              <div className="border border-gray-200 rounded">
                <div className="px-3 py-2 bg-gray-50 text-xs font-medium text-gray-600 border-b border-gray-200">Lines</div>
                <div className="divide-y divide-gray-100">
                  {lines.map((ln, i) => (
                    <div key={i} className="p-3 grid grid-cols-12 gap-2 items-center">
                      <select
                        value={ln.account_code}
                        onChange={(e) => setLines((ls) => ls.map((l, j) => j === i ? { ...l, account_code: e.target.value } : l))}
                        className="col-span-5 border border-gray-200 rounded px-2 py-1.5 text-sm focus:outline-none focus:border-bv-red-400"
                      >
                        <option value="">Account...</option>
                        {accounts.map((a) => (
                          <option key={a.account_code} value={a.account_code}>{a.account_code} - {a.account_name}</option>
                        ))}
                      </select>
                      <select
                        value={ln.side}
                        onChange={(e) => setLines((ls) => ls.map((l, j) => j === i ? { ...l, side: e.target.value as 'debit' | 'credit' } : l))}
                        className="col-span-3 border border-gray-200 rounded px-2 py-1.5 text-sm focus:outline-none focus:border-bv-red-400"
                      >
                        <option value="debit">Debit</option>
                        <option value="credit">Credit</option>
                      </select>
                      <input
                        type="number" min="0" step="0.01" value={ln.amount} placeholder="0.00"
                        onChange={(e) => setLines((ls) => ls.map((l, j) => j === i ? { ...l, amount: e.target.value } : l))}
                        className="col-span-3 border border-gray-200 rounded px-2 py-1.5 text-sm text-right focus:outline-none focus:border-bv-red-400"
                      />
                      <button
                        onClick={() => setLines((ls) => ls.length > 2 ? ls.filter((_, j) => j !== i) : ls)}
                        disabled={lines.length <= 2}
                        className="col-span-1 text-gray-300 hover:text-red-600 disabled:opacity-30"
                        title="Remove line"
                      ><X className="w-4 h-4" /></button>
                    </div>
                  ))}
                </div>
                <div className="px-3 py-2 border-t border-gray-200">
                  <button onClick={() => setLines((ls) => [...ls, blankLine()])} className="text-sm text-bv-red-600 hover:underline">+ Add line</button>
                </div>
                <div className="px-3 py-2 bg-gray-50 border-t border-gray-200 flex items-center justify-between text-sm">
                  <span className="text-gray-600">Debit &#8377;{totals.debit.toFixed(2)} &nbsp;|&nbsp; Credit &#8377;{totals.credit.toFixed(2)}</span>
                  {balanced
                    ? <span className="text-green-700 font-medium">Balanced</span>
                    : <span className="text-red-600 font-medium">Out by &#8377;{Math.abs(totals.debit - totals.credit).toFixed(2)}</span>}
                </div>
              </div>

              <div className="flex gap-2 pt-2">
                <button onClick={() => saveDraft(false)} disabled={!canSubmit || saving} className="btn-secondary text-sm disabled:opacity-50">
                  Save draft
                </button>
                <button onClick={() => saveDraft(true)} disabled={!canSubmit || saving} className="btn-primary text-sm disabled:opacity-50">
                  {saving ? 'Saving...' : 'Submit for approval'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Detail + actions modal */}
      {selected && (
        <div className="fixed inset-0 z-40 flex justify-end bg-black/30" onClick={() => setSelected(null)}>
          <div className="w-full max-w-xl bg-white h-full overflow-y-auto shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="sticky top-0 bg-white border-b border-gray-200 px-5 py-3 flex items-center justify-between">
              <div>
                <h3 className="font-semibold text-gray-900">{selected.je_number}</h3>
                <span className={clsx('inline-block mt-1 px-2 py-0.5 rounded text-xs font-medium', STATUS_STYLE[selected.status])}>{selected.status}</span>
              </div>
              <button onClick={() => setSelected(null)} className="text-gray-400 hover:text-gray-700"><X className="w-5 h-5" /></button>
            </div>
            <div className="p-5 space-y-4 text-sm">
              <div className="grid grid-cols-2 gap-y-1 gap-x-4 text-gray-700">
                <div><span className="text-gray-500">Date</span><div>{(selected.entry_date || '').slice(0, 10)}</div></div>
                <div><span className="text-gray-500">Maker</span><div>{selected.maker_name || selected.maker_id}</div></div>
                <div className="col-span-2"><span className="text-gray-500">Description</span><div>{selected.description}</div></div>
                {selected.reference && <div className="col-span-2"><span className="text-gray-500">Reference</span><div>{selected.reference}</div></div>}
                {selected.checker_note && <div className="col-span-2"><span className="text-gray-500">Checker note</span><div className="text-red-700">{selected.checker_note}</div></div>}
                {selected.reversed_by && <div className="col-span-2 text-slate-600">Reversed by {selected.reversed_by}</div>}
                {selected.reversal_of && <div className="col-span-2 text-slate-600">Reversal of {selected.reversal_of}</div>}
              </div>

              <div className="border border-gray-200 rounded overflow-hidden">
                <table className="min-w-full">
                  <thead className="bg-gray-50 text-gray-600 text-xs">
                    <tr><th className="px-3 py-1.5 text-left">Account</th><th className="px-3 py-1.5 text-right">Debit</th><th className="px-3 py-1.5 text-right">Credit</th></tr>
                  </thead>
                  <tbody>
                    {selected.lines.map((l) => (
                      <tr key={l.line_id || l.account_code} className="border-t border-gray-100">
                        <td className="px-3 py-1.5">{l.account_code} - {l.account_name}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{l.debit ? `₹${rupee(l.debit)}` : ''}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{l.credit ? `₹${rupee(l.credit)}` : ''}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Maker: submit own DRAFT */}
              {selected.status === 'DRAFT' && isMaker && isOwnEntry(selected) && (
                <button onClick={() => submitExisting(selected)} disabled={acting} className="btn-primary text-sm disabled:opacity-50">
                  Submit for approval
                </button>
              )}

              {/* Checker: approve / reject a SUBMITTED entry (PIN-gated) */}
              {selected.status === 'SUBMITTED' && isChecker && (
                isOwnEntry(selected) ? (
                  <div className="text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
                    You drafted this entry. A different checker must approve it.
                  </div>
                ) : (
                  <div className="space-y-3 border-t border-gray-200 pt-3">
                    <label className="block">
                      <span className="block text-gray-600 mb-1">Approval PIN</span>
                      <input type="password" inputMode="numeric" value={pin} maxLength={6}
                        onChange={(e) => setPin(e.target.value.replace(/\D/g, ''))}
                        className="w-40 border border-gray-200 rounded px-3 py-2 text-sm focus:outline-none focus:border-bv-red-400" />
                    </label>
                    <label className="block">
                      <span className="block text-gray-600 mb-1">Rejection note (required to reject)</span>
                      <textarea value={rejectNote} onChange={(e) => setRejectNote(e.target.value)} rows={2} maxLength={500}
                        className="w-full border border-gray-200 rounded px-3 py-2 text-sm focus:outline-none focus:border-bv-red-400" />
                    </label>
                    <div className="flex gap-2">
                      <button onClick={() => doApprove(selected)} disabled={acting} className="btn-primary text-sm disabled:opacity-50">Approve</button>
                      <button onClick={() => doReject(selected)} disabled={acting} className="btn-secondary text-sm text-red-700 disabled:opacity-50">Reject</button>
                    </div>
                  </div>
                )
              )}

              {/* Checker: post an APPROVED entry */}
              {selected.status === 'APPROVED' && isChecker && (
                <button onClick={() => doPost(selected)} disabled={acting} className="btn-primary text-sm disabled:opacity-50">
                  Post to ledger
                </button>
              )}

              {/* Checker: reverse a POSTED entry */}
              {selected.status === 'POSTED' && isChecker && !selected.reversed_by && (
                <button onClick={() => doReverse(selected)} disabled={acting} className="btn-secondary text-sm disabled:opacity-50">
                  Reverse this entry
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
