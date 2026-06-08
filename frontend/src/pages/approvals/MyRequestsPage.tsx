// ============================================================================
// IMS 2.0 - E4 My approval requests (maker view)
// ============================================================================
// The current user's own submitted requests + their live status. Once a
// request is APPROVED, its single-use approval_token is shown (truncated +
// copyable) so the maker can pass it to the consuming action.

import { useCallback, useEffect, useState } from 'react';
import { Loader2, FileText, Copy, Check } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { approvalsApi } from '../../services/api/approvals';
import type { ApprovalRequest } from '../../services/api/approvals';
import { formatDateTimeIST } from '../../utils/datetime';
import {
  actionLabel,
  formatRupees,
  StatusBadge,
} from '../../components/approvals/ApprovalRequestCard';

export function MyRequestsPage() {
  const toast = useToast();
  const [rows, setRows] = useState<ApprovalRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await approvalsApi.getMyRequests();
      setRows(res.requests || []);
    } catch {
      toast.error('Failed to load your requests');
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  const copyToken = useCallback(
    async (token: string) => {
      try {
        await navigator.clipboard.writeText(token);
        setCopied(token);
        toast.success('Approval token copied');
        window.setTimeout(() => setCopied((c) => (c === token ? null : c)), 2000);
      } catch {
        toast.error('Could not copy token');
      }
    },
    [toast],
  );

  return (
    <div className="p-6 max-w-3xl mx-auto">
      <div className="mb-4">
        <h1 className="text-xl font-semibold text-gray-900">My Requests</h1>
        <p className="text-sm text-gray-500 mt-0.5">
          Approval requests you have submitted and their current status.
        </p>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16 text-gray-500">
          <Loader2 className="w-5 h-5 animate-spin mr-2" />
          <span className="text-sm">Loading…</span>
        </div>
      ) : rows.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-gray-400">
          <FileText className="w-8 h-8 mb-2" />
          <p className="text-sm">You have not submitted any approval requests.</p>
        </div>
      ) : (
        <div className="overflow-x-auto border border-gray-200 rounded-lg bg-white">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-500 border-b border-gray-200">
                <th className="px-4 py-2 font-medium">Action</th>
                <th className="px-4 py-2 font-medium text-right">Amount</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 font-medium">Reason</th>
                <th className="px-4 py-2 font-medium">Created</th>
                <th className="px-4 py-2 font-medium">Token</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map((r) => {
                const token = r.approval_token;
                const showToken = r.status === 'APPROVED' && !!token;
                return (
                  <tr key={r.request_id} className="text-gray-700">
                    <td className="px-4 py-2 font-medium text-gray-900">
                      {actionLabel(r.action_type)}
                    </td>
                    <td className="px-4 py-2 text-right font-mono">
                      {formatRupees(r.amount)}
                    </td>
                    <td className="px-4 py-2">
                      <StatusBadge status={r.status} />
                    </td>
                    <td className="px-4 py-2 max-w-[14rem] truncate" title={r.reason}>
                      {r.reason || '—'}
                    </td>
                    <td className="px-4 py-2 text-xs text-gray-500 whitespace-nowrap">
                      {formatDateTimeIST(r.created_at)}
                    </td>
                    <td className="px-4 py-2">
                      {showToken && token ? (
                        <button
                          type="button"
                          onClick={() => copyToken(token)}
                          className="inline-flex items-center gap-1 font-mono text-xs text-blue-700 hover:text-blue-800"
                          title="Copy approval token"
                        >
                          {copied === token ? (
                            <Check className="w-3.5 h-3.5" />
                          ) : (
                            <Copy className="w-3.5 h-3.5" />
                          )}
                          {token.slice(0, 12)}…
                        </button>
                      ) : (
                        <span className="text-xs text-gray-400">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default MyRequestsPage;
