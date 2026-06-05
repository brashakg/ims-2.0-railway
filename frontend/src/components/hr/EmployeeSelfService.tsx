// ============================================================================
// Employee Self-Service View (HR Page - Staff Role)
// ============================================================================
// Read-only view showing:
// - My attendance this month (real -- /hr/attendance)
// - My latest salary slip (real -- /payroll/payslip/{id})
// - My leaves taken this year (real -- /hr/leaves)
// - My month-to-date incentive score (real -- /incentive/points/mtd)
// - My commission this month (real -- /payroll/commission/summary)
//
// HR-4: Mobile-first layout -- each section stacks vertically on small screens,
// stat cards use a 2-col grid on phones and 4-col on tablet+.

import { useState, useEffect } from 'react';
import { Calendar, FileText, TrendingUp, Clock, Loader2, Award } from 'lucide-react';
import { hrApi, incentiveApi } from '../../services/api';
import { payrollApi } from '../../services/api/payroll';
import { useAuth } from '../../context/AuthContext';

interface AttendanceSummary {
  present: number;
  absent: number;
  leaves: number;
  halfDays: number;
}

interface SalarySlip {
  id: string;
  month: string;
  salary: number;       // gross
  deductions: number;
  netAmount: number;    // net pay
}

interface IncentiveSummary {
  daysLogged: number;
  avgTotal: number;
  eligibilityAvg: number;
}

interface CommissionMtd {
  sales_count: number;
  revenue: number;
  commission_rate_percent: number;
  commission_amount: number;
  rank: number;
}

