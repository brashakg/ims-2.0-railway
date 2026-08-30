// Incentive (Pune Module ii) routes. Moved verbatim from App.tsx
// (route-registry split); paths, elements and role gates are unchanged.
import { lazy } from 'react';
import { Route } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';

const DailyScorecardPage = lazy(() => import('../pages/incentive/DailyScorecardPage').then(m => ({ default: m.DailyScorecardPage })));
const MTDLeaderboardPage = lazy(() => import('../pages/incentive/MTDLeaderboardPage').then(m => ({ default: m.MTDLeaderboardPage })));
const PointsHistoryPage = lazy(() => import('../pages/incentive/PointsHistoryPage').then(m => ({ default: m.PointsHistoryPage })));
const PayoutDashboardPage = lazy(() => import('../pages/incentive/PayoutDashboardPage').then(m => ({ default: m.PayoutDashboardPage })));
const PayoutSnapshotsPage = lazy(() => import('../pages/incentive/PayoutSnapshotsPage').then(m => ({ default: m.PayoutSnapshotsPage })));
const IncentiveSettingsPage = lazy(() => import('../pages/incentive/IncentiveSettingsPage').then(m => ({ default: m.IncentiveSettingsPage })));

export const incentiveRoutes = (
  <>
    {/* Pune Incentive Module ii — Daily Points */}
    <Route
      path="incentive"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'SALES_STAFF', 'CASHIER']}
        >
          <DailyScorecardPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="incentive/leaderboard"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}
        >
          <MTDLeaderboardPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="incentive/staff/:staffId"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}
        >
          <PointsHistoryPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="incentive/payout"
      element={
        // OWNER DECISION 2026-08-13: a payout body lists NAMED
        // colleagues with their per-person incentive rupees,
        // which is a payslip line. Backend /payout/* reads are
        // now ADMIN/SUPERADMIN only; this matches so the screen
        // is never reachable-but-403.
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN']}>
          <PayoutDashboardPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="incentive/payouts"
      element={
        // OWNER DECISION 2026-08-13: a payout body lists NAMED
        // colleagues with their per-person incentive rupees,
        // which is a payslip line. Backend /payout/* reads are
        // now ADMIN/SUPERADMIN only; this matches so the screen
        // is never reachable-but-403.
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN']}>
          <PayoutSnapshotsPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="incentive/settings"
      element={
        // SUPERADMIN-only to match the backend: the
        // /incentive/points/settings/* PATCH endpoints are
        // SUPERADMIN-only by design (points.py), so other roles
        // previously saw a fully editable page where every Save
        // 403'd. (Widen the backend if ADMIN should manage these.)
        <ProtectedRoute
          allowedRoles={['SUPERADMIN']}
        >
          <IncentiveSettingsPage />
        </ProtectedRoute>
      }
    />
  </>
);
