// ============================================================================
// IMS 2.0 - Payroll: shared types, the salary role list and the section context
// ============================================================================
// Wave 6 B14: the old PayrollDashboard held three tabs (Salary Sheet /
// Advances / Payslips) in useState behind the one URL /hr/payroll, OUTSIDE
// HRLayout. Each tab is now its own page, a section of the HR module:
//   /hr/payroll (Salary Sheet, the index) · /hr/payroll/advances ·
//   /hr/payroll/payslips
// What more than one of those pages reads lives here, moved byte-identical
// from the old page.

import { useOutletContext } from 'react-router-dom';
import type { UserRole } from '../../../types';

// Salary-bearing screens. Owner ruling 2026-08-10 (fully strict, no accountant
// carve-out) - do not widen. Read by BOTH the router (routes/hrRoutes.tsx:
// the /hr/payroll subtree and /hr/salary-setup) and HRLayout's section nav,
// so a payroll section can never be offered to a role its route refuses.
const SALARY_ROLES: UserRole[] = ['SUPERADMIN', 'ADMIN'];

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

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

/** What PayrollLayout hands its section pages through <Outlet context>: the
 *  old page's state and handlers, unchanged. The layout owns the one
 *  salary-sheet load; Advances and Payslips fill their employee picker from
 *  it, and month / year / employee stay picked when you move between
 *  sections, as they did between the old tabs. */
interface PayrollOutletContext {
  currentYear: number;
  selectedMonth: number;
  setSelectedMonth: (month: number) => void;
  selectedYear: number;
  setSelectedYear: (year: number) => void;
  selectedEmployee: string;
  setSelectedEmployee: (employeeId: string) => void;
  salarySheet: SalaryRecord[];
  advances: SalaryAdvance[];
  payslip: Payslip | null;
  isLoading: boolean;
  noAccess: string | null;
  showAdvanceForm: boolean;
  setShowAdvanceForm: (open: boolean) => void;
  advanceForm: { amount: string; reason: string };
  setAdvanceForm: (form: { amount: string; reason: string }) => void;
  loadAdvances: (employeeId: string) => Promise<void>;
  loadPayslip: (employeeId: string) => Promise<void>;
  handleRecordAdvance: (employeeId: string) => Promise<void>;
  exportSalarySheet: () => void;
}

const usePayrollContext = () => useOutletContext<PayrollOutletContext>();

export { SALARY_ROLES, MONTHS, usePayrollContext };
export type { SalaryRecord, SalaryAdvance, Payslip, PayrollOutletContext };
