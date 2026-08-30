// Finance routes. Moved verbatim from App.tsx (route-registry split);
// paths, elements and role gates are unchanged.
import { lazy } from 'react';
import { Route, Navigate } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';

const ExpenseTracker = lazy(() => import('../pages/finance/ExpenseTracker'));
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

export const financeRoutes = (
  <>
    {/* Expenses — any authenticated user can submit + see their own;
        ownership scoping + role-gated approval/entry happen inside. */}
    <Route
      path="finance/expenses"
      element={
        <ProtectedRoute>
          <ExpenseTracker />
        </ProtectedRoute>
      }
    />

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
