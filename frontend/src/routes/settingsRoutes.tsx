// Settings + admin + Jarvis routes. Moved verbatim from App.tsx
// (route-registry split); paths, elements and role gates are unchanged.
import { lazy } from 'react';
import { Route, Navigate } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';

// EntitiesPage retired from routing — /settings/entities now redirects to the
// canonical /organization screen (COUNCIL RULING §3). The page file is kept for
// a later-release deletion.
const OrganizationPage = lazy(() => import('../pages/settings/OrganizationPage'));
const SettingsPage = lazy(() => import('../pages/settings/SettingsPage').then(m => ({ default: m.SettingsPage })));
const SetupPage = lazy(() => import('../pages/settings/SetupPage'));
const GoLiveChecklistPage = lazy(() => import('../pages/settings/GoLiveChecklistPage').then(m => ({ default: m.GoLiveChecklistPage })));
const JarvisPage = lazy(() => import('../pages/jarvis/JarvisPage').then(m => ({ default: m.JarvisPage })));
const ActivityLogPage = lazy(() => import('../pages/admin/ActivityLogPage'));

export const settingsRoutes = (
  <>
    {/* Settings */}
    <Route
      path="settings"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'AREA_MANAGER', 'CATALOG_MANAGER', 'ACCOUNTANT']}>
          <SettingsPage />
        </ProtectedRoute>
      }
    />
    {/* COUNCIL RULING §3: Entities are managed on the canonical
        /organization screen. Redirect the orphaned SPA route
        instead of shipping a parallel editor (delete the page a
        release later). */}
    <Route
      path="settings/entities"
      element={<Navigate to="/organization" replace />}
    />
    <Route
      path="organization"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN']}>
          <OrganizationPage />
        </ProtectedRoute>
      }
    />

    {/* Store Setup & Employee Onboarding */}
    <Route
      path="setup"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN']}>
          <SetupPage />
        </ProtectedRoute>
      }
    />

    {/* Go-Live Readiness Checklist */}
    <Route
      path="go-live"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN']}>
          <GoLiveChecklistPage />
        </ProtectedRoute>
      }
    />

    {/* AI Intelligence — Superadmin only */}
    <Route
      path="jarvis"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN']}>
          <JarvisPage />
        </ProtectedRoute>
      }
    />

    {/* User Activity Log (audit trail) — Superadmin only */}
    <Route
      path="admin/activity-log"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN']}>
          <ActivityLogPage />
        </ProtectedRoute>
      }
    />
  </>
);
