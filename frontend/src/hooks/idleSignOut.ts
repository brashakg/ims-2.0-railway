// The idle sign-out rule, shared by the in-session watcher
// (components/auth/IdleLogoutWatcher) and the page-load check (AuthContext).
// Kept out of useIdleLogout.ts: services/api/client imports that file, and
// posStore -> gstRuntime -> client would make an import cycle.

import { usePOSStore } from '../stores/posStore';

/** Where every idle sign-out lands (in-session watcher and page-load check). */
export const IDLE_LOGIN_PATH = '/login?reason=idle';

/**
 * The one idle sign-out step before logout(): AUTO-PARK any in-progress POS
 * sale for `userId`, THEN clear the draft. Order matters - clearing first
 * empties the cart and nothing gets parked. Never throws (a park error must
 * not block the logout). Callers then run logout() and go to IDLE_LOGIN_PATH.
 */
export function autoParkForIdleLogout(userId?: string): void {
  try {
    const pos = usePOSStore.getState();
    const heldId = pos.parkCurrentSale({ auto: true, heldBy: userId });
    if (heldId) {
      // eslint-disable-next-line no-console
      console.info('[idle-logout] in-progress sale auto-parked before logout', heldId);
    }
    pos.clearAllOnLogout();
  } catch {
    /* never let a park error block the logout */
  }
}
