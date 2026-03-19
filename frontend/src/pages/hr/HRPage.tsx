// ============================================================================
// IMS 2.0 - HR Management Page
// ============================================================================
// NO MOCK DATA - All data from API

import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Clock,
  Calendar,
  CheckCircle,
  XCircle,
  MapPin,
  User,
  AlertTriangle,
  FileText,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import { hrApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import clsx from 'clsx';
import { MonthlyAttendanceGrid } from '../../components/hr/MonthlyAttendanceGrid';
import { EmployeeSelfService } from '../../components/hr/EmployeeSelfService';

type AttendanceStatus = 'PRESENT' | 'ABSENT' | 'HALF_DAY' | 'LEAVE' | 'LATE';
type LeaveStatus = 'PENDING' | 'APPROVED' | 'REJECTED';

// Attendance record type
interface AttendanceRecord {
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

// Leave request type
interface LeaveRequest {
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
}

const ATTENDANCE_STATUS_CONFIG: Record<AttendanceStatus, { label: string; class: string }> = {
  PRESENT: { label: 'Present', class: 'bg-green-100 text-green-600' },
  ABSENT: { label: 'Absent', class: 'bg-red-100 text-red-600' },
  HALF_DAY: { label: 'Half Day', class: 'bg-yellow-100 text-yellow-600' },
  LEAVE: { label: 'On Leave', class: 'bg-blue-100 text-blue-600' },
  LATE: { label: 'Late', class: 'bg-orange-100 text-orange-600' },
};

const LEAVE_STATUS_CONFIG: Record<LeaveStatus, { label: string; class: string }> = {
  PENDING: { label: 'Pending', class: 'badge-warning' },
  APPROVED: { label: 'Approved', class: 'badge-success' },
  REJECTED: { label: 'Rejected', class: 'badge-error' },
};

export function HRPage() {
  const { user, hasRole } = useAuth();
  const toast = useToast();
  const [searchParams] = useSearchParams();

  // Data state
  const [attendance, setAttendance] = useState<AttendanceRecord[]>([]);
  const [leaveRequests, setLeaveRequests] = useState<LeaveRequest[]>([]);

  // UI state
  const [activeTab, setActiveTab] = useState<'attendance' | 'leave' | 'monthly_grid' | 'self_service'>('attendance');

  // Sync active tab from URL query params (e.g. /hr?tab=leave)
  useEffect(() => {
    const tabParam = searchParams.get('tab');
    if (tabParam === 'leave' && activeTab !== 'leave') {
      setActiveTab('leave');
    }
  }, [searchParams]);

  // Loading state
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  // Role-based permissions
  const canApproveLeave = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER']);

  // Load data on mount
  useEffect(() => {
    loadData();
  }, [user?.activeStoreId]);

  const loadData = async () => {
    if (!user?.activeStoreId) {
      setIsLoading(false);
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const [attendanceData, leavesData] = await Promise.all([
        hrApi.getAttendance(user.activeStoreId).catch(() => ({ records: [] })),
        hrApi.getLeaves().catch(() => ({ leaves: [] })),
      ]);

      const records = attendanceData?.records || attendanceData || [];
      setAttendance(Array.isArray(records) ? records : []);

      const leaves = leavesData?.leaves || leavesData || [];
      setLeaveRequests(Array.isArray(leaves) ? leaves : []);
    } catch {
      setError('Failed to load data. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleLeaveAction = async (leaveId: string, approved: boolean) => {
    setActionLoading(leaveId);
    try {
      await hrApi.approveLeave(leaveId, approved);
      // Refresh data
      await loadData();
    } catch {
      setError('Failed to process leave request.');
    } finally {
      setActionLoading(null);
    }
  };

  // Stats
  const presentCount = attendance.filter(a => ['PRESENT', 'LATE'].includes(a.status)).length;
  const absentCount = attendance.filter(a => a.status === 'ABSENT').length;
  const onLeaveCount = attendance.filter(a => a.status === 'LEAVE').length;
  const pendingLeaves = leaveRequests.filter(l => l.status === 'PENDING').length;

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleDateString('en-IN', {
      day: '2-digit',
      month: 'short',
    });
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">HR Management</h1>
          <p className="text-gray-400">Attendance tracking and leave management</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={async () => {
              try {
                const pos = await new Promise<GeolocationPosition>((resolve, reject) =>
                  navigator.geolocation.getCurrentPosition(resolve, reject, { timeout: 10000, enableHighAccuracy: true }));
                const lat = pos.coords.latitude;
                const lng = pos.coords.longitude;

                // Geo-fence enforcement: store locations (Bokaro Steel City area)
                const STORE_LOCATIONS: Record<string, { lat: number; lng: number; radius: number }> = {
                  'BV-BOK-01': { lat: 23.6693, lng: 86.1511, radius: 200 }, // 200m radius
                  'BV-BOK-02': { lat: 23.6750, lng: 86.1480, radius: 200 },
                };
                const storeLoc = STORE_LOCATIONS[user?.activeStoreId || ''];
                if (storeLoc) {
                  // Haversine distance calculation
                  const R = 6371000; // Earth's radius in meters
                  const dLat = (lat - storeLoc.lat) * Math.PI / 180;
                  const dLng = (lng - storeLoc.lng) * Math.PI / 180;
                  const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
                    Math.cos(storeLoc.lat * Math.PI / 180) * Math.cos(lat * Math.PI / 180) *
                    Math.sin(dLng/2) * Math.sin(dLng/2);
                  const distance = R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
                  if (distance > storeLoc.radius) {
                    toast.error(`You are ${Math.round(distance)}m from the store. Check-in requires being within ${storeLoc.radius}m.`);
                    return;
                  }
                }

                await hrApi.checkIn(user?.activeStoreId || '', lat, lng);
                toast.success('Checked in successfully');
                await loadData();
              } catch (err: any) {
                if (err?.code === 1) toast.error('Location access is required for check-in. Please enable GPS.');
                else if (err?.code === 3) toast.error('Location request timed out. Please try again.');
                else toast.error('Check-in failed. Please try again.');
              }
            }}
            className="btn-primary flex items-center gap-2 text-sm"
          >
            <Clock className="w-4 h-4" /> Check In
          </button>
          <button
            onClick={async () => {
              try {
                // Get latest attendance to find the ID
                const data = await hrApi.getAttendance(user?.activeStoreId || '');
                const records = data?.records || data || [];
                const today = records.find((r: any) => r.userId === user?.id && !r.checkOut);
                if (today?.id) {
                  await hrApi.checkOut(today.id);
                  await loadData();
                }
              } catch { /* ignore */ }
            }}
            className="btn-outline flex items-center gap-2 text-sm"
          >
            <Clock className="w-4 h-4" /> Check Out
          </button>
          <button
            onClick={loadData}
            disabled={isLoading}
            className="btn-outline flex items-center gap-2"
          >
            {isLoading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4" />
            )}
            Refresh
          </button>
        </div>
      </div>

      {/* Error State */}
      {error && (
        <div className="card bg-red-50 border-red-200">
          <div className="flex items-center gap-3 text-red-600">
            <AlertTriangle className="w-5 h-5" />
            <p>{error}</p>
            <button onClick={loadData} className="ml-auto text-sm underline">
              Retry
            </button>
          </div>
        </div>
      )}

      {/* Stats Cards */}
      <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-green-100 rounded-lg flex items-center justify-center">
              <CheckCircle className="w-5 h-5 text-green-600" />
            </div>
            <div>
              <p className="text-sm text-gray-400">Present Today</p>
              <p className="text-2xl font-bold text-green-600">{presentCount}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-red-100 rounded-lg flex items-center justify-center">
              <XCircle className="w-5 h-5 text-red-600" />
            </div>
            <div>
              <p className="text-sm text-gray-400">Absent</p>
              <p className="text-2xl font-bold text-red-600">{absentCount}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
              <Calendar className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <p className="text-sm text-gray-400">On Leave</p>
              <p className="text-2xl font-bold text-blue-600">{onLeaveCount}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-yellow-100 rounded-lg flex items-center justify-center">
              <FileText className="w-5 h-5 text-yellow-600" />
            </div>
            <div>
              <p className="text-sm text-gray-400">Pending Leaves</p>
              <p className="text-2xl font-bold text-yellow-600">{pendingLeaves}</p>
            </div>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-gray-700">
        <button
          onClick={() => setActiveTab('attendance')}
          className={clsx(
            'flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors',
            activeTab === 'attendance'
              ? 'border-bv-red-600 text-bv-red-600'
              : 'border-transparent text-gray-400 hover:text-gray-300'
          )}
        >
          <Clock className="w-4 h-4" />
          Today's Attendance
        </button>
        <button
          onClick={() => setActiveTab('leave')}
          className={clsx(
            'flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors',
            activeTab === 'leave'
              ? 'border-bv-red-600 text-bv-red-600'
              : 'border-transparent text-gray-400 hover:text-gray-300'
          )}
        >
          <Calendar className="w-4 h-4" />
          Leave Requests
          {pendingLeaves > 0 && (
            <span className="ml-1 px-2 py-0.5 rounded-full bg-yellow-100 text-yellow-600 text-xs">
              {pendingLeaves}
            </span>
          )}
        </button>
        <button
          onClick={() => setActiveTab('monthly_grid')}
          className={clsx(
            'flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors',
            activeTab === 'monthly_grid'
              ? 'border-bv-red-600 text-bv-red-600'
              : 'border-transparent text-gray-400 hover:text-gray-300'
          )}
        >
          <Calendar className="w-4 h-4" />
          Monthly View
        </button>
        <button
          onClick={() => setActiveTab('self_service')}
          className={clsx(
            'flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors',
            activeTab === 'self_service'
              ? 'border-bv-red-600 text-bv-red-600'
              : 'border-transparent text-gray-400 hover:text-gray-300'
          )}
        >
          <User className="w-4 h-4" />
          My Dashboard
        </button>
      </div>

      {/* Attendance Tab */}
      {activeTab === 'attendance' && (
        <div className="card overflow-hidden">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
            </div>
          ) : attendance.length === 0 ? (
            <div className="text-center py-12 text-gray-400">
              <Clock className="w-12 h-12 mx-auto mb-2 opacity-50" />
              <p>No attendance records for today</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead className="bg-gray-900 border-b border-gray-700">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Employee</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Role</th>
                    <th className="px-4 py-3 text-center text-xs font-medium text-gray-400 uppercase">Check In</th>
                    <th className="px-4 py-3 text-center text-xs font-medium text-gray-400 uppercase">Check Out</th>
                    <th className="px-4 py-3 text-center text-xs font-medium text-gray-400 uppercase">Status</th>
                    <th className="px-4 py-3 text-center text-xs font-medium text-gray-400 uppercase">Geo</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {attendance.map(record => {
                    const statusConfig = ATTENDANCE_STATUS_CONFIG[record.status] || { label: record.status, class: 'bg-gray-700 text-gray-400' };
                    return (
                      <tr key={record.id} className="hover:bg-gray-900">
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-3">
                            <div className="w-8 h-8 bg-gray-700 rounded-full flex items-center justify-center">
                              <User className="w-4 h-4 text-gray-400" />
                            </div>
                            <span className="font-medium text-white">{record.userName}</span>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-400">{record.role}</td>
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
                            <span className="text-gray-400">-</span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-center">
                          {record.checkOutTime || <span className="text-gray-400">-</span>}
                        </td>
                        <td className="px-4 py-3 text-center">
                          <span className={clsx('px-2 py-1 rounded-full text-xs font-medium', statusConfig.class)}>
                            {statusConfig.label}
                          </span>
                          {record.leaveType && (
                            <span className="block text-xs text-gray-400 mt-1">{record.leaveType}</span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-center">
                          {record.geoVerified ? (
                            <MapPin className="w-4 h-4 text-green-500 mx-auto" />
                          ) : record.status === 'LEAVE' ? (
                            <span className="text-gray-400">-</span>
                          ) : (
                            <AlertTriangle className="w-4 h-4 text-yellow-500 mx-auto" />
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
      )}

      {/* Leave Tab */}
      {activeTab === 'leave' && (
        <div className="space-y-3">
          {isLoading ? (
            <div className="card flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
            </div>
          ) : leaveRequests.length === 0 ? (
            <div className="card text-center py-12 text-gray-400">
              <Calendar className="w-12 h-12 mx-auto mb-2 opacity-50" />
              <p>No leave requests</p>
            </div>
          ) : (
            leaveRequests.map(leave => {
              const statusConfig = LEAVE_STATUS_CONFIG[leave.status] || { label: leave.status, class: 'bg-gray-700 text-gray-400' };
              const isActionLoading = actionLoading === leave.id;
              return (
                <div key={leave.id} className="card">
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex items-start gap-4">
                      <div className="w-10 h-10 bg-gray-700 rounded-full flex items-center justify-center">
                        <User className="w-5 h-5 text-gray-400" />
                      </div>
                      <div>
                        <div className="flex items-center gap-2 mb-1">
                          <span className="font-medium text-white">{leave.userName}</span>
                          <span className={statusConfig.class}>{statusConfig.label}</span>
                        </div>
                        <p className="text-sm text-gray-400">{leave.role}</p>
                        <div className="mt-2 text-sm">
                          <p className="font-medium">{leave.leaveType}</p>
                          <p className="text-gray-400">
                            {formatDate(leave.startDate)}
                            {leave.startDate !== leave.endDate && ` - ${formatDate(leave.endDate)}`}
                            <span className="ml-2">({leave.days} day{leave.days > 1 ? 's' : ''})</span>
                          </p>
                          <p className="text-gray-400 mt-1">Reason: {leave.reason}</p>
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
                      <p className="text-xs text-gray-400">
                        Approved by {leave.approvedBy}
                      </p>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>
      )}

      {/* Monthly Attendance Grid Tab */}
      {activeTab === 'monthly_grid' && (
        <div>
          <MonthlyAttendanceGrid />
        </div>
      )}

      {/* Employee Self Service Tab */}
      {activeTab === 'self_service' && (
        <div>
          <EmployeeSelfService />
        </div>
      )}
    </div>
  );
}

export default HRPage;
