// Reports + print routes. Moved verbatim from App.tsx (route-registry split);
// paths, elements and role gates are unchanged.
import { lazy } from 'react';
import { Route } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';

const ReportsPage = lazy(() => import('../pages/reports/ReportsPage').then(m => ({ default: m.ReportsPage })));
const GrowthBlueprintPage = lazy(() => import('../pages/reports/GrowthBlueprintPage').then(m => ({ default: m.GrowthBlueprintPage })));
const DayEndReport = lazy(() => import('../pages/reports/DayEndReport'));
const OutstandingPaymentsReport = lazy(() => import('../pages/reports/OutstandingPaymentsReport'));
const PrintPage = lazy(() => import('../pages/print/PrintPage'));

export const reportRoutes = (
  <>
    {/* Reports */}
    <Route
      path="reports"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}
        >
          <ReportsPage />
        </ProtectedRoute>
      }
    />

    {/* R3 — Growth Blueprint (SUPERADMIN-only — uses LLM tokens) */}
    <Route
      path="reports/blueprint"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN']}>
          <GrowthBlueprintPage />
        </ProtectedRoute>
      }
    />

    {/* Day-End Closing Report */}
    <Route
      path="reports/day-end"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CASHIER', 'SALES_STAFF']}
        >
          <DayEndReport />
        </ProtectedRoute>
      }
    />

    {/* Outstanding Payments Report */}
    <Route
      path="reports/outstanding"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}
        >
          <OutstandingPaymentsReport />
        </ProtectedRoute>
      }
    />

    {/* Print templates index — directory of all printable docs */}
    <Route
      path="print"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'CASHIER', 'SALES_STAFF']}
        >
          <PrintPage />
        </ProtectedRoute>
      }
    />
  </>
);
