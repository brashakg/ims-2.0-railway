// HR + payroll routes.
//
// Wave 2 split: the old /hr tab container (HRPage) is now a layout with one
// REAL page per surviving section:
//   /hr/today · /hr/leave · /hr/week-off-swaps · /hr/shifts · /hr/leaderboard
// Only /hr?tab=leave was ever addressable; that link still works (HRTabRedirect
// below), and bare /hr lands on Today's Attendance as it always did.
//
// TWO of the seven tabs were NOT given a URL - both were duplicates of a
// screen that already exists as a top-level destination, so a dedicated /hr
// address would have been a bookmarkable dead end:
//   * "Monthly Summary" rendered AttendanceSummaryCard - four numbers rolled
//     up from the SAME grid endpoint /attendance already draws in full, plus
//     a "View attendance" link to that page. Deleted; /hr/monthly-summary
//     forwards to /attendance, which shows a strict superset.
//   * "My Dashboard" rendered components/hr/EmployeeSelfService - an older
//     second implementation of /my-work. It rebuilt "my attendance" by
//     client-side filtering the store-wide, manager-gated roster, where
//     /my-work reads the server-pinned /hr/me/* endpoints any role can call.
//     Deleted (one rule, one implementation); /hr/self-service forwards to
//     /my-work. Its one non-duplicated card (MTD incentive points) is already
//     a full screen at /incentive.
//
// SALARY GATE (owner ruling 2026-08-10, fully strict, no accountant carve-out):
// /hr/payroll/* and /hr/salary-setup render gross_salary / net_pay / the
// Structured-CTC master, so both are now SUPERADMIN + ADMIN only. They used to
// admit AREA_MANAGER / STORE_MANAGER / ACCOUNTANT, who were stopped only by a
// backend 403 - a menu item that always refused. The list (SALARY_ROLES) lives
// in pages/hr/payroll/payrollShared.ts so HRLayout's nav reads the same one.
//
// Wave 6 B14: the payroll page's three tabs (Salary Sheet / Advances /
// Payslips) are sections of the HR module now, one URL each under the layout:
//   /hr/payroll (the sheet, index) · /hr/payroll/advances · /hr/payroll/payslips
// The old page never honoured a ?tab= (useState only) and nothing in the app
// linked it, so there is no legacy link to forward.
import { lazy } from 'react';
import { Route, Navigate, useSearchParams } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';
import { SALARY_ROLES } from '../pages/hr/payroll/payrollShared';
import type { UserRole } from '../types';

const HRLayout = lazy(() => import('../pages/hr/HRLayout').then(m => ({ default: m.HRLayout })));
const HRTodayPage = lazy(() => import('../pages/hr/HRTodayPage').then(m => ({ default: m.HRTodayPage })));
const HRLeavePage = lazy(() => import('../pages/hr/HRLeavePage').then(m => ({ default: m.HRLeavePage })));
const HRLeaderboardPage = lazy(() => import('../pages/hr/HRLeaderboardPage').then(m => ({ default: m.HRLeaderboardPage })));
const WeekOffSwap = lazy(() => import('../components/hr/WeekOffSwap').then(m => ({ default: m.WeekOffSwap })));
const ShiftSetup = lazy(() => import('../components/hr/ShiftSetup').then(m => ({ default: m.ShiftSetup })));
const PayrollLayout = lazy(() => import('../pages/hr/payroll/PayrollLayout').then(m => ({ default: m.PayrollLayout })));
const SalarySheetSection = lazy(() => import('../pages/hr/payroll/SalarySheetSection').then(m => ({ default: m.SalarySheetSection })));
const SalaryAdvancesSection = lazy(() => import('../pages/hr/payroll/SalaryAdvancesSection').then(m => ({ default: m.SalaryAdvancesSection })));
const PayslipsSection = lazy(() => import('../pages/hr/payroll/PayslipsSection').then(m => ({ default: m.PayslipsSection })));
const SalarySetupPage = lazy(() => import('../pages/hr/SalarySetupPage').then(m => ({ default: m.SalarySetupPage })));
const PayrollRunPage = lazy(() => import('../pages/hr/PayrollRunPage').then(m => ({ default: m.PayrollRunPage })));

