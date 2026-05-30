// ============================================================================
// IMS 2.0 - Protected Route Component
// ============================================================================

import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { moduleForPath, type ModuleKey } from '../../context/ModuleContext';
import type { UserRole } from '../../types';

interface ProtectedRouteProps {
  children: React.ReactNode;
  allowedRoles?: UserRole[];
  requirePermission?: string;
  /** Override the module gate for this route. When omitted, the module is
   *  derived from the current path via moduleForPath. Pass `null` to opt a
   *  route OUT of module gating entirely (rare). */
  requireModule?: ModuleKey | null;
}

export function ProtectedRoute({
  children,
  allowedRoles,
  requirePermission,
  requireModule,
}: ProtectedRouteProps) {
  const { isAuthenticated, isLoading, hasRole, hasPermission, hasModuleAccess } = useAuth();
  const location = useLocation();

  // Show loading state - don't render children until auth initialization completes
  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50" role="status" aria-live="polite" aria-label="Loading application">
        <div className="text-center">
          <div className="w-12 h-12 border-4 border-bv-red-600 border-t-transparent rounded-full animate-spin mx-auto" aria-hidden="true"></div>
          <p className="mt-4 text-gray-600">Initializing application...</p>
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

  // Check per-user module access (deny-only override on top of the role above).
  // The role check already ran, so this can only FURTHER restrict -- a denied
  // module redirects to /unauthorized even via a direct URL, closing the gap
  // that nav-hiding alone leaves open. `requireModule` (when passed) wins;
  // otherwise the module is derived from the path. `requireModule === null`
  // (explicit opt-out) and ungated paths (moduleForPath -> null) skip the gate.
  const gatedModule =
    requireModule !== undefined ? requireModule : moduleForPath(location.pathname);
  if (gatedModule && !hasModuleAccess(gatedModule)) {
    return <Navigate to="/unauthorized" state={{ from: location }} replace />;
  }

  return <>{children}</>;
}

export default ProtectedRoute;
