// ============================================================================
// IMS 2.0 - A login with no shop sees no shop (owner ruling 2026-10-12, R4)
// ============================================================================
// "A non-admin login with NO shop assigned sees NO shop until an admin assigns
// one: empty lists and a clear 'No shop is assigned to this login' message;
// never every shop."
//
// The rule lives on the server (api.dependencies.resolve_store_scope): such a
// login's lists come back EMPTY and naming a shop is a 403 worded like
// NO_SHOP_MESSAGE. This is the matching screen: instead of an empty table with
// no reason given, the screen says why it is empty and who can fix it.

import type { ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import type { User } from '../../types';
import { userSeesAllStores } from '../../utils/storeAccess';

/** The server's words (api.dependencies.NO_SHOP_DETAIL), kept identical. */
export const NO_SHOP_MESSAGE = 'No shop is assigned to this login. Ask an admin to assign one.';

/** The reserved shop id the server scopes such a login to
 *  (api.dependencies.NO_SHOP_SCOPE) and echoes back as a report's store_id --
 *  never a real shop, so never shown as one. */
export const NO_SHOP_SCOPE = '__no_shop_assigned__';

/** What a screen calls the shop a report covers when the server says the
 *  login has none. */
export const NO_SHOP_LABEL = 'No shop assigned';

type ShopHolder = Partial<Pick<User, 'roles' | 'activeStoreId' | 'storeIds'>>;

/** True when the server gives this login NO shop: anyone but ADMIN /
 *  SUPERADMIN (they reach every shop) with neither an active shop nor any
 *  assigned shop. A multi-store login with assigned shops but no active one is
 *  NOT shopless -- the server reads its first own shop for it. */
export function hasNoShop(user: ShopHolder | null | undefined): boolean {
  if (!user) return false;
  if (userSeesAllStores(user.roles)) return false;
  if (user.activeStoreId) return false;
  return !(user.storeIds || []).some((s) => typeof s === 'string' && s.length > 0);
}

/** The notice itself, for the signed-in user; renders nothing for a login
 *  that holds a shop (or an admin). */
export function NoShopNotice() {
  const { user } = useAuth();
  if (!hasNoShop(user)) return null;
  return (
    <div
      role="status"
      data-testid="no-shop-notice"
      className="p-4 bg-amber-50 border border-amber-200 rounded-lg flex items-start gap-3"
    >
      <AlertTriangle className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5" aria-hidden="true" />
      <p className="text-sm font-medium text-amber-900">{NO_SHOP_MESSAGE}</p>
    </div>
  );
}

/** A whole screen for a login with no shop: its title and the notice, in
 *  place of the screen -- so nothing on it (a figure, a 'Shop:' line, a
 *  button that records money) is offered to a login the server gives no
 *  shop. Anyone else gets the screen itself. */
export function NoShopGate({ title, children }: { title: string; children: ReactNode }) {
  const { user } = useAuth();
  if (!hasNoShop(user)) return <>{children}</>;
  return (
    <div className="p-6 max-w-6xl mx-auto space-y-4" data-testid="no-shop-gate">
      <h1 className="text-xl font-semibold text-gray-900">{title}</h1>
      <NoShopNotice />
    </div>
  );
}
