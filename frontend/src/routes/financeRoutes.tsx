// Finance routes. Moved verbatim from App.tsx (route-registry split);
// paths, elements and role gates are unchanged.
import { lazy } from 'react';
import { Route, Navigate, useSearchParams } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';
import { legacyTabTarget } from '../pages/finance/expenses/legacyTabRedirect';
import {
  EXPENSE_APPROVER_ROLES,
  EXPENSE_ACCOUNTANT_ROLES,
  FLOAT_VIEW_ROLES,
} from '../pages/finance/expenses/expenseShared';

// The Expenses layout (header, cards, section nav, Add-expense modal). The old
// page path is kept as a re-export shim of ExpensesLayout.
const ExpenseTracker = lazy(() => import('../pages/finance/ExpenseTracker'));
const MyExpensesSection = lazy(() => import('../pages/finance/expenses/MyExpensesSection').then(m => ({ default: m.MyExpensesSection })));
const ExpenseApprovalsSection = lazy(() => import('../pages/finance/expenses/ExpenseApprovalsSection').then(m => ({ default: m.ExpenseApprovalsSection })));
const ExpenseEntrySection = lazy(() => import('../pages/finance/expenses/ExpenseEntrySection').then(m => ({ default: m.ExpenseEntrySection })));
const ExpenseAgingSection = lazy(() => import('../pages/finance/expenses/ExpenseAgingSection').then(m => ({ default: m.ExpenseAgingSection })));
const ExpenseDuplicatesSection = lazy(() => import('../pages/finance/expenses/ExpenseDuplicatesSection').then(m => ({ default: m.ExpenseDuplicatesSection })));
const ExpenseAdvancesSection = lazy(() => import('../pages/finance/expenses/ExpenseAdvancesSection').then(m => ({ default: m.ExpenseAdvancesSection })));
const PettyCashFloatSection = lazy(() => import('../pages/finance/expenses/PettyCashFloatSection').then(m => ({ default: m.PettyCashFloatSection })));
const DaySettlementSection = lazy(() => import('../pages/finance/expenses/DaySettlementSection').then(m => ({ default: m.DaySettlementSection })));
const ExpenseSummarySection = lazy(() => import('../pages/finance/expenses/ExpenseSummarySection').then(m => ({ default: m.ExpenseSummarySection })));
const FinanceDashboard = lazy(() => import('../pages/finance/FinanceDashboard'));
const CashFlowPage = lazy(() => import('../pages/finance/CashFlowPage'));
const ItcReconcilePage = lazy(() => import('../pages/finance/ItcReconcilePage'));
const GstCrossCheckPage = lazy(() => import('../pages/finance/GstCrossCheckPage'));
const CashRegisterPage = lazy(() => import('../pages/finance/CashRegisterPage'));
const BlindEodTallyPage = lazy(() => import('../pages/finance/BlindEodTallyPage'));
const CashReconciliationPage = lazy(() => import('../pages/finance/CashReconciliationPage'));
const BudgetingPage = lazy(() => import('../pages/finance/BudgetingPage'));
const B2BTallyExport = lazy(() => import('../pages/finance/B2BTallyExport'));
const B2BTallyWorklist = lazy(() => import('../pages/finance/B2BTallyWorklist'));

// Bare /finance/expenses IS My Expenses. A legacy ?tab=<x> link forwards to
// that section's own URL (mapper in pages/finance/expenses/legacyTabRedirect).
function ExpensesIndex() {
  const [searchParams] = useSearchParams();
  if (!searchParams.has('tab')) return <MyExpensesSection />;
  return <Navigate to={legacyTabTarget(searchParams)} replace />;
}