// The module gate for the section pages — identical to the old /hr gate.
const HR_ROLES: UserRole[] = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'];
// Shift config is manager-tier, matching the backend require_roles gate (the
// tab was hidden from ACCOUNTANT on the old page for the same reason).
const SHIFT_ROLES: UserRole[] = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'];

// Legacy ?tab= mapper: /hr and /hr?tab=leave land on the section page,
// carrying every other query param along.
// `leaderboard` is here because the HR module menu linked /hr?tab=leaderboard
// while the old page only honoured `leave` — that row silently landed on
// attendance. Same class of bug as the unreachable Reports forecast tab.
const TAB_TO_PATH: Record<string, string> = {
  leave: 'leave',
  leaderboard: 'leaderboard',
};

function HRTabRedirect() {
  const [searchParams] = useSearchParams();
  const tab = searchParams.get('tab') || '';
  const section = TAB_TO_PATH[tab] ?? 'today';
  const rest = new URLSearchParams(searchParams);
  rest.delete('tab');
  const suffix = rest.toString() ? `?${rest.toString()}` : '';
  return <Navigate to={`/hr/${section}${suffix}`} replace />;
}

export const hrRoutes = (
  <>
    {/* HR module — layout + one page per section */}
    <Route
      path="hr"
      element={
        <ProtectedRoute allowedRoles={HR_ROLES}>
          <HRLayout />
        </ProtectedRoute>
      }
    >
      {/* Bare /hr (incl. the legacy ?tab=leave link) → the right section */}
      <Route index element={<HRTabRedirect />} />
      <Route
        path="today"
        element={
          <ProtectedRoute allowedRoles={HR_ROLES}>
            <HRTodayPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="leave"
        element={
          <ProtectedRoute allowedRoles={HR_ROLES}>
            <HRLeavePage />
          </ProtectedRoute>
        }
      />
      <Route
        path="week-off-swaps"
        element={
          <ProtectedRoute allowedRoles={HR_ROLES}>
            <WeekOffSwap />
          </ProtectedRoute>
        }
      />
      <Route
        path="shifts"
        element={
          <ProtectedRoute allowedRoles={SHIFT_ROLES}>
            <ShiftSetup />
          </ProtectedRoute>
        }
      />
      <Route
        path="leaderboard"
        element={
          <ProtectedRoute allowedRoles={HR_ROLES}>
            <HRLeaderboardPage />
          </ProtectedRoute>
        }
      />
      {/* Payroll — SUPERADMIN + ADMIN only (SALARY_ROLES). The gate sits ONCE
          on the subtree: a section only ever renders inside this layout's
          Outlet, so every payroll address is refused to the same roles the
          old /hr/payroll refused, and no section can be gated differently. */}
      <Route
        path="payroll"
        element={
          <ProtectedRoute allowedRoles={SALARY_ROLES}>
            <PayrollLayout />
          </ProtectedRoute>
        }
      >
        <Route index element={<SalarySheetSection />} />
        <Route path="advances" element={<SalaryAdvancesSection />} />
        <Route path="payslips" element={<PayslipsSection />} />
      </Route>
    </Route>

    {/* Retired tabs → the real screen (see the header note). Both live
        OUTSIDE /hr on purpose, so they are not children of the layout. */}
    <Route path="hr/monthly-summary" element={<Navigate to="/attendance" replace />} />
    {/* JARVIS' "Low attendance today" card has always linked /hr/attendance
        (backend/api/routers/jarvis.py), an address that never existed and
        404'd. Now that the roster HAS a URL, point it there. */}
    <Route path="hr/attendance" element={<Navigate to="/hr/today" replace />} />
    <Route path="hr/self-service" element={<Navigate to="/my-work" replace />} />

    {/* Salary-bearing screen — SUPERADMIN + ADMIN only. */}
    <Route
      path="hr/salary-setup"
      element={(
        <ProtectedRoute allowedRoles={SALARY_ROLES}>
          <SalarySetupPage />
        </ProtectedRoute>
      )}
    />
    <Route
      path="hr/payroll-run"
      element={(
        <ProtectedRoute allowedRoles={["SUPERADMIN", "ADMIN", "ACCOUNTANT"]}>
          <PayrollRunPage />
        </ProtectedRoute>
      )}
    />
  </>
);
