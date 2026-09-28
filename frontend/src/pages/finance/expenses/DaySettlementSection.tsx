// /finance/expenses/settle -- F17 EOD petty-cash Day Settlement (manager /
// area-manager / accountant / admin; route-gated). CASH SURFACE: its loader,
// handler and modal are moved byte-identical from the old ExpenseTracker
// 'settle' tab and nothing else changed.

import { useState, useEffect, useCallback } from 'react';
import { Check, X as XIcon, Loader2, Scale, CalendarClock } from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../../context/AuthContext';
import { useToast } from '../../../context/ToastContext';
import {
  expensesApi,
  type PettyCashSettlementPosition,
  type PettyCashSettlementRecord,
} from '../../../services/api/expenses';
import { formatDateIST } from '../../../utils/datetime';
import { fc, useExpensesContext, type ApiError } from './expenseShared';
import { Card, Row, Labeled } from './expenseWidgets';

export function DaySettlementSection() {
  const { user } = useAuth();
  const toast = useToast();
  const { canManageFloat } = useExpensesContext();

  // F17 EOD settlement (per store/day)
  const todayISO = new Date().toISOString().split('T')[0];
  const [settleDate, setSettleDate] = useState(todayISO);
  const [settlePos, setSettlePos] = useState<PettyCashSettlementPosition | null>(null);
  const [settleHistory, setSettleHistory] = useState<PettyCashSettlementRecord[]>([]);
  const [settleLoading, setSettleLoading] = useState(false);
  const [showSettleModal, setShowSettleModal] = useState(false);
  const [countedClosing, setCountedClosing] = useState('');
  const [settleNote, setSettleNote] = useState('');
  const [saving, setSaving] = useState(false);

  // F17 EOD settlement: load the day's position + recent settlement history.
  const loadSettlement = useCallback(async () => {
    if (!user?.activeStoreId) { setSettlePos(null); setSettleHistory([]); return; }
    setSettleLoading(true);
    try {
      const [pos, hist] = await Promise.all([
        expensesApi.getPettyCashSettlementPosition(user.activeStoreId, settleDate),
        expensesApi.listPettyCashSettlements(user.activeStoreId, { limit: 30 }).catch(() => null),
      ]);
      setSettlePos(pos);
      setSettleHistory(hist?.settlements || []);
    } catch {
      setSettlePos(null);
    } finally {
      setSettleLoading(false);
    }
  }, [user?.activeStoreId, settleDate]);

  useEffect(() => { loadSettlement(); }, [loadSettlement]);

  const handleSettle = async () => {
    const storeId = user?.activeStoreId;
    if (!storeId) { toast.error('No active store'); return; }
    if (countedClosing === '' || Number(countedClosing) < 0) { toast.error('Enter the counted closing cash'); return; }
    setSaving(true);
    try {
      const res = await expensesApi.settlePettyCashDay({
        store_id: storeId,
        counted_closing: parseFloat(countedClosing),
        settle_date: settleDate,
        note: settleNote.trim() || undefined,
      });
      const v = res?.variance ?? 0;
      const vs = res?.variance_status;
      toast.success(
        vs === 'BALANCED' ? 'Day settled — balanced' : `Day settled — ${vs} by ${fc(Math.abs(v))}`,
      );
      setShowSettleModal(false); setCountedClosing(''); setSettleNote('');
      await loadSettlement();
    } catch (err) {
      const e = err as ApiError;
      const d = e?.response?.data?.detail;
      const errCode = (d as unknown as { error?: string } | undefined)?.error;
      const msg = typeof d === 'string' ? d
        : errCode === 'already_settled' ? 'This day is already settled.'
        : errCode === 'float_not_open' ? 'No petty-cash float is open for this store.'
        : 'Settlement failed';
      toast.error(msg);
      await loadSettlement();
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      {/* F17 EOD Day Settlement */}
      <div className="space-y-6">
        <div className="bg-white rounded-lg border border-gray-200 p-4 flex flex-wrap items-end gap-4">
          <label className="block">
            <span className="block text-sm font-medium text-gray-600 mb-1 flex items-center gap-1.5">
              <CalendarClock className="w-4 h-4" /> Settlement day
            </span>
            <input type="date" value={settleDate} max={todayISO}
              onChange={(e) => setSettleDate(e.target.value)}
              className="px-3 py-2 border border-gray-200 rounded-lg text-sm bg-white focus:outline-none focus:border-bv-red-500" />
          </label>
          <div className="text-xs text-gray-500">
            Count the petty-cash box at close, then record the counted amount. We compare it
            against the system's expected closing and flag any short / over.
          </div>
        </div>

        {!user?.activeStoreId ? (
          <div className="bg-white rounded-lg border border-gray-200 p-10 text-center text-gray-500 text-sm">
            Select an active store to settle its petty-cash day.
          </div>
        ) : settleLoading ? (
          <div className="flex items-center justify-center h-40"><Loader2 className="w-6 h-6 text-bv-red-600 animate-spin" /></div>
        ) : !settlePos?.exists ? (
          <div className="bg-white rounded-lg border border-gray-200 p-8 text-center text-sm text-gray-600">
            No petty-cash float is open for this store, so there is nothing to settle.
          </div>
        ) : (
          <>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              <Card label="Opening float" value={fc(settlePos.opening_float)} />
              <Card label={`Payouts (${settlePos.payouts_count})`} value={`− ${fc(settlePos.debits_today)}`} color="text-red-600" />
              <Card label="Top-ups / reversals" value={`+ ${fc(settlePos.credits_today)}`} color="text-green-600" />
              <Card label="Expected closing" value={fc(settlePos.expected_closing)} color="text-blue-600" />
            </div>

            {settlePos.settled && settlePos.settlement ? (
              <div className="bg-white rounded-lg border border-gray-200 p-5">
                <div className="flex items-center gap-2 mb-4">
                  <Check className="w-5 h-5 text-green-600" />
                  <h3 className="text-sm font-semibold text-gray-700">
                    Settled on {formatDateIST(settlePos.settlement.settled_at)}
                  </h3>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <Row label="Expected" value={fc(settlePos.settlement.expected_closing)} />
                  <Row label="Counted" value={fc(settlePos.settlement.counted_closing)} />
                  <Row
                    label="Variance"
                    value={`${settlePos.settlement.variance >= 0 ? '+' : '−'}${fc(Math.abs(settlePos.settlement.variance))}`}
                    color={settlePos.settlement.variance_status === 'BALANCED' ? 'text-green-600'
                      : settlePos.settlement.variance_status === 'OVER' ? 'text-amber-600' : 'text-red-600'}
                  />
                </div>
                <div className="mt-3">
                  <VarianceBadge status={settlePos.settlement.variance_status} />
                  {settlePos.settlement.note && (
                    <span className="ml-3 text-sm text-gray-500">Note: {settlePos.settlement.note}</span>
                  )}
                </div>
              </div>
            ) : (
              <div className="bg-white rounded-lg border border-gray-200 p-6 flex flex-wrap items-center justify-between gap-4">
                <div className="text-sm text-gray-600">
                  This day is <span className="font-medium text-gray-900">not yet settled</span>.
                  Expected closing is <span className="font-semibold">{fc(settlePos.expected_closing)}</span>.
                </div>
                {canManageFloat && (
                  <button className="btn sm primary" onClick={() => { setShowSettleModal(true); setCountedClosing(''); setSettleNote(''); }}>
                    <Scale className="w-4 h-4" /> Count &amp; settle
                  </button>
                )}
              </div>
            )}

            <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-200 text-sm font-semibold text-gray-700">Settlement history</div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 text-gray-500">
                    <tr>
                      <th className="px-4 py-2 text-left font-medium">Day</th>
                      <th className="px-4 py-2 text-right font-medium">Expected</th>
                      <th className="px-4 py-2 text-right font-medium">Counted</th>
                      <th className="px-4 py-2 text-right font-medium">Variance</th>
                      <th className="px-4 py-2 text-left font-medium">Result</th>
                      <th className="px-4 py-2 text-left font-medium">Settled by</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {settleHistory.map((s) => (
                      <tr key={s.settlement_id}>
                        <td className="px-4 py-2 text-gray-600 whitespace-nowrap">{s.settle_date}</td>
                        <td className="px-4 py-2 text-right text-gray-700">{fc(s.expected_closing)}</td>
                        <td className="px-4 py-2 text-right text-gray-700">{fc(s.counted_closing)}</td>
                        <td className={clsx('px-4 py-2 text-right font-medium',
                          s.variance_status === 'BALANCED' ? 'text-green-700' : s.variance_status === 'OVER' ? 'text-amber-600' : 'text-red-600')}>
                          {s.variance >= 0 ? '+' : '−'}{fc(Math.abs(s.variance))}
                        </td>
                        <td className="px-4 py-2"><VarianceBadge status={s.variance_status} /></td>
                        <td className="px-4 py-2 text-gray-500">{s.settled_by_name || s.settled_by || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {settleHistory.length === 0 && <div className="p-8 text-center text-gray-500 text-sm">No settlements recorded yet</div>}
              </div>
            </div>
          </>
        )}
      </div>

      {/* F17: settle the petty-cash day (count + record + close) */}
      {showSettleModal && settlePos && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg border border-gray-200 max-w-md w-full">
            <div className="border-b border-gray-200 px-6 py-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-gray-900">Settle {settlePos.settle_date}</h2>
              <button onClick={() => setShowSettleModal(false)} className="text-gray-500 hover:text-gray-700" aria-label="Close modal"><XIcon className="w-5 h-5" /></button>
            </div>
            <div className="p-6 space-y-4">
              <div className="rounded-lg bg-gray-50 border border-gray-200 p-3 text-sm text-gray-600 flex justify-between">
                <span>Expected closing</span>
                <span className="font-semibold text-gray-900">{fc(settlePos.expected_closing)}</span>
              </div>
              <Labeled label="Counted closing cash (₹)">
                <input type="number" min="0" step="0.01" value={countedClosing}
                  onChange={(e) => setCountedClosing(e.target.value)} placeholder="Count the box" className="input-field" />
              </Labeled>
              {countedClosing !== '' && !Number.isNaN(Number(countedClosing)) && (
                <div className="text-sm">
                  Variance:{' '}
                  <span className={clsx('font-semibold',
                    Math.abs(Number(countedClosing) - settlePos.expected_closing) <= (settlePos.tolerance || 0) ? 'text-green-600'
                      : Number(countedClosing) > settlePos.expected_closing ? 'text-amber-600' : 'text-red-600')}>
                    {Number(countedClosing) - settlePos.expected_closing >= 0 ? '+' : '−'}
                    {fc(Math.abs(Number(countedClosing) - settlePos.expected_closing))}
                  </span>
                </div>
              )}
              <Labeled label="Note (optional)">
                <input value={settleNote} onChange={(e) => setSettleNote(e.target.value)} placeholder="e.g. counted with shift lead" className="input-field" />
              </Labeled>
            </div>
            <div className="border-t border-gray-200 px-6 py-4 flex gap-3 justify-end">
              <button onClick={() => setShowSettleModal(false)} className="px-4 py-2 rounded-lg text-gray-600 hover:bg-gray-100">Cancel</button>
              <button disabled={saving} onClick={handleSettle} className="px-4 py-2 bg-bv-red-600 text-white rounded-lg hover:bg-bv-red-700 disabled:opacity-50">
                {saving ? 'Settling…' : 'Settle day'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function VarianceBadge({ status }: { status: 'BALANCED' | 'OVER' | 'SHORT' | string }) {
  const map: Record<string, string> = {
    BALANCED: 'bg-green-50 text-green-700',
    OVER: 'bg-amber-50 text-amber-700',
    SHORT: 'bg-red-50 text-red-700',
  };
  return (
    <span className={clsx('inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium', map[status] || 'bg-gray-100 text-gray-700')}>
      {status === 'BALANCED' ? 'Balanced' : status === 'OVER' ? 'Over (excess)' : status === 'SHORT' ? 'Short (missing)' : status}
    </span>
  );
}
