// ============================================================================
// IMS 2.0 — Attendance Check-In/Check-Out
// ============================================================================
// Self-service check-in card. Server-authoritative with a localStorage cache
// for instant paint. Guards against double check-in:
//   - On mount it reconciles against today's server record (so an already-
//     checked-in user sees the checked-in state even on a fresh device).
//   - The Check In button is disabled once checked in.
// The backend is idempotent on (employee, store, date), so a stray duplicate
// POST is harmless; this guard keeps the UI honest.

import { useState, useEffect } from 'react';
import { useAuth } from '../../context/AuthContext';
import { hrApi } from '../../services/api';
import { Clock, MapPin, LogIn, LogOut, CheckCircle, AlertTriangle } from 'lucide-react';

interface AttendanceRecord {
  date: string;
  attendanceId: string | null;
  checkIn: string | null;
  checkOut: string | null;
  status: 'CHECKED_IN' | 'CHECKED_OUT' | 'NOT_CHECKED_IN';
  location?: { lat: number; lng: number };
  late: boolean;
}

function todayIso(): string {
  return new Date().toISOString().split('T')[0];
}

export function AttendanceWidget() {
  const { user } = useAuth();
  const [record, setRecord] = useState<AttendanceRecord>({
    date: todayIso(),
    attendanceId: null,
    checkIn: null, checkOut: null,
    status: 'NOT_CHECKED_IN', late: false,
  });
  const [isLoading, setIsLoading] = useState(false);
  const [geoError, setGeoError] = useState<string | null>(null);

  // Optimistic cache key.
  const cacheKey = `attendance-${user?.id}-${record.date}`;

  // 1) Instant paint from localStorage. 2) Reconcile with the server so an
  //    already-checked-in user (on any device) sees the real state.
  useEffect(() => {
    const saved = localStorage.getItem(cacheKey);
    if (saved) {
      try { setRecord((prev) => ({ ...prev, ...JSON.parse(saved) })); } catch { /* ignore */ }
    }
    let cancelled = false;
    (async () => {
      if (!user?.id) return;
      try {
        const data = await hrApi.getAttendance(user.activeStoreId || '', todayIso());
        const records: any[] = data?.records || [];
        const mine = records.find(
          (r) => (r.employeeId === user.id || r.userId === user.id) && r.date === todayIso(),
        );
        if (mine && !cancelled) {
          setRecord((prev) => ({
            ...prev,
            attendanceId: mine.attendanceId || mine.id || null,
            checkIn: mine.checkIn ?? prev.checkIn,
            checkOut: mine.checkOut ?? prev.checkOut,
            status: mine.checkOut ? 'CHECKED_OUT' : 'CHECKED_IN',
          }));
        }
      } catch {
        // Offline / no DB — keep the optimistic localStorage state.
      }
    })();
    return () => { cancelled = true; };
  }, [user?.id, user?.activeStoreId]);

  const saveRecord = (updated: AttendanceRecord) => {
    localStorage.setItem(`attendance-${user?.id}-${updated.date}`, JSON.stringify(updated));
    setRecord(updated);
  };

  const getLocation = (): Promise<{ lat: number; lng: number } | null> => {
    return new Promise((resolve) => {
      if (!navigator.geolocation) { setGeoError('Geolocation not supported'); resolve(null); return; }
      navigator.geolocation.getCurrentPosition(
        (pos) => { setGeoError(null); resolve({ lat: pos.coords.latitude, lng: pos.coords.longitude }); },
        () => { setGeoError('Location access denied — check-in will proceed without location'); resolve(null); },
        { timeout: 5000 }
      );
    });
  };

  const handleCheckIn = async () => {
    // UI guard: never double check-in.
    if (record.status !== 'NOT_CHECKED_IN') return;
    setIsLoading(true);
    const loc = await getLocation();
    try {
      // Geo-fenced, late-aware check-in (query-param contract). Idempotent server-side.
      const res = await hrApi.checkIn(user?.activeStoreId || '', loc?.lat ?? 0, loc?.lng ?? 0);
      saveRecord({
        ...record,
        checkIn: res?.checkInTime || new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }),
        status: 'CHECKED_IN',
        location: loc || undefined,
        late: !!res?.is_late,
      });
    } catch {
      // Fail-soft: still reflect locally so the user isn't stuck; the backend
      // idempotency means a later real check-in won't duplicate.
      saveRecord({
        ...record,
        checkIn: new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }),
        status: 'CHECKED_IN',
        location: loc || undefined,
      });
    } finally {
      setIsLoading(false);
    }
  };

  const handleCheckOut = async () => {
    if (record.status !== 'CHECKED_IN') return;
    setIsLoading(true);
    const checkOutTime = new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
    try {
      if (record.attendanceId) {
        await hrApi.checkOut(record.attendanceId);
      }
    } catch {
      // Keep local state regardless.
    }
    saveRecord({ ...record, checkOut: checkOutTime, status: 'CHECKED_OUT' });
    setIsLoading(false);
  };

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-semibold text-gray-900 flex items-center gap-2">
          <Clock className="w-5 h-5 text-bv-red-500" /> Attendance
        </h3>
        <span className="text-xs text-gray-500">{new Date().toLocaleDateString('en-IN', { weekday: 'long', day: '2-digit', month: 'short' })}</span>
      </div>

      {record.status === 'NOT_CHECKED_IN' && (
        <div className="text-center py-4">
          <p className="text-sm text-gray-500 mb-3">You haven't checked in today</p>
          <button onClick={handleCheckIn} disabled={isLoading}
            className="flex items-center gap-2 mx-auto px-6 py-3 bg-green-500 text-white rounded-xl font-semibold hover:bg-green-600 disabled:opacity-50 transition-colors">
            <LogIn className="w-5 h-5" /> {isLoading ? 'Getting location...' : 'Check In'}
          </button>
        </div>
      )}

      {record.status === 'CHECKED_IN' && (
        <div className="space-y-3">
          <div className="bg-green-50 border border-green-200 rounded-lg p-3 flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-green-700 flex items-center gap-1">
                <CheckCircle className="w-4 h-4" /> Checked in at {record.checkIn}
              </p>
              {record.late && (
                <p className="text-xs text-amber-600 flex items-center gap-1 mt-1">
                  <AlertTriangle className="w-3 h-3" /> Late arrival
                </p>
              )}
              {record.location && (
                <p className="text-xs text-gray-500 flex items-center gap-1 mt-1">
                  <MapPin className="w-3 h-3" /> Location recorded
                </p>
              )}
            </div>
          </div>
          <button onClick={handleCheckOut} disabled={isLoading}
            className="w-full flex items-center justify-center gap-2 px-6 py-3 bg-red-500 text-white rounded-xl font-semibold hover:bg-red-600 disabled:opacity-50 transition-colors">
            <LogOut className="w-5 h-5" /> Check Out
          </button>
        </div>
      )}

      {record.status === 'CHECKED_OUT' && (
        <div className="bg-gray-50 rounded-lg p-4 text-center">
          <CheckCircle className="w-8 h-8 text-green-500 mx-auto mb-2" />
          <p className="font-medium text-gray-900">Day complete</p>
          <div className="flex justify-center gap-4 mt-2 text-sm text-gray-500">
            <span>In: {record.checkIn}</span>
            <span>Out: {record.checkOut}</span>
          </div>
          {record.late && <p className="text-xs text-amber-600 mt-1">Late mark recorded</p>}
        </div>
      )}

      {geoError && <p className="text-xs text-amber-600 mt-2">{geoError}</p>}
    </div>
  );
}
