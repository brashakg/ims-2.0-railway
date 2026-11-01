// ============================================================================
// Returns > Needs completing (PR #1264 money review 13)
// ============================================================================
// Counter returns whose money step stopped part way: a store credit that
// landed without its return record, a record that could not be saved, or a
// return that never finished. The bill takes no new return until each is
// finished here. Accounts / Admin answer ONE question per return -- was the
// cash part paid (Yes / No), or was nothing paid at all -- and the server does
// the rest (writes the record, or gives the units and the bill back).

import { useCallback, useEffect, useState } from 'react';
import {
  returnsApi, type CashPaidFrom, type NeedsCompletingItem, type OwedPaidMethod, type OwedReturn,
} from '../../services/api/returns';
import { useToast } from '../../context/ToastContext';
import { getErrorMessage } from '../../utils/errorHandler';
import { formatDateTimeIST } from '../../utils/datetime';

type Answer = 'YES' | 'NO' | 'NOTHING' | 'COMPLETE';

const ANSWER_LABEL: Record<Answer, string> = {
  YES: 'Yes - the cash part was paid',
  NO: 'No - the cash part was NOT paid',
  NOTHING: 'Nothing was paid',
  COMPLETE: 'Complete the record',
};

const rs = (n?: number) => `Rs ${(Number(n) || 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

function creditText(item: NeedsCompletingItem): string {
  const line = item.credit_line;
  if (!line) return item.record_not_saved ? 'Recorded at the counter, record not saved' : 'No store credit written';
  if (line.type === 'VOID') return 'Store credit never landed (void)';
  return `Store credit given ${rs(line.amount)}${line.note_number ? ` - CN ${line.note_number}` : ''}`;
}

/** `standalone`: the Accounts view (no return desk below it) -- it says so when empty. */
export function NeedsCompletingPanel({ standalone = false }: { standalone?: boolean }) {
  const toast = useToast();
  const [items, setItems] = useState<NeedsCompletingItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<Record<string, Answer>>({});
  const [busy, setBusy] = useState<string | null>(null);
  // Owed to customer: completed returns whose cash part is still unpaid.
  const [owed, setOwed] = useState<OwedReturn[]>([]);
  const [payPick, setPayPick] = useState<Record<string, { method: OwedPaidMethod; from?: CashPaidFrom; confirm?: boolean }>>({});
  const focus = typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('return') : null;

  const load = useCallback(async () => {
    try {
      const r = await returnsApi.needsCompleting();
      setItems(r.items || []);
      setOwed(r.owed || []);
      setError(null);
    } catch (e) {
      setError(getErrorMessage(e, 'Could not load the returns that need completing.'));
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const confirm = async (item: NeedsCompletingItem) => {
    const answer = picked[item.return_id];
    if (!answer) return;
    setBusy(item.return_id);
    try {
      const res = await returnsApi.completeRecord(item.return_id,
        answer === 'NOTHING' ? { nothing_paid: true } : { cash_leg_paid: answer !== 'NO' });
      toast.success(res?.message || 'Done.');
      setPicked((p) => { const n = { ...p }; delete n[item.return_id]; return n; });
      await load();
    } catch (e) {
      toast.error(getErrorMessage(e, 'Could not complete this return.'));
    } finally {
      setBusy(null);
    }
  };

  const payOwed = async (row: OwedReturn) => {
    const pick = payPick[row.return_id];
    if (!pick) return;
    setBusy(row.return_id);
    try {
      const res = await returnsApi.owedPaid(row.return_id, {
        method: pick.method, ...(pick.method === 'CASH' && pick.from ? { cash_paid_from: pick.from } : {}),
      });
      toast.success(res?.message || 'Booked as paid.');
      setPayPick((p) => { const n = { ...p }; delete n[row.return_id]; return n; });
      await load();
    } catch (e) {
      toast.error(getErrorMessage(e, 'Could not book this payment.'));
    } finally {
      setBusy(null);
    }
  };

  if (!standalone && (!items || items.length === 0) && owed.length === 0 && !error) return null;

  return (
    <section aria-label="Needs completing" className="bg-white border border-amber-300 rounded-lg p-4 mb-4">
      <div className="flex items-center gap-2 mb-1">
        <h2 className="text-sm font-semibold text-gray-900">Needs completing</h2>
        {items && items.length > 0 && (
          <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-amber-100 text-amber-800">{items.length}</span>
        )}
      </div>
      <p className="text-xs text-gray-500 mb-3">
        Returns whose money step stopped part way. The bill takes no new return until each is finished.
      </p>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      {items && items.length === 0 && !error && <p className="text-sm text-gray-500">Nothing needs completing.</p>}
      {items && items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-500 border-b border-gray-100">
                <th className="py-2 pr-3">Store</th>
                <th className="py-2 pr-3">Bill</th>
                <th className="py-2 pr-3 text-right">Amount</th>
                <th className="py-2 pr-3">Credit line</th>
                <th className="py-2 pr-3 text-right">Cash part</th>
                <th className="py-2">Was the cash part paid?</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                // Exactly what the server accepts: money moved -> was the cash
                // part paid (or, with no cash part, just complete it); nothing
                // moved -> nothing was paid.
                const answers: Answer[] = !item.money_known ? ['NOTHING'] : item.cash_leg ? ['YES', 'NO'] : ['COMPLETE'];
                const chosen = picked[item.return_id];
                return (
                  <tr key={item.return_id}
                    ref={focus === item.return_id ? (el) => el?.scrollIntoView?.({ block: 'center' }) : undefined}
                    className={'border-b border-gray-50 align-top' + (focus === item.return_id ? ' bg-amber-50' : '')}>
                    <td className="py-2 pr-3">{item.store_id || '-'}</td>
                    <td className="py-2 pr-3">
                      <div className="font-medium">{item.order_number || item.order_id || '-'}</div>
                      <div className="text-xs text-gray-400">{item.return_id}{item.at ? ` - ${formatDateTimeIST(item.at)}` : ''}</div>
                    </td>
                    <td className="py-2 pr-3 text-right">{rs(item.amount)}</td>
                    <td className="py-2 pr-3">{creditText(item)}</td>
                    <td className="py-2 pr-3 text-right">{item.cash_leg ? rs(item.cash_leg) : '-'}</td>
                    <td className="py-2">
                      <div className="flex flex-wrap gap-1">
                        {answers.map((a) => (
                          <button key={a} type="button" onClick={() => setPicked((p) => ({ ...p, [item.return_id]: a }))}
                            aria-pressed={chosen === a}
                            className={'px-2 py-1 text-xs rounded-md border ' + (chosen === a
                              ? 'bg-gray-900 text-white border-gray-900' : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50')}>
                            {ANSWER_LABEL[a]}
                          </button>
                        ))}
                      </div>
                      {chosen && (
                        <div className="mt-2 flex items-center gap-2">
                          <button type="button" onClick={() => confirm(item)} disabled={busy === item.return_id}
                            className="px-3 py-1 text-xs rounded-md bg-bv-red-600 text-white disabled:opacity-50">
                            {busy === item.return_id ? 'Saving...' : `Confirm: ${ANSWER_LABEL[chosen]}`}
                          </button>
                          <button type="button" onClick={() => setPicked((p) => { const n = { ...p }; delete n[item.return_id]; return n; })}
                            className="px-2 py-1 text-xs rounded-md text-gray-600 hover:bg-gray-100">Cancel</button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {owed.length > 0 && (
        <div aria-label="Owed to customer" className="mt-4">
          <h3 className="text-sm font-semibold text-gray-900 mb-1">Owed to customer</h3>
          <p className="text-xs text-gray-500 mb-2">
            Completed returns whose cash part was not paid at the counter. Book it here the day it is paid.
          </p>
          <ul className="divide-y divide-gray-100">
            {owed.map((row) => {
              const pick = payPick[row.return_id];
              const set = (v: Partial<{ method: OwedPaidMethod; from?: CashPaidFrom; confirm?: boolean }>) =>
                setPayPick((p) => ({ ...p, [row.return_id]: Object.assign({ method: 'CASH' as OwedPaidMethod }, p[row.return_id], v) }));
              const ready = pick && (pick.method !== 'CASH' || !!pick.from);
              return (
                <li key={row.return_id}
                  ref={focus === row.return_id ? (el) => el?.scrollIntoView?.({ block: 'center' }) : undefined}
                  className={'py-2 flex flex-wrap items-center gap-2 text-sm' + (focus === row.return_id ? ' bg-amber-50' : '')}>
                  <span className="font-medium">{row.order_number || row.order_id || '-'}</span>
                  <span className="text-xs text-gray-400">{row.return_id}{row.store_id ? ` - ${row.store_id}` : ''}</span>
                  <span className="ml-auto font-medium">{rs(row.owed_amount)}</span>
                  <select aria-label="Paid by" className="input-field w-auto text-xs" value={pick?.method || ''}
                    onChange={(e) => set({ method: e.target.value as OwedPaidMethod, confirm: false })}>
                    <option value="" disabled>Paid by...</option>
                    <option value="CASH">Cash</option>
                    <option value="UPI">UPI</option>
                    <option value="CARD">Card</option>
                    <option value="BANK_TRANSFER">Bank transfer</option>
                  </select>
                  {pick?.method === 'CASH' && (
                    <select aria-label="Cash from" className="input-field w-auto text-xs" value={pick.from || ''}
                      onChange={(e) => set({ from: e.target.value as CashPaidFrom, confirm: false })}>
                      <option value="" disabled>Cash from...</option>
                      <option value="STORE_DRAWER">The shop drawer</option>
                      <option value="HQ">HQ</option>
                    </select>
                  )}
                  {ready && !pick?.confirm && (
                    <button type="button" onClick={() => set({ confirm: true })}
                      className="px-3 py-1 text-xs rounded-md border border-gray-300 hover:bg-gray-50">Cash owed - now paid</button>
                  )}
                  {ready && pick?.confirm && (
                    <button type="button" onClick={() => payOwed(row)} disabled={busy === row.return_id}
                      className="px-3 py-1 text-xs rounded-md bg-bv-red-600 text-white disabled:opacity-50">
                      {busy === row.return_id ? 'Saving...' : `Confirm: ${rs(row.owed_amount)} paid today`}
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </section>
  );
}
