// HR + payroll routes. Moved verbatim from App.tsx (route-registry split);
// paths, elements and role gates are unchanged.
import { lazy } from 'react';
import { Route } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';

const HRPage = lazy(() => import('../pages/hr/HRPage').then(m => ({ default: m.HRPage })));
const PayrollDashboard = lazy(() => import('../pages/hr/PayrollDashboard').then(m => ({ default: m.PayrollDashboard })));
const SalarySetupPage = lazy(() => import('../pages/hr/SalarySetupPage').then(m => ({ default: m.SalarySetupPage })));
const PayrollRunPage = lazy(() => import('../pages/hr/PayrollRunPage').then(m => ({ default: m.PayrollRunPage })));

export const hrRoutes = (
  <>
    {/* HR */}
    <Route
      path="hr"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}
        >
          <HRPage />
        </ProtectedRoute>
      }
    />

    <Route
      path="hr/payroll"
      element={(
        <ProtectedRoute allowedRoles={["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT"]}>
          <PayrollDashboard />
        </ProtectedRoute>
      )}
    />
    <Route
      path="hr/salary-setup"
      element={(
        <ProtectedRoute allowedRoles={["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT"]}>
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