export const financeRoutes = (
  <>
    {/* Expenses — any authenticated user can submit + see their own
        (ownership scoping happens server-side). Wave 6 split: one URL per
        section; each section's allowedRoles is the role gate its tab's JSX
        used to carry (lists in pages/finance/expenses/expenseShared.ts). */}
    <Route
      path="finance/expenses"
      element={
        <ProtectedRoute>
          <ExpenseTracker />
        </ProtectedRoute>
      }
    >
      <Route index element={<ExpensesIndex />} />
      <Route
        path="approvals"
        element={
          <ProtectedRoute allowedRoles={EXPENSE_APPROVER_ROLES}>
            <ExpenseApprovalsSection />
          </ProtectedRoute>
        }
      />
      <Route
        path="entry"
        element={
          <ProtectedRoute allowedRoles={EXPENSE_ACCOUNTANT_ROLES}>
            <ExpenseEntrySection />
          </ProtectedRoute>
        }
      />
      <Route
        path="aging"
        element={
          <ProtectedRoute allowedRoles={EXPENSE_ACCOUNTANT_ROLES}>
            <ExpenseAgingSection />
          </ProtectedRoute>
        }
      />
      <Route
        path="duplicates"
        element={
          <ProtectedRoute allowedRoles={EXPENSE_APPROVER_ROLES}>
            <ExpenseDuplicatesSection />
          </ProtectedRoute>
        }
      />
      {/* Advances and the category summary had no role gate on the old page. */}
      <Route path="advances" element={<ExpenseAdvancesSection />} />
      <Route
        path="float"
        element={
          <ProtectedRoute allowedRoles={FLOAT_VIEW_ROLES}>
            <PettyCashFloatSection />
          </ProtectedRoute>
        }
      />
      <Route
        path="settle"
        element={
          <ProtectedRoute allowedRoles={FLOAT_VIEW_ROLES}>
            <DaySettlementSection />
          </ProtectedRoute>
        }
      />
      <Route path="summary" element={<ExpenseSummarySection />} />
    </Route>

    {/* Bare /finance → /finance/dashboard. QA 2026-05-27 reported a 404
        on /finance because no route was defined. Same for the sidebar's
        old /cash-flow path. Hard 404s are user-hostile when the
        intent is clearly the canonical module landing screen. */}
    <Route
      path="finance"
      element={<Navigate to="/finance/dashboard" replace />}
    />
    <Route
      path="cash-flow"
      element={<Navigate to="/finance/cash-flow" replace />}
    />

    {/* Finance Dashboard */}
    <Route
      path="finance/dashboard"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}>
          <FinanceDashboard />
        </ProtectedRoute>
      }
    />
    <Route
      path="finance/cash-flow"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'ACCOUNTANT']}>
          <CashFlowPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="finance/itc"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'ACCOUNTANT']}>
          <ItcReconcilePage />
        </ProtectedRoute>
      }
    />
    {/* Accountant GST cross-check: GSTR-1/3B vs books side-by-side
        + month sign-off. Finance-admin only (matches backend
        _require_finance_admin). */}
    <Route
      path="finance/gst-cross-check"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'ACCOUNTANT']}>
          <GstCrossCheckPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="finance/cash-register"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}>
          <CashRegisterPage />
        </ProtectedRoute>
      }
    />
    {/* F23 Blind EOD cash tally & Z-Read -- cashiers reach it to
        open + blind-submit; managers reveal variance + lock. */}
    <Route
      path="finance/blind-eod"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'CASHIER', 'SALES_STAFF']}>
          <BlindEodTallyPage />
        </ProtectedRoute>
      }
    />
    {/* #7 Manager-facing cash-register vs blind-EOD reconciliation
        console -- READ-ONLY view across both day-close flows so an
        owner / store-manager can spot a cash disparity. Store
        Manager sees own store; HQ roles see all (store-scoped on the
        backend via resolve_store_scope). */}
    <Route
      path="finance/cash-reconciliation"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}>
          <CashReconciliationPage />
        </ProtectedRoute>
      }
    />
    {/* Dual-mode (planned vs actual) budgeting */}
    <Route
      path="finance/budgeting"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}>
          <BudgetingPage />
        </ProtectedRoute>
      }
    />
    {/* B2B invoices -> Tally: e-invoice + e-way bill issued in Tally.
        Export console + reminder worklist. Finance-admin only. */}
    <Route
      path="finance/b2b-tally-export"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'ACCOUNTANT']}>
          <B2BTallyExport />
        </ProtectedRoute>
      }
    />
    <Route
      path="finance/b2b-tally-worklist"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'ACCOUNTANT']}>
          <B2BTallyWorklist />
        </ProtectedRoute>
      }
    />
  </>
);
