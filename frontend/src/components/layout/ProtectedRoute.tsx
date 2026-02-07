// ============================================================================
// IMS 2.0 - Protected Route Component
// ============================================================================

import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import type { UserRole } from '../../types';

interface ProtectedRouteProps {
  children: React.ReactNode;
  allowedRoles?: UserRole[];
  requirePermission?: string;
}

export function ProtectedRoute({
  children,
  allowedRoles,
  requirePermission,
}: ProtectedRouteProps) {
  const { isAuthenticated, isLoading, hasRole, hasPermission } = useAuth();
  const location = useLocation();

  // Show loading state
  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="text-center">
          <div className="w-12 h-12 border-4 border-bv-red-600 border-t-transparent rounded-full animate-spin mx-auto"></div>
          <p className="mt-4 text-gray-600">Loading...</p>
        </div>
      </div>
    );
  }

  // Redirect to login if not authenticated
  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  // Check role-based access
  if (allowedRoles && allowedRoles.length > 0) {
    if (!hasRole(allowedRoles)) {
      return <Navigate to="/unauthorized" state={{ from: location }} replace />;
    }
  }

  // Check permission-based access
  if (requirePermission) {
    if (!hasPermission(requirePermission)) {
      return <Navigate to="/unauthorized" state={{ from: location }} replace />;
    }
  }

  return <>{children}</>;
}

export default ProtectedRoute;
