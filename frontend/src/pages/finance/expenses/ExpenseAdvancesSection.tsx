// /finance/expenses/advances -- Advances (every signed-in user; the server
// decides whose rows come back). Moved byte-identical from the old
// ExpenseTracker 'advances' tab, with its loader, handlers and request modal.

import { useState, useEffect, useCallback } from 'react';
import { Plus, Check, X as XIcon, Loader2, Banknote, Scale } from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../../context/AuthContext';
import { useToast } from '../../../context/ToastContext';
import {
  expensesApi,
  ADVANCE_TYPES,
  advanceTypeLabel,
  type AdvanceType,
  type AdvanceRecord,
} from '../../../services/api/expenses';
import { formatDateIST } from '../../../utils/datetime';
import { fc, useExpensesContext, type ApiError } from './expenseShared';
import { Labeled } from './expenseWidgets';

const ADVANCE_STATUS_META: Record<string, { label: string; badge: string }> = {
  PENDING: { label: 'Awaiting approval', badge: 'bg-amber-50 text-amber-700' },
  APPROVED: { label: 'Approved — cash not yet given', badge: 'bg-blue-50 text-blue-700' },
  DISBURSED: { label: 'Cash given', badge: 'bg-green-50 text-green-700' },
  PARTIALLY_SETTLED: { label: 'Partly settled', badge: 'bg-amber-50 text-amber-700' },
  SETTLED: { label: 'Settled', badge: 'bg-gray-100 text-gray-600' },
};

