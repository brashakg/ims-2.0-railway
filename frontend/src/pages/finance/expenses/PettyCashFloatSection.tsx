// /finance/expenses/float -- F17 Petty Cash Float (manager / area-manager /
// accountant / admin; route-gated). CASH SURFACE: its loader, handler and
// modal are moved byte-identical from the old ExpenseTracker 'float' tab and
// nothing else changed.

import { useState, useEffect, useCallback } from 'react';
import { Plus, X as XIcon, Loader2, Banknote } from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../../context/AuthContext';
import { useToast } from '../../../context/ToastContext';
import { expensesApi, type PettyCashBalance } from '../../../services/api/expenses';
import { formatDateIST } from '../../../utils/datetime';
import { fc, useExpensesContext, type ApiError } from './expenseShared';
import { Labeled } from './expenseWidgets';

export function PettyCashFloatSection() {
  const { user } = useAuth();
  const toast = useToast();
  const { canManageFloat } = useExpensesContext();

  // F17 petty-cash float
  const [floatData, setFloatData] = useState<PettyCashBalance | null>(null);
  const [floatLoading, setFloatLoading] = useState(false);
  const [showFloatModal, setShowFloatModal] = useState<null | 'open' | 'topup'>(null);
  const [floatAmount, setFloatAmount] = useState('');
  const [floatReason, setFloatReason] = useState('');
  const [floatLimit, setFloatLimit] = useState('5000');
  const [saving, setSaving] = useState(false);

  const loadFloat = useCallback(async () => {
    if (!user?.activeStoreId) { setFloatData(null); return; }
    setFloatLoading(true);
    try {
      setFloatData(await expensesApi.getPettyCashBalance(user.activeStoreId));
    } catch {
      setFloatData(null);
    } finally {
      setFloatLoading(false);
    }
  }, [user?.activeStoreId]);

  useEffect(() => { loadFloat(); }, [loadFloat]);

  const handleFloatSave = async () => {
    const storeId = user?.activeStoreId;
    if (!storeId) { toast.error('No active store'); return; }
    const amt = parseFloat(floatAmount);
    if (!amt || amt <= 0) { toast.error('Enter a valid amount'); return; }
    setSaving(true);
    try {
      if (showFloatModal === 'open') {
        await expensesApi.openPettyCashFloat({
          store_id: storeId, amount: amt,
          float_limit: floatLimit ? parseFloat(floatLimit) : undefined,
        });
        toast.success('Petty-cash float opened');
      } else {
        await expensesApi.topupPettyCashFloat({ store_id: storeId, amount: amt, reason: floatReason.trim() || undefined });
        toast.success('Float topped up');
      }
      setShowFloatModal(null); setFloatAmount(''); setFloatReason('');
      await loadFloat();
    } catch (err) {
      const e = err as ApiError;
      const d = e?.response?.data?.detail;
      toast.error(typeof d === 'string' ? d : 'Float update failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      {/* F17 Petty Cash Float */}
      <div className="space-y-6">
        {!user?.activeStoreId ? (
          <div className="bg-white rounded-lg border border-gray-200 p-10 text-center text-gray-500 text-sm">
            Select an active store to manage its petty-cash float.
          </div>
        ) : floatLoading ? (
          <div className="flex items-center justify-center h-40"><Loader2 className="w-6 h-6 text-bv-red-600 animate-spin" /></div>
        ) : !floatData?.exists ? (
          <div className="bg-white rounded-lg border border-gray-200 p-8 text-center">
            <div className="text-sm text-gray-600 mb-4">No petty-cash float is open for this store yet.</div>
            {canManageFloat && (
              <button className="btn sm primary" onClick={() => { setShowFloatModal('open'); setFloatAmount(''); setFloatLimit('5000'); }}>
                <Plus className="w-4 h-4" /> Open float
              </button>
            )}
          </div>
        ) : (
          <>
            <div className="bg-white rounded-lg border border-gray-200 p-5 flex items-center justify-between flex-wrap gap-4">
              <div>
                <div className="text-xs uppercase tracking-wide text-gray-500 mb-1">Float balance</div>
                <div className="flex items-baseline gap-2">
                  <span className={clsx('text-3xl font-semibold', floatData.is_low ? 'text-red-600' : 'text-gray-900')}>
                    {fc(floatData.balance)}
                  </span>
                  <span className="text-sm text-gray-400">/ {fc(floatData.float_limit)}</span>
                </div>
                <div className="text-xs text-gray-500 mt-1">
                  Status: {floatData.status || '—'} · Low-balance alert below {fc(floatData.low_balance_threshold)}
                  {floatData.is_low && <span className="text-red-600 font-medium"> · LOW — top up soon</span>}
                </div>
              </div>
              {canManageFloat && (
                <button className="btn sm primary" onClick={() => { setShowFloatModal('topup'); setFloatAmount(''); setFloatReason(''); }}>
                  <Banknote className="w-4 h-4" /> Top up
                </button>
              )}
            </div>

            <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-200 text-sm font-semibold text-gray-700">Recent movements</div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 text-gray-500">
                    <tr>
                      <th className="px-4 py-2 text-left font-medium">Date</th>
                      <th className="px-4 py-2 text-left font-medium">Type</th>
                      <th className="px-4 py-2 text-right font-medium">Amount</th>
                      <th className="px-4 py-2 text-right font-medium">Balance after</th>
                      <th className="px-4 py-2 text-left font-medium">Reason</th>
                      <th className="px-4 py-2 text-left font-medium">Expense ref</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {floatData.recent_ledger.map((r) => (
                      <tr key={r.txn_id}>
                        <td className="px-4 py-2 text-gray-500 whitespace-nowrap">{formatDateIST(r.created_at)}</td>
                        <td className="px-4 py-2">
                          <span className={clsx('inline-block px-2 py-0.5 rounded-full text-xs font-medium',
                            r.type === 'CREDIT' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700')}>
                            {r.type}
                          </span>
                        </td>
                        <td className={clsx('px-4 py-2 text-right font-medium', r.type === 'CREDIT' ? 'text-green-700' : 'text-red-700')}>
                          {r.type === 'CREDIT' ? '+' : '−'}{fc(r.delta)}
                        </td>
                        <td className="px-4 py-2 text-right text-gray-700">{r.balance_after != null ? fc(r.balance_after) : '—'}</td>
                        <td className="px-4 py-2 text-gray-600">{r.reason || '—'}</td>
                        <td className="px-4 py-2 text-gray-500">{r.expense_id || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {floatData.recent_ledger.length === 0 && <div className="p-8 text-center text-gray-500 text-sm">No movements yet</div>}
              </div>
            </div>
          </>
        )}
      </div>

      {/* F17: open / top-up petty-cash float */}
      {showFloatModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg border border-gray-200 max-w-md w-full">
            <div className="border-b border-gray-200 px-6 py-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-gray-900">
                {showFloatModal === 'open' ? 'Open petty-cash float' : 'Top up petty-cash float'}
              </h2>
              <button onClick={() => setShowFloatModal(null)} className="text-gray-500 hover:text-gray-700" aria-label="Close modal"><XIcon className="w-5 h-5" /></button>
            </div>
            <div className="p-6 space-y-4">
              <Labeled label="Amount (₹)">
                <input type="number" min="1" value={floatAmount} onChange={(e) => setFloatAmount(e.target.value)} placeholder="e.g. 5000" className="input-field" />
              </Labeled>
              {showFloatModal === 'open' && (
                <Labeled label="Authorised float limit (₹)">
                  <input type="number" min="1" value={floatLimit} onChange={(e) => setFloatLimit(e.target.value)} placeholder="5000" className="input-field" />
                </Labeled>
              )}
              {showFloatModal === 'topup' && (
                <Labeled label="Reason (optional)">
                  <input value={floatReason} onChange={(e) => setFloatReason(e.target.value)} placeholder="e.g. weekly replenishment" className="input-field" />
                </Labeled>
              )}
            </div>
            <div className="border-t border-gray-200 px-6 py-4 flex gap-3 justify-end">
              <button onClick={() => setShowFloatModal(null)} className="px-4 py-2 rounded-lg text-gray-600 hover:bg-gray-100">Cancel</button>
              <button disabled={saving} onClick={handleFloatSave} className="px-4 py-2 bg-bv-red-600 text-white rounded-lg hover:bg-bv-red-700 disabled:opacity-50">
                {saving ? 'Saving…' : showFloatModal === 'open' ? 'Open float' : 'Top up'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
