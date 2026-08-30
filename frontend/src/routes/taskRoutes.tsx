// Tasks & SOPs + self-service + attendance routes. Moved verbatim from App.tsx
// (route-registry split); paths, elements and role gates are unchanged.
import { lazy } from 'react';
import { Route } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';

const TaskManagementPage = lazy(() => import('../pages/tasks/TaskManagementPage').then(m => ({ default: m.TaskManagementPage })));
const TasksDashboard = lazy(() => import('../pages/tasks/TasksDashboard').then(m => ({ default: m.TasksDashboard })));
const EmployeeSelfServicePage = lazy(() => import('../pages/hr/EmployeeSelfService').then(m => ({ default: m.EmployeeSelfService })));
const AttendancePage = lazy(() => import('../pages/attendance/AttendancePage').then(m => ({ default: m.AttendancePage })));

export const taskRoutes = (
  <>
    {/* Tasks & SOPs */}
    <Route
      path="tasks"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}>
          <TaskManagementPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="tasks/checklists"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'SALES_STAFF']}>
          <TasksDashboard />
        </ProtectedRoute>
      }
    />

    {/* My Work — mobile-first employee self-service. Open to EVERY
        operational role (incl. floor staff). Reads only the
        caller's OWN data via /hr/me/* (own attendance / payslip /
        commission / leave balance). Path is intentionally NOT
        under /hr (which is module-gated to managers) so it is
        ungated at the module level; ProtectedRoute lists the staff
        roles explicitly. */}
    <Route
      path="my-work"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'OPTOMETRIST', 'CASHIER', 'SALES_STAFF', 'WORKSHOP_STAFF']}
        >
          <EmployeeSelfServicePage />
        </ProtectedRoute>
      }
    />

    {/* Attendance — its own top-level page (was an HR tab).
        Open to all operational roles: managers get the monthly
        grid + admin edit, floor staff get the self check-in card.
        The grid + edit are further role-gated inside the page. */}
    <Route
      path="attendance"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'OPTOMETRIST', 'CASHIER', 'SALES_STAFF', 'WORKSHOP_STAFF']}
        >
          <AttendancePage />
        </ProtectedRoute>
      }
    />
  </>
);