export function EmployeeSelfService() {
  const { user } = useAuth();
  const [attendance, setAttendance] = useState<AttendanceSummary | null>(null);
  const [salarySlips, setSalarySlips] = useState<SalarySlip[]>([]);
  const [leavesTaken, setLeavesTaken] = useState<number | null>(null);
  const [incentive, setIncentive] = useState<IncentiveSummary | null>(null);
  const [commission, setCommission] = useState<CommissionMtd | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    loadEmployeeData();
  }, [user?.id]);

  const loadEmployeeData = async () => {
    if (!user?.id) return;
    setIsLoading(true);
    try {
      // Load attendance for current month
      const attendanceData = await hrApi.getAttendance(user.activeStoreId).catch(() => ({}));
      const records = attendanceData?.records || [];

      const now = new Date();
      const monthRecords = records.filter((r: any) => {
        // getAttendance maps backend keys; fall back to the record date when
        // there is no check-in time (e.g. LEAVE / ABSENT rows).
        const refDate = new Date(r.checkInTime || r.checkIn || r.date || now);
        return refDate.getMonth() === now.getMonth() &&
               refDate.getFullYear() === now.getFullYear() &&
               (r.userId || r.employeeId) === user.id;
      });

      const attSummary: AttendanceSummary = {
        present: monthRecords.filter((r: any) => r.status === 'PRESENT').length,
        absent: monthRecords.filter((r: any) => r.status === 'ABSENT').length,
        leaves: monthRecords.filter((r: any) => r.status === 'LEAVE').length,
        halfDays: monthRecords.filter((r: any) => r.status === 'HALF_DAY').length,
      };
      setAttendance(attSummary);

      // Real: latest payslip
      const payslipRes = await hrApi.getLatestPayslip(user.id).catch(() => ({ payslip: null }));
      const p = payslipRes?.payslip;
      if (p) {
        const bd = p.breakdown || {};
        const monthLabel = new Date(p.year, (p.month || 1) - 1, 1).toLocaleString('en-IN', {
          month: 'long',
          year: 'numeric',
        });
        setSalarySlips([
          {
            id: p.payslip_id || 'latest',
            month: monthLabel,
            salary: Number(bd.gross_salary) || 0,
            deductions: Number(bd.total_deductions) || 0,
            netAmount: Number(bd.net_pay) || 0,
          },
        ]);
      } else {
        setSalarySlips([]);
      }

      // Real: approved leave days taken in the current calendar year
      const leavesRes = await hrApi.getLeaves({ userId: user.id }).catch(() => ({ leaves: [] }));
      const leaveList: any[] = Array.isArray(leavesRes) ? leavesRes : leavesRes?.leaves || [];
      const yearNow = new Date().getFullYear();
      const daysTaken = leaveList
        .filter((l) => l.status === 'APPROVED' && new Date(l.startDate).getFullYear() === yearNow)
        .reduce((sum, l) => {
          const start = new Date(l.startDate);
          const end = new Date(l.endDate || l.startDate);
          const d = Math.floor((end.getTime() - start.getTime()) / 86400000) + 1;
          return sum + (d > 0 ? d : 1);
        }, 0);
      setLeavesTaken(daysTaken);

      // Real: this employee's month-to-date incentive score
      const now2 = new Date();
      const mtd = await incentiveApi
        .getMtd(now2.getFullYear(), now2.getMonth() + 1, user.activeStoreId)
        .catch(() => null);
      const myEntry = mtd?.items?.find((it: any) => it.staff_id === user.id);
      setIncentive(
        myEntry
          ? {
              daysLogged: myEntry.days_logged || 0,
              avgTotal: Math.round((myEntry.avg?.total || 0) * 10) / 10,
              eligibilityAvg: Math.round((myEntry.eligibility_avg || 0) * 10) / 10,
            }
          : null
      );

      // Real: this month's commission (HR-3)
      const commRes = await payrollApi
        .getCommissionSummary({
          month: now2.getMonth() + 1,
          year: now2.getFullYear(),
          store_id: user.activeStoreId,
          employee_id: user.id,
        })
        .catch(() => null);
      const myCommission = commRes?.items?.[0];
      if (myCommission) {
        setCommission({
          sales_count: myCommission.sales_count,
          revenue: myCommission.revenue,
          commission_rate_percent: myCommission.commission_rate_percent,
          commission_amount: myCommission.commission_amount,
          rank: myCommission.rank,
        });
      } else {
        setCommission(null);
      }
    } catch {
      // Handle errors silently
    } finally {
      setIsLoading(false);
    }
  };

  if (isLoading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
      </div>
    );
  }

  return (
    // HR-4: max-w-lg keeps the layout phone-sized even on a tablet/desktop so
    // staff using this on their personal phone see a comfortable single-column
    // view; managers on desktop see the same compact dashboard.
    <div className="space-y-6 max-w-lg mx-auto w-full">
      {/* Header */}
      <div>
        <h1 className="text-xl font-bold text-gray-900">My Dashboard</h1>
        <p className="text-sm text-gray-500">Attendance, salary, leaves and commission</p>
      </div>

      {/* Attendance This Month — 2 cols on phone, 4 on tablet */}
      {attendance && (
        <div className="grid grid-cols-2 tablet:grid-cols-4 gap-3">
          <StatCard
            icon={<Clock className="w-4 h-4 text-green-600" />}
            bg="bg-green-50"
            label="Present"
            value={attendance.present}
            color="text-green-700"
          />
          <StatCard
            icon={<Clock className="w-4 h-4 text-red-600" />}
            bg="bg-red-50"
            label="Absent"
            value={attendance.absent}
            color="text-red-700"
          />
          <StatCard
            icon={<Calendar className="w-4 h-4 text-blue-600" />}
            bg="bg-blue-50"
            label="Leaves"
            value={attendance.leaves}
            color="text-blue-700"
          />
          <StatCard
            icon={<Clock className="w-4 h-4 text-yellow-600" />}
            bg="bg-yellow-50"
            label="Half Days"
            value={attendance.halfDays}
            color="text-yellow-700"
          />
        </div>
      )}

      {/* Leaves Taken (this year) */}
      <div className="card p-4">
        <h2 className="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-1">
          Leaves Taken (This Year)
        </h2>
        <p className="text-3xl font-bold text-blue-600">{leavesTaken ?? 0}</p>
        <p className="text-xs text-gray-400 mt-1">
          Approved leave days in {new Date().getFullYear()}
        </p>
      </div>

      {/* Commission (HR-3) */}
      <div className="card p-4">
        <h2 className="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-3 flex items-center gap-2">
          <Award className="w-4 h-4" />
          Commission (This Month)
        </h2>
        {commission && commission.commission_rate_percent > 0 ? (
          <div className="grid grid-cols-2 gap-3">
            <div className="bg-bv-red-50 rounded-lg p-3 text-center">
              <p className="text-xs text-gray-500 mb-1">Sales</p>
              <p className="text-2xl font-bold text-gray-900">{commission.sales_count}</p>
            </div>
            <div className="bg-bv-red-50 rounded-lg p-3 text-center">
              <p className="text-xs text-gray-500 mb-1">Revenue</p>
              <p className="text-2xl font-bold text-gray-900">
                {commission.revenue.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
              </p>
            </div>
            <div className="bg-green-50 rounded-lg p-3 text-center col-span-2">
              <p className="text-xs text-gray-500 mb-1">
                Commission @ {commission.commission_rate_percent}%
              </p>
              <p className="text-3xl font-bold text-green-700">
                Rs {commission.commission_amount.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
              </p>
            </div>
            {commission.rank > 0 && (
              <div className="col-span-2 text-center text-xs text-gray-400">
                Store rank: #{commission.rank}
              </div>
            )}
          </div>
        ) : commission ? (
          <div className="space-y-2">
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Sales closed</span>
              <span className="font-medium text-gray-900">{commission.sales_count}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Revenue</span>
              <span className="font-medium text-gray-900">
                Rs {commission.revenue.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
              </span>
            </div>
            <p className="text-xs text-gray-400 pt-1">
              Commission rate not configured for your role.
            </p>
          </div>
        ) : (
          <p className="text-sm text-gray-400">No sales recorded this month yet.</p>
        )}
      </div>

      {/* Incentive -- month-to-date */}
      <div className="card p-4">
        <h2 className="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-3 flex items-center gap-2">
          <TrendingUp className="w-4 h-4" />
          Incentive (Month to Date)
        </h2>
        {incentive ? (
          <div className="grid grid-cols-3 gap-3">
            <div className="text-center">
              <p className="text-xs text-gray-500 mb-1">Avg Score</p>
              <p className="text-2xl font-bold text-bv-red-600">{incentive.avgTotal}</p>
            </div>
            <div className="text-center">
              <p className="text-xs text-gray-500 mb-1">Eligibility</p>
              <p className="text-2xl font-bold text-green-600">{incentive.eligibilityAvg}</p>
            </div>
            <div className="text-center">
              <p className="text-xs text-gray-500 mb-1">Days</p>
              <p className="text-2xl font-bold text-blue-600">{incentive.daysLogged}</p>
            </div>
          </div>
        ) : (
          <p className="text-sm text-gray-400">No incentive points logged this month yet.</p>
        )}
      </div>

      {/* Salary Slips */}
      <div className="card p-4">
        <h2 className="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-3 flex items-center gap-2">
          <FileText className="w-4 h-4" />
          Recent Salary Slips
        </h2>
        {salarySlips.length === 0 ? (
          <p className="text-sm text-gray-400">No payslips generated yet.</p>
        ) : (
          <div className="space-y-3">
            {salarySlips.map(slip => (
              <div
                key={slip.id}
                className="rounded-lg p-3 border border-gray-200 hover:border-bv-red-300 transition-colors"
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="font-medium text-gray-900 text-sm">{slip.month}</p>
                    <p className="text-xs text-gray-500 mt-0.5">
                      Gross Rs {slip.salary.toLocaleString('en-IN')} | Ded Rs {slip.deductions.toLocaleString('en-IN')}
                    </p>
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-xs text-gray-400">Net</p>
                    <p className="text-lg font-bold text-green-600">
                      Rs {slip.netAmount.toLocaleString('en-IN')}
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helper component
// ---------------------------------------------------------------------------

function StatCard({
  icon,
  bg,
  label,
  value,
  color,
}: {
  icon: React.ReactNode;
  bg: string;
  label: string;
  value: number;
  color: string;
}) {
  return (
    <div className="card p-3">
      <div className="flex items-center gap-2 mb-1">
        <div className={`w-7 h-7 ${bg} rounded-md flex items-center justify-center`}>
          {icon}
        </div>
        <p className="text-xs text-gray-500">{label}</p>
      </div>
      <p className={`text-2xl font-bold ${color}`}>{value}</p>
    </div>
  );
}
