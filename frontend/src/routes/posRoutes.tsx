// POS routes. Moved verbatim from App.tsx (route-registry split);
// paths, elements and role gates are unchanged.
import { lazy } from 'react';
import { Route } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';

const POSPage = lazy(() => import('../pages/pos/POSPage').then(m => ({ default: m.POSPage })));
const FootfallPage = lazy(() => import('../pages/pos/FootfallPage').then(m => ({ default: m.FootfallPage })));

export const posRoutes = (
  <>
    {/* POS */}
    <Route
      path="pos"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'CASHIER', 'SALES_STAFF']}
        >
          <POSPage />
        </ProtectedRoute>
      }
    />

    {/* POS: Footfall Tracking (N3 — manual walk-in capture + conversion %) */}
    <Route
      path="pos/footfall"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CASHIER', 'SALES_STAFF', 'OPTOMETRIST']}
        >
          <FootfallPage />
        </ProtectedRoute>
      }
    />
  </>
);
