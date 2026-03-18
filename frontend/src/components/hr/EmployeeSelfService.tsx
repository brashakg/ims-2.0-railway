// ============================================================================
// Employee Self-Service View (HR Page - Staff Role)
// ============================================================================
// Read-only view showing:
// - My attendance this month
// - My salary slips
// - My incentive progress
// - My leaves balance

import { useState, useEffect } from 'react';
import { Calendar, FileText, TrendingUp, Clock, Loader2 } from 'lucide-react';
import { hrApi } from '../../services/api';
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
  salary: number;
  deductions: number;
  netAmount: number;
}

interface LeaveBalance {
  casual: number;
  sick: number;
  annual: number;
}

export function EmployeeSelfService() {
  const { user } = useAuth();
  const [attendance, setAttendance] = useState<AttendanceSummary | null>(null);
  const [salarySlips, setSalarySlips] = useState<SalarySlip[]>([]);
  const [leaves, setLeaves] = useState<LeaveBalance | null>(null);
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

      // Mock salary slips (in production, fetch from API)
      setSalarySlips([
        { id: '1', month: 'March 2026', salary: 25000, deductions: 2500, netAmount: 22500 },
        { id: '2', month: 'February 2026', salary: 25000, deductions: 2500, netAmount: 22500 },
        { id: '3', month: 'January 2026', salary: 25000, deductions: 2500, netAmount: 22500 },
      ]);

      // Mock leave balance (in production, fetch from API)
      setLeaves({ casual: 3, sick: 5, annual: 8 });
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
        <h1 className="text-2xl font-bold text-white">My Dashboard</h1>
        <p className="text-gray-400">Your attendance, salary, and leave information</p>
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
                <p className="text-sm text-gray-400">Present</p>
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
                <p className="text-sm text-gray-400">Absent</p>
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
                <p className="text-sm text-gray-400">Leaves</p>
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
                <p className="text-sm text-gray-400">Half Days</p>
                <p className="text-2xl font-bold text-yellow-600">{attendance.halfDays}</p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Leave Balance */}
      {leaves && (
        <div className="card">
          <h2 className="text-lg font-bold text-white mb-4">Leave Balance</h2>
          <div className="grid grid-cols-3 gap-4">
            {[
              { type: 'Casual Leave', balance: leaves.casual, color: 'text-blue-500' },
              { type: 'Sick Leave', balance: leaves.sick, color: 'text-red-500' },
              { type: 'Annual Leave', balance: leaves.annual, color: 'text-green-500' },
            ].map(leave => (
              <div key={leave.type} className="bg-gray-800 rounded-lg p-4 border border-gray-700">
                <p className="text-sm text-gray-400 mb-2">{leave.type}</p>
                <p className={`text-3xl font-bold ${leave.color}`}>{leave.balance}</p>
                <p className="text-xs text-gray-500 mt-2">days available</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Salary Slips */}
      <div className="card">
        <h2 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
          <FileText className="w-5 h-5" />
          Recent Salary Slips
        </h2>
        <div className="space-y-3">
          {salarySlips.map(slip => (
            <div key={slip.id} className="bg-gray-800 rounded-lg p-4 border border-gray-700 hover:border-bv-gold-500 transition-colors">
              <div className="flex items-center justify-between">
                <div>
                  <p className="font-medium text-white">{slip.month}</p>
                  <p className="text-sm text-gray-400 mt-1">
                    Gross: ₹{slip.salary.toLocaleString('en-IN')} | 
                    Deductions: ₹{slip.deductions.toLocaleString('en-IN')}
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-sm text-gray-400">Net Amount</p>
                  <p className="text-xl font-bold text-green-500">₹{slip.netAmount.toLocaleString('en-IN')}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Incentive Progress (Mock) */}
      <div className="card">
        <h2 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
          <TrendingUp className="w-5 h-5" />
          Incentive Progress
        </h2>
        <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
          <div className="flex items-center justify-between mb-3">
            <p className="text-gray-300">March 2026 Target</p>
            <p className="text-sm text-gray-400">₹5,000 of ₹10,000</p>
          </div>
          <div className="w-full bg-gray-700 rounded-full h-2">
            <div className="bg-bv-gold-500 h-2 rounded-full" style={{ width: '50%' }}></div>
          </div>
          <p className="text-xs text-gray-400 mt-2">50% of target achieved</p>
        </div>
      </div>
    </div>
  );
}
