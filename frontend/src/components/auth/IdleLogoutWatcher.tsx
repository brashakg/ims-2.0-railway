// ============================================================================
// IMS 2.0 - Idle auto-logout watcher
// ============================================================================
// Rendered once inside the authenticated Shell (AppLayout). On mount it loads
// the runtime session policy from /health, then arms the useIdleLogout hook.
// When the idle threshold is crossed it signs the user out and routes to
// /login?reason=idle. During the warning window it shows an accessible modal
// with a live MM:SS countdown and "Stay signed in" / "Sign out now" actions.
//
// Non-destructive logout: an in-progress POS sale is AUTO-PARKED to the held-
// bills queue BEFORE logout fires (logout's clearAllOnLogout then clears only
// the in-progress draft, never the parked carts). So the cashier's cart is
// never lost on an idle logout -- it can be recalled after re-login. The
// warning modal still gives staff a heads-up to click "Stay signed in".

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { useIdleLogout } from '../../hooks/useIdleLogout';
import { usePOSStore } from '../../stores/posStore';
import { loadSessionPolicy, getSessionPolicy, type SessionPolicy } from '../../constants/sessionPolicy';

function formatMMSS(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${String(sec).padStart(2, '0')}`;
}

export function IdleLogoutWatcher() {
  const navigate = useNavigate();
  const { logout, user } = useAuth();

  // Seed from the synchronous accessor (last-known / default) so the timer is
  // armed immediately, then refresh from /health.
  const [policy, setPolicy] = useState<SessionPolicy>(() => getSessionPolicy());

  useEffect(() => {
    let alive = true;
    loadSessionPolicy().then(() => {
      if (alive) setPolicy(getSessionPolicy());
    });
    return () => {
      alive = false;
    };
  }, []);

  const handleLogout = () => {
    // Non-destructive: AUTO-PARK any in-progress POS sale BEFORE logging out so
    // the cart is never lost (logout's clearAllOnLogout clears the draft but
    // keeps parked bills). Wrapped so a park failure can never block logout.
    try {
      const heldId = usePOSStore
        .getState()
        .parkCurrentSale({ auto: true, heldBy: user?.id });
      if (heldId) {
        // eslint-disable-next-line no-console
        console.info('[idle-logout] in-progress sale auto-parked before logout', heldId);
      }
    } catch {
      /* never let a park error block the logout */
    }
    // Clear auth state, then route to login with the idle reason. logout() is
    // async (best-effort server call) but we don't need to await it to navigate.
    void logout();
    navigate('/login?reason=idle', { replace: true });
  };

  const { warning, remainingSec, stayActive } = useIdleLogout({
    enabled: policy.enabled,
    minutes: policy.minutes,
    warnSeconds: policy.warnSeconds,
    onLogout: handleLogout,
  });

  if (!warning) return null;

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-[100] p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="idle-logout-title"
    >
      <div className="bg-white rounded-xl w-full max-w-sm shadow-2xl p-6">
        <h2 id="idle-logout-title" className="text-lg font-semibold text-gray-900">
          Still there?
        </h2>
        <p className="mt-2 text-sm text-gray-600" aria-live="polite">
          You&rsquo;ll be signed out in{' '}
          <span className="font-semibold text-gray-900">{formatMMSS(remainingSec)}</span> due to
          inactivity. Click <span className="font-semibold">Stay signed in</span> to continue —
          moving the mouse won&rsquo;t cancel this.
        </p>
        <p className="mt-2 text-xs text-gray-500">
          Any in-progress sale will be saved and can be resumed after you sign back in.
        </p>
        <div className="mt-6 flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={handleLogout}
            className="px-4 py-2 text-sm font-medium text-gray-700 rounded-lg border border-gray-300 hover:bg-gray-50 transition-colors"
          >
            Sign out now
          </button>
          <button type="button" onClick={stayActive} className="btn-primary">
            Stay signed in
          </button>
        </div>
      </div>
    </div>
  );
}

export default IdleLogoutWatcher;
