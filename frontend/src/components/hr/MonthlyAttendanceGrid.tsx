// ============================================================================
// Monthly Attendance Grid View (HR Page - "Monthly View" tab)
// ============================================================================
// Rows = Employees, Columns = days 1..N of the selected month.
// Cell values: P / A / L / HD / LWP / WO color-coded status codes, plus a
// right-hand summary column (P/A/L counts) and a totals row.
// Server-authoritative: the grid (day math + roster join + per-day codes) is
// computed by GET /hr/attendance/grid. Read-only. Light theme only.

import { useState, useEffect } from 'react';
import { ChevronLeft, ChevronRight, Loader2, AlertTriangle } from 'lucide-react';
import { hrApi } from '../../services/api';
import type { AttendanceCode, AttendanceGrid, LwpReport } from '../../services/api/hr';
import { useAuth } from '../../context/AuthContext';
import clsx from 'clsx';

const ATTENDANCE_COLORS: Record<AttendanceCode, { bg: string; text: string; label: string }> = {
  P: { bg: 'bg-green-100', text: 'text-green-700', label: 'Present' },
  A: { bg: 'bg-red-100', text: 'text-red-700', label: 'Absent' },
  L: { bg: 'bg-amber-100', text: 'text-amber-700', label: 'Leave' },
  HD: { bg: 'bg-amber-100', text: 'text-amber-700', label: 'Half Day' },
  LWP: { bg: 'bg-orange-100', text: 'text-orange-700', label: 'LWP' },
  WO: { bg: 'bg-gray-100', text: 'text-gray-600', label: 'Week Off' },
  '-': { bg: 'bg-white', text: 'text-gray-400', label: 'No record' },
};

// Order shown in the legend + summary blocks.
const LEGEND_CODES: AttendanceCode[] = ['P', 'A', 'L', 'HD', 'LWP', 'WO'];

