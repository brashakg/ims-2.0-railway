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

type Gate = Omit<ProtectedRouteProps, 'children'>;
type GateAuth = Pick<ReturnType<typeof useAuth>, 'hasRole' | 'hasPermission' | 'hasModuleAccess'>;

/** THE route gate: may this signed-in user open `pathname` behind `gate`?
 *  ProtectedRoute redirects to /unauthorized when it says no, and a parent
 *  breadcrumb is a link only when it says yes (AppLayout) -- one rule. */
export function gateAllows(
  auth: GateAuth,
  { allowedRoles, requirePermission, requireModule }: Gate,
  pathname: string,
): boolean {
  // Role-based access
  if (allowedRoles && allowedRoles.length > 0 && !auth.hasRole(allowedRoles)) return false;
  // Permission-based access
  if (requirePermission && !auth.hasPermission(requirePermission)) return false;
  // Per-user module access (deny-only override on top of the role above).
  // The role check already ran, so this can only FURTHER restrict -- a denied
  // module redirects to /unauthorized even via a direct URL, closing the gap
  // that nav-hiding alone leaves open. `requireModule` (when passed) wins;
  // otherwise the module is derived from the path. `requireModule === null`
  // (explicit opt-out) and ungated paths (moduleForPath -> null) skip the gate.
  const gatedModule = requireModule !== undefined ? requireModule : moduleForPath(pathname);
  return !gatedModule || auth.hasModuleAccess(gatedModule);
}

export function ProtectedRoute({
  children,
  allowedRoles,
  requirePermission,
  requireModule,
}: ProtectedRouteProps) {
  const auth = useAuth();
  const { isAuthenticated, isLoading } = auth;
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

  if (!gateAllows(auth, { allowedRoles, requirePermission, requireModule }, location.pathname)) {
    return <Navigate to="/unauthorized" state={{ from: location }} replace />;
  }

  return <>{children}</>;
}

export default ProtectedRoute;
