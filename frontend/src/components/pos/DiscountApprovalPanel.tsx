// ============================================================================
// IMS 2.0 - "Ask a manager to approve" (discount over the seller's limit)
// ============================================================================
// Owner decision 2026-10-18: who approved a discount is the SERVER's call.
// A discount over the seller's own limit is refused by order create with
// DISCOUNT_NEEDS_APPROVAL; its detail carries the exact E4 request that would
// cover this bill (`approval_request`). This panel sends it through the
// existing E4 flow (POST /approvals/requests), waits while a manager approves
// it with their PIN in Approvals, and hands the request id back so the till
// resubmits the same sale with it. No second PIN system, no typed names.

import { useEffect, useRef, useState } from 'react';
import { Loader2, ShieldCheck, X } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { approvalsApi } from '../../services/api/approvals';
import { usePOSStore } from '../../stores/posStore';

/** The server's DISCOUNT_NEEDS_APPROVAL refusal detail. */
export interface DiscountApprovalNeed {
  code: 'DISCOUNT_NEEDS_APPROVAL';
  message: string;
  requested_percent: number;
  max_percent: number;
  zero_total?: boolean;
  approval_request: {
    action_type: string;
    store_id?: string | null;
    required_tier?: string;
    context: Record<string, unknown>;
  };
}

/** How often the till re-reads the request while a manager decides. */
export const APPROVAL_POLL_MS = 4000;

interface Props {
  need: DiscountApprovalNeed;
  /** The manager approved: resubmit the sale with this request id. */
  onApproved: (requestId: string) => void;
  onClose: () => void;
}

export function DiscountApprovalPanel({ need, onApproved, onClose }: Props) {
  const { user } = useAuth();
  const [requestId, setRequestId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  // The surface passes a fresh closure each render; the poll must not restart.
  const onApprovedRef = useRef(onApproved);
  onApprovedRef.current = onApproved;

  const ask = async () => {
    setBusy(true);
    setNote(null);
    const s = usePOSStore.getState();
    const reasons = [s.cart_discount_reason, ...(s.cart || []).map((l) => l.discount_reason)]
      .filter((r): r is string => !!r && !!r.trim());
    try {
      const res = await approvalsApi.createRequest({
        ...need.approval_request,
        context: { ...need.approval_request.context, requested_by_name: user?.name },
        reason: reasons.length ? reasons.join('; ') : need.message,
      });
      setRequestId(res.request_id);
    } catch (e) {
      setNote(e instanceof Error && e.message ? e.message : 'Could not send the request. Try again.');
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!requestId) return;
    let done = false;
    const timer = setInterval(async () => {
      try {
        const r = await approvalsApi.getRequest(requestId);
        if (done) return;
        if (r.status === 'APPROVED') {
          done = true;
          clearInterval(timer);
          onApprovedRef.current(requestId);
        } else if (r.status === 'REJECTED' || r.status === 'EXPIRED') {
          done = true;
          clearInterval(timer);
          setRequestId(null);
          setNote(
            r.status === 'REJECTED'
              ? `The manager did not approve it${r.reject_reason ? `: ${r.reject_reason}` : ''}. Lower the discount, or ask again.`
              : 'The request expired before a manager approved it. Ask again.',
          );
        }
      } catch {
        // A network blip must not drop the request: keep waiting.
      }
    }, APPROVAL_POLL_MS);
    return () => {
      done = true;
      clearInterval(timer);
    };
  }, [requestId]);

  return (
    <div
      role="status"
      className="mx-3 mt-2 bg-amber-50 border border-amber-200 rounded-lg p-2.5 text-sm text-amber-900 space-y-2"
    >
      <div className="flex items-start gap-2">
        <ShieldCheck className="w-4 h-4 mt-0.5 flex-shrink-0" />
        <span className="flex-1">{need.message}</span>
        <button onClick={onClose} aria-label="Dismiss" title="Dismiss">
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
      {requestId ? (
        <p className="flex items-center gap-2 text-xs">
          <Loader2 className="w-3.5 h-3.5 animate-spin flex-shrink-0" />
          Waiting for a manager to approve it under Approvals. The sale completes as soon as they do.
        </p>
      ) : (
        <button
          type="button"
          onClick={ask}
          disabled={busy}
          className="min-h-[44px] px-3 rounded-lg bg-amber-600 text-white text-sm font-medium hover:bg-amber-700 disabled:opacity-50"
        >
          {busy ? 'Sending…' : 'Ask a manager to approve'}
        </button>
      )}
      {note && <p className="text-xs text-red-700">{note}</p>}
    </div>
  );
}

export default DiscountApprovalPanel;
