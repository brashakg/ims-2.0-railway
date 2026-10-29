// 'Refund advance and cancel' on the Orders screen (owner 2026-10-23):
// ACCOUNTANT / ADMIN / SUPERADMIN only. The server (/returns/advance-refund)
// decides what goes back and why it may not; this only shows its answer.
import { useEffect, useState } from 'react';
import { Undo2 } from 'lucide-react';
import { returnsApi } from '../../services/api/returns';
import type { AdvanceRefundPlan, CashPaidFrom } from '../../services/api/returns';
import { useToast } from '../../context/ToastContext';

export const ADVANCE_REFUND_ROLES = ['ACCOUNTANT', 'ADMIN', 'SUPERADMIN'];

export function canRefundAdvance(user: { roles?: string[] } | null | undefined): boolean {
  return (user?.roles || []).some((r) => ADVANCE_REFUND_ROLES.includes(r));
}

const METHOD_LABEL: Record<string, string> = {
  CASH: 'Cash',
  UPI: 'UPI',
  CARD: 'Card',
  BANK_TRANSFER: 'Bank transfer',
  LOYALTY: 'Loyalty points (restored)',
  STORE_CREDIT: 'Store credit',
};

const rs = (n: number) =>
  `₹${n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

function serverMessage(e: any): string {
  const d = e?.response?.data?.detail;
  return typeof d === 'string' ? d : d?.message || 'The refund could not be recorded.';
}

export function RefundAdvanceButton({ orderId, onDone }: { orderId: string; onDone: () => void }) {
  const toast = useToast();
  const [plan, setPlan] = useState<AdvanceRefundPlan | null>(null);
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [cashFrom, setCashFrom] = useState<CashPaidFrom | undefined>(undefined);

  useEffect(() => {
    let live = true;
    returnsApi
      .advanceRefundPreview(orderId)
      .then((p) => live && setPlan(p))
      .catch((e) => live && setPlan({ ok: false, reason: serverMessage(e) }));
    return () => {
      live = false;
    };
  }, [orderId]);

  const submit = async () => {
    setBusy(true);
    try {
      const res = await returnsApi.refundAdvanceAndCancel(orderId, reason.trim(), needsCashFrom ? cashFrom : undefined);
      if (res?.cancel_error) toast.error(res.message);
      else toast.success(res?.message || 'Advance refunded and order cancelled');
      setOpen(false);
      onDone();
    } catch (e) {
      toast.error(serverMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const allowed = plan?.ok === true;
  // The advance is refunded but the cancel did not finish: one press finishes it.
  const finishOnly = plan?.action === 'finish_cancel';
  // A refund started but not finished: the same money, the cash source already chosen.
  const finishRefund = plan?.action === 'finish_refund';
  const needsCashFrom = !finishOnly && !finishRefund && plan?.has_cash === true;
  const label = finishOnly ? 'Finish cancel' : finishRefund ? 'Finish refund and cancel' : 'Refund advance and cancel';
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        disabled={!allowed}
        title={allowed ? undefined : plan?.reason || 'Checking...'}
        className="btn-outline flex-1 flex items-center justify-center gap-2 min-w-[150px] disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <Undo2 className="w-4 h-4" />
        {label}
      </button>
      {!allowed && plan?.reason && (
        <p className="w-full text-xs text-gray-500">Refund advance: {plan.reason}</p>
      )}
      {open && allowed && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-label={label}
        >
          <div className="w-full max-w-md rounded-lg bg-white shadow-lg p-5 space-y-4">
            <h3 className="text-sm font-semibold text-gray-900">{label}</h3>
            {finishOnly && (
              <p className="text-sm text-gray-700">
                The advance is already refunded. This cancels the order.
              </p>
            )}
            {finishRefund && <p className="text-sm text-gray-700">{plan?.reason}</p>}
            {!finishOnly && (
            <div className="rounded-md bg-gray-50 border border-gray-200 px-3 py-2 text-sm space-y-1">
              {(plan?.refund_tenders || []).map((t) => (
                <div key={t.method} className="flex justify-between">
                  <span className="text-gray-600">{METHOD_LABEL[t.method] || t.method}</span>
                  <span className="font-mono">{rs(t.amount)}</span>
                </div>
              ))}
              <div className="flex justify-between border-t border-gray-200 pt-1 font-semibold">
                <span>Total refund</span>
                <span className="font-mono">{rs(plan?.total || 0)}</span>
              </div>
            </div>
            )}
            {needsCashFrom && (
              <fieldset className="text-sm space-y-1">
                <legend className="text-gray-700">The cash is paid back from</legend>
                <label className="flex items-center gap-2">
                  <input type="radio" name="cash-from" checked={cashFrom === 'STORE_DRAWER'}
                    onChange={() => setCashFrom('STORE_DRAWER')} />
                  This shop's cash drawer
                </label>
                <label className="flex items-center gap-2">
                  <input type="radio" name="cash-from" checked={cashFrom === 'HQ'}
                    onChange={() => setCashFrom('HQ')} />
                  HQ (not from the shop drawer)
                </label>
              </fieldset>
            )}
            {!finishOnly && plan?.invoice_number && (
              <p className="text-xs text-gray-600">
                A credit note for the whole invoice {plan.invoice_number} will be issued (all its GST
                reversed); only the money paid above is refunded.
              </p>
            )}
            <p className="text-xs text-gray-600">The order will then be cancelled.</p>
            <label className="block text-sm">
              <span className="text-gray-700">Reason (at least 10 characters)</span>
              <textarea
                className="input-field mt-1 w-full"
                rows={2}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </label>
            <div className="flex gap-2 justify-end">
              <button className="btn-outline" onClick={() => setOpen(false)} disabled={busy}>
                Back
              </button>
              <button
                className="btn-primary"
                onClick={submit}
                disabled={busy || reason.trim().length < 10 || (needsCashFrom && !cashFrom)}
              >
                {busy ? 'Working...' : finishOnly ? 'Finish the cancel' : `Refund ${rs(plan?.total || 0)} and cancel`}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
