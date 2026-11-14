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
// page's editorial header stays, as an h2 under HRLayout's h1 (one h1 per
// page), and its tab buttons are HRLayout's nav.

import { useState, useEffect, useRef } from 'react';
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
  const [selectedEmployee, setEmployee] = useState<string>('');
  
  // Data
  const [salarySheet, setSalarySheet] = useState<SalaryRecord[]>([]);
  const [advances, setAdvances] = useState<SalaryAdvance[]>([]);
  const [payslip, setPayslip] = useState<Payslip | null>(null);
  
  // UI
  const [isLoading, setIsLoading] = useState(true);
  // The payslip has its own flag and request counter: its load runs next to
  // the salary-sheet reload on every month change, and whichever answered
  // first used to decide what the Payslips section showed.
  const [payslipLoading, setPayslipLoading] = useState(false);
  const payslipRequest = useRef(0);
  const advancesRequest = useRef(0);
  const salarySheetRequest = useRef(0);
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

  // Advances and Payslips share ONE picked employee, and what each of them has
  // loaded belongs to that employee. Every change of the pick goes through
  // here and drops both, so one person's advances or payslip can never sit
  // under another person's name; each section then loads for the new pick.
  const setSelectedEmployee = (employeeId: string) => {
    setEmployee(employeeId);
    setAdvances([]);
    setPayslip(null);
  };

  // The picked employee belongs to the shop they were picked in. The shop
  // switches in place (no page reload), so drop the pick with it: the Advances
  // and Payslips sections show nothing until someone on the new shop's list is
  // picked, instead of the previous shop's employee under the new shop's list.
  useEffect(() => {
    setSelectedEmployee('');
  }, [user?.activeStoreId]);

  const loadSalarySheet = async () => {
    // Same rule as the payslip and advances loads: the list on screen (and the
    // employee pickers it feeds) belongs to the month / year / shop it was
    // asked for. Drop it before asking again and ignore any answer that is not
    // for the latest ask, so an older month's late answer never replaces it.
    const request = ++salarySheetRequest.current;
    setSalarySheet([]);
    setIsLoading(true);
    setError(null);
    setNoAccess(null);
    try {
      const data = await payrollApi.getSalarySheet(
        selectedMonth,
        selectedYear,
        user?.activeStoreId
      );
      if (request === salarySheetRequest.current) setSalarySheet(data?.salaries || []);
    } catch (err) {
      if (request !== salarySheetRequest.current) return;
      setSalarySheet([]);
      if (isForbiddenError(err)) {
        setNoAccess(forbiddenDetail(err, 'The salary sheet is restricted to administrators.'));
      } else {
        setError('Failed to load salary sheet');
      }
    } finally {
      if (request === salarySheetRequest.current) setIsLoading(false);
    }
  };

  const loadAdvances = async (employeeId: string) => {
    // Same rule as loadPayslip below: drop the previous employee's list before
    // asking, and ignore any answer that is not for the latest ask.
    const request = ++advancesRequest.current;
    setAdvances([]);
    setIsLoading(true);
    setError(null);
    setNoAccess(null);
    try {
      const data = await payrollApi.getAdvances(employeeId);
      if (request === advancesRequest.current) setAdvances(data?.advances || []);
    } catch (err) {
      if (request !== advancesRequest.current) return;
      if (isForbiddenError(err)) {
        setNoAccess(
          forbiddenDetail(err, "Another employee's salary advances are restricted to administrators.")
        );
      } else {
        setError('Failed to load advances');
      }
    } finally {
      if (request === advancesRequest.current) setIsLoading(false);
    }
  };

  const loadPayslip = async (employeeId: string) => {
    // The slip on screen belongs to the PREVIOUS employee / month / year: drop
    // it before asking, and ignore any answer that is not for the latest ask,
    // so one month's payslip can never sit under another month's selector.
    const request = ++payslipRequest.current;
    setPayslip(null);
    setPayslipLoading(true);
    setError(null);
    setNoAccess(null);
    try {
      const data = await payrollApi.getPayslip(employeeId, selectedMonth, selectedYear);
      if (request === payslipRequest.current) setPayslip(data?.payslip || null);
    } catch (err) {
      if (request !== payslipRequest.current) return;
      if (isForbiddenError(err)) {
        setNoAccess(
          forbiddenDetail(err, "Another employee's payslip is restricted to administrators.")
        );
      } else {
        setError('Failed to load payslip');
      }
    } finally {
      if (request === payslipRequest.current) setPayslipLoading(false);
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
    isLoading, payslipLoading, noAccess,
    showAdvanceForm, setShowAdvanceForm,
    advanceForm, setAdvanceForm,
    loadAdvances, loadPayslip, handleRecordAdvance, exportSalarySheet,
  };

  return (
    <>
      {/* Editorial header (kept from the old PayrollDashboard) */}
      <div className="inv-head">
        <div>
          <div className="eyebrow mb-1.5">Payroll</div>
          <h2>Month-end, by the rupee.</h2>
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

      <Outlet context={sectionContext} />
    </>
  );
}
