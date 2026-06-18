// ============================================================================
// IMS 2.0 - Refund approval request + poll modal (F27, maker side)
// ============================================================================
// When the server gates a refund behind the tiered approval matrix (403
// reason=REFUND_APPROVAL_REQUIRED), the till opens this modal. It:
//   1. mints an E4 approval request (action_type REFUND_APPROVAL_MATRIX) bound
//      to this refund (store + order + amount + reason),
//   2. polls the maker's own requests until a manager PIN-approves it
//      (status APPROVED -> the approval_token becomes visible to the maker),
//   3. hands the { request_id, approval_token } back so the page re-submits the
//      return; the server consumes the token (single-use, bound to this refund).
// On the 60-minute TTL expiry (or a manager rejection) it surfaces a clear
// message + a "Re-request" action. The approver never enters their PIN here —
// that happens on the manager's /returns/approvals queue.

import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, X, ShieldCheck, Clock, AlertTriangle } from 'lucide-react';
import { approvalsApi } from '../../services/api/approvals';
import type { ApprovalRequest } from '../../services/api/approvals';

const POLL_MS = 4000;

interface Props {
  /** Net refund amount (Rs) the matrix is gating. */
  amount: number;
  storeId?: string;
  orderId?: string;
  orderNumber?: string;
  customerName?: string;
  /** Matrix reason code (DEFECTIVE / CHANGE_OF_MIND / PRICE_MATCH / GOODWILL). */
  reason?: string;
  /** Display name of the cashier raising the refund (for the approver's bell). */
  requestedByName?: string;
  /** The tier the server said is required (auto / admin / super) — display only. */
  requiredTier?: string;
  onClose: () => void;
  /** Called once a manager has approved — the page re-submits with these. */
  onApproved: (args: { requestId: string; approvalToken?: string }) => void;
}

type Phase = 'requesting' | 'waiting' | 'approved' | 'expired' | 'rejected' | 'error';

const TIER_LABEL: Record<string, string> = {
  auto: 'Store Manager',
  admin: 'Area Manager / Admin',
  super: 'Superadmin',
};