// 'YYYY-MM' for the current month.
function currentMonthValue(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

function shiftMonth(value: string, delta: number): string {
  const [y, m] = value.split('-').map(Number);
  const d = new Date(y, (m - 1) + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

function monthLabel(value: string): string {
  const [y, m] = value.split('-').map(Number);
  return new Date(y, m - 1, 1).toLocaleString('en-IN', { month: 'long', year: 'numeric' });
}

// Day-of-week initial for a given day number in the selected month.
function weekdayInitial(month: string, day: number): { initial: string; isWeekend: boolean } {
  const [y, m] = month.split('-').map(Number);
  const date = new Date(y, m - 1, day);
  const dow = date.getDay();
  return { initial: ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'][dow], isWeekend: dow === 0 || dow === 6 };
}

export function MonthlyAttendanceGrid() {
  const { user } = useAuth();
  const [month, setMonth] = useState<string>(currentMonthValue());
  const [grid, setGrid] = useState<AttendanceGrid | null>(null);
  const [lwp, setLwp] = useState<LwpReport | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      if (!user?.activeStoreId) return;
      setIsLoading(true);
      const [y, m] = month.split('-').map(Number);
      try {
        const [gridData, lwpData] = await Promise.all([
          hrApi.getAttendanceGrid({ month, storeId: user.activeStoreId }),
          hrApi.getLwpReport({ year: y, month: m, storeId: user.activeStoreId }).catch(() => null),
        ]);
        if (!cancelled) {
          setGrid(gridData);
          setLwp(lwpData);
        }
      } catch {
        if (!cancelled) { setGrid(null); setLwp(null); }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, [month, user?.activeStoreId]);

  const days = grid?.days ?? [];
  const employees = grid?.employees ?? [];
  const totals = grid?.totals;
  const lwpRows = (lwp?.employees ?? []).filter((r) => r.lwp_days > 0);

  return (
    <div className="space-y-4">
      {/* Header with month picker + navigation */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold text-gray-900">{monthLabel(month)}</h2>
          <p className="text-sm text-gray-500">Monthly attendance — per-employee, per-day</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setMonth(shiftMonth(month, -1))} className="btn-outline p-2" title="Previous month">
            <ChevronLeft className="w-5 h-5" />
          </button>
          <input
            type="month"
            value={month}
            onChange={(e) => setMonth(e.target.value || currentMonthValue())}
            className="input-field text-sm"
          />
          <button onClick={() => setMonth(shiftMonth(month, 1))} className="btn-outline p-2" title="Next month">
            <ChevronRight className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-3 p-3 bg-gray-50 rounded-lg">
        {LEGEND_CODES.map(code => {
          const { bg, text, label } = ATTENDANCE_COLORS[code];
          return (
            <div key={code} className="flex items-center gap-2 text-sm">
              <div className={clsx('w-6 h-6 rounded flex items-center justify-center font-bold text-xs', bg, text)}>
                {code}
              </div>
              <span className="text-gray-600">{label}</span>
            </div>
          );
        })}
      </div>

      {/* Grid */}
      {isLoading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
        </div>
      ) : employees.length === 0 ? (
        <div className="card text-center py-12 text-gray-500">
          <p>No employees / attendance for this month</p>
        </div>
      ) : (
        <div className="overflow-x-auto card">
          <table className="w-full text-sm border-collapse">
            <thead className="bg-gray-50 border-b border-gray-200 sticky top-0">
              <tr>
                <th className="px-3 py-2 text-left text-xs font-bold text-gray-500 sticky left-0 bg-gray-50 z-10 whitespace-nowrap">
                  Employee
                </th>
                {days.map(day => {
                  const { initial, isWeekend } = weekdayInitial(month, day);
                  return (
                    <th
                      key={day}
                      className={clsx(
                        'px-1 py-2 text-center text-xs font-bold w-10',
                        isWeekend ? 'bg-gray-100 text-gray-500' : 'text-gray-500',
                      )}
                    >
                      <div>{day}</div>
                      <div className="text-[10px] font-normal text-gray-400">{initial}</div>
                    </th>
                  );
                })}
                {/* Summary columns */}
                <th className="px-2 py-2 text-center text-xs font-bold text-green-700 border-l border-gray-200">P</th>
                <th className="px-2 py-2 text-center text-xs font-bold text-red-700">A</th>
                <th className="px-2 py-2 text-center text-xs font-bold text-amber-700">L</th>
                <th className="px-2 py-2 text-center text-xs font-bold text-orange-700" title="Late marks">Late</th>
                <th className="px-2 py-2 text-center text-xs font-bold text-orange-700" title="Leave Without Pay (report only)">LWP</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {employees.map(emp => (
                <tr key={emp.employee_id} className="hover:bg-gray-50">
                  <td className="px-3 py-2 font-medium text-gray-900 sticky left-0 bg-white z-10 whitespace-nowrap">
                    {emp.name}
                  </td>
                  {days.map(day => {
                    const code = (emp.days[String(day)] || '-') as AttendanceCode;
                    const color = ATTENDANCE_COLORS[code] || ATTENDANCE_COLORS['-'];
                    const { isWeekend } = weekdayInitial(month, day);
                    return (
                      <td key={day} className={clsx('px-1 py-1.5 text-center', isWeekend ? 'bg-gray-50' : '')}>
                        <span
                          title={color.label}
                          className={clsx(
                            'inline-flex w-7 h-7 items-center justify-center rounded font-bold text-xs',
                            color.bg, color.text,
                          )}
                        >
                          {code === '-' ? '' : code}
                        </span>
                      </td>
                    );
                  })}
                  {/* Per-employee summary */}
                  <td className="px-2 py-2 text-center font-semibold text-green-700 border-l border-gray-200">
                    {emp.summary.present}
                  </td>
                  <td className="px-2 py-2 text-center font-semibold text-red-700">{emp.summary.absent}</td>
                  <td className="px-2 py-2 text-center font-semibold text-amber-700">
                    {emp.summary.leave + emp.summary.half_day + emp.summary.lwp}
                  </td>
                  <td className="px-2 py-2 text-center font-semibold text-orange-700">{emp.summary.late}</td>
                  <td className="px-2 py-2 text-center font-semibold text-orange-700">{emp.summary.lwp}</td>
                </tr>
              ))}
            </tbody>
            {totals && (
              <tfoot>
                <tr className="border-t-2 border-gray-300 bg-gray-50 font-bold">
                  <td className="px-3 py-2 text-gray-900 sticky left-0 bg-gray-50 z-10 whitespace-nowrap">Totals</td>
                  <td colSpan={days.length} className="px-3 py-2 text-xs text-gray-500">
                    Present {totals.present} &middot; Absent {totals.absent} &middot; Leave {totals.leave}
                    {' '}&middot; Half-day {totals.half_day} &middot; LWP {totals.lwp}
                    {' '}&middot; Week-off {totals.week_off} &middot; Late {totals.late}
                  </td>
                  <td className="px-2 py-2 text-center text-green-700 border-l border-gray-200">{totals.present}</td>
                  <td className="px-2 py-2 text-center text-red-700">{totals.absent}</td>
                  <td className="px-2 py-2 text-center text-amber-700">
                    {totals.leave + totals.half_day + totals.lwp}
                  </td>
                  <td className="px-2 py-2 text-center text-orange-700">{totals.late}</td>
                  <td className="px-2 py-2 text-center text-orange-700">{totals.lwp}</td>
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      )}

      {/* LWP breakdown (report only — accountant enters these manually into payroll) */}
      {!isLoading && lwp && (
        <div className="card">
          <div className="flex items-center justify-between mb-1">
            <h3 className="text-base font-bold text-gray-900">Leave Without Pay (LWP)</h3>
            <span className="text-sm font-semibold text-orange-700">
              {lwp.total_lwp_days} day{lwp.total_lwp_days === 1 ? '' : 's'} total
            </span>
          </div>
          <p className="text-xs text-gray-500 mb-3 flex items-center gap-1">
            <AlertTriangle className="w-3.5 h-3.5 text-amber-500" />
            Report only — enter LWP days manually into the payroll run. Not auto-applied.
          </p>
          {lwpRows.length === 0 ? (
            <p className="text-sm text-gray-500">No LWP days this month.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Employee</th>
                    <th className="px-3 py-2 text-center text-xs font-medium text-gray-500 uppercase">LWP Days</th>
                    <th className="px-3 py-2 text-center text-xs font-medium text-gray-500 uppercase">Absent</th>
                    <th className="px-3 py-2 text-center text-xs font-medium text-gray-500 uppercase">Marked LWP</th>
                    <th className="px-3 py-2 text-center text-xs font-medium text-gray-500 uppercase">Half-days</th>
                    <th className="px-3 py-2 text-center text-xs font-medium text-gray-500 uppercase">Unpaid Leave</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {lwpRows.map((row) => (
                    <tr key={row.employee_id} className="hover:bg-gray-50">
                      <td className="px-3 py-2 font-medium text-gray-900">{row.name}</td>
                      <td className="px-3 py-2 text-center font-bold text-orange-700">{row.lwp_days}</td>
                      <td className="px-3 py-2 text-center text-gray-700">{row.absent_days}</td>
                      <td className="px-3 py-2 text-center text-gray-700">{row.marked_lwp_days}</td>
                      <td className="px-3 py-2 text-center text-gray-700">{row.half_days}</td>
                      <td className="px-3 py-2 text-center text-gray-700">{row.unpaid_leave_days}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
