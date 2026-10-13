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
  /** One line the blocked page shows above the list of who CAN open this
   *  route (e.g. "Only managers receive goods into stock."). */
  deniedHint?: string;
  /** false on a section LAYOUT's gate whose sections are narrower than it
   *  (/purchase: the accountant may not open vendor returns, workshop staff
   *  may not open orders). Its list is the union of every section's, so naming
   *  it on the blocked page told people to hand the work to roles the section
   *  itself refuses. Leave it on where every section shares the layout's list
   *  (inventory, reports) -- that list is the right one to name. */
  namesWhoCan?: boolean;
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
  deniedHint,
  namesWhoCan = true,
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
    // A role refusal tells the blocked page who CAN open this, so it can name them.
    const roleRefused = !!allowedRoles?.length && !auth.hasRole(allowedRoles);
    const state = roleRefused
      ? { from: location, allowedRoles: namesWhoCan ? allowedRoles : undefined, deniedHint }
      : { from: location };
    return <Navigate to="/unauthorized" state={state} replace />;
  }

  return <>{children}</>;
}

export default ProtectedRoute;
