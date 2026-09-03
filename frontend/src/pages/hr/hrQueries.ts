// ============================================================================
// IMS 2.0 - HR module data via React Query
// ============================================================================
// Wave 2 split of the old HRPage tab container. Copies the Wave 1 template
// (pages/reports/reportsQueries.ts): ONE cache shared by the layout and every
// section page (5-min staleTime from the app QueryClient), so switching
// sections renders from cache instead of refetching.
//
// The two reads here are the two the old HRPage fired on mount. The layout
// needs both (the four stat cards count across them); /hr/today and /hr/leave
// then read the SAME cache entries rather than fetching again.
//
// Behaviour is preserved exactly, including the ugly bits:
//   * both reads were gated on activeStoreId - with no active store the page
//     fetched NOTHING and showed empty lists  -> `enabled: !!storeId` on both,
//     even though getLeaves() takes no store.
//   * each read had its own .catch(() => empty), so a failed load showed an
//     empty table and no error  -> the queryFn swallows the same way.
//   * the envelope unwrap was `data?.records || data || []` and then an
//     Array.isArray guard - a BARE ARRAY response is valid and must survive.

import { useQuery } from '@tanstack/react-query';
import { hrApi } from '../../services/api';

export type AttendanceStatus = 'PRESENT' | 'ABSENT' | 'HALF_DAY' | 'LEAVE' | 'LATE';
export type LeaveStatus = 'PENDING' | 'APPROVED' | 'REJECTED';

export interface AttendanceRecord {
  id: string;
  userId: string;
  userName: string;
  role: string;
  checkInTime: string | null;
  checkOutTime: string | null;
  status: AttendanceStatus;
  lateMinutes: number;
  geoVerified?: boolean;
  leaveType?: string;
}

export interface LeaveRequest {
  id: string;
  userId: string;
  userName: string;
  role: string;
  leaveType: string;
  startDate: string;
  endDate: string;
  days: number;
  reason: string;
  status: LeaveStatus;
  appliedAt: string;
  approvedBy?: string;
  // F26 remote-approval passthrough (raw snake_case from the leave doc).
  fast_path?: boolean;
  approved_via?: string | null;
}

/**
 * `data?.<key> || data || []`, then the Array.isArray guard. Verbatim from
 * HRPage. Exported ONLY so the fallback ladder has a test: a previous attempt
 * at this split wrote `data?.records ?? []` and silently dropped the
 * bare-array response shape.
 */
export function unwrapHrList<T>(data: any, key: string): T[] {
  const list = data?.[key] || data || [];
  return Array.isArray(list) ? (list as T[]) : [];
}

/** Today's roster for the active store. Silent-empty on failure, as before. */
export function useAttendance(storeId: string | undefined) {
  return useQuery({
    queryKey: ['hr', 'attendance', storeId ?? 'none'] as const,
    enabled: !!storeId,
    queryFn: async () => {
      const data = await hrApi.getAttendance(storeId as string).catch(() => ({ records: [] }));
      return unwrapHrList<AttendanceRecord>(data, 'records');
    },
  });
}

/** Leave requests. Store-gated only because the old page gated it. */
export function useLeaveRequests(storeId: string | undefined) {
  return useQuery({
    queryKey: ['hr', 'leaves', storeId ?? 'none'] as const,
    enabled: !!storeId,
    queryFn: async () => {
      const data = await hrApi.getLeaves().catch(() => ({ leaves: [] }));
      return unwrapHrList<LeaveRequest>(data, 'leaves');
    },
  });
}
