// ============================================================================
// Week-Off Swap (HR Page - "Week-off Swaps" tab)
// ============================================================================
// Employees request to move their weekly-off to another date; a manager
// (STORE_MANAGER+) approves/rejects. The requester can never approve their own
// request (enforced server-side, SYSTEM_INTENT 7). Light theme only.

import { useState, useEffect, useCallback } from 'react';
import { CalendarSync, Loader2, Check, X, Clock } from 'lucide-react';
import { hrApi } from '../../services/api';
import type { WeekOffSwap as WeekOffSwapRow, WeekOffSwapStatus } from '../../services/api/hr';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

const STATUS_BADGE: Record<WeekOffSwapStatus, string> = {
  PENDING: 'badge-warning',
  APPROVED: 'badge-success',
  REJECTED: 'badge-error',
  CANCELLED: 'badge-error',
};

function fmt(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleDateString('en-IN', {
      weekday: 'short',
      day: '2-digit',
      month: 'short',
    });
  } catch {
    return dateStr;
  }
}

export function WeekOffSwap() {
  const { user, hasRole } = useAuth();
  const toast = useToast();

  const canApprove = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER']);

  const [swaps, setSwaps] = useState<WeekOffSwapRow[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [actionId, setActionId] = useState<string | null>(null);

  // Request form.
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate] = useState('');
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const loadSwaps = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await hrApi.getWeekOffSwaps({ storeId: user?.activeStoreId });
      setSwaps(data?.swaps ?? []);
    } catch {
      setSwaps([]);
    } finally {
      setIsLoading(false);
    }
  }, [user?.activeStoreId]);

  useEffect(() => {
    loadSwaps();
  }, [loadSwaps]);

  const handleRequest = async () => {
    if (!fromDate || !toDate) {
      toast.warning('Pick both the current week-off and the new date.');
      return;
    }
    if (fromDate === toDate) {
      toast.warning('The two dates must be different.');
      return;
    }
    setSubmitting(true);
    try {
      await hrApi.requestWeekOffSwap({ from_date: fromDate, to_date: toDate, reason });
      toast.success('Week-off swap requested');
      setFromDate('');
      setToDate('');
      setReason('');
      await loadSwaps();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed to submit request');
    } finally {
      setSubmitting(false);
    }
  };

  const handleApprove = async (swapId: string) => {
    setActionId(swapId);
    try {
      await hrApi.approveWeekOffSwap(swapId);
      toast.success('Swap approved');
      await loadSwaps();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed to approve');
    } finally {
      setActionId(null);
    }
  };

  const handleReject = async (swapId: string) => {
    setActionId(swapId);
    try {
      await hrApi.rejectWeekOffSwap(swapId, 'Rejected by manager');
      toast.success('Swap rejected');
      await loadSwaps();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed to reject');
    } finally {
      setActionId(null);
    }
  };

  const pendingCount = swaps.filter((s) => s.status === 'PENDING').length;

  return (
    <div className="space-y-6">
      {/* Request a swap */}
      <div className="card">
        <h2 className="text-lg font-bold text-gray-900 mb-1 flex items-center gap-2">
          <CalendarSync className="w-5 h-5" /> Request a Week-off Swap
        </h2>
        <p className="text-sm text-gray-500 mb-4">
          Move your scheduled weekly-off to another date. A manager must approve.
        </p>
        <div className="grid grid-cols-1 tablet:grid-cols-3 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Current week-off</label>
            <input type="date" className="input-field" value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">New week-off date</label>
            <input type="date" className="input-field" value={toDate} onChange={(e) => setToDate(e.target.value)} />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Reason (optional)</label>
            <input className="input-field" placeholder="e.g. family function" value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>
        </div>
        <div className="mt-4 flex justify-end">
          <button onClick={handleRequest} disabled={submitting} className="btn-primary flex items-center gap-2 disabled:opacity-50">
            {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <CalendarSync className="w-4 h-4" />}
            Submit request
          </button>
        </div>
      </div>

      {/* Requests list */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold text-gray-900">
            {canApprove ? 'Swap Requests' : 'My Swap Requests'}
          </h2>
          {pendingCount > 0 && (
            <span className="px-2 py-0.5 rounded-full bg-yellow-100 text-yellow-700 text-xs">
              {pendingCount} pending
            </span>
          )}
        </div>

        {isLoading ? (
          <div className="card flex items-center justify-center py-12">
            <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
          </div>
        ) : swaps.length === 0 ? (
          <div className="card text-center py-12 text-gray-500">
            <CalendarSync className="w-12 h-12 mx-auto mb-2 opacity-50" />
            <p>No week-off swap requests.</p>
          </div>
        ) : (
          swaps.map((swap) => {
            const isOwn = swap.requested_by === user?.id || swap.employee_id === user?.id;
            const busy = actionId === swap.swap_id;
            return (
              <div key={swap.swap_id} className="card">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex items-start gap-3">
                    <div className="w-10 h-10 bg-gray-100 rounded-full flex items-center justify-center">
                      <Clock className="w-5 h-5 text-gray-500" />
                    </div>
                    <div>
                      <div className="flex items-center gap-2 mb-1">
                        <span className="font-medium text-gray-900">{swap.employee_id}</span>
                        <span className={STATUS_BADGE[swap.status]}>{swap.status}</span>
                      </div>
                      <p className="text-sm text-gray-700">
                        {fmt(swap.from_date)} <span className="mx-1 text-gray-400">&rarr;</span> {fmt(swap.to_date)}
                      </p>
                      {swap.reason && <p className="text-sm text-gray-500 mt-1">Reason: {swap.reason}</p>}
                      {swap.status === 'REJECTED' && swap.rejection_reason && (
                        <p className="text-xs text-red-500 mt-1">Rejected: {swap.rejection_reason}</p>
                      )}
                    </div>
                  </div>

                  {/* Approve/reject — managers only, and never on your own request. */}
                  {swap.status === 'PENDING' && canApprove && !isOwn && (
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleReject(swap.swap_id)}
                        disabled={busy}
                        className="btn-outline text-sm text-red-600 border-red-300 hover:bg-red-50 disabled:opacity-50 flex items-center gap-1"
                      >
                        {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <X className="w-4 h-4" />}
                        Reject
                      </button>
                      <button
                        onClick={() => handleApprove(swap.swap_id)}
                        disabled={busy}
                        className="btn-primary text-sm disabled:opacity-50 flex items-center gap-1"
                      >
                        {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                        Approve
                      </button>
                    </div>
                  )}
                  {swap.status === 'PENDING' && canApprove && isOwn && (
                    <p className="text-xs text-gray-400 self-center max-w-[140px] text-right">
                      You can't approve your own request
                    </p>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

export default WeekOffSwap;
