// ============================================================================
// IMS 2.0 — Attendance Check-In/Check-Out
// ============================================================================
import { useState, useEffect } from 'react';
import { useAuth } from '../../context/AuthContext';
import { Clock, MapPin, LogIn, LogOut, CheckCircle, AlertTriangle } from 'lucide-react';

interface AttendanceRecord {
  date: string;
  checkIn: string | null;
  checkOut: string | null;
  status: 'CHECKED_IN' | 'CHECKED_OUT' | 'NOT_CHECKED_IN';
  location?: { lat: number; lng: number };
  late: boolean;
}

export function AttendanceWidget() {
  const { user } = useAuth();
  const [record, setRecord] = useState<AttendanceRecord>({
    date: new Date().toISOString().split('T')[0],
    checkIn: null, checkOut: null,
    status: 'NOT_CHECKED_IN', late: false,
  });
  const [isLoading, setIsLoading] = useState(false);
  const [geoError, setGeoError] = useState<string | null>(null);

  // Load today's record from localStorage (until backend wired)
  useEffect(() => {
    const key = `attendance-${user?.id}-${record.date}`;
    const saved = localStorage.getItem(key);
    if (saved) {
      try { setRecord(JSON.parse(saved)); } catch { /* ignore */ }
    }
  }, [user?.id, record.date]);

  const saveRecord = (updated: AttendanceRecord) => {
    const key = `attendance-${user?.id}-${updated.date}`;
    localStorage.setItem(key, JSON.stringify(updated));
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
    setIsLoading(true);
    const loc = await getLocation();
    const now = new Date();
    const checkInTime = now.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    
    // Late if after 10:15 AM (configurable in production)
    const isLate = now.getHours() > 10 || (now.getHours() === 10 && now.getMinutes() > 15);

    const updated: AttendanceRecord = {
      ...record,
      checkIn: checkInTime,
      status: 'CHECKED_IN',
      location: loc || undefined,
      late: isLate,
    };
    saveRecord(updated);
    // TODO: POST /api/v1/hr/attendance/check-in
    setIsLoading(false);
  };

  const handleCheckOut = async () => {
    setIsLoading(true);
    const now = new Date();
    const checkOutTime = now.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const updated: AttendanceRecord = {
      ...record,
      checkOut: checkOutTime,
      status: 'CHECKED_OUT',
    };
    saveRecord(updated);
    // TODO: POST /api/v1/hr/attendance/check-out
    setIsLoading(false);
  };

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-semibold text-gray-900 flex items-center gap-2">
          <Clock className="w-5 h-5 text-bv-gold-500" /> Attendance
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
