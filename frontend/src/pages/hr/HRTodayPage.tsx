// ============================================================================
// IMS 2.0 - HR > Today's Attendance  (/hr/today)
// ============================================================================
// The old HRPage `activeTab === 'attendance'` block on its own URL. Reads the
// SAME cache entry the layout's stat cards read, so landing here fires no
// extra request.
//
// NOTE: this is the store's roster for TODAY. The monthly grid (and the
// self check-in card for floor staff) lives at /attendance, deliberately
// outside /hr so it survives the HR module being switched off.

import { Clock, User, MapPin, AlertTriangle, Loader2 } from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import { useAttendance, type AttendanceStatus } from './hrQueries';

const ATTENDANCE_STATUS_CONFIG: Record<AttendanceStatus, { label: string; class: string }> = {
  PRESENT: { label: 'Present', class: 'bg-green-50 text-green-700' },
  ABSENT: { label: 'Absent', class: 'bg-red-50 text-red-700' },
  HALF_DAY: { label: 'Half Day', class: 'bg-amber-50 text-amber-700' },
  LEAVE: { label: 'On Leave', class: 'bg-blue-50 text-blue-700' },
  LATE: { label: 'Late', class: 'bg-amber-50 text-amber-700' },
};

export function HRTodayPage() {
  const { user } = useAuth();
  const { data, isPending } = useAttendance(user?.activeStoreId);
  const attendance = data ?? [];

  return (
    <div className="card overflow-hidden">
      {isPending ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
        </div>
      ) : attendance.length === 0 ? (
        <div className="text-center py-12 text-gray-500">
          <Clock className="w-12 h-12 mx-auto mb-2 opacity-50" />
          <p>No attendance records for today</p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Employee</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Role</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Check In</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Check Out</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Status</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Geo</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {attendance.map(record => {
                const statusConfig = ATTENDANCE_STATUS_CONFIG[record.status] || { label: record.status, class: 'bg-gray-100 text-gray-500' };
                return (
                  <tr key={record.id} className="hover:bg-gray-50">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 bg-gray-100 rounded-full flex items-center justify-center">
                          <User className="w-4 h-4 text-gray-500" />
                        </div>
                        <span className="font-medium text-gray-900">{record.userName}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-500">{record.role}</td>
                    <td className="px-4 py-3 text-center">
                      {record.checkInTime ? (
                        <div>
                          <span className="font-medium">{record.checkInTime}</span>
                          {record.lateMinutes > 0 && (
                            <span className="ml-1 text-xs text-red-500">
                              (+{record.lateMinutes}m)
                            </span>
                          )}
                        </div>
                      ) : (
                        <span className="text-gray-500">-</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-center">
                      {record.checkOutTime || <span className="text-gray-500">-</span>}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <span className={clsx('px-2 py-1 rounded-full text-xs font-medium', statusConfig.class)}>
                        {statusConfig.label}
                      </span>
                      {record.leaveType && (
                        <span className="block text-xs text-gray-500 mt-1">{record.leaveType}</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-center">
                      {record.geoVerified ? (
                        <MapPin className="w-4 h-4 text-green-500 mx-auto" />
                      ) : record.status === 'LEAVE' ? (
                        <span className="text-gray-500">-</span>
                      ) : (
                        <AlertTriangle className="w-4 h-4 text-amber-500 mx-auto" />
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

export default HRTodayPage;