export function RefundApprovalModal({
  amount,
  storeId,
  orderId,
  orderNumber,
  customerName,
  reason,
  requestedByName,
  requiredTier,
  onClose,
  onApproved,
}: Props) {
  const [phase, setPhase] = useState<Phase>('requesting');
  const [requestId, setRequestId] = useState<string | null>(null);
  const [tier, setTier] = useState<string | undefined>(requiredTier);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [remaining, setRemaining] = useState<string>('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopTimers = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (tickRef.current) clearInterval(tickRef.current);
    pollRef.current = null;
    tickRef.current = null;
  }, []);

  // Create the approval request (idempotent-ish via dedupe_key on the order+amount).
  const createRequest = useCallback(async () => {
    setPhase('requesting');
    setErrorMsg(null);
    try {
      const res = await approvalsApi.createRequest({
        action_type: 'REFUND_APPROVAL_MATRIX',
        store_id: storeId,
        amount,
        reason: reason || 'Refund',
        context: {
          order_id: orderId,
          order_number: orderNumber,
          customer_name: customerName,
          refund_reason: reason,
          requested_by_name: requestedByName,
        },
        // Dedupe an identical live request so a double-click / re-open reuses
        // the same pending approval instead of stacking duplicates.
        dedupe_key: orderId ? `refund:${orderId}:${Math.round(amount)}` : undefined,
      });
      setRequestId(res.request_id);
      setTier(res.required_tier || requiredTier);
      setExpiresAt(res.expires_at || null);
      setPhase('waiting');
    } catch {
      setErrorMsg('Could not raise the approval request. Please try again.');
      setPhase('error');
    }
  }, [amount, storeId, orderId, orderNumber, customerName, reason, requestedByName, requiredTier]);

  useEffect(() => {
    createRequest();
    return stopTimers;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Poll the maker's own requests for the approval outcome + token.
  useEffect(() => {
    if (phase !== 'waiting' || !requestId) return;
    stopTimers();

    const check = async () => {
      try {
        const res = await approvalsApi.getMyRequests();
        const mine = (res.requests || []).find(
          (r: ApprovalRequest) => r.request_id === requestId,
        );
        if (!mine) return;
        if (mine.status === 'APPROVED' || mine.status === 'CONSUMED') {
          stopTimers();
          setPhase('approved');
          onApproved({ requestId, approvalToken: mine.approval_token || undefined });
        } else if (mine.status === 'EXPIRED') {
          stopTimers();
          setPhase('expired');
        } else if (mine.status === 'REJECTED') {
          stopTimers();
          setErrorMsg(mine.reject_reason || 'A manager rejected this refund.');
          setPhase('rejected');
        }
      } catch {
        /* transient — keep polling */
      }
    };
    check();
    pollRef.current = setInterval(check, POLL_MS);

    // Local countdown ticker + client-side expiry guard.
    tickRef.current = setInterval(() => {
      if (!expiresAt) return;
      const ms = new Date(expiresAt).getTime() - Date.now();
      if (ms <= 0) {
        stopTimers();
        setPhase('expired');
        setRemaining('');
        return;
      }
      const totalSec = Math.floor(ms / 1000);
      const m = Math.floor(totalSec / 60);
      const s = totalSec % 60;
      setRemaining(`${m}m ${String(s).padStart(2, '0')}s`);
    }, 1000);

    return stopTimers;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, requestId, expiresAt]);

  const fc = (n: number) => `₹${Math.round(n).toLocaleString('en-IN')}`;
  const tierLabel = tier ? TIER_LABEL[tier] || tier : null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Refund approval required"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-md rounded-lg bg-white shadow-lg">
        <div className="flex items-center justify-between border-b border-gray-200 px-5 py-3">
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-amber-600" />
            <h3 className="text-sm font-semibold text-gray-900">Refund approval required</h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded-md text-gray-400 hover:text-gray-600 hover:bg-gray-100"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          <div className="rounded-md bg-gray-50 border border-gray-200 px-3 py-2 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-gray-600">Refund amount</span>
              <span className="font-mono font-semibold text-gray-900">{fc(amount)}</span>
            </div>
            {orderNumber && (
              <div className="flex items-center justify-between mt-1">
                <span className="text-gray-600">Order</span>
                <span className="text-gray-800">{orderNumber}</span>
              </div>
            )}
            {tierLabel && (
              <div className="flex items-center justify-between mt-1">
                <span className="text-gray-600">Needs sign-off from</span>
                <span className="text-gray-800">{tierLabel}</span>
              </div>
            )}
          </div>

          {phase === 'requesting' && (
            <div className="flex items-center gap-2 text-sm text-gray-600">
              <Loader2 className="w-4 h-4 animate-spin" />
              Raising approval request…
            </div>
          )}

          {phase === 'waiting' && (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-sm text-gray-700">
                <Loader2 className="w-4 h-4 animate-spin text-amber-600" />
                Waiting for a manager to PIN-approve…
              </div>
              {remaining && (
                <div className="flex items-center gap-1.5 text-xs text-gray-500">
                  <Clock className="w-3.5 h-3.5" />
                  Expires in {remaining}
                </div>
              )}
              <p className="text-xs text-gray-500 leading-relaxed">
                A manager opens <span className="font-medium">Refund Approvals</span> and
                approves with their PIN. This screen continues automatically once approved.
              </p>
            </div>
          )}

          {phase === 'approved' && (
            <div className="flex items-center gap-2 text-sm text-green-700">
              <ShieldCheck className="w-4 h-4" />
              Approved — finalising the refund…
            </div>
          )}

          {(phase === 'expired' || phase === 'rejected' || phase === 'error') && (
            <div className="rounded-md bg-amber-50 border border-amber-200 px-3 py-2 text-sm text-amber-800 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
              <span>
                {phase === 'expired'
                  ? 'The approval request expired before it was actioned. Re-request to try again.'
                  : phase === 'rejected'
                    ? errorMsg || 'A manager rejected this refund.'
                    : errorMsg || 'Something went wrong.'}
              </span>
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-gray-200 px-5 py-3">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 text-sm text-gray-600 hover:text-gray-800"
          >
            {phase === 'waiting' ? 'Cancel' : 'Close'}
          </button>
          {(phase === 'expired' || phase === 'error') && (
            <button
              type="button"
              onClick={createRequest}
              className="inline-flex items-center gap-2 px-4 py-1.5 text-sm font-medium rounded-md bg-gray-900 text-white hover:bg-gray-800"
            >
              Re-request
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default RefundApprovalModal;
