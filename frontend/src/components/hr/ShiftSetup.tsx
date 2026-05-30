// ============================================================================
// Shift Setup (HR Page - "Shifts" tab)
// ============================================================================
// Manager-tier screen to define work shifts (name, start/end, grace window,
// weekly-off day(s)) and assign a shift to an employee. Late marks are
// auto-calculated from the shift start + grace at check-in (server-side).
// Light theme only; design-token classes.

import { useState, useEffect, useCallback } from 'react';
import { Clock, Plus, Loader2, Users, CalendarOff } from 'lucide-react';
import { hrApi } from '../../services/api';
import type { Shift } from '../../services/api/hr';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

// Python weekday() convention: Monday=0 .. Sunday=6.
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function weeklyOffLabel(days: number[]): string {
  if (!days || days.length === 0) return 'None';
  return days
    .slice()
    .sort((a, b) => a - b)
    .map((d) => WEEKDAYS[d] ?? `?${d}`)
    .join(', ');
}

export function ShiftSetup() {
  const { user } = useAuth();
  const toast = useToast();

  const [shifts, setShifts] = useState<Shift[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [assigningFor, setAssigningFor] = useState<string | null>(null);

  // New-shift form state.
  const [name, setName] = useState('');
  const [startTime, setStartTime] = useState('10:00');
  const [endTime, setEndTime] = useState('19:00');
  const [graceMinutes, setGraceMinutes] = useState(15);
  const [weeklyOff, setWeeklyOff] = useState<number[]>([6]); // default Sunday off

  // Assign form state.
  const [assignEmployeeId, setAssignEmployeeId] = useState('');

  const loadShifts = useCallback(async () => {
    if (!user?.activeStoreId) return;
    setIsLoading(true);
    try {
      const data = await hrApi.getShifts({ storeId: user.activeStoreId, activeOnly: true });
      setShifts(data?.shifts ?? []);
    } catch {
      setShifts([]);
    } finally {
      setIsLoading(false);
    }
  }, [user?.activeStoreId]);

  useEffect(() => {
    loadShifts();
  }, [loadShifts]);

  const toggleWeeklyOff = (day: number) => {
    setWeeklyOff((prev) =>
      prev.includes(day) ? prev.filter((d) => d !== day) : [...prev, day].sort((a, b) => a - b),
    );
  };

  const handleCreate = async () => {
    if (!name.trim()) {
      toast.warning('Give the shift a name.');
      return;
    }
    setSaving(true);
    try {
      await hrApi.createShift({
        name: name.trim(),
        start_time: startTime,
        end_time: endTime,
        grace_minutes: graceMinutes,
        weekly_off: weeklyOff,
        store_id: user?.activeStoreId,
      });
      toast.success('Shift created');
      setName('');
      await loadShifts();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed to create shift');
    } finally {
      setSaving(false);
    }
  };

  const handleAssign = async (shiftId: string) => {
    if (!assignEmployeeId.trim()) {
      toast.warning('Enter the employee ID to assign.');
      return;
    }
    setAssigningFor(shiftId);
    try {
      await hrApi.assignShift(assignEmployeeId.trim(), shiftId);
      toast.success('Shift assigned to employee');
      setAssignEmployeeId('');
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed to assign shift');
    } finally {
      setAssigningFor(null);
    }
  };

  return (
    <div className="space-y-6">
      {/* Create shift */}
      <div className="card">
        <h2 className="text-lg font-bold text-gray-900 mb-1 flex items-center gap-2">
          <Plus className="w-5 h-5" /> Define a Shift
        </h2>
        <p className="text-sm text-gray-500 mb-4">
          Late marks are auto-calculated from the shift start + grace window when staff check in.
        </p>

        <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Shift name</label>
            <input
              className="input-field"
              placeholder="e.g. Morning (10-7)"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="grid grid-cols-1 tablet:grid-cols-3 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Start</label>
              <input
                type="time"
                className="input-field"
                value={startTime}
                onChange={(e) => setStartTime(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">End</label>
              <input
                type="time"
                className="input-field"
                value={endTime}
                onChange={(e) => setEndTime(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Grace (min)</label>
              <input
                type="number"
                min={0}
                max={240}
                className="input-field"
                value={graceMinutes}
                onChange={(e) => setGraceMinutes(Math.max(0, Number(e.target.value) || 0))}
              />
            </div>
          </div>
        </div>

        <div className="mt-4">
          <label className="block text-sm font-medium text-gray-700 mb-2 flex items-center gap-2">
            <CalendarOff className="w-4 h-4" /> Weekly off
          </label>
          <div className="flex flex-wrap gap-2">
            {WEEKDAYS.map((label, day) => {
              const active = weeklyOff.includes(day);
              return (
                <button
                  key={label}
                  type="button"
                  onClick={() => toggleWeeklyOff(day)}
                  className={
                    active
                      ? 'px-3 py-1.5 rounded-lg text-sm font-medium bg-bv-red-600 text-white'
                      : 'px-3 py-1.5 rounded-lg text-sm font-medium bg-gray-100 text-gray-700 hover:bg-gray-200'
                  }
                >
                  {label}
                </button>
              );
            })}
          </div>
        </div>

        <div className="mt-4 flex justify-end">
          <button onClick={handleCreate} disabled={saving} className="btn-primary flex items-center gap-2 disabled:opacity-50">
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            Create shift
          </button>
        </div>
      </div>

      {/* Existing shifts */}
      <div className="card overflow-hidden">
        <h2 className="text-lg font-bold text-gray-900 mb-4 flex items-center gap-2">
          <Clock className="w-5 h-5" /> Shifts
        </h2>
        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
          </div>
        ) : shifts.length === 0 ? (
          <div className="text-center py-12 text-gray-500">
            <Clock className="w-12 h-12 mx-auto mb-2 opacity-50" />
            <p>No shifts defined yet.</p>
          </div>
        ) : (
          <div className="space-y-4">
            {/* Shared assign input */}
            <div className="flex flex-wrap items-end gap-3 p-3 bg-gray-50 rounded-lg">
              <div className="flex-1 min-w-[200px]">
                <label className="block text-xs font-medium text-gray-500 mb-1 uppercase">
                  Employee ID to assign
                </label>
                <input
                  className="input-field"
                  placeholder="user-id"
                  value={assignEmployeeId}
                  onChange={(e) => setAssignEmployeeId(e.target.value)}
                />
              </div>
              <p className="text-xs text-gray-500 pb-2">
                Enter an employee ID, then click "Assign" on a shift below.
              </p>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Shift</th>
                    <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Timing</th>
                    <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Grace</th>
                    <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Weekly Off</th>
                    <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">Assign</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {shifts.map((shift) => (
                    <tr key={shift.shift_id} className="hover:bg-gray-50">
                      <td className="px-4 py-3 font-medium text-gray-900">{shift.name}</td>
                      <td className="px-4 py-3 text-center text-sm text-gray-700">
                        {shift.start_time} – {shift.end_time}
                      </td>
                      <td className="px-4 py-3 text-center text-sm text-gray-700">{shift.grace_minutes}m</td>
                      <td className="px-4 py-3 text-center text-sm text-gray-700">
                        {weeklyOffLabel(shift.weekly_off)}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <button
                          onClick={() => handleAssign(shift.shift_id)}
                          disabled={assigningFor === shift.shift_id}
                          className="btn-outline text-sm flex items-center gap-1 ml-auto disabled:opacity-50"
                        >
                          {assigningFor === shift.shift_id ? (
                            <Loader2 className="w-4 h-4 animate-spin" />
                          ) : (
                            <Users className="w-4 h-4" />
                          )}
                          Assign
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default ShiftSetup;
