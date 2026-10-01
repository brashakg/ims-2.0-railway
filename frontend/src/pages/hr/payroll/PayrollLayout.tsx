// ============================================================================
// IMS 2.0 - Payroll & Salary (layout)
// ============================================================================
// Salary sheet, advances, and payslips for Indian optical retail chain.
//
// Wave 6 B14 (the expenses split, PR #1156, is the model): the old
// PayrollDashboard held these three tabs in useState behind /hr/payroll and
// sat OUTSIDE HRLayout. Each is a real page now, a section of the HR module:
//   /hr/payroll (Salary Sheet, the index) · /hr/payroll/advances ·
//   /hr/payroll/payslips
// HRLayout draws the module header and the section nav (its SECTIONS offer
// the three payroll rows to SALARY_ROLES only); routes/hrRoutes.tsx gates the
// whole /hr/payroll subtree once with the same list. This layout keeps the
// old page's state, loaders and handlers unchanged - including the ONE
// salary-sheet load that the Advances and Payslips employee pickers also read
// - and hands them to the section pages through <Outlet context>. The old
// page's own editorial header is gone (HRLayout's is on screen) and its tab
// buttons are HRLayout's nav.

import { useState, useEffect } from 'react';
import { Outlet } from 'react-router-dom';
import { AlertCircle } from 'lucide-react';
import { useAuth } from '../../../context/AuthContext';
import { default as api } from '../../../services/api/client';
import { neutralizeFormula } from '../../../utils/exportUtils';
import { isForbiddenError, forbiddenDetail } from '../../../utils/errorHandler';
import type { SalaryRecord, SalaryAdvance, Payslip, PayrollOutletContext } from './payrollShared';

// API helper — uses the shared axios client so the right baseURL +
// auth token are applied. Raw fetch hit the Vercel domain instead of
// the Railway backend, AND used the wrong localStorage key
// ('token' instead of 'ims_token'), so every payroll call 404'd.
const payrollApi = {
  getSalarySheet: async (month: number, year: number, storeId?: string) => {
    const r = await api.get('/payroll/salary-sheet', {
      params: { month, year, ...(storeId ? { store_id: storeId } : {}) },
    });
    return r.data;
  },

  recordAdvance: async (employeeId: string, amount: number, reason?: string) => {
    const r = await api.post('/payroll/advances', {
      employee_id: employeeId, amount, reason,
    });
    return r.data;
  },

  getAdvances: async (employeeId: string) => {
    const r = await api.get(`/payroll/advances/${employeeId}`);
    return r.data;
  },

  getPayslip: async (employeeId: string, month: number, year: number) => {
    const r = await api.get(`/payroll/payslip/${employeeId}/${month}/${year}`);
    return r.data;
  },
};

const getCurrentMonthYear = () => {
  const today = new Date();
  return { month: today.getMonth() + 1, year: today.getFullYear() };
};

export function PayrollLayout() {
  const { user } = useAuth();
  const { month: currentMonth, year: currentYear } = getCurrentMonthYear();

  // State
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
  // Owner ruling 2026-08-09: salary data is ADMIN-only. A 403 must show an
  // explicit access notice -- an empty table would read as "nobody was paid".
  const [noAccess, setNoAccess] = useState<string | null>(null);
  const [showAdvanceForm, setShowAdvanceForm] = useState(false);
  const [advanceForm, setAdvanceForm] = useState({ amount: '', reason: '' });

  // Load salary sheet
  useEffect(() => {
    loadSalarySheet();
  }, [selectedMonth, selectedYear, user?.activeStoreId]);

  const loadSalarySheet = async () => {
    setIsLoading(true);
    setError(null);
    setNoAccess(null);
    try {
      const data = await payrollApi.getSalarySheet(
        selectedMonth,
        selectedYear,
        user?.activeStoreId
      );
      setSalarySheet(data?.salaries || []);
    } catch (err) {
      setSalarySheet([]);
      if (isForbiddenError(err)) {
        setNoAccess(forbiddenDetail(err, 'The salary sheet is restricted to administrators.'));
      } else {
        setError('Failed to load salary sheet');
      }
    } finally {
      setIsLoading(false);
    }
  };

  const loadAdvances = async (employeeId: string) => {
    setIsLoading(true);
    setError(null);
    setNoAccess(null);
    try {
      const data = await payrollApi.getAdvances(employeeId);
      setAdvances(data?.advances || []);
    } catch (err) {
      setAdvances([]);
      if (isForbiddenError(err)) {
        setNoAccess(
          forbiddenDetail(err, "Another employee's salary advances are restricted to administrators.")
        );
      } else {
        setError('Failed to load advances');
      }
    } finally {
      setIsLoading(false);
    }
  };

  const loadPayslip = async (employeeId: string) => {
    setIsLoading(true);
    setError(null);
    setNoAccess(null);
    try {
      const data = await payrollApi.getPayslip(employeeId, selectedMonth, selectedYear);
      setPayslip(data?.payslip || null);
    } catch (err) {
      setPayslip(null);
      if (isForbiddenError(err)) {
        setNoAccess(
          forbiddenDetail(err, "Another employee's payslip is restricted to administrators.")
        );
      } else {
        setError('Failed to load payslip');
      }
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
    const BOM = '﻿'; // UTF-8 BOM for Excel compatibility
    salarySheet.forEach((salary: any) => {
      // Flat rows from GET /payroll/salary-sheet (no nested breakdown).
      csv += `${neutralizeFormula(salary.employee_name)},${salary.basic ?? 0},${salary.hra ?? 0},${salary.allowances ?? 0},${salary.gross_salary ?? 0},${salary.pf_employee ?? 0},${salary.esi ?? 0},${salary.professional_tax ?? 0},${salary.tds ?? 0},${salary.lwp_deduction ?? 0},${salary.advance_deduction ?? 0},${salary.net_pay ?? 0}\n`;
    });
    const blob = new Blob([BOM + csv], { type: 'text/csv;charset=utf-8;' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `payroll_${selectedMonth}_${selectedYear}.csv`;
    a.click();
  };

  const sectionContext: PayrollOutletContext = {
    currentYear,
    selectedMonth, setSelectedMonth,
    selectedYear, setSelectedYear,
    selectedEmployee, setSelectedEmployee,
    salarySheet, advances, payslip,
    isLoading, noAccess,
    showAdvanceForm, setShowAdvanceForm,
    advanceForm, setAdvanceForm,
    loadAdvances, loadPayslip, handleRecordAdvance, exportSalarySheet,
  };

  return (
    <>
      {/* Error Alert */}
      {error && (
        <div className="bg-red-50/20 border border-red-600 text-red-600 px-4 py-3 rounded-lg flex items-center gap-2">
          <AlertCircle className="w-5 h-5" />
          {error}
        </div>
      )}

      <Outlet context={sectionContext} />
    </>
  );
}
