// Dashboard + notifications routes. Moved verbatim from App.tsx (route-registry
// split); paths, elements and role gates are unchanged.
import { lazy } from 'react';
import { Route } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';

const DashboardPage = lazy(() => import('../pages/dashboard/HubPage'));
const NotificationsPage = lazy(() => import('../pages/notifications/NotificationsPage'));
const ExecutiveDashboard = lazy(() => import('../pages/dashboard/ExecutiveDashboard').then(m => ({ default: m.ExecutiveDashboard })));
const EnterpriseAnalyticsDashboard = lazy(() => import('../pages/dashboard/EnterpriseAnalyticsDashboard'));

export const dashboardRoutes = (
  <>
    {/* Dashboard */}
    <Route path="dashboard" element={<DashboardPage />} />

    {/* Notifications (any authenticated user) */}
    <Route path="notifications" element={<ProtectedRoute><NotificationsPage /></ProtectedRoute>} />

    <Route
      path="dashboard/executive"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER']}>
          <ExecutiveDashboard />
        </ProtectedRoute>
      }
    />
    <Route
      path="dashboard/analytics"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER']}>
          <EnterpriseAnalyticsDashboard />
        </ProtectedRoute>
      }
    />
  </>
);
