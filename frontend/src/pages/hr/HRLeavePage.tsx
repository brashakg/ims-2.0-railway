// ============================================================================
// IMS 2.0 - HR > Leave Requests  (/hr/leave)
// ============================================================================
// The old HRPage `activeTab === 'leave'` block on its own URL. This was the
// ONE tab the old page could be deep-linked to (/hr?tab=leave); that address
// still works - hrRoutes.tsx forwards it here.
//
// The approve/reject error banner lives here because this is the only error
// the old page could actually surface (see the note in HRLayout.tsx).

import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Calendar, User, AlertTriangle, Loader2 } from 'lucide-react';
import { hrApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useLeaveRequests, type LeaveStatus } from './hrQueries';

const LEAVE_STATUS_CONFIG: Record<LeaveStatus, { label: string; class: string }> = {
  PENDING: { label: 'Pending', class: 'badge-warning' },
  APPROVED: { label: 'Approved', class: 'badge-success' },
  REJECTED: { label: 'Rejected', class: 'badge-error' },
};

const formatDate = (dateStr: string) =>
  new Date(dateStr).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' });

export function HRLeavePage() {
  const { user, hasRole } = useAuth();
  const queryClient = useQueryClient();
  const { data, isPending } = useLeaveRequests(user?.activeStoreId);
  const leaveRequests = data ?? [];

  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const canApproveLeave = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER']);

  const handleLeaveAction = async (leaveId: string, approved: boolean) => {
    setActionLoading(leaveId);
    setError(null);
    try {
      await hrApi.approveLeave(leaveId, approved);
      await queryClient.invalidateQueries({ queryKey: ['hr'] });
    } catch {
      setError('Failed to process leave request.');
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div className="space-y-3">
      {error && (
        <div className="card bg-red-50 border-red-200">
          <div className="flex items-center gap-3 text-red-600">
            <AlertTriangle className="w-5 h-5" />
            <p>{error}</p>
          </div>
        </div>
      )}

      {isPending ? (
        <div className="card flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
        </div>
      ) : leaveRequests.length === 0 ? (
        <div className="card text-center py-12 text-gray-500">
          <Calendar className="w-12 h-12 mx-auto mb-2 opacity-50" />
          <p>No leave requests</p>
        </div>
      ) : (
        leaveRequests.map(leave => {
          const statusConfig = LEAVE_STATUS_CONFIG[leave.status] || { label: leave.status, class: 'bg-gray-100 text-gray-500' };
          const isActionLoading = actionLoading === leave.id;
          return (
            <div key={leave.id} className="card">
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-start gap-4">
                  <div className="w-10 h-10 bg-gray-100 rounded-full flex items-center justify-center">
                    <User className="w-5 h-5 text-gray-500" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-medium text-gray-900">{leave.userName}</span>
                      <span className={statusConfig.class}>{statusConfig.label}</span>
                      {leave.status === 'PENDING' && leave.fast_path && (
                        <span
                          className="px-1.5 py-0.5 rounded text-xs font-medium bg-amber-100 text-amber-700"
                          title="Short-notice leave — eligible for remote PIN approval from your approvals inbox"
                        >
                          Urgent · short notice
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-gray-500">{leave.role}</p>
                    <div className="mt-2 text-sm">
                      <p className="font-medium">{leave.leaveType}</p>
                      <p className="text-gray-500">
                        {formatDate(leave.startDate)}
                        {leave.startDate !== leave.endDate && ` - ${formatDate(leave.endDate)}`}
                        <span className="ml-2">({leave.days} day{leave.days > 1 ? 's' : ''})</span>
                      </p>
                      <p className="text-gray-500 mt-1">Reason: {leave.reason}</p>
                    </div>
                  </div>
                </div>

                {leave.status === 'PENDING' && canApproveLeave && (
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleLeaveAction(leave.id, false)}
                      disabled={isActionLoading}
                      className="btn-outline text-sm text-red-600 border-red-300 hover:bg-red-50 disabled:opacity-50"
                    >
                      {isActionLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Reject'}
                    </button>
                    <button
                      onClick={() => handleLeaveAction(leave.id, true)}
                      disabled={isActionLoading}
                      className="btn-primary text-sm disabled:opacity-50"
                    >
                      {isActionLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Approve'}
                    </button>
                  </div>
                )}
                {leave.status === 'APPROVED' && leave.approvedBy && (
                  <p className="text-xs text-gray-500">
                    Approved by {leave.approvedBy}
                    {leave.approved_via === 'fast_path' && (
                      <span className="ml-1 text-amber-700">(remote)</span>
                    )}
                  </p>
                )}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}

export default HRLeavePage;
