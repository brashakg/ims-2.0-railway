// ============================================================================
// IMS 2.0 - E4 PIN approve / reject modal
// ============================================================================
// The single action surface that puts an approver's PIN on a request. Approve
// or reject calls the engine; the engine's structured failures (423 PIN-locked,
// 403 wrong-pin / insufficient-tier, 409 already-actioned, 410 expired) are
// mapped to clear toasts here. The PIN is obscured, numeric, never stored, and
// never echoed.

import { useEffect, useState } from 'react';
import { Loader2, X } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { approvalsApi } from '../../services/api/approvals';
import type { ApprovalRequest } from '../../services/api/approvals';
import { actionLabel, formatRupees } from './ApprovalRequestCard';

export type PinModalMode = 'approve' | 'reject';

interface Props {
  request: ApprovalRequest;
  mode: PinModalMode;
  onClose: () => void;
  /** Called after a successful approve/reject so the parent can refresh. */
  onDone: () => void;
}

/** Map an engine failure (status + machine error) to a human message. */
function failureMessage(
  status: number | undefined,
  error: string | undefined,
  remaining: number | undefined,
  retryAfterMin: number | undefined,
): string {
  switch (status) {
    case 423:
      if (error === 'pin_not_set')
        return 'You have not set an approval PIN yet. Set one in Settings → Approval Workflows.';
      return retryAfterMin != null
        ? `PIN locked — too many attempts. Try again in ${retryAfterMin} minute${retryAfterMin === 1 ? '' : 's'}.`
        : 'PIN locked — too many failed attempts. Try again later.';
    case 403:
      if (error === 'wrong_pin')
        return remaining != null
          ? `Incorrect PIN (${remaining} attempt${remaining === 1 ? '' : 's'} remaining).`
          : 'Incorrect PIN.';
      if (error === 'insufficient_tier')
        return 'Your role cannot approve this request (tier too high).';
      if (error === 'cannot_approve_own')
        return 'You cannot approve your own request (maker-checker).';
      if (error === 'store_scope')
        return 'This request belongs to a store outside your scope.';
      return 'Not permitted.';
    case 409:
      return 'This request was already actioned by another approver.';
    case 410:
      return 'This request has expired.';
    case 404:
      return 'Request not found.';
    case 503:
      return 'Approval service is unavailable. Try again shortly.';
    default:
      return 'Could not complete the action. Please try again.';
  }
}

export function PINApproveModal({ request, mode, onClose, onDone }: Props) {
  const toast = useToast();
  const [pin, setPin] = useState('');
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // Close on Escape.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !submitting) onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, submitting]);

  const isApprove = mode === 'approve';
  const pinValid = /^\d{4,6}$/.test(pin);
  const canSubmit = pinValid && !submitting;

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      const res = isApprove
        ? await approvalsApi.approve(request.request_id, pin)
        : await approvalsApi.reject(request.request_id, pin, reason);
      if (res.ok) {
        toast.success(isApprove ? 'Request approved' : 'Request rejected');
        onDone();
        onClose();
        return;
      }
      toast.error(
        failureMessage(res.status, res.error, res.remaining, res.retry_after_min),
      );
      // A wrong PIN leaves the modal open so the approver can retry; everything
      // else (locked / expired / already-actioned) is terminal — close it.
      if (res.status === 403 && res.error === 'wrong_pin') {
        setPin('');
      } else {
        onClose();
        if (res.status === 409 || res.status === 410) onDone();
      }
    } catch {
      toast.error('Network error — please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={isApprove ? 'Approve request' : 'Reject request'}
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <div className="w-full max-w-md rounded-lg bg-white shadow-lg">
        <div className="flex items-center justify-between border-b border-gray-200 px-5 py-3">
          <h3 className="text-sm font-semibold text-gray-900">
            {isApprove ? 'Approve' : 'Reject'} request
          </h3>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="p-1 rounded-md text-gray-400 hover:text-gray-600 hover:bg-gray-100 disabled:opacity-50"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          <div className="rounded-md bg-gray-50 border border-gray-200 px-3 py-2 text-sm">
            <div className="flex items-center justify-between">
              <span className="font-medium text-gray-900">
                {actionLabel(request.action_type)}
              </span>
              <span className="font-mono text-gray-900">
                {formatRupees(request.amount)}
              </span>
            </div>
            {request.reason && (
              <p className="mt-1 text-xs text-gray-600">{request.reason}</p>
            )}
          </div>

          <div>
            <label
              htmlFor="approval-pin"
              className="block text-xs font-medium text-gray-700 mb-1"
            >
              Your approval PIN
            </label>
            <input
              id="approval-pin"
              type="password"
              inputMode="numeric"
              autoComplete="new-password"
              maxLength={6}
              value={pin}
              autoFocus
              onChange={(e) => setPin(e.target.value.replace(/\D/g, ''))}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleSubmit();
              }}
              placeholder="4–6 digits"
              className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm font-mono tracking-widest focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
            />
          </div>

          {!isApprove && (
            <div>
              <label
                htmlFor="reject-reason"
                className="block text-xs font-medium text-gray-700 mb-1"
              >
                Reason (optional)
              </label>
              <textarea
                id="reject-reason"
                rows={2}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Why is this being rejected?"
                className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none resize-none"
              />
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-gray-200 px-5 py-3">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="px-3 py-1.5 text-sm text-gray-600 hover:text-gray-800 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!canSubmit}
            className={`inline-flex items-center gap-2 px-4 py-1.5 text-sm font-medium rounded-md text-white disabled:opacity-50 disabled:cursor-not-allowed transition-colors ${
              isApprove
                ? 'bg-blue-600 hover:bg-blue-700'
                : 'bg-red-600 hover:bg-red-700'
            }`}
          >
            {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
            {isApprove ? 'Approve' : 'Reject'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default PINApproveModal;
