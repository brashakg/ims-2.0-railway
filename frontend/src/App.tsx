// ============================================================================
// IMS 2.0 - Main Application Entry
//
// Route registry split (2026-08-30): module routes live in src/routes/*.tsx —
// one small file per module, each owning its lazy imports and role gates.
// This file keeps only the app shell (providers), the public routes, and the
// composition of the per-module route groups. Paths and gates are unchanged.
// ============================================================================

import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Suspense, lazy } from 'react';
import { AuthProvider } from './context/AuthContext';
import { ToastProvider } from './context/ToastContext';
import { ModuleProvider } from './context/ModuleContext';
import { AppearanceProvider } from './context/AppearanceContext';
import { AppLayout } from './components/layout/AppLayout';
import { ProtectedRoute } from './components/layout/ProtectedRoute';
import { ErrorBoundary } from './components/layout/ErrorBoundary';
import { SessionExpiryWarning } from './components/common/SessionExpiryWarning';
// SpeedInsights removed — INP overlay disrupts user experience in production
// import { SpeedInsights } from '@vercel/speed-insights/react';
import { Analytics } from '@vercel/analytics/react';

import { dashboardRoutes } from './routes/dashboardRoutes';
import { approvalRoutes } from './routes/approvalRoutes';
import { posRoutes } from './routes/posRoutes';
import { customerRoutes } from './routes/customerRoutes';
import { onlineStoreRoutes } from './routes/onlineStoreRoutes';
import { inventoryRoutes } from './routes/inventoryRoutes';
import { orderRoutes } from './routes/orderRoutes';
import { incentiveRoutes } from './routes/incentiveRoutes';
import { clinicalRoutes } from './routes/clinicalRoutes';
import { workshopRoutes } from './routes/workshopRoutes';
import { purchaseRoutes } from './routes/purchaseRoutes';
import { taskRoutes } from './routes/taskRoutes';
import { hrRoutes } from './routes/hrRoutes';
import { reportRoutes } from './routes/reportRoutes';
import { settingsRoutes } from './routes/settingsRoutes';
import { catalogRoutes } from './routes/catalogRoutes';
import { financeRoutes } from './routes/financeRoutes';

// Public / shell pages
const LoginPage = lazy(() => import('./pages/auth/LoginPage').then(m => ({ default: m.LoginPage })));
const StoreSelectPage = lazy(() => import('./pages/auth/StoreSelectPage').then(m => ({ default: m.StoreSelectPage })));
const VendorPortalPage = lazy(() => import('./pages/vendor-portal/VendorPortalPage'));
const OrderTrackingPage = lazy(() => import('./pages/portal/OrderTrackingPage'));
const RxPortalPage = lazy(() => import('./pages/portal/RxPortalPage'));

// Loading fallback component
const PageLoader = () => (
  <div className="flex items-center justify-center h-64">
    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
  </div>
);

// Create React Query client
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5, // 5 minutes
      retry: 1,
    },
  },
});

// Unauthorized page
const UnauthorizedPage = () => (
  <div className="min-h-screen flex items-center justify-center bg-gray-50">
    <div className="text-center">
      <h1 className="text-4xl font-bold text-gray-900 mb-2">403</h1>
      <p className="text-gray-500 mb-4">You don't have permission to access this page.</p>
      <a href="/dashboard" className="btn-primary">
        Go to Dashboard
      </a>
    </div>
  </div>
);

// Not Found page
const NotFoundPage = () => (
  <div className="min-h-screen flex items-center justify-center bg-gray-50">
    <div className="text-center">
      <h1 className="text-4xl font-bold text-gray-900 mb-2">404</h1>
      <p className="text-gray-500 mb-4">Page not found.</p>
      <a href="/dashboard" className="btn-primary">
        Go to Dashboard
      </a>
    </div>
  </div>
);

function App() {
  return (
    <ErrorBoundary>
      <AppearanceProvider>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <ModuleProvider>
            <ToastProvider>
              <BrowserRouter>
                <SessionExpiryWarning />
                <Suspense fallback={<PageLoader />}>
                <Routes>
                  {/* Public routes */}
                  <Route path="/login" element={<LoginPage />} />
                  <Route path="/unauthorized" element={<UnauthorizedPage />} />
                  {/* Vendor Portal — public, token-auth via URL.
                      Mounted OUTSIDE ProtectedRoute because external lens
                      labs hit this without an IMS user account. The
                      tokenId in the URL IS the auth (server-side check). */}
                  <Route path="/vendor-portal/:tokenId" element={<VendorPortalPage />} />
                  {/* Customer self-service — public, no AppLayout/auth.
                      Order tracking is a tokenized link; Rx viewing is
                      OTP-gated (medical data). See pages/portal/. */}
                  <Route path="/track/:token" element={<OrderTrackingPage />} />
                  <Route path="/rx-portal" element={<RxPortalPage />} />

                {/* Post-login store selector — auth-gated but rendered FULL-SCREEN
                    (no AppLayout shell). Multi-store roles land here after login to
                    pick the active store; single-store users auto-proceed. Kept
                    OUTSIDE the AppLayout route so its guard can redirect here
                    without a loop. */}
                <Route
                  path="/select-store"
                  element={
                    <ProtectedRoute>
                      <StoreSelectPage />
                    </ProtectedRoute>
                  }
                />

                {/* Protected routes with layout */}
                <Route
                  path="/"
                  element={
                    <ProtectedRoute>
                      <AppLayout />
                    </ProtectedRoute>
                  }
                >
                  {/* Redirect root to dashboard */}
                  <Route index element={<Navigate to="/dashboard" replace />} />

                  {dashboardRoutes}
                  {approvalRoutes}
                  {posRoutes}
                  {customerRoutes}
                  {onlineStoreRoutes}
                  {inventoryRoutes}
                  {orderRoutes}
                  {incentiveRoutes}
                  {clinicalRoutes}
                  {workshopRoutes}
                  {purchaseRoutes}
                  {taskRoutes}
                  {hrRoutes}
                  {reportRoutes}
                  {settingsRoutes}
                  {catalogRoutes}
                  {financeRoutes}
                </Route>

                  {/* 404 */}
                  <Route path="*" element={<NotFoundPage />} />
                </Routes>
              </Suspense>
            </BrowserRouter>
              <Analytics />
          </ToastProvider>
        </ModuleProvider>
      </AuthProvider>
    </QueryClientProvider>
    </AppearanceProvider>
    </ErrorBoundary>
  );
}

export default App;
