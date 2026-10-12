// ============================================================================
// IMS 2.0 - The one Purchase shop scope (audit F63, owner ruling 2026-09-28)
// ============================================================================
// Admins open Purchase on ALL STORES and may narrow it to one shop; every tab
// (orders, receiving, invoices, returns, variance, recon, this month) reads the
// same choice. Everyone else keeps their own shop. The rule itself lives on
// the server (api.dependencies.resolve_store_scope: no store_id = all stores
// for ADMIN/SUPERADMIN, the caller's own shop for anyone else, 403 for another
// shop); this only carries the admin's pick between tabs.
//
// Owner ruling 2026-10-12 (R4): anyone else with NO shop gets no shop's data.
// The server scopes them to no shop (resolve_store_scope: empty lists, a 403
// worded NO_SHOP_MESSAGE for a shop they name); the screens say so through
// the ONE no-shop rule, components/common/NoShopNotice (hasNoShop /
// NoShopNotice / NoShopGate) -- not a second predicate or message here.

import { create } from 'zustand';
import { useAuth } from '../../context/AuthContext';
import { useStores } from '../../hooks/usePOSQueries';
import { NO_SHOP_LABEL, NO_SHOP_SCOPE } from '../../components/common/NoShopNotice';
import { userSeesAllStores } from '../../utils/storeAccess';

const useChosenShop = create<{ shop: string; setShop: (shop: string) => void }>((set) => ({
  shop: '',
  setShop: (shop) => set({ shop }),
}));

/** `storeId` is what a Purchase or Finance read sends as ?store_id: the
 *  admin's pick (undefined = all stores, the default), else the caller's own
 *  shop. Who picks is the one 'sees every shop' rule (utils/storeAccess
 *  userSeesAllStores: ADMIN / SUPERADMIN -- never an AREA_MANAGER, whom the
 *  server holds to his own shops). A new order or return is raised at
 *  `ownStoreId` (the server's rule); `showShopOf` then points an admin's list
 *  at that shop if it is showing another -- true when it did -- so what was
 *  just raised never vanishes from view. */
export function usePurchaseShop() {
  const { user } = useAuth();
  const { shop, setShop } = useChosenShop();
  const canPick = userSeesAllStores(user?.roles);
  // The server's own-shop rule (resolve_store_scope): the active shop, else a
  // non-admin's first own shop -- the one every screen names as 'your shop'.
  const ownStoreId = user?.activeStoreId || (canPick ? undefined : (user?.storeIds || []).find((s) => !!s));
  const storeId = canPick ? shop || undefined : ownStoreId;
  const showShopOf = (raisedAt: string | undefined): boolean => {
    const move = canPick && !!shop && !!raisedAt && shop !== raisedAt;
    if (move) setShop(raisedAt as string);
    return move;
  };
  return { storeId, ownStoreId, canPick, shop, setShop, showShopOf };
}

type StoreRow = { store_id?: string; store_name?: string; store_code?: string };

const storeName = (s: StoreRow) => s.store_name || s.store_code || s.store_id;

/** For an admin, whatever shop the filter shows: a new PO delivers to his own
 *  shop (W1.4), so say which before he creates it. Nothing for anyone else. */
export function NewOrdersDeliverTo() {
  const { canPick, ownStoreId } = usePurchaseShop();
  return canPick && ownStoreId ? <OwnShop storeId={ownStoreId} /> : null;
}

function OwnShop({ storeId }: { storeId: string }) {
  return (
    <span className="text-xs text-gray-500">
      New orders deliver to <PurchaseShopName storeId={storeId} />
    </span>
  );
}

/** THE rule that turns a shop id into the name a screen prints: the store
 *  list's name (else its code); its id until the list arrives. The server's
 *  reserved no-shop id (a login with no shop, owner ruling 2026-10-12, R4) is
 *  never a shop: it reads 'No shop assigned', not the id. */
export function shopNameOf(storeId: string, stores: unknown): string {
  if (storeId === NO_SHOP_SCOPE) return NO_SHOP_LABEL;
  const row = ((Array.isArray(stores) ? stores : []) as StoreRow[]).find((s) => s.store_id === storeId);
  return (row && storeName(row)) || storeId;
}

/** A shop's name from the store list (shopNameOf). */
export function PurchaseShopName({ storeId }: { storeId: string }) {
  const { data } = useStores();
  return <>{shopNameOf(storeId, data)}</>;
}

/** The admin's shop filter; renders nothing for anyone else. */
export function PurchaseShopPicker() {
  const { canPick, shop, setShop } = usePurchaseShop();
  return canPick ? <ShopSelect shop={shop} setShop={setShop} /> : null;
}

/** Where an admin has the picker, everyone else is told -- read-only -- which
 *  shop every Purchase figure covers: their own, the server's rule. Nothing
 *  for an admin (the picker says it) or a login with no shop to name. */
export function PurchaseShopLabel() {
  const { canPick, ownStoreId } = usePurchaseShop();
  if (canPick || !ownStoreId) return null;
  return (
    <span className="text-sm text-gray-600">
      Shop: <span className="font-medium text-gray-900"><PurchaseShopName storeId={ownStoreId} /></span>
    </span>
  );
}

function ShopSelect({ shop, setShop }: { shop: string; setShop: (shop: string) => void }) {
  const { data } = useStores();
  const stores = (Array.isArray(data) ? data : []) as StoreRow[];
  return (
    <label className="flex items-center gap-2 text-sm text-gray-600">
      Shop
      <select
        value={shop}
        onChange={(e) => setShop(e.target.value)}
        className="text-sm border border-gray-300 rounded px-2 py-1.5 bg-white"
        aria-label="Purchase shop"
      >
        <option value="">All stores</option>
        {stores.map((s) => (
          <option key={s.store_id} value={s.store_id}>
            {storeName(s)}
          </option>
        ))}
      </select>
    </label>
  );
}
