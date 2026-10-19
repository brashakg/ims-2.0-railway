// ============================================================================
// IMS 2.0 - "Ask a manager to approve" (discount over the seller's limit)
// ============================================================================
// Owner decision 2026-10-18: who approved a discount is the SERVER's call.
// A discount over the seller's own limit is refused by order create with
// DISCOUNT_NEEDS_APPROVAL. "Ask a manager to approve" re-sends THIS bill with
// request_approval: the server prices it and raises the E4 discount_override
// request from its own figures (the cashier adds only a note). A manager
// approves it with their PIN under Approvals; the till then resubmits the same
// sale with the request id -- unless the bill changed meanwhile, in which case
// the approval no longer fits and the cashier is told to ask again.

import { useEffect, useRef, useState } from 'react';
import { Loader2, ShieldCheck, X } from 'lucide-react';
import { approvalsApi } from '../../services/api/approvals';
import { usePOSStore } from '../../stores/posStore';
import { askManagerForDiscount, billFingerprint } from './submitOrder';

/** The server's DISCOUNT_NEEDS_APPROVAL refusal detail. */
export interface DiscountApprovalNeed {
  code: 'DISCOUNT_NEEDS_APPROVAL';
  message: string;
  requested_percent: number;
  max_percent: number;
  zero_total?: boolean;
}

/** How often the till re-reads the request while a manager decides. */
export const APPROVAL_POLL_MS = 4000;

export const BILL_CHANGED_MESSAGE = 'The bill changed - ask the manager again.';

interface Props {
  need: DiscountApprovalNeed;
  /** The manager approved THIS bill: resubmit the sale with this request id. */
  onApproved: (requestId: string) => void;
  onClose: () => void;
}

export function DiscountApprovalPanel({ need, onApproved, onClose }: Props) {
  const [requestId, setRequestId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  // The bill the manager was asked about.
  const askedFor = useRef<string | null>(null);
  // The surface passes a fresh closure each render; the poll must not restart.
  const onApprovedRef = useRef(onApproved);
  useEffect(() => {
    onApprovedRef.current = onApproved;
  });

  const approved = (id: string) => {
    if (billFingerprint(usePOSStore.getState()) !== askedFor.current) {
      setRequestId(null);
      setMessage(BILL_CHANGED_MESSAGE);
      return;
    }
    onApprovedRef.current(id);
  };

  const ask = async () => {
    setBusy(true);
    setMessage(null);
    const live = usePOSStore.getState();
    askedFor.current = billFingerprint(live);
    try {
      const res = await askManagerForDiscount(live, note);
      if (res.status === 'APPROVED') approved(res.requestId);
      else setRequestId(res.requestId);
    } catch (e) {
      setMessage(e instanceof Error && e.message ? e.message : 'Could not send the request. Try again.');
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
          approved(requestId);
        } else if (r.status === 'REJECTED' || r.status === 'EXPIRED') {
          done = true;
          clearInterval(timer);
          setRequestId(null);
          setMessage(
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
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={note}
            maxLength={300}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Note for the manager (optional)"
            aria-label="Note for the manager"
            className="flex-1 min-w-[12rem] min-h-[44px] px-2 border border-amber-300 rounded text-xs text-gray-900 bg-white"
          />
          <button
            type="button"
            onClick={ask}
            disabled={busy}
            className="min-h-[44px] px-3 rounded-lg bg-amber-600 text-white text-sm font-medium hover:bg-amber-700 disabled:opacity-50"
          >
            {busy ? 'Sending…' : 'Ask a manager to approve'}
          </button>
        </div>
      )}
      {message && <p className="text-xs text-red-700">{message}</p>}
    </div>
  );
}

export default DiscountApprovalPanel;
