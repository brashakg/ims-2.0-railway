// /hr/payroll -- Salary Sheet (the index). Moved byte-identical from the old
// PayrollDashboard 'sheet' tab; its state and handlers live in PayrollLayout
// and arrive through <Outlet context>.

import { Calendar, Loader2, DownloadCloud } from 'lucide-react';
import { PayrollAccessNotice } from '../../../components/hr/PayrollAccessNotice';
import { MONTHS, usePayrollContext } from './payrollShared';

export function SalarySheetSection() {
  const {
    currentYear, selectedMonth, setSelectedMonth, selectedYear, setSelectedYear,
    salarySheet, isLoading, noAccess, exportSalarySheet,
  } = usePayrollContext();

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="flex items-center justify-between bg-white border border-gray-200 p-4 rounded-lg">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <Calendar className="w-4 h-4 text-gray-500" />
            <select
              value={selectedMonth}
              onChange={(e) => setSelectedMonth(Number(e.target.value))}
              title="Select payroll month"
              className="bg-white border border-gray-300 text-gray-900 px-3 py-1 rounded text-sm"
            >
              {MONTHS.map((m, i) => (
                <option key={i} value={i + 1}>
                  {m}
                </option>
              ))}
            </select>
            <select
              value={selectedYear}
              onChange={(e) => setSelectedYear(Number(e.target.value))}
              title="Select payroll year"
              className="bg-white border border-gray-300 text-gray-900 px-3 py-1 rounded text-sm"
            >
              {[currentYear - 1, currentYear, currentYear + 1].map((year) => (
                <option key={year} value={year}>
                  {year}
                </option>
              ))}
            </select>
          </div>
        </div>
        <button
          onClick={exportSalarySheet}
          className="btn-outline text-sm flex items-center gap-2"
        >
          <DownloadCloud className="w-4 h-4" />
          Export
        </button>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="flex justify-center items-center py-8">
          <Loader2 className="w-8 h-8 text-blue-600 animate-spin" />
        </div>
      ) : noAccess ? (
        <PayrollAccessNotice message={noAccess} what="the salary sheet" />
      ) : salarySheet.length === 0 ? (
        <div className="text-center py-8 text-gray-500">
          No salary data for {MONTHS[selectedMonth - 1]} {selectedYear}
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="text-left px-4 py-2 text-gray-500 font-medium">Employee</th>
                <th className="text-right px-4 py-2 text-gray-500 font-medium">Basic</th>
                <th className="text-right px-4 py-2 text-gray-500 font-medium">HRA</th>
                <th className="text-right px-4 py-2 text-gray-500 font-medium">Allow.</th>
                <th className="text-right px-4 py-2 text-gray-500 font-medium">Gross</th>
                <th className="text-right px-4 py-2 text-gray-500 font-medium">PF</th>
                <th className="text-right px-4 py-2 text-gray-500 font-medium">ESI</th>
                <th className="text-right px-4 py-2 text-gray-500 font-medium">PT</th>
                <th className="text-right px-4 py-2 text-gray-500 font-medium">TDS</th>
                <th className="text-right px-4 py-2 text-gray-500 font-medium">LWP</th>
                <th className="text-right px-4 py-2 text-gray-500 font-medium">Advance</th>
                <th className="text-right px-4 py-2 text-gray-500 font-medium">Net Pay</th>
              </tr>
            </thead>
            <tbody>
              {salarySheet.map((salary: any) => {
                // GET /payroll/salary-sheet returns FLAT rows (no nested
                // `breakdown`): basic / hra / allowances / gross_salary /
                // pf_employee / esi / professional_tax / tds / lwp_deduction
                // / advance_deduction / net_pay. Read them directly with
                // ?? 0 guards (the old salary.breakdown.* threw at runtime).
                return (
                  <tr
                    key={salary.salary_record_id}
                    className="border-b border-gray-200 hover:bg-gray-50 transition"
                  >
                    <td className="px-4 py-2 text-gray-900 font-medium">
                      {salary.employee_name}
                    </td>
                    <td className="text-right px-4 py-2 text-gray-600">₹{(salary.basic ?? 0).toLocaleString()}</td>
                    <td className="text-right px-4 py-2 text-gray-600">₹{(salary.hra ?? 0).toLocaleString()}</td>
                    <td className="text-right px-4 py-2 text-gray-600">₹{(salary.allowances ?? 0).toLocaleString()}</td>
                    <td className="text-right px-4 py-2 text-green-600 font-medium">
                      ₹{(salary.gross_salary ?? 0).toLocaleString()}
                    </td>
                    <td className="text-right px-4 py-2 text-gray-600">₹{(salary.pf_employee ?? 0).toLocaleString()}</td>
                    <td className="text-right px-4 py-2 text-gray-600">₹{(salary.esi ?? 0).toLocaleString()}</td>
                    <td className="text-right px-4 py-2 text-gray-600">₹{(salary.professional_tax ?? 0).toLocaleString()}</td>
                    <td className="text-right px-4 py-2 text-gray-600">₹{(salary.tds ?? 0).toLocaleString()}</td>
                    <td className="text-right px-4 py-2 text-gray-600">₹{(salary.lwp_deduction ?? 0).toLocaleString()}</td>
                    <td className="text-right px-4 py-2 text-gray-600">₹{(salary.advance_deduction ?? 0).toLocaleString()}</td>
                    <td className="text-right px-4 py-2 text-yellow-600 font-bold">
                      ₹{(salary.net_pay ?? 0).toLocaleString()}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