export function ExpenseAdvancesSection() {
  const { user } = useAuth();
  const toast = useToast();
  const { isApprover } = useExpensesContext();

  // Advances. The backend decides what comes back: approvers get the store's
  // advances, everyone else only their own.
  const [advances, setAdvances] = useState<AdvanceRecord[]>([]);
  const [advancesLoading, setAdvancesLoading] = useState(false);
  const [showAdvanceModal, setShowAdvanceModal] = useState(false);
  // No preselected reason: the requester must choose one deliberately.
  const [advType, setAdvType] = useState<AdvanceType | ''>('');
  const [advAmount, setAdvAmount] = useState('');
  const [advPurpose, setAdvPurpose] = useState('');
  const [advSettleBy, setAdvSettleBy] = useState('');
  const [saving, setSaving] = useState(false);

  const loadAdvances = useCallback(async () => {
    setAdvancesLoading(true);
    try {
      const res = await expensesApi.getAdvances({ store_id: user?.activeStoreId });
      setAdvances(res?.advances || []);
    } catch {
      setAdvances([]);
      toast.error('Failed to load advances');
    } finally {
      setAdvancesLoading(false);
    }
  }, [user?.activeStoreId, toast]);

  useEffect(() => { loadAdvances(); }, [loadAdvances]);

  const handleRequestAdvance = async () => {
    if (!advType) { toast.error('Choose what the advance is for'); return; }
    if (!advAmount || Number(advAmount) <= 0) { toast.error('Enter a valid amount'); return; }
    if (!advPurpose.trim()) { toast.error('Say what the money is for'); return; }
    setSaving(true);
    try {
      await expensesApi.createAdvance({
        advance_type: advType,
        amount: parseFloat(advAmount),
        purpose: advPurpose.trim(),
        expected_settlement_date: advSettleBy || undefined,
      });
      toast.success('Advance requested');
      setShowAdvanceModal(false);
      setAdvType(''); setAdvAmount(''); setAdvPurpose(''); setAdvSettleBy('');
      await loadAdvances();
    } catch (err) {
      // The backend blocks a second unsettled advance (400) and refuses a
      // reason that is not on the list (422). Show what it said.
      const e = err as ApiError;
      const d = e?.response?.data?.detail;
      const msg = typeof d === 'string' ? d
        : Array.isArray(d) ? (d[0] as { msg?: string })?.msg || 'Check the form and try again'
        : 'Failed to request advance';
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  };

  const advanceAction = async (fn: () => Promise<unknown>, ok: string) => {
    try { await fn(); toast.success(ok); await loadAdvances(); }
    catch (err) {
      const e = err as ApiError;
      const d = e?.response?.data?.detail;
      toast.error(typeof d === 'string' ? d : 'Action failed');
    }
  };

  return (
    <>
      {/* Advances. Who is listed here is decided by the server: an approver
          sees the whole store, everyone else sees only their own rows. */}
      <div className="space-y-4">
        <div className="bg-white rounded-lg border border-gray-200 p-4 flex flex-wrap items-center justify-between gap-3">
          <div className="text-sm text-gray-600">
            {isApprover
              ? 'Every advance for this store. Approve the request, then record the cash you hand over.'
              : 'Your advances. Colleagues’ advances are visible only to the people who approve them.'}
            {' '}Salary advances are not requested here — ask an administrator to raise one in Payroll.
          </div>
          <button className="btn sm primary" onClick={() => setShowAdvanceModal(true)}>
            <Plus className="w-4 h-4" /> Request advance
          </button>
        </div>

        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          {advancesLoading ? (
            <div className="flex items-center justify-center h-40"><Loader2 className="w-6 h-6 text-bv-red-600 animate-spin" /></div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-gray-200 text-left text-xs font-semibold text-gray-500 uppercase">
                    <th className="px-4 py-3">Requested</th>
                    {isApprover && <th className="px-4 py-3">By</th>}
                    <th className="px-4 py-3">For</th>
                    <th className="px-4 py-3 text-right">Amount</th>
                    <th className="px-4 py-3">Purpose</th>
                    <th className="px-4 py-3">Settle by</th>
                    <th className="px-4 py-3">Status</th>
                    {isApprover && <th className="px-4 py-3 text-right">Actions</th>}
                  </tr>
                </thead>
                <tbody>
                  {advances.map((a) => {
                    const st = (a.status || '').toUpperCase();
                    const meta = ADVANCE_STATUS_META[st] || ADVANCE_STATUS_META.PENDING;
                    const isMine = a.employee_id === user?.id;
                    return (
                      <tr key={a.advance_id} className="border-b border-gray-100 hover:bg-gray-50">
                        <td className="px-4 py-3 text-sm text-gray-500 whitespace-nowrap">{formatDateIST(a.created_at)}</td>
                        {isApprover && <td className="px-4 py-3 text-sm text-gray-700">{a.employee_name || a.employee_id || '—'}</td>}
                        <td className="px-4 py-3 text-sm text-gray-700">{advanceTypeLabel(a.advance_type)}</td>
                        <td className="px-4 py-3 text-sm font-semibold text-gray-900 text-right">{fc(a.amount)}</td>
                        <td className="px-4 py-3 text-sm text-gray-600 max-w-xs"><div className="truncate" title={a.purpose}>{a.purpose || '—'}</div></td>
                        <td className="px-4 py-3 text-sm text-gray-500 whitespace-nowrap">{a.expected_settlement_date ? formatDateIST(a.expected_settlement_date) : '—'}</td>
                        <td className="px-4 py-3">
                          <span className={clsx('inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium', meta.badge)}>{meta.label}</span>
                        </td>
                        {isApprover && (
                          <td className="px-4 py-3 text-right">
                            {st === 'PENDING' && !isMine && (
                              <button className="px-3 py-1 rounded-md bg-green-600 text-white text-xs inline-flex items-center gap-1 hover:bg-green-700"
                                onClick={() => advanceAction(() => expensesApi.approveAdvance(a.advance_id), 'Advance approved')}>
                                <Check className="w-3.5 h-3.5" /> Approve
                              </button>
                            )}
                            {st === 'PENDING' && isMine && (
                              <span className="text-xs text-gray-400">Someone else must approve yours</span>
                            )}
                            {st === 'APPROVED' && (
                              <button className="px-3 py-1 rounded-md bg-blue-600 text-white text-xs inline-flex items-center gap-1 hover:bg-blue-700"
                                onClick={() => {
                                  const ref = window.prompt('Reference for the cash handed over (UPI ref, voucher no., "cash in hand")');
                                  if (ref && ref.trim()) advanceAction(() => expensesApi.disburseAdvance(a.advance_id, ref.trim()), 'Marked as given');
                                }}>
                                <Banknote className="w-3.5 h-3.5" /> Record cash given
                              </button>
                            )}
                            {(st === 'DISBURSED' || st === 'PARTIALLY_SETTLED') && (
                              <button className="px-3 py-1 rounded-md bg-gray-700 text-white text-xs inline-flex items-center gap-1 hover:bg-gray-800"
                                onClick={() => advanceAction(() => expensesApi.settleAdvance(a.advance_id), 'Advance settled')}>
                                <Scale className="w-3.5 h-3.5" /> Settle
                              </button>
                            )}
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {advances.length === 0 && (
                <div className="p-10 text-center text-gray-500 text-sm">
                  {isApprover ? 'No advances for this store' : 'You have no advances'}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Request advance. The reason is a fixed list -- there is no free-text
          box, so a salary advance cannot be recorded here at all. */}
      {showAdvanceModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg border border-gray-200 max-w-md w-full max-h-[90dvh] overflow-y-auto">
            <div className="border-b border-gray-200 px-6 py-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-gray-900">Request an advance</h2>
              <button onClick={() => setShowAdvanceModal(false)} className="text-gray-500 hover:text-gray-700" aria-label="Close modal"><XIcon className="w-5 h-5" /></button>
            </div>
            <div className="p-6 space-y-4">
              <Labeled label="What is the advance for?">
                <select value={advType} onChange={(e) => setAdvType(e.target.value as AdvanceType | '')}
                  className="input-field" title="Reason for the advance">
                  <option value="">Choose a reason…</option>
                  {ADVANCE_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
                <span className="mt-1 block text-xs text-gray-500">
                  A salary advance is not on this list on purpose — those go through Payroll.
                </span>
              </Labeled>
              <Labeled label="Amount (₹)">
                <input type="number" min="1" value={advAmount} onChange={(e) => setAdvAmount(e.target.value)} placeholder="0" className="input-field" title="Amount" />
              </Labeled>
              <Labeled label="Purpose">
                <textarea value={advPurpose} onChange={(e) => setAdvPurpose(e.target.value)} rows={3}
                  placeholder="Explain it for the person approving — e.g. bus fare to the Ranchi camp on the 20th"
                  className="input-field" title="Purpose" />
              </Labeled>
              <Labeled label="Expected settlement date (optional)">
                <input type="date" value={advSettleBy} onChange={(e) => setAdvSettleBy(e.target.value)} className="input-field" title="Expected settlement date" />
              </Labeled>
            </div>
            <div className="border-t border-gray-200 px-6 py-4 flex gap-3 justify-end">
              <button onClick={() => setShowAdvanceModal(false)} className="px-4 py-2 rounded-lg text-gray-600 hover:bg-gray-100">Cancel</button>
              <button onClick={handleRequestAdvance} disabled={saving || !advType}
                className="px-4 py-2 bg-bv-red-600 text-white rounded-lg hover:bg-bv-red-700 disabled:opacity-60">
                {saving ? 'Requesting…' : 'Request'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
