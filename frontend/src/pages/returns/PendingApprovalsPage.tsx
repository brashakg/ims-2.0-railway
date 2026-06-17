// ============================================================================
// IMS 2.0 - Refund Approvals queue (F27, approver view)
// ============================================================================
// The refund-specific slice of the E4 approver inbox: the refunds awaiting a
// PIN-approval (action_type REFUND_APPROVAL_MATRIX), most-urgent-first by expiry.
// Approve / Reject open the shared PIN modal, which calls the EXISTING approval
// endpoints (POST /approvals/requests/{id}/approve|reject).
//
// REUSE: this is a thin, refund-filtered wrapper over approvalsApi.getInbox +
// ApprovalRequestCard + PINApproveModal — no new approval logic, no parallel
// modal. The generic /approvals inbox shows ALL action types; this view exists
// so a refund-desk approver sees only refunds (with the order id surfaced).

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Loader2, Inbox, RotateCcw } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { approvalsApi } from '../../services/api/approvals';
import type { ApprovalRequest } from '../../services/api/approvals';
import { ApprovalRequestCard } from '../../components/approvals/ApprovalRequestCard';
import {
  PINApproveModal,
  type PinModalMode,
} from '../../components/approvals/PINApproveModal';

const REFUND_ACTION = 'REFUND_APPROVAL_MATRIX';
type Tab = 'pending' | 'history';

const isRefund = (r: ApprovalRequest) => r.action_type === REFUND_ACTION;

export function PendingApprovalsPage() {
  const toast = useToast();
  const [tab, setTab] = useState<Tab>('pending');
  const [pending, setPending] = useState<ApprovalRequest[]>([]);
  const [history, setHistory] = useState<ApprovalRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState<{
    request: ApprovalRequest;
    mode: PinModalMode;
  } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // The backend already tier/role/store-scopes the inbox; we narrow to the
      // refund action client-side (the inbox is small — one request per tier).
      const [p, all] = await Promise.all([
        approvalsApi.getInbox({ status: 'REQUESTED' }),
        approvalsApi.getInbox({ status: 'ALL' }),
      ]);
      setPending((p.requests || []).filter(isRefund));
      setHistory(
        (all.requests || []).filter((r) => isRefund(r) && r.status !== 'REQUESTED'),
      );
    } catch {
      toast.error('Failed to load refund approvals');
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  // Most-urgent-first: ascending expiry (refund requests expire in 60 min).
  const sortedPending = useMemo(() => {
    return [...pending].sort((a, b) => {
      const ax = a.expires_at ? new Date(a.expires_at).getTime() : Infinity;
      const bx = b.expires_at ? new Date(b.expires_at).getTime() : Infinity;
      return ax - bx;
    });
  }, [pending]);

  const rows = tab === 'pending' ? sortedPending : history;

  return (
    <div className="p-6 max-w-3xl mx-auto">
      <div className="mb-4">
        <div className="flex items-center gap-2">
          <RotateCcw className="w-5 h-5 text-gray-500" />
          <h1 className="text-xl font-semibold text-gray-900">Refund Approvals</h1>
        </div>
        <p className="text-sm text-gray-500 mt-0.5">
          Refunds awaiting your sign-off. Each refund shows its amount, original
          tender and order; approving is PIN-verified and audit-logged. Requests
          expire 60 minutes after they are raised.
        </p>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-gray-200 mb-4">
        <TabButton
          active={tab === 'pending'}
          onClick={() => setTab('pending')}
          label={`Pending${pending.length ? ` (${pending.length})` : ''}`}
        />
        <TabButton
          active={tab === 'history'}
          onClick={() => setTab('history')}
          label="History"
        />
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16 text-gray-500">
          <Loader2 className="w-5 h-5 animate-spin mr-2" />
          <span className="text-sm">Loading…</span>
        </div>
      ) : rows.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-gray-400">
          <Inbox className="w-8 h-8 mb-2" />
          <p className="text-sm">
            {tab === 'pending'
              ? 'No refunds awaiting approval.'
              : 'No recent refund approval history.'}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {rows.map((r) => (
            <ApprovalRequestCard
              key={r.request_id}
              request={r}
              showActions={tab === 'pending'}
              onApprove={(req) => setModal({ request: req, mode: 'approve' })}
              onReject={(req) => setModal({ request: req, mode: 'reject' })}
            />
          ))}
        </div>
      )}

      {modal && (
        <PINApproveModal
          request={modal.request}
          mode={modal.mode}
          onClose={() => setModal(null)}
          onDone={load}
        />
      )}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
        active
          ? 'border-blue-600 text-blue-700'
          : 'border-transparent text-gray-500 hover:text-gray-700'
      }`}
    >
      {label}
    </button>
  );
}

export default PendingApprovalsPage;
