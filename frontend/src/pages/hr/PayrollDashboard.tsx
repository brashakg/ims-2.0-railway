// ============================================================================
// IMS 2.0 - Payroll & Salary Dashboard
// ============================================================================
// Salary sheet, advances, and payslips for Indian optical retail chain

import { useState, useEffect } from 'react';
import {
  DollarSign,
  Calendar,
  Loader2,
  AlertCircle,
  TrendingDown,
  FileText,
  DownloadCloud,
  Plus,
  Check,
  Clock,
  X,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import clsx from 'clsx';

// Types
interface SalaryBreakdown {
  basic: number;
  hra: number;
  conveyance: number;
  medical: number;
  special_allowance: number;
  gross_salary: number;
  pf_employee: number;
  pf_employer: number;
  professional_tax: number;
  esi: number;
  tds: number;
  lwp_deduction: number;
  advance_deduction: number;
  net_pay: number;
}

interface SalaryRecord {
  salary_record_id: string;
  employee_id: string;
  employee_name: string;
  month: number;
  year: number;
  breakdown: SalaryBreakdown;
  status: string;
}

interface SalaryAdvance {
  advance_id: string;
  employee_id: string;
  employee_name: string;
  amount: number;
  date_requested: string;
  status: 'pending' | 'approved' | 'settled' | 'deducted';
}

interface Payslip {
  payslip_id: string;
  employee_id: string;
  employee_name: string;
  employee_number: string;
  designation: string;
  month: number;
  year: number;
  breakdown: SalaryBreakdown;
  generated_at: string;
}

// API helper
const payrollApi = {
  getSalarySheet: async (month: number, year: number, storeId?: string) => {
    const params = new URLSearchParams();
    params.append('month', String(month));
    params.append('year', String(year));
    if (storeId) params.append('store_id', storeId);
    const response = await fetch(`/api/v1/payroll/salary-sheet?${params}`, {
      headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` },
    });
    return response.json();
  },

  recordAdvance: async (employeeId: string, amount: number, reason?: string) => {
    const response = await fetch('/api/v1/payroll/advances', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${localStorage.getItem('token')}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        employee_id: employeeId,
        amount,
        reason,
      }),
    });
    return response.json();
  },

  getAdvances: async (employeeId: string) => {
    const response = await fetch(`/api/v1/payroll/advances/${employeeId}`, {
      headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` },
    });
    return response.json();
  },

  getPayslip: async (employeeId: string, month: number, year: number) => {
    const response = await fetch(
      `/api/v1/payroll/payslip/${employeeId}/${month}/${year}`,
      {
        headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` },
      }
    );
    return response.json();
  },
};

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

const getCurrentMonthYear = () => {
  const today = new Date();
  return { month: today.getMonth() + 1, year: today.getFullYear() };
};

export function PayrollDashboard() {
  const { user } = useAuth();
  const { month: currentMonth, year: currentYear } = getCurrentMonthYear();

  // State
  const [activeTab, setActiveTab] = useState<'sheet' | 'advances' | 'payslips'>('sheet');
  const [selectedMonth, setSelectedMonth] = useState(currentMonth);
  const [selectedYear, setSelectedYear] = useState(currentYear);
  const [selectedEmployee, setSelectedEmployee] = useState<string>('');
  
  // Data
  const [salarySheet, setSalarySheet] = useState<SalaryRecord[]>([]);
  const [advances, setAdvances] = useState<SalaryAdvance[]>([]);
  const [payslip, setPayslip] = useState<Payslip | null>(null);
  
  // UI
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAdvanceForm, setShowAdvanceForm] = useState(false);
  const [advanceForm, setAdvanceForm] = useState({ amount: '', reason: '' });

  // Load salary sheet
  useEffect(() => {
    loadSalarySheet();
  }, [selectedMonth, selectedYear, user?.activeStoreId]);

  const loadSalarySheet = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await payrollApi.getSalarySheet(
        selectedMonth,
        selectedYear,
        user?.activeStoreId
      );
      setSalarySheet(data?.salaries || []);
    } catch (err) {
      setError('Failed to load salary sheet');
    } finally {
      setIsLoading(false);
    }
  };

  const loadAdvances = async (employeeId: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await payrollApi.getAdvances(employeeId);
      setAdvances(data?.advances || []);
    } catch (err) {
      setError('Failed to load advances');
    } finally {
      setIsLoading(false);
    }
  };

  const loadPayslip = async (employeeId: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await payrollApi.getPayslip(employeeId, selectedMonth, selectedYear);
      setPayslip(data?.payslip || null);
    } catch (err) {
      setError('Failed to load payslip');
    } finally {
      setIsLoading(false);
    }
  };

  const handleRecordAdvance = async (employeeId: string) => {
    if (!advanceForm.amount) {
      setError('Please enter advance amount');
      return;
    }
    try {
      await payrollApi.recordAdvance(
        employeeId,
        parseFloat(advanceForm.amount),
        advanceForm.reason
      );
      setShowAdvanceForm(false);
      setAdvanceForm({ amount: '', reason: '' });
      await loadAdvances(employeeId);
    } catch (err) {
      setError('Failed to record advance');
    }
  };

  const exportSalarySheet = () => {
    let csv = 'Name,Basic,HRA,Allowances,Gross,PF,ESI,PT,TDS,LWP,Advance,Net Pay\n';
    salarySheet.forEach((salary) => {
      const b = salary.breakdown;
      const allowances = b.conveyance + b.medical;
      csv += `${salary.employee_name},${b.basic},${b.hra},${allowances},${b.gross_salary},${b.pf_employee},${b.esi},${b.professional_tax},${b.tds},${b.lwp_deduction},${b.advance_deduction},${b.net_pay}\n`;
    });
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `payroll_${selectedMonth}_${selectedYear}.csv`;
    a.click();
  };

  return (
    <div className="inv-body">
      {/* Editorial header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Payroll</div>
          <h1>Month-end, by the rupee.</h1>
          <div className="hint">Basic + HRA + allowances − PF − ESI − PT − TDS − advances. Payslips, salary sheet export, month-lock after close.</div>
        </div>
      </div>

      {/* Error Alert */}
      {error && (
        <div className="bg-red-50/20 border border-red-600 text-red-600 px-4 py-3 rounded-lg flex items-center gap-2">
          <AlertCircle className="w-5 h-5" />
          {error}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-200">
        <button
          onClick={() => setActiveTab('sheet')}
          className={clsx(
            'px-4 py-2 font-medium transition-colors',
            activeTab === 'sheet'
              ? 'text-blue-600 border-b-2 border-blue-400'
              : 'text-gray-500 hover:text-gray-700'
          )}
        >
          <DollarSign className="inline w-4 h-4 mr-2" />
          Salary Sheet
        </button>
        <button
          onClick={() => setActiveTab('advances')}
          className={clsx(
            'px-4 py-2 font-medium transition-colors',
            activeTab === 'advances'
              ? 'text-blue-600 border-b-2 border-blue-400'
              : 'text-gray-500 hover:text-gray-700'
          )}
        >
          <TrendingDown className="inline w-4 h-4 mr-2" />
          Advances
        </button>
        <button
          onClick={() => setActiveTab('payslips')}
          className={clsx(
            'px-4 py-2 font-medium transition-colors',
            activeTab === 'payslips'
              ? 'text-blue-600 border-b-2 border-blue-400'
              : 'text-gray-500 hover:text-gray-700'
          )}
        >
          <FileText className="inline w-4 h-4 mr-2" />
          Payslips
        </button>
      </div>

      {/* TAB: SALARY SHEET */}
      {activeTab === 'sheet' && (
        <div className="space-y-4">
          {/* Controls */}
          <div className="flex items-center justify-between bg-white border border-gray-200 p-4 rounded-lg">
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2">
                <Calendar className="w-4 h-4 text-gray-500" />
                <select
                  value={selectedMonth}
                  onChange={(e) => setSelectedMonth(Number(e.target.value))}
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
                  {salarySheet.map((salary) => {
                    const b = salary.breakdown;
                    const allowances = b.conveyance + b.medical;
                    return (
                      <tr
                        key={salary.salary_record_id}
                        className="border-b border-gray-200 hover:bg-gray-50 transition"
                      >
                        <td className="px-4 py-2 text-gray-900 font-medium">
                          {salary.employee_name}
                        </td>
                        <td className="text-right px-4 py-2 text-gray-600">₹{b.basic.toLocaleString()}</td>
                        <td className="text-right px-4 py-2 text-gray-600">₹{b.hra.toLocaleString()}</td>
                        <td className="text-right px-4 py-2 text-gray-600">₹{allowances.toLocaleString()}</td>
                        <td className="text-right px-4 py-2 text-green-600 font-medium">
                          ₹{b.gross_salary.toLocaleString()}
                        </td>
                        <td className="text-right px-4 py-2 text-gray-600">₹{b.pf_employee.toLocaleString()}</td>
                        <td className="text-right px-4 py-2 text-gray-600">₹{b.esi.toLocaleString()}</td>
                        <td className="text-right px-4 py-2 text-gray-600">₹{b.professional_tax.toLocaleString()}</td>
                        <td className="text-right px-4 py-2 text-gray-600">₹{b.tds.toLocaleString()}</td>
                        <td className="text-right px-4 py-2 text-gray-600">₹{b.lwp_deduction.toLocaleString()}</td>
                        <td className="text-right px-4 py-2 text-gray-600">₹{b.advance_deduction.toLocaleString()}</td>
                        <td className="text-right px-4 py-2 text-yellow-600 font-bold">
                          ₹{b.net_pay.toLocaleString()}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* TAB: ADVANCES */}
      {activeTab === 'advances' && (
        <div className="space-y-4">
          {/* Employee selector */}
          <div className="bg-white border border-gray-200 p-4 rounded-lg">
            <div className="flex items-center gap-4">
              <select
                value={selectedEmployee}
                onChange={(e) => {
                  setSelectedEmployee(e.target.value);
                  if (e.target.value) {
                    loadAdvances(e.target.value);
                  }
                }}
                className="bg-white border border-gray-300 text-gray-900 px-4 py-2 rounded flex-1"
              >
                <option value="">Select employee...</option>
                {salarySheet.map((salary) => (
                  <option key={salary.employee_id} value={salary.employee_id}>
                    {salary.employee_name}
                  </option>
                ))}
              </select>
              {selectedEmployee && (
                <button
                  onClick={() => setShowAdvanceForm(!showAdvanceForm)}
                  className="btn-primary flex items-center gap-2"
                >
                  <Plus className="w-4 h-4" />
                  Record Advance
                </button>
              )}
            </div>
          </div>

          {/* Advance form */}
          {showAdvanceForm && selectedEmployee && (
            <div className="bg-white border border-gray-200 p-4 rounded-lg space-y-3">
              <input
                type="number"
                placeholder="Advance amount (₹)"
                value={advanceForm.amount}
                onChange={(e) => setAdvanceForm({ ...advanceForm, amount: e.target.value })}
                className="w-full bg-white border border-gray-300 text-gray-900 px-3 py-2 rounded text-sm"
              />
              <textarea
                placeholder="Reason (optional)"
                value={advanceForm.reason}
                onChange={(e) => setAdvanceForm({ ...advanceForm, reason: e.target.value })}
                className="w-full bg-white border border-gray-300 text-gray-900 px-3 py-2 rounded text-sm h-20"
              />
              <div className="flex gap-2">
                <button
                  onClick={() => handleRecordAdvance(selectedEmployee)}
                  className="btn-primary flex-1"
                >
                  Submit
                </button>
                <button
                  onClick={() => setShowAdvanceForm(false)}
                  className="btn-outline flex-1"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {/* Advances list */}
          {selectedEmployee && (
            isLoading ? (
              <div className="flex justify-center py-8">
                <Loader2 className="w-8 h-8 text-blue-600 animate-spin" />
              </div>
            ) : advances.length === 0 ? (
              <div className="text-center py-8 text-gray-500">No advances recorded</div>
            ) : (
              <div className="space-y-3">
                {advances.map((adv) => (
                  <div
                    key={adv.advance_id}
                    className="bg-white border border-gray-200 p-4 rounded-lg flex items-center justify-between"
                  >
                    <div>
                      <p className="text-gray-900 font-medium">₹{adv.amount.toLocaleString()}</p>
                      <p className="text-gray-500 text-sm">{adv.date_requested}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      {adv.status === 'pending' && (
                        <span className="flex items-center gap-1 text-yellow-600 text-sm">
                          <Clock className="w-4 h-4" /> Pending
                        </span>
                      )}
                      {adv.status === 'approved' && (
                        <span className="flex items-center gap-1 text-green-600 text-sm">
                          <Check className="w-4 h-4" /> Approved
                        </span>
                      )}
                      {adv.status === 'settled' && (
                        <span className="flex items-center gap-1 text-blue-600 text-sm">
                          <Check className="w-4 h-4" /> Settled
                        </span>
                      )}
                      {adv.status === 'deducted' && (
                        <span className="flex items-center gap-1 text-gray-500 text-sm">$
                          <X className="w-4 h-4" /> Deducted
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )
          )}
        </div>
      )}

      {/* TAB: PAYSLIPS */}
      {activeTab === 'payslips' && (
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
            ) : (
              <div className="text-center py-8 text-gray-500">
                No payslip found for selected month
              </div>
            )
          )}
        </div>
      )}
    </div>
  );
}
