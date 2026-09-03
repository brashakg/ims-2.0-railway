// Tasks & SOPs + self-service + attendance routes.
//
// Wave 2 split: the two rival task mega-pages are gone. /tasks (TaskManagement,
// 4 tabs) and /tasks/checklists (TasksDashboard, 3 tabs) were two addresses
// doing the same job with OPPOSITE permission lists; between them they held
// seven tabs in useState and not one bookmarkable section. They are replaced by
// a layout with one REAL page per section:
//   /tasks/mine · /tasks/team · /tasks/checklists · /tasks/sops ·
//   /tasks/performance
//
// /tasks/checklists KEEPS its address exactly - the Hub links it. What changed
// there is the page contents (one checklist, not three rival tabs).
//
// Role gates come from ONE list, pages/tasks/taskRoles.ts (owner ruling
// 2026-09-03: team tasks are managers and above). The three contradicting
// copies that used to live here, in TasksDashboard and in TaskManagementPage
// are deleted, not synced.
import { lazy } from 'react';
import { Route, Navigate } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';
import { TASK_MODULE_ROLES, TEAM_TASK_ROLES } from '../pages/tasks/taskRoles';

const TasksLayout = lazy(() => import('../pages/tasks/TasksLayout').then(m => ({ default: m.TasksLayout })));
const TasksMinePage = lazy(() => import('../pages/tasks/TasksMinePage').then(m => ({ default: m.TasksMinePage })));
const TasksTeamPage = lazy(() => import('../pages/tasks/TasksTeamPage').then(m => ({ default: m.TasksTeamPage })));
const TasksChecklistPage = lazy(() => import('../pages/tasks/TasksChecklistPage').then(m => ({ default: m.TasksChecklistPage })));
const TasksSopPage = lazy(() => import('../pages/tasks/TasksSopPage').then(m => ({ default: m.TasksSopPage })));
const TasksPerformancePage = lazy(() => import('../pages/tasks/TasksPerformancePage').then(m => ({ default: m.TasksPerformancePage })));
const EmployeeSelfServicePage = lazy(() => import('../pages/hr/EmployeeSelfService').then(m => ({ default: m.EmployeeSelfService })));
const AttendancePage = lazy(() => import('../pages/attendance/AttendancePage').then(m => ({ default: m.AttendancePage })));

export const taskRoutes = (
  <>
    {/* Tasks & SOPs — layout + one page per section */}
    <Route
      path="tasks"
      element={
        <ProtectedRoute allowedRoles={TASK_MODULE_ROLES}>
          <TasksLayout />
        </ProtectedRoute>
      }
    >
      {/* Bare /tasks — every role can open Mine, so that is the landing. */}
      <Route index element={<Navigate to="/tasks/mine" replace />} />
      <Route
        path="mine"
        element={
          <ProtectedRoute allowedRoles={TASK_MODULE_ROLES}>
            <TasksMinePage />
          </ProtectedRoute>
        }
      />
      <Route
        path="team"
        element={
          <ProtectedRoute allowedRoles={TEAM_TASK_ROLES}>
            <TasksTeamPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="checklists"
        element={
          <ProtectedRoute allowedRoles={TASK_MODULE_ROLES}>
            <TasksChecklistPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="sops"
        element={
          <ProtectedRoute allowedRoles={TEAM_TASK_ROLES}>
            <TasksSopPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="performance"
        element={
          <ProtectedRoute allowedRoles={TEAM_TASK_ROLES}>
            <TasksPerformancePage />
          </ProtectedRoute>
        }
      />
      {/* Legacy address the HR module launcher still links (ModuleContext
          `hr-tasks` -> /tasks/dashboard). It has never existed as a route and
          404'd; it means "the tasks list", so send it to Mine. */}
      <Route path="dashboard" element={<Navigate to="/tasks/mine" replace />} />
    </Route>

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
