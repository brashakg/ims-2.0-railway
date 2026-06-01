// ============================================================================
// Attendance Summary Card (HR page)
// ============================================================================
// Compact month rollup (present / absent / late + leave) with a link to the
// full Attendance page. Replaces the full monthly grid that used to live in an
// HR tab — the grid now has its own top-level destination.
//
// Data: GET /hr/attendance/summary. Fail-soft — if that endpoint isn't
// available it derives the same totals from the existing grid endpoint, so the
// card never blanks out during the backend rollout.

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Calendar, ChevronRight, Loader2 } from 'lucide-react';
import { hrApi } from '../../services/api';
import type { AttendanceMonthSummary } from '../../services/api/hr';
import { useAuth } from '../../context/AuthContext';

function currentMonthValue(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

function monthLabel(value: string): string {
  const [y, m] = value.split('-').map(Number);
  return new Date(y, m - 1, 1).toLocaleString('en-IN', { month: 'long', year: 'numeric' });
}

export function AttendanceSummaryCard() {
  const { user } = useAuth();
  const [summary, setSummary] = useState<AttendanceMonthSummary | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const month = currentMonthValue();

  useEffect(() => {
    let cancelled = false;
    const storeId = user?.activeStoreId;
    if (!storeId) return;
    setIsLoading(true);
    (async () => {
      try {
        // Preferred: dedicated summary endpoint.
        const s = await hrApi.getAttendanceSummary({ month, storeId });
        if (!cancelled) setSummary(s);
      } catch {
        // Fail-soft: roll up the grid totals so the card still shows numbers
        // while the /summary endpoint is being rolled out.
        try {
          const grid = await hrApi.getAttendanceGrid({ month, storeId });
          if (!cancelled) {
            setSummary({
              month,
              present: grid.totals.present,
              absent: grid.totals.absent,
              leave: grid.totals.leave,
              half_day: grid.totals.half_day,
              lwp: grid.totals.lwp,
              week_off: grid.totals.week_off,
              late: grid.totals.late,
              employee_count: grid.employees.length,
            });
          }
        } catch {
          if (!cancelled) setSummary(null);
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [user?.activeStoreId, month]);

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Calendar className="w-5 h-5 text-bv-red-600" />
          <h2 className="text-base font-bold text-gray-900">Attendance — {monthLabel(month)}</h2>
        </div>
        <Link
          to="/attendance"
          className="text-sm font-medium text-bv-red-600 hover:text-bv-red-700 inline-flex items-center gap-1"
        >
          View attendance <ChevronRight className="w-4 h-4" />
        </Link>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-6">
          <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
        </div>
      ) : !summary ? (
        <p className="text-sm text-gray-500 py-4">
          No attendance recorded yet this month.{' '}
          <Link to="/attendance" className="text-bv-red-600 hover:underline">Open the attendance page</Link>.
        </p>
      ) : (
        <div className="grid grid-cols-2 tablet:grid-cols-4 gap-3">
          <SummaryStat label="Present" value={summary.present} className="text-green-600" />
          <SummaryStat label="Absent" value={summary.absent} className="text-red-600" />
          <SummaryStat label="Late marks" value={summary.late} className="text-orange-600" />
          <SummaryStat
            label="Leave / Half-day"
            value={summary.leave + summary.half_day}
            className="text-blue-600"
          />
        </div>
      )}
    </div>
  );
}

function SummaryStat({ label, value, className }: { label: string; value: number; className: string }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white px-3 py-2">
      <p className="text-xs text-gray-500">{label}</p>
      <p className={`text-2xl font-bold ${className}`}>{value}</p>
    </div>
  );
}

export default AttendanceSummaryCard;
