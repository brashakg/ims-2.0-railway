// /hr/payroll/payslips -- Payslips. Moved byte-identical from the old
// PayrollDashboard 'payslips' tab; its state and handlers live in
// PayrollLayout and arrive through <Outlet context>.

import { Loader2 } from 'lucide-react';
import { PayrollAccessNotice } from '../../../components/hr/PayrollAccessNotice';
import { MONTHS, usePayrollContext } from './payrollShared';

export function PayslipsSection() {
  const {
    currentYear, selectedMonth, setSelectedMonth, selectedYear, setSelectedYear,
    selectedEmployee, setSelectedEmployee, salarySheet, payslip, isLoading, noAccess,
    loadPayslip,
  } = usePayrollContext();

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="bg-white border border-gray-200 p-4 rounded-lg">
        <div className="flex items-center gap-4">
          <select
            value={selectedEmployee}
            onChange={(e) => {
              setSelectedEmployee(e.target.value);
              if (e.target.value) {
                loadPayslip(e.target.value);
              }
            }}
            title="Select employee for payslip"
            className="bg-white border border-gray-300 text-gray-900 px-4 py-2 rounded flex-1"
          >
            <option value="">Select employee...</option>
            {salarySheet.map((salary) => (
              <option key={salary.employee_id} value={salary.employee_id}>
                {salary.employee_name}
              </option>
            ))}
          </select>
          <div className="flex items-center gap-2">
            <select
              value={selectedMonth}
              onChange={(e) => setSelectedMonth(Number(e.target.value))}
              title="Select payslip month"
              className="bg-white border border-gray-300 text-gray-900 px-3 py-2 rounded"
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
              title="Select payslip year"
              className="bg-white border border-gray-300 text-gray-900 px-3 py-2 rounded"
            >
              {[currentYear - 1, currentYear, currentYear + 1].map((year) => (
                <option key={year} value={year}>
                  {year}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* Payslip display */}
      {selectedEmployee && (
        isLoading ? (
          <div className="flex justify-center py-8">
            <Loader2 className="w-8 h-8 text-blue-600 animate-spin" />
          </div>
        ) : payslip ? (
          <div className="bg-white border border-gray-200 p-8 rounded-lg space-y-4">
            <div className="text-center pb-4 border-b border-gray-200">
              <h2 className="text-2xl font-bold text-gray-900">{payslip.employee_name}</h2>
              <p className="text-gray-500 text-sm">{payslip.designation}</p>
              <p className="text-gray-500 text-xs">
                {MONTHS[payslip.month - 1]} {payslip.year}
              </p>
            </div>

            <div className="grid grid-cols-2 gap-8">
              <div>
                <h3 className="text-gray-500 font-medium mb-3 text-sm">EARNINGS</h3>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-gray-600">Basic Salary</span>
                    <span className="text-gray-900">₹{payslip.breakdown.basic.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-600">HRA</span>
                    <span className="text-gray-900">₹{payslip.breakdown.hra.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-600">Conveyance</span>
                    <span className="text-gray-900">₹{payslip.breakdown.conveyance.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-600">Medical</span>
                    <span className="text-gray-900">₹{payslip.breakdown.medical.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between border-t border-gray-200 pt-2 mt-2">
                    <span className="text-gray-600 font-medium">Gross Salary</span>
                    <span className="text-green-600 font-bold">
                      ₹{payslip.breakdown.gross_salary.toLocaleString()}
                    </span>
                  </div>
                </div>
              </div>

              <div>
                <h3 className="text-gray-500 font-medium mb-3 text-sm">DEDUCTIONS</h3>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-gray-600">PF (Employee)</span>
                    <span className="text-gray-900">₹{payslip.breakdown.pf_employee.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-600">ESI</span>
                    <span className="text-gray-900">₹{payslip.breakdown.esi.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-600">Professional Tax</span>
                    <span className="text-gray-900">₹{payslip.breakdown.professional_tax.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-600">TDS</span>
                    <span className="text-gray-900">₹{payslip.breakdown.tds.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-600">LWP Deduction</span>
                    <span className="text-gray-900">₹{payslip.breakdown.lwp_deduction.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-600">Advance Deduction</span>
                    <span className="text-gray-900">₹{payslip.breakdown.advance_deduction.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between border-t border-gray-200 pt-2 mt-2">
                    <span className="text-gray-600 font-medium">Net Pay</span>
                    <span className="text-yellow-600 font-bold">
                      ₹{payslip.breakdown.net_pay.toLocaleString()}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        ) : noAccess ? (
          <PayrollAccessNotice message={noAccess} what="this payslip" />
        ) : (
          <div className="text-center py-8 text-gray-500">
            No payslip found for selected month
          </div>
        )
      )}
    </div>
  );
}
