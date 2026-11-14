// ============================================================================
// IMS 2.0 — Attendance Check-In/Check-Out (the ONE self check-in door)
// ============================================================================
// R01: this card shows only what the SERVER holds for today (GET /hr/me/
// attendance -> today). Nothing is cached in the browser and nothing is shown
// as saved until the server says so: a refused / failed / offline check-in or
// check-out stays visibly unsaved with the server's reason and a retry. After
// every successful write the card re-reads the server, so a reload and My Work
// agree with it. Every role uses it (R13: the routes are not finance-gated).
// Location: sent when the browser gives it; when it does not, the request goes
// without coordinates and the server's existing rule decides (exempt roles
// pass, fenced roles are refused with a clear message).

import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../../context/AuthContext';
import { hrApi } from '../../services/api';
import type { MyAttendanceToday } from '../../services/api/hr';
import { Clock, LogIn, LogOut, CheckCircle, AlertTriangle, Loader2 } from 'lucide-react';
import { formatTimeIST } from '../../utils/datetime';
import { getErrorMessage } from '../../utils/errorHandler';

function getLocation(): Promise<{ lat: number; lng: number } | null> {
  return new Promise((resolve) => {
    if (!navigator.geolocation) { resolve(null); return; }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
      () => resolve(null),
      { timeout: 10000, enableHighAccuracy: true },
    );
  });
}

export function AttendanceWidget() {
  const { user } = useAuth();
  const [today, setToday] = useState<MyAttendanceToday | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await hrApi.getMyAttendance();
      setToday(data?.today ?? null);
      setLoadError(null);
    } catch (e) {
      setLoadError(getErrorMessage(e, "Could not load today's attendance."));
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => { if (user?.id) void load(); }, [user?.id, load]);

  const checkedIn = !!today?.check_in;
  const checkedOut = !!today?.check_out;

  const handleCheckIn = async () => {
    if (busy || checkedIn) return;
    setBusy(true);
    setActionError(null);
    try {
      const loc = await getLocation();
      await hrApi.checkIn(user?.activeStoreId || '', loc?.lat, loc?.lng);
      await load();
    } catch (e) {
      setActionError(getErrorMessage(e, 'Check-in was not saved. Please try again.'));
    } finally {
      setBusy(false);
    }
  };

  const handleCheckOut = async () => {
    if (busy || !checkedIn || checkedOut) return;
    setBusy(true);
    setActionError(null);
    try {
      await hrApi.checkOut(user?.activeStoreId || '');
      await load();
    } catch (e) {
      setActionError(getErrorMessage(e, 'Check-out was not saved. Please try again.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-semibold text-gray-900 flex items-center gap-2">
          <Clock className="w-5 h-5 text-bv-red-500" /> Attendance
        </h3>
        <span className="text-xs text-gray-500">{new Date().toLocaleDateString('en-IN', { weekday: 'long', day: '2-digit', month: 'short', timeZone: 'Asia/Kolkata' })}</span>
      </div>

      {!loaded ? (
        <div className="flex justify-center py-4"><Loader2 className="w-5 h-5 animate-spin text-gray-400" /></div>
      ) : !checkedIn ? (
        <div className="text-center py-4">
          <p className="text-sm text-gray-500 mb-3">
            {loadError ? "Today's attendance could not be loaded." : "You haven't checked in today"}
          </p>
          <button onClick={handleCheckIn} disabled={busy}
            className="flex items-center gap-2 mx-auto px-6 py-3 bg-green-500 text-white rounded-xl font-semibold hover:bg-green-600 disabled:opacity-50 transition-colors">
            <LogIn className="w-5 h-5" /> {busy ? 'Saving...' : 'Check In'}
          </button>
        </div>
      ) : !checkedOut ? (
        <div className="space-y-3">
          <div className="bg-green-50 border border-green-200 rounded-lg p-3">
            <p className="text-sm font-medium text-green-700 flex items-center gap-1">
              <CheckCircle className="w-4 h-4" /> Checked in at {formatTimeIST(today?.check_in)}
            </p>
            {today?.is_late && (
              <p className="text-xs text-amber-600 flex items-center gap-1 mt-1">
                <AlertTriangle className="w-3 h-3" /> Late arrival
              </p>
            )}
          </div>
          <button onClick={handleCheckOut} disabled={busy}
            className="w-full flex items-center justify-center gap-2 px-6 py-3 bg-red-500 text-white rounded-xl font-semibold hover:bg-red-600 disabled:opacity-50 transition-colors">
            <LogOut className="w-5 h-5" /> {busy ? 'Saving...' : 'Check Out'}
          </button>
        </div>
      ) : (
        <div className="bg-gray-50 rounded-lg p-4 text-center">
          <CheckCircle className="w-8 h-8 text-green-500 mx-auto mb-2" />
          <p className="font-medium text-gray-900">Day complete</p>
          <div className="flex justify-center gap-4 mt-2 text-sm text-gray-500">
            <span>In: {formatTimeIST(today?.check_in)}</span>
            <span>Out: {formatTimeIST(today?.check_out)}</span>
          </div>
          {today?.is_late && <p className="text-xs text-amber-600 mt-1">Late mark recorded</p>}
        </div>
      )}

      {actionError && (
        <p role="alert" className="text-sm text-red-600 mt-3 flex items-start gap-1">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> {actionError}
        </p>
      )}
    </div>
  );
}
