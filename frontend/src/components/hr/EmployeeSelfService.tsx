// ============================================================================
// Employee Self-Service View (HR Page - Staff Role)
// ============================================================================
// Read-only view showing:
// - My attendance this month (real — /hr/attendance)
// - My latest salary slip (real — /payroll/payslip/{id})
// - My leaves taken this year (real — /hr/leaves)
// - My month-to-date incentive score (real — /incentive/points/mtd)

import { useState, useEffect } from 'react';
import { Calendar, FileText, TrendingUp, Clock, Loader2 } from 'lucide-react';
import { hrApi, incentiveApi } from '../../services/api';
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

export function EmployeeSelfService() {
  const { user } = useAuth();
  const [attendance, setAttendance] = useState<AttendanceSummary | null>(null);
  const [salarySlips, setSalarySlips] = useState<SalarySlip[]>([]);
  const [leavesTaken, setLeavesTaken] = useState<number | null>(null);
  const [incentive, setIncentive] = useState<IncentiveSummary | null>(null);
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
        const checkInDate = new Date(r.checkInTime || now);
        return checkInDate.getMonth() === now.getMonth() && 
               checkInDate.getFullYear() === now.getFullYear() &&
               r.userId === user.id;
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
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">My Dashboard</h1>
        <p className="text-gray-500">Your attendance, salary, and leave information</p>
      </div>

      {/* Attendance This Month */}
      {attendance && (
        <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
          <div className="card bg-gradient-to-br from-green-500/10 to-green-600/5 border-green-500/20">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 bg-green-100 rounded-lg flex items-center justify-center">
                <Clock className="w-5 h-5 text-green-600" />
              </div>
              <div>
                <p className="text-sm text-gray-500">Present</p>
                <p className="text-2xl font-bold text-green-600">{attendance.present}</p>
              </div>
            </div>
          </div>

          <div className="card bg-gradient-to-br from-red-500/10 to-red-600/5 border-red-500/20">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 bg-red-100 rounded-lg flex items-center justify-center">
                <Clock className="w-5 h-5 text-red-600" />
              </div>
              <div>
                <p className="text-sm text-gray-500">Absent</p>
                <p className="text-2xl font-bold text-red-600">{attendance.absent}</p>
              </div>
            </div>
          </div>

          <div className="card bg-gradient-to-br from-blue-500/10 to-blue-600/5 border-blue-500/20">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
                <Calendar className="w-5 h-5 text-blue-600" />
              </div>
              <div>
                <p className="text-sm text-gray-500">Leaves</p>
                <p className="text-2xl font-bold text-blue-600">{attendance.leaves}</p>
              </div>
            </div>
          </div>

          <div className="card bg-gradient-to-br from-yellow-500/10 to-yellow-600/5 border-yellow-500/20">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 bg-yellow-100 rounded-lg flex items-center justify-center">
                <TrendingUp className="w-5 h-5 text-yellow-600" />
              </div>
              <div>
                <p className="text-sm text-gray-500">Half Days</p>
                <p className="text-2xl font-bold text-yellow-600">{attendance.halfDays}</p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Leaves Taken (this year) */}
      <div className="card">
        <h2 className="text-lg font-bold text-gray-900 mb-2">Leaves Taken (This Year)</h2>
        <p className="text-3xl font-bold text-blue-600">{leavesTaken ?? 0}</p>
        <p className="text-xs text-gray-500 mt-1">approved leave days in {new Date().getFullYear()}</p>
      </div>

      {/* Salary Slips */}
      <div className="card">
        <h2 className="text-lg font-bold text-gray-900 mb-4 flex items-center gap-2">
          <FileText className="w-5 h-5" />
          Recent Salary Slips
        </h2>
        <div className="space-y-3">
          {salarySlips.length === 0 && (
            <p className="text-sm text-gray-500">No payslips generated yet.</p>
          )}
          {salarySlips.map(slip => (
            <div key={slip.id} className="bg-white rounded-lg p-4 border border-gray-200 hover:border-bv-red-600 transition-colors">
              <div className="flex items-center justify-between">
                <div>
                  <p className="font-medium text-gray-900">{slip.month}</p>
                  <p className="text-sm text-gray-500 mt-1">
                    Gross: ₹{slip.salary.toLocaleString('en-IN')} | 
                    Deductions: ₹{slip.deductions.toLocaleString('en-IN')}
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-sm text-gray-500">Net Amount</p>
                  <p className="text-xl font-bold text-green-500">₹{slip.netAmount.toLocaleString('en-IN')}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Incentive — month-to-date (real) */}
      <div className="card">
        <h2 className="text-lg font-bold text-gray-900 mb-4 flex items-center gap-2">
          <TrendingUp className="w-5 h-5" />
          Incentive (Month to Date)
        </h2>
        {incentive ? (
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-white rounded-lg p-4 border border-gray-200">
              <p className="text-sm text-gray-500 mb-2">Avg Daily Score</p>
              <p className="text-3xl font-bold text-bv-red-600">{incentive.avgTotal}</p>
            </div>
            <div className="bg-white rounded-lg p-4 border border-gray-200">
              <p className="text-sm text-gray-500 mb-2">Eligibility Avg</p>
              <p className="text-3xl font-bold text-green-600">{incentive.eligibilityAvg}</p>
            </div>
            <div className="bg-white rounded-lg p-4 border border-gray-200">
              <p className="text-sm text-gray-500 mb-2">Days Logged</p>
              <p className="text-3xl font-bold text-blue-600">{incentive.daysLogged}</p>
            </div>
          </div>
        ) : (
          <p className="text-sm text-gray-500">No incentive points logged this month yet.</p>
        )}
      </div>
    </div>
  );
}
