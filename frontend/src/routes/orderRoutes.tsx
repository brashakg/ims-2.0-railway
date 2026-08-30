// Orders, estimates, returns + walkouts routes. Moved verbatim from App.tsx
// (route-registry split); paths, elements and role gates are unchanged.
import { lazy } from 'react';
import { Route } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';

const OrdersPage = lazy(() => import('../pages/orders/OrdersPage').then(m => ({ default: m.OrdersPage })));
const ReturnsPage = lazy(() => import('../pages/orders/ReturnsPage'));
const EstimatesPage = lazy(() => import('../pages/orders/EstimatesPage').then(m => ({ default: m.EstimatesPage })));
const WalkoutsPage = lazy(() => import('../pages/walkouts/WalkoutsPage').then(m => ({ default: m.WalkoutsPage })));
const WalkoutDetailPage = lazy(() => import('../pages/walkouts/WalkoutDetailPage').then(m => ({ default: m.WalkoutDetailPage })));
const WalkoutsDashboardPage = lazy(() => import('../pages/walkouts/WalkoutsDashboardPage').then(m => ({ default: m.WalkoutsDashboardPage })));

export const orderRoutes = (
  <>
    {/* Orders */}
    <Route
      path="orders"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CASHIER', 'SALES_STAFF', 'OPTOMETRIST', 'WORKSHOP_STAFF']}
        >
          <OrdersPage />
        </ProtectedRoute>
      }
    />

    {/* Estimates / Quotations (non-binding priced quotes) */}
    <Route
      path="estimates"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'SALES_STAFF']}
        >
          <EstimatesPage />
        </ProtectedRoute>
      }
    />

    {/* Walkouts (Pune Incentive Module i) */}
    <Route
      path="walkouts"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'SALES_STAFF', 'CASHIER']}
        >
          <WalkoutsPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="walkouts/dashboard"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}
        >
          <WalkoutsDashboardPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="walkouts/:walkoutId"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'SALES_STAFF', 'CASHIER']}
        >
          <WalkoutDetailPage />
        </ProtectedRoute>
      }
    />

    {/* Returns & Exchanges */}
    <Route
      path="returns"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'CASHIER', 'SALES_STAFF']}
        >
          <ReturnsPage />
        </ProtectedRoute>
      }
    />
  </>
);
