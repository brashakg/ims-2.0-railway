// ============================================================================
// IMS 2.0 - Main Application Entry
// ============================================================================

import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Suspense, lazy } from 'react';
import { AuthProvider } from './context/AuthContext';
import { ToastProvider } from './context/ToastContext';
import { ModuleProvider } from './context/ModuleContext';
import { AppLayout } from './components/layout/AppLayout';
import { ProtectedRoute } from './components/layout/ProtectedRoute';

// Lazy load pages for code splitting
const LoginPage = lazy(() => import('./pages/auth/LoginPage').then(m => ({ default: m.LoginPage })));
const DashboardPage = lazy(() => import('./pages/dashboard/DashboardPage'));
const POSPage = lazy(() => import('./pages/pos/POSPage').then(m => ({ default: m.POSPage })));
const CustomersPage = lazy(() => import('./pages/customers/CustomersPage').then(m => ({ default: m.CustomersPage })));
const InventoryPage = lazy(() => import('./pages/inventory/InventoryPage').then(m => ({ default: m.InventoryPage })));
const OrdersPage = lazy(() => import('./pages/orders/OrdersPage').then(m => ({ default: m.OrdersPage })));
const ClinicalPage = lazy(() => import('./pages/clinical/ClinicalPage').then(m => ({ default: m.ClinicalPage })));
const WorkshopPage = lazy(() => import('./pages/workshop/WorkshopPage').then(m => ({ default: m.WorkshopPage })));
const TasksPage = lazy(() => import('./pages/tasks/TasksPage').then(m => ({ default: m.TasksPage })));
const HRPage = lazy(() => import('./pages/hr/HRPage').then(m => ({ default: m.HRPage })));
const ReportsPage = lazy(() => import('./pages/reports/ReportsPage').then(m => ({ default: m.ReportsPage })));
const SettingsPage = lazy(() => import('./pages/settings/SettingsPage').then(m => ({ default: m.SettingsPage })));

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
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <ModuleProvider>
          <ToastProvider>
            <BrowserRouter>
              <Suspense fallback={<PageLoader />}>
                <Routes>
                  {/* Public routes */}
                  <Route path="/login" element={<LoginPage />} />
                  <Route path="/unauthorized" element={<UnauthorizedPage />} />

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

                  {/* Dashboard */}
                  <Route path="dashboard" element={<DashboardPage />} />

                  {/* POS */}
                  <Route
                    path="pos"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'SALES_CASHIER', 'SALES_STAFF']}
                      >
                        <POSPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* Customers */}
                  <Route
                    path="customers"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'SALES_CASHIER', 'SALES_STAFF']}
                      >
                        <CustomersPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* Inventory */}
                  <Route
                    path="inventory"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER', 'WORKSHOP_STAFF']}
                      >
                        <InventoryPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* Orders */}
                  <Route
                    path="orders"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'SALES_CASHIER']}
                      >
                        <OrdersPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* Clinical / Eye Tests */}
                  <Route
                    path="clinical"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST']}
                      >
                        <ClinicalPage />
                      </ProtectedRoute>
                    }
                  />

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

                  {/* Tasks */}
                  <Route path="tasks" element={<TasksPage />} />

                  {/* HR */}
                  <Route
                    path="hr"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}
                      >
                        <HRPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* Reports */}
                  <Route
                    path="reports"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}
                      >
                        <ReportsPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* Settings */}
                  <Route
                    path="settings"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN']}>
                        <SettingsPage />
                      </ProtectedRoute>
                    }
                  />
                </Route>

                  {/* 404 */}
                  <Route path="*" element={<NotFoundPage />} />
                </Routes>
              </Suspense>
            </BrowserRouter>
          </ToastProvider>
        </ModuleProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}

export default App;
