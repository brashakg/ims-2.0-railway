// POS routes. Moved verbatim from App.tsx (route-registry split);
// paths, elements and role gates are unchanged.
import { lazy } from 'react';
import { Route } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';

const POSPage = lazy(() => import('../pages/pos/POSPage').then(m => ({ default: m.POSPage })));
const FootfallPage = lazy(() => import('../pages/pos/FootfallPage').then(m => ({ default: m.FootfallPage })));
const BillingSurface = lazy(() => import('../pages/pos/next/BillingSurface'));
const DeliverySurface = lazy(() => import('../pages/pos/next/DeliverySurface'));
const GeneralCounterSurface = lazy(() => import('../pages/pos/next/GeneralCounterSurface'));

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

    {/* POS Wave 4: the general (non-optical) counter - sunglasses, solutions,
        accessories. No Rx panel and no workshop job; the same order API, GST
        math and discount caps. Prompts back to /pos/new when the sale turns
        out to be optical, so that route must stay. Same role gate as /pos. */}
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

    {/* POS Wave 4: the new one-surface register, building at /pos/new.
        Same role gate as /pos. Swaps onto /pos (with the classic surface
        moving to /pos/classic) only when the owner calls the switch. */}
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
