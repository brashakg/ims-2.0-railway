// Dashboard + notifications routes. Moved verbatim from App.tsx (route-registry
// split); paths, elements and role gates are unchanged.
import { lazy } from 'react';
import { Route } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';

const DashboardPage = lazy(() => import('../pages/dashboard/HubPage'));
const NotificationsPage = lazy(() => import('../pages/notifications/NotificationsPage'));

export const dashboardRoutes = (
  <>
    {/* Dashboard */}
    <Route path="dashboard" element={<DashboardPage />} />

    {/* Notifications (any authenticated user) */}
    <Route path="notifications" element={<ProtectedRoute><NotificationsPage /></ProtectedRoute>} />

  </>
);
