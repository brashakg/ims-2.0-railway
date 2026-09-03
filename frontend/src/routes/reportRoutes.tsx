// Reports + print routes.
//
// Wave 2 split: the old /reports tab container (ReportsPage) is now a layout
// with one REAL page per section:
//   /reports/sales · /reports/inventory · /reports/customers ·
//   /reports/gst · /reports/forecast
// plus the two GST returns lifted out of their modals onto full, bookmarkable
// pages the accountant can read a number off: /reports/gstr1 · /reports/gstr3b
// Legacy /reports?tab=<x> deep-links (bookmarks, the module launcher in
// ModuleContext, old builds) forward via ReportsTabRedirect below.
//
// Role gates are copied CHARACTER-FOR-CHARACTER off the old /reports route
// so a bisect can tell a permission change from a file move.
import { lazy } from 'react';
import { Route, Navigate, useSearchParams } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';
import { legacyTabTarget } from '../pages/reports/legacyTabRedirect';
import type { UserRole } from '../types';

const ReportsLayout = lazy(() => import('../pages/reports/ReportsLayout').then(m => ({ default: m.ReportsLayout })));
const ReportsSalesPage = lazy(() => import('../pages/reports/ReportsSalesPage').then(m => ({ default: m.ReportsSalesPage })));
const ReportsInventoryPage = lazy(() => import('../pages/reports/ReportsInventoryPage').then(m => ({ default: m.ReportsInventoryPage })));
const ReportsCustomersPage = lazy(() => import('../pages/reports/ReportsCustomersPage').then(m => ({ default: m.ReportsCustomersPage })));
const ReportsGstPage = lazy(() => import('../pages/reports/ReportsGstPage').then(m => ({ default: m.ReportsGstPage })));
const ReportsForecastPage = lazy(() => import('../pages/reports/ReportsForecastPage').then(m => ({ default: m.ReportsForecastPage })));
const GSTR1Page = lazy(() => import('../pages/reports/GSTR1Page').then(m => ({ default: m.GSTR1Page })));
const GSTR3BPage = lazy(() => import('../pages/reports/GSTR3BPage').then(m => ({ default: m.GSTR3BPage })));
const GrowthBlueprintPage = lazy(() => import('../pages/reports/GrowthBlueprintPage').then(m => ({ default: m.GrowthBlueprintPage })));
const DayEndReport = lazy(() => import('../pages/reports/DayEndReport'));
const OutstandingPaymentsReport = lazy(() => import('../pages/reports/OutstandingPaymentsReport'));
const PrintPage = lazy(() => import('../pages/print/PrintPage'));

// The module gate for the section pages — identical to the old /reports gate.
const REPORTS_ROLES: UserRole[] = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'];

// Legacy ?tab= mapper — logic in pages/reports/legacyTabRedirect.ts so it is
// testable without mounting the router. The old page's allow-list was SHORT
// BY ONE ('forecast' missing, so /reports?tab=forecast silently landed on
// Sales); that live bug is fixed there.
function ReportsTabRedirect() {
  const [searchParams] = useSearchParams();
  return <Navigate to={legacyTabTarget(searchParams)} replace />;
}

export const reportRoutes = (
  <>
    {/* Reports module — layout + one page per section */}
    <Route
      path="reports"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}
        >
          <ReportsLayout />
        </ProtectedRoute>
      }
    >
      {/* Bare /reports (incl. legacy ?tab= links) → the right section */}
      <Route index element={<ReportsTabRedirect />} />
      <Route
        path="sales"
        element={
          <ProtectedRoute allowedRoles={REPORTS_ROLES}>
            <ReportsSalesPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="inventory"
        element={
          <ProtectedRoute allowedRoles={REPORTS_ROLES}>
            <ReportsInventoryPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="customers"
        element={
          <ProtectedRoute allowedRoles={REPORTS_ROLES}>
            <ReportsCustomersPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="gst"
        element={
          <ProtectedRoute allowedRoles={REPORTS_ROLES}>
            <ReportsGstPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="forecast"
        element={
          <ProtectedRoute allowedRoles={REPORTS_ROLES}>
            <ReportsForecastPage />
          </ProtectedRoute>
        }
      />
    </Route>

    {/* GST returns — full pages, outside the layout: no KPI strip and no
        second scroll container between the accountant and the figure he
        types into the portal. Same gate as the rest of Reports. */}
    <Route
      path="reports/gstr1"
      element={
        <ProtectedRoute allowedRoles={REPORTS_ROLES}>
          <GSTR1Page />
        </ProtectedRoute>
      }
    />
    <Route
      path="reports/gstr3b"
      element={
        <ProtectedRoute allowedRoles={REPORTS_ROLES}>
          <GSTR3BPage />
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
