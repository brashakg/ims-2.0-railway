// ============================================================================
// IMS 2.0 - Main Application Entry
// ============================================================================

import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider } from './context/AuthContext';
import { ToastProvider } from './context/ToastContext';
import { ModuleProvider } from './context/ModuleContext';
import { AppLayout } from './components/layout/AppLayout';
import { ProtectedRoute } from './components/layout/ProtectedRoute';

// Page imports
import { LoginPage } from './pages/auth/LoginPage';
import DashboardPage from './pages/dashboard/DashboardPage';
import { POSPage } from './pages/pos/POSPage';
import { CustomersPage } from './pages/customers/CustomersPage';
import { InventoryPage } from './pages/inventory/InventoryPage';
import { OrdersPage } from './pages/orders/OrdersPage';
import { ClinicalPage } from './pages/clinical/ClinicalPage';
import { WorkshopPage } from './pages/workshop/WorkshopPage';
import { TasksPage } from './pages/tasks/TasksPage';
import { HRPage } from './pages/hr/HRPage';
import { ReportsPage } from './pages/reports/ReportsPage';
import { SettingsPage } from './pages/settings/SettingsPage';

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
            </BrowserRouter>
          </ToastProvider>
        </ModuleProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}

export default App;
