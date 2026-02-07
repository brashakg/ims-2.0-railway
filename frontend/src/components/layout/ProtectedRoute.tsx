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

  // Log route access attempts for debugging
  console.log('[ProtectedRoute]', {
    path: location.pathname,
    isLoading,
    isAuthenticated,
    allowedRoles,
    hasRequiredRole: allowedRoles ? hasRole(allowedRoles) : 'N/A',
  });

  // Show loading state - don't render children until auth initialization completes
  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="text-center">
          <div className="w-12 h-12 border-4 border-bv-red-600 border-t-transparent rounded-full animate-spin mx-auto"></div>
          <p className="mt-4 text-gray-600">Initializing...</p>
        </div>
      </div>
    );
  }

  // Redirect to login if not authenticated
  if (!isAuthenticated) {
    console.warn('[ProtectedRoute] User not authenticated, redirecting to login', {
      path: location.pathname,
    });
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  // Check role-based access
  if (allowedRoles && allowedRoles.length > 0) {
    if (!hasRole(allowedRoles)) {
      console.warn('[ProtectedRoute] User lacks required role(s)', {
        path: location.pathname,
        requiredRoles: allowedRoles,
      });
      return <Navigate to="/unauthorized" state={{ from: location }} replace />;
    }
  }

  // Check permission-based access
  if (requirePermission) {
    if (!hasPermission(requirePermission)) {
      console.warn('[ProtectedRoute] User lacks required permission', {
        path: location.pathname,
        requiredPermission: requirePermission,
      });
      return <Navigate to="/unauthorized" state={{ from: location }} replace />;
    }
  }

  return <>{children}</>;
}

export default ProtectedRoute;
