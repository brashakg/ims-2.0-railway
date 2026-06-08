// ============================================================================
// IMS 2.0 - E4 Approval inbox (approver view)
// ============================================================================
// The approvals the current user is eligible to action. The backend already
// tier/role/store-scopes the list, so we render exactly what it returns. Two
// tabs: Pending (live, urgency-sorted) and History (recent actioned rows).
// Approve / Reject open the PIN modal.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Loader2, Inbox } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { approvalsApi } from '../../services/api/approvals';
import type { ApprovalRequest } from '../../services/api/approvals';
import { ApprovalRequestCard } from '../../components/approvals/ApprovalRequestCard';
import {
  PINApproveModal,
  type PinModalMode,
} from '../../components/approvals/PINApproveModal';

type Tab = 'pending' | 'history';

export function ApprovalInboxPage() {
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
      const [p, all] = await Promise.all([
        approvalsApi.getInbox({ status: 'REQUESTED' }),
        approvalsApi.getInbox({ status: 'ALL' }),
      ]);
      setPending(p.requests || []);
      // History = everything that has left REQUESTED.
      setHistory(
        (all.requests || []).filter((r) => r.status !== 'REQUESTED'),
      );
    } catch {
      toast.error('Failed to load approval inbox');
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  // Most-urgent-first: ascending expiry.
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
        <h1 className="text-xl font-semibold text-gray-900">Approvals</h1>
        <p className="text-sm text-gray-500 mt-0.5">
          Requests you can approve or reject. Each action is PIN-verified and
          audit-logged.
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
              ? 'No pending approvals.'
              : 'No recent approval history.'}
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

export default ApprovalInboxPage;
