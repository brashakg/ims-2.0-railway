// POS routes. /pos is a redirect to the one-surface till at /pos/new since
// the legacy wizard was retired (owner, 2026-09-03/04); every other path and
// role gate is as it was when this moved out of App.tsx.
import { lazy } from 'react';
import { Navigate, Route, useLocation } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';

/** /pos -> /pos/new, query string intact. The legacy wizard till is retired
    (owner, 2026-09-03); staff bookmarks and the walkouts deep-link
    (walkouts/ResultPanel: ?customer_id&walkout_id&return_to) keep working
    forever. No role gate here -- /pos/new carries the same one. */
function LegacyPosRedirect() {
  const { search } = useLocation();
  return <Navigate to={`/pos/new${search}`} replace />;
}
const FootfallPage = lazy(() => import('../pages/pos/FootfallPage').then(m => ({ default: m.FootfallPage })));
const BillingSurface = lazy(() => import('../pages/pos/next/BillingSurface'));
const DeliverySurface = lazy(() => import('../pages/pos/next/DeliverySurface'));
const GeneralCounterSurface = lazy(() => import('../pages/pos/next/GeneralCounterSurface'));

export const posRoutes = (
  <>
    <Route path="pos" element={<LegacyPosRedirect />} />

    {/* POS Wave 4: the general (non-optical) counter - sunglasses, solutions,
        accessories. No Rx panel and no workshop job; the same order API, GST
        math and discount caps. Prompts back to /pos/new when the sale turns
        out to be optical, so that route must stay. Same role gate as /pos/new. */}
    <Route
      path="pos/counter"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'CASHIER', 'SALES_STAFF']}
        >
          <GeneralCounterSurface />
        </ProtectedRoute>
      }
    />

    {/* POS Wave 4: the one-surface register. /pos redirects here. */}
    <Route
      path="pos/new"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'CASHIER', 'SALES_STAFF']}
        >
          <BillingSurface />
        </ProtectedRoute>
      }
    />

    {/* POS Wave 4: delivery / pickup counter (owner spec 1-iii). Same role
        gate as billing; HANDOVER_ROLES on the server is the real lock. */}
    <Route
      path="pos/delivery"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'CASHIER', 'SALES_STAFF']}
        >
          <DeliverySurface />
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
