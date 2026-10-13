// ============================================================================
// IMS 2.0 - 403 page that says who CAN do this
// ============================================================================
// ProtectedRoute sends a blocked person here with the route's allowed roles
// (and, for some routes, a one-line hint). A bare "you don't have permission"
// left the person who opened the parcel with nowhere to go (procurement audit
// F5); naming who can do it tells them whom to hand it to.

import { useLocation } from 'react-router-dom';

function roleLabel(role: string): string {
  const words = role.toLowerCase().replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function UnauthorizedPage() {
  const state = (useLocation().state ?? {}) as { allowedRoles?: string[]; deniedHint?: string };
  const who = (state.allowedRoles ?? []).filter((r) => r !== 'SUPERADMIN').map(roleLabel);

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
      <div className="text-center max-w-md">
        <h1 className="text-4xl font-bold text-gray-900 mb-2">403</h1>
        <p className="text-gray-500 mb-2">You don't have permission to access this page.</p>
        {state.deniedHint && <p className="text-gray-700 mb-2">{state.deniedHint}</p>}
        {who.length > 0 && (
          <p className="text-gray-700 mb-4">Who can: {who.join(', ')}.</p>
        )}
        <a href="/dashboard" className="btn-primary">
          Go to Dashboard
        </a>
      </div>
    </div>
  );
}

export default UnauthorizedPage;
