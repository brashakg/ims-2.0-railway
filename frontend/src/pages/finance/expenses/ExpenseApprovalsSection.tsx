// /finance/expenses/approvals -- Pending Approval (approvers; route-gated).
// Moved byte-identical from the old ExpenseTracker 'approvals' tab, with the
// reject modal only this section opens.

import { useState } from 'react';
import { Check, X as XIcon, AlertTriangle } from 'lucide-react';
import clsx from 'clsx';
import { useToast } from '../../../context/ToastContext';
import { expensesApi, type ExpenseRecord } from '../../../services/api/expenses';
import { fc, useExpensesContext } from './expenseShared';
import { ExpenseTable, Labeled } from './expenseWidgets';

// F17: a petty-cash claim strictly above this rupee amount needs a receipt
// before it can be approved (mirrors petty_cash_service.receipt_required_above).
const RECEIPT_REQUIRED_ABOVE = 200;

export function ExpenseApprovalsSection() {
  const toast = useToast();
  const { approvals, doAction } = useExpensesContext();
  const [showRejectModal, setShowRejectModal] = useState(false);
  const [selected, setSelected] = useState<ExpenseRecord | null>(null);
  const [rejectionReason, setRejectionReason] = useState('');

  const handleReject = async () => {
    if (!rejectionReason.trim()) { toast.error('Provide a rejection reason'); return; }
    if (!selected) return;
    await doAction(() => expensesApi.rejectExpense(selected.expense_id, rejectionReason.trim()), 'Expense rejected');
    setShowRejectModal(false); setRejectionReason(''); setSelected(null);
  };

  // F17: a petty-cash claim above Rs 200 with no bill cannot be approved. The
  // server enforces this too (defence in depth); the FE just guards the button.
  const receiptMissing = (e: ExpenseRecord) =>
    (e.category || '').toUpperCase() === 'PETTY_CASH'
    && (e.amount || 0) > RECEIPT_REQUIRED_ABOVE
    && !e.bill_file_id;

  return (
    <>
      <div className="bg-white rounded-lg border border-gray-200">
        <ExpenseTable rows={approvals} showOwner
          empty="No expenses pending approval"
          renderActions={(e) => {
            const blocked = receiptMissing(e);
            return (
              <div className="flex flex-col items-end gap-1">
                {blocked && (
                  <div className="text-xs text-red-600 inline-flex items-center gap-1">
                    <AlertTriangle className="w-3.5 h-3.5" /> Receipt missing — upload before approving
                  </div>
                )}
                <div className="flex items-center gap-2 justify-end">
                  <button
                    disabled={blocked}
                    title={blocked ? 'A petty-cash claim above ₹200 needs a receipt' : 'Approve'}
                    className={clsx(
                      'px-3 py-1 rounded-md text-white text-xs inline-flex items-center gap-1',
                      blocked ? 'bg-gray-300 cursor-not-allowed' : 'bg-green-600 hover:bg-green-700',
                    )}
                    onClick={() => { if (!blocked) doAction(() => expensesApi.approveExpense(e.expense_id), 'Approved'); }}>
                    <Check className="w-3.5 h-3.5" /> Approve
                  </button>
                  <button className="px-3 py-1 rounded-md bg-red-600 text-white text-xs inline-flex items-center gap-1 hover:bg-red-700"
                    onClick={() => { setSelected(e); setShowRejectModal(true); }}>
                    <XIcon className="w-3.5 h-3.5" /> Reject
                  </button>
                </div>
              </div>
            );
          }} />
      </div>

      {/* Reject modal */}
      {showRejectModal && selected && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg border border-gray-200 max-w-md w-full">
            <div className="border-b border-gray-200 px-6 py-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-gray-900">Reject expense</h2>
              <button onClick={() => { setShowRejectModal(false); setRejectionReason(''); }} className="text-gray-500 hover:text-gray-700" aria-label="Close modal"><XIcon className="w-5 h-5" /></button>
            </div>
            <div className="p-6 space-y-4">
              <p className="text-gray-600 text-sm"><strong>Expense:</strong> {selected.description} · {fc(selected.amount)}</p>
              <Labeled label="Reason for rejection">
                <textarea value={rejectionReason} onChange={(e) => setRejectionReason(e.target.value)} rows={3} placeholder="Why is this being rejected?" className="input-field" />
              </Labeled>
            </div>
            <div className="border-t border-gray-200 px-6 py-4 flex gap-3 justify-end">
              <button onClick={() => { setShowRejectModal(false); setRejectionReason(''); }} className="px-4 py-2 rounded-lg text-gray-600 hover:bg-gray-100">Cancel</button>
              <button onClick={handleReject} className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700">Reject</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
