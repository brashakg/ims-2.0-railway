// ============================================================================
// IMS 2.0 - Attendance (top-level page)
// ============================================================================
// Was buried inside the HR page's tabs. Now its own destination so floor staff
// can self check-in and managers can run + edit the monthly grid in one place.
//
// Role split:
//   - Self check-in card (geo-fenced, double-check-in guarded) — every role.
//   - Monthly attendance grid + store selector — manager/accountant tier.
//     Admin edit inside the grid is further gated to SUPERADMIN/ADMIN/STORE_MANAGER.
//
// Light theme only. No mock data — grid + check-in are server-authoritative.

import { useEffect, useMemo, useState } from 'react';
import { Clock, Loader2 } from 'lucide-react';
import { hrApi, storeApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { MonthlyAttendanceGrid } from '../../components/hr/MonthlyAttendanceGrid';
import { AttendanceWidget } from '../../components/hr/AttendanceWidget';

interface StoreOpt {
  store_id: string;
  store_name?: string;
  store_code?: string;
}

export function AttendancePage() {
  const { user, hasRole } = useAuth();
  const toast = useToast();

  // Manager-tier roles see the monthly grid (mirrors the grid read gate on the
  // backend). Everyone else just gets the self check-in card.
  const canViewGrid = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']);
  // HQ roles can switch stores; lower roles are pinned to their active store
  // (the backend's validate_store_access enforces this regardless).
  const canSwitchStore = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER']);

  const [stores, setStores] = useState<StoreOpt[]>([]);
  const [selectedStore, setSelectedStore] = useState<string>(user?.activeStoreId || '');
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    if (!canSwitchStore) return;
    storeApi.getStores()
      .then((r) => setStores(r?.stores || []))
      .catch(() => setStores([]));
  }, [canSwitchStore]);

  // Keep the selector defaulted to the user's active store once auth resolves.
  useEffect(() => {
    if (!selectedStore && user?.activeStoreId) setSelectedStore(user.activeStoreId);
  }, [user?.activeStoreId, selectedStore]);

  // The grid is inherently per-store (the backend grid endpoint scopes to one
  // store), so HQ users pick a concrete store rather than an "all" aggregate.
  const gridStore = canSwitchStore
    ? (selectedStore || user?.activeStoreId || undefined)
    : (user?.activeStoreId || undefined);

  const storeLabel = useMemo(() => {
    const s = stores.find((x) => x.store_id === gridStore);
    return s ? (s.store_name || s.store_code || s.store_id) : gridStore;
  }, [stores, gridStore]);

  // Geo-fenced self check-in (same flow as the HR page header). The backend is
  // idempotent on (employee, store, date); the UI guard lives in AttendanceWidget.
  const handleCheckIn = async () => {
    setChecking(true);
    try {
      const pos = await new Promise<GeolocationPosition>((resolve, reject) =>
        navigator.geolocation.getCurrentPosition(resolve, reject, { timeout: 10000, enableHighAccuracy: true }));
      await hrApi.checkIn(user?.activeStoreId || '', pos.coords.latitude, pos.coords.longitude);
      toast.success('Checked in successfully');
    } catch (err: any) {
      if (err?.code === 1) toast.error('Location access is required for check-in. Please enable GPS.');
      else if (err?.code === 3) toast.error('Location request timed out. Please try again.');
      else toast.error('Check-in failed. Please try again.');
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="inv-body">
      {/* Editorial header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Attendance</div>
          <h1>Clock in. Track the floor.</h1>
          <div className="hint">
            Geo-fenced check-in for every employee. Managers see the monthly grid and can correct any day.
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleCheckIn}
            disabled={checking}
            className="btn-primary flex items-center gap-2 text-sm disabled:opacity-50"
          >
            {checking ? <Loader2 className="w-4 h-4 animate-spin" /> : <Clock className="w-4 h-4" />}
            Check In
          </button>
        </div>
      </div>

      {/* Self check-in card — universal. Guards against double check-in. */}
      <div className="max-w-md">
        <AttendanceWidget />
      </div>

      {/* Monthly grid — manager/accountant tier only. */}
      {canViewGrid && (
        <div className="space-y-4">
          {canSwitchStore && (
            <div className="flex items-center gap-2">
              <label htmlFor="att-store" className="text-sm font-medium text-gray-600">Store</label>
              <select
                id="att-store"
                value={gridStore || ''}
                onChange={(e) => setSelectedStore(e.target.value)}
                className="input-field text-sm"
              >
                {stores.length === 0 && gridStore && (
                  <option value={gridStore}>{storeLabel}</option>
                )}
                {stores.map((s) => (
                  <option key={s.store_id} value={s.store_id}>
                    {s.store_name || s.store_code || s.store_id}
                  </option>
                ))}
              </select>
            </div>
          )}
          {/* key forces a fresh fetch when the selected store changes. */}
          <MonthlyAttendanceGrid key={gridStore || 'all'} storeId={gridStore} />
        </div>
      )}
    </div>
  );
}

export default AttendancePage;
