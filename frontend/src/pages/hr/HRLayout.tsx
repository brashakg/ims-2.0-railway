// ============================================================================
// IMS 2.0 - HR module layout
// ============================================================================
// Wave 2 split: HRPage held seven tabs in useState, so only ?tab=leave was
// linkable and every tab paid for both requests. Each surviving tab is now a
// real page with its own URL (/hr/today, /hr/leave, ...).
//
// This layout keeps what was ALWAYS on screen - the editorial header, the
// check-in / check-out / refresh buttons, the four stat cards and the section
// nav - and each section page owns its own widgets.
//
// TWO tabs did NOT survive (see hrRoutes.tsx): "Monthly Summary" was a teaser
// card whose own body links to /attendance, and "My Dashboard" was an older
// second implementation of /my-work. Both now redirect to the real screen
// instead of becoming a bookmarkable dead end.
//
// The old page's load-error banner is gone because it could never fire: both
// reads had their own .catch(() => empty), so the outer try/catch was dead
// code. A failed load shows an empty table, exactly as it did before. The
// LIVE error path - a failed approve/reject - keeps its banner, on /hr/leave.

import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import {
  Clock,
  Calendar,
  CheckCircle,
  XCircle,
  FileText,
  Loader2,
  RefreshCw,
  Settings,
  CalendarSync,
  Trophy,
} from 'lucide-react';
import clsx from 'clsx';
import { hrApi, storeApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { useAttendance, useLeaveRequests } from './hrQueries';

const SECTIONS = [
  { path: '/hr/today', label: "Today's Attendance", icon: Clock, managerOnly: false, badge: false },
  { path: '/hr/leave', label: 'Leave Requests', icon: Calendar, managerOnly: false, badge: true },
  { path: '/hr/week-off-swaps', label: 'Week-off Swaps', icon: CalendarSync, managerOnly: false, badge: false },
  { path: '/hr/shifts', label: 'Shifts', icon: Settings, managerOnly: true, badge: false },
  { path: '/hr/leaderboard', label: 'Leaderboard', icon: Trophy, managerOnly: false, badge: false },
];

export function HRLayout() {
  const { user, hasRole } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const queryClient = useQueryClient();

  const storeId = user?.activeStoreId;
  const attendanceQ = useAttendance(storeId);
  const leavesQ = useLeaveRequests(storeId);
  const attendance = attendanceQ.data ?? [];
  const leaveRequests = leavesQ.data ?? [];
  const isLoading = attendanceQ.isPending || leavesQ.isPending;

  // Shift config is manager-tier (matches the backend require_roles gate).
  const canConfigureShifts = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER']);

  // Stats (unchanged)
  const presentCount = attendance.filter(a => ['PRESENT', 'LATE'].includes(a.status)).length;
  const absentCount = attendance.filter(a => a.status === 'ABSENT').length;
  const onLeaveCount = attendance.filter(a => a.status === 'LEAVE').length;
  const pendingLeaves = leaveRequests.filter(l => l.status === 'PENDING').length;

  // One refresh for the whole module: invalidating 'hr' refetches every ACTIVE
  // query, so the section on screen reloads and nothing it has not mounted does.
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['hr'] });

  // Warm the sibling section chunks once the browser is idle, so the FIRST
  // click on any section renders without the lazy-chunk spinner (Wave 1
  // template). Vite dedupes these against the route-level lazy() imports.
  useEffect(() => {
    const idle: (cb: () => void) => void =
      'requestIdleCallback' in window
        ? (cb) => (window as Window & { requestIdleCallback: (cb: () => void) => void }).requestIdleCallback(cb)
        : (cb) => { setTimeout(cb, 1500); };
    idle(() => {
      void import('./HRTodayPage');
      void import('./HRLeavePage');
      void import('../../components/hr/WeekOffSwap');
      void import('../../components/hr/ShiftSetup');
      void import('./HRLeaderboardPage');
    });
  }, []);

  return (
    <div className="inv-body">
      {/* Editorial header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow mb-1.5">HR &amp; Attendance</div>
          <h1>Who's on the floor.</h1>
          <div className="hint">Geo-fenced check-in, leave management, shift roster, payroll prep. Late marks auto-calculated from shift start.</div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={async () => {
              try {
                const pos = await new Promise<GeolocationPosition>((resolve, reject) =>
                  navigator.geolocation.getCurrentPosition(resolve, reject, { timeout: 10000, enableHighAccuracy: true }));
                const lat = pos.coords.latitude;
                const lng = pos.coords.longitude;

                // Geo-fence enforcement: fetch store location from API
                if (storeId) {
                  try {
                    const storeData = await storeApi.getStore(storeId);
                    const storeLat: number | undefined = storeData?.latitude ?? storeData?.lat;
                    const storeLng: number | undefined = storeData?.longitude ?? storeData?.lng;
                    const radius: number = storeData?.geofence_radius ?? storeData?.geofenceRadius ?? 200;

                    if (storeLat == null || storeLng == null) {
                      toast.warning('Store location coordinates are not configured. Geo-fence check skipped.');
                    } else {
                      // Haversine distance calculation
                      const R = 6371000; // Earth radius in metres
                      const dLat = (lat - storeLat) * Math.PI / 180;
                      const dLng = (lng - storeLng) * Math.PI / 180;
                      const a =
                        Math.sin(dLat / 2) * Math.sin(dLat / 2) +
                        Math.cos(storeLat * Math.PI / 180) * Math.cos(lat * Math.PI / 180) *
                        Math.sin(dLng / 2) * Math.sin(dLng / 2);
                      const distance = R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
                      if (distance > radius) {
                        toast.error(`You are ${Math.round(distance)}m from the store. Check-in requires being within ${radius}m.`);
                        return;
                      }
                    }
                  } catch {
                    // If we cannot fetch store data, skip geo-fence rather than blocking check-in
                    toast.warning('Could not verify store location. Proceeding without geo-fence check.');
                  }
                }

                await hrApi.checkIn(storeId || '', lat, lng);
                toast.success('Checked in successfully');
                await refresh();
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
                // Get latest attendance to find the ID. getAttendance now maps
                // the backend camel keys, so userId / checkOutTime / id resolve.
                const data = await hrApi.getAttendance(storeId || '');
                const records = data?.records || data || [];
                const today = records.find((r: any) => r.userId === user?.id && !r.checkOutTime);
                if (today?.id) {
                  await hrApi.checkOut(today.id);
                  toast.success('Checked out successfully');
                  await refresh();
                } else {
                  toast.warning('No open check-in found to check out.');
                }
              } catch {
                toast.error('Check-out failed. Please try again.');
              }
            }}
            className="btn-outline flex items-center gap-2 text-sm"
          >
            <Clock className="w-4 h-4" /> Check Out
          </button>
          <button
            onClick={refresh}
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

      {/* Stats Cards */}
      <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-green-50 rounded-lg flex items-center justify-center">
              <CheckCircle className="w-5 h-5 text-green-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Present Today</p>
              <p className="text-2xl font-bold text-green-700">{presentCount}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-red-50 rounded-lg flex items-center justify-center">
              <XCircle className="w-5 h-5 text-red-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Absent</p>
              <p className="text-2xl font-bold text-red-700">{absentCount}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-blue-50 rounded-lg flex items-center justify-center">
              <Calendar className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">On Leave</p>
              <p className="text-2xl font-bold text-blue-700">{onLeaveCount}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-amber-50 rounded-lg flex items-center justify-center">
              <FileText className="w-5 h-5 text-amber-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Pending Leaves</p>
              <p className="text-2xl font-bold text-amber-700">{pendingLeaves}</p>
            </div>
          </div>
        </div>
      </div>

      {/* Section nav - same underline tabs, but each now navigates to a real
          URL instead of flipping a useState, so every section is linkable. */}
      <div className="flex border-b border-gray-200 overflow-x-auto">
        {SECTIONS.filter(s => !s.managerOnly || canConfigureShifts).map(({ path, label, icon: TabIcon, badge }) => (
          <button
            key={path}
            onClick={() => navigate(path)}
            className={clsx(
              'flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors whitespace-nowrap',
              pathname === path
                ? 'border-bv-red-600 text-bv-red-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            )}
          >
            <TabIcon className="w-4 h-4" />
            {label}
            {badge && pendingLeaves > 0 && (
              <span className="ml-1 px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 text-xs">
                {pendingLeaves}
              </span>
            )}
          </button>
        ))}
      </div>

      <Outlet />
    </div>
  );
}

export default HRLayout;
