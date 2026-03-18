// ============================================================================
// Monthly Attendance Grid View (HR Page - New Tab)
// ============================================================================
// Rows = Employees, Columns = Days of Month
// Cell values: P/A/L/H/WO with color coding
// Supports current month viewing and navigation

import { useState, useEffect } from 'react';
import { ChevronLeft, ChevronRight, Loader2 } from 'lucide-react';
import { hrApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import clsx from 'clsx';

type AttendanceCode = 'P' | 'A' | 'L' | 'H' | 'WO' | '-';

interface EmployeeAttendance {
  userId: string;
  name: string;
  attendance: Record<number, AttendanceCode>; // day -> code
}

const ATTENDANCE_COLORS: Record<AttendanceCode, { bg: string; text: string; label: string }> = {
  P: { bg: 'bg-green-100', text: 'text-green-700', label: 'Present' },
  A: { bg: 'bg-red-100', text: 'text-red-700', label: 'Absent' },
  L: { bg: 'bg-blue-100', text: 'text-blue-700', label: 'Leave' },
  H: { bg: 'bg-yellow-100', text: 'text-yellow-700', label: 'Half Day' },
  WO: { bg: 'bg-gray-100', text: 'text-gray-700', label: 'Week Off' },
  '-': { bg: 'bg-white', text: 'text-gray-400', label: '-' },
};

export function MonthlyAttendanceGrid() {
  const { user } = useAuth();
  const [currentDate, setCurrentDate] = useState(new Date());
  const [employees, setEmployees] = useState<EmployeeAttendance[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const daysInMonth = new Date(currentDate.getFullYear(), currentDate.getMonth() + 1, 0).getDate();

  useEffect(() => {
    loadMonthlyData();
  }, [currentDate, user?.activeStoreId]);

  const loadMonthlyData = async () => {
    if (!user?.activeStoreId) return;
    setIsLoading(true);
    try {
      const data = await hrApi.getAttendance(user.activeStoreId);
      const records = data?.records || data || [];

      // Group by employee and build attendance map for the month
      const empMap: Record<string, EmployeeAttendance> = {};
      
      for (const record of records) {
        if (!empMap[record.userId]) {
          empMap[record.userId] = {
            userId: record.userId,
            name: record.userName,
            attendance: {},
          };
        }

        // Extract day from record date
        const recordDate = new Date(record.checkInTime || new Date());
        if (recordDate.getMonth() === currentDate.getMonth() && 
            recordDate.getFullYear() === currentDate.getFullYear()) {
          const day = recordDate.getDate();
          let code: AttendanceCode = 'P';
          
          if (record.status === 'ABSENT') code = 'A';
          else if (record.status === 'LEAVE') code = 'L';
          else if (record.status === 'HALF_DAY') code = 'H';
          else if (record.leaveType === 'WEEKLY_OFF') code = 'WO';

          empMap[record.userId].attendance[day] = code;
        }
      }

      setEmployees(Object.values(empMap));
    } catch {
      setEmployees([]);
    } finally {
      setIsLoading(false);
    }
  };

  const monthName = currentDate.toLocaleString('en-IN', { month: 'long', year: 'numeric' });

  const prevMonth = () => {
    setCurrentDate(new Date(currentDate.getFullYear(), currentDate.getMonth() - 1));
  };

  const nextMonth = () => {
    setCurrentDate(new Date(currentDate.getFullYear(), currentDate.getMonth() + 1));
  };

  return (
    <div className="space-y-4">
      {/* Header with Navigation */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-white">{monthName}</h2>
          <p className="text-sm text-gray-400">Monthly Attendance View</p>
        </div>
        <div className="flex gap-2">
          <button onClick={prevMonth} className="btn-outline p-2">
            <ChevronLeft className="w-5 h-5" />
          </button>
          <button onClick={nextMonth} className="btn-outline p-2">
            <ChevronRight className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-3 p-3 bg-gray-900 rounded-lg">
        {Object.entries(ATTENDANCE_COLORS).map(([code, { bg, label }]) => (
          code !== '-' && (
            <div key={code} className="flex items-center gap-2 text-sm">
              <div className={`w-6 h-6 rounded ${bg} flex items-center justify-center`}>
                <span className="text-xs font-bold">{code}</span>
              </div>
              <span className="text-gray-300">{label}</span>
            </div>
          )
        ))}
      </div>

      {/* Grid Table */}
      {isLoading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
        </div>
      ) : employees.length === 0 ? (
        <div className="card text-center py-12 text-gray-400">
          <p>No attendance records for this month</p>
        </div>
      ) : (
        <div className="overflow-x-auto card">
          <table className="w-full text-sm">
            <thead className="bg-gray-900 border-b border-gray-700 sticky top-0">
              <tr>
                <th className="px-3 py-2 text-left text-xs font-bold text-gray-400 min-w-max">Employee</th>
                {Array.from({ length: daysInMonth }, (_, i) => i + 1).map(day => {
                  const date = new Date(currentDate.getFullYear(), currentDate.getMonth(), day);
                  const isWeekend = date.getDay() === 0 || date.getDay() === 6;
                  return (
                    <th 
                      key={day} 
                      className={clsx(
                        'px-1 py-2 text-center text-xs font-bold min-w-10',
                        isWeekend ? 'bg-gray-800 text-gray-500' : 'text-gray-400'
                      )}
                    >
                      <div className="text-xs">{day}</div>
                      <div className="text-xs text-gray-600">
                        {['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'][date.getDay()]}
                      </div>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-700">
              {employees.map(emp => (
                <tr key={emp.userId} className="hover:bg-gray-800/50">
                  <td className="px-3 py-2 font-medium text-white min-w-max">{emp.name}</td>
                  {Array.from({ length: daysInMonth }, (_, i) => i + 1).map(day => {
                    const code = emp.attendance[day] || '-';
                    const color = ATTENDANCE_COLORS[code];
                    const date = new Date(currentDate.getFullYear(), currentDate.getMonth(), day);
                    const isWeekend = date.getDay() === 0 || date.getDay() === 6;
                    
                    return (
                      <td 
                        key={day}
                        className={clsx(
                          'px-1 py-2 text-center',
                          isWeekend ? 'bg-gray-900/50' : ''
                        )}
                      >
                        <button 
                          title={color.label}
                          className={clsx(
                            'w-8 h-8 rounded font-bold text-xs transition-all',
                            color.bg, color.text,
                            'hover:shadow-md hover:scale-105'
                          )}
                        >
                          {code}
                        </button>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Summary Stats */}
      {employees.length > 0 && (
        <div className="grid grid-cols-5 gap-3">
          {['P', 'A', 'L', 'H', 'WO'].map(code => {
            const count = employees.reduce((sum, emp) => 
              sum + Object.values(emp.attendance).filter(c => c === code).length, 0
            );
            const color = ATTENDANCE_COLORS[code as AttendanceCode];
            return (
              <div key={code} className={`card ${color.bg}`}>
                <p className="text-xs text-gray-600">{ATTENDANCE_COLORS[code as AttendanceCode].label}</p>
                <p className={`text-2xl font-bold ${color.text}`}>{count}</p>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
