// Workshop routes. Moved verbatim from App.tsx (route-registry split);
// paths, elements and role gates are unchanged.
import { lazy } from 'react';
import { Route } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';

const WorkshopPage = lazy(() => import('../pages/workshop/WorkshopPage').then(m => ({ default: m.WorkshopPage })));
const StationScanPage = lazy(() => import('../pages/workshop/StationScanPage').then(m => ({ default: m.StationScanPage })));

export const workshopRoutes = (
  <>
    {/* Workshop */}
    <Route
      path="workshop"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'WORKSHOP_STAFF']}
        >
          <WorkshopPage />
        </ProtectedRoute>
      }
    />

    {/* F2 -- fullscreen lab-bench station scan terminal. CASHIER is
        included for the front-desk PICKUP scan (mirrors the backend
        _LAB_SCAN_ROLES gate). */}
    <Route
      path="workshop/station/:stationCode"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'WORKSHOP_STAFF', 'CASHIER']}
        >
          <StationScanPage />
        </ProtectedRoute>
      }
    />
  </>
);
